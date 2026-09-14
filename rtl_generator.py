#!/usr/bin/env python3
"""Emit a restricted RTL realization from two graph artifacts and evidence."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Sequence


class RTLGenerationError(ValueError):
    """Raised when graph evidence cannot justify the specialized template gate."""


def _load(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RTLGenerationError("cannot read {}: {}".format(path, exc))
    if not isinstance(value, dict):
        raise RTLGenerationError("{} is not a JSON object".format(path))
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _operation(node: Dict[str, Any]) -> str:
    return str((node.get("attrs") or {}).get("operation") or
               node.get("kind", "")).lstrip("$").strip("_").lower()


def _nodes(graph: Dict[str, Any], label: str) -> Dict[str, Dict[str, Any]]:
    result = {}
    for node in graph.get("nodes", []):
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id or node_id in result:
            raise RTLGenerationError("{} has invalid or duplicate node IDs".format(label))
        result[node_id] = node
    return result


def _graph_sources(graph: Dict[str, Any]) -> Sequence[str]:
    attrs = graph.get("attrs") or {}
    sources = graph.get("sources", attrs.get("sources", []))
    return sources if isinstance(sources, list) else []


def _interface(graph: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(node.get("label", node.get("id", ""))): node
        for node in graph.get("nodes", [])
        if node.get("kind") in ("port_in", "port_out")
    }


def _unsigned(node: Dict[str, Any]) -> bool:
    attrs = node.get("attrs") or {}
    if "signed" in attrs:
        value = attrs["signed"]
        return value in (None, False, 0, "0", "false", "unsigned")
    params = attrs.get("parameters") or {}
    signed = [value for key, value in params.items() if "SIGNED" in str(key).upper()]
    return all(str(value).lower() in ("0", "false", "unsigned") for value in signed)


def _evidence_hashes(paths: Dict[str, Path]) -> Dict[str, Dict[str, str]]:
    return {name: {"path": str(path.resolve()), "sha256": _sha256(path)}
            for name, path in paths.items()}


def validate_evidence(graph_a: Dict[str, Any], graph_b: Dict[str, Any],
                      match: Dict[str, Any], merge: Dict[str, Any],
                      paths: Dict[str, Path] = None) -> Dict[str, Any]:
    if graph_a.get("schema") != "fu-graph/v1" or graph_b.get("schema") != "fu-graph/v1":
        raise RTLGenerationError("both inputs must use fu-graph/v1")
    if graph_a.get("level") != graph_b.get("level"):
        raise RTLGenerationError("graph levels differ")
    if match.get("schema") != "fu-graph-match/v1":
        raise RTLGenerationError("match must use fu-graph-match/v1")
    if merge.get("schema") != "fu-graph/v1":
        raise RTLGenerationError("merge must use fu-graph/v1")
    if merge.get("attrs", {}).get("hardware_status", {}).get("executable") is not False:
        raise RTLGenerationError("structural merge must remain executable=false")
    if match.get("graph_a") != graph_a.get("design") or match.get("graph_b") != graph_b.get("design"):
        raise RTLGenerationError("match graph names do not identify graph A and graph B")
    if match.get("level") != graph_a.get("level"):
        raise RTLGenerationError("match level does not agree with graph level")
    source_graphs = merge.get("attrs", {}).get("source_graphs", {})
    if (source_graphs.get("a", {}).get("design") != graph_a.get("design") or
            source_graphs.get("b", {}).get("design") != graph_b.get("design")):
        raise RTLGenerationError("merge provenance does not identify graph A and graph B")
    nodes_a, nodes_b = _nodes(graph_a, "graph A"), _nodes(graph_b, "graph B")
    matched_nodes = match.get("matched_nodes") or []
    if not matched_nodes or match.get("common_node_count") != len(matched_nodes):
        raise RTLGenerationError("match has zero or inconsistent matched nodes")
    seen_a, seen_b = set(), set()
    multiplier_pair = None
    for item in matched_nodes:
        a_id, b_id = item.get("a"), item.get("b")
        if a_id not in nodes_a or b_id not in nodes_b:
            raise RTLGenerationError("match references a node absent from its graph")
        if a_id in seen_a or b_id in seen_b:
            raise RTLGenerationError("match node IDs are not one-to-one")
        seen_a.add(a_id); seen_b.add(b_id)
        if _operation(nodes_a[a_id]) == "mul" and _operation(nodes_b[b_id]) == "mul":
            if multiplier_pair is not None:
                raise RTLGenerationError("more than one matched multiplier candidate")
            multiplier_pair = (a_id, b_id)
    if multiplier_pair is None:
        raise RTLGenerationError("expected matched multiplier candidate is missing")
    merged_shared = [
        node for node in merge.get("nodes", [])
        if (node.get("attrs", {}).get("merge", {}).get("shared_candidate") is True)
    ]
    origins = {(node.get("attrs", {}).get("merge", {}).get("origins", {}).get("a"),
                node.get("attrs", {}).get("merge", {}).get("origins", {}).get("b"))
               for node in merged_shared}
    if multiplier_pair not in origins:
        raise RTLGenerationError("merge does not preserve the matched multiplier candidate")
    operations_a = Counter(_operation(node) for node in graph_a.get("nodes", [])
                           if node.get("kind") not in ("port_in", "port_out", "const"))
    operations_b = Counter(_operation(node) for node in graph_b.get("nodes", [])
                           if node.get("kind") not in ("port_in", "port_out", "const"))
    expected_ops = Counter({"add": 1, "mul": 1, "mux": 1})
    if operations_a != expected_ops or operations_b != expected_ops:
        raise RTLGenerationError("only the one-ADD/one-MUL/one-mux evidence is supported")
    interface_a, interface_b = _interface(graph_a), _interface(graph_b)
    expected_interface = {"a_i", "b_i", "add_i", "result_o"}
    if set(interface_a) != expected_interface or set(interface_b) != expected_interface:
        raise RTLGenerationError("unsupported named graph interface")
    if any(node.get("kind") in ("port_in", "port_out") and not _unsigned(node)
           for graph in (graph_a, graph_b) for node in graph.get("nodes", [])):
        raise RTLGenerationError("signed graph evidence is unsupported")
    for node in list(nodes_a.values()) + list(nodes_b.values()):
        if not _unsigned(node):
            raise RTLGenerationError("signed graph evidence is unsupported")
        if node.get("width") not in (1, 32, 64, None):
            raise RTLGenerationError("unsupported graph width")
    mul_a, mul_b = nodes_a[multiplier_pair[0]], nodes_b[multiplier_pair[1]]
    params_a = mul_a.get("attrs", {}).get("parameters", {})
    params_b = mul_b.get("attrs", {}).get("parameters", {})
    operand_widths = [int(params_a.get(key, params_b.get(key, 0))) for key in ("A_WIDTH", "B_WIDTH")]
    if operand_widths != [32, 32] or [int(params_b.get(key, params_a.get(key, 0))) for key in ("A_WIDTH", "B_WIDTH")] != [32, 32]:
        raise RTLGenerationError("specialized generator requires unsigned 32-bit multiplier operands")
    output_width = int(params_a.get("Y_WIDTH", params_b.get("Y_WIDTH", mul_a.get("width", 0))))
    if output_width != 64 or int(params_b.get("Y_WIDTH", params_a.get("Y_WIDTH", output_width))) != 64:
        raise RTLGenerationError("specialized generator requires a 64-bit multiplier result")
    return {
        "operations": sorted(expected_ops), "operand_width": operand_widths[0],
        "output_width": output_width, "ports": sorted(expected_interface),
        "shared_operator": "matched multiplier candidate",
        "mux_behavior": {"ADD": "bypass multiplication", "MUL": "bypass final add",
                          "MAC": "route product through shared final adder"},
        "matched_multiplier": {"graph_a": multiplier_pair[0], "graph_b": multiplier_pair[1]},
        "evidence_paths": {name: str(path.resolve()) for name, path in (paths or {}).items()},
    }


def emit(output: Path, graph_a: Dict[str, Any], graph_b: Dict[str, Any],
         match: Dict[str, Any], merge: Dict[str, Any], evidence_paths: Dict[str, Path]) -> Dict[str, Any]:
    derived = validate_evidence(graph_a, graph_b, match, merge, evidence_paths)
    output.parent.mkdir(parents=True, exist_ok=True)
    rtl = r'''`timescale 1ns/1ps

// GENERATED SPECIALIZED REALIZATION: parameters and allowed modes are derived
// from the checked graph A/B, match, and structural-merge evidence.
module generated_fused_add_mul_mac (
  input logic clk_i, input logic rst_ni, input logic valid_i,
  input logic [1:0] mode_i, input logic [__OPERAND_MSB__:0] a_i, input logic [__OPERAND_MSB__:0] b_i,
  input logic [__OPERAND_MSB__:0] c_i, output logic ready_o, output logic valid_o,
  output logic [__OUTPUT_MSB__:0] result_o
);
  localparam logic [1:0] MODE_ADD = 2'b00;
  localparam logic [1:0] MODE_MUL = 2'b01;
  localparam logic [1:0] MODE_MAC = 2'b10;
  logic [__OUTPUT_MSB__:0] product, add_input, add_result, selected_result;
  assign ready_o = rst_ni;
  assign product = a_i * b_i;
  assign add_input = (mode_i == MODE_MAC) ? product : {__ZERO_EXT_WIDTH__'b0, a_i};
  assign add_result = add_input + ((mode_i == MODE_MAC) ? {__ZERO_EXT_WIDTH__'b0, c_i} : {__ZERO_EXT_WIDTH__'b0, b_i});
  always_comb begin
    case (mode_i)
      MODE_ADD: selected_result = add_result;
      MODE_MUL: selected_result = product;
      MODE_MAC: selected_result = add_result;
      default: selected_result = '0;
    endcase
  end
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin valid_o <= 1'b0; result_o <= '0; end
    else begin valid_o <= valid_i; if (valid_i) result_o <= selected_result; end
  end
endmodule
'''
    rtl = (rtl.replace("__OPERAND_MSB__", str(derived["operand_width"] - 1))
           .replace("__OUTPUT_MSB__", str(derived["output_width"] - 1))
           .replace("__ZERO_EXT_WIDTH__", str(derived["output_width"] - derived["operand_width"])))
    output.write_text(rtl)
    manifest_paths = dict(evidence_paths)
    manifest_paths["generator"] = Path(__file__).resolve()
    manifest_paths["generated_rtl"] = output.resolve()
    manifest = {
        "schema": "fu-rtl-generator/v2",
        "specialization": "graph-gated_specialized_RTL_template_unsigned_32bit_ADD_MUL_MAC",
        "universal": False, "template_backed": True,
        "graph_to_fused_rtl_status": "partial",
        "mac_topology_derived_from_structural_merge": False,
        "structural_merge_executable": False,
        "derived_from_graph": derived,
        "inputs": _evidence_hashes(manifest_paths),
        "generated_rtl_sha256": _sha256(output),
        "modes": derived["mux_behavior"],
        "structural_candidate_role": "eligibility and provenance evidence only; not executable hardware",
        "protocol": {"ready": "rst_ni", "latency_cycles": 1,
                      "initiation_interval_cycles": 1},
    }
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-a", type=Path, required=True)
    parser.add_argument("--graph-b", type=Path, required=True)
    parser.add_argument("--match", type=Path, required=True)
    parser.add_argument("--merge", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        inputs = {"graph_a": args.graph_a.resolve(), "graph_b": args.graph_b.resolve(),
                  "match": args.match.resolve(), "merge": args.merge.resolve()}
        manifest = emit(args.output.resolve(), _load(args.graph_a), _load(args.graph_b),
                        _load(args.match), _load(args.merge), inputs)
    except (OSError, ValueError, RTLGenerationError) as exc:
        parser.error(str(exc))
    print("generated {}".format(args.output))
    print("wrote {}".format(args.output.with_suffix(args.output.suffix + ".manifest.json")))
    print("specialization: {}".format(manifest["specialization"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
