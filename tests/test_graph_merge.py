import unittest

from graph_match import compare
from graph_merge import GraphMergeError, merge_graphs


def graph(design, unique_kind):
    return {
        "schema": "fu-graph/v1",
        "design": design,
        "level": "rtlil",
        "top": design,
        "attrs": {"sources": ["rtl/{}.sv".format(design)]},
        "nodes": [
            {"id": "{}_shared".format(design), "kind": "$mul",
             "label": "mul[8]", "width": 8, "attrs": {}},
            {"id": "{}_unique".format(design), "kind": unique_kind,
             "label": unique_kind, "width": 8, "attrs": {}},
        ],
        "edges": [
            {"src": "{}_shared".format(design),
             "dst": "{}_unique".format(design),
             "src_port": "Y", "dst_port": "A", "width": 8,
             "inverted": False},
        ],
    }


class GraphMergeTest(unittest.TestCase):
    def test_coalesces_exact_matches_and_preserves_provenance(self):
        left = graph("left", "$add")
        right = graph("right", "$sub")
        match = compare(left, right)
        merged = merge_graphs(left, right, match)

        self.assertEqual(merged.attrs["artifact_type"], "structural_merged_candidate")
        self.assertFalse(merged.attrs["hardware_status"]["executable"])
        self.assertEqual(merged.attrs["node_accounting"], {
            "graph_a": 2,
            "graph_b": 2,
            "coalesced": 1,
            "merged_candidate": 3,
        })
        shared = [
            node for node in merged.nodes
            if node.attrs["merge"]["shared_candidate"]
        ]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0].attrs["merge"]["membership"], ["a", "b"])
        self.assertEqual(len(merged.edges), 2)

    def test_deduplicates_an_edge_present_in_both_graphs(self):
        left = graph("left", "$add")
        right = graph("right", "$add")
        match = compare(left, right)
        merged = merge_graphs(left, right, match)
        self.assertEqual(len(merged.nodes), 2)
        self.assertEqual(len(merged.edges), 1)
        self.assertEqual(
            merged.edges[0].attrs["merge"]["membership"], ["a", "b"]
        )

    def test_rejects_a_match_for_different_inputs(self):
        left = graph("left", "$add")
        right = graph("right", "$sub")
        match = compare(left, right)
        match["graph_b"] = "not-right"
        with self.assertRaises(GraphMergeError):
            merge_graphs(left, right, match)

    def test_rejects_nonsemantic_manual_pair(self):
        left = graph("left", "$add")
        right = graph("right", "$sub")
        match = compare(left, right)
        match["matched_nodes"].append({
            "a": "left_unique", "b": "right_unique"
        })
        with self.assertRaises(GraphMergeError):
            merge_graphs(left, right, match)


if __name__ == "__main__":
    unittest.main()
