import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "tachyum_module_study.py"


def load_study():
    spec = importlib.util.spec_from_file_location("tachyum_module_study", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TachyumModuleStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.study = load_study()

    def test_survey_is_bounded_and_selected_pair_is_real_source_instance_pair(self):
        self.assertLessEqual(len(self.study.PAIR_SPECS), 3)
        selected = [item for item in self.study.PAIR_SPECS if item["selected"]]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["kind"], "right_shifter_74_with_outside_bits")
        self.assertEqual(
            selected[0]["parent_instances"],
            ["u_right_shifter_for_addend_single_1", "u_right_shifter_for_addend_single_2"],
        )

    def test_provenance_and_frozen_rejection_contract(self):
        provenance = self.study.source_revision()
        self.assertEqual(len(provenance["revision"]), 40)
        self.assertEqual(provenance["license"], "Apache License 2.0")
        contract = self.study._contract()
        self.assertEqual(contract["latency_cycles"], 4)
        self.assertEqual(contract["ppa_status"], "not_run")
        self.assertIn("throughput", contract["ppa_blocker"])

    def test_wrapper_names_are_distinct(self):
        text = self.study.WRAPPERS.read_text()
        self.assertIn("tachyum_single_1_right_shifter", text)
        self.assertIn("tachyum_single_2_right_shifter", text)
        self.assertIn("lane_single_1", text)
        self.assertIn("lane_single_2", text)


if __name__ == "__main__":
    unittest.main()
