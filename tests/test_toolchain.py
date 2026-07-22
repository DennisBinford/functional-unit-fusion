import tempfile
import unittest
from pathlib import Path
from unittest import mock

import toolchain


class ToolchainTest(unittest.TestCase):
    def test_liberty_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            liberty = Path(directory) / "example.lib"
            liberty.write_text(
                'library (example_tt) {\n'
                '  time_unit : "1ns";\n'
                '  voltage_unit : "1V";\n'
                '}\n'
            )
            metadata = toolchain.inspect_liberty(str(liberty))
            self.assertTrue(metadata["exists"])
            self.assertEqual(metadata["library"], "example_tt")
            self.assertEqual(metadata["time_unit"], "1ns")
            self.assertTrue(metadata["time_unit_is_1ns"])
            self.assertEqual(len(metadata["sha256"]), 64)

    def test_source_manifest_hashes_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "design.sv"
            source.write_text("module design; endmodule\n")
            manifest = toolchain.source_manifest([source], root=root)
            self.assertEqual(manifest[0]["relative_path"], "design.sv")
            self.assertEqual(manifest[0]["size_bytes"], source.stat().st_size)
            self.assertEqual(manifest[0]["sha256"], toolchain.sha256_file(source))

    def test_snapshot_declares_open_source_policy(self):
        data = toolchain.snapshot()
        self.assertEqual(data["policy"], "open_source_tools_only")
        self.assertIn("simulation_ready", data["checks"])

    def test_lock_round_trip_uses_stable_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            liberty = root / "cells.lib"
            liberty.write_text('library(test) { time_unit : "1ns"; }\n')
            data = toolchain.snapshot(str(liberty))
            for check in (
                "simulation_ready",
                "synthesis_ready",
                "liberty_ready",
                "timing_power_ready",
            ):
                data["checks"][check] = True
            lock_path = root / "toolchain.lock.json"
            with mock.patch("toolchain.snapshot", return_value=data):
                toolchain.create_lock(str(liberty), lock_path)
                verification = toolchain.verify_lock(lock_path, str(liberty))
            self.assertEqual(verification["status"], "pass")
            self.assertEqual(len(verification["lock_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
