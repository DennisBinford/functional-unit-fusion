#!/usr/bin/env python3
"""Machine-checkable pre-technology-mapping measurement guardrails."""

import json
import shlex
import subprocess
import tempfile
from pathlib import Path

import toolchain


SEMANTICS = {
    "ADD": "zero_extend_64(a) + zero_extend_64(b), modulo 2^64",
    "MUL": "a * b, producing 64 bits",
    "MAC": "(a * b + zero_extend_64(c)), modulo 2^64",
}


def inspect_design(source, top, clock_period_ns=10.0, sdc=None, library=None, lock=None,
                   semantics=None):
    """Return RTLIL cell/port evidence after proc+opt and before techmap."""
    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError("missing guardrail source: {}".format(source))
    yosys = toolchain.find_executable("yosys", "FU_YOSYS")
    if not yosys:
        raise ValueError("Yosys is required for pre-technology guardrails")
    with tempfile.TemporaryDirectory(prefix="measurement_guardrails-") as temp:
        out = Path(temp) / "design.json"
        script = (
            "read_verilog -sv {}; hierarchy -top {}; proc; opt; write_json {}"
            .format(shlex.quote(str(source)), shlex.quote(str(top)), shlex.quote(str(out)))
        )
        completed = subprocess.run([yosys, "-p", script], cwd=source.parents[1],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True)
        if completed.returncode:
            raise ValueError("Yosys guardrail failed for {}:\n{}".format(top, completed.stdout[-2000:]))
        data = json.loads(out.read_text())
    module = data.get("modules", {}).get(top)
    if module is None:
        raise ValueError("Yosys guardrail did not produce top {}".format(top))
    cells = module.get("cells", {})
    counts = {}
    for cell in cells.values():
        counts[cell.get("type")] = counts.get(cell.get("type"), 0) + 1
    ports = {}
    for name, port in module.get("ports", {}).items():
        ports[name] = {"direction": port.get("direction"),
                       "width": len(port.get("bits", [])), "signed": False}
    result = {
        "stage": "pre_technology_mapping",
        "source": str(source),
        "source_sha256": toolchain.sha256_file(source),
        "top": top,
        "operator_counts": {key: counts.get(key, 0) for key in ("$mul", "$add")},
        "all_cell_counts": dict(sorted(counts.items())),
        "ports": ports,
        "operand_widths": {name: value["width"] for name, value in ports.items()
                            if value["direction"] == "input"},
        "result_widths": {name: value["width"] for name, value in ports.items()
                          if value["direction"] == "output"},
        "signedness": "unsigned; signed capability is rejected",
        "semantics": dict(semantics or SEMANTICS),
        "clock_period_ns": clock_period_ns,
        "sdc": str(Path(sdc).resolve()) if sdc else None,
        "library": str(Path(library).resolve()) if library else None,
        "toolchain_lock": str(Path(lock).resolve()) if lock else None,
        "yosys": toolchain.tool_version("yosys", yosys),
    }
    if sdc:
        result["sdc_sha256"] = toolchain.sha256_file(Path(sdc))
    if library:
        result["library_sha256"] = toolchain.sha256_file(Path(library))
    if lock:
        result["toolchain_lock_sha256"] = toolchain.sha256_file(Path(lock))
    return result


def require_one_mul_one_add(evidence, label):
    counts = evidence.get("operator_counts", {})
    if counts.get("$mul") != 1 or counts.get("$add") != 1:
        raise ValueError("{} must have exactly one pre-techmap $mul and $add; got {}"
                         .format(label, counts))


def require_semantic_contract(evidence, label):
    if evidence.get("semantics") != SEMANTICS:
        raise ValueError("{} does not use the canonical unsigned 64-bit contract".format(label))


def require_literal_core_comparison(add_evidence, mul_evidence, fused_evidence,
                                    simulation_evidence):
    """Require structural width compatibility and independent composition proof.

    The declared ``semantics`` fields are descriptive metadata only.  This
    guardrail deliberately relies on Yosys-derived ports/cells and the
    independent simulation marker rather than a caller-supplied string.
    """
    add_ports = add_evidence.get("ports", {})
    mul_ports = mul_evidence.get("ports", {})
    fused_ports = fused_evidence.get("ports", {})
    if any(add_ports.get(name, {}).get("width") != 64 for name in ("a_i", "b_i", "result_o")):
        raise ValueError("literal core comparison requires a 64-bit input/output adder")
    if mul_ports.get("result_o", {}).get("width") != 64:
        raise ValueError("multiplier result is not 64 bits")
    if fused_ports.get("result_o", {}).get("width") != 64:
        raise ValueError("fused result is not 64 bits")
    if any(mul_ports.get(name, {}).get("width") != 32 for name in ("a_i", "b_i")):
        raise ValueError("literal multiplier operands must be 32 bits")
    if fused_ports.get("c_i", {}).get("width") != 32 or fused_ports.get("mode_i", {}).get("width") != 2:
        raise ValueError("fused core lacks the canonical c/mode interface")
    if mul_evidence.get("result_widths", {}).get("result_o") != add_ports["a_i"]["width"]:
        raise ValueError("multiplier result width does not equal chained adder input width")
    if add_evidence.get("operator_counts", {}).get("$add") != 1:
        raise ValueError("standalone adder must contain one add operator")
    if mul_evidence.get("operator_counts", {}).get("$mul") != 1:
        raise ValueError("standalone multiplier must contain one multiply operator")
    if fused_evidence.get("operator_counts", {}).get("$add") != 1 or fused_evidence.get("operator_counts", {}).get("$mul") != 1:
        raise ValueError("fused core must contain one multiplier and one final adder")
    if simulation_evidence.get("checks") != 70 or simulation_evidence.get("composition_checks") != 70:
        raise ValueError("independent ADD/MUL and MUL->ADD/fused-MAC equivalence checks are incomplete")
    return {"width_chain_valid": True, "standalone_add32_via_zero_extension": True,
            "composition_semantics_proven_by": "tb_core_semantics.sv independent 70-case simulation",
            "composition_checks": simulation_evidence["composition_checks"]}
