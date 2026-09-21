import tempfile
import unittest
from pathlib import Path

import graph_extract
from graph_match import compare
from hierarchy_flow import analyze_pair


def _extract_pair(directory, left, right, top_left, top_right):
    left_path = Path(directory) / "left.sv"
    right_path = Path(directory) / "right.sv"
    left_path.write_text(left)
    right_path.write_text(right)
    levels_a = graph_extract.extract_all("left", [str(left_path)], top_left,
                                         workdir=Path(directory) / "yosys_a",
                                         levels=("module", "rtlil", "gate"))
    levels_b = graph_extract.extract_all("right", [str(right_path)], top_right,
                                         workdir=Path(directory) / "yosys_b",
                                         levels=("module", "rtlil", "gate"))
    return {level: graph.to_dict() for level, graph in levels_a.items()}, \
        {level: graph.to_dict() for level, graph in levels_b.items()}


class HierarchyFlowTest(unittest.TestCase):
    def test_parameterized_hierarchy_ignores_child_name_but_keeps_specialization(self):
        from graph_extract import _graph_from_hierarchy

        def hierarchy(child_name):
            specialized = "$paramod\\{}\\MODE=1".format(child_name)
            return {"modules": {
                "top": {"ports": {}, "cells": {
                    "u_child": {"type": specialized, "parameters": {}}
                }},
                specialized: {"ports": {}, "cells": {}}
            }}

        left = _graph_from_hierarchy(hierarchy("child_a"), "left", "top")
        right = _graph_from_hierarchy(hierarchy("child_b"), "right", "top")
        match = compare(left.to_dict(), right.to_dict())
        self.assertEqual(match["common_node_count"], 2)

    def test_renamed_equivalent_hierarchy_is_not_rejected_by_names(self):
        with tempfile.TemporaryDirectory() as directory:
            left = """
module child_a(input logic [7:0] a_i, b_i, output logic [7:0] y_o);
  assign y_o = a_i + b_i;
endmodule
module top_a(input logic [7:0] a_i, b_i, output logic [7:0] y_o);
  child_a u_child(.a_i(a_i), .b_i(b_i), .y_o(y_o));
endmodule
"""
            right = left.replace("child_a", "child_b").replace("top_a", "top_b")
            levels_a, levels_b = _extract_pair(directory, left, right, "top_a", "top_b")
            module_match = compare(levels_a["module"], levels_b["module"])
            result = analyze_pair(levels_a, levels_b, name="renamed", evaluate_all=False)
            self.assertEqual(module_match["common_node_count"], 2)
            self.assertEqual(result["selected_level"], "module")

    def test_different_rtl_writing_matches_at_word_level(self):
        with tempfile.TemporaryDirectory() as directory:
            left = """
module left(input logic [7:0] a_i, b_i, output logic [7:0] y_o);
  assign y_o = a_i + b_i;
endmodule
"""
            right = """
module right(input logic [7:0] a_i, b_i, output logic [7:0] y_o);
  always_comb begin y_o = b_i + a_i; end
endmodule
"""
            levels_a, levels_b = _extract_pair(directory, left, right, "left", "right")
            result = analyze_pair(levels_a, levels_b, name="rewritten", evaluate_all=False)
            self.assertEqual(result["selected_level"], "rtlil")
            self.assertGreater(result["metrics"]["rtlil"]["operator_match_size"], 0)
            self.assertNotIn("gate", result["metrics"])

    def test_partial_sharing_falls_through_from_module_to_rtlil(self):
        with tempfile.TemporaryDirectory() as directory:
            left = """
module left(input logic [7:0] a_i, b_i, output logic [7:0] y_o);
  assign y_o = a_i + b_i;
endmodule
"""
            right = """
module right(input logic [7:0] a_i, b_i, c_i, output logic [7:0] y_o);
  assign y_o = (a_i + b_i) ^ c_i;
endmodule
"""
            levels_a, levels_b = _extract_pair(directory, left, right, "left", "right")
            result = analyze_pair(levels_a, levels_b, name="partial", evaluate_all=True)
            self.assertEqual(result["selected_level"], "rtlil")
            self.assertFalse(result["metrics"]["module"]["useful_candidate"])
            self.assertGreater(result["metrics"]["rtlil"]["operator_match_size"], 0)
            self.assertIn("gate", result["metrics"])

    def test_true_nonmatch_is_not_accepted_from_boundary_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            left = """
module left(input logic [7:0] a_i, b_i, output logic [7:0] y_o);
  assign y_o = a_i + b_i;
endmodule
"""
            right = """
module right(input logic [7:0] a_i, b_i, output logic [7:0] y_o);
  assign y_o = a_i * b_i;
endmodule
"""
            levels_a, levels_b = _extract_pair(directory, left, right, "left", "right")
            result = analyze_pair(levels_a, levels_b, name="nonmatch", evaluate_all=False)
            self.assertIsNone(result["selected_level"])
            self.assertEqual(result["metrics"]["rtlil"]["operator_match_size"], 0)


if __name__ == "__main__":
    unittest.main()
