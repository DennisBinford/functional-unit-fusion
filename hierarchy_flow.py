#!/usr/bin/env python3
"""Conservative module -> RTLIL -> gate graph analysis.

The hierarchy is an analysis policy, not a claim that every lower-level match
is executable.  A whole-module opportunity wins when the top module nodes and
their hierarchy skeleton match.  Otherwise the word-level RTLIL graph is the
first actionable fallback.  Mapped-gate commonality is considered only when
the higher levels expose no operator candidate; it remains structural.
"""

import time
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from graph_match import compare


HIERARCHY_SCHEMA = "fu-hierarchy-study/v1"
LEVELS = ("module", "rtlil", "gate")
BOUNDARY_KINDS = frozenset(("port_in", "port_out", "const", "boundary"))


def graph_size(graph: Mapping[str, Any]) -> Dict[str, Any]:
    stats = graph.get("stats") or {}
    kinds = dict(stats.get("kinds") or {})
    operators = sum(count for kind, count in kinds.items()
                    if kind not in BOUNDARY_KINDS)
    return {
        "nodes": int(stats.get("nodes", len(graph.get("nodes", [])))),
        "edges": int(stats.get("edges", len(graph.get("edges", [])))),
        "operators": int(stats.get("operators", operators)),
        "kinds": kinds,
    }


def _node_map(graph: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {node["id"]: node for node in graph.get("nodes", [])}


def _operator_match_count(match: Mapping[str, Any], graph_a: Mapping[str, Any],
                          graph_b: Mapping[str, Any]) -> int:
    nodes_a, nodes_b = _node_map(graph_a), _node_map(graph_b)
    return sum(
        1 for item in match.get("matched_nodes", [])
        if nodes_a.get(item.get("a"), {}).get("kind") not in BOUNDARY_KINDS
        and nodes_b.get(item.get("b"), {}).get("kind") not in BOUNDARY_KINDS
    )


def _largest_connected(match: Mapping[str, Any]) -> int:
    return max((int(item.get("node_count", 0))
                for item in match.get("common_subgraphs", [])), default=0)


def _top_matched(match: Mapping[str, Any], graph_a: Mapping[str, Any],
                 graph_b: Mapping[str, Any]) -> bool:
    return any(item.get("a") == graph_a.get("top") and
               item.get("b") == graph_b.get("top")
               for item in match.get("matched_nodes", []))


def _module_useful(match: Mapping[str, Any], graph_a: Mapping[str, Any],
                   graph_b: Mapping[str, Any]) -> bool:
    # A leaf source module has no hierarchy evidence; its module node is only
    # an opaque wrapper and must fall through to RTLIL.
    return (_top_matched(match, graph_a, graph_b)
            and bool(graph_a.get("edges")) and bool(graph_b.get("edges")))


def _level_useful(level: str, match: Mapping[str, Any],
                  graph_a: Mapping[str, Any], graph_b: Mapping[str, Any]) -> bool:
    if level == "module":
        return _module_useful(match, graph_a, graph_b)
    return _operator_match_count(match, graph_a, graph_b) > 0


def _metric(level: str, graph_a: Mapping[str, Any], graph_b: Mapping[str, Any],
            match: Mapping[str, Any], runtime_seconds: float,
            useful: bool) -> Dict[str, Any]:
    return {
        "level": level,
        "match_size": int(match.get("common_node_count", 0)),
        "operator_match_size": _operator_match_count(match, graph_a, graph_b),
        "connected_subgraph_size": _largest_connected(match),
        "coverage": match.get("coverage", {"a": 0.0, "b": 0.0}),
        "runtime_seconds": runtime_seconds,
        "graph_size": {"a": graph_size(graph_a), "b": graph_size(graph_b)},
        "useful_candidate": bool(useful),
        "match_mode": match.get("match_mode"),
        "engine": match.get("engine", "native"),
    }


def analyze_pair(
    graph_sets_a: Mapping[str, Mapping[str, Any]],
    graph_sets_b: Mapping[str, Mapping[str, Any]],
    *,
    name: str,
    evaluate_all: bool = False,
    engine: str = "native",
) -> Dict[str, Any]:
    """Compare one pair under the explicit fallthrough policy.

    ``evaluate_all`` is useful for a vertical report that wants gate/AIG
    observations beside the selected level.  Selection still obeys the same
    module-first policy and never treats gate counts as executable evidence.
    """
    metrics: Dict[str, Dict[str, Any]] = {}
    matches: Dict[str, Dict[str, Any]] = {}
    selected: Optional[str] = None
    fallthrough = []
    for level in LEVELS:
        if level not in graph_sets_a or level not in graph_sets_b:
            fallthrough.append({"from": level, "reason": "graph level unavailable"})
            continue
        started = time.perf_counter()
        match = compare(graph_sets_a[level], graph_sets_b[level], engine=engine)
        runtime = time.perf_counter() - started
        useful = _level_useful(level, match, graph_sets_a[level], graph_sets_b[level])
        metrics[level] = _metric(level, graph_sets_a[level], graph_sets_b[level],
                                 match, runtime, useful)
        matches[level] = match
        if selected is None and useful:
            selected = level
        if selected is not None and not evaluate_all:
            break
        if not useful:
            next_level = {"module": "rtlil", "rtlil": "gate", "gate": None}[level]
            if next_level:
                fallthrough.append({"from": level, "to": next_level,
                                    "reason": "no useful candidate"})
    return {
        "schema": HIERARCHY_SCHEMA,
        "pair": name,
        "policy": {
            "order": list(LEVELS),
            "whole_module_requires_top_match": True,
            "leaf_module_falls_through": True,
            "rtlil_is_first_word_level": True,
            "gate_is_structural_only": True,
            "evaluate_all_for_report": bool(evaluate_all),
        },
        "selected_level": selected,
        "metrics": metrics,
        "matches": matches,
        "fallthrough": fallthrough,
    }
