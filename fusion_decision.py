#!/usr/bin/env python3
"""Conservative interface-aware screening and measured-result policy.

The preflight stage is intentionally not a profitability predictor.  It checks
whether a proposed sharing contract is sufficiently explicit and compatible to
justify emitting RTL for measurement.  Synthesis results are evaluated by a
separate function so screening evidence cannot be confused with measured PPA.
"""

from typing import Any, Dict, List, Optional


DECISION_SCHEMA = "fu-fusion-decision/v1"
DECISIONS = {"REJECT", "MEASURE", "UNSUPPORTED"}


def _problem(code: str, message: str) -> Dict[str, str]:
    return {"code": code, "message": message}


def assess_routing_overhead(evidence: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize routing/control evidence without treating it as PPA."""
    evidence = evidence or {}
    pretechmap = evidence.get("pretechmap") or {}
    mapped = evidence.get("mapped") or {}
    mux_count = pretechmap.get("mux_count")
    mapped_mux = mapped.get("mux_cell")
    mapped_count = mapped.get("count")
    mapped_area = mapped.get("area_um2")
    available = any(value is not None for value in (mux_count, mapped_mux, mapped_count, mapped_area))
    uncertainty = [
        "screening evidence is not measured area or delay",
        "routing congestion, placement, buffering, and cell remapping remain unknown",
    ]
    if mapped_mux == "MUX2" or evidence.get("mapped_mux2_note"):
        uncertainty.append("MUX2 population is mapped-cell evidence, not a formal causal decomposition")
    return {
        "status": "screening_only" if available else "unavailable",
        "pretechmap_mux_count": mux_count,
        "mapped_mux_cell": mapped_mux,
        "mapped_mux_count": mapped_count,
        "mapped_mux_area_um2": mapped_area,
        "measured_area_um2": None,
        "uncertainty": uncertainty,
    }


def _interface_shape(interface: Dict[str, Any]) -> Dict[str, Any]:
    sources = interface.get("operand_sources") or []
    return {
        "operand_sources": [(source.get("width"), source.get("independent")) for source in sources],
        "externally_visible_results": interface.get("externally_visible_results"),
        "selection_mode": interface.get("selection_mode"),
        "latency": interface.get("latency"),
        "throughput": interface.get("throughput"),
    }


def preflight_candidate(contract: Dict[str, Any],
                        baseline_contract: Optional[Dict[str, Any]] = None,
                        routing_evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a conservative, structured pre-emission decision."""
    problems: List[Dict[str, str]] = []
    unsupported: List[Dict[str, str]] = []
    interface = contract.get("interface") or {}
    requirements = contract.get("requirements") or {}
    resource = contract.get("shared_resource") or {}
    match = contract.get("match_evidence") or {}
    sources = interface.get("operand_sources")
    if not isinstance(sources, list) or not sources:
        unsupported.append(_problem("missing_operand_sources", "candidate contract lacks operand source declarations"))
    if interface.get("selection_mode") not in ("client_selection", "operation_selection"):
        unsupported.append(_problem("unknown_selection_mode", "selection_mode must be client_selection or operation_selection"))
    if not isinstance(interface.get("externally_visible_results"), int):
        unsupported.append(_problem("missing_output_interface", "externally_visible_results must be explicit"))
    if not isinstance(interface.get("simultaneous_use_preserved"), bool):
        unsupported.append(_problem("missing_simultaneous_semantics", "simultaneous_use_preserved must be explicit"))
    throughput = interface.get("throughput")
    if (not isinstance(interface.get("latency"), dict) or not isinstance(throughput, dict) or
            not isinstance(throughput.get("available_clients_per_evaluation"), int) or
            throughput.get("available_clients_per_evaluation") < 1):
        unsupported.append(_problem("missing_timing_contract", "latency and throughput assumptions must be explicit"))
    if not resource.get("kind") or not isinstance(resource.get("width"), int):
        unsupported.append(_problem("missing_shared_resource", "shared resource kind and width must be explicit"))
    if not isinstance(match.get("matched_operator_count"), int) or not match.get("shared_resource_kind"):
        unsupported.append(_problem("insufficient_match_evidence", "operator match and shared-resource evidence are required"))
    if not isinstance(requirements.get("equal_external_interface"), bool):
        unsupported.append(_problem("missing_interface_requirement", "equal_external_interface requirement must be explicit"))
    if not isinstance(requirements.get("simultaneous_use_required"), bool):
        unsupported.append(_problem("missing_simultaneous_requirement", "simultaneous_use_required must be explicit"))
    required_clients = requirements.get("required_clients_per_evaluation")
    if not isinstance(required_clients, int) or required_clients < 1:
        unsupported.append(_problem("missing_throughput_requirement", "required client throughput must be explicit"))

    if unsupported:
        decision = "UNSUPPORTED"
    else:
        if requirements.get("equal_external_interface"):
            if baseline_contract is None:
                unsupported.append(_problem(
                    "missing_comparison_interface",
                    "equal_external_interface is required but no comparison contract was supplied"))
            else:
                baseline_interface = baseline_contract.get("interface") or {}
                if _interface_shape(interface) != _interface_shape(baseline_interface):
                    problems.append(_problem(
                        "external_interface_mismatch",
                        "candidate and comparison baseline do not have equal external interface shape"))
        if requirements.get("simultaneous_use_required") and not interface.get("simultaneous_use_preserved"):
            problems.append(_problem(
                "simultaneous_use_violation",
                "candidate multiplexes a shared resource but the contract requires simultaneous client use"))
        if throughput["available_clients_per_evaluation"] < required_clients:
            problems.append(_problem(
                "throughput_violation",
                "candidate provides {} client per evaluation but the contract requires {}".format(
                    throughput["available_clients_per_evaluation"], required_clients)))
        decision = "UNSUPPORTED" if unsupported else ("REJECT" if problems else "MEASURE")

    routing = assess_routing_overhead(routing_evidence)
    return {
        "schema": DECISION_SCHEMA,
        "version": 1,
        "candidate_id": contract.get("candidate_id", "unnamed"),
        "stage": "preflight",
        "decision": decision,
        "reasons": problems + unsupported,
        "routing_overhead": routing,
        "measurement_required": decision == "MEASURE",
        "profitability_claim": "none_before_synthesis",
        "diagnostic_override": {
            "allowed": decision == "REJECT",
            "purpose": "emit a rejected candidate for diagnosis only",
        },
    }


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def evaluate_measured_result(preflight: Dict[str, Any], baseline: Dict[str, Any],
                            candidate: Dict[str, Any], correctness: Dict[str, Any],
                            policy: Dict[str, Any]) -> Dict[str, Any]:
    """Apply an explicit post-synthesis policy and preserve tradeoffs."""
    required = ("area_um2", "critical_delay_ns", "fmax_mhz")
    missing = [key for key in required if not _number(baseline.get(key)) or not _number(candidate.get(key))]
    if missing or correctness.get("status") != "pass":
        return {
            "schema": DECISION_SCHEMA, "version": 1, "stage": "measured",
            "candidate_id": preflight.get("candidate_id"), "decision": "UNSUPPORTED",
            "reasons": [{"code": "measurement_invalid", "message": "missing PPA metrics or correctness did not pass",
                         "details": {"missing": missing, "correctness": correctness.get("status")}}],
        }
    area_delta = candidate["area_um2"] - baseline["area_um2"]
    area_pct = 100.0 * area_delta / baseline["area_um2"]
    delay_delta = candidate["critical_delay_ns"] - baseline["critical_delay_ns"]
    fmax_delta = candidate["fmax_mhz"] - baseline["fmax_mhz"]
    max_area = policy.get("max_area_increase_percent")
    max_delay = policy.get("max_delay_increase_ns")
    reasons = []
    if max_area is None:
        return {"schema": DECISION_SCHEMA, "version": 1, "stage": "measured",
                "candidate_id": preflight.get("candidate_id"), "decision": "UNSUPPORTED",
                "reasons": [{"code": "missing_area_policy", "message": "acceptable area policy is unspecified"}],
                "deltas": {"area_um2": area_delta, "area_percent": area_pct,
                           "critical_delay_ns": delay_delta, "fmax_mhz": fmax_delta}}
    area_pass = area_pct <= max_area
    delay_pass = max_delay is not None and delay_delta <= max_delay
    if delay_delta > 0 and max_delay is None:
        reasons.append({"code": "performance_bound_unspecified",
                        "message": "delay worsened but no acceptable performance bound was supplied"})
    elif max_delay is not None and not delay_pass:
        reasons.append({"code": "delay_policy_exceeded", "message": "measured delay exceeds the explicit policy"})
    if not area_pass:
        reasons.append({"code": "area_policy_exceeded", "message": "measured area exceeds the explicit policy"})
    if reasons:
        decision = "REJECT" if any(item["code"] in ("area_policy_exceeded", "delay_policy_exceeded")
                                    for item in reasons) else "TRADEOFF"
    elif max_delay is None and delay_delta > 0:
        decision = "TRADEOFF"
    else:
        decision = "FUSE"
    return {
        "schema": DECISION_SCHEMA, "version": 1, "stage": "measured",
        "candidate_id": preflight.get("candidate_id"), "decision": decision,
        "reasons": reasons, "correctness": correctness,
        "policy": {"max_area_increase_percent": max_area, "max_delay_increase_ns": max_delay},
        "deltas": {"area_um2": area_delta, "area_percent": area_pct,
                   "critical_delay_ns": delay_delta, "fmax_mhz": fmax_delta},
    }
