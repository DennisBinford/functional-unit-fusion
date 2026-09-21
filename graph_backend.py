#!/usr/bin/env python3
"""Backend-neutral graph interchange for the research FU representation.

``fu-graph/v1`` is canonical.  NetworkX is an optional analysis backend; it
must never become the source of semantic identity or ordering.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from graph_extract import GRAPH_SCHEMA


BACKEND_SCHEMA = "fu-graph-backend/v1"


class GraphBackendError(ValueError):
    pass


def canonicalize(graph: Dict[str, Any]) -> Dict[str, Any]:
    validate(graph)
    nodes = sorted((dict(node) for node in graph["nodes"]), key=lambda n: n["id"])
    edges = sorted((dict(edge) for edge in graph["edges"]), key=lambda e: (
        e["src"], e["dst"], e.get("src_port", ""), e.get("dst_port", ""),
        e.get("width", 1), bool(e.get("inverted", False)),
        json.dumps(e.get("attrs", {}), sort_keys=True, separators=(",", ":"))))
    kinds = {}
    for node in nodes:
        kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
    return {"schema": GRAPH_SCHEMA, "design": graph["design"], "level": graph["level"],
            "top": graph["top"], "attrs": graph.get("attrs", {}),
            "stats": {"nodes": len(nodes), "edges": len(edges),
                      "operators": sum(value for key, value in kinds.items()
                                        if key not in ("port_in", "port_out", "const")),
                      "kinds": dict(sorted(kinds.items(), key=lambda item: (-item[1], item[0])))},
            "nodes": nodes, "edges": edges}


def validate(graph: Dict[str, Any]) -> None:
    if not isinstance(graph, dict) or graph.get("schema") != GRAPH_SCHEMA:
        raise GraphBackendError("expected fu-graph/v1 object")
    for field in ("design", "level", "top"):
        if not isinstance(graph.get(field), str) or not graph[field]:
            raise GraphBackendError("graph is missing {}".format(field))
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        raise GraphBackendError("graph nodes must be a list")
    ids = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or node["id"] in ids:
            raise GraphBackendError("graph has an invalid or duplicate node")
        ids.add(node["id"])
        if not isinstance(node.get("kind"), str):
            raise GraphBackendError("node {} lacks kind".format(node["id"]))
    if not isinstance(graph.get("edges"), list):
        raise GraphBackendError("graph edges must be a list")
    for edge in graph["edges"]:
        if not isinstance(edge, dict) or edge.get("src") not in ids or edge.get("dst") not in ids:
            raise GraphBackendError("edge references a missing node")
        if not isinstance(edge.get("src_port"), str) or not isinstance(edge.get("dst_port"), str):
            raise GraphBackendError("edge port metadata is missing")
        if not isinstance(edge.get("width", 1), int) or edge.get("width", 1) < 1:
            raise GraphBackendError("edge width is invalid")


def deterministic_nodes(graph: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    return iter(canonicalize(graph)["nodes"])


def deterministic_edges(graph: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    return iter(canonicalize(graph)["edges"])


def graph_hash(graph: Dict[str, Any]) -> str:
    payload = json.dumps(canonicalize(graph), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class FuGraphJSONBackend:
    name = "fu-graph-json"

    @staticmethod
    def validate(graph):
        validate(graph)

    @staticmethod
    def load(path: Path) -> Dict[str, Any]:
        graph = json.loads(Path(path).read_text())
        validate(graph)
        return graph

    @staticmethod
    def save(graph: Dict[str, Any], path: Path) -> Path:
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(canonicalize(graph), indent=2, sort_keys=True) + "\n")
        return path

    @staticmethod
    def canonicalize(graph):
        return canonicalize(graph)

    @staticmethod
    def to_canonical_fu_graph(graph):
        return canonicalize(graph)

    @staticmethod
    def from_canonical_fu_graph(graph):
        return canonicalize(graph)

    @staticmethod
    def capabilities():
        return {"load": True, "save": True, "canonical": True, "provenance": True}


class NetworkXBackend:
    name = "networkx"

    @staticmethod
    def _nx():
        try:
            import networkx as nx
        except ImportError as exc:
            raise GraphBackendError("NetworkX backend is optional and unavailable") from exc
        return nx

    @staticmethod
    def validate(graph):
        validate(graph)

    @staticmethod
    def load(path: Path):
        """Load the lossless node-link JSON form produced by this backend."""
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError) as exc:
            raise GraphBackendError("cannot read NetworkX node-link JSON {}: {}".format(path, exc))
        return NetworkXBackend.from_node_link_json(data)

    @staticmethod
    def save(graph, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        nx = NetworkXBackend._nx()
        if isinstance(graph, nx.MultiDiGraph):
            data = nx.node_link_data(graph)
        else:
            data = NetworkXBackend.to_node_link_json(graph)
        path.write_text(json.dumps(data,
                                   indent=2, sort_keys=True) + "\n")
        return path

    @staticmethod
    def to_networkx(graph):
        nx = NetworkXBackend._nx(); canonical = canonicalize(graph)
        result = nx.MultiDiGraph()
        result.graph.update({"fu_graph": {key: canonical[key] for key in ("schema", "design", "level", "top", "attrs")}})
        for node in canonical["nodes"]:
            result.add_node(node["id"], **{key: value for key, value in node.items() if key != "id"})
        occurrences = {}
        for edge in canonical["edges"]:
            identity = json.dumps(edge, sort_keys=True, separators=(",", ":"))
            occurrence = occurrences.get(identity, 0); occurrences[identity] = occurrence + 1
            key = "e_" + hashlib.sha256((identity + ":{}".format(occurrence)).encode()).hexdigest()[:16]
            attrs = dict(edge.get("attrs", {})); attrs.update({key: edge.get(key) for key in ("src_port", "dst_port", "width", "inverted")})
            result.add_edge(edge["src"], edge["dst"], key=key, **attrs)
        return result

    @staticmethod
    def from_networkx(graph) -> Dict[str, Any]:
        nx = NetworkXBackend._nx()
        if not isinstance(graph, nx.MultiDiGraph):
            raise GraphBackendError("NetworkX conversion requires MultiDiGraph to preserve parallel edges")
        metadata = graph.graph.get("fu_graph")
        if not isinstance(metadata, dict):
            raise GraphBackendError("NetworkX graph lacks fu-graph metadata")
        nodes = []
        for node_id, attrs in graph.nodes(data=True):
            node = dict(attrs); node["id"] = node_id; nodes.append(node)
        edges = []
        for src, dst, _key, attrs in graph.edges(keys=True, data=True):
            attrs = dict(attrs)
            edge = {"src": src, "dst": dst}
            for key in ("src_port", "dst_port", "width", "inverted"):
                if key not in attrs:
                    raise GraphBackendError("NetworkX edge lacks {} metadata".format(key))
                edge[key] = attrs.pop(key)
            edge["attrs"] = attrs
            edges.append(edge)
        result = {**metadata, "nodes": nodes, "edges": edges}
        return canonicalize(result)

    @staticmethod
    def to_node_link_json(graph) -> Dict[str, Any]:
        nx = NetworkXBackend._nx()
        return nx.node_link_data(NetworkXBackend.to_networkx(graph))

    @staticmethod
    def from_node_link_json(data: Dict[str, Any]) -> Dict[str, Any]:
        nx = NetworkXBackend._nx()
        try:
            graph = nx.node_link_graph(data, directed=True, multigraph=True)
        except Exception as exc:
            raise GraphBackendError("malformed NetworkX node-link JSON: {}".format(exc))
        return NetworkXBackend.from_networkx(graph)

    @staticmethod
    def to_canonical_fu_graph(graph):
        return NetworkXBackend.from_networkx(graph)

    @staticmethod
    def from_canonical_fu_graph(graph):
        return NetworkXBackend.to_networkx(graph)

    @staticmethod
    def canonicalize(graph):
        return NetworkXBackend.from_networkx(graph)

    @staticmethod
    def capabilities():
        return {"multidigraph": True, "parallel_edges": True, "ports": True,
                "provenance": True, "node_link_json": True, "graphml": False}


def backend_capabilities() -> Dict[str, Any]:
    result = {"fu-graph-json": FuGraphJSONBackend.capabilities()}
    try:
        result["networkx"] = NetworkXBackend.capabilities()
        NetworkXBackend._nx()
        result["networkx"]["available"] = True
    except GraphBackendError:
        result["networkx"] = {"available": False, "optional": True}
    return result
