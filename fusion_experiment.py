#!/usr/bin/env python3
"""Plan, run, and compare the demo ALU fusion variants.

The correctness run is immediately usable with Verilator. PPA comparison is
deliberately fail-closed: it accepts only fully validated adapter result files
created with identical tool/library/constraint fingerprints.
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import adapter
from toolchain import find_executable, select_cxx, sha256_file, source_manifest
from verilator_flow import VerilatorFlowError, create_plan, execute_plan


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "experiments" / "fusion_variants.json"


class FusionExperimentError(RuntimeError):
    """Raised when a variant plan or comparison lacks trustworthy evidence."""


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def load_manifest(path: Path = DEFAULT_MANIFEST) -> Tuple[Dict[str, Any], List[Path]]:
    manifest_path = path.expanduser().resolve()
    if not manifest_path.is_file():
        raise FusionExperimentError("Variant manifest does not exist: {}".format(manifest_path))
    data = json.loads(manifest_path.read_text())
    variants = data.get("variants")
    if not isinstance(variants, list) or not variants:
        raise FusionExperimentError("Variant manifest has no variants")

    names = set()
    ordered_sources: List[Path] = []
    for variant in variants:
        name = variant.get("name")
        top = variant.get("top")
        if not isinstance(name, str) or not re.match(r"^[a-z][a-z0-9_]+$", name):
            raise FusionExperimentError("Invalid variant name: {!r}".format(name))
        if name in names:
            raise FusionExperimentError("Duplicate variant name: {}".format(name))
        names.add(name)
        if not isinstance(top, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", top):
            raise FusionExperimentError("Invalid top for {}: {!r}".format(name, top))
        sources = variant.get("sources")
        if not isinstance(sources, list) or not sources:
            raise FusionExperimentError("Variant {} has no sources".format(name))
        for relative in sources:
            source = (ROOT / relative).resolve()
            if not source.is_file():
                raise FusionExperimentError("Variant source does not exist: {}".format(source))
            if source not in ordered_sources:
                ordered_sources.append(source)

    simulation = data.get("simulation", {})
    testbench = (ROOT / str(simulation.get("testbench", ""))).resolve()
    if not testbench.is_file():
        raise FusionExperimentError("Variant testbench does not exist: {}".format(testbench))
    data["manifest_file"] = str(manifest_path)
    data["resolved_sources"] = source_manifest(
        ordered_sources + [testbench, manifest_path, ROOT / "fusion_experiment.py"]
    )
    return data, ordered_sources


def make_verilator_plan(
    manifest_path: Path,
    output: Path,
    jobs: int,
    seed: int,
) -> Tuple[Dict[str, Any], Any]:
    manifest, rtl_sources = load_manifest(manifest_path)
    simulation = manifest["simulation"]
    testbench = (ROOT / simulation["testbench"]).resolve()
    cxx = select_cxx()
    executable = find_executable("verilator", "FU_VERILATOR") or "verilator"
    plan = create_plan(
        executable=executable,
        rtl_sources=rtl_sources,
        testbench_sources=[testbench],
        design_top="demo_alu",
        testbench_top=simulation["top"],
        output_dir=output,
        jobs=jobs,
        trace=True,
        cxx=cxx["path"] if cxx["coroutine_support"] else None,
        cxx_family=cxx["family"] if cxx["coroutine_support"] else None,
        seed=seed,
        lint_testbench=True,
    )
    resolved_path = output.resolve() / "fusion_experiment.json"
    _write_json(resolved_path, manifest)
    return manifest, plan


def run_correctness(
    manifest_path: Path,
    output: Path,
    jobs: int,
    seed: int,
) -> Dict[str, Any]:
    manifest, plan = make_verilator_plan(manifest_path, output, jobs, seed)
    cxx = select_cxx()
    if not cxx["coroutine_support"]:
        raise FusionExperimentError("No C++20 coroutine compiler is available")
    try:
        flow = execute_plan(plan)
    except VerilatorFlowError as exc:
        raise FusionExperimentError(str(exc))
    transcript = Path(flow["simulation_log"]).read_text(errors="replace")
    match = re.search(r"VARIANT_TEST_PASS\s+checks=(\d+)\s+variants=(\d+)", transcript)
    expected_checks = int(manifest["simulation"]["expected_checks"])
    expected_variants = int(manifest["simulation"]["expected_variants"])
    if not match or (int(match.group(1)), int(match.group(2))) != (
        expected_checks,
        expected_variants,
    ):
        raise FusionExperimentError(
            "Variant regression did not report the exact expected pass counts; see {}".format(
                flow["simulation_log"]
            )
        )
    result_path = output.resolve() / "variant_simulation.json"
    result = {
        "operation": "variant_simulation",
        "status": "pass",
        "checks": expected_checks,
        "variants": expected_variants,
        "seed": seed,
        "manifest_file": manifest["manifest_file"],
        "source_manifest": manifest["resolved_sources"],
        "result_file": str(result_path),
        **flow,
    }
    _write_json(result_path, result)
    return result


def _load_named_result(
    argument: str,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> Tuple[str, Dict[str, Any]]:
    if "=" not in argument:
        raise FusionExperimentError("Result must use NAME=/path/to/ppa.json: {}".format(argument))
    name, raw_path = argument.split("=", 1)
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise FusionExperimentError("PPA result does not exist: {}".format(path))
    result = json.loads(path.read_text())
    if result.get("status") != "pass" or result.get("ppa_validated") is not True:
        raise FusionExperimentError("{} is not a validated PPA result: {}".format(name, path))
    expected_variants = {
        variant["name"]: variant
        for variant in json.loads(manifest_path.expanduser().resolve().read_text())["variants"]
    }
    expected = expected_variants.get(name)
    if expected is None:
        raise FusionExperimentError(
            "Result alias {} is not declared in {}".format(
                name, manifest_path.expanduser().resolve()
            )
        )
    if result.get("design") != expected["top"]:
        raise FusionExperimentError(
            "Result alias {} expects design {}, found {}".format(
                name, expected["top"], result.get("design")
            )
        )
    recorded_sources = {
        item.get("relative_path"): item.get("sha256")
        for item in result.get("source_manifest") or []
    }
    source_mismatches = []
    for relative in expected["sources"]:
        expected_path = (ROOT / relative).resolve()
        if recorded_sources.get(relative) != sha256_file(expected_path):
            source_mismatches.append(relative)
    if source_mismatches:
        raise FusionExperimentError(
            "Result {} does not match manifest source hash(es): {}".format(
                name, ", ".join(source_mismatches)
            )
        )
    if expected.get("expected_locked_instances"):
        audit = result.get("hierarchy_audit") or {}
        audit_path = Path(str(audit.get("file", ""))).expanduser()
        if (
            audit.get("status") != "pass"
            or not audit_path.is_file()
            or audit.get("sha256") != sha256_file(audit_path)
        ):
            raise FusionExperimentError(
                "Locked result {} lacks a valid bound hierarchy audit".format(name)
            )
    result["_result_file"] = str(path)
    result["_result_sha256"] = sha256_file(path)
    return name, result


def _fingerprint(result: Dict[str, Any]) -> Dict[str, Any]:
    library = result.get("target_library_metadata") or {}
    common_flow_paths = {"adapter.py", "toolchain.py", "scripts/timing_opensta.tcl"}
    common_flow_hashes = {
        item.get("relative_path"): item.get("sha256")
        for item in result.get("source_manifest", [])
        if item.get("relative_path") in common_flow_paths
    }
    provenance = result.get("activity_provenance") or {}
    simulation_paths = {
        "adapter.py",
        "toolchain.py",
        "verilator_flow.py",
        "tb/tb_demo_alu.sv",
    }
    simulation_source_hashes = {
        item.get("relative_path"): item.get("sha256")
        for item in provenance.get("source_manifest") or []
        if item.get("relative_path") in simulation_paths
    }
    return {
        "yosys": result.get("tool_version"),
        "opensta": result.get("timing_power_tool_version"),
        "liberty_sha256": library.get("sha256"),
        "toolchain_lock_sha256": (result.get("toolchain_lock") or {}).get("sha256"),
        "clock_period_ns": result.get("clock_period_ns"),
        "constraints": result.get("constraints"),
        "activity_workload": result.get("activity_workload"),
        "activity_seed": result.get("activity_seed"),
        "activity_stimulus_interval_ns": result.get("activity_stimulus_interval_ns"),
        "activity_trace_sha256": result.get("activity_trace_sha256"),
        "activity_trace_end_timestamp": result.get("activity_trace_end_timestamp"),
        "activity_trace_timescale": result.get("activity_trace_timescale"),
        "simulation": {
            "simulator": provenance.get("simulator"),
            "simulator_version": provenance.get("simulator_version"),
            "cxx_version": provenance.get("cxx_version"),
            "testbench": provenance.get("testbench"),
            "checks": provenance.get("checks"),
            "seed": provenance.get("seed"),
            "activity_trace_sha256": provenance.get("activity_trace_sha256"),
            "activity_trace_end_timestamp": provenance.get("activity_trace_end_timestamp"),
            "activity_trace_timescale": provenance.get("activity_trace_timescale"),
            "source_hashes": simulation_source_hashes,
        },
        "common_flow_hashes": common_flow_hashes,
    }


def _metric(result: Dict[str, Any], name: str) -> float:
    value = (result.get("metrics", {}).get(name) or {}).get("value")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise FusionExperimentError("Validated result is missing numeric metric {}".format(name))
    return float(value)


def audit_locked_hierarchy(
    json_path: Path,
    top: str,
    expected_instances: Sequence[str],
) -> Dict[str, Any]:
    """Prove that a Yosys JSON snapshot retained each locked operation leaf."""

    path = json_path.expanduser().resolve()
    if not path.is_file():
        raise FusionExperimentError("Yosys structural JSON does not exist: {}".format(path))
    data = json.loads(path.read_text())
    modules = data.get("modules")
    if not isinstance(modules, dict):
        raise FusionExperimentError("Yosys JSON has no modules object: {}".format(path))
    top_matches = [
        module for name, module in modules.items()
        if name.lstrip("\\") == top
    ]
    if len(top_matches) != 1:
        raise FusionExperimentError(
            "Expected one top module {} in {}; found {}".format(top, path, len(top_matches))
        )
    cells = top_matches[0].get("cells") or {}
    audited = {}
    for expected in expected_instances:
        matches = [
            (name, cell) for name, cell in cells.items()
            if name.lstrip("\\") == expected
        ]
        if len(matches) != 1:
            raise FusionExperimentError(
                "Expected locked instance {} exactly once in {}; found {}".format(
                    expected, path, len(matches)
                )
            )
        cell_name, cell = matches[0]
        attributes = cell.get("attributes") or {}
        keep_value = str(attributes.get("keep_hierarchy", "0")).lower()
        keep_enabled = keep_value not in ("", "0", "false", "none") and "1" in keep_value
        if not keep_enabled:
            raise FusionExperimentError(
                "Instance {} lost keep_hierarchy in {}".format(expected, path)
            )
        cell_type = str(cell.get("type", "")).lstrip("\\")
        if not cell_type:
            raise FusionExperimentError("Instance {} has no cell type".format(expected))
        audited[expected] = {
            "json_cell_name": cell_name,
            "cell_type": cell_type,
            "keep_hierarchy": attributes.get("keep_hierarchy"),
        }
    return {
        "status": "pass",
        "json_file": str(path),
        "top": top,
        "expected_leaf_count": len(expected_instances),
        "audited_instances": audited,
    }


def compare_results(
    arguments: Sequence[str],
    manifest_path: Path = DEFAULT_MANIFEST,
) -> Dict[str, Any]:
    loaded = [_load_named_result(argument, manifest_path) for argument in arguments]
    if len({name for name, _result in loaded}) != len(loaded):
        raise FusionExperimentError("Each comparison result name must be unique")
    results = dict(loaded)
    if "separate_locked" not in results:
        raise FusionExperimentError("A separate_locked baseline result is required")
    if len(results) < 2:
        raise FusionExperimentError("At least one fused/control result is required")
    fingerprints = {name: _fingerprint(result) for name, result in results.items()}
    for name, fingerprint in fingerprints.items():
        required_values = (
            "yosys",
            "opensta",
            "liberty_sha256",
            "toolchain_lock_sha256",
            "clock_period_ns",
            "constraints",
            "activity_workload",
            "activity_seed",
            "activity_stimulus_interval_ns",
            "activity_trace_sha256",
            "activity_trace_end_timestamp",
            "activity_trace_timescale",
            "simulation",
        )
        missing = [key for key in required_values if fingerprint.get(key) in (None, "", {})]
        common_hashes = fingerprint.get("common_flow_hashes") or {}
        if set(common_hashes) != {
            "adapter.py",
            "toolchain.py",
            "scripts/timing_opensta.tcl",
        } or any(not digest for digest in common_hashes.values()):
            missing.append("common_flow_hashes")
        simulation = fingerprint.get("simulation") or {}
        simulation_required = (
            "simulator",
            "simulator_version",
            "cxx_version",
            "testbench",
            "checks",
            "seed",
            "activity_trace_sha256",
            "activity_trace_end_timestamp",
            "activity_trace_timescale",
        )
        if any(simulation.get(key) in (None, "", {}) for key in simulation_required):
            missing.append("simulation_provenance")
        source_hashes = simulation.get("source_hashes") or {}
        if set(source_hashes) != {
            "adapter.py",
            "toolchain.py",
            "verilator_flow.py",
            "tb/tb_demo_alu.sv",
        } or any(not digest for digest in source_hashes.values()):
            missing.append("simulation_source_hashes")
        if missing:
            raise FusionExperimentError(
                "{} lacks comparison fingerprint field(s): {}".format(
                    name, ", ".join(missing)
                )
            )
    baseline_fingerprint = fingerprints["separate_locked"]
    mismatches = [
        name for name, fingerprint in fingerprints.items()
        if fingerprint != baseline_fingerprint
    ]
    if mismatches:
        raise FusionExperimentError(
            "Comparison inputs do not share one tool/library/constraint fingerprint: {}".format(
                ", ".join(mismatches)
            )
        )

    baseline = results["separate_locked"]
    baseline_area = _metric(baseline, "area")
    baseline_delay = _metric(baseline, "critical_path_delay")
    baseline_power = _metric(baseline, "total_power")
    comparisons = {}
    for name, result in sorted(results.items()):
        area = _metric(result, "area")
        delay = _metric(result, "critical_path_delay")
        power = _metric(result, "total_power")
        comparisons[name] = {
            "area": area,
            "critical_path_delay_ns": delay,
            "total_power_mw": power,
            "area_saving_vs_separate_locked": (baseline_area - area) / baseline_area,
            "delay_penalty_vs_separate_locked": delay / baseline_delay - 1.0,
            "power_change_vs_separate_locked": power / baseline_power - 1.0,
        }
    return {
        "schema_version": 1,
        "baseline": "separate_locked",
        "fingerprint": baseline_fingerprint,
        "input_results": {
            name: {
                "path": result["_result_file"],
                "sha256": result["_result_sha256"],
                "design": result.get("design"),
            }
            for name, result in sorted(results.items())
        },
        "comparisons": comparisons,
        "caveat": "Pre-layout screening only; normalize dedicated-parallel capacity before conclusions.",
    }


def evaluate_variants(
    manifest_path: Path,
    output: Path,
    target_library: str,
    clock_period: float,
    jobs: int,
    seed: int,
    toolchain_lock: str,
) -> Dict[str, Any]:
    """Run matched simulation and PPA for every selected-interface variant."""

    manifest, _sources = load_manifest(manifest_path)
    library = Path(target_library).expanduser().resolve()
    if not library.is_file():
        raise FusionExperimentError("Target Liberty does not exist: {}".format(library))
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    for stale_name in ("comparison.json", "evaluation.json"):
        stale = output / stale_name
        if stale.exists():
            stale.unlink()
    pending_path = output / "evaluation.pending.json"
    _write_json(pending_path, {
        "operation": "fusion_evaluation",
        "status": "running",
        "manifest_file": manifest["manifest_file"],
        "target_library": str(library),
        "clock_period_ns": clock_period,
        "seed": seed,
    })
    ppa_arguments = []
    variant_results = {}
    for variant in manifest["variants"]:
        if variant.get("ppa_eligible") is False:
            continue
        name = variant["name"]
        design_top = variant["top"]
        sources = [str((ROOT / path).resolve()) for path in variant["sources"]]
        simulation = adapter.simulateDesign(
            simulator="verilator",
            build_dir=str(output / name / "simulation"),
            clean=True,
            jobs=jobs,
            seed=seed,
            design_top=design_top,
            rtl_sources=sources,
            verilator_defines=["DEMO_DUT_MODULE={}".format(design_top)],
        )
        synthesis = adapter.synthesizeDesign(
            synthesizer="yosys",
            target_library=str(library),
            clock_period=clock_period,
            activity_vcd=simulation["activity_vcd"],
            build_dir=str(output / name / "synthesis"),
            clean=True,
            design_top=design_top,
            rtl_sources=sources,
            activity_seed=seed,
            activity_provenance=adapter.simulation_provenance(simulation),
            toolchain_lock=toolchain_lock,
        )
        variant_results[name] = {
            "simulation": simulation["result_file"],
            "ppa": synthesis["result_file"],
        }
        expected_instances = variant.get("expected_locked_instances")
        if expected_instances:
            audits = {
                stage: audit_locked_hierarchy(
                    output / name / "synthesis" / "{}.json".format(stage),
                    design_top,
                    expected_instances,
                )
                for stage in ("coarse", "mapped")
            }
            audit_path = output / name / "synthesis" / "hierarchy_audit.json"
            audit_document = {"status": "pass", "stages": audits}
            _write_json(audit_path, audit_document)
            synthesis["hierarchy_audit"] = {
                "status": "pass",
                "file": str(audit_path),
                "sha256": sha256_file(audit_path),
            }
            _write_json(Path(synthesis["result_file"]), synthesis)
            variant_results[name]["hierarchy_audit"] = str(audit_path)
        ppa_arguments.append("{}={}".format(name, synthesis["result_file"]))

    comparison = compare_results(ppa_arguments, manifest_path)
    comparison_path = output / "comparison.json"
    _write_json(comparison_path, comparison)
    result_path = output / "evaluation.json"
    result = {
        "operation": "fusion_evaluation",
        "status": "pass",
        "manifest_file": manifest["manifest_file"],
        "target_library": str(library),
        "clock_period_ns": clock_period,
        "seed": seed,
        "variant_results": variant_results,
        "comparison_file": str(comparison_path),
        "excluded_from_selected_interface_ppa": [
            variant["name"] for variant in manifest["variants"]
            if variant.get("ppa_eligible") is False
        ],
        "result_file": str(result_path),
    }
    _write_json(result_path, result)
    if pending_path.exists():
        pending_path.unlink()
    return result


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "run", "analyze", "evaluate"))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output", default="build/fusion_variants")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--target-library")
    parser.add_argument("--clock-period", type=float, default=2.0)
    parser.add_argument("--toolchain-lock", default="toolchain.lock.json")
    parser.add_argument(
        "--result",
        action="append",
        default=[],
        help="For analyze: NAME=/path/to/validated/ppa.json (repeatable)",
    )
    args = parser.parse_args(argv)
    try:
        if args.action == "analyze":
            comparison = compare_results(args.result, Path(args.manifest))
            output = Path(args.output).expanduser().resolve() / "comparison.json"
            _write_json(output, comparison)
            print(str(output))
        elif args.action == "evaluate":
            if not args.target_library:
                raise FusionExperimentError("--target-library is required for evaluate")
            result = evaluate_variants(
                Path(args.manifest),
                Path(args.output),
                args.target_library,
                args.clock_period,
                args.jobs,
                args.seed,
                args.toolchain_lock,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            if args.action == "run":
                result = run_correctness(
                    Path(args.manifest), Path(args.output), args.jobs, args.seed
                )
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                _manifest, plan = make_verilator_plan(
                    Path(args.manifest), Path(args.output), args.jobs, args.seed
                )
                print(plan.manifest_file)
    except (
        FusionExperimentError,
        VerilatorFlowError,
        adapter.AdapterError,
        ValueError,
        OSError,
    ) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
