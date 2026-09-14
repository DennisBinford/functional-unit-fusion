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
from typing import Any, Dict, List, Optional, Tuple

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


def _signedness(node: Dict[str, Any]) -> Any:
    attrs = node.get("attrs") or {}
    if "signed" in attrs:
        value = attrs["signed"]
        if isinstance(value, str):
            return value.lower() not in ("0", "false", "no", "unsigned")
        return bool(value)
    params = attrs.get("parameters") or {}
    values = tuple(sorted((str(k), str(v)) for k, v in params.items()
                          if "SIGNED" in str(k).upper()))
    if not values:
        return None
    return any(str(value).lower() not in ("0", "false", "no", "unsigned")
               for _, value in values)


def _unsigned_capability_node(node: Dict[str, Any]) -> bool:
    """Capability mode is deliberately limited to explicitly unsigned nodes."""
    return _signedness(node) in (None, False)


def _input_widths(node: Dict[str, Any]) -> List[int]:
    attrs = node.get("attrs") or {}
    value = attrs.get("input_width", attrs.get("input_widths"))
    if isinstance(value, (list, tuple)):
        return [int(item) for item in value]
    if isinstance(value, (int, float)):
        return [int(value)]
    params = attrs.get("parameters") or {}
    widths = [int(params[name]) for name in ("A_WIDTH", "B_WIDTH", "C_WIDTH")
              if name in params]
    if widths:
        return widths
    width = node.get("width")
    return [int(width)] if isinstance(width, int) else []


def _output_width(node: Dict[str, Any]) -> Optional[int]:
    attrs = node.get("attrs") or {}
    value = attrs.get("output_width", attrs.get("output_widths"))
    if isinstance(value, (list, tuple)):
        return int(value[0]) if value else None
    if isinstance(value, (int, float)):
        return int(value)
    params = attrs.get("parameters") or {}
    if "Y_WIDTH" in params:
        return int(params["Y_WIDTH"])
    return node.get("width") if isinstance(node.get("width"), int) else None


def _capability_key(node: Dict[str, Any]) -> Tuple[Any, ...]:
    """Semantic key for capability matching, intentionally width-agnostic."""
    attrs = node.get("attrs") or {}
    params = attrs.get("parameters") or {}
    # Width and signedness are checked separately. Other parameters remain
    # semantic requirements; a different multiplier algorithm is not a match.
    params = tuple(sorted((str(k), str(v)) for k, v in params.items()
                          if "WIDTH" not in str(k).upper()
                          and "SIGNED" not in str(k).upper()))
    label = (str(node.get("label", node.get("id", "")))
             if node.get("kind") in ("port_in", "port_out") else "")
    return (_operation(node), node.get("kind", "").lstrip("$").strip("_").lower(),
            _signedness(node), label, params)


def capability_compatible(implementer: Dict[str, Any], required: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a directional adaptation when *implementer* can realize *required*.

    This is deliberately a narrow width-capability relation.  It does not
    claim algebraic equivalence, signed/unsigned equivalence, or interface
    compatibility that is not present in the graph metadata.
    """
    if (not _unsigned_capability_node(implementer) or
            not _unsigned_capability_node(required) or
            _capability_key(implementer) != _capability_key(required)):
        return None
    iw, rw = implementer.get("width"), required.get("width")
    if not isinstance(iw, int) or not isinstance(rw, int) or iw < rw:
        return None
    implementation_inputs = _input_widths(implementer)
    required_inputs = _input_widths(required)
    if len(implementation_inputs) != len(required_inputs):
        return None
    if any(actual < needed for actual, needed in
           zip(implementation_inputs, required_inputs)):
        return None
    implementation_output = _output_width(implementer)
    required_output = _output_width(required)
    if (implementation_output is None or required_output is None or
            implementation_output < required_output):
        return None
    return {
        "direction": "implementer_to_required",
        "implementer_width": iw,
        "required_width": rw,
        "input_extension": [
            {"input": index, "from_width": int(width),
             "to_width": int(implementation_inputs[index]), "mode": "zero_extend"}
            for index, width in enumerate(required_inputs)
        ],
        "output_slicing": {
            "from_width": int(implementation_output), "to_width": int(required_output),
            "range": "[{}:0]".format(int(required_output) - 1),
        },
    }


def _interface_compatible(implementer: Dict[str, Any], required: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Check that a wider implementation has the same named port interface."""
    def ports(graph: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        return {
            (node.get("kind", ""), str(node.get("label", node.get("id", "")))): node
            for node in graph.get("nodes", [])
            if node.get("kind") in ("port_in", "port_out")
        }

    implementation_ports, required_ports = ports(implementer), ports(required)
    if set(implementation_ports) != set(required_ports):
        return None
    adaptations = []
    for key in sorted(required_ports):
        implementation, requirement = implementation_ports[key], required_ports[key]
        iw, rw = implementation.get("width"), requirement.get("width")
        if not isinstance(iw, int) or not isinstance(rw, int) or iw < rw:
            return None
        direction = "input" if key[0] == "port_in" else "output"
        adaptations.append({
            "port": key[1], "direction": direction,
            "implementer_width": iw, "required_width": rw,
            "adaptation": ("zero_extend" if direction == "input" and iw > rw
                           else "slice" if direction == "output" and iw > rw
                           else "none"),
        })
    return {"ports": adaptations}


def _edge_width_adapter(edge_a: Dict[str, Any], edge_b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    width_a, width_b = edge_a.get("width", 1), edge_b.get("width", 1)
    if not isinstance(width_a, int) or not isinstance(width_b, int):
        return None
    if width_a >= width_b:
        direction, implementation, required = "a_implements_b", width_a, width_b
    elif width_b >= width_a:
        direction, implementation, required = "b_implements_a", width_b, width_a
    else:
        return None
    return {
        "direction": direction,
        "implementer_width": implementation,
        "required_width": required,
        "adapter": "zero_extend" if implementation > required else "none",
    }


def _matched_capability_edges(
    graph_a: Dict[str, Any], graph_b: Dict[str, Any],
    matched_nodes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Match connected edges after node matching, retaining width adapters."""
    nodes_a = {node["id"]: node for node in graph_a.get("nodes", [])}
    nodes_b = {node["id"]: node for node in graph_b.get("nodes", [])}
    mapping = {item["a"]: item["b"] for item in matched_nodes}
    edges_b = list(graph_b.get("edges", []))
    used_b = set()
    records = []
    for edge_a in graph_a.get("edges", []):
        if edge_a.get("src") not in mapping or edge_a.get("dst") not in mapping:
            continue
        candidates = []
        for index, edge_b in enumerate(edges_b):
            if index in used_b:
                continue
            if (edge_b.get("src") != mapping[edge_a["src"]] or
                    edge_b.get("dst") != mapping[edge_a["dst"]] or
                    edge_b.get("src_port", "") != edge_a.get("src_port", "") or
                    bool(edge_b.get("inverted", False)) != bool(edge_a.get("inverted", False))):
                continue
            operation = _operation(nodes_a[edge_a["dst"]])
            if (operation not in COMMUTATIVE_OPERATIONS and
                    edge_b.get("dst_port", "") != edge_a.get("dst_port", "")):
                continue
            adapter = _edge_width_adapter(edge_a, edge_b)
            if adapter is not None:
                if (nodes_a[edge_a["dst"]].get("kind") == "port_out" or
                        nodes_b[edge_b["dst"]].get("kind") == "port_out"):
                    adapter["adapter"] = "slice" if adapter["implementer_width"] > adapter["required_width"] else "none"
                candidates.append((index, edge_b, adapter))
        if len(candidates) != 1:
            continue
        index, edge_b, adapter = candidates[0]
        used_b.add(index)
        records.append({
            "a": {key: edge_a.get(key) for key in
                  ("src", "dst", "src_port", "dst_port", "width", "inverted")},
            "b": {key: edge_b.get(key) for key in
                  ("src", "dst", "src_port", "dst_port", "width", "inverted")},
            "capability": adapter,
        })
    return records


def _incoming(graph: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    incoming = defaultdict(list)
    for edge in graph.get("edges", []):
        incoming[edge["dst"]].append(edge)
    return incoming


def _signatures(graph: Dict[str, Any], capability: bool = False) -> Dict[str, Tuple[Any, ...]]:
    """Build compact fixed-point rooted signatures for a DAG or stable graph."""
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    incoming = _incoming(graph)
    base_key = _capability_key if capability else _node_key
    signatures = {
        node_id: hashlib.sha256(repr(base_key(node)).encode()).hexdigest()
        for node_id, node in nodes.items()
    }
    # A fixed point may require graph depth iterations, but using node count as
    # the bound is needlessly expensive for large synthesized netlists.
    for _ in range(min(64, max(1, len(nodes)))):
        updated = {}
        for node_id, node in nodes.items():
            if _operation(node) in COMMUTATIVE_OPERATIONS:
                parents = sorted(
                    ((edge.get("width", 1),) if not capability else ()) +
                    (bool(edge.get("inverted", False)), signatures.get(edge["src"]))
                    for edge in incoming[node_id]
                )
            else:
                # Preserve the destination-port association for
                # noncommutative operations. Sorting the complete tuples
                # would erase operand order when source signatures differ.
                parents = [
                    (edge.get("dst_port", ""),) +
                    ((edge.get("width", 1),) if not capability else ()) +
                    (bool(edge.get("inverted", False)), signatures.get(edge["src"]))
                    for edge in sorted(incoming[node_id],
                                       key=lambda edge: edge.get("dst_port", ""))
                ]
            payload = repr((base_key(node), parents)).encode()
            updated[node_id] = hashlib.sha256(payload).hexdigest()
        if updated == signatures:
            break
        signatures = updated
    return signatures


def compare(graph_a: Dict[str, Any], graph_b: Dict[str, Any], mode: str = "exact") -> Dict[str, Any]:
    _validate_graph_pair(graph_a, graph_b)
    if mode not in ("exact", "capability"):
        raise GraphMatchError("unknown match mode {!r}".format(mode))
    capability = mode == "capability"
    interface_a_to_b = _interface_compatible(graph_a, graph_b) if capability else {}
    interface_b_to_a = _interface_compatible(graph_b, graph_a) if capability else {}
    signed_capability_blocked = capability and any(
        not _unsigned_capability_node(node)
        for graph in (graph_a, graph_b) for node in graph.get("nodes", [])
    )
    sig_a, sig_b = _signatures(graph_a, capability), _signatures(graph_b, capability)
    nodes_a = {node["id"]: node for node in graph_a.get("nodes", [])}
    nodes_b = {node["id"]: node for node in graph_b.get("nodes", [])}
    by_sig_b = defaultdict(list)
    for node_id, signature in sig_b.items():
        by_sig_b[signature].append(node_id)

    matched = []
    used_b = set()
    for node_id in sorted(sig_a):
        candidates = []
        for candidate in by_sig_b[sig_a[node_id]]:
            if candidate in used_b:
                continue
            if capability and (signed_capability_blocked or
                               (interface_a_to_b is None and interface_b_to_a is None)):
                continue
            if capability:
                relation = (capability_compatible(nodes_a[node_id], nodes_b[candidate])
                            or capability_compatible(nodes_b[candidate], nodes_a[node_id]))
                if relation is None:
                    continue
            candidates.append(candidate)
        if len(candidates) == 1:
            item = {"a": node_id, "b": candidates[0]}
            if capability:
                a, b = nodes_a[node_id], nodes_b[candidates[0]]
                a_to_b = capability_compatible(a, b)
                b_to_a = capability_compatible(b, a)
                if a_to_b and b_to_a:
                    item["capability"] = {"direction": "equal_width", "adaptation": a_to_b}
                elif a_to_b:
                    item["capability"] = {"direction": "a_implements_b", "adaptation": a_to_b}
                else:
                    item["capability"] = {"direction": "b_implements_a", "adaptation": b_to_a}
            matched.append(item)
            used_b.add(candidates[0])

    ids_a, ids_b = set(sig_a), set(sig_b)
    count = len(matched)
    matched_edges = (_matched_capability_edges(graph_a, graph_b, matched)
                     if capability else [])
    common_subgraphs = enumerate_common_subgraphs(graph_a, graph_b, matched)
    result = {
        "schema": MATCH_SCHEMA,
        "graph_a": graph_a.get("design"),
        "graph_b": graph_b.get("design"),
        "level": graph_a.get("level"),
        "metadata": {
            "a": _graph_metadata(graph_a),
            "b": _graph_metadata(graph_b),
        },
        "match_mode": "capability_rooted_structural" if capability else "exact_rooted_structural",
        "matched_nodes": matched,
        "matched_edges": matched_edges,
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
    if capability:
        if signed_capability_blocked:
            result["capability_rejected"] = "signed node metadata is unsupported in capability mode"
        if interface_a_to_b is None and interface_b_to_a is None:
            result["interface"] = {"compatible": False}
        elif interface_a_to_b and interface_b_to_a:
            result["interface"] = {"compatible": True, "direction": "equal_width",
                                    "adaptation": interface_a_to_b}
        elif interface_a_to_b:
            result["interface"] = {"compatible": True, "direction": "a_implements_b",
                                    "adaptation": interface_a_to_b}
        else:
            result["interface"] = {"compatible": True, "direction": "b_implements_a",
                                    "adaptation": interface_b_to_a}
        result["limitations"].extend([
            "Capability mode is unsigned and width-directional; it does not prove algebraic equivalence.",
            "The recorded direction identifies the wider implementer; reverse replacement is rejected.",
        ])
    return result


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
    parser.add_argument("--mode", choices=("exact", "capability"), default="exact",
                        help="exact (default) or directional width-capability matching")
    args = parser.parse_args()
    graph_a = json.loads(args.graph_a.read_text())
    graph_b = json.loads(args.graph_b.read_text())
    result = compare(graph_a, graph_b, mode=args.mode)
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
