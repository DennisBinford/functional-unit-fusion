#!/usr/bin/env python3
"""Plan and execute the project's Verilator simulation flow.

Python does not link to a Verilator Python API here.  It invokes Verilator's
stable command-line interface with subprocess, preserves the generated response
files and command manifest, then runs the executable produced by ``--binary``.
The ``plan`` action works even when Verilator is not installed.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from toolchain import find_executable, select_cxx, tool_version


ROOT = Path(__file__).resolve().parent


class VerilatorFlowError(RuntimeError):
    """Raised when lint, compilation, or simulation fails."""


@dataclass
class VerilatorPlan:
    executable: str
    cxx: Optional[str]
    cxx_family: Optional[str]
    design_top: str
    testbench_top: str
    rtl_sources: List[str]
    testbench_sources: List[str]
    output_dir: str
    object_dir: str
    binary: str
    lint_response_file: str
    compile_response_file: str
    manifest_file: str
    lint_log: str
    compile_log: str
    simulation_log: str
    jobs: int
    seed: int
    trace: bool
    defines: List[str]
    lint_testbench: bool
    lint_command: List[str]
    compile_command: List[str]
    run_command: List[str]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.update({
            "schema_version": 1,
            "backend": "verilator",
            "integration": "python_subprocess_cli",
            "mode": "systemverilog_testbench_via_binary",
            "option_rationale": {
                "--binary": "Generate C++ and build a standalone simulation executable.",
                "--timing": "Implement SystemVerilog delays/event timing used by the testbench.",
                "--assert": "Compile SystemVerilog assertions and unique/priority checks.",
                "--trace-vcd": "Compile VCD waveform support used for activity-based power.",
                "--Mdir": "Keep all generated C++ and build artifacts in the run directory.",
                "--top-module": "Select the self-checking SystemVerilog testbench explicitly.",
                "-f": "Read a preserved response file rather than construct an opaque long command.",
                "-j": "Bound Verilation/build parallelism for reproducible resource use.",
                "-Wall": "Enable Verilator's stronger lint/style warning set; warnings remain fatal.",
                "-MAKEFLAGS": "Select the recorded C++20 compiler for Verilator's generated build.",
                "+verilator+seed": "Pin Verilator runtime random initialization for reproducibility.",
                "+fu_seed": "Seed the testbench's explicit deterministic xorshift32 generator.",
            },
            "official_documentation": {
                "binary_example": "https://verilator.org/guide/latest/example_binary.html",
                "verilating": "https://verilator.org/guide/latest/verilating.html",
                "arguments": "https://verilator.org/guide/latest/exe_verilator.html",
                "files": "https://verilator.org/guide/latest/files.html",
            },
        })
        return data


def _response_argument(argument: str) -> str:
    """Quote an argument for Verilator's shell-like -f file parser."""

    if re.search(r"[\s#'\"]", argument):
        return '"{}"'.format(argument.replace("\\", "\\\\").replace('"', '\\"'))
    return argument


def _write_response(path: Path, arguments: Sequence[str]) -> None:
    path.write_text("\n".join(_response_argument(str(argument)) for argument in arguments) + "\n")


def create_plan(
    executable: str,
    rtl_sources: Sequence[Path],
    testbench_sources: Sequence[Path],
    design_top: str,
    testbench_top: str,
    output_dir: Path,
    jobs: int = 1,
    trace: bool = True,
    cxx: Optional[str] = None,
    cxx_family: Optional[str] = None,
    seed: int = 1,
    defines: Optional[Sequence[str]] = None,
    lint_testbench: bool = False,
) -> VerilatorPlan:
    """Write Verilator response files and a machine-readable command manifest."""

    if jobs < 0:
        raise VerilatorFlowError("jobs must be zero (all cores) or a positive integer")
    if seed < 0:
        raise VerilatorFlowError("seed must be a non-negative integer")
    selected_defines = list(defines or [])
    invalid_defines = [
        define for define in selected_defines
        if not re.match(
            r"^[A-Za-z_][A-Za-z0-9_]*(?:=[A-Za-z_][A-Za-z0-9_$]*)?$",
            define,
        )
    ]
    if invalid_defines:
        raise VerilatorFlowError(
            "invalid preprocessor define(s): {}".format(", ".join(invalid_defines))
        )
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", design_top):
        raise VerilatorFlowError("invalid design top module: {}".format(design_top))
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", testbench_top):
        raise VerilatorFlowError("invalid testbench top module: {}".format(testbench_top))
    output = output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    object_dir = output / "obj_dir"
    prefix = "V" + testbench_top
    binary = object_dir / prefix
    lint_response = output / "verilator_lint.f"
    compile_response = output / "verilator_compile.f"
    manifest = output / "verilator_command.json"

    rtl = [str(Path(source).resolve()) for source in rtl_sources]
    testbench = [str(Path(source).resolve()) for source in testbench_sources]
    if not rtl:
        raise VerilatorFlowError("at least one RTL source is required")
    if not testbench:
        raise VerilatorFlowError("at least one testbench source is required")
    missing_sources = [source for source in rtl + testbench if not Path(source).is_file()]
    if missing_sources:
        raise VerilatorFlowError(
            "source file(s) do not exist: {}".format(", ".join(missing_sources))
        )
    common = [
        "--language", "1800-2017",
        "--timing",
        "--assert",
        "-Wall",
    ] + ["-D{}".format(define) for define in selected_defines]
    lint_top = testbench_top if lint_testbench else design_top
    lint_sources = rtl + testbench if lint_testbench else rtl
    lint_arguments = common + ["--lint-only", "--top-module", lint_top] + lint_sources
    compile_arguments = common + [
        "--binary",
        "-j", str(jobs),
        "--Mdir", str(object_dir),
        "--prefix", prefix,
        "--top-module", testbench_top,
    ]
    selected_cxx_family = cxx_family
    if cxx:
        selected_cxx_family = cxx_family or (
            "clang" if "clang" in Path(cxx).name.lower() else "gcc"
        )
        if selected_cxx_family in ("clang", "gcc"):
            compile_arguments += ["--compiler", selected_cxx_family]
        compile_arguments += [
            "-MAKEFLAGS", "CXX={}".format(cxx),
            "-MAKEFLAGS", "LINK={}".format(cxx),
        ]
    if trace:
        compile_arguments.append("--trace-vcd")
    compile_arguments += rtl + testbench
    _write_response(lint_response, lint_arguments)
    _write_response(compile_response, compile_arguments)

    plan = VerilatorPlan(
        executable=str(executable),
        cxx=str(cxx) if cxx else None,
        cxx_family=selected_cxx_family,
        design_top=design_top,
        testbench_top=testbench_top,
        rtl_sources=rtl,
        testbench_sources=testbench,
        output_dir=str(output),
        object_dir=str(object_dir),
        binary=str(binary),
        lint_response_file=str(lint_response),
        compile_response_file=str(compile_response),
        manifest_file=str(manifest),
        lint_log=str(output / "lint.log"),
        compile_log=str(output / "compile.log"),
        simulation_log=str(output / "simulation.log"),
        jobs=jobs,
        seed=seed,
        trace=trace,
        defines=selected_defines,
        lint_testbench=lint_testbench,
        lint_command=[str(executable), "-f", str(lint_response)],
        compile_command=[str(executable), "-f", str(compile_response)],
        run_command=[
            str(binary),
            "+dump",
            "+fu_seed={}".format(seed),
            "+verilator+seed+{}".format(seed),
        ],
    )
    manifest.write_text(json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n")
    return plan


def _run(command: Sequence[str], cwd: Path, log_path: Path) -> float:
    started = time.time()
    with log_path.open("w") as log:
        log.write("$ {}\n\n".format(" ".join(shlex.quote(str(item)) for item in command)))
        log.flush()
        completed = subprocess.run(
            [str(item) for item in command],
            cwd=str(cwd),
            stdout=log,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
    elapsed = time.time() - started
    if completed.returncode != 0:
        tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-30:])
        raise VerilatorFlowError(
            "Command failed with exit code {}. See {}\n{}".format(
                completed.returncode, log_path, tail
            )
        )
    return elapsed


def execute_plan(plan: VerilatorPlan) -> Dict[str, Any]:
    """Run lint, compile, and simulation exactly as captured in a plan."""

    executable_path = Path(plan.executable)
    executable_available = (
        executable_path.is_file() and os.access(str(executable_path), os.X_OK)
    ) or shutil.which(plan.executable) is not None
    if not executable_available:
        raise VerilatorFlowError("Verilator executable is unavailable: {}".format(plan.executable))
    output = Path(plan.output_dir)
    lint_seconds = _run(plan.lint_command, output, Path(plan.lint_log))
    compile_seconds = _run(plan.compile_command, output, Path(plan.compile_log))
    if not Path(plan.binary).is_file():
        raise VerilatorFlowError("Verilator did not produce expected binary: {}".format(plan.binary))
    run_seconds = _run(plan.run_command, output, Path(plan.simulation_log))
    warning_count = 0
    for log_name in (plan.lint_log, plan.compile_log):
        warning_count += len(re.findall(r"^%Warning", Path(log_name).read_text(errors="replace"), re.MULTILINE))
    return {
        "tool": "verilator",
        "tool_path": plan.executable,
        "tool_version": tool_version("verilator", plan.executable),
        "cxx_path": plan.cxx,
        "cxx_version": tool_version("cxx", plan.cxx) if plan.cxx else None,
        "lint_seconds": round(lint_seconds, 3),
        "compile_seconds": round(compile_seconds, 3),
        "run_seconds": round(run_seconds, 3),
        "warning_count": warning_count,
        "lint_log": plan.lint_log,
        "compile_log": plan.compile_log,
        "simulation_log": plan.simulation_log,
        "command_manifest": plan.manifest_file,
        "binary": plan.binary,
    }


def _default_sources() -> Dict[str, List[Path]]:
    return {
        "rtl": [ROOT / "rtl" / "demo_alu.sv"],
        "testbench": [ROOT / "tb" / "tb_demo_alu.sv"],
    }


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "run"))
    parser.add_argument("--executable", help="Verilator path; plan defaults to literal 'verilator'")
    parser.add_argument("--rtl", action="append", default=[])
    parser.add_argument("--testbench", action="append", default=[])
    parser.add_argument("--design-top", default="demo_alu")
    parser.add_argument("--testbench-top", default="tb_demo_alu")
    parser.add_argument("--output", default="build/verilator_plan")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--cxx", help="C++20 compiler path for the generated model")
    parser.add_argument("--no-trace", action="store_true")
    parser.add_argument(
        "--define",
        action="append",
        default=[],
        help="Verilator preprocessor definition NAME or NAME=TOKEN (repeatable)",
    )
    parser.add_argument(
        "--lint-testbench",
        action="store_true",
        help="Lint the elaborated testbench hierarchy instead of only the design top",
    )
    args = parser.parse_args(argv)

    defaults = _default_sources()
    rtl = [Path(path) for path in args.rtl] if args.rtl else defaults["rtl"]
    testbench = (
        [Path(path) for path in args.testbench] if args.testbench else defaults["testbench"]
    )
    executable = args.executable or find_executable("verilator", "FU_VERILATOR") or "verilator"
    cxx_info = select_cxx()
    cxx = args.cxx or (cxx_info["path"] if cxx_info["coroutine_support"] else None)
    cxx_family = cxx_info["family"] if cxx == cxx_info.get("path") else None
    try:
        plan = create_plan(
            executable=executable,
            rtl_sources=rtl,
            testbench_sources=testbench,
            design_top=args.design_top,
            testbench_top=args.testbench_top,
            output_dir=Path(args.output),
            jobs=args.jobs,
            trace=not args.no_trace,
            cxx=cxx,
            cxx_family=cxx_family,
            seed=args.seed,
            defines=args.define,
            lint_testbench=args.lint_testbench,
        )
        if args.action == "run":
            result = execute_plan(plan)
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(plan.manifest_file)
    except VerilatorFlowError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
