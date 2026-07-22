#!/usr/bin/env python3
"""Open-source adapter for the bundled multi-operation ALU.

Supported tools are intentionally limited to Verilator for simulation, Yosys
for technology mapping, and OpenSTA for timing and activity-based power.
"""

import json
import hashlib
import math
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from toolchain import (
    find_executable,
    inspect_liberty,
    select_cxx,
    sha256_file,
    source_manifest,
    tool_version,
    verify_lock,
)
from verilator_flow import VerilatorFlowError, create_plan, execute_plan


ROOT = Path(__file__).resolve().parent
RTL_SOURCES = [ROOT / "rtl" / "demo_alu.sv"]
TESTBENCH_SOURCES = [ROOT / "tb" / "tb_demo_alu.sv"]
DESIGN_TOP = "demo_alu"
TESTBENCH_TOP = "tb_demo_alu"
EXPECTED_CHECKS = 1757
WIDTH = 32
EXPECTED_ACTIVITY_INPUT_PINS = set(
    ["a_i[{}]".format(bit) for bit in range(WIDTH)]
    + ["b_i[{}]".format(bit) for bit in range(WIDTH)]
    + ["op_i[{}]".format(bit) for bit in range(3)]
)
IO_DELAY_FRACTION = 0.10
INPUT_TRANSITION_FRACTION = 0.05
OUTPUT_LOAD = 0.05
STIMULUS_INTERVAL_NS = 1.0


class AdapterError(RuntimeError):
    """Raised for a missing tool or failed open-source flow."""


def _resolve_sources(
    configured: Optional[Sequence[str]], defaults: Sequence[Path]
) -> List[Path]:
    selected = [Path(path).expanduser().resolve() for path in configured] if configured else [
        Path(path).resolve() for path in defaults
    ]
    if not selected:
        raise AdapterError("At least one source file is required")
    missing = [str(path) for path in selected if not path.is_file()]
    if missing:
        raise AdapterError("Source file(s) do not exist: {}".format(", ".join(missing)))
    return selected


def _prepare_directory(path: Path, clean: bool) -> Path:
    if clean and path.exists():
        shutil.rmtree(str(path))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _tail(path: Path, lines: int = 30) -> str:
    try:
        content = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _run(command: Sequence[str], cwd: Path, log_path: Path) -> float:
    started = time.time()
    with log_path.open("w") as log:
        log.write("$ {}\n\n".format(" ".join(shlex.quote(str(item)) for item in command)))
        log.flush()
        completed = subprocess.run(
            [str(item) for item in command],
            cwd=str(cwd),
            stdout=log,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
    elapsed = time.time() - started
    if completed.returncode != 0:
        raise AdapterError(
            "Command failed with exit code {}. See {}\n{}".format(
                completed.returncode, log_path, _tail(log_path)
            )
        )
    return elapsed


def _select_yosys() -> str:
    executable = find_executable("yosys", "FU_YOSYS")
    if not executable:
        raise AdapterError("Yosys was not found; run 'python3 toolchain.py doctor'")
    return executable


def _find_vcd_scope(
    vcd_path: Path,
    dut_instance: str = "dut",
    testbench_top: str = TESTBENCH_TOP,
) -> str:
    """Return the exact slash-separated VCD scope that contains the DUT.

    OpenSTA removes this prefix before matching VCD variables to the mapped
    netlist. Verilator normally adds a ``TOP`` scope, so a guessed dotted scope
    such as ``tb_demo_alu.dut`` does not annotate the design.
    """

    stack: List[str] = []
    scopes: List[Tuple[str, ...]] = []
    with vcd_path.open(errors="replace") as stream:
        for line in stream:
            scope_match = re.match(r"\s*\$scope\s+\S+\s+(.+?)\s+\$end\s*$", line)
            if scope_match:
                stack.append(scope_match.group(1))
                scopes.append(tuple(stack))
                continue
            if re.match(r"\s*\$upscope\s+\$end\s*$", line):
                if stack:
                    stack.pop()
                continue
            if "$enddefinitions" in line:
                break

    candidates = [scope for scope in scopes if scope and scope[-1] == dut_instance]
    preferred = [
        scope
        for scope in candidates
        if len(scope) >= 2 and scope[-2] == testbench_top
    ]
    selected = preferred or candidates
    if len(selected) != 1:
        rendered = ["/".join(scope) for scope in selected]
        raise AdapterError(
            "Could not identify one VCD DUT scope ending in '{}'; candidates: {}".format(
                dut_instance, rendered or "none"
            )
        )
    return "/".join(selected[0])


def _primary_input_trace(
    vcd_path: Path,
    scope: str,
    required_signals: Sequence[str] = ("a_i", "b_i", "op_i"),
) -> Dict[str, Any]:
    """Hash only primary-input value changes, independent of VCD symbol IDs."""

    header_lines = []
    with vcd_path.open(errors="replace") as header_stream:
        for raw_line in header_stream:
            header_lines.append(raw_line)
            if "$enddefinitions" in raw_line:
                break
    timescale_match = re.search(
        r"\$timescale\s+(.+?)\s+\$end", "".join(header_lines), re.DOTALL
    )
    timescale = (
        " ".join(timescale_match.group(1).split())
        if timescale_match else "unspecified"
    )
    scope_parts = tuple(scope.split("/"))
    stack: List[str] = []
    symbols: Dict[str, Tuple[str, int]] = {}
    in_definitions = True
    current_time = 0
    digest = hashlib.sha256()
    digest.update("timescale {}\n".format(timescale).encode("utf-8"))
    event_count = 0

    # VCD writers may emit independent signal changes at the same timestamp in
    # different orders. Canonicalize across signals while retaining the order of
    # repeated transitions for any one signal.
    pending_events: Dict[str, List[str]] = {}

    def flush_timestamp() -> None:
        nonlocal event_count
        for name in sorted(pending_events):
            for value in pending_events[name]:
                digest.update(
                    "{} {} {}\n".format(current_time, name, value).encode("utf-8")
                )
                event_count += 1
        pending_events.clear()

    with vcd_path.open(errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if in_definitions:
                scope_match = re.match(r"\$scope\s+\S+\s+(.+?)\s+\$end$", line)
                if scope_match:
                    stack.append(scope_match.group(1))
                    continue
                if re.match(r"\$upscope\s+\$end$", line):
                    if stack:
                        stack.pop()
                    continue
                variable = re.match(
                    r"\$var\s+\S+\s+(\d+)\s+(\S+)\s+(\S+)(?:\s+(\[[^]]+\]))?\s+\$end$",
                    line,
                )
                if variable and tuple(stack) == scope_parts:
                    width = int(variable.group(1))
                    symbol = variable.group(2)
                    reference = variable.group(3).lstrip("\\")
                    suffix = (variable.group(4) or "").replace(" ", "")
                    base = reference.split("[")[0]
                    if base in required_signals:
                        symbols[symbol] = (reference + suffix, width)
                if "$enddefinitions" in line:
                    in_definitions = False
                    for symbol, (name, width) in sorted(
                        symbols.items(), key=lambda item: (item[1][0], item[0])
                    ):
                        digest.update("var {} {}\n".format(name, width).encode("utf-8"))
                continue

            if not line:
                continue
            if line.startswith("#"):
                try:
                    next_time = int(line[1:])
                except ValueError:
                    raise AdapterError("Invalid VCD timestamp in {}: {}".format(vcd_path, line))
                if next_time < current_time:
                    raise AdapterError(
                        "VCD timestamps are not monotonic in {}: {} after {}".format(
                            vcd_path, next_time, current_time
                        )
                    )
                if next_time != current_time:
                    flush_timestamp()
                    current_time = next_time
                continue
            symbol = None
            value = None
            if line[0].lower() == "b":
                fields = line.split()
                if len(fields) == 2:
                    value, symbol = fields[0][1:].lower(), fields[1]
            elif line[0].lower() in "01xz":
                value, symbol = line[0].lower(), line[1:]
            if symbol in symbols and value is not None:
                name, width = symbols[symbol]
                if len(value) < width:
                    extension = value[0] if value and value[0] in "xz" else "0"
                    value = extension * (width - len(value)) + value
                pending_events.setdefault(name, []).append(value)

    flush_timestamp()
    digest.update("end {}\n".format(current_time).encode("utf-8"))

    found_bases = {name.split("[")[0] for name, _width in symbols.values()}
    missing = sorted(set(required_signals) - found_bases)
    if missing or event_count == 0:
        raise AdapterError(
            "VCD primary-input trace is incomplete; missing={} events={}".format(
                missing, event_count
            )
        )
    return {
        "sha256": digest.hexdigest(),
        "event_count": event_count,
        "variables": sorted(name for name, _width in symbols.values()),
        "scope": scope,
        "end_timestamp": current_time,
        "timescale": timescale,
    }


def _write_primary_input_vcd(
    source: Path,
    destination: Path,
    scope: str,
    required_signals: Sequence[str] = ("a_i", "b_i", "op_i"),
) -> Dict[str, Any]:
    """Create a comparable VCD containing no design-specific internal signals."""

    text = source.read_text(errors="replace")
    timescale_match = re.search(r"\$timescale\s+(.+?)\s+\$end", text, re.DOTALL)
    timescale = " ".join(timescale_match.group(1).split()) if timescale_match else None
    scope_parts = tuple(scope.split("/"))
    stack: List[str] = []
    selected_symbols = set()
    selected_variables: List[str] = []
    in_definitions = True
    events: List[Tuple[int, str]] = []
    current_time = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if in_definitions:
            scope_match = re.match(r"\$scope\s+\S+\s+(.+?)\s+\$end$", line)
            if scope_match:
                stack.append(scope_match.group(1))
                continue
            if re.match(r"\$upscope\s+\$end$", line):
                if stack:
                    stack.pop()
                continue
            variable = re.match(
                r"\$var\s+\S+\s+(\d+)\s+(\S+)\s+(\S+)(?:\s+(\[[^]]+\]))?\s+\$end$",
                line,
            )
            if variable and tuple(stack) == scope_parts:
                base = variable.group(3).lstrip("\\").split("[")[0]
                if base in required_signals:
                    selected_symbols.add(variable.group(2))
                    selected_variables.append(line)
            if "$enddefinitions" in line:
                in_definitions = False
            continue
        if not line:
            continue
        if line.startswith("#"):
            current_time = int(line[1:])
            continue
        symbol = None
        if line[0].lower() == "b":
            fields = line.split()
            if len(fields) == 2:
                symbol = fields[1]
        elif line[0].lower() in "01xz":
            symbol = line[1:]
        if symbol in selected_symbols:
            events.append((current_time, line))

    if not selected_variables or not events:
        raise AdapterError("Could not create primary-input-only VCD from {}".format(source))
    lines = [
        "$date generated by adapter.py $end",
        "$version primary-input-only activity $end",
    ]
    if timescale:
        lines.append("$timescale {} $end".format(timescale))
    for part in scope_parts:
        lines.append("$scope module {} $end".format(part))
    lines.extend(selected_variables)
    for _part in reversed(scope_parts):
        lines.append("$upscope $end")
    lines.append("$enddefinitions $end")
    last_time = None
    for timestamp, event in events:
        if timestamp != last_time:
            lines.append("#{}".format(timestamp))
            last_time = timestamp
        lines.append(event)
    if current_time != last_time:
        lines.append("#{}".format(current_time))
    destination.write_text("\n".join(lines) + "\n")
    trace = _primary_input_trace(destination, scope, required_signals)
    return {
        "path": str(destination.resolve()),
        "sha256": sha256_file(destination),
        "policy": "primary_inputs_only",
        "trace": trace,
    }


def simulation_provenance(result: Dict[str, Any]) -> Dict[str, Any]:
    """Select simulation identity fields needed for a fair PPA comparison."""

    return {
        "simulator": result.get("tool"),
        "simulator_version": result.get("tool_version"),
        "cxx_version": result.get("cxx_version"),
        "testbench": result.get("testbench"),
        "checks": result.get("checks"),
        "seed": result.get("seed"),
        "source_manifest": result.get("source_manifest"),
        "activity_trace_sha256": result.get("activity_trace_sha256"),
        "activity_trace_end_timestamp": result.get("activity_trace_end_timestamp"),
        "activity_trace_timescale": result.get("activity_trace_timescale"),
    }


def simulateDesign(
    simulator: str = "auto",
    build_dir: Optional[str] = None,
    clean: bool = False,
    jobs: int = 1,
    seed: int = 1,
    design_top: str = DESIGN_TOP,
    rtl_sources: Optional[Sequence[str]] = None,
    verilator_defines: Optional[Sequence[str]] = None,
    **_unused: Any
) -> Dict[str, Any]:
    """Compile and run the self-checking testbench and save activity.vcd."""

    if simulator not in ("auto", "verilator"):
        raise AdapterError("This project standardizes simulation on Verilator")
    executable = find_executable("verilator", "FU_VERILATOR")
    if not executable:
        raise AdapterError(
            "Verilator was not found. Generate the complete command plan with "
            "'python3 verilator_flow.py plan' and follow README.md to install it."
        )
    cxx_info = select_cxx()
    if not cxx_info["coroutine_support"]:
        raise AdapterError(
            "Verilator timing mode requires a C++20 coroutine compiler; "
            "set FU_CXX to a compatible compiler and run toolchain.py doctor"
        )
    output = _prepare_directory(
        Path(build_dir).resolve() if build_dir else ROOT / "build" / "simulation",
        clean,
    )
    selected_rtl = _resolve_sources(rtl_sources, RTL_SOURCES)
    plan = create_plan(
        executable=executable,
        rtl_sources=selected_rtl,
        testbench_sources=TESTBENCH_SOURCES,
        design_top=design_top,
        testbench_top=TESTBENCH_TOP,
        output_dir=output,
        jobs=jobs,
        trace=True,
        cxx=cxx_info["path"],
        cxx_family=cxx_info["family"],
        seed=seed,
        defines=verilator_defines,
    )
    try:
        flow_result = execute_plan(plan)
    except VerilatorFlowError as exc:
        raise AdapterError(str(exc))
    simulation_log = Path(flow_result["simulation_log"])
    transcript = simulation_log.read_text(errors="replace")
    match = re.search(r"TEST_PASS\s+checks=(\d+)", transcript)
    if not match:
        raise AdapterError("Simulation did not report TEST_PASS. See {}".format(simulation_log))
    check_count = int(match.group(1))
    if check_count != EXPECTED_CHECKS:
        raise AdapterError(
            "Simulation reported {} checks; expected exactly {}".format(
                check_count, EXPECTED_CHECKS
            )
        )
    activity_vcd = output / "activity.vcd"
    if not activity_vcd.is_file() or activity_vcd.stat().st_size == 0:
        raise AdapterError("Simulation passed but did not create {}".format(activity_vcd))
    activity_scope = _find_vcd_scope(activity_vcd)
    primary_input_trace = _primary_input_trace(activity_vcd, activity_scope)

    result_file = output / "simulation.json"
    result = {
        "operation": "simulation",
        "status": "pass",
        "design": design_top,
        "testbench": TESTBENCH_TOP,
        "tool": "verilator",
        "tool_path": executable,
        "tool_version": flow_result["tool_version"],
        "cxx_path": flow_result["cxx_path"],
        "cxx_version": flow_result["cxx_version"],
        "seed": seed,
        "checks": check_count,
        "lint_seconds": flow_result["lint_seconds"],
        "compile_seconds": flow_result["compile_seconds"],
        "run_seconds": flow_result["run_seconds"],
        "warning_count": flow_result["warning_count"],
        "lint_log": flow_result["lint_log"],
        "compile_log": flow_result["compile_log"],
        "command_manifest": flow_result["command_manifest"],
        "activity_vcd": str(activity_vcd),
        "activity_scope": activity_scope,
        "activity_trace_sha256": primary_input_trace["sha256"],
        "activity_trace_event_count": primary_input_trace["event_count"],
        "activity_trace_variables": primary_input_trace["variables"],
        "activity_trace_end_timestamp": primary_input_trace["end_timestamp"],
        "activity_trace_timescale": primary_input_trace["timescale"],
        "activity_workload": "directed_and_uniform_random_regression",
        "stimulus_interval_ns": STIMULUS_INTERVAL_NS,
        "source_manifest": source_manifest(
            selected_rtl + TESTBENCH_SOURCES + [
                ROOT / "adapter.py",
                ROOT / "verilator_flow.py",
                ROOT / "toolchain.py",
            ]
        ),
        "log": str(simulation_log),
        "result_file": str(result_file),
    }
    _write_json(result_file, result)
    return result


def _parse_float(pattern: str, text: str, take_max: bool = False) -> Optional[float]:
    matches = re.findall(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not matches:
        return None
    values = [float(value) for value in matches]
    return max(values) if take_max else values[-1]


def _parse_yosys_metrics(
    log_path: Path,
    has_library: bool,
    stats_path: Optional[Path] = None,
    design_top: str = DESIGN_TOP,
) -> Dict[str, Any]:
    text = log_path.read_text(errors="replace")
    area = _parse_float(r"Chip area for module ['\\]?[^:]+:\s*([0-9.eE+\-]+)", text)
    cells = _parse_float(r"Number of cells:\s*(\d+)", text)
    cell_mix = None
    if stats_path and stats_path.is_file():
        try:
            statistics = json.loads(stats_path.read_text())
        except (OSError, ValueError) as exc:
            raise AdapterError("Could not parse Yosys stat JSON {}: {}".format(stats_path, exc))
        modules = statistics.get("modules") or {}
        matching = [
            module for name, module in modules.items()
            if name.lstrip("\\") == design_top
        ]
        if len(matching) != 1:
            raise AdapterError(
                "Yosys stat JSON must contain one top {}: {}".format(design_top, stats_path)
            )
        top_stats = matching[0]
        cells_field = top_stats.get("num_cells")
        try:
            if isinstance(cells_field, dict):
                cells = float(cells_field.get("count"))
                if has_library:
                    area = float(cells_field.get("area"))
            elif isinstance(cells_field, (int, float, str)):
                cells = float(cells_field)
                if has_library and top_stats.get("area") is not None:
                    area = float(top_stats["area"])
            else:
                raise AdapterError("Yosys stat JSON has no usable num_cells field")
        except (TypeError, ValueError) as exc:
            raise AdapterError("Yosys stat JSON has invalid count/area values: {}".format(exc))
        cell_mix = top_stats.get("num_cells_by_type")
    return {
        "area": {
            "value": area,
            "unit": "liberty_area_units" if has_library else "unavailable_without_liberty",
        },
        "cell_count": {"value": int(cells) if cells is not None else None, "unit": "cells"},
        "cell_mix": {"value": cell_mix, "unit": "cells_by_type"},
    }


def _parse_opensta_metrics(output: Path, clock_period: float) -> Dict[str, Any]:
    timing_text = (output / "timing.rpt").read_text(errors="replace")
    slack_text = (output / "slack.rpt").read_text(errors="replace")
    power_text = (output / "power.rpt").read_text(errors="replace")
    number = r"([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?)"
    arrival = _parse_float(
        r"^\s*{}\s+data arrival time\s*$".format(number),
        timing_text,
        take_max=True,
    )
    slack = _parse_float(
        r"^\s*(?:worst|max)\s+slack(?:\s+\w+)?\s+{}\s*$".format(number),
        slack_text,
    )
    if slack is None:
        slack = _parse_float(
            r"^\s*{}\s+slack\s*\([^)]*\)\s*$".format(number), timing_text
        )

    input_delay = clock_period * IO_DELAY_FRACTION
    output_delay = clock_period * IO_DELAY_FRACTION
    internal_delay = arrival - input_delay if arrival is not None else None
    slack_derived_delay = (
        clock_period - input_delay - output_delay - slack
        if slack is not None else None
    )
    consistency_delta = (
        abs(internal_delay - slack_derived_delay)
        if internal_delay is not None and slack_derived_delay is not None else None
    )

    # OpenSTA's report_power Total row is Internal, Switching, Leakage, Total.
    power_values = None
    power_report_unit = "W" if re.search(r"\(Watts\)", power_text, re.IGNORECASE) else None
    total_match = re.search(
        r"^\s*Total\s+([0-9.eE+\-]+)\s+([0-9.eE+\-]+)\s+"
        r"([0-9.eE+\-]+)\s+([0-9.eE+\-]+)",
        power_text,
        flags=re.MULTILINE,
    )
    if total_match and power_report_unit == "W":
        power_values = [float(total_match.group(index)) * 1000.0 for index in range(1, 5)]
    timing_paths = len(re.findall(r"^\s*Startpoint:", timing_text, flags=re.MULTILINE))
    return {
        "critical_path_delay": {"value": internal_delay, "unit": "ns"},
        "worst_data_arrival_time": {"value": arrival, "unit": "ns"},
        "input_delay": {"value": input_delay, "unit": "ns"},
        "output_delay": {"value": output_delay, "unit": "ns"},
        "input_transition": {
            "value": clock_period * INPUT_TRANSITION_FRACTION,
            "unit": "ns",
        },
        "timing_paths_reported": {"value": timing_paths, "unit": "paths"},
        "timing_consistency_delta": {"value": consistency_delta, "unit": "ns"},
        "slack": {"value": slack, "unit": "ns"},
        "timing_met": {"value": slack >= 0 if slack is not None else None, "unit": "boolean"},
        "power_report_unit": {"value": power_report_unit, "unit": "source_report"},
        "internal_power": {
            "value": power_values[0] if power_values else None,
            "unit": "mW",
        },
        "switching_power": {
            "value": power_values[1] if power_values else None,
            "unit": "mW",
        },
        "leakage_power": {
            "value": power_values[2] if power_values else None,
            "unit": "mW",
        },
        "total_power": {
            "value": power_values[3] if power_values else None,
            "unit": "mW",
            "activity_source": "simulation_vcd",
        },
    }


def _parse_activity_annotation(report_path: Path) -> Dict[str, Any]:
    text = report_path.read_text(errors="replace")
    summary_match = re.search(r"^\s*vcd\s+(\d+)\s*$", text, flags=re.MULTILINE)
    annotated = set()
    for match in re.finditer(r"^\s*vcd\s+(.+?)\s*$", text, flags=re.MULTILINE):
        pin = match.group(1).strip()
        if pin.isdigit():
            continue
        if pin.startswith("\\"):
            pin = pin[1:]
        annotated.add(pin)
    expected = set(EXPECTED_ACTIVITY_INPUT_PINS)
    missing = sorted(expected - annotated)
    return {
        "summary_vcd_pins": int(summary_match.group(1)) if summary_match else None,
        "annotated_pin_names": sorted(annotated),
        "expected_primary_input_pins": len(expected),
        "annotated_primary_input_pins": len(expected & annotated),
        "primary_input_annotation_fraction": (
            float(len(expected & annotated)) / len(expected) if expected else 1.0
        ),
        "missing_primary_input_pins": missing,
    }


def _opensta_diagnostics(text: str) -> List[str]:
    """Return both plain and message-ID-prefixed OpenSTA diagnostics."""

    return re.findall(
        r"^\s*(?:Error|Warning)(?:\s+(?:\d+|\[[^]]+\]))?\s*:\s*.+$",
        text,
        flags=re.MULTILINE,
    )


def _require_finite_metric(
    metrics: Dict[str, Any], name: str, minimum: Optional[float] = None
) -> float:
    value = metrics.get(name, {}).get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AdapterError("PPA metric '{}' is missing or non-finite: {}".format(name, value))
    if minimum is not None and value <= minimum:
        raise AdapterError("PPA metric '{}' must be greater than {}: {}".format(name, minimum, value))
    return float(value)


def _validate_ppa_metrics(metrics: Dict[str, Any]) -> None:
    """Reject incomplete, unconstrained, unmapped, or vectorless PPA evidence."""

    _require_finite_metric(metrics, "area", 0.0)
    _require_finite_metric(metrics, "cell_count", 0.0)
    _require_finite_metric(metrics, "critical_path_delay", 0.0)
    _require_finite_metric(metrics, "worst_data_arrival_time", 0.0)
    _require_finite_metric(metrics, "slack")
    if metrics.get("power_report_unit", {}).get("value") != "W":
        raise AdapterError("OpenSTA power report does not declare Watts")
    total_power = _require_finite_metric(metrics, "total_power", 0.0)
    power_components = {}
    for name in ("internal_power", "switching_power", "leakage_power"):
        value = _require_finite_metric(metrics, name)
        if value < 0:
            raise AdapterError("PPA metric '{}' must be non-negative: {}".format(name, value))
        power_components[name] = value
    if power_components["internal_power"] + power_components["switching_power"] <= 0:
        raise AdapterError("Activity-based power has no internal or switching component")
    component_sum = sum(power_components.values())
    power_tolerance = max(1.0e-6, total_power * 0.005)
    if abs(component_sum - total_power) > power_tolerance:
        raise AdapterError(
            "Power components do not sum to total: {:.9f} versus {:.9f} mW".format(
                component_sum, total_power
            )
        )
    if _require_finite_metric(metrics, "timing_paths_reported") < 1:
        raise AdapterError("OpenSTA reported no constrained timing paths")
    consistency = _require_finite_metric(metrics, "timing_consistency_delta")
    if consistency > 0.005:
        raise AdapterError(
            "OpenSTA arrival/slack consistency differs by {:.6f} ns".format(consistency)
        )
    annotation = metrics.get("activity_annotation", {})
    missing = annotation.get("missing_primary_input_pins")
    if missing is None or missing:
        raise AdapterError(
            "VCD did not annotate every primary input bit; missing: {}".format(
                ", ".join((missing or ["annotation report unavailable"])[:12])
            )
        )


def _write_yosys_script(
    output: Path,
    target_library: Optional[str],
    clock_period: float = 2.0,
    rtl_sources: Optional[Sequence[Path]] = None,
    design_top: str = DESIGN_TOP,
    netlist_name: Optional[str] = None,
) -> Path:
    script = output / "synth.ys"
    sources = list(rtl_sources or RTL_SOURCES)
    mapped_netlist = output / (netlist_name or "{}_netlist.v".format(design_top))
    lines = [
        "read_verilog -sv {}".format(" ".join(shlex.quote(str(path)) for path in sources)),
        "hierarchy -check -top {}".format(design_top),
        "check -assert",
        # Flatten ordinary hierarchy so separate_flat_auto genuinely exposes
        # cross-operation sharing. Yosys retains explicitly keep_hierarchy
        # protected leaves, which are checked later in coarse and mapped JSON.
        "synth -top {} -flatten -noabc".format(design_top),
        "write_json {}".format(shlex.quote(str(output / "coarse.json"))),
    ]
    if target_library:
        quoted = shlex.quote(str(Path(target_library).resolve()))
        delay_target_ps = int(round(clock_period * 1000.0))
        lines.extend([
            "dfflibmap -liberty {}".format(quoted),
            "abc -liberty {} -D {}".format(quoted, delay_target_ps),
            "clean",
            "check -mapped -assert",
            "stat -top {} -liberty {}".format(design_top, quoted),
            "tee -q -o {} stat -json -hierarchy -top {} -liberty {}".format(
                shlex.quote(str(output / "stats.json")), design_top, quoted
            ),
            "write_json {}".format(shlex.quote(str(output / "mapped.json"))),
        ])
    else:
        lines.extend([
            "stat -top {}".format(design_top),
            "tee -q -o {} stat -json -hierarchy -top {}".format(
                shlex.quote(str(output / "stats.json")), design_top
            ),
        ])
    lines.append(
        "write_verilog -noattr -noexpr -nodec {}".format(
            shlex.quote(str(mapped_netlist))
        )
    )
    script.write_text("\n".join(lines) + "\n")
    return script


def _run_opensta(
    output: Path,
    target_library: str,
    clock_period: float,
    activity_vcd: Path,
    design_top: str = DESIGN_TOP,
    netlist_name: Optional[str] = None,
) -> Tuple[str, float, Dict[str, Any]]:
    sta = find_executable("sta", "FU_OPENSTA")
    if not sta:
        raise AdapterError(
            "OpenSTA is required for timing/power with a Liberty file; run toolchain.py doctor"
        )
    activity_scope = _find_vcd_scope(activity_vcd)
    primary_activity = _write_primary_input_vcd(
        activity_vcd, output / "primary_inputs.vcd", activity_scope
    )
    config = {
        "FU_TOP": design_top,
        "FU_NETLIST": str(
            (output / (netlist_name or "{}_netlist.v".format(design_top))).resolve()
        ),
        "FU_LIBERTY": str(Path(target_library).resolve()),
        "FU_CLOCK_PERIOD": str(clock_period),
        "FU_INPUT_TRANSITION": str(clock_period * INPUT_TRANSITION_FRACTION),
        "FU_OUTPUT_LOAD": str(OUTPUT_LOAD),
        "FU_BUILD_DIR": str(output.resolve()),
        "FU_ACTIVITY_VCD": primary_activity["path"],
        "FU_ACTIVITY_SCOPE": activity_scope,
    }
    config_path = output / "opensta_config.tcl"
    config_path.write_text(
        "\n".join(
            "set ::env({}) {}".format(key, "{" + value + "}")
            for key, value in sorted(config.items())
        ) + "\n"
    )
    driver = output / "run_opensta.tcl"
    driver.write_text(
        "source {{{}}}\nsource {{{}}}\n".format(
            str(config_path.resolve()), str((ROOT / "scripts" / "timing_opensta.tcl").resolve())
        )
    )
    seconds = _run([sta, "-exit", str(driver)], output, output / "opensta.log")
    required_nonempty = [
        output / name
        for name in (
            "timing.rpt",
            "slack.rpt",
            "power.rpt",
            "units.rpt",
            "activity_annotation.rpt",
        )
    ]
    check_setup = output / "check_setup.rpt"
    missing = [
        str(path)
        for path in required_nonempty
        if not path.is_file() or path.stat().st_size == 0
    ]
    if not check_setup.is_file():
        missing.append(str(check_setup))
    if missing:
        raise AdapterError("OpenSTA did not produce report(s): {}".format(", ".join(missing)))
    if check_setup.read_text(errors="replace").strip():
        raise AdapterError(
            "OpenSTA check_setup reported incomplete constraints; see {}".format(check_setup)
        )
    opensta_text = (output / "opensta.log").read_text(errors="replace")
    diagnostics = _opensta_diagnostics(opensta_text)
    if diagnostics:
        raise AdapterError(
            "OpenSTA emitted {} diagnostic(s); see {}".format(
                len(diagnostics), output / "opensta.log"
            )
        )
    annotations = re.findall(
        r"Annotated\s+(\d+)\s+pin activities\.", opensta_text, flags=re.IGNORECASE
    )
    if not annotations:
        raise AdapterError(
            "OpenSTA did not report VCD annotation count; see {}".format(
                output / "opensta.log"
            )
        )
    annotated_pins = int(annotations[-1])
    if annotated_pins != len(EXPECTED_ACTIVITY_INPUT_PINS):
        raise AdapterError(
            "Primary-input-only VCD annotated {} pins; expected exactly {}".format(
                annotated_pins, len(EXPECTED_ACTIVITY_INPUT_PINS)
            )
        )
    annotation = _parse_activity_annotation(output / "activity_annotation.rpt")
    if annotation["summary_vcd_pins"] != annotated_pins:
        raise AdapterError(
            "OpenSTA annotation log/report disagree: {} versus {}".format(
                annotated_pins, annotation["summary_vcd_pins"]
            )
        )
    metrics = _parse_opensta_metrics(output, clock_period)
    metrics["total_power"]["activity_source"] = "primary_inputs_only_vcd"
    metrics["activity_annotation"] = {
        "annotated_pins": annotated_pins,
        "scope": activity_scope,
        "source": "simulation_vcd",
        "activity_policy": primary_activity["policy"],
        "activity_vcd": primary_activity["path"],
        "activity_vcd_sha256": primary_activity["sha256"],
        **annotation
    }
    return sta, seconds, metrics


def synthesizeDesign(
    synthesizer: str = "auto",
    target_library: Optional[str] = None,
    clock_period: float = 2.0,
    activity_vcd: Optional[str] = None,
    build_dir: Optional[str] = None,
    clean: bool = False,
    design_top: str = DESIGN_TOP,
    rtl_sources: Optional[Sequence[str]] = None,
    activity_seed: Optional[int] = None,
    activity_workload: str = "directed_and_uniform_random_regression",
    activity_provenance: Optional[Dict[str, Any]] = None,
    toolchain_lock: Optional[str] = None,
    **_unused: Any
) -> Dict[str, Any]:
    """Map with Yosys; with Liberty+VCD, run OpenSTA for PPA estimates.

    The ``siliconcompiler`` synthesizer instead drives the SiliconCompiler
    synthesis+STA flow on the open Skywater130 PDK (Yosys -> OpenSTA) and returns
    real area/timing/power without needing a hand-supplied Liberty file.
    """

    if synthesizer == "siliconcompiler":
        import sc_flow
        selected_rtl = _resolve_sources(rtl_sources, RTL_SOURCES)
        sdc = os.environ.get("FU_SDC", str(ROOT / "rtl" / "{}.sdc".format(design_top)))
        if not Path(sdc).is_file():
            raise AdapterError("SiliconCompiler flow needs an SDC file: {}".format(sdc))
        return sc_flow.synthesize_design_sc(
            design_top=design_top,
            rtl_sources=selected_rtl,
            sdc=sdc,
            build_dir=build_dir or str(ROOT / "build" / "sc"),
            clean=clean,
        )

    if synthesizer not in ("auto", "yosys"):
        raise AdapterError(
            "Supported synthesizers: auto, yosys, siliconcompiler"
        )
    if clock_period <= 0:
        raise AdapterError("clock_period must be greater than zero")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", design_top):
        raise AdapterError("Invalid design top: {}".format(design_top))
    yosys = _select_yosys()
    output = _prepare_directory(
        Path(build_dir).resolve() if build_dir else ROOT / "build" / "synthesis",
        clean,
    )
    library = str(Path(target_library).resolve()) if target_library else None
    if library and not Path(library).is_file():
        raise AdapterError("Target Liberty file does not exist: {}".format(library))
    library_metadata = inspect_liberty(library) if library else None
    if library_metadata and not library_metadata.get("time_unit_is_1ns"):
        raise AdapterError(
            "This prototype requires a Liberty time_unit of 1ns; found {}".format(
                library_metadata.get("time_unit")
            )
        )
    lock_verification = None
    if library:
        lock_path = Path(
            toolchain_lock
            or os.environ.get("FU_TOOLCHAIN_LOCK", str(ROOT / "toolchain.lock.json"))
        )
        lock_verification = verify_lock(lock_path, library)
        if lock_verification.get("status") != "pass":
            raise AdapterError(
                "Active EDA environment does not match toolchain lock {}: {}".format(
                    lock_verification.get("lock_file"),
                    "; ".join(lock_verification.get("differences") or ["unknown mismatch"]),
                )
            )
    selected_rtl = _resolve_sources(rtl_sources, RTL_SOURCES)
    netlist_name = "{}_netlist.v".format(design_top)
    script = _write_yosys_script(
        output,
        library,
        clock_period,
        rtl_sources=selected_rtl,
        design_top=design_top,
        netlist_name=netlist_name,
    )
    yosys_seconds = _run([yosys, "-s", str(script)], output, output / "yosys.log")
    yosys_text = (output / "yosys.log").read_text(errors="replace")
    yosys_warnings = re.findall(r"^\s*Warning:", yosys_text, flags=re.MULTILINE)
    if yosys_warnings:
        raise AdapterError(
            "Yosys emitted {} warning(s); review {}".format(
                len(yosys_warnings), output / "yosys.log"
            )
        )
    unknown_area = re.findall(
        r"Area for cell type\s+(.+?)\s+is unknown!", yosys_text, flags=re.IGNORECASE
    )
    if library and unknown_area:
        raise AdapterError(
            "Yosys could not determine area for mapped cell type(s): {}".format(
                ", ".join(sorted(set(unknown_area)))
            )
        )
    mapped_netlist = output / netlist_name
    if not mapped_netlist.is_file() or mapped_netlist.stat().st_size == 0:
        raise AdapterError("Yosys did not produce a non-empty mapped netlist")
    structural_json = [output / "coarse.json", output / "stats.json"]
    if library:
        structural_json.append(output / "mapped.json")
    missing_structural = [
        str(path) for path in structural_json
        if not path.is_file() or path.stat().st_size == 0
    ]
    if missing_structural:
        raise AdapterError(
            "Yosys did not produce structural JSON: {}".format(", ".join(missing_structural))
        )
    metrics = _parse_yosys_metrics(
        output / "yosys.log",
        bool(library),
        stats_path=output / "stats.json",
        design_top=design_top,
    )

    sta_path = None
    sta_version = None
    sta_seconds = 0.0
    activity = (
        Path(activity_vcd).resolve()
        if activity_vcd else ROOT / "build" / "simulation" / "activity.vcd"
    )
    activity_trace = None
    if activity.is_file():
        activity_scope = _find_vcd_scope(activity)
        activity_trace = _primary_input_trace(activity, activity_scope)
    if library:
        if not activity.is_file():
            raise AdapterError(
                "A simulation VCD is required for activity-based PPA. Run simulateDesign first "
                "or pass activity_vcd. Missing: {}".format(activity)
            )
        sta_path, sta_seconds, sta_metrics = _run_opensta(
            output,
            library,
            clock_period,
            activity,
            design_top=design_top,
            netlist_name=netlist_name,
        )
        metrics.update(sta_metrics)
        _validate_ppa_metrics(metrics)
    else:
        metrics.update({
            "critical_path_delay": {"value": None, "unit": "requires_liberty_and_opensta"},
            "slack": {"value": None, "unit": "requires_liberty_and_opensta"},
            "total_power": {"value": None, "unit": "requires_liberty_vcd_and_opensta"},
        })

    if sta_path:
        sta_version = tool_version("opensta", sta_path)
    result_file = output / "ppa.json"
    result = {
        "operation": "synthesis",
        "status": "pass",
        "design": design_top,
        "tool": "yosys",
        "tool_path": yosys,
        "tool_version": tool_version("yosys", yosys),
        "timing_power_tool": "opensta" if sta_path else None,
        "timing_power_tool_path": sta_path,
        "timing_power_tool_version": sta_version,
        "target_library": library,
        "target_library_metadata": library_metadata,
        "toolchain_lock": (
            {
                "file": lock_verification["lock_file"],
                "sha256": lock_verification["lock_sha256"],
                "status": lock_verification["status"],
            }
            if lock_verification else None
        ),
        "clock_period_ns": clock_period,
        "constraints": {
            "io_delay_fraction": IO_DELAY_FRACTION,
            "input_delay_ns": clock_period * IO_DELAY_FRACTION,
            "output_delay_ns": clock_period * IO_DELAY_FRACTION,
            "input_transition_fraction": INPUT_TRANSITION_FRACTION,
            "input_transition_ns": clock_period * INPUT_TRANSITION_FRACTION,
            "output_load": OUTPUT_LOAD,
            "output_load_unit": "selected_liberty_capacitance_unit",
        },
        "activity_vcd": str(activity) if activity.is_file() else None,
        "activity_vcd_sha256": sha256_file(activity) if activity.is_file() else None,
        "activity_trace_sha256": activity_trace["sha256"] if activity_trace else None,
        "activity_trace_event_count": activity_trace["event_count"] if activity_trace else None,
        "activity_trace_end_timestamp": (
            activity_trace["end_timestamp"] if activity_trace else None
        ),
        "activity_trace_timescale": activity_trace["timescale"] if activity_trace else None,
        "activity_workload": activity_workload if activity.is_file() else None,
        "activity_seed": activity_seed if activity.is_file() else None,
        "activity_provenance": activity_provenance if activity.is_file() else None,
        "activity_stimulus_interval_ns": STIMULUS_INTERVAL_NS if activity.is_file() else None,
        "estimate_stage": "post_synthesis_pre_layout",
        "ppa_validated": bool(library and sta_path),
        "source_manifest": source_manifest(
            selected_rtl + [
                ROOT / "adapter.py",
                ROOT / "toolchain.py",
                ROOT / "scripts" / "timing_opensta.tcl",
            ]
        ),
        "metrics": metrics,
        "mapped_netlist": str(mapped_netlist),
        "coarse_json": str(output / "coarse.json"),
        "mapped_json": str(output / "mapped.json") if library else None,
        "statistics_json": str(output / "stats.json"),
        "yosys_script": str(script),
        "elapsed_seconds": round(yosys_seconds + sta_seconds, 3),
        "yosys_log": str(output / "yosys.log"),
        "opensta_log": str(output / "opensta.log") if sta_path else None,
        "result_file": str(result_file),
        "caveat": "Compare only identical tools, Liberty corner, constraints, loads, and activity.",
    }
    _write_json(result_file, result)
    return result
