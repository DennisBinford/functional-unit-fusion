#!/usr/bin/env python3
"""Coalesce exact graph matches into a provenance-preserving merge candidate.

This stage creates a structural artifact, not executable hardware. Exact node
matches are represented once, unmatched structure from both input graphs is
retained, and every node and edge records which source graph supplied it. A
later hardware-realization stage must add steering/control, emit RTL, and
verify PPA before the candidate can be called an implementation.
"""

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from graph_extract import Edge, FUGraph, GRAPH_SCHEMA, Node
from graph_match import (MATCH_SCHEMA, GraphMatchError, _node_key,
                         _interface_compatible, _validate_graph_pair,
                         capability_compatible)


class GraphMergeError(ValueError):
    """Raised when graphs and their match artifact cannot be merged safely."""


def _nodes_by_id(graph: Dict[str, Any], label: str) -> Dict[str, Dict[str, Any]]:
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        raise GraphMergeError("{} has no node list".format(label))
    result = {}
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise GraphMergeError("{} contains a node without a string id".format(label))
        if node_id in result:
            raise GraphMergeError("{} contains duplicate node id {!r}".format(label, node_id))
        result[node_id] = node
    return result


def _validate_match(
    graph_a: Dict[str, Any],
    graph_b: Dict[str, Any],
    match: Dict[str, Any],
) -> List[Tuple[str, str]]:
    try:
        _validate_graph_pair(graph_a, graph_b)
    except GraphMatchError as exc:
        raise GraphMergeError(str(exc))
    if match.get("schema") != MATCH_SCHEMA:
        raise GraphMergeError(
            "match has schema {!r}; expected {!r}".format(
                match.get("schema"), MATCH_SCHEMA
            )
        )
    if match.get("level") != graph_a.get("level"):
        raise GraphMergeError("match level does not agree with input graphs")
    if match.get("graph_a") != graph_a.get("design"):
        raise GraphMergeError("match graph_a does not identify the first input graph")
    if match.get("graph_b") != graph_b.get("design"):
        raise GraphMergeError("match graph_b does not identify the second input graph")

    nodes_a = _nodes_by_id(graph_a, "graph_a")
    nodes_b = _nodes_by_id(graph_b, "graph_b")
    capability = str(match.get("match_mode", "")).startswith("capability")
    if capability:
        interface_a_to_b = _interface_compatible(graph_a, graph_b)
        interface_b_to_a = _interface_compatible(graph_b, graph_a)
        if interface_a_to_b is None and interface_b_to_a is None:
            raise GraphMergeError("capability match has incompatible interfaces")
    pairs = []
    seen_a, seen_b = set(), set()
    for item in match.get("matched_nodes") or []:
        node_a, node_b = item.get("a"), item.get("b")
        if node_a not in nodes_a or node_b not in nodes_b:
            raise GraphMergeError("match references a node absent from its source graph")
        if node_a in seen_a or node_b in seen_b:
            raise GraphMergeError("matched nodes must form a one-to-one mapping")
        if capability:
            if not (capability_compatible(nodes_a[node_a], nodes_b[node_b]) or
                    capability_compatible(nodes_b[node_b], nodes_a[node_a])):
                raise GraphMergeError("capability match is not directional-compatible")
        elif _node_key(nodes_a[node_a]) != _node_key(nodes_b[node_b]):
            raise GraphMergeError(
                "matched nodes {!r} and {!r} are not semantically compatible".format(
                    node_a, node_b
                )
            )
        seen_a.add(node_a)
        seen_b.add(node_b)
        pairs.append((node_a, node_b))
    return sorted(pairs)


def _node_from_source(
    source: Dict[str, Any],
    merged_id: str,
    membership: Sequence[str],
    origins: Dict[str, str],
) -> Node:
    attrs = copy.deepcopy(source.get("attrs") or {})
    attrs["merge"] = {
        "membership": list(membership),
        "origins": dict(origins),
        "shared_candidate": len(membership) == 2,
    }
    return Node(
        id=merged_id,
        kind=source.get("kind", "?"),
        label=source.get("label") or source.get("kind", "?"),
        width=source.get("width"),
        attrs=attrs,
    )


def _edge_key(edge: Dict[str, Any], id_map: Dict[str, str]) -> Tuple[Any, ...]:
    try:
        return (
            id_map[edge["src"]], id_map[edge["dst"]],
            edge.get("src_port", ""), edge.get("dst_port", ""),
            edge.get("width", 1), bool(edge.get("inverted", False)),
        )
    except KeyError as exc:
        raise GraphMergeError("edge references unknown node {!r}".format(exc.args[0]))


def merge_graphs(
    graph_a: Dict[str, Any],
    graph_b: Dict[str, Any],
    match: Dict[str, Any],
) -> FUGraph:
    """Return the exact structural union with matched nodes coalesced."""
    pairs = _validate_match(graph_a, graph_b, match)
    nodes_a = _nodes_by_id(graph_a, "graph_a")
    nodes_b = _nodes_by_id(graph_b, "graph_b")
    capability = str(match.get("match_mode", "")).startswith("capability")
    map_a, map_b = {}, {}
    merged_nodes = []

    for index, (node_a, node_b) in enumerate(pairs):
        merged_id = "shared:{:05d}".format(index)
        map_a[node_a] = merged_id
        map_b[node_b] = merged_id
        source_label = "a"
        adaptation = None
        replacement_direction = "equal_width"
        if capability:
            a_to_b = capability_compatible(nodes_a[node_a], nodes_b[node_b])
            b_to_a = capability_compatible(nodes_b[node_b], nodes_a[node_a])
            if a_to_b and not b_to_a:
                source_label, adaptation = "a", a_to_b
                replacement_direction = "a_implements_b"
            elif b_to_a and not a_to_b:
                source_label, adaptation = "b", b_to_a
                replacement_direction = "b_implements_a"
            elif not a_to_b and not b_to_a:
                raise GraphMergeError("capability match lost directional compatibility")
        source_node = nodes_a[node_a] if source_label == "a" else nodes_b[node_b]
        merged_node = _node_from_source(
            source_node, merged_id, ("a", "b"), {"a": node_a, "b": node_b}
        )
        if capability:
            merged_node.attrs["merge"]["capability"] = {
                "implementation_graph": source_label,
                "replacement_direction": replacement_direction,
                "adaptation": adaptation,
            }
        merged_nodes.append(merged_node)

    for label, source_nodes, id_map in (
        ("a", nodes_a, map_a), ("b", nodes_b, map_b)
    ):
        for index, node_id in enumerate(sorted(source_nodes)):
            if node_id in id_map:
                continue
            merged_id = "{}:{:05d}".format(label, index)
            id_map[node_id] = merged_id
            merged_nodes.append(_node_from_source(
                source_nodes[node_id], merged_id, (label,), {label: node_id}
            ))

    if capability:
        # Capability edge records already carry the directional width proof.
        # Canonicalize each pair to the wider implementer's edge and retain the
        # adapters instead of silently unioning incompatible widths.
        pair_records = match.get("matched_edges") or []
        paired_edges = set()
        merged_edges = []
        for pair_index, pair in enumerate(pair_records):
            edge_a, edge_b = pair.get("a") or {}, pair.get("b") or {}
            if edge_a.get("src") not in map_a or edge_a.get("dst") not in map_a:
                raise GraphMergeError("capability edge references an unmatched graph_a node")
            if edge_b.get("src") not in map_b or edge_b.get("dst") not in map_b:
                raise GraphMergeError("capability edge references an unmatched graph_b node")
            adapter = pair.get("capability")
            if not isinstance(adapter, dict):
                raise GraphMergeError("capability edge is missing its width adapter")
            if adapter.get("direction") == "a_implements_b":
                implementer, required, label = edge_a, edge_b, "a"
            elif adapter.get("direction") == "b_implements_a":
                implementer, required, label = edge_b, edge_a, "b"
            elif adapter.get("direction") == "equal_width":
                implementer, required, label = edge_a, edge_b, "a"
            else:
                raise GraphMergeError("capability edge has an unknown replacement direction")
            source, destination = (map_a[implementer["src"]], map_a[implementer["dst"]]) \
                if label == "a" else (map_b[implementer["src"]], map_b[implementer["dst"]])
            merged_edges.append(Edge(
                src=source, dst=destination,
                src_port=implementer.get("src_port", ""),
                dst_port=implementer.get("dst_port", ""),
                width=implementer.get("width", 1),
                inverted=bool(implementer.get("inverted", False)),
                attrs={"merge": {
                    "membership": ["a", "b"],
                    "origins": [{"graph": "a", **edge_a}, {"graph": "b", **edge_b}],
                    "capability": {
                        "replacement_direction": adapter.get("direction"),
                        "adapter": adapter.get("adapter"),
                        "implementer_width": adapter.get("implementer_width"),
                        "required_width": adapter.get("required_width"),
                    },
                }},
            ))
            paired_edges.add(("a", tuple(edge_a.get(key) for key in
                                          ("src", "dst", "src_port", "dst_port", "width", "inverted"))))
            paired_edges.add(("b", tuple(edge_b.get(key) for key in
                                          ("src", "dst", "src_port", "dst_port", "width", "inverted"))))
        for label, source_graph, id_map in (("a", graph_a, map_a), ("b", graph_b, map_b)):
            for edge in source_graph.get("edges") or []:
                edge_id = (label, tuple(edge.get(key) for key in
                                        ("src", "dst", "src_port", "dst_port", "width", "inverted")))
                if edge_id in paired_edges:
                    continue
                merged_edges.append(Edge(
                    src=id_map[edge["src"]], dst=id_map[edge["dst"]],
                    src_port=edge.get("src_port", ""), dst_port=edge.get("dst_port", ""),
                    width=edge.get("width", 1), inverted=bool(edge.get("inverted", False)),
                    attrs={"merge": {"membership": [label], "origins": [{"graph": label, **edge}]}},
                ))
    else:
        edge_records: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for label, source_graph, id_map in (("a", graph_a, map_a), ("b", graph_b, map_b)):
            for edge in source_graph.get("edges") or []:
                key = _edge_key(edge, id_map)
                record = edge_records.setdefault(key, {"membership": [], "origins": []})
                record["membership"].append(label)
                record["origins"].append({
                    "graph": label,
                    "src": edge["src"], "dst": edge["dst"],
                    "src_port": edge.get("src_port", ""),
                    "dst_port": edge.get("dst_port", ""),
                })
        merged_edges = []
        for key in sorted(edge_records):
            src, dst, src_port, dst_port, width, inverted = key
            merged_edges.append(Edge(
                src=src, dst=dst, src_port=src_port, dst_port=dst_port,
                width=width, inverted=inverted, attrs={"merge": edge_records[key]},
            ))

    design_a, design_b = graph_a.get("design"), graph_b.get("design")
    merged = FUGraph(
        design="merged__{}__{}".format(design_a, design_b),
        level=graph_a["level"],
        top="merged_candidate",
        attrs={
            "artifact_type": "structural_merged_candidate",
            "source_graphs": {
                "a": {"design": design_a, "top": graph_a.get("top")},
                "b": {"design": design_b, "top": graph_b.get("top")},
            },
            "match": {
                "schema": match.get("schema"),
                "mode": match.get("match_mode"),
                "matched_node_count": len(pairs),
            },
            "merge_policy": {
                "nodes": ("coalesce one-to-one capability matches and retain the wider implementer"
                          if capability else "coalesce one-to-one exact matches"),
                "edges": ("coalesce matched edges at the wider width and record adapters"
                          if capability else "union exact edge records after endpoint remapping"),
            },
            "hardware_status": {
                "executable": False,
                "reason": (
                    "structural candidate only; steering/control, RTL emission, "
                    "functional verification, and synthesis remain required"
                ),
            },
            "node_accounting": {
                "graph_a": len(nodes_a),
                "graph_b": len(nodes_b),
                "coalesced": len(pairs),
                "merged_candidate": len(merged_nodes),
            },
        },
        nodes=merged_nodes,
        edges=merged_edges,
    )
    return merged


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise GraphMergeError("cannot read {}: {}".format(path, exc))
    if not isinstance(value, dict):
        raise GraphMergeError("{} does not contain a JSON object".format(path))
    return value


def _highlight(graph: FUGraph) -> Dict[str, str]:
    result = {}
    for node in graph.nodes:
        membership = ((node.attrs or {}).get("merge") or {}).get("membership") or []
        if membership == ["a", "b"]:
            result[node.id] = "shared"
        elif membership == ["a"]:
            result[node.id] = "unique_a"
        elif membership == ["b"]:
            result[node.id] = "unique_b"
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("graph_a", type=Path)
    parser.add_argument("graph_b", type=Path)
    parser.add_argument("match", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--render", type=Path, help="output stem for a highlighted figure")
    parser.add_argument("--format", default="svg", choices=("svg", "png", "pdf"))
    parser.add_argument("--max-nodes", type=int, default=400)
    args = parser.parse_args(argv)

    try:
        graph_a = _load_json(args.graph_a)
        graph_b = _load_json(args.graph_b)
        match = _load_json(args.match)
        merged = merge_graphs(graph_a, graph_b, match)
        merged.save(args.output)
        if args.render:
            from graph_viz import render
            render(
                merged,
                args.render,
                image_format=args.format,
                max_nodes=args.max_nodes,
                highlight=_highlight(merged),
                title=(
                    "Structural merge candidate: {} + {}\n"
                    "yellow=coalesced exact match; blue/red=source-unique"
                ).format(graph_a.get("design"), graph_b.get("design")),
            )
    except GraphMergeError as exc:
        print("graph merge failed: {}".format(exc), file=sys.stderr)
        return 2

    accounting = merged.attrs["node_accounting"]
    print(
        "merged {} + {} nodes into {} nodes ({} {} nodes coalesced)".format(
            accounting["graph_a"], accounting["graph_b"],
            accounting["merged_candidate"], accounting["coalesced"],
            "capability" if str(merged.attrs["match"]["mode"]).startswith("capability") else "exact",
        )
    )
    print("wrote {}".format(args.output))
    if args.render:
        print("rendered {}.{}".format(args.render, args.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
