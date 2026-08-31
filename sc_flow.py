#!/usr/bin/env python3
"""SiliconCompiler synthesis + STA backend for the evaluation framework.

This module gives the framework a real PPA path: it drives SiliconCompiler's
synthesis + static-timing flow (Yosys -> OpenSTA) on the FreePDK45 (Nangate45)
open PDK and returns area / timing / power numbers in the framework's result-dict
contract (a JSON-serializable dict carrying at least a ``status`` field).

It is intentionally free of design-specific knowledge: the adapter passes the
top module, RTL sources, and an SDC, and this module runs the flow. Tool
binaries are resolved from the project-local, no-sudo toolchain installed under
``tools/`` (OSS CAD Suite Yosys + a locally built OpenSTA); the FreePDK45
Nangate45 standard cells and timing corners are supplied by SiliconCompiler.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent

# Project-local tool locations (installed without root; see README/action_items).
_EDA_ENV = ROOT / "tools" / "mm" / "root" / "envs" / "eda"      # OpenSTA + PDK deps
_OSS_BIN = ROOT / "tools" / "oss-cad-suite" / "bin"             # Yosys 0.67 (>= SC floor)


class SCFlowError(RuntimeError):
    """Raised when the SiliconCompiler PPA flow cannot produce valid results."""


def tool_environment() -> Dict[str, str]:
    """Return an os.environ-style dict with the local EDA tools on PATH."""
    env = dict(os.environ)
    parts = [str(_OSS_BIN), str(_EDA_ENV / "bin")]
    env["PATH"] = os.pathsep.join(parts + [env.get("PATH", "")])
    # OpenSTA is built with an rpath to the env libs, but set this defensively
    # so tcl/cudd/libstdc++ resolve regardless of how the process was launched.
    libdir = str(_EDA_ENV / "lib")
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        [libdir, env.get("LD_LIBRARY_PATH", "")]
    ).strip(os.pathsep)
    return env


def _apply_tool_environment() -> None:
    os.environ.update(tool_environment())


# Metrics we surface as the PPA result, mapped to the SC flow step that records
# them. SC records timing/power on the 'timing' step and cell area there too.
_TIMING_METRICS = (
    "cellarea",      # mapped standard-cell area, um^2  (Area)
    "setupslack",    # worst setup slack, ns            (Performance / timing)
    "setuptns",      # total negative setup slack, ns
    "fmax",          # max frequency, if reported
    "peakpower",     # total power at typical corner, W (Power)
    "leakagepower",  # leakage power, W
    "cells",         # instance count
    "logicdepth",    # logic levels on critical path
)


def _read_metric(history, metric: str, step: str, index: str = "0"):
    try:
        value = history.get("metric", metric, step=step, index=index)
    except Exception:
        return None, None
    unit = None
    try:
        unit = history.get("metric", metric, field="unit")
    except Exception:
        pass
    return value, unit


_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


def _first_float(pattern: str, text: str, group: int = 1) -> Optional[float]:
    match = re.search(pattern, text)
    return float(match.group(group)) if match else None


def _parse_setup_report(report: Path) -> Dict[str, Any]:
    """Split the worst setup path into the I/O reservation and the design's own delay.

    A combinational functional unit has no clock port, so the SDC constrains it
    against a virtual clock and reserves part of the period for the surrounding
    logic via ``set_input_delay`` / ``set_output_delay``.  OpenSTA's ``fmax`` is
    ``1 / find_clk_min_period``, which is the minimum period for the *whole*
    constraint -- so that reservation is charged to the unit.  When a unit's own
    logic is ~1-2 ns and the reservation is several ns, ``fmax`` reports the
    constraint, not the circuit, and every design converges to the same number.

    The worst-path report carries both halves, so report both:
      * ``core_path_delay_ns``  = arrival - input external delay  (the unit alone)
      * ``fmax_core_hz``        = 1 / core_path_delay_ns
      * ``io_budget_ns``        = input + output external delay   (the reservation)
    """
    if not report.is_file():
        return {}
    text = report.read_text(errors="replace")

    # The report may hold several paths, and each ends with a summary block that
    # restates arrival negated ("-1.48  data arrival time") for the subtraction.
    # Split on Startpoint and read the first occurrence of each field inside the
    # worst-slack block, so neither the summary restatement nor a second path
    # can be picked up instead.
    blocks = [b for b in re.split(r"(?=^Startpoint:)", text, flags=re.MULTILINE) if b.strip()]
    if not blocks:
        return {}

    def slack_of(block: str) -> float:
        value = _first_float(r"({})\s+slack\s*\(".format(_NUM), block)
        return float("inf") if value is None else value

    block = min(blocks, key=slack_of)

    arrival = _first_float(r"({})\s+data arrival time".format(_NUM), block)
    required = _first_float(r"({})\s+data required time".format(_NUM), block)
    slack = _first_float(r"({})\s+slack\s*\(".format(_NUM), block)
    # "   5.00    5.00 v input external delay"  -> take the Time column.
    input_delay = _first_float(
        r"{0}\s+({0})\s*[v^]?\s+input external delay".format(_NUM), block
    )
    # "  -5.00   45.00   output external delay" -> take the Delay column.
    output_delay = _first_float(
        r"({0})\s+{0}\s+output external delay".format(_NUM), block
    )

    core_delay = None
    if arrival is not None and input_delay is not None:
        core_delay = arrival - input_delay

    io_budget = None
    if input_delay is not None and output_delay is not None:
        io_budget = input_delay + abs(output_delay)

    period = None
    if required is not None and output_delay is not None:
        period = required + abs(output_delay)

    parsed: Dict[str, Any] = {
        "core_path_delay": {"value": core_delay, "unit": "ns"},
        "fmax_core": {
            "value": (1e9 / core_delay) if core_delay else None,
            "unit": "Hz",
        },
        "data_arrival_time": {"value": arrival, "unit": "ns"},
        "data_required_time": {"value": required, "unit": "ns"},
        "input_external_delay": {"value": input_delay, "unit": "ns"},
        "output_external_delay": {
            "value": abs(output_delay) if output_delay is not None else None,
            "unit": "ns",
        },
        "io_budget": {"value": io_budget, "unit": "ns"},
        "constraint_period": {"value": period, "unit": "ns"},
        "setup_slack_from_report": {"value": slack, "unit": "ns"},
    }
    if core_delay and io_budget is not None:
        # A reservation larger than the logic it surrounds means fmax is
        # measuring the SDC.  Flag it rather than letting it pass silently.
        parsed["io_budget_dominates"] = {
            "value": io_budget > core_delay,
            "unit": "boolean",
        }
        parsed["core_fraction_of_constraint"] = {
            "value": core_delay / (core_delay + io_budget),
            "unit": "ratio",
        }
    return parsed


def _normalize_power(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Split total power into leakage + dynamic and rate the dynamic part.

    ``peakpower`` is total power *at the SDC clock period*.  Two designs
    constrained at different periods therefore have incomparable power columns:
    the faster constraint charges more switching energy per second.  Only the
    leakage term and the per-MHz dynamic rate compare directly, so derive them.
    """
    def value(name):
        entry = metrics.get(name) or {}
        return entry.get("value")

    total = value("peakpower")
    leakage = value("leakagepower")
    period = value("constraint_period")
    if total is None or leakage is None:
        return {}

    dynamic = float(total) - float(leakage)
    out: Dict[str, Any] = {
        "dynamic_power": {"value": dynamic, "unit": "mW"},
        "leakage_fraction": {
            "value": (float(leakage) / float(total)) if total else None,
            "unit": "ratio",
        },
    }
    if period:
        frequency_mhz = 1e3 / float(period)
        out["power_measurement_freq"] = {"value": frequency_mhz, "unit": "MHz"}
        out["dynamic_power_per_mhz"] = {
            "value": dynamic / frequency_mhz,
            "unit": "mW/MHz",
        }
    return out


def power_at_frequency(metrics: Dict[str, Any], frequency_mhz: float) -> Optional[float]:
    """Total power (mW) this design would draw at ``frequency_mhz``.

    Use this -- not raw ``peakpower`` -- when tabulating two designs that closed
    at different clock periods.  Dynamic power is taken as linear in frequency,
    which is the standard first-order model for a fixed netlist and supply.
    """
    def value(name):
        entry = metrics.get(name) or {}
        return entry.get("value")

    rate = value("dynamic_power_per_mhz")
    leakage = value("leakagepower")
    if rate is None or leakage is None:
        return None
    return float(leakage) + float(rate) * frequency_mhz


def _timing_caveats(metrics: Dict[str, Any]) -> List[str]:
    """Surface the ways a PPA point can be an artifact of its constraints."""
    notes: List[str] = []

    def value(name):
        return metrics.get(name, {}).get("value")

    core = value("core_path_delay")
    io_budget = value("io_budget")
    period = value("constraint_period")
    slack = value("setupslack")

    if core and io_budget and io_budget > core:
        notes.append(
            "fmax_with_io_budget is dominated by the SDC I/O reservation "
            "({:.2f} ns of budget vs {:.2f} ns of logic): it understates the "
            "unit by {:.1f}x. Compare units with fmax_core.".format(
                io_budget, core, (core + io_budget) / core
            )
        )
    if core and period and period > 4.0 * core:
        notes.append(
            "The SDC period ({:.1f} ns) is {:.0f}x the critical path ({:.2f} ns), "
            "so synthesis had no timing pressure and optimized for area. "
            "fmax_core is what this area-mapped netlist happens to achieve, not "
            "the best the design can do; run a timing-closure sweep for that."
            .format(period, period / core, core)
        )
    if slack is not None and period and float(slack) > 0.5 * period:
        notes.append(
            "Setup slack is {:.0f}% of the period -- the constraint is not "
            "binding, so timing numbers are unconverged.".format(
                100.0 * float(slack) / period
            )
        )
    if value("peakpower") is not None:
        notes.append(
            "Power is reported at the SDC clock period"
            + (" ({:.1f} ns = {:.0f} MHz)".format(period, 1e3 / period) if period else "")
            + ", not at fmax. Dynamic power scales with frequency; do not pair "
              "this power number with the fmax number."
        )
    return notes


def synthesize_design_sc(
    design_top: str,
    rtl_sources: Sequence[str],
    sdc: str,
    build_dir: Optional[str] = None,
    clean: bool = False,
) -> Dict[str, Any]:
    """Run the SiliconCompiler synthesis+STA flow and return a PPA result dict.

    Args:
        design_top: top module name.
        rtl_sources: RTL source files (Verilog/SystemVerilog).
        sdc: timing-constraint file (SDC) used by STA.
        build_dir: SC build directory (default: build/sc).
        clean: remove the build directory before running.
    """
    _apply_tool_environment()

    # Imported here so the module can be inspected without SC installed.
    from siliconcompiler import ASIC, Design
    from siliconcompiler.targets import freepdk45_demo
    from siliconcompiler.flows import synflow

    sources = [str(Path(s).resolve()) for s in rtl_sources]
    missing = [s for s in sources if not Path(s).is_file()]
    if missing:
        raise SCFlowError("RTL source(s) not found: {}".format(", ".join(missing)))
    sdc_path = str(Path(sdc).resolve())
    if not Path(sdc_path).is_file():
        raise SCFlowError("SDC file not found: {}".format(sdc_path))

    builddir = Path(build_dir).resolve() if build_dir else ROOT / "build" / "sc"
    if clean and builddir.exists():
        import shutil
        shutil.rmtree(builddir)
    builddir.mkdir(parents=True, exist_ok=True)

    design = Design(design_top)
    design.set_topmodule(design_top, fileset="rtl")
    for src in sources:
        design.add_file(src, fileset="rtl")
    design.add_file(sdc_path, fileset="sdc")

    proj = ASIC(design)
    proj.add_fileset(["rtl", "sdc"])
    freepdk45_demo(proj)                         # FreePDK45 PDK + Nangate45 stdcells + corners
    proj.set_flow(synflow.SynthesisFlow())       # synthesis + STA only (no P&R)
    proj.set("option", "builddir", str(builddir))
    # Locally built OpenSTA reports version 3.1.0 (>= SC floor) so no bypass is
    # needed; Yosys 0.67 also clears the floor. novercheck stays off on purpose.

    # SiliconCompiler's scheduler occasionally hiccups on a run (a rare, transient
    # failure that succeeds on a re-run). Retry once before reporting failure so
    # the demo is reproducible; capture the reason to a log if it truly fails.
    last_error = None
    for attempt in range(2):
        try:
            proj.run()
            last_error = None
            break
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        error_log = builddir / "sc_error.log"
        try:
            error_log.write_text("SiliconCompiler run failed after retry:\n{}\n".format(last_error))
        except OSError:
            pass
        return {
            "status": "fail",
            "operation": "synthesis",
            "tool": "siliconcompiler",
            "flow": "synflow.SynthesisFlow",
            "error": str(last_error),
            "error_log": str(error_log),
            "build_dir": str(builddir),
        }

    jobname = proj.option.get_jobname()
    history = proj.history(jobname)

    metrics: Dict[str, Any] = {}
    for metric in _TIMING_METRICS:
        value, unit = _read_metric(history, metric, step="timing")
        if value is None:
            value, unit = _read_metric(history, metric, step="synthesis")
        if value is not None:
            metrics[metric] = {"value": value, "unit": unit}

    # OpenSTA's fmax includes the SDC's I/O reservation; keep it under a name
    # that says so, and derive the unit-only number from the worst-path report.
    if "fmax" in metrics:
        metrics["fmax_with_io_budget"] = metrics.pop("fmax")
    setup_report = (
        builddir / design_top / jobname / "timing" / "0" / "reports" / "setup.rpt"
    )
    metrics.update(_parse_setup_report(setup_report))
    metrics.update(_normalize_power(metrics))

    # A valid PPA point must at least have real mapped area.
    area = metrics.get("cellarea", {}).get("value")
    ppa_valid = area is not None and float(area) > 0.0

    setup_slack = metrics.get("setupslack", {}).get("value")
    timing_met = None if setup_slack is None else (float(setup_slack) >= 0.0)

    result = {
        "status": "pass" if ppa_valid else "fail",
        "operation": "synthesis",
        "tool": "siliconcompiler",
        "flow": "synflow.SynthesisFlow",
        "pdk": "freepdk45",
        "stdcell_library": "nangate45 (NangateOpenCellLibrary_typical)",
        "synthesis_tool": "yosys",
        "timing_tool": "opensta",
        "design": design_top,
        "rtl_sources": sources,
        "sdc": sdc_path,
        "ppa_validated": ppa_valid,
        "timing_met": timing_met,
        "metrics": metrics,
        "metric_definitions": {
            "fmax_core": "1 / (worst in-design combinational delay). The unit's "
                         "own speed; use this to compare functional units.",
            "fmax_with_io_budget": "OpenSTA 1/find_clk_min_period. Includes the "
                                   "SDC set_input_delay + set_output_delay "
                                   "reservation, so it drops toward "
                                   "1/io_budget as the reservation grows.",
            "peakpower": "report_power at the SDC clock period with default "
                         "switching activity. Dynamic power scales with the "
                         "operating frequency, so this is NOT power at fmax.",
        },
        "caveats": _timing_caveats(metrics),
        "build_dir": str(builddir),
        "manifest": str(builddir / design_top / jobname / f"{design_top}.pkg.json"),
    }

    # Durable PPA artifact for demo transcripts, mirroring the Yosys path.
    import json
    result_file = builddir / "ppa.json"
    result_file.write_text(json.dumps(result, indent=2, sort_keys=True))
    result["result_file"] = str(result_file)
    return result


if __name__ == "__main__":
    import json
    result = synthesize_design_sc(
        "demo_alu",
        [str(ROOT / "rtl" / "demo_alu.sv")],
        str(ROOT / "rtl" / "demo_alu.sdc"),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
