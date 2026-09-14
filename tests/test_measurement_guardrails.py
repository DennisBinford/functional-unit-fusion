import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import measurement_guardrails as guardrails


ROOT = Path(__file__).resolve().parents[1]


class MeasurementGuardrailTest(unittest.TestCase):
    def test_separate_interface_has_one_mul_and_one_add_after_proc_opt(self):
        evidence = guardrails.inspect_design(
            ROOT / "rtl/fma_experiment/interface_separate_add_mul_mac.sv",
            "interface_separate_add_mul_mac")
        self.assertEqual(evidence["operator_counts"], {"$mul": 1, "$add": 1})
        guardrails.require_one_mul_one_add(evidence, "separate interface")

    def test_generated_interface_has_one_mul_and_one_add_after_proc_opt(self):
        evidence = guardrails.inspect_design(
            ROOT / "rtl/fma_experiment/generated_fused_add_mul_mac.sv",
            "generated_fused_add_mul_mac")
        self.assertEqual(evidence["operator_counts"], {"$mul": 1, "$add": 1})

    def test_canonical_contract_is_recorded(self):
        evidence = guardrails.inspect_design(
            ROOT / "rtl/fma_experiment/core_fused_add_mul_mac_u32.sv",
            "core_fused_add_mul_mac_u32")
        guardrails.require_semantic_contract(evidence, "fused core")
        self.assertEqual(evidence["result_widths"]["result_o"], 64)

    def test_literal_comparison_requires_widths_and_independent_composition(self):
        add = guardrails.inspect_design(ROOT / "rtl/fma_experiment/core_add_u64.sv", "core_add_u64")
        mul = guardrails.inspect_design(ROOT / "rtl/fma_experiment/core_mul_u32.sv", "core_mul_u32")
        fused = guardrails.inspect_design(ROOT / "rtl/fma_experiment/core_fused_add_mul_mac_u32.sv", "core_fused_add_mul_mac_u32")
        result = guardrails.require_literal_core_comparison(
            add, mul, fused, {"checks": 70, "composition_checks": 70})
        self.assertTrue(result["width_chain_valid"])
        add["semantics"] = {"caller_claim": "not proof"}
        self.assertTrue(guardrails.require_literal_core_comparison(
            add, mul, fused, {"checks": 70, "composition_checks": 70})["width_chain_valid"])
        with self.assertRaises(ValueError):
            guardrails.require_literal_core_comparison(
                guardrails.inspect_design(ROOT / "rtl/fma_experiment/core_mul_u32.sv", "core_mul_u32"),
                mul, fused, {"checks": 70, "composition_checks": 70})


if __name__ == "__main__":
    unittest.main()
