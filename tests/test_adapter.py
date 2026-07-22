import json
import tempfile
import unittest
from pathlib import Path

import adapter


class ReportParsingTest(unittest.TestCase):
    @staticmethod
    def _valid_ppa_metrics():
        return {
            "area": {"value": 100.0},
            "cell_count": {"value": 42},
            "critical_path_delay": {"value": 1.7},
            "worst_data_arrival_time": {"value": 1.9},
            "slack": {"value": -0.1},
            "total_power": {"value": 0.25},
            "power_report_unit": {"value": "W"},
            "internal_power": {"value": 0.10},
            "switching_power": {"value": 0.10},
            "leakage_power": {"value": 0.05},
            "timing_paths_reported": {"value": 1},
            "timing_consistency_delta": {"value": 0.0},
            "activity_annotation": {"missing_primary_input_pins": []},
        }

    def test_yosys_report_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "yosys.log"
            log.write_text(
                "Number of cells: 42\n"
                "Chip area for module '\\demo_alu': 123.50\n"
            )
            metrics = adapter._parse_yosys_metrics(log, True)
            self.assertEqual(metrics["area"]["value"], 123.5)
            self.assertEqual(metrics["cell_count"]["value"], 42)

    def test_yosys_hierarchical_stat_json_is_authoritative(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "yosys.log"
            stats = root / "stats.json"
            log.write_text("Number of cells: 1\nChip area for module 'leaf': 2.0\n")
            stats.write_text(json.dumps({
                "modules": {
                    "demo_alu": {
                        "num_cells": {
                            "count": "42",
                            "area": "123.500000",
                            "local_count": "3",
                            "local_area": "4.0",
                        },
                        "num_cells_by_type": {"NAND2_X1": {"count": "42"}},
                    }
                }
            }))
            metrics = adapter._parse_yosys_metrics(
                log, True, stats_path=stats, design_top="demo_alu"
            )
            self.assertEqual(metrics["area"]["value"], 123.5)
            self.assertEqual(metrics["cell_count"]["value"], 42)

    def test_opensta_report_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "timing.rpt").write_text(
                "       1.25   data arrival time\n"
                "      -1.25   data arrival time\n"
                "       0.55   slack (MET)\n"
            )
            (output / "slack.rpt").write_text("worst slack 0.55\n")
            (output / "power.rpt").write_text(
                "Internal Switching Leakage Total (Watts)\n"
                "Total 1.0e-04 2.0e-04 3.0e-05 3.3e-04 100.0%\n"
            )
            metrics = adapter._parse_opensta_metrics(output, clock_period=2.0)
            self.assertAlmostEqual(metrics["critical_path_delay"]["value"], 1.05)
            self.assertEqual(metrics["worst_data_arrival_time"]["value"], 1.25)
            self.assertEqual(metrics["slack"]["value"], 0.55)
            self.assertTrue(metrics["timing_met"]["value"])
            self.assertAlmostEqual(metrics["total_power"]["value"], 0.33)

    def test_vcd_scope_uses_verilator_hierarchy(self):
        with tempfile.TemporaryDirectory() as directory:
            vcd = Path(directory) / "activity.vcd"
            vcd.write_text(
                "$scope module TOP $end\n"
                "$scope module tb_demo_alu $end\n"
                "$scope module dut $end\n"
                "$var wire 1 ! a_i $end\n"
                "$upscope $end\n"
                "$upscope $end\n"
                "$upscope $end\n"
                "$enddefinitions $end\n"
            )
            self.assertEqual(adapter._find_vcd_scope(vcd), "TOP/tb_demo_alu/dut")

    def test_primary_input_trace_ignores_vcd_symbol_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = (
                "$scope module TOP $end\n"
                "$scope module tb_demo_alu $end\n"
                "$scope module dut $end\n"
                "$var wire 32 {a} a_i [31:0] $end\n"
                "$var wire 32 {b} b_i [31:0] $end\n"
                "$var wire 3 {op} op_i [2:0] $end\n"
                "$upscope $end\n$upscope $end\n$upscope $end\n"
                "$enddefinitions $end\n"
                "#0\nb0 {a}\nb1 {b}\nb0 {op}\n"
                "#1\nb10 {a}\nb11 {b}\nb1 {op}\n"
            )
            first = root / "first.vcd"
            second = root / "second.vcd"
            first.write_text(template.format(a="!", b='"', op="#"))
            second.write_text(template.format(a="A", b="B", op="C"))
            scope = "TOP/tb_demo_alu/dut"
            first_trace = adapter._primary_input_trace(first, scope)
            second_trace = adapter._primary_input_trace(second, scope)
            self.assertEqual(first_trace["sha256"], second_trace["sha256"])
            self.assertEqual(first_trace["event_count"], 6)
            filtered = adapter._write_primary_input_vcd(
                first, root / "primary.vcd", scope
            )
            self.assertEqual(filtered["policy"], "primary_inputs_only")
            self.assertEqual(filtered["trace"]["sha256"], first_trace["sha256"])

    def test_primary_input_trace_ignores_same_timestamp_signal_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            header = (
                "$scope module TOP $end\n"
                "$scope module tb_demo_alu $end\n"
                "$scope module dut $end\n"
                "$var wire 32 ! a_i [31:0] $end\n"
                '$var wire 32 " b_i [31:0] $end\n'
                "$var wire 3 # op_i [2:0] $end\n"
                "$upscope $end\n$upscope $end\n$upscope $end\n"
                "$enddefinitions $end\n"
            )
            first = root / "first.vcd"
            second = root / "second.vcd"
            first.write_text(
                header + '#0\nb0 !\nb1 "\nb0 #\n#1\nb10 !\nb11 "\nb1 #\n'
            )
            second.write_text(
                header + '#0\nb0 #\nb1 "\nb0 !\n#1\nb1 #\nb10 !\nb11 "\n'
            )
            scope = "TOP/tb_demo_alu/dut"
            first_trace = adapter._primary_input_trace(first, scope)
            second_trace = adapter._primary_input_trace(second, scope)
            self.assertEqual(first_trace["sha256"], second_trace["sha256"])
            self.assertEqual(first_trace["event_count"], 6)

    def test_primary_input_vcd_preserves_final_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.vcd"
            source.write_text(
                "$timescale 1ns $end\n"
                "$scope module TOP $end\n"
                "$scope module tb_demo_alu $end\n"
                "$scope module dut $end\n"
                "$var wire 32 ! a_i [31:0] $end\n"
                '$var wire 32 " b_i [31:0] $end\n'
                "$var wire 3 # op_i [2:0] $end\n"
                "$upscope $end\n$upscope $end\n$upscope $end\n"
                "$enddefinitions $end\n"
                '#0\nb0 !\nb0 "\nb0 #\n#5\nb1 !\n#9\n'
            )
            scope = "TOP/tb_demo_alu/dut"
            filtered = adapter._write_primary_input_vcd(
                source, root / "filtered.vcd", scope
            )
            self.assertTrue((root / "filtered.vcd").read_text().endswith("#9\n"))
            self.assertEqual(filtered["trace"]["end_timestamp"], 9)
            self.assertEqual(filtered["trace"]["timescale"], "1ns")

    def test_opensta_numbered_diagnostics_are_rejected(self):
        diagnostics = adapter._opensta_diagnostics(
            "Warning: plain warning\n"
            "Warning 198: numbered warning\n"
            "Error [STA-001]: tagged error\n"
            "ordinary report text\n"
        )
        self.assertEqual(len(diagnostics), 3)

    def test_yosys_script_has_mapping_and_netlist(self):
        with tempfile.TemporaryDirectory() as directory:
            liberty = Path(directory) / "cells.lib"
            liberty.write_text("library(test) {}\n")
            script = adapter._write_yosys_script(Path(directory), str(liberty))
            text = script.read_text()
            self.assertIn("synth -top demo_alu -flatten -noabc", text)
            self.assertIn("abc -liberty", text)
            self.assertIn("check -mapped -assert", text)
            self.assertIn("write_json", text)
            self.assertIn("write_verilog", text)

    def test_yosys_script_accepts_a_variant_top(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "variant.sv"
            source.write_text("module variant; endmodule\n")
            script = adapter._write_yosys_script(
                root,
                None,
                rtl_sources=[source],
                design_top="variant",
                netlist_name="variant_netlist.v",
            )
            text = script.read_text()
            self.assertIn("hierarchy -check -top variant", text)
            self.assertIn("variant_netlist.v", text)

    def test_activity_annotation_requires_all_primary_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "activity_annotation.rpt"
            pins = sorted(adapter.EXPECTED_ACTIVITY_INPUT_PINS)
            report.write_text(
                "vcd {:5d}\nunannotated     0\nAnnotated pins:\n{}\n".format(
                    len(pins), "\n".join("  vcd {}".format(pin) for pin in pins)
                )
            )
            parsed = adapter._parse_activity_annotation(report)
            self.assertEqual(parsed["summary_vcd_pins"], 67)
            self.assertEqual(parsed["missing_primary_input_pins"], [])
            self.assertEqual(parsed["primary_input_annotation_fraction"], 1.0)

    def test_ppa_validation_accepts_a_real_timing_miss(self):
        # Negative slack is a valid measured result, not missing evidence.
        adapter._validate_ppa_metrics(self._valid_ppa_metrics())

    def test_ppa_validation_rejects_missing_activity(self):
        metrics = self._valid_ppa_metrics()
        metrics["activity_annotation"]["missing_primary_input_pins"] = ["op_i[2]"]
        with self.assertRaises(adapter.AdapterError):
            adapter._validate_ppa_metrics(metrics)

    def test_ppa_validation_rejects_leakage_only_power(self):
        metrics = self._valid_ppa_metrics()
        metrics["internal_power"]["value"] = 0.0
        metrics["switching_power"]["value"] = 0.0
        metrics["leakage_power"]["value"] = 0.25
        with self.assertRaises(adapter.AdapterError):
            adapter._validate_ppa_metrics(metrics)


if __name__ == "__main__":
    unittest.main()
