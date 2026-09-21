import json
import tempfile
import unittest
from pathlib import Path

from graph_rtl_emit import emit
import graph_viz
from graph_extract import FUGraph
from ibex_fusion_manifest import IbexManifestError, validate_manifest
from scripts.ibex_module_preserving_emit import ContractEmitError, emit as emit_module_preserving


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "rtl" / "ibex_fusion" / "ibex_alu_operation_wrappers.sv"


def _plan(kind, width=4):
    result_width = 2 if kind == "slice" else 2 * width if kind == "concat" else width
    nodes = [
        {"id": "x", "kind": "port_in", "label": "x_i", "width": width,
         "attrs": {"direction": "input"}},
        {"id": "y", "kind": "port_in", "label": "y_i", "width": width,
         "attrs": {"direction": "input"}},
        {"id": "out", "kind": "port_out", "label": "result_o", "width": result_width,
         "attrs": {"direction": "output"}},
        {"id": "op", "kind": kind, "label": kind, "width": width,
         "attrs": {"parameters": {"WIDTH": width}, "provenance": {"test": True}}},
    ]
    if kind == "slice":
        nodes[-1]["width"] = result_width
        nodes[-1]["attrs"]["source_lsb"] = 1
        edge = {"src": "x", "dst": "op", "src_port": "x_i", "dst_port": "A", "width": width,
                "inverted": False}
    elif kind == "concat":
        nodes[-1]["width"] = result_width
        nodes[-1]["attrs"]["target_width"] = result_width
        edge = {"src": "x", "dst": "op", "src_port": "x_i", "dst_port": "SEG_0", "width": width,
                "inverted": False, "attrs": {"bit_positions": [0, 1, 2, 3],
                                                 "source_bit_positions": [0, 1, 2, 3]}}
        edge2 = {"src": "y", "dst": "op", "src_port": "y_i", "dst_port": "SEG_4", "width": width,
                 "inverted": False, "attrs": {"bit_positions": [4, 5, 6, 7],
                                                  "source_bit_positions": [0, 1, 2, 3]}}
    else:
        edge = {"src": "x", "dst": "op", "src_port": "x_i", "dst_port": "A", "width": width,
                "inverted": False}
    edges = [edge]
    if kind in ("$xor",):
        edges.append({"src": "y", "dst": "op", "src_port": "y_i", "dst_port": "B", "width": width,
                      "inverted": False})
    if kind == "concat":
        edges.append(edge2)
    edges.append({"src": "op", "dst": "out", "src_port": "Y", "dst_port": "result_o",
                  "width": result_width, "inverted": False})
    return {"schema": "fu-executable-graph/v1", "version": 1, "design": "cell_test",
            "level": "rtlil", "top": "cell_test", "nodes": nodes, "edges": edges,
            "outputs": [{"name": "result_o", "source_node": "out", "width": result_width}],
            "topological_order": ["x", "y", "op", "out"],
            "hardware_status": {"executable": True, "template_backed": False}}


class IbexFusionTest(unittest.TestCase):
    def test_sub_ltu_interface_contract_cannot_regress_to_same_input_description(self):
        sprint = (ROOT / "scripts" / "professor_sprint_2026_09_21.py").read_text()
        self.assertNotIn("same-operands ALU-operation sharing", sprint)
        self.assertNotIn("same-input design", sprint)
        contract_path = ROOT / "meeting-artifacts" / "2026-09-21" / "sub_ltu" / "interface_contract.json"
        if contract_path.exists():
            contract = json.loads(contract_path.read_text())
            self.assertEqual(contract["number_of_independent_operand_pairs"], 2)
            self.assertEqual(contract["number_of_externally_visible_results"], 1)
            self.assertFalse(contract["simultaneous_use_preserved"])
            self.assertEqual(contract["selection_model"], "client_selection")
            self.assertTrue(contract["client_selection_modeled"])
            self.assertFalse(contract["operation_selection_modeled"])
            self.assertIn("independent-input", contract["classification"])

    def test_all_operation_wrappers_instantiate_upstream_without_fusion_control(self):
        text = WRAPPER.read_text()
        for operation in ("add", "sub", "lt", "ltu", "ge", "geu", "eq", "ne",
                          "xor", "or", "and", "sll", "srl", "sra"):
            self.assertIn("module ibex_alu_{}32".format(operation), text)
        self.assertEqual(text.count("u(.operand_a_i"), 14)
        self.assertEqual(text.count("ibex_alu #"), 1)
        self.assertNotIn("source_select_i", text)

    def test_generic_emitter_supports_new_rtlil_subset_cells(self):
        self.assertIn("~", emit(_plan("$not")))
        self.assertIn("^", emit(_plan("$xor")))
        self.assertIn("x_i", emit(_plan("slice")))
        self.assertIn("{y_i, x_i}", emit(_plan("concat", 4)))

    def test_ibex_manifest_rejects_mutation_and_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sv"
            source.write_text("module source; endmodule\n")
            import hashlib
            record = {"path": "source.sv", "external": False,
                      "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema": "fu-ibex-fusion-manifest/v1", "version": 1,
                                             "template_backed": False, "graph_to_rtl_status": "executable",
                                             "evidence": {"source": record}, "external_dependencies": {}}, indent=2))
            self.assertTrue(validate_manifest(manifest, root)["valid"])
            source.write_text("module changed; endmodule\n")
            with self.assertRaises(IbexManifestError):
                validate_manifest(manifest, root)
            source.unlink()
            with self.assertRaises(IbexManifestError):
                validate_manifest(manifest, root)

    def test_generated_ibex_manifest_and_plan_are_graph_emitted_when_artifacts_exist(self):
        manifest = ROOT / "meeting-artifacts" / "ibex-fusion" / "generated_rtl.manifest.json"
        if not manifest.exists():
            self.skipTest("Ibex demo artifacts have not been generated")
        result = validate_manifest(manifest, ROOT)
        self.assertTrue(result["valid"])
        self.assertEqual(json.loads(manifest.read_text())["template_backed"], False)

    def test_module_preserving_contract_is_validated_and_controls_emission(self):
        contract_path = ROOT / "meeting-artifacts" / "ibex-fusion" / "module-preserving" / "sharing_contract.json"
        manifest = ROOT / "meeting-artifacts" / "ibex-fusion" / "module-preserving" / "manifest.json"
        if not contract_path.exists() or not manifest.exists():
            self.skipTest("module-preserving artifacts have not been generated")
        self.assertTrue(validate_manifest(manifest, ROOT)["valid"])
        contract = json.loads(contract_path.read_text())
        self.assertEqual(contract["shared_resource"]["operand_width"], 33)
        self.assertEqual(contract["shared_resource"]["extended_result_width"], 34)
        self.assertEqual(contract["shared_resource"]["architectural_result_width"], 32)
        self.assertEqual(contract["shared_resource"]["architectural_result_slice"], "[32:1]")
        self.assertNotIn("width", contract["shared_resource"])
        text = emit_module_preserving(contract)
        self.assertIn("u_owner", text)
        self.assertIn("{client_operand_a_i, 1'b1}", text)
        self.assertIn("multdiv_sel_i(client_select_i)", text)
        bad = dict(contract)
        bad["operand_paths"] = dict(contract["operand_paths"])
        bad["operand_paths"]["client_a"] = "client_operand_a_i"
        with self.assertRaises(ContractEmitError):
            emit_module_preserving(bad)

    def test_module_manifest_schema_rejects_claim_classification_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sv"
            source.write_text("module source; endmodule\n")
            import hashlib
            record = {"path": "source.sv", "external": False,
                      "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema": "fu-ibex-module-preserving-manifest/v1", "version": 1,
                                             "classification": "contract-guided module-preserving fusion",
                                             "evidence": {"source": record}, "external_dependencies": {}}, indent=2))
            self.assertTrue(validate_manifest(manifest, root)["valid"])
            data = json.loads(manifest.read_text())
            data["classification"] = "flattened RTLIL reconstruction"
            manifest.write_text(json.dumps(data))
            with self.assertRaises(IbexManifestError):
                validate_manifest(manifest, root)

    def test_whole_alu_actual_graph_figures_are_durable(self):
        figures = ROOT / "meeting-artifacts" / "ibex-fusion" / "module-preserving" / "figures"
        before = figures / "whole_alu_separate_rtlil.svg"
        after = figures / "whole_alu_fused_rtlil.svg"
        before_dot = figures / "whole_alu_separate_rtlil.dot"
        after_dot = figures / "whole_alu_fused_rtlil.dot"
        if not before.exists() or not after.exists():
            self.skipTest("actual graph figures have not been generated")
        for figure, dot, title in ((before, before_dot, "equal-interface separate RTLIL graph"),
                                   (after, after_dot, "fused RTLIL graph")):
            text = figure.read_text()
            self.assertIn("<svg", text)
            self.assertIn(title, dot.read_text())
        manifest = ROOT / "meeting-artifacts" / "ibex-fusion" / "module-preserving" / "manifest.json"
        evidence = json.loads(manifest.read_text())["evidence"]
        self.assertIn("actual_before_graph_figure", evidence)
        self.assertIn("actual_after_graph_figure", evidence)

    def test_actual_rtlil_renders_use_graph_nodes_and_diff_highlights(self):
        artifact = ROOT / "meeting-artifacts" / "ibex-fusion" / "module-preserving"
        before = FUGraph.load(artifact / "equal_interface_separate.rtlil.graph.json")
        after = FUGraph.load(artifact / "generated_fused.rtlil.graph.json")
        diff = json.loads((artifact / "whole_alu_before_after.rtlil.diff.json").read_text())
        before_dot = (artifact / "figures/whole_alu_separate_rtlil.dot").read_text()
        after_dot = (artifact / "figures/whole_alu_fused_rtlil.dot").read_text()
        self.assertIn("digraph", before_dot)
        self.assertIn("digraph", after_dot)
        self.assertNotIn("architecture schematic", before_dot)
        self.assertNotIn("architecture schematic", after_dot)
        self.assertEqual(len(before.nodes), 113)
        self.assertEqual(len(after.nodes), 117)
        self.assertNotIn("LOGIC CONE ONLY", before_dot)
        self.assertNotIn("LOGIC CONE ONLY", after_dot)
        for node in before.nodes:
            self.assertIn(graph_viz._dot_id(node.id), before_dot)
        for node in after.nodes:
            self.assertIn(graph_viz._dot_id(node.id), after_dot)
        before_ids = {node.id for node in before.nodes}
        after_ids = {node.id for node in after.nodes}
        self.assertIn(diff["retained_shared_add"]["before_id"], before_ids)
        self.assertIn(diff["retained_shared_add"]["after_id"], after_ids)
        self.assertIn(diff["removed_dedicated_add"][0]["id"], before_ids)
        highlighted_before = (artifact / "figures/whole_alu_separate_rtlil_highlighted.dot").read_text()
        highlighted_after = (artifact / "figures/whole_alu_fused_rtlil_highlighted.dot").read_text()
        self.assertIn(graph_viz._dot_id(diff["retained_shared_add"]["before_id"]), highlighted_before)
        self.assertIn(graph_viz._dot_id(diff["retained_shared_add"]["after_id"]), highlighted_after)
        self.assertIn(graph_viz._dot_id(diff["removed_dedicated_add"][0]["id"]), highlighted_before)
        for item in diff["additional_post_fusion_mux_nodes"]:
            self.assertIn(graph_viz._dot_id(item["id"]), highlighted_after)

    def test_directed_client_path_views_are_source_graph_slices(self):
        artifact = ROOT / "meeting-artifacts" / "ibex-fusion" / "module-preserving"
        provenance = json.loads((artifact / "whole_alu_rtlil_client_path.provenance.json").read_text())
        self.assertEqual(provenance["algorithm"], "directed reachability and deterministic shortest-path union")
        self.assertNotIn("undirected", json.dumps(provenance).lower())
        diff = json.loads((artifact / "whole_alu_before_after.rtlil.diff.json").read_text())

        def edge_key(edge):
            return (edge.src, edge.dst, edge.src_port, edge.dst_port, edge.width,
                    edge.inverted, json.dumps(edge.attrs, sort_keys=True))

        for side, source_name, expected_add_width in (
                ("before", "equal_interface_separate.rtlil.graph.json", 32),
                ("after", "generated_fused.rtlil.graph.json", 34)):
            source = FUGraph.load(artifact / source_name)
            sliced = FUGraph.load(artifact / "whole_alu_rtlil_client_path_{}.graph.json".format(side))
            source_ids = {node.id for node in source.nodes}
            source_edges = {edge_key(edge) for edge in source.edges}
            source_node_map = source.node_map()
            real_nodes = [node for node in sliced.nodes if node.kind != "boundary"]
            self.assertTrue(real_nodes)
            self.assertTrue({node.id for node in real_nodes} <= source_ids)
            self.assertTrue(any(node.kind == "$add" and node.width == expected_add_width
                                for node in real_nodes))
            for edge in sliced.edges:
                if edge.attrs.get("collapsed_boundary_edge"):
                    self.assertIn("source_edge", edge.attrs)
                    continue
                self.assertIn(edge_key(edge), source_edges)
                self.assertIn(edge.src, source_ids)
                self.assertIn(edge.dst, source_ids)
            dot = (artifact / "figures/whole_alu_rtlil_client_path_{}.dot".format(side)).read_text()
            for node in real_nodes:
                self.assertIn(graph_viz._dot_id(node.id), dot)
            self.assertIn("client_operand_a_i", dot)
            self.assertIn("client_operand_b_i", dot)
            self.assertIn("client_select_i", dot)
            self.assertIn("result_o", dot)
            self.assertIn("dashed", dot)

        before = provenance["before"]
        after = provenance["after"]
        self.assertEqual(before["source_graph"], "equal_interface_separate.rtlil.graph.json")
        self.assertEqual(after["source_graph"], "generated_fused.rtlil.graph.json")
        self.assertEqual(before["target_add_id"], diff["removed_dedicated_add"][0]["id"])
        self.assertEqual(after["target_add_id"], diff["retained_shared_add"]["after_id"])
        self.assertEqual(before["retained_add_id"], diff["retained_shared_add"]["before_id"])
        comparison = (artifact / "figures/whole_alu_rtlil_client_path_comparison.svg").read_text()
        for caption in ("Before: 2 $add, 9 $mux, 1232.112", "After: 1 $add, 12 $mux, 1115.870",
                        "Area: -9.434%", "Delay: 1.860 ns", "simultaneous ALU/client operation is not preserved"):
            self.assertIn(caption, comparison)


if __name__ == "__main__":
    unittest.main()
