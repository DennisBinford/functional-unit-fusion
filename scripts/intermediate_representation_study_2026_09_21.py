#!/usr/bin/env python3
"""Reproducible three-level fusion-opportunity study for Ibex LT/GE.

This is a follow-up study, not a replacement for the completed 2026-09-21
bundle.  It writes only to ``meeting-artifacts/2026-09-21-intermediate-search``
and to an ignored build directory.  The selected middle stop is the existing
specialized word-level RTLIL flow; gate graphs are structural evidence only.
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
from executable_graph import realize
from fusion_decision import evaluate_measured_result, preflight_candidate
from graph_match import compare
from graph_merge import merge_graphs
from graph_rtl_emit import emit
import hierarchy_flow
import sc_flow
from toolchain import select_cxx


OUT = ROOT / "meeting-artifacts" / "2026-09-21-intermediate-search"
BUILD = ROOT / "build" / "intermediate_search_2026_09_21"
IBEX_RTL = ROOT / "third_party" / "ibex" / "rtl"
UPSTREAM_ALU = IBEX_RTL / "ibex_alu.sv"
UPSTREAM_PKG = IBEX_RTL / "ibex_pkg.sv"
UPSTREAM_LOCK = ROOT / "designs" / "ibex.source.json"
WRAPPER_RTL = ROOT / "rtl" / "ibex_fusion" / "ibex_alu_operation_wrappers.sv"
SV2V = ROOT / "tools" / "oss-cad-suite" / "bin" / "sv2v"
VERILATOR = ROOT / "tools" / "oss-cad-suite" / "bin" / "verilator"
OPS = {"LT": "ibex_alu_lt32", "GE": "ibex_alu_ge32"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(path):
    path = Path(path).resolve()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path)}


def portable_value(value):
    """Replace checkout-specific provenance without changing graph semantics."""
    if isinstance(value, str):
        return value.replace(str(ROOT), "$REPO")
    if isinstance(value, list):
        return [portable_value(item) for item in value]
    if isinstance(value, dict):
        return {key: portable_value(item) for key, item in value.items()}
    return value


def portableize_graph(graph):
    for node in graph.nodes:
        node.id = portable_value(node.id)
        node.label = portable_value(node.label)
        node.attrs = portable_value(node.attrs)
    for edge in graph.edges:
        edge.src = portable_value(edge.src)
        edge.dst = portable_value(edge.dst)
        edge.src_port = portable_value(edge.src_port)
        edge.dst_port = portable_value(edge.dst_port)
        edge.attrs = portable_value(edge.attrs)
    graph.attrs = portable_value(graph.attrs)
    return graph


def run(command, log=None):
    command = [str(item) for item in command]
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        with Path(log).open("w") as handle:
            result = subprocess.run(command, cwd=ROOT, stdout=handle,
                                    stderr=subprocess.STDOUT)
    else:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        detail = "see {}".format(log) if log else (result.stdout + result.stderr)[-3000:]
        raise RuntimeError("command failed: {} ({})".format(" ".join(command), detail))


def convert_sources():
    converted = BUILD / "ibex_alu_operation_wrappers.v"
    converted.parent.mkdir(parents=True, exist_ok=True)
    run([SV2V, "--write", converted, UPSTREAM_PKG, UPSTREAM_ALU, WRAPPER_RTL],
        BUILD / "sv2v.log")
    return converted


def specialized_graph(converted, op):
    top = OPS[op]
    netlist = BUILD / "specialized" / (top + ".json")
    netlist.parent.mkdir(parents=True, exist_ok=True)
    yosys = ("read_verilog {}; hierarchy -top {}; flatten; proc; opt -full; "
             "opt_muxtree; memory_map; opt -fast; write_json {}").format(
                 converted, top, netlist)
    graph_extract.run_yosys(yosys)
    graph = graph_extract._graph_from_netlist(json.loads(netlist.read_text()), top,
                                              top, "rtlil")
    graph.attrs.update({"source_operation": op, "upstream_module": "ibex_alu",
                        "specialization": "constant_operator"})
    return portableize_graph(graph)


def save_graph(graph, path):
    return graph.save(path)


def graph_summary(graph):
    return graph.stats()


def level_graphs(converted, op):
    result = {}
    for level in ("ast", "module", "gate"):
        work = BUILD / "generic" / op / level
        started = time.perf_counter()
        graph = graph_extract.extract(OPS[op], [str(converted)], OPS[op], level, work)
        portableize_graph(graph)
        elapsed = time.perf_counter() - started
        result[level] = {"graph": graph, "runtime_seconds": elapsed}
    started = time.perf_counter()
    graph = specialized_graph(converted, op)
    elapsed = time.perf_counter() - started
    result["rtlil"] = {"graph": graph, "runtime_seconds": elapsed}
    return result


def hierarchy_report(levels):
    a = {level: item["graph"].to_dict() for level, item in levels["LT"].items()
         if level in hierarchy_flow.LEVELS}
    b = {level: item["graph"].to_dict() for level, item in levels["GE"].items()
         if level in hierarchy_flow.LEVELS}
    report = hierarchy_flow.analyze_pair(a, b, name="ibex_lt_ge_independent",
                                         evaluate_all=True)
    for level in report["metrics"]:
        report["metrics"][level]["extraction_runtime_seconds"] = {
            "a": levels["LT"][level]["runtime_seconds"],
            "b": levels["GE"][level]["runtime_seconds"],
        }
    # The full matcher payload contains duplicated bounded subgraph details
    # and is useful scratch data, but it is not needed to reproduce the study
    # claim.  Keep the compact decision/metric record publishable.
    return {
        key: value for key, value in report.items() if key != "matches"
    }


def candidate_contract(match, graph_a, graph_b):
    add = next(item for item in match["matched_nodes"]
               if next(node for node in graph_a["nodes"] if node["id"] == item["a"])["kind"] == "$add")
    operator_match_count = sum(
        1 for item in match["matched_nodes"]
        if next(node for node in graph_a["nodes"] if node["id"] == item["a"])["kind"]
        not in ("port_in", "port_out", "const")
    )
    return {
        "candidate_id": "ibex_lt_ge_independent",
        "source": {"operations": ["LT", "GE"], "module": "ibex_alu",
                    "selection": "selected-source independent-input"},
        "interface": {
            "operand_sources": [
                {"name": "source_a", "width": 32, "independent": True},
                {"name": "source_b", "width": 32, "independent": True},
            ],
            "externally_visible_results": 1,
            "selection_mode": "client_selection",
            "simultaneous_use_preserved": False,
            "latency": {"kind": "combinational", "registered_latency_cycles": None},
            "throughput": {"available_clients_per_evaluation": 1,
                           "assumption": "one selected client per evaluation"},
        },
        "requirements": {"equal_external_interface": True,
                          "simultaneous_use_required": False,
                          "required_clients_per_evaluation": 1},
        "shared_resource": {"kind": "$add", "width": 34,
                             "role": "shared signed/unsigned comparison adder cone"},
        "match_evidence": {"matched_operator_count": operator_match_count,
                           "exact_match_count": match["common_node_count"],
                           "largest_connected_match": max(
                               (item["node_count"] for item in match.get("common_subgraphs", [])),
                               default=0),
                           "shared_resource_kind": "$add", "shared_resource_pair": add},
        "expected_commonality": {
            "selected": "34-bit adder plus comparison control cone",
            "not_claimed": "matched nodes do not predict area savings",
        },
        "hierarchy_stopping_rule": {
            "module": "continue when only leaf/specialization wrappers match",
            "rtlil": "stop for executable assessment when connected operator match is present",
            "gate": "inspect only if RTLIL is not actionable; structural only",
        },
    }


def baseline_rtl():
    return """// Equal-interface baseline for the LT/GE selected-source comparison.
module ibex_lt_ge_separate (
    input logic [31:0] b_operand_a_i,
    input logic [31:0] b_operand_b_i,
    input logic [31:0] operand_a_i,
    input logic [31:0] operand_b_i,
    input logic source_select_i,
    output logic [31:0] result_o
);
    logic [31:0] lt_result;
    logic [31:0] ge_result;
    ibex_alu_lt32 u_lt(.operand_a_i(operand_a_i), .operand_b_i(operand_b_i), .result_o(lt_result));
    ibex_alu_ge32 u_ge(.operand_a_i(b_operand_a_i), .operand_b_i(b_operand_b_i), .result_o(ge_result));
    assign result_o = source_select_i ? lt_result : ge_result;
endmodule
"""


def testbench():
    return r"""`timescale 1ns/1ps
module tb_intermediate_search;
  logic [31:0] b_operand_a_i, b_operand_b_i, operand_a_i, operand_b_i;
  logic source_select_i;
  logic [31:0] separate_result, fused_result;
  integer checks, errors, i;
  logic [31:0] state;
  ibex_lt_ge_separate separate(
    .b_operand_a_i, .b_operand_b_i, .operand_a_i, .operand_b_i,
    .source_select_i, .result_o(separate_result));
  ibex_fused_lt_ge32 fused(
    .b_operand_a_i, .b_operand_b_i, .operand_a_i, .operand_b_i,
    .source_select_i, .result_o(fused_result));
  function automatic [31:0] model_lt(input [31:0] x, input [31:0] y);
    model_lt = ($signed(x) < $signed(y)) ? 32'b1 : 32'b0;
  endfunction
  function automatic [31:0] model_ge(input [31:0] x, input [31:0] y);
    model_ge = ($signed(x) >= $signed(y)) ? 32'b1 : 32'b0;
  endfunction
  function automatic [31:0] next_rand(input [31:0] x);
    begin next_rand = x ^ (x << 13); next_rand = next_rand ^ (next_rand >> 17); next_rand = next_rand ^ (next_rand << 5); end
  endfunction
  task automatic check(input [31:0] x, input [31:0] y, input [31:0] u, input [31:0] v);
    begin
      operand_a_i=x; operand_b_i=y; b_operand_a_i=u; b_operand_b_i=v;
      source_select_i=1'b1; #1; checks=checks+1;
      if (fused_result !== separate_result || fused_result !== model_lt(x,y)) errors=errors+1;
      source_select_i=1'b0; #1; checks=checks+1;
      if (fused_result !== separate_result || fused_result !== model_ge(u,v)) errors=errors+1;
    end
  endtask
  initial begin
    checks=0; errors=0; state=32'h1;
    check(32'h0,32'h0,32'h0,32'h0);
    check(32'h80000000,32'h7fffffff,32'h7fffffff,32'h80000000);
    check(32'hffffffff,32'h1,32'h1,32'hffffffff);
    for (i=0;i<256;i=i+1) begin
      state=next_rand(state); check(state,next_rand(state),next_rand(next_rand(state)),next_rand(next_rand(next_rand(state))));
    end
    if (errors != 0) $fatal(1,"LT/GE equivalence failed: %0d", errors);
    $display("INTERMEDIATE_SEARCH_TEST_PASS checks=%0d vectors=%0d", checks, checks/2); $finish;
  end
endmodule
"""


def make_control(graph_a, graph_b, match):
    add = next(item for item in match["matched_nodes"]
               if next(node for node in graph_a["nodes"] if node["id"] == item["a"])["kind"] == "$add")
    return {
        "schema": "fu-control-spec/v1", "version": 1,
        "graph_a": graph_a["design"], "graph_b": graph_b["design"],
        "level": "rtlil", "generated_design": "ibex_fused_lt_ge32",
        "generated_top": "ibex_fused_lt_ge32",
        "inserted_ports": [{"direction": "input", "name": "source_select_i", "width": 1}],
        "shared_control": {"port": "source_select_i", "encoding": {"0": "graph_b", "1": "graph_a"}},
        "shared_operator": {"operation": "add", "match_pair": {"a": add["a"], "b": add["b"]}},
        "outputs": [{"behavior": "selected_source_result", "fused_port": "result_o",
                     "sources": [{"graph": "a", "port": "result_o", "select_when": {"source_select_i": 1}},
                                 {"graph": "b", "port": "result_o", "select_when": {"source_select_i": 0}}]}],
        "required_paths": [
            {"from_port": "operand_a_i", "graph": "a", "to_node": add["a"]},
            {"from_port": "operand_b_i", "graph": "a", "to_node": add["a"]},
            {"from_port": "operand_a_i", "graph": "b", "to_node": add["b"]},
            {"from_port": "operand_b_i", "graph": "b", "to_node": add["b"]},
        ],
    }


def run_simulation(converted, fused, separate, tb):
    sim_build = BUILD / "simulation"
    sim_build.mkdir(parents=True, exist_ok=True)
    binary = sim_build / "intermediate_search_sim"
    cxx = select_cxx()
    run([VERILATOR, "--binary", "--timing", "-Wno-fatal", "-Wno-TIMESCALEMOD",
         "--compiler", cxx.get("family", "gcc"),
         "-MAKEFLAGS", "CXX={}".format(cxx["path"]),
         "-MAKEFLAGS", "LINK={}".format(cxx["path"]),
         "--top-module", "tb_intermediate_search", "-o", binary,
         "--Mdir", sim_build / "obj_dir", converted, separate, fused, tb],
        sim_build / "compile.log")
    result = subprocess.run([str(binary)], cwd=sim_build, capture_output=True, text=True)
    (OUT / "simulation.log").write_text(
        portable_value(result.stdout + result.stderr)
    )
    if result.returncode or "INTERMEDIATE_SEARCH_TEST_PASS" not in result.stdout:
        raise RuntimeError("correctness simulation failed; see {}".format(OUT / "simulation.log"))
    marker = next(line for line in result.stdout.splitlines() if "INTERMEDIATE_SEARCH_TEST_PASS" in line)
    checks = int(marker.split("checks=")[1].split()[0])
    return {"status": "pass", "tool": "verilator", "checks": checks,
            "vectors": checks // 2, "log": record(OUT / "simulation.log")}


def ppa(top, sources, sdc, build):
    result = sc_flow.synthesize_design_sc(top, [str(item) for item in sources], str(sdc),
                                          str(build), clean=True)
    # SiliconCompiler can record a negative scheduler bookkeeping interval when
    # a task starts and exits at the same instant.  Follow the established
    # project flow's fresh-build retry rather than consuming a partial result.
    if result.get("status") != "pass":
        result = sc_flow.synthesize_design_sc(top, [str(item) for item in sources], str(sdc),
                                              str(build), clean=True)
    metrics = result.get("metrics", {})
    def value(name):
        return (metrics.get(name) or {}).get("value")
    record = {"status": result.get("status"), "area_um2": value("cellarea"),
            "critical_delay_ns": value("core_path_delay"),
            "fmax_mhz": ((metrics.get("fmax_core") or {}).get("value") or 0) / 1e6,
            "cell_count": value("cells"), "timing_met": result.get("timing_met"),
            "metrics": metrics}
    if record["status"] != "pass" or record["area_um2"] is None or record["critical_delay_ns"] is None:
        raise RuntimeError("synthesis did not produce a valid PPA point for {}: {}".format(top, result))
    return record


def representation_assessment(levels, hierarchy):
    def compact_match(match):
        return {
            "common_node_count": match.get("common_node_count", 0),
            "coverage": match.get("coverage", {}),
            "largest_connected_subgraph": max(
                (item.get("node_count", 0) for item in match.get("common_subgraphs", [])),
                default=0,
            ),
            "common_subgraph_count": len(match.get("common_subgraphs", [])),
            "engine": match.get("engine", "native"),
        }

    rows = {}
    ast_started = time.perf_counter()
    ast_match = compare(levels["LT"]["ast"]["graph"].to_dict(),
                        levels["GE"]["ast"]["graph"].to_dict(),
                        mode="exact", engine="native")
    ast_runtime = time.perf_counter() - ast_started
    for level in ("ast", "module", "rtlil", "gate"):
        metric = hierarchy["metrics"].get(level)
        match = compact_match(ast_match) if level == "ast" else metric
        rows[level] = {
            "left": graph_summary(levels["LT"][level]["graph"]),
            "right": graph_summary(levels["GE"][level]["graph"]),
            "extraction_runtime_seconds": {
                "left": levels["LT"][level]["runtime_seconds"],
                "right": levels["GE"][level]["runtime_seconds"],
            },
            "match": match,
        }
        if level == "ast":
            rows[level]["match_runtime_seconds"] = ast_runtime
    return {
        "selected_middle_stop": "rtlil",
        "reason": "specialized word-level RTLIL exposes a six-node connected match including the 34-bit adder and is supported by the executable emitter; AST is source/coding-style dependent, module is an opaque specialization boundary, and gate is larger structural-only evidence",
        "levels": rows,
        "gate_reconstruction": "not claimed; gate/AIG evidence is structural only",
        "name_and_style": {
            "module_name_independence": "covered by parameterized-child and renamed-module regression tests",
            "parameter_specialization": "retained: different parameter values are not treated as identical children",
            "coding_style_variation": "covered by assign versus always_comb word-level regression test",
        },
    }


def provenance_probes():
    """Exercise name independence, parameter identity, and coding-style change."""
    from graph_extract import _graph_from_hierarchy

    def hierarchy(child_name, mode):
        specialized = "$paramod\\{}\\MODE={}".format(child_name, mode)
        return {"modules": {
            "top": {"ports": {}, "cells": {"u_child": {"type": specialized, "parameters": {}}}},
            specialized: {"ports": {}, "cells": {}},
        }}

    same_mode_a = _graph_from_hierarchy(hierarchy("child_a", 1), "left", "top").to_dict()
    same_mode_b = _graph_from_hierarchy(hierarchy("child_b", 1), "right", "top").to_dict()
    different_mode = _graph_from_hierarchy(hierarchy("child_b", 2), "right", "top").to_dict()
    name_match = compare(same_mode_a, same_mode_b)
    specialization_match = compare(same_mode_a, different_mode)

    probe = OUT / "probes"
    probe.mkdir(parents=True, exist_ok=True)
    assign = probe / "assign.sv"
    procedural = probe / "procedural.sv"
    assign.write_text("module assign_style(input logic [7:0] a_i, b_i, output logic [7:0] y_o);\n"
                      "assign y_o = a_i + b_i;\nendmodule\n")
    procedural.write_text("module procedural_style(input logic [7:0] a_i, b_i, output logic [7:0] y_o);\n"
                          "always_comb begin y_o = b_i + a_i; end\nendmodule\n")
    left = graph_extract.extract("assign", [str(assign)], "assign_style", "rtlil",
                                 BUILD / "probes" / "assign")
    right = graph_extract.extract("procedural", [str(procedural)], "procedural_style", "rtlil",
                                  BUILD / "probes" / "procedural")
    style_match = compare(left.to_dict(), right.to_dict())
    return {
        "parameterized_module_name_probe": {
            "same_specialization_different_child_names_common_nodes": name_match["common_node_count"],
            "different_parameter_specialization_common_nodes": specialization_match["common_node_count"],
            "interpretation": "child names are ignored for equivalent parameterized instances, but parameter specialization remains part of identity",
        },
        "coding_style_probe": {
            "forms": ["continuous assign", "procedural always_comb with commuted operands"],
            "rtlil_common_nodes": style_match["common_node_count"],
            "rtlil_common_operators": sum(1 for item in style_match["matched_nodes"]
                                           if next(node for node in left.to_dict()["nodes"] if node["id"] == item["a"])["kind"] not in ("port_in", "port_out", "const")),
            "interpretation": "word-level normalization sees the same adder despite source-style variation",
        },
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    converted = convert_sources()
    levels = {op: level_graphs(converted, op) for op in ("LT", "GE")}
    for op, items in levels.items():
        for level, item in items.items():
            # Module and gate graphs are regenerated by this script and their
            # summary is sufficient for the report.  Retain only the selected
            # word-level RTLIL graphs as compact inspectable evidence.
            if level == "rtlil":
                save_graph(item["graph"], OUT / "selected_graphs" / op.lower() / "rtlil.graph.json")

    hierarchy = hierarchy_report(levels)
    (OUT / "hierarchy_analysis.json").write_text(json.dumps(hierarchy, indent=2, sort_keys=True) + "\n")
    (OUT / "representation_assessment.json").write_text(json.dumps(
        representation_assessment(levels, hierarchy), indent=2, sort_keys=True) + "\n")
    probes = provenance_probes()
    (OUT / "provenance_probes.json").write_text(json.dumps(probes, indent=2, sort_keys=True) + "\n")

    matrix = json.loads((OUT.parent / "ibex-fusion" / "candidate_matrix.json").read_text())
    selected_candidates = []
    for pair in (("LT", "GE"), ("LT", "LTU"), ("GE", "GEU")):
        row = next(item for item in matrix["rows"]
                   if item["source_operations"] == {"a": pair[0], "b": pair[1]})
        selected_candidates.append({
            "pair": pair,
            "exact_match_count": row["exact_match_count"],
            "largest_connected_exact_match": row["largest_connected_exact_match"],
            "current_graph_to_rtl_realization_possible": row["current_graph_to_rtl_realization_possible"],
            "selection": "chosen" if pair == ("LT", "GE") else "surveyed_alternative",
        })
    (OUT / "candidate_survey.json").write_text(json.dumps({
        "source": record(OUT.parent / "ibex-fusion" / "candidate_matrix.json"),
        "pairs": selected_candidates,
        "selection_reason": "LT/GE has the largest connected exact word-level match and exposes a shared 34-bit adder plus comparison control.",
    }, indent=2, sort_keys=True) + "\n")

    graph_a = levels["LT"]["rtlil"]["graph"].to_dict()
    graph_b = levels["GE"]["rtlil"]["graph"].to_dict()
    match = compare(graph_a, graph_b, mode="exact", engine="native")
    merged = merge_graphs(graph_a, graph_b, match)
    control = make_control(graph_a, graph_b, match)
    contract = candidate_contract(match, graph_a, graph_b)
    # The separate baseline has the same external shape by construction.  The
    # realizer receives that explicit comparison contract so its preflight is
    # the same gate used by the standalone decision record.
    contract["comparison_contract"] = {"interface": contract["interface"]}
    preflight = preflight_candidate(contract, contract, {
        "pretechmap": {"mux_count": 3},
        "mapped_mux2_note": "No mapped-cell result is claimed at preflight.",
    })
    if preflight["decision"] != "MEASURE":
        raise RuntimeError("new LT/GE contract did not reach MEASURE: {}".format(preflight))
    plan = realize(graph_a, graph_b, match, merged.to_dict(), control,
                   candidate_contract=contract,
                   routing_evidence={"pretechmap": {"mux_count": 3}})
    fused_rtl = OUT / "rtl" / "ibex_fused_lt_ge32.sv"
    fused_rtl.parent.mkdir(parents=True, exist_ok=True)
    fused_rtl.write_text(emit(plan))
    separate_rtl = OUT / "rtl" / "ibex_lt_ge_separate.sv"
    separate_rtl.write_text(baseline_rtl())
    tb = OUT / "tb_intermediate_search.sv"
    tb.write_text(testbench())
    for name, value in (("match.rtlil.json", match), ("structural_merge.rtlil.json", merged.to_dict()),
                        ("control_spec.json", control), ("executable_graph.rtlil.json", plan),
                        ("candidate_contract.json", contract), ("preflight_decision.json", preflight)):
        (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    simulation = run_simulation(converted, fused_rtl, separate_rtl, tb)
    sdc = OUT / "intermediate_search.sdc"
    sdc.write_text("set clk_period 2.5\ncreate_clock -name vclk -period $clk_period\n"
                   "set_input_delay [expr {0.1 * $clk_period}] -clock vclk [all_inputs]\n"
                   "set_output_delay [expr {0.1 * $clk_period}] -clock vclk [all_outputs]\n")
    separate_ppa = ppa("ibex_lt_ge_separate", [converted, separate_rtl], sdc, BUILD / "ppa" / "separate")
    fused_ppa = ppa("ibex_fused_lt_ge32", [fused_rtl], sdc, BUILD / "ppa" / "fused")
    measured = evaluate_measured_result(preflight, separate_ppa, fused_ppa, simulation,
                                        {"max_area_increase_percent": 0.0,
                                         "max_delay_increase_ns": None})
    measured_record = {"decision": measured, "baseline": separate_ppa, "candidate": fused_ppa,
                       "comparison": "equal external ports, same source, SDC, and synthesis backend",
                       "policy": {"max_area_increase_percent": 0.0,
                                  "max_delay_increase_ns": None,
                                  "note": "No performance bound was invented; delay tradeoff is reported."}}
    (OUT / "measured_result.json").write_text(json.dumps(measured_record, indent=2, sort_keys=True) + "\n")

    provenance_copy = OUT / "provenance" / "ibex_alu_operation_wrappers.v"
    provenance_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(converted, provenance_copy)
    provenance_lock = OUT / "provenance" / "ibex.source.json"
    provenance_lock.write_text(portable_value(UPSTREAM_LOCK.read_text()))

    source_paths = [WRAPPER_RTL,
                    ROOT / "graph_extract.py", ROOT / "graph_match.py", ROOT / "hierarchy_flow.py",
                    ROOT / "fusion_decision.py", ROOT / "executable_graph.py", ROOT / "graph_rtl_emit.py",
                    Path(__file__), ROOT / "tests" / "test_intermediate_representation_study.py",
                    OUT.parent / "ibex-fusion" / "candidate_matrix.json", provenance_copy,
                    provenance_lock]
    manifest = {"schema": "fu-intermediate-search-manifest/v1", "study": "ibex_lt_ge_independent",
                "artifacts": {}, "source_hashes": [record(path) for path in source_paths if path.is_file()]}
    for path in sorted(OUT.rglob("*")):
        relative = path.relative_to(OUT)
        # Raw AST/gate/Yosys exploration is reproducible scratch output, not a
        # compact publishable artifact.  The generator, summaries, selected
        # RTLIL graphs, and machine-readable decisions are sufficient.
        excluded = (
            relative.parts[0] in {"exploration", "graphs"}
            or relative.parts[0] == "specialized"
            or path.name in {"manifest.json", "report.md"}
        )
        if path.is_file() and not excluded:
            manifest["artifacts"][path.relative_to(ROOT).as_posix()] = sha256(path)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    area_delta = fused_ppa["area_um2"] - separate_ppa["area_um2"]
    area_percent = 100.0 * area_delta / separate_ppa["area_um2"]
    delay_delta = fused_ppa["critical_delay_ns"] - separate_ppa["critical_delay_ns"]
    fmax_delta = fused_ppa["fmax_mhz"] - separate_ppa["fmax_mhz"]
    report = [
        "# Three-level fusion-opportunity search: Ibex LT/GE", "",
        "This new study uses the pinned Ibex ALU operation specializations and does not modify the completed 2026-09-21 bundle.", "",
        "## Decision", "",
        "The module → specialized word-level RTLIL → gate walk selected RTLIL. Module graphs expose only parameterized leaf specializations, while RTLIL exposes a six-node connected common cone including the 34-bit adder. Gate extraction is retained as structural evidence; it is not executable fusion evidence.", "",
        "Preflight is **MEASURE**: the contract records two independent 32-bit operand sources, one selected output, client selection, combinational latency, one-client throughput, no simultaneous-use guarantee, and a shared 34-bit adder. This is not a profitability claim.", "",
        "## Chosen case and alternatives", "",
        "LT/GE was selected from the source-derived Ibex matrix because it has the largest connected exact RTLIL match among the surveyed candidates (7 exact nodes, connected size 6). LT/LTU (5, connected 3) and GE/GEU (5, connected 3) were retained as alternatives. The prior whole-Ibex-ALU + ADD case is not used as a holdout.", "",
        "## Measured result", "",
        "The baseline and generated fused RTL use identical external ports, the same converted pinned source, the same SDC, and the same synthesis backend. Correctness compares both selected operations over directed and pseudo-random vectors. Separate is {:.3f} µm² / {:.3f} ns / {:.3f} MHz; fused is {:.3f} µm² / {:.3f} ns / {:.3f} MHz. The measured deltas are {:+.3f} µm² ({:+.3f}%), {:+.3f} ns, and {:+.3f} MHz. The measured decision is **{}** under an explicit no-area-increase policy. No delay bound was supplied, so delay is reported rather than judged against an invented limit.".format(
            separate_ppa["area_um2"], separate_ppa["critical_delay_ns"], separate_ppa["fmax_mhz"],
            fused_ppa["area_um2"], fused_ppa["critical_delay_ns"], fused_ppa["fmax_mhz"],
            area_delta, area_percent, delay_delta, fmax_delta, measured["decision"]), "",
        "Gate/AIG matches remain structural observations. The emitted RTL is reconstructed only from the selected RTLIL subset; this study makes no gate-level executable-fusion claim.", "",
        "## What the gate can decide", "",
        "Now: reject an interface or throughput violation, report unsupported when required evidence is absent, and send a contract-compatible connected RTLIL opportunity to synthesis. After synthesis, apply explicit area and delay policy to equal-interface measured data.", "",
        "Still required: synthesis for profitability, correctness for emitted RTL, and a larger corpus of contract-matched examples before any general area threshold or automatic whole-module discovery claim. The next limitation is that the gate does not estimate placement/routing cost and does not reconstruct gate-level fusion.", "",
    ]
    (OUT / "report.md").write_text("\n".join(report) + "\n")
    print(json.dumps({"out": str(OUT), "preflight": preflight["decision"],
                      "measured": measured["decision"], "simulation": simulation,
                      "baseline": separate_ppa, "fused": fused_ppa}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
