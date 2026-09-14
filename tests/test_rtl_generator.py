import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from graph_match import compare
from graph_merge import merge_graphs
from rtl_generator import RTLGenerationError, emit


def _evidence_graph(design, width=32, signed=False):
    """Small deterministic graph fixture with a real named interface."""
    def node(node_id, kind, label, node_width, attrs=None):
        return {"id": node_id, "kind": kind, "label": label,
                "width": node_width, "attrs": attrs or {}}

    return {
        "schema": "fu-graph/v1", "design": design, "level": "rtlil",
        "top": design,
        "nodes": [
            node("a", "port_in", "a_i", width, {"direction": "input"}),
            node("b", "port_in", "b_i", width, {"direction": "input"}),
            node("add_i", "port_in", "add_i", 1, {"direction": "input"}),
            node("result", "port_out", "result_o", width * 2,
                 {"direction": "output"}),
            node("mul", "$mul", "mul", width * 2, {
                "signed": signed,
                "parameters": {"A_WIDTH": width, "B_WIDTH": width,
                                "Y_WIDTH": width * 2},
            }),
            node("add", "$add", "add", width, {
                "signed": signed,
                "parameters": {"A_WIDTH": width, "B_WIDTH": width,
                                "Y_WIDTH": width},
            }),
            node("mux", "$mux", "mux", width * 2, {
                "signed": signed, "parameters": {"WIDTH": width * 2},
            }),
        ],
        "edges": [
            {"src": "a", "dst": "mul", "src_port": "a_i", "dst_port": "A",
             "width": width, "inverted": False},
            {"src": "b", "dst": "mul", "src_port": "b_i", "dst_port": "B",
             "width": width, "inverted": False},
            {"src": "a", "dst": "add", "src_port": "a_i", "dst_port": "A",
             "width": width, "inverted": False},
            {"src": "b", "dst": "add", "src_port": "b_i", "dst_port": "B",
             "width": width, "inverted": False},
            {"src": "mul", "dst": "mux", "src_port": "Y", "dst_port": "A",
             "width": width * 2, "inverted": False},
            {"src": "add", "dst": "mux", "src_port": "Y", "dst_port": "B",
             "width": width, "inverted": False},
            {"src": "add_i", "dst": "mux", "src_port": "add_i", "dst_port": "S",
             "width": 1, "inverted": False},
            {"src": "mux", "dst": "result", "src_port": "Y", "dst_port": "result_o",
             "width": width * 2, "inverted": False},
        ],
    }


class RTLGeneratorTest(unittest.TestCase):
    def _fixture(self, directory, width=32, signed=False):
        graph_a = _evidence_graph("graph_a", width, signed)
        graph_b = _evidence_graph("graph_b", width, signed)
        graph_a_path, graph_b_path = directory / "a.json", directory / "b.json"
        graph_a_path.write_text(json.dumps(graph_a, sort_keys=True))
        graph_b_path.write_text(json.dumps(graph_b, sort_keys=True))
        match = compare(graph_a, graph_b)
        match_path = directory / "match.json"
        match_path.write_text(json.dumps(match, sort_keys=True))
        merge_path = directory / "merge.json"
        merge_path.write_text(json.dumps(merge_graphs(graph_a, graph_b, match).to_dict(),
                                        sort_keys=True))
        return graph_a, graph_b, match, graph_a_path, graph_b_path, match_path, merge_path

    def test_valid_evidence_emits_deterministic_compilable_rtl_and_70_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            _, _, _, graph_a, graph_b, match, merge = self._fixture(directory)
            output = directory / "generated_fused_add_mul_mac.sv"
            manifest = emit(output, json.loads(graph_a.read_text()),
                            json.loads(graph_b.read_text()), json.loads(match.read_text()),
                            json.loads(merge.read_text()),
                            {"graph_a": graph_a, "graph_b": graph_b,
                             "match": match, "merge": merge})
            first = output.read_text()
            second = emit(output, json.loads(graph_a.read_text()),
                          json.loads(graph_b.read_text()), json.loads(match.read_text()),
                          json.loads(merge.read_text()),
                          {"graph_a": graph_a, "graph_b": graph_b,
                           "match": match, "merge": merge})
            self.assertEqual(first, output.read_text())
            self.assertEqual(manifest["generated_rtl_sha256"],
                             second["generated_rtl_sha256"])
            self.assertIn("logic [63:0]", first)

            simulation = directory / "simulation"
            command = [sys.executable, "verilator_flow.py", "run",
                       "--rtl", output,
                       "--rtl", Path("rtl/fma_experiment/interface_separate_add_mul_mac.sv"),
                       "--testbench", Path("tb/tb_interface_level_compare.sv"),
                       "--design-top", "generated_fused_add_mul_mac",
                       "--testbench-top", "tb_interface_level_compare",
                       "--output", simulation, "--jobs", "1", "--seed", "1"]
            result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            log = (simulation / "simulation.log").read_text()
            self.assertIn("INTERFACE_LEVEL_TEST_PASS checks=70", log)

    def test_unrelated_swapped_and_tampered_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            graph_a, graph_b, match, graph_a_path, graph_b_path, match_path, merge_path = self._fixture(directory)
            output = directory / "generated.sv"
            evidence = {"graph_a": graph_a_path, "graph_b": graph_b_path,
                        "match": match_path, "merge": merge_path}
            unrelated = _evidence_graph("unrelated")
            unrelated_path = directory / "unrelated.json"
            unrelated_path.write_text(json.dumps(unrelated))
            unrelated_match = compare(graph_b, unrelated)
            unrelated_match_path = directory / "unrelated_match.json"
            unrelated_match_path.write_text(json.dumps(unrelated_match))
            with self.assertRaises(RTLGenerationError):
                emit(output, graph_a, graph_b, unrelated_match,
                     json.loads(merge_path.read_text()), evidence)

            tampered = json.loads(match_path.read_text())
            tampered["matched_nodes"][0]["a"] = "tampered-node"
            with self.assertRaises(RTLGenerationError):
                emit(output, graph_a, graph_b, tampered,
                     json.loads(merge_path.read_text()), evidence)

            removed = json.loads(match_path.read_text())
            removed["matched_nodes"] = [item for item in removed["matched_nodes"]
                                         if item["a"] != "mul"]
            with self.assertRaises(RTLGenerationError):
                emit(output, graph_a, graph_b, removed,
                     json.loads(merge_path.read_text()), evidence)

            swapped_merge = json.loads(merge_path.read_text())
            swapped_merge["attrs"]["source_graphs"]["a"]["design"] = "unrelated"
            with self.assertRaises(RTLGenerationError):
                emit(output, graph_a, graph_b, match, swapped_merge, evidence)

    def test_signed_and_unsupported_width_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for width, signed in ((16, False), (32, True)):
                graph_a, graph_b, match, graph_a_path, graph_b_path, match_path, merge_path = self._fixture(
                    directory, width, signed)
                with self.assertRaises(RTLGenerationError):
                    emit(directory / "generated.sv", graph_a, graph_b, match,
                         json.loads(merge_path.read_text()),
                         {"graph_a": graph_a_path, "graph_b": graph_b_path,
                          "match": match_path, "merge": merge_path})


if __name__ == "__main__":
    unittest.main()
