import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "meeting-artifacts" / "2026-09-21-intermediate-search"


@unittest.skipUnless((STUDY / "manifest.json").is_file(),
                     "run scripts/intermediate_representation_study_2026_09_21.py first")
class IntermediateRepresentationStudyTest(unittest.TestCase):
    def load(self, name):
        return json.loads((STUDY / name).read_text())

    def test_three_level_walk_selects_rtlil_and_keeps_gate_structural(self):
        assessment = self.load("representation_assessment.json")
        hierarchy = self.load("hierarchy_analysis.json")
        self.assertEqual(assessment["selected_middle_stop"], "rtlil")
        self.assertEqual(hierarchy["selected_level"], "rtlil")
        self.assertEqual(hierarchy["metrics"]["module"]["operator_match_size"], 0)
        self.assertEqual(hierarchy["metrics"]["rtlil"]["operator_match_size"], 6)
        self.assertEqual(hierarchy["metrics"]["rtlil"]["connected_subgraph_size"], 6)
        self.assertIn("structural only", assessment["gate_reconstruction"])
        self.assertGreater(assessment["levels"]["ast"]["match"]["common_node_count"], 0)

    def test_source_style_and_parameter_name_probes_are_recorded(self):
        probes = self.load("provenance_probes.json")
        names = probes["parameterized_module_name_probe"]
        self.assertEqual(names["same_specialization_different_child_names_common_nodes"], 2)
        self.assertEqual(names["different_parameter_specialization_common_nodes"], 0)
        style = probes["coding_style_probe"]
        self.assertGreater(style["rtlil_common_operators"], 0)

    def test_preflight_and_measured_results_are_separate(self):
        preflight = self.load("preflight_decision.json")
        measured = self.load("measured_result.json")
        self.assertEqual(preflight["stage"], "preflight")
        self.assertEqual(preflight["decision"], "MEASURE")
        self.assertEqual(preflight["profitability_claim"], "none_before_synthesis")
        self.assertEqual(measured["decision"]["stage"], "measured")
        self.assertEqual(measured["decision"]["decision"], "REJECT")
        self.assertGreater(measured["decision"]["deltas"]["area_percent"], 0.0)
        self.assertGreater(measured["decision"]["deltas"]["critical_delay_ns"], 0.0)
        self.assertIsNone(measured["policy"]["max_delay_increase_ns"])

    def test_every_manifest_hash_is_valid(self):
        manifest = self.load("manifest.json")
        for relative, expected in manifest["artifacts"].items():
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(actual, expected, relative)

    def test_publishable_artifacts_do_not_embed_this_checkout_path(self):
        manifest = self.load("manifest.json")
        for relative in manifest["artifacts"]:
            payload = (ROOT / relative).read_bytes()
            self.assertNotIn(b"/home/dabinford/research", payload, relative)


if __name__ == "__main__":
    unittest.main()
