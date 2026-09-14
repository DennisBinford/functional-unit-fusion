#!/usr/bin/env python3
"""Measure fair operator-core and interface-level ADD/MUL/MAC baselines."""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import adapter  # noqa: E402
import measurement_guardrails  # noqa: E402
import sc_flow  # noqa: E402
import toolchain  # noqa: E402
from verilator_flow import create_plan, execute_plan  # noqa: E402


LOCK = ROOT / "build" / "toolchain.lock.json"
BUILD = ROOT / "build" / "fair_baselines"
DEFAULT_OUTPUT = ROOT / "meeting-artifacts" / "2026-09-14" / "fair_baselines.json"
CORE_SDC = ROOT / "rtl" / "fma_experiment" / "core_10ns.sdc"
INTERFACE_SDC = ROOT / "rtl" / "fma_experiment" / "generated_fused_10ns.sdc"
CHECKS = 70
CLOCK_NS = 10.0
CORE_SEMANTICS_TB = ROOT / "tb" / "tb_core_semantics.sv"


def _metric(result, name):
    value = result.get("metrics", {}).get(name, {}).get("value")
    if value is None:
        raise RuntimeError("synthesis result lacks metric {}".format(name))
    return float(value)


def _synthesize(name, top, source, sdc, clean):
    directory = BUILD / "sc" / name
    last_error = "synthesis failed for {}".format(name)
    # A SiliconCompiler scheduler failure can leave its flowgraph populated
    # while the retry in sc_flow is still using the same project object.  A
    # fresh invocation/project is the safe retry boundary for this workflow.
    for attempt in range(2):
        result = sc_flow.synthesize_design_sc(
            top, [str(source)], str(sdc), build_dir=str(directory),
            clean=clean or attempt > 0
        )
        if result.get("status") == "pass":
            return result
        last_error = result.get("error", last_error)
    raise RuntimeError(last_error)


def _summary_row(result):
    metrics = result["metrics"]
    # sc_flow names the virtual-clock path with its in-design delay; do not
    # substitute the I/O-budgeted fmax for the operator-core delay.
    delay = metrics.get("core_path_delay", {}).get("value")
    if delay is None:
        delay = metrics.get("critical_path_delay", {}).get("value")
    if delay is None:
        raise RuntimeError("synthesis result lacks a critical/in-design path delay")
    delay = float(delay)
    slack = metrics.get("setupslack", {}).get("value")
    return {
        "area_um2": _metric(result, "cellarea"),
        "cell_count": int(_metric(result, "cells")),
        "critical_delay_ns": delay,
        "estimated_fmax_mhz": 1000.0 / delay,
        "slack_ns": float(slack) if slack is not None else None,
        "synthesis_result": result["result_file"],
    }


def _activity_pins():
    return (
        {"clk_i", "rst_ni", "valid_i"}
        | {"mode_i[{}]".format(i) for i in range(2)}
        | {"a_i[{}]".format(i) for i in range(32)}
        | {"b_i[{}]".format(i) for i in range(32)}
        | {"c_i[{}]".format(i) for i in range(32)}
    )


def _simulate(generated, clean):
    output = BUILD / "interface_simulation"
    if clean and output.exists():
        shutil.rmtree(output)
    verilator = toolchain.find_executable("verilator", "FU_VERILATOR")
    cxx = toolchain.select_cxx()
    if not verilator or not cxx.get("coroutine_support"):
        raise RuntimeError("the locked Verilator/C++20 simulation toolchain is unavailable")
    plan = create_plan(
        executable=verilator,
        rtl_sources=[generated, ROOT / "rtl/fma_experiment/interface_separate_add_mul_mac.sv"],
        testbench_sources=[ROOT / "tb/tb_interface_level_compare.sv"],
        design_top="generated_fused_add_mul_mac",
        testbench_top="tb_interface_level_compare",
        output_dir=output, jobs=1, trace=True, cxx=cxx["path"],
        cxx_family=cxx.get("family"), lint_testbench=True,
    )
    result = execute_plan(plan)
    text = Path(result["simulation_log"]).read_text(errors="replace")
    match = re.search(r"INTERFACE_LEVEL_TEST_PASS checks=(\d+) cycles=(\d+)", text)
    if not match or int(match.group(1)) != CHECKS:
        raise RuntimeError("interface regression did not report 70 checks")
    vcd = output / "interface_level_activity.vcd"
    if not vcd.is_file() or vcd.stat().st_size == 0:
        raise RuntimeError("interface regression did not produce activity VCD")
    return {"checks": int(match.group(1)), "cycles": int(match.group(2)),
            "vcd": str(vcd), "simulation_log": result["simulation_log"],
            "warning_count": result["warning_count"]}


def _simulate_core_semantics(clean):
    output = BUILD / "core_semantics_simulation"
    if clean and output.exists():
        shutil.rmtree(output)
    verilator = toolchain.find_executable("verilator", "FU_VERILATOR")
    cxx = toolchain.select_cxx()
    if not verilator or not cxx.get("coroutine_support"):
        raise RuntimeError("the locked Verilator/C++20 simulation toolchain is unavailable")
    sources = [ROOT / "rtl/fma_experiment/core_add_u64.sv",
               ROOT / "rtl/fma_experiment/core_mul_u32.sv",
               ROOT / "rtl/fma_experiment/core_fused_add_mul_mac_u32.sv"]
    plan = create_plan(
        executable=verilator, rtl_sources=sources, testbench_sources=[CORE_SEMANTICS_TB],
        design_top="core_fused_add_mul_mac_u32", testbench_top="tb_core_semantics",
        output_dir=output, jobs=1, trace=False, cxx=cxx["path"],
        cxx_family=cxx.get("family"), lint_testbench=True,
    )
    result = execute_plan(plan)
    text = Path(result["simulation_log"]).read_text(errors="replace")
    match = re.search(r"CORE_SEMANTICS_TEST_PASS checks=(\d+) composition_checks=(\d+)", text)
    if not match or int(match.group(1)) != CHECKS:
        raise RuntimeError("core semantic regression did not report 70 checks")
    return {"checks": int(match.group(1)), "composition_checks": int(match.group(2)),
            "simulation_log": result["simulation_log"],
            "warning_count": result["warning_count"],
            "testbench_sha256": toolchain.sha256_file(CORE_SEMANTICS_TB)}


def _activity_row(result, name, simulation, latency, ii):
    top = result["design"]
    mapped = list((Path(result["build_dir"]).glob(
        "{}/job*/synthesis/0/outputs/{}.vg".format(top, top))))
    if len(mapped) != 1:
        raise RuntimeError("expected one mapped netlist for {}".format(name))
    sta_dir = BUILD / "activity_sta" / name
    sta_dir.mkdir(parents=True, exist_ok=True)
    netlist_name = top + "_netlist.v"
    shutil.copy2(mapped[0], sta_dir / netlist_name)
    lock = toolchain.verify_lock(LOCK)
    liberty = lock["locked_fingerprint"]["liberty"]["path"]
    _, seconds, sta = adapter._run_opensta(
        sta_dir, liberty, CLOCK_NS, Path(simulation["vcd"]), design_top=top,
        netlist_name=netlist_name,
        activity_required_signals=("clk_i", "rst_ni", "valid_i", "mode_i", "a_i", "b_i", "c_i"),
        expected_activity_input_pins=sorted(_activity_pins()), clock_port="clk_i",
    )
    checks, cycles = simulation["checks"], simulation["cycles"]
    power = float(sta["total_power"]["value"])
    return {
        "name": name,
        "area_um2": _metric(result, "cellarea"),
        "cell_count": int(_metric(result, "cells")),
        "critical_delay_ns": float(sta["critical_path_delay"]["value"]),
        "estimated_fmax_mhz": 1000.0 / float(sta["critical_path_delay"]["value"]),
        "slack_ns": float(sta["slack"]["value"]),
        "power_mw": power,
        "internal_power_mw": float(sta["internal_power"]["value"]),
        "switching_power_mw": float(sta["switching_power"]["value"]),
        "leakage_power_mw": float(sta["leakage_power"]["value"]),
        "energy_per_workload_operation_pj": power * cycles * CLOCK_NS / checks,
        "latency_cycles": latency, "initiation_interval_cycles": ii,
        "throughput_mops": (1000.0 / (CLOCK_NS * ii)) if ii else None,
        "workload_throughput_mops": 1000.0 * checks / (CLOCK_NS * cycles),
        "activity_annotation_fraction": sta["activity_annotation"]["primary_input_annotation_fraction"],
        "simulation": simulation, "opensta_seconds": seconds,
        "synthesis_result": result["result_file"],
    }


def _source_hashes(paths):
    return {str(Path(path).resolve()): toolchain.sha256_file(Path(path)) for path in paths}


def run(generated, output=DEFAULT_OUTPUT, clean=False):
    generated = Path(generated).resolve()
    if not generated.is_file():
        raise RuntimeError("generated RTL is missing: {}".format(generated))
    lock = toolchain.verify_lock(LOCK)
    if lock.get("status") != "pass":
        raise RuntimeError("toolchain lock verification failed: {}".format(lock.get("differences")))
    liberty = Path(lock["locked_fingerprint"]["liberty"]["path"])
    core_specs = {
        "add_core": ("core_add_u64", ROOT / "rtl/fma_experiment/core_add_u64.sv"),
        "mul_core": ("core_mul_u32", ROOT / "rtl/fma_experiment/core_mul_u32.sv"),
        "fused_core": ("core_fused_add_mul_mac_u32", ROOT / "rtl/fma_experiment/core_fused_add_mul_mac_u32.sv"),
    }
    core_results = {}
    for name, (top, source) in core_specs.items():
        core_results[name] = _summary_row(_synthesize(name, top, source, CORE_SDC, clean))
        core_results[name]["guardrails"] = measurement_guardrails.inspect_design(
            source, top, CLOCK_NS, CORE_SDC, liberty, LOCK)
        measurement_guardrails.require_semantic_contract(core_results[name]["guardrails"], name)
    core_sum = core_results["add_core"]["area_um2"] + core_results["mul_core"]["area_um2"]
    fused_core = core_results["fused_core"]["area_um2"]
    core_semantics = _simulate_core_semantics(clean)
    generated_guardrails = measurement_guardrails.inspect_design(
        generated, "generated_fused_add_mul_mac", CLOCK_NS, INTERFACE_SDC, liberty, LOCK)
    separate_source = ROOT / "rtl/fma_experiment/interface_separate_add_mul_mac.sv"
    separate_guardrails = measurement_guardrails.inspect_design(
        separate_source, "interface_separate_add_mul_mac", CLOCK_NS,
        INTERFACE_SDC, liberty, LOCK)
    measurement_guardrails.require_one_mul_one_add(generated_guardrails, "generated triple-mode interface")
    measurement_guardrails.require_one_mul_one_add(separate_guardrails, "separate-system interface")
    for label, evidence in (("generated triple-mode interface", generated_guardrails),
                            ("separate-system interface", separate_guardrails)):
        measurement_guardrails.require_semantic_contract(evidence, label)
    core_comparison_guardrails = measurement_guardrails.require_literal_core_comparison(
        core_results["add_core"]["guardrails"], core_results["mul_core"]["guardrails"],
        core_results["fused_core"]["guardrails"], core_semantics)
    simulation = _simulate(generated, clean)
    generated_result = _synthesize("generated_triple", "generated_fused_add_mul_mac",
                                   generated, INTERFACE_SDC, clean)
    separate_result = _synthesize("separate_system", "interface_separate_add_mul_mac",
                                  separate_source, INTERFACE_SDC, clean)
    generated_row = _activity_row(generated_result, "generated_triple", simulation, 1, 1)
    separate_row = _activity_row(separate_result, "separate_system", simulation, 1, 1)
    generated_row["guardrails"] = generated_guardrails
    separate_row["guardrails"] = separate_guardrails
    separate_row["operation_latency_and_ii"] = {
        "ADD": {"latency_cycles": 1, "ii_cycles": 1},
        "MUL": {"latency_cycles": 1, "ii_cycles": 1},
        "MAC": {"latency_cycles": 2, "ii_cycles": 2},
    }
    generated_row["operation_latency_and_ii"] = {
        mode: {"latency_cycles": 1, "ii_cycles": 1} for mode in ("ADD", "MUL", "MAC")
    }
    generated_row["throughput_by_mode_mops"] = {mode: 1000.0 / CLOCK_NS
                                                for mode in ("ADD", "MUL", "MAC")}
    separate_row["latency_cycles"] = None
    separate_row["initiation_interval_cycles"] = None
    separate_row["throughput_mops"] = None
    separate_row["throughput_by_mode_mops"] = {
        "ADD": 1000.0 / CLOCK_NS, "MUL": 1000.0 / CLOCK_NS,
        "MAC": 1000.0 / (CLOCK_NS * 2),
    }
    result = {
        "schema": "fair-baselines/v1",
        "method": {
            "stage": "post_synthesis_pre_layout",
            "pdk": "FreePDK45", "library": "NangateOpenCellLibrary_typical",
            "library_path": lock["locked_fingerprint"]["liberty"]["path"],
            "library_sha256": lock["locked_fingerprint"]["liberty"]["sha256"],
            "clock_period_ns": CLOCK_NS, "frequency_mhz": 100.0,
            "workload": "70 deterministic ADD/MUL/MAC operations",
            "core_only_policy": "source modules contain only combinational operators; the ADD core is 64-bit input/output and the MUL core is 32x32-to-64; no registers, protocol, or wrapper logic",
            "interface_policy": "same named ready/valid interface; separate-system MAC is two-stage multiply then add",
            "guardrail_stage": "Yosys proc; opt pre-technology-mapping cell/port inspection plus independent semantic simulation",
            "toolchain_lock": str(LOCK.resolve()),
            "toolchain_lock_sha256": toolchain.sha256_file(LOCK),
        },
        "core_only": {
            "cores": core_results,
            "literal_area_A_plus_B_um2": core_sum,
            "fused_core_area_um2": fused_core,
            "delta_um2_A_plus_B_minus_fused": core_sum - fused_core,
            "delta_percent": 100.0 * (core_sum - fused_core) / core_sum,
            "interpretation": "literal mapped-area sum of standalone unsigned 32-bit ADD and MUL operator cores versus combinational fused core; no claim about registered interface architectures",
        },
        "interface_level": {
            "separate_system": separate_row,
            "generated_triple_mode": generated_row,
            "scheduling_difference": "generated ADD/MUL/MAC are one-cycle; separate-system MAC is two cycles through one multiplier followed by one adder",
        },
        "source_sha256": _source_hashes([generated, separate_source] + [v[1] for v in core_specs.values()]),
        "core_semantics_simulation": core_semantics,
        "core_comparison_guardrails": core_comparison_guardrails,
        "guardrails": {
            "generated_triple_mode": generated_guardrails,
            "separate_system": separate_guardrails,
            "core_only": {name: row["guardrails"] for name, row in core_results.items()},
        },
        "toolchain": lock,
    }
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    readme = output.with_name("fair_baselines.md")
    c = result["core_only"]
    i = result["interface_level"]
    readme.write_text("""# Fair baseline comparisons

All rows use the locked FreePDK45/Nangate45 typical library and 10 ns
conditions. Core-only rows contain no registers, protocol, or wrapper logic.

## Literal operator-core comparison

| ADD core area | MUL core area | literal area(A)+area(B) | fused core area | delta |
|---:|---:|---:|---:|---:|
| {a:.3f} | {m:.3f} | {s:.3f} | {f:.3f} | {d:.3f} ({p:.3f}%) |

This is the literal combinational operator-core comparison only.

## Interface-level comparison

| Design | Area | Cells | Delay | Fmax | Slack | Power | Energy/op | ADD/MUL/MAC latency | ADD/MUL/MAC II | Mode throughput ADD/MUL/MAC (Mops/s) | Shared 70-op harness throughput |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|---:|
| generated triple-mode | {ga:.3f} | {gc} | {gd:.3f} | {gf:.1f} | {gs:.3f} | {gp:.4f} | {ge:.3f} | 1/1/1 | 1/1/1 | 100/100/100 | {gt:.2f} |
| separate system | {sa:.3f} | {sc} | {sd:.3f} | {sf:.1f} | {ss:.3f} | {sp:.4f} | {se:.3f} | 1/1/2 | 1/1/2 | 100/100/50 | {st:.2f} |

The separate system has one multiplier and one adder. Its MAC is a natural
two-cycle multiply-then-add schedule, so these architectures are not equivalent
in latency or initiation interval. The shared harness throughput is paced by
the separate system's ready signal; mode-throughput values are the per-mode
steady-state values.
""".format(
        a=c["cores"]["add_core"]["area_um2"], m=c["cores"]["mul_core"]["area_um2"],
        s=c["literal_area_A_plus_B_um2"], f=c["fused_core_area_um2"],
        d=c["delta_um2_A_plus_B_minus_fused"], p=c["delta_percent"],
        ga=i["generated_triple_mode"]["area_um2"], gc=i["generated_triple_mode"]["cell_count"],
        gd=i["generated_triple_mode"]["critical_delay_ns"], gf=i["generated_triple_mode"]["estimated_fmax_mhz"],
        gs=i["generated_triple_mode"]["slack_ns"], gp=i["generated_triple_mode"]["power_mw"],
        ge=i["generated_triple_mode"]["energy_per_workload_operation_pj"], gt=i["generated_triple_mode"]["workload_throughput_mops"],
        sa=i["separate_system"]["area_um2"], sc=i["separate_system"]["cell_count"],
        sd=i["separate_system"]["critical_delay_ns"], sf=i["separate_system"]["estimated_fmax_mhz"],
        ss=i["separate_system"]["slack_ns"], sp=i["separate_system"]["power_mw"],
        se=i["separate_system"]["energy_per_workload_operation_pj"], st=i["separate_system"]["workload_throughput_mops"],
    ))
    print("wrote {}".format(output))
    print("wrote {}".format(readme))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", default=str(ROOT / "rtl/fma_experiment/generated_fused_add_mul_mac.sv"))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args(argv)
    try:
        run(Path(args.generated), Path(args.output), args.clean)
    except Exception as exc:
        print("fair baseline measurement failed: {}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
