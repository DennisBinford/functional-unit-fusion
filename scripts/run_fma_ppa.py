#!/usr/bin/env python3
"""Reproduce functional, area, timing, and VCD-based power evidence.

The comparison uses the same 32-bit ADD-or-MUL interface, 100 MHz clock,
FreePDK45/Nangate45 typical library, and 70-operation directed workload for
all candidates.  Results are post-synthesis/pre-layout estimates.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import adapter  # noqa: E402
from sc_flow import synthesize_design_sc  # noqa: E402
from toolchain import find_executable, select_cxx, verify_lock  # noqa: E402
from verilator_flow import create_plan, execute_plan  # noqa: E402


CLOCK_PERIOD_NS = 10.0
CHECKS = 70
RTL_DIR = ROOT / "rtl" / "fma_experiment"
BUILD = ROOT / "build" / "fma_ppa"
ARTIFACTS = ROOT / "meeting-artifacts" / "fma-ppa"
LOCK = ROOT / "build" / "toolchain.lock.json"
SDC = RTL_DIR / "mul_add_10ns.sdc"

VARIANTS = {
    "parallel": {
        "top": "registered_parallel_mul_add",
        "source": RTL_DIR / "registered_parallel_mul_add.sv",
        "define": "PPA_PARALLEL",
        "add_latency_cycles": 1,
        "mul_latency_cycles": 1,
        "mul_initiation_interval_cycles": 1,
        "description": "independent one-cycle ADD and MUL datapaths",
    },
    "radix2_original": {
        "top": "shared_iterative_mul_add",
        "source": RTL_DIR / "shared_iterative_mul_add.sv",
        "define": "PPA_RADIX2_ORIGINAL",
        "add_latency_cycles": 1,
        "mul_latency_cycles": 32,
        "mul_initiation_interval_cycles": 32,
        "description": "radix-2 iterative MUL plus a separate ADD datapath",
    },
    "radix2_reused": {
        "top": "shared_reused_adder_mul_add",
        "source": RTL_DIR / "shared_reused_adder_mul_add.sv",
        "define": "PPA_RADIX2_REUSED",
        "add_latency_cycles": 1,
        "mul_latency_cycles": 32,
        "mul_initiation_interval_cycles": 32,
        "description": "radix-2 MUL accumulator adder reused by ADD",
    },
    "radix4_reused": {
        "top": "radix4_reused_adder_mul_add",
        "source": RTL_DIR / "radix4_reused_adder_mul_add.sv",
        "define": "PPA_RADIX4_REUSED",
        "add_latency_cycles": 1,
        "mul_latency_cycles": 16,
        "mul_initiation_interval_cycles": 16,
        "description": "radix-4 MUL with its accumulator adder reused by ADD",
    },
}


def _expected_activity_pins():
    return (
        {"clk_i", "rst_ni", "valid_i", "add_i"}
        | {"a_i[{}]".format(bit) for bit in range(32)}
        | {"b_i[{}]".format(bit) for bit in range(32)}
    )


def _simulation(name, spec, clean):
    output = BUILD / "simulation" / name
    if clean and output.exists():
        shutil.rmtree(output)
    sources = [entry["source"] for entry in VARIANTS.values()]
    sources.append(RTL_DIR / "ppa_mul_add_dut.sv")
    verilator = find_executable("verilator", "FU_VERILATOR")
    if not verilator:
        raise RuntimeError("Verilator is unavailable")
    cxx = select_cxx()
    plan = create_plan(
        executable=verilator,
        rtl_sources=sources,
        testbench_sources=[ROOT / "tb" / "tb_mul_add_ppa.sv"],
        design_top="ppa_mul_add_dut",
        testbench_top="tb_mul_add_ppa",
        output_dir=output,
        jobs=1,
        trace=True,
        cxx=cxx["path"] if cxx.get("coroutine_support") else None,
        cxx_family=cxx.get("family"),
        defines=[spec["define"]],
        lint_testbench=True,
    )
    tool_result = execute_plan(plan)
    log_text = Path(tool_result["simulation_log"]).read_text(errors="replace")
    match = re.search(r"PPA_MUL_ADD_TEST_PASS checks=(\d+) cycles=(\d+)", log_text)
    if not match or int(match.group(1)) != CHECKS:
        raise RuntimeError("simulation did not report the expected {} checks".format(CHECKS))
    vcd = output / "activity.vcd"
    if not vcd.is_file() or vcd.stat().st_size == 0:
        raise RuntimeError("simulation did not produce {}".format(vcd))
    return {
        "status": "pass",
        "checks": int(match.group(1)),
        "cycles": int(match.group(2)),
        "vcd": str(vcd),
        "simulation_log": tool_result["simulation_log"],
        "warning_count": tool_result["warning_count"],
    }


def _synthesis_and_power(name, spec, vcd, clean):
    sc_dir = BUILD / "sc" / (name + "_p10")
    synthesis = synthesize_design_sc(
        spec["top"],
        [str(spec["source"])],
        str(SDC),
        build_dir=str(sc_dir),
        clean=clean,
    )
    if synthesis.get("status") != "pass":
        raise RuntimeError("SiliconCompiler failed: {}".format(synthesis.get("error")))

    candidates = sorted(sc_dir.glob(
        "{}/job*/synthesis/0/outputs/{}.vg".format(spec["top"], spec["top"])
    ))
    if len(candidates) != 1:
        raise RuntimeError("expected one mapped netlist, found {}".format(candidates))
    sta_dir = BUILD / "activity_sta" / name
    if clean and sta_dir.exists():
        shutil.rmtree(sta_dir)
    sta_dir.mkdir(parents=True, exist_ok=True)
    netlist_name = spec["top"] + "_netlist.v"
    shutil.copy2(candidates[0], sta_dir / netlist_name)

    lock = verify_lock(LOCK)
    if lock["status"] != "pass":
        raise RuntimeError("toolchain lock verification failed: {}".format(lock["differences"]))
    liberty = lock["locked_fingerprint"]["liberty"]["path"]
    _, sta_seconds, sta_metrics = adapter._run_opensta(
        sta_dir,
        liberty,
        CLOCK_PERIOD_NS,
        Path(vcd),
        design_top=spec["top"],
        netlist_name=netlist_name,
        activity_required_signals=(
            "clk_i", "rst_ni", "valid_i", "add_i", "a_i", "b_i"
        ),
        expected_activity_input_pins=sorted(_expected_activity_pins()),
        clock_port="clk_i",
    )
    combined = {
        "area": {
            "value": synthesis["metrics"]["cellarea"]["value"],
            "unit": "um^2",
        },
        "cell_count": {
            "value": synthesis["metrics"]["cells"]["value"],
            "unit": "cells",
        },
        **sta_metrics,
    }
    adapter._validate_ppa_metrics(combined)
    return synthesis, sta_seconds, combined


def _derive(name, spec, simulation, metrics):
    area = float(metrics["area"]["value"])
    delay = float(metrics["critical_path_delay"]["value"])
    fmax_mhz = 1000.0 / delay
    total_power_mw = float(metrics["total_power"]["value"])
    duration_ns = simulation["cycles"] * CLOCK_PERIOD_NS
    energy_per_workload_op_pj = total_power_mw * duration_ns / simulation["checks"]
    return {
        "name": name,
        "description": spec["description"],
        "status": "pass",
        "area_um2": area,
        "cells": int(metrics["cell_count"]["value"]),
        "critical_path_delay_ns": delay,
        "estimated_fmax_mhz": fmax_mhz,
        "setup_slack_ns_at_100mhz": float(metrics["slack"]["value"]),
        "average_power_mw_at_100mhz": total_power_mw,
        "internal_power_mw": float(metrics["internal_power"]["value"]),
        "switching_power_mw": float(metrics["switching_power"]["value"]),
        "leakage_power_mw": float(metrics["leakage_power"]["value"]),
        "energy_per_workload_operation_pj": energy_per_workload_op_pj,
        "simulation_cycles_for_70_operations": simulation["cycles"],
        "add_latency_cycles": spec["add_latency_cycles"],
        "mul_latency_cycles": spec["mul_latency_cycles"],
        "mul_initiation_interval_cycles": spec["mul_initiation_interval_cycles"],
        "estimated_peak_mul_throughput_mops": (
            fmax_mhz / spec["mul_initiation_interval_cycles"]
        ),
        "activity_annotation_fraction": metrics["activity_annotation"][
            "primary_input_annotation_fraction"
        ],
    }


def _markdown(rows):
    lines = [
        "# ADD/MUL PPA comparison",
        "",
        "Post-synthesis/pre-layout estimates using FreePDK45/Nangate45 typical, "
        "a 10 ns clock, identical I/O constraints, and each candidate's VCD from "
        "the same 70-operation self-checking workload.",
        "",
        "| Architecture | Area (um^2) | Delay (ns) | Est. fmax (MHz) | Power @ 100 MHz (mW) | Energy/workload op (pJ) | MUL latency / II (cycles) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {name} | {area_um2:.3f} | {critical_path_delay_ns:.3f} | "
            "{estimated_fmax_mhz:.1f} | {average_power_mw_at_100mhz:.4f} | "
            "{energy_per_workload_operation_pj:.3f} | {mul_latency_cycles} / "
            "{mul_initiation_interval_cycles} |".format(**row)
        )
    lines.extend([
        "",
        "The fmax column is 1/(critical delay) for the netlist mapped at 10 ns; it "
        "is not a timing-closure sweep. Energy/workload-op includes leakage and the "
        "actual one-request-at-a-time test duration, so it captures the cost of "
        "multi-cycle multiplication for this 50/50 ADD/MUL workload.",
        "",
    ])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variants", nargs="+", choices=sorted(VARIANTS), default=list(VARIANTS)
    )
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args(argv)

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    results = {
        "schema_version": 1,
        "method": {
            "stage": "post_synthesis_pre_layout",
            "pdk": "FreePDK45",
            "library": "NangateOpenCellLibrary_typical",
            "clock_period_ns": CLOCK_PERIOD_NS,
            "activity": "candidate-specific VCD, identical 70-operation workload",
            "power_frequency_mhz": 100.0,
        },
        "variants": [],
    }
    for name in args.variants:
        spec = VARIANTS[name]
        print("[{}] simulation".format(name), flush=True)
        simulation = _simulation(name, spec, args.clean)
        print("[{}] synthesis/timing/power".format(name), flush=True)
        synthesis, sta_seconds, metrics = _synthesis_and_power(
            name, spec, simulation["vcd"], args.clean
        )
        row = _derive(name, spec, simulation, metrics)
        row["evidence"] = {
            "simulation": simulation,
            "siliconcompiler_result": synthesis["result_file"],
            "mapped_netlist_build": synthesis["build_dir"],
            "activity_sta_dir": str(BUILD / "activity_sta" / name),
            "opensta_seconds": sta_seconds,
        }
        results["variants"].append(row)
        print(json.dumps(row, indent=2, sort_keys=True), flush=True)

    result_json = ARTIFACTS / "fma_ppa_results.json"
    result_md = ARTIFACTS / "README.md"
    result_json.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    result_md.write_text(_markdown(results["variants"]))
    print("wrote {}".format(result_json))
    print("wrote {}".format(result_md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
