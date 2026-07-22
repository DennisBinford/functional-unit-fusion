#!/usr/bin/env python3
"""Tool-agnostic entry point for functional-unit evaluation.

Design-specific knowledge intentionally lives in an adapter module.  The two
public functions in this file are the stable API used by scripts, notebooks,
and future experiment runners.
"""

import argparse
import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Union


class FrameworkError(RuntimeError):
    """Raised when an adapter does not satisfy the framework contract."""


AdapterReference = Union[str, ModuleType]


def _load_adapter(adapter: AdapterReference) -> ModuleType:
    if isinstance(adapter, ModuleType):
        module = adapter
    elif isinstance(adapter, str) and (adapter.endswith(".py") or "/" in adapter):
        path = Path(adapter).expanduser().resolve()
        if not path.is_file():
            raise FrameworkError("Adapter file does not exist: {}".format(path))
        module_name = "_fu_design_adapter_{}".format(
            hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        )
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            raise FrameworkError("Could not import adapter file: {}".format(path))
        module = importlib.util.module_from_spec(spec)
        previous_module = sys.modules.get(module_name)
        sys.modules[module_name] = module
        adapter_directory = str(path.parent)
        inserted_path = adapter_directory not in sys.path
        if inserted_path:
            sys.path.insert(0, adapter_directory)
        try:
            spec.loader.exec_module(module)
        except BaseException:
            if previous_module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous_module
            raise
        finally:
            if inserted_path:
                try:
                    sys.path.remove(adapter_directory)
                except ValueError:
                    pass
    elif isinstance(adapter, str):
        module = importlib.import_module(adapter)
    else:
        raise FrameworkError("adapter must be a module name, file path, or module")

    missing = [
        name for name in ("simulateDesign", "synthesizeDesign")
        if not callable(getattr(module, name, None))
    ]
    if missing:
        raise FrameworkError(
            "Adapter {} is missing callable(s): {}".format(
                getattr(module, "__name__", repr(module)), ", ".join(missing)
            )
        )
    return module


def _validate_result(operation: str, result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        raise FrameworkError(
            "Adapter {} must return a dictionary, got {}".format(
                operation, type(result).__name__
            )
        )
    if "status" not in result:
        raise FrameworkError("Adapter {} result has no 'status' field".format(operation))
    if result["status"] not in ("pass", "fail"):
        raise FrameworkError(
            "Adapter {} result has unsupported status {!r}".format(
                operation, result["status"]
            )
        )
    return result


def simulateDesign(adapter: AdapterReference = "adapter", **kwargs: Any) -> Dict[str, Any]:
    """Simulate a design through its adapter and return structured results."""

    module = _load_adapter(adapter)
    return _validate_result("simulateDesign", module.simulateDesign(**kwargs))


def synthesizeDesign(adapter: AdapterReference = "adapter", **kwargs: Any) -> Dict[str, Any]:
    """Synthesize a design through its adapter and return structured PPA data."""

    module = _load_adapter(adapter)
    return _validate_result("synthesizeDesign", module.synthesizeDesign(**kwargs))


# Snake-case aliases make the API comfortable in new Python code while keeping
# the exact function names requested for the prototype.
simulate_design = simulateDesign
synthesize_design = synthesizeDesign


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a functional-unit design through a Python adapter."
    )
    parser.add_argument("action", choices=("simulate", "synthesize", "all"))
    parser.add_argument(
        "--adapter",
        default="adapter",
        help="Adapter module name or path to an adapter.py file (default: adapter)",
    )
    parser.add_argument(
        "--simulator",
        default="auto",
        choices=("auto", "verilator"),
    )
    parser.add_argument(
        "--synthesizer",
        default="auto",
        choices=("auto", "yosys", "siliconcompiler"),
    )
    parser.add_argument(
        "--target-library",
        help="Standard-cell Liberty (.lib) used by Yosys and OpenSTA",
    )
    parser.add_argument(
        "--clock-period",
        type=float,
        default=2.0,
        help="Virtual I/O timing constraint in ns (default: 2.0)",
    )
    parser.add_argument(
        "--toolchain-lock",
        default="toolchain.lock.json",
        help="Environment lock required for Liberty-based PPA",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Verilator build parallelism; 0 uses all available cores (default: 1)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=1,
        help="Verilator runtime seed for repeatable random stimulus (default: 1)",
    )
    parser.add_argument("--clean", action="store_true", help="Remove the selected build output first")
    parser.add_argument("--json", action="store_true", help="Print full JSON results")
    return parser


def _brief(result: Dict[str, Any]) -> str:
    operation = result.get("operation", "run")
    tool = result.get("tool", "unknown")
    status = result.get("status", "unknown")
    path = result.get("result_file") or result.get("log", "")
    return "{}: {} using {}{}".format(
        operation,
        status,
        tool,
        " ({})".format(path) if path else "",
    )


def _activity_provenance(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "simulator": result.get("tool"),
        "simulator_version": result.get("tool_version"),
        "cxx_version": result.get("cxx_version"),
        "testbench": result.get("testbench"),
        "checks": result.get("checks"),
        "seed": result.get("seed"),
        "source_manifest": result.get("source_manifest"),
        "activity_trace_sha256": result.get("activity_trace_sha256"),
        "activity_trace_end_timestamp": result.get("activity_trace_end_timestamp"),
        "activity_trace_timescale": result.get("activity_trace_timescale"),
    }


def main(argv: Any = None) -> int:
    args = _build_parser().parse_args(argv)
    results = []
    simulation_result = None
    try:
        if args.action in ("simulate", "all"):
            simulation_result = simulateDesign(
                    args.adapter,
                    simulator=args.simulator,
                    clean=args.clean,
                    jobs=args.jobs,
                    seed=args.seed,
                )
            results.append(simulation_result)
        if args.action in ("synthesize", "all"):
            results.append(
                synthesizeDesign(
                    args.adapter,
                    synthesizer=args.synthesizer,
                    target_library=args.target_library,
                    clock_period=args.clock_period,
                    activity_vcd=(
                        simulation_result.get("activity_vcd")
                        if simulation_result else None
                    ),
                    activity_seed=args.seed if args.action == "all" else None,
                    activity_provenance=(
                        _activity_provenance(simulation_result)
                        if simulation_result else None
                    ),
                    toolchain_lock=args.toolchain_lock,
                    clean=args.clean,
                )
            )
    except Exception as exc:  # Keep command-line failures concise; logs hold detail.
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(results[0] if len(results) == 1 else results, indent=2, sort_keys=True))
    else:
        for result in results:
            print(_brief(result))
    return 0 if all(result.get("status") == "pass" for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
