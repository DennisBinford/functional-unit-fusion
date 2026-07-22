import json
import tempfile
import unittest
from pathlib import Path

import fusion_experiment


class FusionExperimentTest(unittest.TestCase):
    def test_manifest_and_verilator_plan_resolve_every_variant(self):
        manifest, sources = fusion_experiment.load_manifest()
        self.assertEqual(len(manifest["variants"]), 6)
        self.assertGreaterEqual(len(sources), 6)
        with tempfile.TemporaryDirectory() as directory:
            _manifest, plan = fusion_experiment.make_verilator_plan(
                fusion_experiment.DEFAULT_MANIFEST,
                Path(directory),
                jobs=1,
                seed=1,
            )
            self.assertEqual(plan.testbench_top, "tb_demo_variants")
            self.assertTrue(plan.lint_testbench)
            self.assertIn("tb_demo_variants", Path(plan.lint_response_file).read_text())
            self.assertTrue(Path(plan.compile_response_file).is_file())

    def test_comparison_uses_locked_baseline(self):
        def result(area, delay, power, design):
            manifest = json.loads(fusion_experiment.DEFAULT_MANIFEST.read_text())
            variant = next(item for item in manifest["variants"] if item["top"] == design)
            design_sources = [
                {
                    "relative_path": relative,
                    "sha256": fusion_experiment.sha256_file(
                        fusion_experiment.ROOT / relative
                    ),
                }
                for relative in variant["sources"]
            ]
            return {
                "status": "pass",
                "ppa_validated": True,
                "design": design,
                "tool_version": "Yosys test",
                "timing_power_tool_version": "OpenSTA test",
                "target_library_metadata": {"sha256": "abc"},
                "toolchain_lock": {"status": "pass", "sha256": "lock"},
                "clock_period_ns": 2.0,
                "constraints": {"load": 0.05},
                "activity_stimulus_interval_ns": 1.0,
                "activity_trace_sha256": "primary-input-trace",
                "activity_trace_end_timestamp": 1757,
                "activity_trace_timescale": "1ns",
                "activity_workload": "regression",
                "activity_seed": 1,
                "source_manifest": design_sources + [
                    {"relative_path": "adapter.py", "sha256": "flow"},
                    {"relative_path": "toolchain.py", "sha256": "tools"},
                    {"relative_path": "scripts/timing_opensta.tcl", "sha256": "sta"},
                ],
                "activity_provenance": {
                    "simulator": "verilator",
                    "simulator_version": "Verilator test",
                    "cxx_version": "clang test",
                    "testbench": "tb_demo_alu",
                    "checks": 1757,
                    "seed": 1,
                    "activity_trace_sha256": "primary-input-trace",
                    "activity_trace_end_timestamp": 1757,
                    "activity_trace_timescale": "1ns",
                    "source_manifest": [
                        {"relative_path": "adapter.py", "sha256": "flow"},
                        {"relative_path": "toolchain.py", "sha256": "tools"},
                        {"relative_path": "verilator_flow.py", "sha256": "verilator-flow"},
                        {"relative_path": "tb/tb_demo_alu.sv", "sha256": "testbench"},
                    ],
                },
                "metrics": {
                    "area": {"value": area},
                    "critical_path_delay": {"value": delay},
                    "total_power": {"value": power},
                },
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locked = root / "locked.json"
            fused = root / "fused.json"
            audit = root / "hierarchy_audit.json"
            audit.write_text(json.dumps({"status": "pass"}))
            locked_result = result(
                100.0, 1.0, 2.0, "demo_alu_separate_locked"
            )
            locked_result["hierarchy_audit"] = {
                "status": "pass",
                "file": str(audit),
                "sha256": fusion_experiment.sha256_file(audit),
            }
            locked.write_text(json.dumps(locked_result))
            fused.write_text(json.dumps(result(
                75.0, 1.1, 1.5, "demo_alu_manual_fused"
            )))
            comparison = fusion_experiment.compare_results([
                "separate_locked={}".format(locked),
                "fused_manual={}".format(fused),
            ])
            metrics = comparison["comparisons"]["fused_manual"]
            self.assertAlmostEqual(metrics["area_saving_vs_separate_locked"], 0.25)
            self.assertAlmostEqual(metrics["delay_penalty_vs_separate_locked"], 0.10)

    def test_locked_hierarchy_audit_requires_attribute(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coarse.json"
            path.write_text(json.dumps({
                "modules": {
                    "demo_alu_separate_locked": {
                        "cells": {
                            "u_add": {
                                "type": "$paramod\\demo_op_add",
                                "attributes": {"keep_hierarchy": "1"},
                            }
                        }
                    }
                }
            }))
            audit = fusion_experiment.audit_locked_hierarchy(
                path, "demo_alu_separate_locked", ["u_add"]
            )
            self.assertEqual(audit["expected_leaf_count"], 1)
            self.assertEqual(audit["status"], "pass")

    def test_comparison_rejects_unfingerprinted_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.json"
            path.write_text(json.dumps({
                "status": "pass",
                "ppa_validated": True,
                "metrics": {
                    "area": {"value": 1.0},
                    "critical_path_delay": {"value": 1.0},
                    "total_power": {"value": 1.0},
                },
            }))
            with self.assertRaises(fusion_experiment.FusionExperimentError):
                fusion_experiment.compare_results([
                    "separate_locked={}".format(path),
                    "fused_auto={}".format(path),
                ])

    def test_comparison_rejects_alias_not_declared_by_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps({
                "status": "pass",
                "ppa_validated": True,
                "design": "unbound_design",
            }))
            with self.assertRaisesRegex(
                fusion_experiment.FusionExperimentError,
                "not declared",
            ):
                fusion_experiment.compare_results([
                    "unknown_candidate={}".format(path),
                    "separate_locked={}".format(path),
                ])


if __name__ == "__main__":
    unittest.main()
