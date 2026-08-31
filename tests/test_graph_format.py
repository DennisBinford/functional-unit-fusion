import unittest

from graph_extract import Edge, FUGraph, Node, GRAPH_SCHEMA, normalize_operation


class GraphFormatTest(unittest.TestCase):
    def test_schema_and_id_independent_node_signature(self):
        graph = FUGraph(
            design="demo",
            level="rtlil",
            top="demo",
            nodes=[Node(id="cell42", kind="$add", label="add[32]", width=32)],
            edges=[Edge(src="a", dst="cell42", dst_port="A", width=32)],
        )
        data = graph.to_dict()
        self.assertEqual(data["schema"], GRAPH_SCHEMA)
        self.assertEqual(graph.node_signature("cell42"), (
            "rtlil", "add", "$add", 32, None, ()
        ))

    def test_operation_aliases(self):
        self.assertEqual(normalize_operation("$pmux"), "mux")
        self.assertEqual(normalize_operation("$_XOR_"), "xor")
        self.assertEqual(normalize_operation("$lt"), "compare")


if __name__ == "__main__":
    unittest.main()
