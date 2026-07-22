import json
import tempfile
import unittest
from pathlib import Path

import verilator_flow


class VerilatorPlanTest(unittest.TestCase):
    def test_plan_is_complete_without_installed_verilator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rtl = root / "unit.sv"
            tb = root / "tb.sv"
            rtl.write_text("module unit; endmodule\n")
            tb.write_text("module tb; unit dut(); endmodule\n")
            plan = verilator_flow.create_plan(
                executable="/not/installed/verilator",
                rtl_sources=[rtl],
                testbench_sources=[tb],
                design_top="unit",
                testbench_top="tb",
                output_dir=root / "out",
                jobs=2,
                trace=True,
                defines=["DEMO_DUT_MODULE=unit"],
            )
            compile_response = Path(plan.compile_response_file).read_text()
            lint_response = Path(plan.lint_response_file).read_text()
            manifest = json.loads(Path(plan.manifest_file).read_text())

            self.assertIn("--binary", compile_response)
            self.assertIn("--timing", compile_response)
            self.assertIn("--trace-vcd", compile_response)
            self.assertIn("-DDEMO_DUT_MODULE=unit", compile_response)
            self.assertNotIn("-Wno-fatal", compile_response)
            self.assertIn("--lint-only", lint_response)
            self.assertEqual(manifest["integration"], "python_subprocess_cli")
            self.assertEqual(manifest["jobs"], 2)
            self.assertIn("+dump", plan.run_command)
            self.assertIn("+fu_seed=1", plan.run_command)
            self.assertEqual(plan.run_command[-1], "+verilator+seed+1")

    def test_negative_jobs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(verilator_flow.VerilatorFlowError):
                verilator_flow.create_plan(
                    executable="verilator",
                    rtl_sources=[],
                    testbench_sources=[],
                    design_top="unit",
                    testbench_top="tb",
                    output_dir=Path(directory),
                    jobs=-1,
                )


if __name__ == "__main__":
    unittest.main()
