import unittest

from fusion_decision import assess_routing_overhead, evaluate_measured_result, preflight_candidate


def candidate(**overrides):
    value = {
        "schema": "fu-candidate-contract/v1",
        "candidate_id": "synthetic",
        "interface": {
            "operand_sources": [
                {"name": "left", "width": 16, "independent": True},
                {"name": "right", "width": 16, "independent": True},
            ],
            "externally_visible_results": 1,
            "selection_mode": "client_selection",
            "simultaneous_use_preserved": False,
            "latency": {"kind": "combinational"},
            "throughput": {"available_clients_per_evaluation": 1},
        },
        "requirements": {"equal_external_interface": True,
                          "simultaneous_use_required": False,
                          "required_clients_per_evaluation": 1},
        "shared_resource": {"kind": "$mul", "width": 32},
        "match_evidence": {"matched_operator_count": 2,
                           "shared_resource_kind": "$mul",
                           "shared_resource_width": 32},
    }
    for key, value_override in overrides.items():
        value[key] = value_override
    return value


class FusionDecisionTest(unittest.TestCase):
    def test_plausible_candidate_is_measure_not_profitable(self):
        value = candidate()
        result = preflight_candidate(value, baseline_contract=candidate(), routing_evidence={
            "pretechmap": {"mux_count": 3},
            "mapped": {"mux_cell": "MUX2", "count": 12, "area_um2": 22.0},
        })
        self.assertEqual(result["decision"], "MEASURE")
        self.assertIsNone(result["routing_overhead"]["measured_area_um2"])
        self.assertTrue(result["routing_overhead"]["uncertainty"])

    def test_simultaneous_use_requirement_rejects_precisely(self):
        value = candidate()
        value["requirements"]["simultaneous_use_required"] = True
        result = preflight_candidate(value, baseline_contract=candidate())
        self.assertEqual(result["decision"], "REJECT")
        self.assertIn("simultaneous_use_violation", [item["code"] for item in result["reasons"]])

    def test_throughput_requirement_rejects_independent_client_shortfall(self):
        value = candidate()
        value["requirements"]["required_clients_per_evaluation"] = 2
        result = preflight_candidate(value, baseline_contract=candidate())
        self.assertEqual(result["decision"], "REJECT")
        self.assertIn("throughput_violation", [item["code"] for item in result["reasons"]])

    def test_equal_interface_mismatch_is_synthetic_negative_contract(self):
        baseline = candidate()
        changed = candidate()
        changed["interface"]["externally_visible_results"] = 2
        result = preflight_candidate(changed, baseline_contract=baseline)
        self.assertEqual(result["decision"], "REJECT")
        self.assertIn("external_interface_mismatch", [item["code"] for item in result["reasons"]])

    def test_missing_evidence_is_unknown_not_rejection(self):
        value = candidate()
        del value["match_evidence"]
        result = preflight_candidate(value)
        self.assertEqual(result["decision"], "UNSUPPORTED")
        self.assertIn("insufficient_match_evidence", [item["code"] for item in result["reasons"]])
        self.assertEqual(assess_routing_overhead({})["status"], "unavailable")

    def test_missing_comparison_interface_is_unsupported(self):
        result = preflight_candidate(candidate())
        self.assertEqual(result["decision"], "UNSUPPORTED")
        self.assertIn("missing_comparison_interface", [item["code"] for item in result["reasons"]])

    def test_measured_policy_reports_unspecified_delay_tradeoff(self):
        result = evaluate_measured_result(
            {"candidate_id": "whole"},
            {"area_um2": 100.0, "critical_delay_ns": 1.0, "fmax_mhz": 1000.0},
            {"area_um2": 90.0, "critical_delay_ns": 1.02, "fmax_mhz": 980.0},
            {"status": "pass"},
            {"max_area_increase_percent": 0.0, "max_delay_increase_ns": None})
        self.assertEqual(result["decision"], "TRADEOFF")
        self.assertIn("performance_bound_unspecified", [item["code"] for item in result["reasons"]])

    def test_measured_area_loss_is_rejected_even_when_delay_bound_is_unspecified(self):
        result = evaluate_measured_result(
            {"candidate_id": "losing"},
            {"area_um2": 100.0, "critical_delay_ns": 1.0, "fmax_mhz": 1000.0},
            {"area_um2": 107.0, "critical_delay_ns": 1.1, "fmax_mhz": 900.0},
            {"status": "pass"},
            {"max_area_increase_percent": 0.0, "max_delay_increase_ns": None})
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["deltas"]["area_percent"], 7.0)

    def test_explicit_delay_bound_exceeded_is_rejected(self):
        result = evaluate_measured_result(
            {"candidate_id": "late"},
            {"area_um2": 100.0, "critical_delay_ns": 1.0, "fmax_mhz": 1000.0},
            {"area_um2": 99.0, "critical_delay_ns": 1.06, "fmax_mhz": 943.4},
            {"status": "pass"},
            {"max_area_increase_percent": 0.0, "max_delay_increase_ns": 0.05})
        self.assertEqual(result["decision"], "REJECT")
        self.assertIn("delay_policy_exceeded", [item["code"] for item in result["reasons"]])


if __name__ == "__main__":
    unittest.main()
