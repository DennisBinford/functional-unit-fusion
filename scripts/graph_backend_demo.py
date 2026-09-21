#!/usr/bin/env python3
"""Compare native and NetworkX structural engines without changing baseline artifacts."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from egraph_backend import EGraphBackend
from graph_backend import NetworkXBackend, backend_capabilities, canonicalize, graph_hash
from graph_match import compare


ARTIFACT = ROOT / "meeting-artifacts" / "graph-backends"


def load(path):
    return json.loads(Path(path).read_text())


def pair(label, left, right, mode="exact"):
    a, b = load(left), load(right)
    result = {"label": label, "sources": [str(left), str(right)], "level": a["level"]}
    for engine in ("native", "networkx"):
        started = time.monotonic()
        found = compare(a, b, mode=mode, engine=engine)
        result[engine] = {"matched_nodes": found["matched_nodes"], "coverage": found["coverage"],
                          "largest_bounded_subgraph": max([item["node_count"] for item in found.get("common_subgraphs", [])] or [0]),
                          "runtime_seconds": time.monotonic() - started,
                          "deterministic_match_hash": graph_hash({**a, "attrs": {"matched": found["matched_nodes"]}})}
        if "networkx_structural_algorithm" in found:
            result[engine]["structural_algorithm"] = found["networkx_structural_algorithm"]
    result["semantic_difference"] = result["native"]["matched_nodes"] != result["networkx"]["matched_nodes"]
    return result


def main():
    source = ROOT / "meeting-artifacts/graph-to-rtl/graphs"
    baseline = ROOT / "meeting-artifacts/2026-09-14/studies"
    pairs = [
        ("executable_rtlil_pair", source / "graph_unit_a_mul.rtlil.graph.json", source / "graph_unit_b_add_mul_mac.rtlil.graph.json"),
        ("capability_add32_add64", baseline / "capability_add32/capability_add32.rtlil.graph.json", baseline / "capability_add64/capability_add64.rtlil.graph.json", "capability"),
        ("capability_add64_add32", baseline / "capability_add64/capability_add64.rtlil.graph.json", baseline / "capability_add32/capability_add32.rtlil.graph.json", "capability"),
        ("horizontal_ibex_rtlil", baseline / "ibex_fused_ex_block_wrapper/ibex_fused_ex_block_wrapper.rtlil.graph.json", baseline / "ibex_alu_wrapper/ibex_alu_wrapper.rtlil.graph.json"),
    ]
    results = {"schema": "fu-graph-backend-comparison/v1", "backend_capabilities": backend_capabilities(),
               "pairs": [pair(*item) for item in pairs], "egraph_feasibility": EGraphBackend.feasibility(),
               "networkx_representation": "MultiDiGraph with stable hash-derived multiedge keys",
               "memory_measurement": None,
               "limitations": ["NetworkX contributes a Weisfeiler-Lehman structural hash and lossless graph conversion; the final conservative semantic matcher remains shared with the native engine, so this is not a second matching algorithm.",
                               "The egglog result is an isolated feasibility spike, not integrated project-backend support; global multi-output extraction remains open."]}
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT / "backend_comparison.json"
    path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    (ARTIFACT / "egraph_feasibility.json").write_text(json.dumps(EGraphBackend.feasibility(), indent=2, sort_keys=True) + "\n")
    print(json.dumps({"path": str(path), "networkx": results["backend_capabilities"].get("networkx"),
                      "pairs": [(item["label"], item["native"]["matched_nodes"], item["networkx"]["matched_nodes"]) for item in results["pairs"]],
                      "egraph": results["egraph_feasibility"]["status"]}, indent=2))


if __name__ == "__main__":
    main()
