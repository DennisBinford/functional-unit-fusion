import unittest

from graph_match import GraphMatchError, compare


def graph_template(design="x", level="rtlil", **metadata):
    graph = {
        "schema": "fu-graph/v1",
        "design": design,
        "level": level,
        "top": metadata.pop("top", design),
        "nodes": [],
        "edges": [],
    }
    if metadata:
        graph["attrs"] = metadata
    return graph


class GraphMatchTest(unittest.TestCase):
    def test_matches_unique_structural_node_without_using_ids(self):
        def graph(node_id, source_id):
            graph = graph_template(node_id, top=node_id)
            graph.update({
                "nodes": [
                    {"id": source_id, "kind": "$add", "width": 32, "attrs": {}},
                ],
                "edges": [],
            })
            return graph

        result = compare(graph("a", "cell_a"), graph("b", "cell_b"))
        self.assertEqual(result["common_node_count"], 1)
        self.assertEqual(result["matched_nodes"], [{"a": "cell_a", "b": "cell_b"}])

    def test_ambiguous_repeated_nodes_are_not_guessed(self):
        def graph(ids):
            graph = graph_template(level="gate")
            graph["nodes"] = [
                {"id": node_id, "kind": "and", "width": 1, "attrs": {}}
                for node_id in ids
            ]
            return graph

        result = compare(graph(["a1", "a2"]), graph(["b1", "b2"]))
        self.assertEqual(result["common_node_count"], 0)

    def test_enumerates_connected_common_subgraph(self):
        def graph(prefix):
            graph = graph_template(prefix)
            graph["nodes"] = [
                {"id": prefix+"1", "kind": "$add", "width": 8, "attrs": {}},
                {"id": prefix+"2", "kind": "$mul", "width": 8, "attrs": {}},
            ]
            graph["edges"] = [{"src": prefix+"1", "dst": prefix+"2", "src_port": "Y",
                           "dst_port": "A", "width": 8, "inverted": False}]
            return graph
        result = compare(graph("a"), graph("b"))
        self.assertEqual(len(result["common_subgraphs"]), 1)
        self.assertEqual(result["common_subgraphs"][0]["node_count"], 2)

    def test_normalizes_operation_aliases_and_commutative_inputs(self):
        def graph(prefix, swapped=False):
            result = graph_template(prefix)
            result["nodes"] = [
                {"id": prefix + "_a", "kind": "$and", "width": 8, "attrs": {}},
                {"id": prefix + "_b", "kind": "$or", "width": 8, "attrs": {}},
                {"id": prefix + "_add", "kind": "$_ADD_", "width": 8, "attrs": {}},
            ]
            sources = [prefix + "_a", prefix + "_b"]
            if swapped:
                sources.reverse()
            result["edges"] = [
                {"src": source, "dst": prefix + "_add", "dst_port": port, "width": 8}
                for source, port in zip(sources, ("A", "B"))
            ]
            return result

        result = compare(graph("a"), graph("b", swapped=True))
        self.assertEqual(result["common_node_count"], 3)
        self.assertIn(3, [item["node_count"] for item in result["common_subgraphs"]])

    def test_noncommutative_operand_order_remains_structural(self):
        def graph(prefix, swapped=False):
            result = graph_template(prefix)
            result["nodes"] = [
                {"id": prefix + "_a", "kind": "$and", "width": 8, "attrs": {}},
                {"id": prefix + "_b", "kind": "$or", "width": 8, "attrs": {}},
                {"id": prefix + "_sub", "kind": "$sub", "width": 8, "attrs": {}},
            ]
            sources = [prefix + "_a", prefix + "_b"]
            if swapped:
                sources.reverse()
            result["edges"] = [
                {"src": source, "dst": prefix + "_sub", "dst_port": port, "width": 8}
                for source, port in zip(sources, ("A", "B"))
            ]
            return result

        result = compare(graph("a"), graph("b", swapped=True))
        self.assertEqual(result["common_node_count"], 2)

    def test_input_and_output_widths_are_structural(self):
        def graph(prefix, input_width=8, output_width=8, edge_width=8):
            result = graph_template(prefix)
            result["nodes"] = [
                {"id": prefix + "_in", "kind": "port_in", "width": input_width, "attrs": {}},
                {"id": prefix + "_add", "kind": "$add", "width": output_width,
                 "attrs": {"parameters": {"A_WIDTH": input_width, "B_WIDTH": input_width,
                                             "Y_WIDTH": output_width}}},
            ]
            result["edges"] = [{"src": prefix + "_in", "dst": prefix + "_add",
                                "dst_port": "A", "width": edge_width}]
            return result

        self.assertEqual(compare(graph("a"), graph("b"))["common_node_count"], 2)
        self.assertEqual(compare(graph("a"), graph("b", output_width=16))["common_node_count"], 1)
        self.assertEqual(compare(graph("a"), graph("b", edge_width=16))["common_node_count"], 1)

    def test_rejects_schema_and_level_mismatches_and_preserves_metadata(self):
        left = graph_template("left", top="left_top", sources=["rtl/left.sv"],
                              tool="yosys", tool_version="0.67")
        right = graph_template("right", top="right_top", sources=["rtl/right.sv"],
                               tool="yosys", tool_version="0.67")
        result = compare(left, right)
        self.assertEqual(result["metadata"]["a"]["top"], "left_top")
        self.assertEqual(result["metadata"]["a"]["sources"], ["rtl/left.sv"])
        self.assertEqual(result["metadata"]["b"]["tool_version"], "0.67")

        bad_schema = dict(right, schema="other/v1")
        with self.assertRaises(GraphMatchError):
            compare(left, bad_schema)
        bad_level = dict(right, level="gate")
        with self.assertRaises(GraphMatchError):
            compare(left, bad_level)


if __name__ == "__main__":
    unittest.main()
