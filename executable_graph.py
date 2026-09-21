#!/usr/bin/env python3
"""Realize a restricted RTLIL word-operator merge as an executable plan.

This is deliberately a small, evidence-driven realization pass.  It consumes
the two extracted graphs, the exact match, the non-executable structural merge,
and a control specification.  It does not infer missing arithmetic or ports.
"""

import argparse
import copy
import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from graph_extract import GRAPH_SCHEMA, normalize_operation


EXECUTABLE_SCHEMA = "fu-executable-graph/v1"
MATCH_SCHEMA = "fu-graph-match/v1"
SUPPORTED = {"port_in", "port_out", "const", "$add", "$mul", "$mux", "$not", "$xor", "concat", "slice"}


class ExecutableGraphError(ValueError):
    pass


def _load(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ExecutableGraphError("cannot read {}: {}".format(path, exc))
    if not isinstance(value, dict):
        raise ExecutableGraphError("{} is not a JSON object".format(path))
    return value


def _nodes(graph: Dict[str, Any], label: str) -> Dict[str, Dict[str, Any]]:
    result = {}
    for node in graph.get("nodes", []):
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id or node_id in result:
            raise ExecutableGraphError("{} has invalid or duplicate node IDs".format(label))
        if node.get("kind") not in SUPPORTED:
            raise ExecutableGraphError("{} uses unsupported RTLIL cell {}".format(label, node.get("kind")))
        width = node.get("width")
        if node.get("kind") != "port_in" and node.get("kind") != "port_out" and (
                not isinstance(width, int) or width < 1):
            raise ExecutableGraphError("{} node {} has an ambiguous width".format(label, node_id))
        attrs = node.get("attrs") or {}
        params = attrs.get("parameters") or {}
        if attrs.get("signed") in (True, "1", "true", "signed") or any(
                str(k).upper().endswith("_SIGNED") and str(v) not in ("0", "false", "False")
                for k, v in params.items()):
            raise ExecutableGraphError("signed arithmetic is unsupported: {}".format(node_id))
        result[node_id] = node
    return result


def _edges(graph: Dict[str, Any], nodes: Dict[str, Dict[str, Any]], label: str) -> List[Dict[str, Any]]:
    result = []
    for edge in graph.get("edges", []):
        if edge.get("src") not in nodes or edge.get("dst") not in nodes:
            raise ExecutableGraphError("{} edge references a missing node".format(label))
        if not edge.get("src_port") or not edge.get("dst_port"):
            raise ExecutableGraphError("{} edge has a missing port".format(label))
        if not isinstance(edge.get("width", 1), int) or edge.get("width", 1) < 1:
            raise ExecutableGraphError("{} edge has an ambiguous width".format(label))
        source_width = nodes[edge["src"]].get("width")
        if isinstance(source_width, int) and edge["width"] > source_width:
            raise ExecutableGraphError("{} edge widens a source without an adapter".format(label))
        result.append(dict(edge))
    return result


def _param(node: Dict[str, Any], name: str, default: Optional[int] = None) -> Optional[int]:
    value = (node.get("attrs") or {}).get("parameters", {}).get(name, default)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _expected_input_width(node: Dict[str, Any], port: str) -> Optional[int]:
    kind = node.get("kind")
    if kind == "$mux":
        return 1 if port == "S" else _param(node, "WIDTH", node.get("width"))
    if kind in ("$add", "$mul"):
        return _param(node, port + "_WIDTH")
    if kind in ("port_out",):
        return node.get("width")
    return None


def _is_zero_const(node: Dict[str, Any]) -> bool:
    return node.get("kind") == "const" and set(str((node.get("attrs") or {}).get("value", "0"))) <= {"0"}


def _normalise_source(graph: Dict[str, Any], label: str) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Normalize Yosys concat-of-zero into an explicit zero_extend node."""
    nodes = _nodes(graph, label)
    edges = _edges(graph, nodes, label)
    by_input: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        by_input[(edge["dst"], edge["dst_port"])].append(edge)
    normalized = []
    additions = []
    for key in sorted(by_input):
        dst, port = key
        group = sorted(by_input[key], key=lambda e: (e["src"], e.get("src_port", ""), e["width"]))
        expected = _expected_input_width(nodes[dst], port)
        total = sum(edge["width"] for edge in group)
        if len(group) == 1:
            edge = group[0]
            source_width = nodes[edge["src"]].get("width")
            positions = sorted((edge.get("attrs") or {}).get("bit_positions", []))
            source_positions = sorted((edge.get("attrs") or {}).get("source_bit_positions", []))
            if (isinstance(source_width, int) and source_width > edge["width"] and
                    len(positions) == edge["width"] and len(source_positions) == edge["width"] and positions and
                    source_positions == list(range(source_positions[0], source_positions[0] + edge["width"]))):
                aid = "adapter:slice:{}:{}".format(dst, port)
                additions.append({"id": aid, "kind": "slice", "label": "slice[{}:{}]".format(positions[-1], positions[0]),
                                  "width": edge["width"], "attrs": {"source_lsb": source_positions[0],
                                  "source_width": source_width, "provenance": {"graph": label, "source_edge": edge}}})
                normalized.append({"src": edge["src"], "dst": aid, "src_port": edge["src_port"],
                                   "dst_port": "A", "width": edge["width"], "inverted": False,
                                   "attrs": {"adapter": "slice", "bit_positions": positions, "source_edge": edge}})
                normalized.append({"src": aid, "dst": dst, "src_port": "Y", "dst_port": port,
                                   "width": edge["width"], "inverted": False, "attrs": {"adapter": "slice"}})
                continue
            if isinstance(source_width, int) and source_width > edge["width"]:
                raise ExecutableGraphError("unsafe truncation on {}.{}".format(dst, port))
            if expected is not None and edge["width"] != expected:
                if edge["width"] < expected and nodes[edge["src"]].get("kind") != "const":
                    aid = "adapter:zext:{}:{}".format(dst, port)
                    additions.append({"id": aid, "kind": "zero_extend", "label": "zero_extend[{}]".format(expected),
                                      "width": expected, "attrs": {"target_width": expected,
                                      "provenance": {"graph": label, "source_edge": edge}}})
                    normalized.append({"src": edge["src"], "dst": aid, "src_port": edge["src_port"],
                                       "dst_port": "A", "width": edge["width"], "inverted": False,
                                       "attrs": {"adapter": "zero_extend"}})
                    normalized.append({"src": aid, "dst": dst, "src_port": "Y", "dst_port": port,
                                       "width": expected, "inverted": False, "attrs": {"adapter": "zero_extend"}})
                    continue
                raise ExecutableGraphError("unsafe width on {}.{}".format(dst, port))
            normalized.append(edge)
            continue
        # Yosys represents packed operands as multiple edges into one cell
        # input. Preserve their bit positions in an explicit concat adapter;
        # this is needed for Ibex's {operand, carry-in} adder encoding and is
        # safe only when the extracted positions cover the declared width.
        data = [e for e in group if nodes[e["src"]].get("kind") != "const"]
        constants = [e for e in group if nodes[e["src"]].get("kind") == "const"]
        if len(data) != 1 or not constants or expected is None or total != expected:
            raise ExecutableGraphError("multiple drivers or unsafe concatenation on {}.{}".format(dst, port))
        segments = []
        all_positions = []
        for edge in group:
            positions = (edge.get("attrs") or {}).get("bit_positions")
            if not isinstance(positions, list) or len(positions) != edge["width"]:
                raise ExecutableGraphError("{} lacks lossless bit positions for {}.{}".format(label, dst, port))
            if len(set(positions)) != len(positions) or any(position < 0 or position >= expected for position in positions):
                raise ExecutableGraphError("unsafe or overlapping concatenation on {}.{}".format(dst, port))
            all_positions.extend(positions)
            segments.append((min(positions), max(positions), edge))
        if sorted(all_positions) != list(range(expected)):
            raise ExecutableGraphError("concatenation does not cover {}.{}".format(dst, port))
        aid = "adapter:concat:{}:{}".format(dst, port)
        additions.append({"id": aid, "kind": "concat", "label": "concat[{}]".format(expected),
                          "width": expected, "attrs": {"target_width": expected,
                          "segments": [{"bit_lsb": first, "width": edge["width"], "source": edge["src"]}
                                       for first, _last, edge in segments],
                          "provenance": {"graph": label, "source_edges": [edge for _, _, edge in segments]}}})
        for first, _last, edge in segments:
            normalized.append({"src": edge["src"], "dst": aid, "src_port": edge["src_port"],
                               "dst_port": "SEG_{}".format(first), "width": edge["width"], "inverted": False,
                               "attrs": {"adapter": "concat", "bit_lsb": first,
                                         "bit_positions": sorted((edge.get("attrs") or {}).get("bit_positions", [])),
                                         "source_bit_positions": sorted((edge.get("attrs") or {}).get("source_bit_positions", [])),
                                         "source_edge": edge}})
        normalized.append({"src": aid, "dst": dst, "src_port": "Y", "dst_port": port,
                           "width": expected, "inverted": False, "attrs": {"adapter": "concat"}})
    nodes.update({node["id"]: node for node in additions})
    return nodes, normalized, additions


def _toposort(nodes: Iterable[str], edges: Sequence[Dict[str, Any]]) -> List[str]:
    ids = set(nodes)
    outgoing = defaultdict(list)
    indegree = {node_id: 0 for node_id in ids}
    seen = set()
    for edge in edges:
        key = (edge["src"], edge["dst"], edge.get("dst_port", ""))
        if key in seen:
            continue
        seen.add(key)
        outgoing[edge["src"]].append(edge["dst"])
        indegree[edge["dst"]] += 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    order = []
    while queue:
        node_id = queue.popleft()
        order.append(node_id)
        for dst in sorted(outgoing[node_id]):
            indegree[dst] -= 1
            if indegree[dst] == 0:
                queue.append(dst)
    if len(order) != len(ids):
        raise ExecutableGraphError("executable graph contains a cycle")
    return order


def _validate_merge(graph_a, graph_b, match, merge, pair):
    if match.get("schema") != MATCH_SCHEMA or match.get("graph_a") != graph_a.get("design") or match.get("graph_b") != graph_b.get("design"):
        raise ExecutableGraphError("match provenance does not identify both source graphs")
    if match.get("level") != graph_a.get("level") or graph_a.get("level") != "rtlil":
        raise ExecutableGraphError("executable realization is restricted to RTLIL graphs")
    if merge.get("schema") != GRAPH_SCHEMA or merge.get("hardware_status", merge.get("attrs", {}).get("hardware_status", {})).get("executable", False):
        raise ExecutableGraphError("structural merge must remain non-executable")
    provenance = (merge.get("attrs") or {}).get("source_graphs") or {}
    if provenance.get("a", {}).get("design") != graph_a.get("design") or provenance.get("b", {}).get("design") != graph_b.get("design"):
        raise ExecutableGraphError("merge provenance does not identify both source graphs")
    a_id, b_id = pair["a"], pair["b"]
    if not any(item.get("a") == a_id and item.get("b") == b_id for item in match.get("matched_nodes", [])):
        raise ExecutableGraphError("control specification pair is absent from match artifact")
    found = False
    for node in merge.get("nodes", []):
        origins = ((node.get("attrs") or {}).get("merge") or {}).get("origins") or {}
        if origins.get("a") == a_id and origins.get("b") == b_id:
            found = True
    if not found:
        raise ExecutableGraphError("selected matched operator is absent from structural merge")


def realize(graph_a: Dict[str, Any], graph_b: Dict[str, Any], match: Dict[str, Any],
            merge: Dict[str, Any], control: Dict[str, Any]) -> Dict[str, Any]:
    for graph in (graph_a, graph_b):
        if graph.get("schema") != GRAPH_SCHEMA:
            raise ExecutableGraphError("source graph has the wrong schema")
    if control.get("schema") != "fu-control-spec/v1":
        raise ExecutableGraphError("control specification has the wrong schema")
    if (control.get("graph_a") != graph_a.get("design") or
            control.get("graph_b") != graph_b.get("design") or
            control.get("level") != graph_a.get("level") or
            graph_a.get("level") != "rtlil"):
        raise ExecutableGraphError("control specification provenance does not agree with graphs")
    if not isinstance(control.get("generated_top"), str) or not control["generated_top"]:
        raise ExecutableGraphError("control specification must request a generated top name")
    if not isinstance(control.get("generated_design"), str) or not control["generated_design"]:
        raise ExecutableGraphError("control specification must request a generated design name")
    pair = (control.get("shared_operator") or {}).get("match_pair") or {}
    operation = (control.get("shared_operator") or {}).get("operation")
    if not pair.get("a") or not pair.get("b") or operation not in ("add", "mul"):
        raise ExecutableGraphError("control specification must select one supported shared operator")
    nodes_a, edges_a, additions_a = _normalise_source(graph_a, "graph_a")
    nodes_b, edges_b, additions_b = _normalise_source(graph_b, "graph_b")
    _validate_merge(graph_a, graph_b, match, merge, pair)
    selected = []
    for source_nodes, node_id in ((nodes_a, pair["a"]), (nodes_b, pair["b"])):
        node = source_nodes.get(node_id)
        if not node or normalize_operation(node.get("kind", "")) != operation:
            raise ExecutableGraphError("selected shared operator does not agree with control metadata")
        selected.append(node)
    input_ports = set()
    for node in selected:
        input_ports.update(edge["dst_port"] for edge in
                           ((edges_a if node is selected[0] else edges_b))
                           if edge["dst"] == node["id"])
    if not input_ports:
        raise ExecutableGraphError("selected shared operator has no connected inputs")
    inserted = control.get("inserted_ports") or []
    if not inserted:
        raise ExecutableGraphError("control specification must declare an inserted fused control port")
    control_decl = (control.get("shared_control") or {})
    control_name = control_decl.get("port")
    if not isinstance(control_name, str) or not any(p.get("name") == control_name for p in inserted):
        raise ExecutableGraphError("shared control must identify an inserted port")
    control_node = "port:inserted:{}".format(control_name)
    plan_nodes = {control_node: {"id": control_node, "kind": "port_in", "label": control_name,
                                 "width": next(p["width"] for p in inserted if p["name"] == control_name),
                                 "attrs": {"direction": "input", "provenance": {"control_spec": control_name}}}}
    if plan_nodes[control_node]["width"] != 1:
        raise ExecutableGraphError("shared mux control must be one bit")
    map_a, map_b = {}, {}
    labels = {control_name}
    for label, source_nodes, mapping in (("a", nodes_a, map_a), ("b", nodes_b, map_b)):
        for node_id in sorted(source_nodes):
            mapping[node_id] = "shared:operator" if (label == "a" and node_id == pair["a"]) or (label == "b" and node_id == pair["b"]) else "{}:{}".format(label, node_id)
            if mapping[node_id] == "shared:operator":
                continue
            node = copy.deepcopy(source_nodes[node_id]); node["id"] = mapping[node_id]
            node.setdefault("attrs", {})["provenance"] = {"graph": label, "source_id": node_id}
            if node.get("kind") in ("port_in", "port_out"):
                if node.get("label") in labels or node.get("label") == control_name:
                    node["label"] = "{}_{}".format(label, node["label"])
                labels.add(node["label"])
            plan_nodes[node["id"]] = node
    shared = copy.deepcopy(selected[1]); shared["id"] = "shared:operator"
    shared.setdefault("attrs", {})["provenance"] = {"graphs": {"a": pair["a"], "b": pair["b"]}, "operation": operation}
    plan_nodes[shared["id"]] = shared
    plan_edges = []
    incoming_shared = defaultdict(list)
    for label, edges, mapping, selected_id in (("a", edges_a, map_a, pair["a"]), ("b", edges_b, map_b, pair["b"])):
        for edge in edges:
            if edge["dst"] == selected_id:
                incoming_shared[edge["dst_port"]].append((label, edge, mapping))
                continue
            plan_edges.append({**edge, "src": mapping[edge["src"]], "dst": mapping[edge["dst"]],
                               "attrs": {**(edge.get("attrs") or {}), "provenance": {"graph": label, "source": edge}}})
    mux_ids = []
    for dst_port in sorted(input_ports):
        entries = incoming_shared[dst_port]
        if len(entries) != 2 or {entry[0] for entry in entries} != {"a", "b"}:
            raise ExecutableGraphError("shared operator input {} lacks one edge from each graph".format(dst_port))
        a_entry = next(entry for entry in entries if entry[0] == "a")
        b_entry = next(entry for entry in entries if entry[0] == "b")
        if a_entry[1]["width"] != b_entry[1]["width"]:
            raise ExecutableGraphError("shared operator input {} has incompatible widths".format(dst_port))
        width = a_entry[1]["width"]
        mux_id = "adapter:mux:shared_operator:{}".format(dst_port)
        plan_nodes[mux_id] = {"id": mux_id, "kind": "$mux", "label": "shared input mux {}".format(dst_port),
                              "width": width, "attrs": {"parameters": {"WIDTH": width},
                              "provenance": {"purpose": "shared_operator_input", "input_port": dst_port,
                              "sources": [{"graph": "a", "edge": a_entry[1]}, {"graph": "b", "edge": b_entry[1]}]}}}
        for entry, mux_port in ((b_entry, "A"), (a_entry, "B")):
            plan_edges.append({"src": entry[2][entry[1]["src"]], "dst": mux_id,
                               "src_port": entry[1]["src_port"], "dst_port": mux_port, "width": width,
                               "inverted": False, "attrs": {"adapter": "shared_input_mux", "provenance": entry[1]}})
        plan_edges.append({"src": control_node, "dst": mux_id, "src_port": control_name, "dst_port": "S", "width": 1,
                           "inverted": False, "attrs": {"adapter": "control_spec"}})
        plan_edges.append({"src": mux_id, "dst": "shared:operator", "src_port": "Y", "dst_port": dst_port,
                           "width": width, "inverted": False, "attrs": {"adapter": "shared_input_mux"}})
        mux_ids.append(mux_id)

    outputs = []
    output_labels = set()
    for item in control.get("outputs", []):
        sources = item.get("sources")
        if sources is not None:
            if not isinstance(sources, list) or len(sources) != 2:
                raise ExecutableGraphError("multi-source output must declare exactly two source outputs")
            source_records = []
            for source in sources:
                label, source_port = source.get("graph"), source.get("port")
                source_nodes, mapping = ((nodes_a, map_a) if label == "a" else
                                         (nodes_b, map_b) if label == "b" else (None, None))
                source_edges = edges_a if label == "a" else edges_b if label == "b" else None
                if source_nodes is None:
                    raise ExecutableGraphError("output names an unknown graph")
                candidates = [node for node in source_nodes.values() if node.get("kind") == "port_out" and node.get("label") == source_port]
                final_edges = [edge for edge in source_edges if edge["dst"] == candidates[0]["id"]] if len(candidates) == 1 else []
                if (len(candidates) != 1 or len(final_edges) != 1 or
                        source.get("select_when", {}).get(control_name) not in (0, 1)):
                    raise ExecutableGraphError("explicit multi-source output is absent or lacks binary selection")
                source_records.append((source, label, source_port, mapping[final_edges[0]["src"]], candidates[0]["width"]))
            if source_records[0][4] != source_records[1][4] or {record[1] for record in source_records} != {"a", "b"}:
                raise ExecutableGraphError("multi-source output must pair one equal-width output from each graph")
            label, source_port, source_node, width = source_records[0][1], source_records[0][2], source_records[0][3], source_records[0][4]
            alternate = next(record for record in source_records[1:] if record[1] != label)
            select = item.get("sources")[0].get("select_when") if source_records[0][1] == "a" else alternate[0].get("select_when")
            active_graph_a = next(record for record in source_records if record[1] == "a")
            active_graph_b = next(record for record in source_records if record[1] == "b")
            source_node_a, source_node_b = active_graph_a[3], active_graph_b[3]
            zero_id = None
        else:
            label, source_port = item.get("graph"), item.get("port")
            source_nodes, mapping, source_edges = ((nodes_a, map_a, edges_a) if label == "a" else
                                                   (nodes_b, map_b, edges_b) if label == "b" else (None, None, None))
            if source_nodes is None:
                raise ExecutableGraphError("output names an unknown graph")
            candidates = [node for node in source_nodes.values() if node.get("kind") == "port_out" and node.get("label") == source_port]
            if len(candidates) != 1:
                raise ExecutableGraphError("explicit output is absent: {}.{}".format(label, source_port))
            source_node = mapping[candidates[0]["id"]]
            width = candidates[0]["width"]
        fused_port = item.get("fused_port", source_port)
        if fused_port in output_labels:
            raise ExecutableGraphError("duplicate fused output {}".format(fused_port))
        if sources is None:
            select = item.get("select_when") or item.get("enable") or {}
            if select.get(control_name) not in (0, 1):
                raise ExecutableGraphError("output {} lacks a binary control selection".format(fused_port))
        zero_id = "adapter:const:output:{}".format(fused_port) if sources is None else None
        mux_id = "adapter:mux:output:{}".format(fused_port)
        if zero_id is not None:
            plan_nodes[zero_id] = {"id": zero_id, "kind": "const", "label": "zero[{}]".format(width), "width": width,
                                   "attrs": {"value": "0" * width, "provenance": {"control_spec": "inactive_output"}}}
        plan_nodes[mux_id] = {"id": mux_id, "kind": "$mux", "label": "output selection {}".format(fused_port), "width": width,
                              "attrs": {"parameters": {"WIDTH": width}, "provenance": {"purpose": "explicit_output_selection", "source": source_port}}}
        if sources is None:
            active = select[control_name]
            a_src, b_src = (zero_id, source_node) if active else (source_node, zero_id)
        else:
            if active_graph_a[0].get("select_when", {}).get(control_name) == 1:
                a_src, b_src = source_node_b, source_node_a
            else:
                a_src, b_src = source_node_a, source_node_b
        for src, dst_port in ((a_src, "A"), (b_src, "B")):
            plan_edges.append({"src": src, "dst": mux_id, "src_port": "Y", "dst_port": dst_port, "width": width,
                               "inverted": False, "attrs": {"adapter": "output_selection"}})
        plan_edges.append({"src": control_node, "dst": mux_id, "src_port": control_name, "dst_port": "S", "width": 1,
                           "inverted": False, "attrs": {"adapter": "control_spec"}})
        output_labels.add(fused_port)
        outputs.append({"name": fused_port, "source_node": mux_id, "source_graph": label,
                        "source_port": source_port, "width": width, "behavior": item.get("behavior"),
                        "select_when": select, "sources": copy.deepcopy(sources) if sources is not None else None})
    if not outputs:
        raise ExecutableGraphError("control specification has no explicit outputs")
    for required in control.get("required_paths", []):
        label = required.get("graph")
        source_nodes, source_edges = (nodes_a, edges_a) if label == "a" else (nodes_b, edges_b) if label == "b" else (None, None)
        if source_nodes is None:
            raise ExecutableGraphError("required path names an unknown graph")
        starts = [node["id"] for node in source_nodes.values() if node.get("kind") == "port_in" and node.get("label") == required.get("from_port")]
        target = required.get("to_node")
        if len(starts) != 1 or target not in source_nodes:
            raise ExecutableGraphError("required path endpoint is absent")
        reachable = set(starts); changed = True
        while changed:
            changed = False
            for edge in source_edges:
                if edge["src"] in reachable and edge["dst"] not in reachable:
                    reachable.add(edge["dst"]); changed = True
        if target not in reachable:
            raise ExecutableGraphError("required declared source path is disconnected")
    referenced = {edge["src"] for edge in plan_edges} | {edge["dst"] for edge in plan_edges}
    referenced.update(item["source_node"] for item in outputs)
    plan_nodes = {node_id: node for node_id, node in plan_nodes.items() if node_id in referenced}
    order = _toposort(plan_nodes, plan_edges)
    result = {"schema": EXECUTABLE_SCHEMA, "version": 1,
              "design": control.get("generated_design", "graph_fused_{}__{}".format(graph_a["design"], graph_b["design"])),
              "level": graph_a["level"], "top": control.get("generated_top", "graph_fused_unit"),
              "source_graphs": {"a": {"design": graph_a["design"], "level": graph_a["level"]}, "b": {"design": graph_b["design"], "level": graph_b["level"]}},
              "match_provenance": {"schema": match["schema"], "matched_pair": pair, "operation": operation},
              "structural_merge_provenance": {"artifact_type": (merge.get("attrs") or {}).get("artifact_type"), "executable": False},
              "control_spec": copy.deepcopy(control), "nodes": [plan_nodes[node_id] for node_id in sorted(plan_nodes)],
              "edges": sorted(plan_edges, key=lambda e: (e["dst"], e["dst_port"], e["src"], e["src_port"])),
              "outputs": sorted(outputs, key=lambda o: o["name"]), "topological_order": order,
              "adapters": {"zero_extensions": len(additions_a) + len(additions_b), "shared_input_muxes": mux_ids,
                            "output_selection_muxes": [item["source_node"] for item in outputs]},
              "hardware_status": {"executable": True, "template_backed": False, "graph_to_rtl_status": "executable"}}
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("graph_a", type=Path); parser.add_argument("graph_b", type=Path)
    parser.add_argument("match", type=Path); parser.add_argument("merge", type=Path)
    parser.add_argument("control", type=Path); parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = realize(_load(args.graph_a), _load(args.graph_b), _load(args.match), _load(args.merge), _load(args.control))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print("wrote {} ({} nodes, {} edges)".format(args.output, len(result["nodes"]), len(result["edges"])))
    except ExecutableGraphError as exc:
        print("executable graph failed: {}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
