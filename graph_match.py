#!/usr/bin/env python3
"""Find conservative exact-structure candidates between two FU graph JSON files.

This is an initial baseline for the research question, not a fusion optimizer.
It matches nodes by semantic attributes and the signatures of their incoming
connections. Tool-generated IDs are never used as identities across graphs.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from graph_extract import GRAPH_SCHEMA, normalize_operation


MATCH_SCHEMA = "fu-graph-match/v1"
COMMUTATIVE_OPERATIONS = frozenset(("add", "mul", "and", "or", "xor", "xnor"))


class GraphMatchError(ValueError):
    """Raised when the input graph pair is not comparable."""


def _operation(node: Dict[str, Any]) -> str:
    attrs = node.get("attrs") or {}
    return normalize_operation(attrs.get("operation") or node.get("kind", ""))


def _width_attribute(attrs: Dict[str, Any], name: str) -> Any:
    value = attrs.get(name)
    if isinstance(value, (list, tuple)):
        return tuple(value)
    if isinstance(value, dict):
        return tuple(sorted((str(key), str(item)) for key, item in value.items()))
    return value


def _graph_metadata(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Return comparable top-level provenance without inventing missing fields."""
    attrs = graph.get("attrs") or {}
    metadata = {
        "design": graph.get("design"),
        "level": graph.get("level"),
        "top": graph.get("top"),
    }
    for key in ("sources", "source", "source_manifest", "tool", "tool_path",
                "tool_version", "toolchain", "yosys_passes"):
        if key in graph:
            metadata[key] = graph[key]
        elif key in attrs:
            metadata[key] = attrs[key]
    return metadata


def _validate_graph_pair(graph_a: Dict[str, Any], graph_b: Dict[str, Any]) -> None:
    for label, graph in (("graph_a", graph_a), ("graph_b", graph_b)):
        if graph.get("schema") != GRAPH_SCHEMA:
            raise GraphMatchError(
                "{} has schema {!r}; expected {!r}".format(
                    label, graph.get("schema"), GRAPH_SCHEMA
                )
            )
        if not graph.get("level"):
            raise GraphMatchError("{} is missing a graph level".format(label))
    if graph_a["level"] != graph_b["level"]:
        raise GraphMatchError(
            "graph levels differ: {!r} vs {!r}".format(
                graph_a["level"], graph_b["level"]
            )
        )


def _node_key(node: Dict[str, Any]) -> Tuple[Any, ...]:
    attrs = node.get("attrs") or {}
    params = attrs.get("parameters") or {}
    return (
        _operation(node), node.get("width"), attrs.get("signed"),
        _width_attribute(attrs, "input_width"),
        _width_attribute(attrs, "output_width"),
        _width_attribute(attrs, "input_widths"),
        _width_attribute(attrs, "output_widths"),
        tuple(sorted((str(k), str(v)) for k, v in params.items())),
    )


def _incoming(graph: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    incoming = defaultdict(list)
    for edge in graph.get("edges", []):
        incoming[edge["dst"]].append(edge)
    return incoming


def _signatures(graph: Dict[str, Any]) -> Dict[str, Tuple[Any, ...]]:
    """Build compact fixed-point rooted signatures for a DAG or stable graph."""
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    incoming = _incoming(graph)
    signatures = {
        node_id: hashlib.sha256(repr(_node_key(node)).encode()).hexdigest()
        for node_id, node in nodes.items()
    }
    # A fixed point may require graph depth iterations, but using node count as
    # the bound is needlessly expensive for large synthesized netlists.
    for _ in range(min(64, max(1, len(nodes)))):
        updated = {}
        for node_id, node in nodes.items():
            if _operation(node) in COMMUTATIVE_OPERATIONS:
                parents = sorted(
                    (edge.get("width", 1),
                     bool(edge.get("inverted", False)), signatures.get(edge["src"]))
                    for edge in incoming[node_id]
                )
            else:
                parents = sorted(
                    (edge.get("dst_port", ""), edge.get("width", 1),
                     bool(edge.get("inverted", False)), signatures.get(edge["src"]))
                    for edge in incoming[node_id]
                )
            payload = repr((_node_key(node), parents)).encode()
            updated[node_id] = hashlib.sha256(payload).hexdigest()
        if updated == signatures:
            break
        signatures = updated
    return signatures


def compare(graph_a: Dict[str, Any], graph_b: Dict[str, Any]) -> Dict[str, Any]:
    _validate_graph_pair(graph_a, graph_b)
    sig_a, sig_b = _signatures(graph_a), _signatures(graph_b)
    by_sig_b = defaultdict(list)
    for node_id, signature in sig_b.items():
        by_sig_b[signature].append(node_id)

    matched = []
    used_b = set()
    for node_id in sorted(sig_a):
        candidates = [candidate for candidate in by_sig_b[sig_a[node_id]]
                      if candidate not in used_b]
        if len(candidates) == 1:
            matched.append({"a": node_id, "b": candidates[0]})
            used_b.add(candidates[0])

    ids_a, ids_b = set(sig_a), set(sig_b)
    count = len(matched)
    common_subgraphs = enumerate_common_subgraphs(graph_a, graph_b, matched)
    return {
        "schema": MATCH_SCHEMA,
        "graph_a": graph_a.get("design"),
        "graph_b": graph_b.get("design"),
        "level": graph_a.get("level"),
        "metadata": {
            "a": _graph_metadata(graph_a),
            "b": _graph_metadata(graph_b),
        },
        "match_mode": "exact_rooted_structural",
        "matched_nodes": matched,
        "matched_edges": [],
        "common_subgraphs": common_subgraphs,
        "common_node_count": count,
        "coverage": {
            "a": count / len(ids_a) if ids_a else 0.0,
            "b": count / len(ids_b) if ids_b else 0.0,
        },
        "unmatched": {
            "a": sorted(ids_a - {item["a"] for item in matched}),
            "b": sorted(ids_b - {item["b"] for item in matched}),
        },
        "limitations": [
            "Exact structural candidates only; no algebraic or e-graph equivalence.",
            "Ambiguous repeated signatures are excluded rather than guessed.",
            "A match is not evidence that fusion improves PPA.",
        ],
    }


def enumerate_common_subgraphs(graph_a: Dict[str, Any], graph_b: Dict[str, Any],
                               matched_nodes: List[Dict[str, str]],
                               min_size: int = 2, max_size: int = 6,
                               max_results: int = 5000) -> List[Dict[str, Any]]:
    """Enumerate bounded connected exact subgraphs among unique node matches.

    This is intentionally conservative: every selected node must be matched by
    the fixed-point matcher, and every reported edge must exist in both graphs
    with the same endpoint mapping and port metadata.
    """
    nodes_a = {node["id"]: node for node in graph_a.get("nodes", [])}
    edges_a = {(e["src"], e["dst"], e.get("src_port", ""), e.get("dst_port", ""),
               e.get("width", 1), bool(e.get("inverted", False))) for e in graph_a.get("edges", [])}
    edges_b = {(e["src"], e["dst"], e.get("src_port", ""), e.get("dst_port", ""),
               e.get("width", 1), bool(e.get("inverted", False))) for e in graph_b.get("edges", [])}
    inverse = {item["a"]: item["b"] for item in matched_nodes}
    ids = sorted(inverse)
    adjacency = {node_id: set() for node_id in ids}
    edge_records = {}
    for src, dst, *meta in edges_a:
        if src not in inverse or dst not in inverse:
            continue
        mapped_edge = (inverse[src], inverse[dst], *meta)
        same_edge = mapped_edge in edges_b
        if not same_edge and _operation(nodes_a[dst]) in COMMUTATIVE_OPERATIONS:
            # The input port is an operand position for non-commutative cells,
            # but not for a commutative operation. Width and inversion remain
            # part of the exact structural match.
            src_port, _dst_port, width, inverted = meta
            same_edge = any(
                candidate[:2] == mapped_edge[:2]
                and candidate[2] == src_port
                and candidate[4:] == (width, inverted)
                for candidate in edges_b
            )
        if not same_edge:
            continue
        adjacency[src].add(dst); adjacency[dst].add(src)
        edge_records[(src, dst)] = {"a": {"src": src, "dst": dst},
                                    "b": {"src": inverse[src], "dst": inverse[dst]}}

    # Grow only through matching edges; this avoids the combinatorial explosion
    # of trying every combination of nodes in large AIGs.
    results = []
    seen_subgraphs = set()
    def grow(selected, frontier):
        if len(selected) >= min_size:
            if len(results) >= max_results:
                return
            key = tuple(sorted(selected))
            if key not in seen_subgraphs:
                seen_subgraphs.add(key)
                chosen_edges = [record for (src, dst), record in edge_records.items()
                                if src in selected and dst in selected]
                results.append({"nodes_a": list(key),
                                "nodes_b": sorted(inverse[node_id] for node_id in key),
                                "edges": chosen_edges,
                                "node_count": len(key),
                                "edge_count": len(chosen_edges)})
        if len(selected) >= max_size:
            return
        for candidate in sorted(frontier):
            if len(results) >= max_results:
                return
            grow(selected | {candidate},
                 (frontier | adjacency[candidate]) - {candidate} - selected)
    for node_id in ids:
        grow({node_id}, adjacency[node_id])
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("graph_a", type=Path)
    parser.add_argument("graph_b", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--max-subgraph-size", type=int, default=6)
    args = parser.parse_args()
    graph_a = json.loads(args.graph_a.read_text())
    graph_b = json.loads(args.graph_b.read_text())
    result = compare(graph_a, graph_b)
    result["common_subgraphs"] = enumerate_common_subgraphs(
        graph_a, graph_b, result["matched_nodes"], max_size=args.max_subgraph_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("matched {} nodes ({:.1%} of A, {:.1%} of B)".format(
        result["common_node_count"], result["coverage"]["a"], result["coverage"]["b"]))
    print("wrote {}".format(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
