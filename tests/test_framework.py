import tempfile
import sys
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import framework


class FrameworkContractTest(unittest.TestCase):
    def test_dispatches_both_operations(self):
        adapter = types.ModuleType("fake_adapter")
        adapter.simulateDesign = lambda **kwargs: {"status": "pass", "kwargs": kwargs}
        adapter.synthesizeDesign = lambda **kwargs: {"status": "pass", "kwargs": kwargs}

        simulation = framework.simulateDesign(adapter, simulator="fake")
        synthesis = framework.synthesizeDesign(adapter, synthesizer="fake")

        self.assertEqual(simulation["kwargs"]["simulator"], "fake")
        self.assertEqual(synthesis["kwargs"]["synthesizer"], "fake")

    def test_rejects_incomplete_adapter(self):
        adapter = types.ModuleType("incomplete_adapter")
        adapter.simulateDesign = lambda **kwargs: {"status": "pass"}
        with self.assertRaises(framework.FrameworkError):
            framework.simulateDesign(adapter)

    def test_rejects_result_without_status(self):
        adapter = types.ModuleType("bad_result_adapter")
        adapter.simulateDesign = lambda **kwargs: {}
        adapter.synthesizeDesign = lambda **kwargs: {"status": "pass"}
        with self.assertRaises(framework.FrameworkError):
            framework.simulateDesign(adapter)

    def test_rejects_unknown_status(self):
        adapter = types.ModuleType("unknown_status_adapter")
        adapter.simulateDesign = lambda **kwargs: {"status": "maybe"}
        adapter.synthesizeDesign = lambda **kwargs: {"status": "pass"}
        with self.assertRaises(framework.FrameworkError):
            framework.simulateDesign(adapter)

    def test_cli_returns_failure_for_structured_fail_result(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter_path = Path(directory) / "failing_adapter.py"
            adapter_path.write_text(
                "def simulateDesign(**kwargs):\n"
                "    return {'status': 'fail', 'operation': 'simulation'}\n"
                "def synthesizeDesign(**kwargs):\n"
                "    return {'status': 'pass', 'operation': 'synthesis'}\n"
            )
            with redirect_stdout(StringIO()):
                status = framework.main([
                    "simulate", "--adapter", str(adapter_path), "--json"
                ])
            self.assertEqual(status, 1)

    def test_path_adapter_supports_dataclass_and_sibling_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper_name = "adapter_contract_helper"
            (root / "{}.py".format(helper_name)).write_text("VALUE = 7\n")
            adapter_path = root / "dataclass_adapter.py"
            adapter_path.write_text(
                "from __future__ import annotations\n"
                "from dataclasses import dataclass\n"
                "from {} import VALUE\n".format(helper_name)
                + "@dataclass\n"
                "class Result:\n"
                "    value: int = VALUE\n"
                "def simulateDesign(**kwargs):\n"
                "    return {'status': 'pass', 'value': Result().value}\n"
                "def synthesizeDesign(**kwargs):\n"
                "    return {'status': 'pass'}\n"
            )
            result = framework.simulateDesign(str(adapter_path))
            self.assertEqual(result["value"], 7)
            sys.modules.pop(helper_name, None)


if __name__ == "__main__":
    unittest.main()
