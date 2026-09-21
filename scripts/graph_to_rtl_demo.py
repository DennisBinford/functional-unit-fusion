#!/usr/bin/env python3
"""Reproduce the restricted RTLIL graph-to-RTL experiment.

All outputs live below ``build/graph_to_rtl_demo`` and
``meeting-artifacts/graph-to-rtl``; the validated 2026-09-14 baseline is not
read or overwritten.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import graph_extract
import graph_match
import graph_viz
import graph_merge
import measurement_guardrails
import sc_flow
import toolchain
from verilator_flow import create_plan, execute_plan


BUILD = ROOT / "build" / "graph_to_rtl_demo"
ARTIFACT = ROOT / "meeting-artifacts" / "graph-to-rtl"
SDC = ROOT / "rtl" / "fma_experiment" / "core_10ns.sdc"
CHECKS = 70


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def graph_path(design, level):
    return BUILD / "graphs" / design / "{}.{}.graph.json".format(design, level)


def run_simulation(rtl):
    output = BUILD / "simulation"
    verilator = toolchain.find_executable("verilator", "FU_VERILATOR")
    cxx = toolchain.select_cxx()
    if not verilator or not cxx.get("coroutine_support"):
        raise RuntimeError("locked Verilator/C++20 toolchain is unavailable")
    plan = create_plan(
        executable=verilator,
        rtl_sources=[ROOT / "rtl/fma_experiment/graph_unit_a_mul.sv",
                     ROOT / "rtl/fma_experiment/graph_unit_b_add_mul_mac.sv", rtl],
        testbench_sources=[ROOT / "tb/tb_graph_fused_unit.sv"],
        design_top="graph_fused_unit", testbench_top="tb_graph_fused_unit",
        output_dir=output, jobs=1, trace=False, cxx=cxx["path"],
        cxx_family=cxx.get("family"), lint_testbench=True,
    )
    result = execute_plan(plan)
    text = Path(result["simulation_log"]).read_text(errors="replace")
    marker = "GRAPH_TO_RTL_TEST_PASS cases=70 checks=700"
    if marker not in text:
        raise RuntimeError("generated four-behavior regression did not pass 70 cases")
    result["cases"] = CHECKS
    result["checks"] = 700
    return result


def synthesize(label, top, source):
    result = None
    for attempt in range(2):
        result = sc_flow.synthesize_design_sc(top, [str(source)], str(SDC),
                                              build_dir=str(BUILD / "sc" / label),
                                              clean=True)
        if result.get("status") == "pass":
            break
    if result is None or result.get("status") != "pass":
        raise RuntimeError("synthesis failed for {}: {}".format(label, result.get("error")))
    metrics = result.get("metrics", {})
    area = float(metrics["cellarea"]["value"])
    delay = metrics.get("core_path_delay", {}).get("value")
    if delay is None:
        raise RuntimeError("{} has no in-design critical delay".format(label))
    return {"label": label, "top": top, "source": str(source),
            "source_sha256": sha(source), "area_um2": area,
            "cell_count": int(float(metrics["cells"]["value"])),
            "critical_delay_ns": float(delay),
            "estimated_fmax_mhz": 1000.0 / float(delay),
            "slack_ns": (float(metrics["setupslack"]["value"])
                          if metrics.get("setupslack", {}).get("value") is not None else None),
            "synthesis_result": result["result_file"], "toolchain": result}


def source_graphs():
    specs = [("a", "graph_unit_a_mul"), ("b", "graph_unit_b_add_mul_mac")]
    graphs = {}
    for label, design in specs:
        top, sources = graph_extract.resolve_design(design)
        path = graph_path(design, "rtlil")
        graph = graph_extract.extract(design, sources, top, "rtlil", workdir=path.parent)
        graph.save(path)
        artifact_path = ARTIFACT / "graphs" / path.name
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, artifact_path)
        graphs[label] = graph.to_dict()
    return graphs


def make_control(graphs):
    mul_a = sorted(node["id"] for node in graphs["a"]["nodes"] if node["kind"] == "$mul")
    mul_b = sorted(node["id"] for node in graphs["b"]["nodes"] if node["kind"] == "$mul")
    if len(mul_a) != 1 or len(mul_b) != 1:
        raise RuntimeError("source pair must provide exactly one multiplier each")
    add_b = sorted(node["id"] for node in graphs["b"]["nodes"] if node["kind"] == "$add")
    if len(add_b) != 1:
        raise RuntimeError("source B must provide exactly one final adder")
    return {
        "schema": "fu-control-spec/v1", "version": 1,
        "graph_a": graphs["a"]["design"], "graph_b": graphs["b"]["design"], "level": "rtlil",
        "generated_design": "graph_fused_graph_unit_pair", "generated_top": "graph_fused_unit",
        "inserted_ports": [{"name": "source_select_i", "direction": "input", "width": 1}],
        "shared_operator": {"operation": "mul", "match_pair": {"a": mul_a[0], "b": mul_b[0]}},
        "shared_control": {"port": "source_select_i", "active_value": 1,
                            "select_semantics": "1 selects graph A; 0 selects graph B"},
        "outputs": [
            {"graph": "a", "port": "product_a_o", "fused_port": "product_a_o", "behavior": "A_MUL", "select_when": {"source_select_i": 1}},
            {"graph": "b", "port": "result_b_o", "fused_port": "result_b_o", "behavior": "B_ADD_MUL_MAC", "select_when": {"source_select_i": 0}},
        ],
        "required_paths": [{"graph": "b", "from_port": "c_b_i", "to_node": add_b[0]}],
        "modes": {"A_MUL": {"source_select_i": 1},
                  "B_ADD": {"source_select_i": 0, "add_mode_b_i": 1, "mul_mode_b_i": 0, "mac_mode_b_i": 0},
                  "B_MUL": {"source_select_i": 0, "add_mode_b_i": 0, "mul_mode_b_i": 1, "mac_mode_b_i": 0},
                  "B_MAC": {"source_select_i": 0, "add_mode_b_i": 0, "mul_mode_b_i": 0, "mac_mode_b_i": 1}},
    }


def vertical_study():
    study = {"schema": "graph-to-rtl-vertical-study/v1", "levels": {},
             "note": "Only RTLIL is used for executable generation; gate/AIG are structural observations."}
    for level in ("rtlil", "gate", "aig"):
        started = time.monotonic()
        entries = {}
        for label, design in (("a", "graph_unit_a_mul"), ("b", "graph_unit_b_add_mul_mac")):
            top, sources = graph_extract.resolve_design(design)
            path = BUILD / "graphs" / design / "{}.{}.graph.json".format(design, level)
            graph = graph_extract.extract(design, sources, top, level, workdir=path.parent)
            graph.save(path)
            artifact_graph = ARTIFACT / "studies" / level / path.name
            artifact_graph.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, artifact_graph)
            figure = ARTIFACT / "studies" / level / "{}.{}".format(design, level)
            graph_viz.render(graph, figure, image_format="svg", max_nodes=250,
                             title="{} {} structural graph".format(design, level))
            entries[label] = {"design": design, "graph": str(artifact_graph), "stats": graph.stats(),
                              "svg": str(Path(str(figure) + ".svg"))}
        a, b = json.loads(Path(entries["a"]["graph"]).read_text()), json.loads(Path(entries["b"]["graph"]).read_text())
        match_started = time.monotonic()
        match = graph_match.compare(a, b, mode="exact")
        match_time = time.monotonic() - match_started
        match_path = ARTIFACT / "studies" / "vertical" / "{}.match.json".format(level)
        save(match_path, match)
        entries["match"] = {"path": str(match_path), "matched_nodes": match["common_node_count"],
                            "coverage": match["coverage"], "largest_bounded_subgraph": max(
                                [item["node_count"] for item in match.get("common_subgraphs", [])] or [0]),
                            "runtime_seconds": match_time}
        study["levels"][level] = {"designs": entries, "runtime_seconds": time.monotonic() - started}
    save(ARTIFACT / "studies" / "vertical_study.json", study)
    return study


def main():
    if BUILD.exists():
        shutil.rmtree(BUILD)
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    graphs = source_graphs()
    match = graph_match.compare(graphs["a"], graphs["b"], mode="exact")
    match_path = ARTIFACT / "match.rtlil.json"; save(match_path, match)
    merge = graph_merge.merge_graphs(graphs["a"], graphs["b"], match).to_dict()
    merge_path = ARTIFACT / "structural_merge.rtlil.json"; save(merge_path, merge)
    control = make_control(graphs)
    control_path = ARTIFACT / "control_spec.json"; save(control_path, control)

    graph_a_path = ARTIFACT / "graphs" / graph_path(graphs["a"]["design"], "rtlil").name
    graph_b_path = ARTIFACT / "graphs" / graph_path(graphs["b"]["design"], "rtlil").name
    plan_path = ARTIFACT / "executable_graph.rtlil.json"
    from executable_graph import realize
    plan = realize(graphs["a"], graphs["b"], match, merge, control)
    save(plan_path, plan)
    rtl_path = ARTIFACT / "graph_fused_unit.sv"
    from graph_rtl_emit import emit, validate_manifest, write_manifest
    rtl_path.write_text(emit(plan))
    manifest_path = ARTIFACT / "graph_fused_unit.manifest.json"
    write_manifest(manifest_path, plan_path, rtl_path, {
        "graph_a": graph_a_path, "graph_b": graph_b_path, "match": match_path,
        "merge": merge_path, "control_spec": control_path,
        "realizer": ROOT / "executable_graph.py", "emitter": ROOT / "graph_rtl_emit.py",
        "graph_extract": ROOT / "graph_extract.py", "graph_match": ROOT / "graph_match.py",
        "graph_merge": ROOT / "graph_merge.py",
        "graph_backend": ROOT / "graph_backend.py",
        "demo_script": ROOT / "scripts/graph_to_rtl_demo.py",
        "source_a": ROOT / "rtl/fma_experiment/graph_unit_a_mul.sv",
        "source_b": ROOT / "rtl/fma_experiment/graph_unit_b_add_mul_mac.sv",
        "testbench": ROOT / "tb/tb_graph_fused_unit.sv", "sdc": SDC,
        "toolchain_lock": ROOT / "build/toolchain.lock.json",
        "liberty": Path(json.loads((ROOT / "build/toolchain.lock.json").read_text())
                         ["fingerprint"]["liberty"]["path"])})
    manifest_validation = validate_manifest(manifest_path, ROOT)
    simulation = run_simulation(rtl_path)

    guardrails = {}
    for label, top, source in (("unit_a", "graph_unit_a_mul", ROOT / "rtl/fma_experiment/graph_unit_a_mul.sv"),
                               ("unit_b", "graph_unit_b_add_mul_mac", ROOT / "rtl/fma_experiment/graph_unit_b_add_mul_mac.sv"),
                               ("fused", "graph_fused_unit", rtl_path)):
        evidence = measurement_guardrails.inspect_design(source, top, 10.0, SDC)
        guardrails[label] = evidence
    if guardrails["unit_a"]["operator_counts"].get("$mul") != 1 or guardrails["unit_b"]["operator_counts"].get("$mul") != 1 or guardrails["unit_b"]["operator_counts"].get("$add") != 1:
        raise RuntimeError("source guardrail operator counts are not the required 1/1 pair")
    if guardrails["fused"]["operator_counts"].get("$mul") != 1 or guardrails["fused"]["operator_counts"].get("$add") != 1:
        raise RuntimeError("generated guardrail did not retain one shared multiplier and one adder")

    ppa = {"unit_a": synthesize("unit_a", "graph_unit_a_mul", ROOT / "rtl/fma_experiment/graph_unit_a_mul.sv"),
           "unit_b": synthesize("unit_b", "graph_unit_b_add_mul_mac", ROOT / "rtl/fma_experiment/graph_unit_b_add_mul_mac.sv"),
           "fused": synthesize("fused", "graph_fused_unit", rtl_path)}
    literal_sum = ppa["unit_a"]["area_um2"] + ppa["unit_b"]["area_um2"]
    fused_area = ppa["fused"]["area_um2"]
    result = {"schema": "graph-to-rtl-demo/v1", "status": "pass",
              "source_pair": {"a": graphs["a"]["design"], "b": graphs["b"]["design"]},
              "semantics": {"ADD": "zero_extend_64(a) + zero_extend_64(b)",
                             "MUL": "a * b, producing 64 bits",
                             "MAC": "(a * b + zero_extend_64(c)) modulo 2^64"},
              "artifacts": {"graph_a": str(graph_a_path), "graph_b": str(graph_b_path),
                            "match": str(match_path), "structural_merge": str(merge_path),
                            "control_spec": str(control_path), "executable_graph": str(plan_path),
                            "generated_rtl": str(rtl_path), "manifest": str(manifest_path)},
              "structural_guardrails": {key: {"operator_counts": value["operator_counts"],
                                                "all_cell_counts": value["all_cell_counts"],
                                                "ports": value["ports"], "source_sha256": value["source_sha256"]}
                                         for key, value in guardrails.items()},
              "simulation": simulation, "manifest_validation": manifest_validation,
              "performance_interpretation": {
                  "combinational": True,
                  "registered_latency_cycles": None,
                  "effective_ii_cycles": None,
                  "effective_ii_assumption": "II is not intrinsic to an unclocked combinational module; a registered wrapper is required.",
                  "simultaneous_throughput": {"separate_units": 2, "fused_shared_multiplier": 1},
                  "economic_workload": "single-stream or mutually exclusive A/B demand; the fused unit does not preserve simultaneous A/B multiplier throughput."
              },
              "vertical_study": vertical_study(),
              "separate_a_plus_b_pretech_counts": {
                  "$mul": guardrails["unit_a"]["all_cell_counts"].get("$mul", 0) + guardrails["unit_b"]["all_cell_counts"].get("$mul", 0),
                  "$add": guardrails["unit_a"]["all_cell_counts"].get("$add", 0) + guardrails["unit_b"]["all_cell_counts"].get("$add", 0),
              },
              "ppa": {"unit_a": ppa["unit_a"], "unit_b": ppa["unit_b"], "fused": ppa["fused"],
                      "literal_area_sum_um2": literal_sum, "fused_minus_sum_um2": fused_area - literal_sum,
                      "fused_vs_sum_percent": 100.0 * (fused_area - literal_sum) / literal_sum,
                      "criterion_fused_below_literal_sum": fused_area < literal_sum,
                      "mux_control_overhead": {"pretech_mux_count": guardrails["fused"]["all_cell_counts"].get("$mux", 0),
                                                "pretech_add_count": guardrails["fused"]["operator_counts"].get("$add", 0),
                                                "pretech_mul_count": guardrails["fused"]["operator_counts"].get("$mul", 0)}},
              "latency_and_ii": {mode: {"combinational": True, "registered_latency_cycles": None,
                                         "initiation_interval_cycles": None,
                                         "registered_wrapper_assumption": None}
                                 for mode in ("A_MUL", "B_ADD", "B_MUL", "B_MAC")},
              "limitations": ["RTLIL subset only: ports, constants, unsigned add/mul/mux, zero extension.",
                              "The realized units are combinational; registered latency and II are undefined without a supplied wrapper.",
                              "Separate A and B can demand two multipliers concurrently; fused time-multiplexes one shared multiplier.",
                              "Gate/AIG matches are structural observations, not realized savings.",
                              "This is not universal arbitrary-graph fusion; larger Ibex ALU versus multiplier/divider is the next horizontal target."]}
    save(ARTIFACT / "graph_to_rtl_results.json", result)
    (ARTIFACT / "graph_to_rtl_results.md").write_text(
        "# Executable RTLIL graph-to-RTL result\n\n"
        "The source-derived fused unit has pre-techmap counts ``$mul=1``, ``$add=1``, "
        "and ``$mux={}``.\n\n".format(result["ppa"]["mux_control_overhead"]["pretech_mux_count"])
        + "| design | area (um^2) | cells | critical delay (ns) |\n|---|---:|---:|---:|\n"
        + "\n".join("| {} | {:.3f} | {} | {:.3f} |".format(k, v["area_um2"], v["cell_count"], v["critical_delay_ns"])
                   for k, v in ppa.items())
        + "\n\nLiteral mapped A+B sum: {:.3f} um^2; fused delta: {:.3f} um^2 ({:.3f}%). Criterion: {}.\n".format(
            literal_sum, fused_area - literal_sum, result["ppa"]["fused_vs_sum_percent"],
            "PASS" if fused_area < literal_sum else "FAIL")
        + "\nRaw designs are combinational: registered latency and II are undefined without a wrapper. "
          "Separate units can sustain simultaneous A/B demand; fused has one shared multiplier.\n")
    print(json.dumps({"results": str(ARTIFACT / "graph_to_rtl_results.json"), "rtl": str(rtl_path),
                      "simulation_checks": simulation["checks"], "literal_sum_um2": literal_sum,
                      "fused_area_um2": fused_area}, indent=2))


if __name__ == "__main__":
    main()
