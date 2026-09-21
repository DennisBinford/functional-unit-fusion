import copy
import tempfile
import unittest
from pathlib import Path

from graph_backend import (GraphBackendError, NetworkXBackend, canonicalize,
                           graph_hash)
from graph_match import compare


def graph():
    return {
        "schema": "fu-graph/v1", "design": "backend_fixture", "level": "rtlil",
        "top": "backend_fixture", "attrs": {"provenance": {"source": "fixture"}},
        "nodes": [
            {"id": "z", "kind": "const", "label": "8'b0", "width": 8,
             "attrs": {"value": "00000000", "provenance": {"origin": "constant"}}},
            {"id": "a", "kind": "port_in", "label": "a_i", "width": 8,
             "attrs": {"direction": "input", "signed": False}},
            {"id": "b", "kind": "port_in", "label": "b_i", "width": 8,
             "attrs": {"direction": "input", "signed": False}},
            {"id": "n", "kind": "$sub", "label": "sub[8]", "width": 8,
             "attrs": {"parameters": {"A_WIDTH": 8, "B_WIDTH": 8,
                                         "Y_WIDTH": 8, "A_SIGNED": 0, "B_SIGNED": 0}}},
            {"id": "y", "kind": "port_out", "label": "y_o", "width": 8,
             "attrs": {"direction": "output"}},
        ],
        "edges": [
            {"src": "a", "dst": "n", "src_port": "a_i", "dst_port": "A",
             "width": 8, "inverted": False, "attrs": {"operand": 0}},
            {"src": "b", "dst": "n", "src_port": "b_i", "dst_port": "B",
             "width": 8, "inverted": True, "attrs": {"operand": 1}},
            {"src": "n", "dst": "y", "src_port": "Y", "dst_port": "y_o",
             "width": 8, "inverted": False, "attrs": {}},
            {"src": "z", "dst": "y", "src_port": "Y", "dst_port": "y_o",
             "width": 8, "inverted": False, "attrs": {"parallel": True}},
            {"src": "z", "dst": "y", "src_port": "Y", "dst_port": "y_o",
             "width": 8, "inverted": False, "attrs": {"parallel": True}},
        ],
    }


@unittest.skipUnless(__import__("importlib").util.find_spec("networkx"),
                     "optional NetworkX dependency is unavailable")
class NetworkXBackendTest(unittest.TestCase):
    def test_json_networkx_json_canonical_equality(self):
        source = graph()
        converted = NetworkXBackend.from_networkx(NetworkXBackend.to_networkx(source))
        self.assertEqual(canonicalize(source), converted)
        self.assertEqual(graph_hash(source), graph_hash(converted))

    def test_node_link_json_round_trip_and_parallel_edges(self):
        source = graph()
        network = NetworkXBackend.to_networkx(source)
        self.assertEqual(network.number_of_edges(), 5)
        converted = NetworkXBackend.from_node_link_json(NetworkXBackend.to_node_link_json(source))
        self.assertEqual(canonicalize(source), converted)

    def test_backend_load_save_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.node-link.json"
            network = NetworkXBackend.to_networkx(graph())
            NetworkXBackend.save(network, path)
            self.assertEqual(canonicalize(graph()), NetworkXBackend.load(path))

    def test_insertion_order_does_not_change_hash_or_operand_ports(self):
        source = graph(); changed = copy.deepcopy(source)
        changed["nodes"].reverse(); changed["edges"].reverse()
        self.assertEqual(graph_hash(source), graph_hash(changed))
        network = NetworkXBackend.to_networkx(source)
        edges = {(data["dst_port"], data["inverted"]) for _, _, _, data in network.edges(keys=True, data=True)}
        self.assertIn(("A", False), edges); self.assertIn(("B", True), edges)

    def test_networkx_preserves_metadata_and_rejects_lossy_graph(self):
        network = NetworkXBackend.to_networkx(graph())
        self.assertEqual(network.nodes["n"]["attrs"]["parameters"]["A_WIDTH"], 8)
        import networkx as nx
        with self.assertRaises(GraphBackendError): NetworkXBackend.from_networkx(nx.DiGraph())

    def test_selectable_networkx_matching_matches_native(self):
        left, right = graph(), copy.deepcopy(graph())
        right["design"] = "backend_fixture_b"
        native = compare(left, right, engine="native")
        converted = compare(left, right, engine="networkx")
        self.assertEqual(native["matched_nodes"], converted["matched_nodes"])
        self.assertEqual(converted["engine"], "networkx")
        self.assertEqual(converted["networkx_structural_algorithm"]["algorithm"],
                         "weisfeiler_lehman_graph_hash")


if __name__ == "__main__":
    unittest.main()
