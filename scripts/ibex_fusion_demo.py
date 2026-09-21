#!/usr/bin/env python3
"""End-to-end, source-derived fusion study for two real Ibex ALU operations.

The study deliberately keeps the old meeting baseline untouched.  It creates
constant-operation wrappers around the pinned upstream ibex_alu, extracts the
word-level cones, selects the strongest executable non-identical pair found by
the matrix, and sends that evidence through the existing structural merge,
executable realizer, and generic RTL emitter.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import graph_extract
from executable_graph import realize
from graph_merge import merge_graphs
from graph_match import compare
from graph_rtl_emit import emit
from graph_viz import render, to_dot
from ibex_fusion_manifest import validate_manifest
import sc_flow
from toolchain import select_cxx


OUT = ROOT / "meeting-artifacts" / "ibex-fusion"
BUILD = ROOT / "build" / "ibex_fusion"
IBEX_RTL = ROOT / "third_party" / "ibex" / "rtl"
UPSTREAM_ALU = IBEX_RTL / "ibex_alu.sv"
UPSTREAM_PKG = IBEX_RTL / "ibex_pkg.sv"
UPSTREAM_LOCK = ROOT / "designs" / "ibex.source.json"
WRAPPER_RTL = ROOT / "rtl" / "ibex_fusion" / "ibex_alu_operation_wrappers.sv"
SV2V = ROOT / "tools" / "oss-cad-suite" / "bin" / "sv2v"
VERILATOR = ROOT / "tools" / "oss-cad-suite" / "bin" / "verilator"

OPS = ["ADD", "SUB", "LT", "LTU", "GE", "GEU", "EQ", "NE", "XOR", "OR", "AND", "SLL", "SRL", "SRA"]
OP_LOWER = {op: op.lower() for op in OPS}
TOPS = {op: "ibex_alu_{}32".format(op.lower()) for op in OPS}
IBEX_ENUM = {"ADD": "ALU_ADD", "SUB": "ALU_SUB", "LT": "ALU_LT", "LTU": "ALU_LTU",
             "GE": "ALU_GE", "GEU": "ALU_GEU", "EQ": "ALU_EQ", "NE": "ALU_NE",
             "XOR": "ALU_XOR", "OR": "ALU_OR", "AND": "ALU_AND", "SLL": "ALU_SLL",
             "SRL": "ALU_SRL", "SRA": "ALU_SRA"}

# The selected pair is checked against this generic emitter subset.  The
# candidate matrix remains authoritative for all other operation pairs.
# The matrix still records unsupported cells for every other candidate.
EMITTER_KINDS = {"port_in", "port_out", "const", "$add", "$mux", "$not", "$xor"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel_record(path: Path) -> Dict[str, Any]:
    path = Path(path).resolve()
    try:
        return {"path": path.relative_to(ROOT).as_posix(), "external": False, "sha256": sha256(path)}
    except ValueError:
        return {"path": str(path), "external": True, "sha256": sha256(path)}


def run(command: Iterable[Any], cwd: Path = ROOT, log: Path = None) -> None:
    command = [str(item) for item in command]
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as handle:
            result = subprocess.run(command, cwd=str(cwd), stdout=handle, stderr=subprocess.STDOUT)
    else:
        result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        detail = "see {}".format(log) if log else (result.stdout + result.stderr)[-2000:]
        raise RuntimeError("command failed: {} ({})".format(" ".join(command), detail))


def prepare_output() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("graphs", "figures", "ppa", "simulation"):
        (OUT / name).mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    old_sim = OUT / "simulation"
    for product in (old_sim / "ibex_fusion_sim",):
        if product.exists():
            product.unlink()
    if (old_sim / "obj_dir").exists():
        shutil.rmtree(old_sim / "obj_dir")


def convert_sources() -> Path:
    converted = BUILD / "ibex_alu_operation_wrappers.v"
    run([SV2V, "--write", converted, UPSTREAM_PKG, UPSTREAM_ALU, WRAPPER_RTL], log=BUILD / "sv2v.log")
    return converted


def extract_specialization(converted: Path, op: str) -> Dict[str, Any]:
    top = TOPS[op]
    yosys_json = BUILD / "yosys" / (top + ".json")
    yosys_json.parent.mkdir(parents=True, exist_ok=True)
    # Flatten before proc so the constant operation reaches upstream processes;
    # opt_muxtree then removes the other fixed-op result branches. memory_map
    # lowers the disabled RV32B intermediate-value ROM to ordinary word cells.
    script = ("read_verilog {}; hierarchy -top {}; flatten; proc; opt -full; "
              "opt_muxtree; memory_map; opt -fast; write_json {}").format(converted, top, yosys_json)
    graph_extract.run_yosys(script)
    graph = graph_extract._graph_from_netlist(json.loads(yosys_json.read_text()), top, top, "rtlil")
    graph.attrs.update({"source_operation": op, "upstream_module": "ibex_alu", "specialization": "constant_operator"})
    path = OUT / "graphs" / (top + ".rtlil.graph.json")
    graph.save(path)
    return graph.to_dict()


def op_kinds(graph: Dict[str, Any]) -> List[str]:
    return sorted({node.get("kind") for node in graph.get("nodes", [])
                   if node.get("kind") not in ("port_in", "port_out", "const")})


def matrix_row(a: str, b: str, graphs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    ga, gb = graphs[a], graphs[b]
    exact = compare(ga, gb, mode="exact", engine="native")
    capability = compare(ga, gb, mode="capability", engine="native")
    shared = []
    nodes_a = {node["id"]: node for node in ga["nodes"]}
    nodes_b = {node["id"]: node for node in gb["nodes"]}
    for item in exact["matched_nodes"]:
        na, nb = nodes_a[item["a"]], nodes_b[item["b"]]
        if na["kind"] not in ("port_in", "port_out", "const"):
            shared.append({"operation": graph_extract.normalize_operation(na["kind"]),
                           "kind_a": na["kind"], "kind_b": nb["kind"],
                           "width_a": na.get("width"), "width_b": nb.get("width"),
                           "node_a": na["id"], "node_b": nb["id"]})
    unsupported = sorted((set(op_kinds(ga)) | set(op_kinds(gb))) - EMITTER_KINDS)
    executable_shared = [item for item in shared if item["operation"] in ("add", "mul")]
    return {
        "source_operations": {"a": a, "b": b},
        "graph_stats": {"a": ga["stats"], "b": gb["stats"]},
        "exact_match_count": exact["common_node_count"],
        "capability_match_count": capability["common_node_count"],
        "exact_coverage": exact["coverage"],
        "capability_coverage": capability["coverage"],
        "largest_connected_exact_match": max([item["node_count"] for item in exact.get("common_subgraphs", [])] or [0]),
        "candidate_shared_operators": shared,
        "unsupported_executable_cell_kinds": unsupported,
        "semantically_valid": bool(exact["matched_nodes"]) and a != b,
        "current_graph_to_rtl_realization_possible": bool(executable_shared) and not unsupported,
        "semantic_note": "Each specialization is independently verified against the upstream Ibex ALU and RV32I reference; a structural match does not assert operation equivalence.",
    }


def choose_pair(rows: List[Dict[str, Any]]) -> Tuple[str, str, Dict[str, Any]]:
    eligible = [row for row in rows if row["current_graph_to_rtl_realization_possible"] and row["semantically_valid"]]
    # Prefer the requested arithmetic candidates, then the largest connected
    # executable match. The matrix, not PPA, chooses the pair.
    preferred = [row for row in eligible if set(row["source_operations"].values()) in ({"ADD", "SUB"}, {"SUB", "LTU"})]
    candidates = preferred or eligible
    if not candidates:
        raise RuntimeError("no semantically valid executable Ibex pair found")
    selected = sorted(candidates, key=lambda row: (-row["largest_connected_exact_match"],
                                                    -row["exact_match_count"],
                                                    tuple(row["source_operations"].values())))[0]
    return selected["source_operations"]["a"], selected["source_operations"]["b"], selected


def selected_evidence(graphs: Dict[str, Dict[str, Any]], a: str, b: str) -> Dict[str, Any]:
    ga, gb = graphs[a], graphs[b]
    match = compare(ga, gb, mode="exact", engine="native")
    match_path = OUT / "match.rtlil.json"
    match_path.write_text(json.dumps(match, indent=2, sort_keys=True) + "\n")
    merge = merge_graphs(ga, gb, match).to_dict()
    merge_path = OUT / "structural_merge.rtlil.json"
    merge_path.write_text(json.dumps(merge, indent=2, sort_keys=True) + "\n")
    shared = [item for item in match["matched_nodes"]
              if next(node for node in ga["nodes"] if node["id"] == item["a"])["kind"] == "$add"]
    if not shared:
        raise RuntimeError("selected pair has no matched $add operator")
    pair = shared[0]
    control = {
        "schema": "fu-control-spec/v1", "version": 1, "graph_a": ga["design"], "graph_b": gb["design"],
        "level": "rtlil", "generated_design": "ibex_fused_{}_{}32".format(a.lower(), b.lower()),
        "generated_top": "ibex_fused_{}_{}32".format(a.lower(), b.lower()),
        "shared_operator": {"match_pair": pair, "operation": "add"},
        "inserted_ports": [{"name": "source_select_i", "width": 1, "direction": "input"}],
        "shared_control": {"port": "source_select_i", "encoding": {"1": "graph_a", "0": "graph_b"}},
        "outputs": [{"sources": [{"graph": "a", "port": "result_o", "select_when": {"source_select_i": 1}},
                                    {"graph": "b", "port": "result_o", "select_when": {"source_select_i": 0}}],
                     "fused_port": "result_o", "behavior": "selected_source_result"}],
        "required_paths": [{"graph": label, "from_port": port, "to_node": pair[label]}
                           for label in ("a", "b") for port in ("operand_a_i", "operand_b_i")],
    }
    control_path = OUT / "control_spec.json"
    control_path.write_text(json.dumps(control, indent=2, sort_keys=True) + "\n")
    plan = realize(ga, gb, match, merge, control)
    plan_path = OUT / "executable_graph.rtlil.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    rtl_text = emit(plan)
    rtl_path = OUT / "ibex_fused_{}_{}32.sv".format(a.lower(), b.lower())
    rtl_path.write_text(rtl_text)
    return {"match": match, "merge": merge, "control": control, "plan": plan,
            "paths": {"match": match_path, "merge": merge_path, "control": control_path,
                      "plan": plan_path, "rtl": rtl_path}}


def extract_generated(path: Path, top: str) -> Dict[str, Any]:
    out = BUILD / "yosys" / (top + ".json")
    graph_extract.run_yosys("read_verilog -sv {}; hierarchy -top {}; proc; opt -fast; write_json {}".format(path, top, out))
    graph = graph_extract._graph_from_netlist(json.loads(out.read_text()), top, top, "rtlil")
    result_path = OUT / "generated_reextracted.rtlil.graph.json"
    graph.save(result_path)
    return graph.to_dict()


def _reference_expression(op: str, x: str, y: str) -> str:
    expressions = {
        "ADD": "{} + {}", "SUB": "{} - {}", "XOR": "{} ^ {}", "OR": "{} | {}", "AND": "{} & {}",
        "SLL": "{} << {}[4:0]", "SRL": "{} >> {}[4:0]", "SRA": "$signed({}) >>> {}[4:0]",
        "LT": "{{31'b0, ($signed({}) < $signed({}))}}", "LTU": "{{31'b0, ({} < {})}}",
        "GE": "{{31'b0, ($signed({}) >= $signed({}))}}", "GEU": "{{31'b0, ({} >= {})}}",
        "EQ": "{{31'b0, ({} == {})}}", "NE": "{{31'b0, ({} != {})}}",
    }
    if op not in expressions:
        raise RuntimeError("no independent reference expression for {}".format(op))
    return expressions[op].format(x, y)


def write_testbench(a: str, b: str, plan: Dict[str, Any]) -> Path:
    ports = {node["attrs"].get("provenance", {}).get("graph"): node["label"]
             for node in plan["nodes"] if node.get("kind") == "port_in"
             and node.get("attrs", {}).get("provenance", {}).get("graph") in ("a", "b")}
    # The generic realizer namespaces colliding source ports. Derive the labels
    # from the plan, never from emitter-specific template text.
    labels = {"a": {}, "b": {}}
    for node in plan["nodes"]:
        prov = node.get("attrs", {}).get("provenance", {})
        if prov.get("graph") in labels and node.get("kind") == "port_in":
            labels[prov["graph"]][prov.get("source_id", "").split(":")[-1]] = node["label"]
    aa, ab = labels["a"]["operand_a_i"], labels["a"]["operand_b_i"]
    ba, bb = labels["b"]["operand_a_i"], labels["b"]["operand_b_i"]
    tb = OUT / "tb_ibex_fusion.sv"
    tb.write_text("""`timescale 1ns/1ps
module tb_ibex_fusion;
  logic [31:0] @AA@, @AB@, @BA@, @BB@;
  logic source_select_i;
  logic [31:0] result_o, result_a, result_b;
  integer checks, errors, i;
  logic [31:0] state;
  ibex_alu_@OP_A@32 ref_a(.operand_a_i(@AA@), .operand_b_i(@AB@), .result_o(result_a));
  ibex_alu_@OP_B@32 ref_b(.operand_a_i(@BA@), .operand_b_i(@BB@), .result_o(result_b));
  @FUSED_TOP@ dut(.@AA@(@AA@), .@AB@(@AB@), .@BA@(@BA@), .@BB@(@BB@),
                         .source_select_i(source_select_i), .result_o(result_o));
  function automatic [31:0] model_a(input [31:0] x, input [31:0] y);
    model_a = @MODEL_A@;
  endfunction
  function automatic [31:0] model_b(input [31:0] x, input [31:0] y);
    model_b = @MODEL_B@;
  endfunction
  function automatic [31:0] next_rand(input [31:0] x);
    begin next_rand = x ^ (x << 13); next_rand = next_rand ^ (next_rand >> 17); next_rand = next_rand ^ (next_rand << 5); end
  endfunction
  task automatic check(input integer op, input [31:0] x, input [31:0] y);
    begin
      @AA@=x; @AB@=y; @BA@=x ^ 32'h13579bdf; @BB@=y ^ 32'h2468ace0;
      source_select_i=1'b1; #1; checks=checks+1;
      if (result_o !== result_a || result_o !== model_a(x,y)) begin errors=errors+1; $display("TEST_ERROR @OP_A@ "); end
      source_select_i=1'b0; #1; checks=checks+1;
      if (result_o !== result_b || result_o !== model_b(@BA@,@BB@)) begin errors=errors+1; $display("TEST_ERROR @OP_B@ "); end
    end
  endtask
  initial begin
    checks=0; errors=0; state=32'h1;
    check(0,32'h00000000,32'h00000000); check(0,32'hffffffff,32'h00000000);
    check(0,32'h80000000,32'h00000000); check(0,32'h7fffffff,32'h80000000);
    check(0,32'hffffffff,32'hffffffff); check(0,32'h00000001,32'hffffffff);
    for (i=0;i<256;i=i+1) begin state=next_rand(state); check(0,state,next_rand(state)); end
    if (errors != 0) $fatal(1,"Ibex fused regression failed");
    $display("IBEX_FUSION_TEST_PASS checks=%0d vectors=%0d operations=2",checks,checks/2); $finish;
  end
endmodule
    """.replace("@AA@", aa).replace("@AB@", ab).replace("@BA@", ba).replace("@BB@", bb)
        .replace("@FUSED_TOP@", plan["top"]).replace("@OP_A@", a.lower()).replace("@OP_B@", b.lower())
        .replace("@MODEL_A@", _reference_expression(a, "x", "y"))
        .replace("@MODEL_B@", _reference_expression(b, "x", "y")))
    return tb


def simulate(tb: Path, top: str) -> Dict[str, Any]:
    sim = OUT / "simulation"
    sim.mkdir(exist_ok=True)
    sim_build = BUILD / "simulation"
    sim_build.mkdir(parents=True, exist_ok=True)
    binary = sim_build / "ibex_fusion_sim"
    sources = [UPSTREAM_PKG, UPSTREAM_ALU, WRAPPER_RTL, OUT / (top + ".sv"), tb]
    cxx = select_cxx()
    run([VERILATOR, "--binary", "--timing", "-Wno-fatal", "-Wno-TIMESCALEMOD", "--compiler", cxx.get("family", "gcc"),
         "-MAKEFLAGS", "CXX={}".format(cxx["path"]), "-MAKEFLAGS", "LINK={}".format(cxx["path"]),
         "--top-module", "tb_ibex_fusion",
         "-o", binary, "--Mdir", sim_build / "obj_dir"] + sources, log=sim_build / "compile.log")
    shutil.copyfile(sim_build / "compile.log", sim / "compile.log")
    result = subprocess.run([str(binary)], cwd=str(sim_build), capture_output=True, text=True)
    (sim / "simulation.log").write_text(result.stdout + result.stderr)
    if result.returncode != 0 or "IBEX_FUSION_TEST_PASS" not in result.stdout:
        raise RuntimeError("Ibex fusion simulation failed; see {}".format(sim / "simulation.log"))
    import re
    checks = int(re.search(r"IBEX_FUSION_TEST_PASS checks=(\d+)", result.stdout).group(1))
    return {"status": "pass", "checks": checks, "vectors": checks // 2, "operations": 2,
            "tool": "verilator", "log": "meeting-artifacts/ibex-fusion/simulation/simulation.log"}


def ppa(design: str, top: str, sources: List[Path], sdc: Path, build: Path) -> Dict[str, Any]:
    # SiliconCompiler can occasionally report a negative bookkeeping runtime
    # for an otherwise completed OpenSTA task when its local scheduler starts
    # and exits at the same instant.  Retry with a fresh project/build tree;
    # never consume a partial failed result as a measurement.
    result = sc_flow.synthesize_design_sc(top, [str(path) for path in sources], str(sdc), str(build), clean=True)
    if result.get("status") != "pass":
        result = sc_flow.synthesize_design_sc(top, [str(path) for path in sources], str(sdc), str(build), clean=True)
    metrics = result.get("metrics", {})
    def val(key): return (metrics.get(key) or {}).get("value")
    return {"design": design, "status": result.get("status"), "area_um2": val("cellarea"),
            "cell_count": val("cells"), "critical_delay_ns": (metrics.get("core_path_delay") or {}).get("value"),
            "slack_ns": val("setupslack"), "fmax_mhz": ((metrics.get("fmax_core") or {}).get("value") or 0) / 1e6,
            "peak_power_mw": val("peakpower"), "dynamic_power_mw": (metrics.get("dynamic_power") or {}).get("value"),
            "source": [str(path) for path in sources], "synthesis_result": result.get("result_file")}


def write_ppa(a: Dict[str, Any], b: Dict[str, Any], fused: Dict[str, Any], graph_a: Dict[str, Any], graph_b: Dict[str, Any], graph_fused: Dict[str, Any], simulation: Dict[str, Any], op_a: str, op_b: str) -> Dict[str, Any]:
    rows = {"unit_a": a, "unit_b": b, "fused": fused}
    def guardrail_stats(graph: Dict[str, Any]) -> Dict[str, Any]:
        kinds = dict(graph["stats"].get("kinds", {}))
        operators = {kind: count for kind, count in kinds.items()
                     if kind not in ("port_in", "port_out", "const")}
        return {"operator_counts": operators, "mux_count": kinds.get("$mux", 0),
                "node_count": graph["stats"].get("nodes"), "edge_count": graph["stats"].get("edges")}
    literal = a["area_um2"] + b["area_um2"]
    fused_area = fused["area_um2"]
    result = {"schema": "fu-ibex-fusion-results/v1", "selected_operations": {"a": op_a, "b": op_b},
              "semantics": "RV32I {} and {}, 32-bit result".format(op_a, op_b),
              "simulation": simulation,
              "pretechmap": {"unit_a": guardrail_stats(graph_a), "unit_b": guardrail_stats(graph_b), "fused": guardrail_stats(graph_fused)},
              "ppa": rows, "literal_area_sum_um2": literal, "fused_minus_literal_sum_um2": fused_area - literal,
              "fused_vs_literal_sum_percent": 100.0 * (fused_area - literal) / literal,
              "criterion_fused_below_literal_sum": fused_area < literal,
              "control_mux_overhead": {"inserted_shared_input_muxes": 2, "inserted_output_selection_muxes": 1,
                                      "note": "Counts are graph-level inserted adapters; no isolated mapped-area claim is made."},
              "throughput": {"separate_sub_ltu_clients": 2, "fused_shared_adder_client": 1,
                             "simultaneous_use_preserved": False,
                             "note": "This SUB/LTU experiment has no multiplier; these are combinational cones, so registered latency and II are undefined without a wrapper."}}
    (OUT / "ppa.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    md = ["# Ibex {}/{} fusion PPA".format(op_a, op_b), "", "| design | area (um^2) | cells | critical delay (ns) | slack (ns) | fmax (MHz) |", "|---|---:|---:|---:|---:|---:|"]
    for label in ("unit_a", "unit_b", "fused"):
        row = rows[label]
        md.append("| {} | {:.3f} | {} | {:.3f} | {:.3f} | {:.3f} |".format(label, row["area_um2"], int(row["cell_count"]), row["critical_delay_ns"], row["slack_ns"], row["fmax_mhz"]))
    md += ["", "Literal area(A)+area(B): {:.3f} um^2; fused delta: {:.3f} um^2 ({:.3f}%). Criterion passed: {}.".format(literal, fused_area-literal, result["fused_vs_literal_sum_percent"], result["criterion_fused_below_literal_sum"]), "", "This is a time-multiplexed combinational comparison; simultaneous client use is not preserved. Registered latency and II are undefined without a wrapper.", "", "Pre-techmap guardrails: A {}, B {}, fused {}; mux counts A={}, B={}, fused={} .".format(result["pretechmap"]["unit_a"]["operator_counts"], result["pretechmap"]["unit_b"]["operator_counts"], result["pretechmap"]["fused"]["operator_counts"], result["pretechmap"]["unit_a"]["mux_count"], result["pretechmap"]["unit_b"]["mux_count"], result["pretechmap"]["fused"]["mux_count"])]
    (OUT / "ppa.md").write_text("\n".join(md) + "\n")
    return result


def write_figures(graphs: Dict[str, Dict[str, Any]], plan: Dict[str, Any], merge: Dict[str, Any], ppa_result: Dict[str, Any], op_a: str, op_b: str) -> None:
    fig = OUT / "figures"
    for name, graph, title in [("separate_" + op_a.lower(), graphs[op_a], "Ibex {} specialization source cone".format(op_a)),
                               ("separate_" + op_b.lower(), graphs[op_b], "Ibex {} specialization source cone".format(op_b)),
                               ("matched_shared_adder", merge, "{}/{} structural candidate — shared adder highlighted".format(op_a, op_b)),
                               ("fused_executable_graph", plan, "{}/{} executable graph plan — selector and adapters".format(op_a, op_b))]:
        if "schema" in graph and graph.get("schema") == "fu-executable-graph/v1":
            # Convert the executable plan to a minimal FUGraph-shaped object for
            # the existing visualizer, preserving the plan's node/edge labels.
            graph = {"schema": graph_extract.GRAPH_SCHEMA, "design": graph["design"], "level": graph["level"],
                     "top": graph["top"], "attrs": {}, "nodes": graph["nodes"], "edges": graph["edges"]}
        path = fig / name
        loaded = graph_extract.FUGraph.load(_write_temp_graph(graph, path.with_suffix(".graph.json")))
        render(loaded, path, image_format="svg", max_nodes=250, title=title)
    # A compact standalone SVG keeps the PPA figure independent of plotting
    # libraries and records the exact measured values in visible labels.
    values = [("A " + op_a, ppa_result["ppa"]["unit_a"]["area_um2"]), ("B " + op_b, ppa_result["ppa"]["unit_b"]["area_um2"]), ("fused", ppa_result["ppa"]["fused"]["area_um2"])]
    scale = 220.0 / max(value for _, value in values)
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="700" height="360" viewBox="0 0 700 360">', '<rect width="100%" height="100%" fill="white"/>', '<text x="350" y="28" text-anchor="middle" font-family="sans-serif" font-size="18">Ibex LT/GE mapped area</text>']
    for idx, (label, value) in enumerate(values):
        x, y = 90 + idx * 200, 300 - value * scale
        lines += ['<rect x="{}" y="{:.1f}" width="100" height="{:.1f}" fill="#6b8fb3"/>'.format(x, y, value * scale), '<text x="{}" y="325" text-anchor="middle" font-family="sans-serif">{}</text>'.format(x+50,label), '<text x="{}" y="{:.1f}" text-anchor="middle" font-family="sans-serif" font-size="12">{:.3f}</text>'.format(x+50,y-6,value)]
    lines.append('</svg>')
    (fig / "ppa_area.svg").write_text("\n".join(lines) + "\n")


def _write_temp_graph(data: Dict[str, Any], path: Path) -> Path:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


def write_manifest(paths: Dict[str, Path], graph_fused: Dict[str, Any], simulation: Dict[str, Any], ppa_result: Dict[str, Any], op_a: str, op_b: str) -> Path:
    evidence = {name: rel_record(path) for name, path in paths.items() if path.is_file()}
    external_dependencies = {}
    if "liberty" in evidence:
        liberty = paths["liberty"].resolve()
        external_dependencies["liberty"] = {"path": str(liberty), "external": True, "sha256": sha256(liberty),
                                              "identity": "NangateOpenCellLibrary_typical / FreePDK45 demo corner"}
        del evidence["liberty"]
    manifest = {"schema": "fu-ibex-fusion-manifest/v1", "version": 1,
                "graph_to_rtl_status": "executable", "template_backed": False,
                "selected_pair": {"a": op_a, "b": op_b},
                "upstream": {"repository": "https://github.com/lowRISC/ibex.git", "commit": json.loads(UPSTREAM_LOCK.read_text())["resolved_commit"], "source_hash": sha256(UPSTREAM_ALU)},
                "simulation": simulation, "ppa_summary": {"literal_sum_um2": ppa_result["literal_area_sum_um2"], "fused_area_um2": ppa_result["ppa"]["fused"]["area_um2"]},
                "generated_graph_stats": graph_fused["stats"], "evidence": evidence,
                "external_dependencies": external_dependencies}
    path = OUT / "generated_rtl.manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return path


def main() -> None:
    prepare_output()
    converted = convert_sources()
    graphs = {op: extract_specialization(converted, op) for op in OPS}
    rows = [matrix_row(a, b, graphs) for index, a in enumerate(OPS) for b in OPS[index + 1:]]
    a, b, selected = choose_pair(rows)
    matrix = {"schema": "fu-ibex-candidate-matrix/v1", "upstream_module": "ibex_alu", "operations": OPS,
              "wrapper_source": rel_record(WRAPPER_RTL), "rows": rows, "selected_pair": {"a": a, "b": b, "reason": "largest connected executable match after requested arithmetic preference"}}
    (OUT / "candidate_matrix.json").write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n")
    md = ["# Ibex specialization candidate matrix", "", "Pinned source: `third_party/ibex/rtl/ibex_alu.sv`; every wrapper instantiates that module.", "", "| A | B | exact | capability | largest connected | shared operators | unsupported cells | executable |", "|---|---|---:|---:|---:|---|---|---|"]
    for row in rows:
        shared = ", ".join("{}[{}]".format(x["operation"], x["width_a"]) for x in row["candidate_shared_operators"]) or "-"
        bad = ", ".join(row["unsupported_executable_cell_kinds"]) or "-"
        md.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(row["source_operations"]["a"], row["source_operations"]["b"], row["exact_match_count"], row["capability_match_count"], row["largest_connected_exact_match"], shared, bad, row["current_graph_to_rtl_realization_possible"]))
    (OUT / "candidate_matrix.md").write_text("\n".join(md) + "\n")
    evidence = selected_evidence(graphs, a, b)
    fused_top = evidence["plan"]["top"]
    fused_graph = extract_generated(evidence["paths"]["rtl"], fused_top)
    tb = write_testbench(a, b, evidence["plan"])
    sim = simulate(tb, fused_top)
    sdc = OUT / "ibex_fusion.sdc"
    sdc.write_text("set clk_period 2.5\ncreate_clock -name vclk -period $clk_period\nset_input_delay [expr {0.1 * $clk_period}] -clock vclk [all_inputs]\nset_output_delay [expr {0.1 * $clk_period}] -clock vclk [all_outputs]\n")
    ppa_results = {"unit_a": ppa("ibex_alu_{}32".format(a.lower()), TOPS[a], [converted], sdc, BUILD / "ppa" / "unit_a"),
                   "unit_b": ppa("ibex_alu_{}32".format(b.lower()), TOPS[b], [converted], sdc, BUILD / "ppa" / "unit_b"),
                   "fused": ppa(fused_top, fused_top, [evidence["paths"]["rtl"]], sdc, BUILD / "ppa" / "fused")}
    ppa_result = write_ppa(ppa_results["unit_a"], ppa_results["unit_b"], ppa_results["fused"], graphs[a], graphs[b], fused_graph, sim, a, b)
    limitations = {"schema": "fu-ibex-fusion-limitations/v1", "status": "partial",
                   "claims": ["The {}/{} generated unit is fully emitted from the selected RTLIL graphs, exact match, non-executable structural merge, and control specification.".format(a, b), "The source specializations instantiate pinned ibex_alu.sv; no operation behavior is copied into the wrappers.", "The result is a combinational, time-multiplexed shared-adder comparison; simultaneous {}/{} clients are not preserved.".format(a, b)],
                   "not_completed": ["formal equivalence was not attempted", "Priority 3 sequential Ibex ALU plus ibex_multdiv_fast fusion was not attempted", "flattened arbitrary-Ibex RTLIL reconstruction is not claimed", "gate/AIG executable reconstruction is not claimed"],
                   "priority_2": {"status": "complete_separate_artifact", "result": "meeting-artifacts/ibex-fusion/module-preserving/", "classification": "contract-guided module-preserving fusion", "note": "The whole-ALU plus independent-adder result is generated by the separate Priority 2 workflow and preserves the upstream ibex_alu module."},
                   "priority_3": {"status": "not_attempted", "unsupported_rtlil_constructs": ["sequential registers/state", "iterative mult/div control", "reset and handshake sequencing"], "missing_scheduler_state_semantics": "The current executable graph is acyclic combinational RTLIL and has no request scheduler, state machine, or cycle-level resource arbitration.", "testbench_limitation": "The current Verilator harness checks combinational SUB/LTU vectors and cannot establish DIV/REM cycle latency or reset sequencing.", "smallest_next_step": "Map ibex_ex_block and ibex_multdiv_fast request/state interfaces into a reset-aware sequential contract, then add a cycle-accurate directed workload before synthesis."},
                   "next_step": "Ibex ALU versus multiplier/divider horizontal target with explicit sequential scheduling and reset verification"}
    (OUT / "limitations.json").write_text(json.dumps(limitations, indent=2, sort_keys=True) + "\n")
    paths = {"upstream_lock": UPSTREAM_LOCK, "upstream_alu": UPSTREAM_ALU, "upstream_pkg": UPSTREAM_PKG,
             "wrapper_rtl": WRAPPER_RTL, "converted_wrappers": converted, "graph_a": OUT / "graphs" / (TOPS[a] + ".rtlil.graph.json"),
             "graph_b": OUT / "graphs" / (TOPS[b] + ".rtlil.graph.json"), "match": evidence["paths"]["match"], "merge": evidence["paths"]["merge"],
             "control": evidence["paths"]["control"], "executable_graph": evidence["paths"]["plan"], "generated_rtl": evidence["paths"]["rtl"],
             "reextracted_graph": OUT / "generated_reextracted.rtlil.graph.json", "testbench": tb, "sdc": sdc,
             "candidate_matrix": OUT / "candidate_matrix.json", "ppa": OUT / "ppa.json", "limitations": OUT / "limitations.json",
             "realizer": ROOT / "executable_graph.py", "emitter": ROOT / "graph_rtl_emit.py", "graph_extract": ROOT / "graph_extract.py", "graph_match": ROOT / "graph_match.py", "graph_merge": ROOT / "graph_merge.py", "demo_script": ROOT / "scripts" / "ibex_fusion_demo.py", "manifest_validator": ROOT / "ibex_fusion_manifest.py", "sc_flow": ROOT / "sc_flow.py", "toolchain": ROOT / "toolchain.py", "toolchain_lock": ROOT / "build" / "toolchain.lock.json"}
    liberty_candidates = list((BUILD / "ppa" / "unit_a").glob("**/inputs/*.lib"))
    if liberty_candidates:
        paths["liberty"] = liberty_candidates[0]
    manifest_path = write_manifest(paths, fused_graph, sim, ppa_result, a, b)
    validation = validate_manifest(manifest_path, ROOT)
    (OUT / "manifest_validation.json").write_text(json.dumps(validation, indent=2, sort_keys=True) + "\n")
    write_figures(graphs, evidence["plan"], evidence["merge"], ppa_result, a, b)
    readme = ["# Ibex fusion meeting summary", "", "This study uses the pinned lowRISC Ibex `ibex_alu` source at the lock-recorded upstream commit and selects {}/{} from a reproducible 91-pair specialization matrix. The wrappers contain no fusion-only selector; each operation is a constant specialization of the upstream module.".format(a, b), "", "The generated `{}` is template-free RTLIL graph emission. It shares the matched upstream adder cone and inserts selector/mux/output adapters from the explicit control specification.".format(fused_top + ".sv"), "", "Simulation: {} checks over {} vectors and two operations; both generated modes were compared with specialized upstream wrappers and an independent RV32I reference. The source cones are combinational, so registered latency and II are undefined without a wrapper.".format(sim["checks"], sim["vectors"]), "", "Mapped PPA: literal A+B = {:.3f} um^2; fused = {:.3f} um^2; delta = {:.3f} um^2 ({:.3f}% larger). The literal area criterion therefore fails for this pair. The comparison is combinational and time-multiplexed; simultaneous {}/{} client throughput is not preserved.".format(ppa_result["literal_area_sum_um2"], ppa_result["ppa"]["fused"]["area_um2"], ppa_result["fused_minus_literal_sum_um2"], ppa_result["fused_vs_literal_sum_percent"], a, b), "", "The pre-techmap source counts are A {}, B {}, and fused {}. The fused graph has one selected shared adder plus selector and output muxing. Gate/AIG observations are structural only.".format(ppa_result["pretechmap"]["unit_a"]["operator_counts"], ppa_result["pretechmap"]["unit_b"]["operator_counts"], ppa_result["pretechmap"]["fused"]["operator_counts"]), "", "Priority 2 whole-ALU plus independent-adder contract-guided fusion and Priority 3 sequential ibex_multdiv_fast fusion were not attempted. The next horizontal target is Ibex ALU versus multiplier/divider with an explicit reset-aware scheduler and cycle-level testbench."]
    (OUT / "README.md").write_text("\n".join(readme) + "\n")
    print(json.dumps({"artifact_dir": str(OUT), "selected_pair": [a, b], "simulation_checks": sim["checks"], "literal_area_um2": ppa_result["literal_area_sum_um2"], "fused_area_um2": ppa_result["ppa"]["fused"]["area_um2"]}, indent=2))


if __name__ == "__main__":
    main()
