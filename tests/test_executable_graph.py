import copy
import tempfile
import unittest
from pathlib import Path

from executable_graph import ExecutableGraphError, realize
from graph_merge import merge_graphs
from graph_match import compare
from graph_rtl_emit import GraphEmitError, emit, validate_manifest, write_manifest


def _fixture():
    def port(node_id, kind, label, width):
        return {"id": node_id, "kind": kind, "label": label, "width": width,
                "attrs": {"direction": "input" if kind == "port_in" else "output"}}
    a = {"schema": "fu-graph/v1", "design": "test_a", "level": "rtlil", "top": "test_a",
         "attrs": {"sources": ["test_a.sv"]}, "nodes": [
             port("a", "port_in", "a_a_i", 32),
             port("b", "port_in", "b_a_i", 32), port("out", "port_out", "product_a_o", 64),
             {"id": "mul_a", "kind": "$mul", "label": "mul[64]", "width": 64,
              "attrs": {"parameters": {"A_WIDTH": 32, "B_WIDTH": 32, "Y_WIDTH": 64,
                                         "A_SIGNED": 0, "B_SIGNED": 0}}}],
         "edges": [{"src": "a", "dst": "mul_a", "src_port": "a_a_i", "dst_port": "A", "width": 32},
                   {"src": "b", "dst": "mul_a", "src_port": "b_a_i", "dst_port": "B", "width": 32},
                   {"src": "mul_a", "dst": "out", "src_port": "Y", "dst_port": "product_a_o", "width": 64}]}
    b = {"schema": "fu-graph/v1", "design": "test_b", "level": "rtlil", "top": "test_b",
         "attrs": {"sources": ["test_b.sv"]}, "nodes": [
             port("a", "port_in", "a_b_i", 32),
             port("b", "port_in", "b_b_i", 32), port("c", "port_in", "c_b_i", 32),
             port("addmode", "port_in", "add_mode_b_i", 1), port("mulmode", "port_in", "mul_mode_b_i", 1),
             port("macmode", "port_in", "mac_mode_b_i", 1), port("out", "port_out", "result_b_o", 64),
             {"id": "mul_b", "kind": "$mul", "label": "mul[64]", "width": 64,
              "attrs": {"parameters": {"A_WIDTH": 32, "B_WIDTH": 32, "Y_WIDTH": 64,
                                         "A_SIGNED": 0, "B_SIGNED": 0}}},
             {"id": "add_b", "kind": "$add", "label": "add[64]", "width": 64,
              "attrs": {"parameters": {"A_WIDTH": 64, "B_WIDTH": 64, "Y_WIDTH": 64,
                                         "A_SIGNED": 0, "B_SIGNED": 0}}}],
         "edges": [{"src": "a", "dst": "mul_b", "src_port": "a_b_i", "dst_port": "A", "width": 32},
                   {"src": "b", "dst": "mul_b", "src_port": "b_b_i", "dst_port": "B", "width": 32},
                   {"src": "mul_b", "dst": "add_b", "src_port": "Y", "dst_port": "A", "width": 64},
                   {"src": "c", "dst": "add_b", "src_port": "c_b_i", "dst_port": "B", "width": 32},
                   {"src": "add_b", "dst": "out", "src_port": "Y", "dst_port": "result_b_o", "width": 64}]}
    return a, b


def _evidence(a, b):
    match = compare(a, b)
    merge = merge_graphs(a, b, match).to_dict()
    mul = next(item for item in match["matched_nodes"] if item["a"] == "mul_a")
    control = {"schema": "fu-control-spec/v1", "version": 1,
               "graph_a": "test_a", "graph_b": "test_b", "level": "rtlil",
               "generated_design": "test_fused", "generated_top": "test_fused",
               "shared_operator": {"operation": "mul", "match_pair": mul},
               "shared_control": {"port": "source_select_i", "active_value": 1},
               "inserted_ports": [{"name": "source_select_i", "direction": "input", "width": 1}],
               "outputs": [{"graph": "a", "port": "product_a_o", "behavior": "A_MUL", "select_when": {"source_select_i": 1}},
                           {"graph": "b", "port": "result_b_o", "behavior": "B_MAC", "select_when": {"source_select_i": 0}}],
               "required_paths": [{"graph": "b", "from_port": "c_b_i", "to_node": "add_b"}]}
    return match, merge, control


def _add_fixture():
    """A second, non-MAC fixture exercising the same generic realization path."""
    def port(node_id, kind, label, width):
        return {"id": node_id, "kind": kind, "label": label, "width": width,
                "attrs": {"direction": "input" if kind == "port_in" else "output"}}
    def graph(design, prefix, width):
        return {"schema": "fu-graph/v1", "design": design, "level": "rtlil", "top": design,
                "attrs": {"sources": [design + ".sv"]}, "nodes": [
            port("a", "port_in", "left_{}_i".format(prefix), width),
            port("b", "port_in", "right_{}_i".format(prefix), width),
            port("out", "port_out", "sum_{}_o".format(prefix), width),
            {"id": "add", "kind": "$add", "label": "add[{}]".format(width), "width": width,
             "attrs": {"parameters": {"A_WIDTH": width, "B_WIDTH": width,
                                        "Y_WIDTH": width, "A_SIGNED": 0, "B_SIGNED": 0}}}],
                "edges": [{"src": "a", "dst": "add", "src_port": "left_{}_i".format(prefix),
                           "dst_port": "A", "width": width},
                          {"src": "b", "dst": "add", "src_port": "right_{}_i".format(prefix),
                           "dst_port": "B", "width": width},
                          {"src": "add", "dst": "out", "src_port": "Y",
                           "dst_port": "sum_{}_o".format(prefix), "width": width}]}
    a, b = graph("add_a", "a", 16), graph("add_b", "b", 16)
    match = compare(a, b)
    merge = merge_graphs(a, b, match).to_dict()
    pair = next(item for item in match["matched_nodes"] if item["a"] == "add")
    control = {"schema": "fu-control-spec/v1", "version": 1,
               "graph_a": "add_a", "graph_b": "add_b", "level": "rtlil",
               "generated_design": "add_pair_fused", "generated_top": "add_pair",
               "shared_operator": {"operation": "add", "match_pair": pair},
               "shared_control": {"port": "select_i", "active_value": 1},
               "inserted_ports": [{"name": "select_i", "direction": "input", "width": 1}],
               "outputs": [{"graph": "a", "port": "sum_a_o", "fused_port": "sum_a_o",
                            "select_when": {"select_i": 1}},
                           {"graph": "b", "port": "sum_b_o", "fused_port": "sum_b_o",
                            "select_when": {"select_i": 0}}]}
    return a, b, match, merge, control


class ExecutableGraphTest(unittest.TestCase):
    def test_valid_plan_has_shared_mul_and_two_operand_muxes(self):
        a, b = _fixture(); match, merge, control = _evidence(a, b)
        plan = realize(a, b, match, merge, control)
        self.assertTrue(plan["hardware_status"]["executable"])
        self.assertEqual(plan["hardware_status"]["template_backed"], False)
        self.assertEqual(plan["adapters"]["shared_input_muxes"],
                         ["adapter:mux:shared_operator:A", "adapter:mux:shared_operator:B"])
        text = emit(plan)
        self.assertIn("*", text); self.assertIn("+", text); self.assertIn("source_select_i", text)

    def test_causality_changes_reject_required_edge_and_operator(self):
        a, b = _fixture(); match, merge, control = _evidence(a, b)
        b["edges"] = [edge for edge in b["edges"] if edge["src"] != "c"]
        with self.assertRaises(ExecutableGraphError): realize(a, b, match, merge, control)
        a, b = _fixture(); match, merge, control = _evidence(a, b)
        next(node for node in b["nodes"] if node["id"] == "add_b")["kind"] = "$div"
        with self.assertRaises(ExecutableGraphError): realize(a, b, match, merge, control)

    def test_same_generic_path_supports_a_different_shared_operator_and_width(self):
        a, b, match, merge, control = _add_fixture()
        plan = realize(a, b, match, merge, control)
        self.assertEqual(next(node for node in plan["nodes"] if node["id"] == "shared:operator")["kind"], "$add")
        self.assertEqual(len(plan["adapters"]["shared_input_muxes"]), 2)
        text = emit(plan)
        self.assertIn("+", text)
        self.assertNotIn(" * ", text)

    def test_plan_provenance_traces_source_or_declared_adapter(self):
        a, b = _fixture(); match, merge, control = _evidence(a, b)
        plan = realize(a, b, match, merge, control)
        for node in plan["nodes"]:
            provenance = (node.get("attrs") or {}).get("provenance")
            self.assertIsNotNone(provenance, node["id"])
        for edge in plan["edges"]:
            attrs = edge.get("attrs") or {}
            self.assertTrue(attrs.get("provenance") or attrs.get("adapter"), edge)

    def test_manifest_validates_relative_paths_and_mutation_or_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "plan.json"; rtl = root / "unit.sv"; source = root / "source.sv"
            plan.write_text("{}\n"); rtl.write_text("module unit; endmodule\n"); source.write_text("source\n")
            manifest = root / "unit.manifest.json"
            write_manifest(manifest, plan, rtl, {"source": source})
            self.assertTrue(validate_manifest(manifest, root)["valid"])
            source.write_text("changed\n")
            with self.assertRaises(GraphEmitError): validate_manifest(manifest, root)
            source.write_text("source\n"); source.unlink()
            with self.assertRaises(GraphEmitError): validate_manifest(manifest, root)

    def test_reordered_ids_are_deterministic_and_port_tampering_rejects(self):
        a, b = _fixture(); match, merge, control = _evidence(a, b)
        first = emit(realize(a, b, match, merge, control))
        a2, b2 = copy.deepcopy(a), copy.deepcopy(b)
        a2["nodes"].reverse(); b2["nodes"].reverse(); a2["edges"].reverse(); b2["edges"].reverse()
        self.assertEqual(first, emit(realize(a2, b2, match, merge, control)))
        b2["nodes"] = [dict(node) for node in b2["nodes"]]
        next(node for node in b2["nodes"] if node["id"] == "c")["label"] = "tampered_c_i"
        with self.assertRaises(ExecutableGraphError): realize(a, b2, match, merge, control)

    def test_source_connection_and_control_provenance_are_causal(self):
        a, b = _fixture(); match, merge, control = _evidence(a, b)
        original = emit(realize(a, b, match, merge, control))
        changed = copy.deepcopy(b)
        edge = next(edge for edge in changed["edges"] if edge["dst"] == "mul_b" and edge["dst_port"] == "A")
        edge["src"] = "c"
        changed_text = emit(realize(a, changed, match, merge, control))
        self.assertNotEqual(original, changed_text)
        tampered = copy.deepcopy(control)
        tampered["graph_b"] = "unrelated"
        with self.assertRaises(ExecutableGraphError): realize(a, b, match, merge, tampered)

    def test_rejects_cycle_signed_unsafe_width_and_multiple_driver(self):
        a, b = _fixture(); match, merge, control = _evidence(a, b)
        a["edges"].append({"src": "mul_a", "dst": "mul_a", "src_port": "Y", "dst_port": "A", "width": 64})
        with self.assertRaises(ExecutableGraphError): realize(a, b, match, merge, control)
        a, b = _fixture(); match, merge, control = _evidence(a, b)
        next(node for node in b["nodes"] if node["id"] == "mul_b")["attrs"]["signed"] = True
        with self.assertRaises(ExecutableGraphError): realize(a, b, match, merge, control)
        a, b = _fixture(); match, merge, control = _evidence(a, b)
        edge = next(edge for edge in b["edges"] if edge["src"] == "c")
        edge["width"] = 64
        with self.assertRaises(ExecutableGraphError): realize(a, b, match, merge, control)
        a, b = _fixture(); match, merge, control = _evidence(a, b)
        b["edges"].append(dict(next(edge for edge in b["edges"] if edge["src"] == "c")))
        with self.assertRaises(ExecutableGraphError): realize(a, b, match, merge, control)


if __name__ == "__main__":
    unittest.main()
