#!/usr/bin/env python3
"""Generate the follow-up interface-aware fusion decision evidence."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fusion_decision import evaluate_measured_result, preflight_candidate


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "meeting-artifacts/2026-09-21-decision-stage"


def read_json(path: str) -> Dict[str, Any]:
    return json.loads((ROOT / path).read_text())


def sha256(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def contract(candidate_id: str, source: Dict[str, Any], *, widths, independent_pairs: int,
             selection_mode: str, shared_width: int, comparison: Dict[str, Any],
             match_count: int) -> Dict[str, Any]:
    return {
        "schema": "fu-candidate-contract/v1",
        "version": 1,
        "candidate_id": candidate_id,
        "classification": source.get("classification"),
        "interface": {
            "operand_sources": [
                {"name": name, "width": width, "independent": independent_pairs > 1}
                for name, width in zip(("source_a", "source_b"), widths)
            ],
            "externally_visible_results": source["number_of_externally_visible_results"],
            "selection_mode": selection_mode,
            "simultaneous_use_preserved": source["simultaneous_use_preserved"],
            "latency": {"kind": "combinational", "registered": "undefined_without_wrapper"},
            "throughput": source["throughput"],
        },
        "requirements": {
            "equal_external_interface": True,
            "simultaneous_use_required": False,
            "required_clients_per_evaluation": 1,
            "max_area_increase_percent": 0.0,
            "max_delay_increase_ns": None,
        },
        "shared_resource": {"kind": "$add", "width": shared_width},
        "match_evidence": {
            "matched_operator_count": match_count,
            "shared_resource_kind": "$add",
            "shared_resource_width": shared_width,
        },
        "comparison_contract": comparison,
    }


def ppa(case: Dict[str, Any]) -> Dict[str, Any]:
    value = case["ppa"] if "ppa" in case else case
    return {key: value[key] for key in ("area_um2", "critical_delay_ns", "fmax_mhz")}


def measured(preflight: Dict[str, Any], separate: Dict[str, Any], fused: Dict[str, Any]) -> Dict[str, Any]:
    return evaluate_measured_result(
        preflight, ppa(separate), ppa(fused),
        {"status": fused.get("simulation", {}).get("status", "pass")},
        {"max_area_increase_percent": 0.0, "max_delay_increase_ns": None})


def interface_pair(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("number_of_externally_visible_results", "number_of_independent_operand_pairs",
            "selection_model", "simultaneous_use_preserved", "throughput")
    compared = {key: {"a": a.get(key), "b": b.get(key), "equal": a.get(key) == b.get(key)}
                for key in keys}
    return {"equal_external_interface": all(item["equal"] for item in compared.values()),
            "compared_fields": compared}


def build() -> Dict[str, Any]:
    control = read_json("meeting-artifacts/2026-09-21/interface-control/interface_control_results.json")
    sub = read_json("meeting-artifacts/2026-09-21/sub_ltu/interface_contract.json")
    fresh = read_json("meeting-artifacts/2026-09-21/fresh/ppa.json")
    ibex_contract = read_json("meeting-artifacts/ibex-fusion/module-preserving/sharing_contract.json")
    ibex_ppa = read_json("meeting-artifacts/ibex-fusion/module-preserving/equal_interface_ppa.json")

    ind_sep = control["cases"]["independent_equal_interface_separate"]
    ind_fused = control["cases"]["independent_input_fused"]
    same_sep = control["cases"]["same_input_separate"]
    same_fused = control["cases"]["same_input_shared"]
    ind_contract = contract("independent_sub_ltu", sub,
                            widths=(33, 33), independent_pairs=2, selection_mode="client_selection",
                            shared_width=34, comparison=contract("independent_sub_ltu_baseline",
                            ind_sep["interface_contract"], widths=(33, 33), independent_pairs=2,
                            selection_mode="client_selection", shared_width=34,
                            comparison={}, match_count=2), match_count=2)
    same_contract = contract("same_input_sub_ltu_control", same_fused["interface_contract"],
                             widths=(33,), independent_pairs=1, selection_mode="operation_selection",
                             shared_width=34, comparison=contract("same_input_sub_ltu_baseline",
                             same_sep["interface_contract"], widths=(33,), independent_pairs=1,
                             selection_mode="operation_selection", shared_width=34,
                             comparison={}, match_count=2), match_count=2)
    whole_source = {
        "classification": ibex_contract["classification"],
        "number_of_externally_visible_results": 1,
        "simultaneous_use_preserved": ibex_contract["simultaneous_use"]["preserved"],
        "throughput": {"available_clients_per_evaluation": 1,
                        "registered_latency_and_initiation_interval": "undefined_without_wrapper"},
    }
    whole_contract = contract("whole_ibex_alu_plus_add_client", whole_source,
                              widths=(33, 33), independent_pairs=2, selection_mode="client_selection",
                              shared_width=34,
                              comparison=contract("whole_ibex_alu_plus_add_client_baseline", whole_source,
                              widths=(33, 33), independent_pairs=2, selection_mode="client_selection",
                              shared_width=34, comparison={}, match_count=2), match_count=2)

    def routing(case: Dict[str, Any], mapped_mux: str = "MUX2") -> Dict[str, Any]:
        value = case["ppa"] if "ppa" in case else case
        return {"pretechmap": {"mux_count": value["pretechmap"]["mux_count"]},
                "mapped": {"mux_cell": mapped_mux,
                           "count": value.get("mapped_mux2_count", 0),
                           "area_um2": value.get("mapped_mux2_area_um2", 0.0)},
                "mapped_mux2_note": "mapped cell population evidence, not a formal causal decomposition"}

    preflights = {
        "independent_sub_ltu": preflight_candidate(ind_contract, ind_contract["comparison_contract"],
                                                    routing(ind_fused)),
        "same_input_sub_ltu_control": preflight_candidate(same_contract, same_contract["comparison_contract"],
                                                           routing(same_fused)),
        "whole_ibex_alu_plus_add_client": preflight_candidate(
            whole_contract, whole_contract["comparison_contract"],
            {"pretechmap": {"mux_count": ibex_ppa["pretechmap"]["fused"]["kinds"]["$mux"]},
             "mapped_mux2_note": "mapped cell population evidence, not a formal causal decomposition"}),
    }
    decisions = {
        "independent_sub_ltu": measured(preflights["independent_sub_ltu"], ind_sep, ind_fused),
        "same_input_sub_ltu_control": measured(preflights["same_input_sub_ltu_control"], same_sep, same_fused),
        "whole_ibex_alu_plus_add_client": evaluate_measured_result(
            preflights["whole_ibex_alu_plus_add_client"], ibex_ppa["separate"], ibex_ppa["fused"],
            {"status": "pass"}, {"max_area_increase_percent": 0.0, "max_delay_increase_ns": None}),
    }
    independent_pair = interface_pair(ind_sep["interface_contract"], ind_fused["interface_contract"])
    same_pair = interface_pair(same_sep["interface_contract"], same_fused["interface_contract"])
    if not independent_pair["equal_external_interface"] or not same_pair["equal_external_interface"]:
        raise ValueError("interface-control comparison pair is not equal-interface")
    source_paths = [
        "meeting-artifacts/2026-09-21/interface-control/interface_control_results.json",
        "meeting-artifacts/2026-09-21/sub_ltu/interface_contract.json",
        "meeting-artifacts/2026-09-21/fresh/ppa.json",
        "meeting-artifacts/ibex-fusion/module-preserving/sharing_contract.json",
        "meeting-artifacts/ibex-fusion/module-preserving/equal_interface_ppa.json",
    ]
    return {
        "schema": "fu-interface-aware-decision-report/v1",
        "version": 1,
        "generated_by": "scripts/interface_aware_decision.py",
        "purpose": "Conservative preflight screening followed by separate measured policy evaluation.",
        "policy": {"acceptable_area_increase_percent": 0.0,
                    "acceptable_delay_increase_ns": None,
                    "unspecified_performance_bound": "report_tradeoff"},
        "source_artifacts": [{"path": path, "sha256": sha256(path)} for path in source_paths],
        "cases": {
            "independent_sub_ltu": {
                "contract": ind_contract, "preflight": preflights["independent_sub_ltu"],
            "measured": decisions["independent_sub_ltu"],
            "interface_pair_check": independent_pair,
            "interpretation": "Selected-source independent-input sharing; area-losing measured case against the 323.456 um2 equal-interface baseline. The 332.500 um2 literal sum is resource accounting only.",
                "resource_accounting_reference": {
                    "literal_sum_um2": fresh["literal_sum_um2"],
                    "label": fresh["literal_sum_interpretation"],
                },
                "evidence_notes": ["original Ibex ALU result is corroborating evidence only",
                                   "MUX2 area is mapped cell-population evidence, not causal decomposition"],
            },
            "same_input_sub_ltu_control": {
                "contract": same_contract, "preflight": preflights["same_input_sub_ltu_control"],
                "measured": decisions["same_input_sub_ltu_control"],
                "interface_pair_check": same_pair,
                "interpretation": "Beneficial same-input operation-selected control within one ALU interface; not an independent-client result.",
            },
            "whole_ibex_alu_plus_add_client": {
                "contract": whole_contract, "preflight": preflights["whole_ibex_alu_plus_add_client"],
                "measured": decisions["whole_ibex_alu_plus_add_client"],
                "interface_pair_check": {"equal_external_interface": True,
                                          "basis": ibex_ppa["comparison"]},
                "interpretation": "Contract-guided whole-Ibex ALU plus ADD-client result using equal external interfaces; not proof of automatic whole-module discovery.",
            },
        },
        "scope_limits": [
            "Preflight can reject incompatible interfaces or insufficient contracts and can authorize measurement; it cannot predict profitability.",
            "Two examples do not establish a universal area threshold.",
            "Generalization requires more equal-interface synthesized/correctness-validated cases across widths, operators, controls, libraries, and timing policies.",
        ],
    }


def markdown(report: Dict[str, Any]) -> str:
    lines = ["# Interface-aware fusion decision follow-up", "",
             "Generated by `scripts/interface_aware_decision.py`; this report separates preflight screening from measured PPA.", "",
             "| Case | Preflight | Measured policy | Area delta | Delay delta | Fmax delta | Interpretation |", "|---|---|---|---:|---:|---:|---|"]
    for name, case in report["cases"].items():
        delta = case["measured"].get("deltas", {})
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            name, case["preflight"]["decision"], case["measured"]["decision"],
            delta.get("area_um2", "n/a"), delta.get("critical_delay_ns", "n/a"),
            delta.get("fmax_mhz", "n/a"), case["interpretation"]))
    lines += ["", "The policy allows no area increase and leaves the acceptable delay bound unspecified. A delay increase is therefore reported as a tradeoff unless area already violates policy.", "",
              "Preflight can decide contract/interface compatibility, simultaneous-use legality, and whether available graph/control evidence is sufficient to measure. It does not estimate synthesized area. Generalization needs additional equal-interface, correctness-validated synthesis cases across operators, widths, libraries, and explicit timing policies.", ""]
    return "\n".join(lines)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report = build()
    (OUT / "decisions.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (OUT / "explanation.md").write_text(markdown(report))
    print("wrote {} and {}".format(OUT / "decisions.json", OUT / "explanation.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
