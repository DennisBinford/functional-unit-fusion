#!/usr/bin/env python3
"""Framework adapter for the isolated lowRISC Ibex ALU functional unit.

Implements the framework contract (`simulateDesign` / `synthesizeDesign`) for the
real open-source Ibex ALU (`third_party/ibex/rtl/ibex_alu.sv`, Apache-2.0),
isolated as a standalone combinational unit by `rtl/ibex_alu_wrapper.sv`.

- simulateDesign(): Verilator builds the wrapper + Ibex RTL + the independent
  self-checking testbench and runs it; a pass requires `TEST_PASS` with no
  errors, matching the framework's other adapters.
- synthesizeDesign(): the full Ibex SystemVerilog exceeds Yosys's native subset
  (named struct assignment patterns in `ibex_pkg`), so it is first converted to
  plain Verilog with sv2v (exactly as Ibex's own upstream flow does), then run
  through the shared SiliconCompiler synthesis+STA flow on the sky130 PDK.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sc_flow  # noqa: E402  (shared SiliconCompiler PPA backend)
from toolchain import select_cxx  # noqa: E402

OSS_BIN = ROOT / "tools" / "oss-cad-suite" / "bin"
VERILATOR = OSS_BIN / "verilator"
SV2V = OSS_BIN / "sv2v"

DESIGN_TOP = "ibex_alu_wrapper"
TESTBENCH_TOP = "tb_ibex_alu"

IBEX_RTL = ROOT / "third_party" / "ibex" / "rtl"
RTL_SOURCES = [
    IBEX_RTL / "ibex_pkg.sv",
    IBEX_RTL / "ibex_alu.sv",
    ROOT / "rtl" / "ibex_alu_wrapper.sv",
]
TESTBENCH_SOURCES = [ROOT / "tb" / "tb_ibex_alu.sv"]
SDC = ROOT / "rtl" / "ibex_alu_wrapper.sdc"


class IbexAdapterError(RuntimeError):
    pass


def _require_sources() -> None:
    missing = [str(p) for p in RTL_SOURCES + TESTBENCH_SOURCES if not p.is_file()]
    if missing:
        raise IbexAdapterError(
            "Ibex RTL not found: {}. Run: python3 designs/fetch_ibex.py".format(
                ", ".join(missing)
            )
        )


def _run(command: Sequence[str], cwd: Path, log_path: Path, env=None) -> int:
    with open(log_path, "w") as log:
        completed = subprocess.run(
            [str(c) for c in command],
            cwd=str(cwd),
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
    return completed.returncode


def simulateDesign(
    simulator: str = "auto",
    build_dir: Optional[str] = None,
    clean: bool = False,
    seed: int = 1,
    jobs: int = 1,
    **_unused: Any,
) -> Dict[str, Any]:
    """Build and run the Ibex ALU testbench under Verilator; check TEST_PASS."""

    if simulator not in ("auto", "verilator"):
        raise IbexAdapterError("Only the Verilator simulator is supported")
    _require_sources()
    if not VERILATOR.is_file():
        raise IbexAdapterError("Verilator not found at {}".format(VERILATOR))

    cxx = select_cxx()
    if not cxx.get("coroutine_support"):
        raise IbexAdapterError(
            "Verilator timing mode needs a C++20 coroutine compiler; set FU_CXX"
        )

    output = Path(build_dir).resolve() if build_dir else ROOT / "build" / "ibex_simulation"
    if clean and output.exists():
        import shutil
        shutil.rmtree(output)
    obj_dir = output / "obj_dir"
    output.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PATH"] = "{}{}{}".format(OSS_BIN, os.pathsep, env.get("PATH", ""))

    binary_name = "V" + TESTBENCH_TOP
    build_cmd = [
        VERILATOR, "--binary", "--timing", "--assert",
        "-Wno-fatal", "-Wno-TIMESCALEMOD",
        "--compiler", cxx.get("family", "gcc"),
        "-MAKEFLAGS", "CXX={}".format(cxx["path"]),
        "-MAKEFLAGS", "LINK={}".format(cxx["path"]),
        "-j", str(jobs),
        "--Mdir", str(obj_dir),
        "--top-module", TESTBENCH_TOP,
        "-o", binary_name,
    ] + [str(s) for s in RTL_SOURCES + TESTBENCH_SOURCES]

    compile_log = output / "compile.log"
    rc = _run(build_cmd, output, compile_log, env=env)
    if rc != 0 or not (obj_dir / binary_name).is_file():
        return _sim_result("fail", output, error="Verilator build failed", checks=None,
                           log=compile_log, seed=seed, cxx=cxx)

    run_log = output / "simulation.log"
    rc = _run([obj_dir / binary_name, "+dump", "+fu_seed={}".format(seed)],
              output, run_log, env=env)
    text = run_log.read_text(errors="replace")

    if "TEST_ERROR" in text or "TEST_FAIL" in text or rc != 0:
        return _sim_result("fail", output, error="Simulation reported errors",
                           checks=_parse_checks(text), log=run_log, seed=seed, cxx=cxx)
    if "TEST_PASS" not in text:
        return _sim_result("fail", output, error="No TEST_PASS marker",
                           checks=None, log=run_log, seed=seed, cxx=cxx)

    return _sim_result("pass", output, error=None, checks=_parse_checks(text),
                       log=run_log, seed=seed, cxx=cxx)


def _parse_checks(text: str) -> Optional[int]:
    m = re.search(r"TEST_PASS checks=(\d+)", text)
    return int(m.group(1)) if m else None


def _sim_result(status, output, error, checks, log, seed, cxx) -> Dict[str, Any]:
    vcd = output / "activity.vcd"
    result = {
        "status": status,
        "operation": "simulation",
        "tool": "verilator",
        "design": DESIGN_TOP,
        "design_source": "lowRISC Ibex ibex_alu.sv (Apache-2.0)",
        "testbench": TESTBENCH_TOP,
        "checks": checks,
        "seed": seed,
        "cxx_path": cxx.get("path"),
        "log": str(log),
    }
    if vcd.is_file() and vcd.stat().st_size > 0:
        result["activity_vcd"] = str(vcd)
    if error:
        result["error"] = error
    # Durable artifact so the demo has a JSON to open, like the demo_alu path.
    import json
    result_file = output / "simulation.json"
    result_file.write_text(json.dumps(result, indent=2, sort_keys=True))
    result["result_file"] = str(result_file)
    return result


def synthesizeDesign(
    synthesizer: str = "siliconcompiler",
    build_dir: Optional[str] = None,
    clean: bool = False,
    **_unused: Any,
) -> Dict[str, Any]:
    """Convert Ibex SV to Verilog with sv2v, then run SiliconCompiler PPA."""

    if synthesizer not in ("auto", "siliconcompiler"):
        raise IbexAdapterError(
            "The Ibex adapter synthesizes via SiliconCompiler (synthesizer=siliconcompiler)"
        )
    _require_sources()
    if not SV2V.is_file():
        raise IbexAdapterError("sv2v not found at {}".format(SV2V))
    if not SDC.is_file():
        raise IbexAdapterError("SDC not found: {}".format(SDC))

    output = Path(build_dir).resolve() if build_dir else ROOT / "build" / "sc_ibex"
    # Clean up-front here, not inside sc_flow, so the sv2v output written below is
    # not deleted by the SC backend's own clean step.
    if clean and output.exists():
        import shutil
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    # sv2v: full Ibex SV -> a single plain-Verilog file Yosys can parse.
    converted = output / "ibex_alu_wrapper.v"
    sv2v_log = output / "sv2v.log"
    env = dict(os.environ)
    env["PATH"] = "{}{}{}".format(OSS_BIN, os.pathsep, env.get("PATH", ""))
    rc = _run([SV2V, "--write", str(converted)] + [str(s) for s in RTL_SOURCES],
              output, sv2v_log, env=env)
    if rc != 0 or not converted.is_file():
        return {
            "status": "fail",
            "operation": "synthesis",
            "tool": "sv2v+siliconcompiler",
            "error": "sv2v conversion failed; see {}".format(sv2v_log),
        }

    result = sc_flow.synthesize_design_sc(
        design_top=DESIGN_TOP,
        rtl_sources=[str(converted)],
        sdc=str(SDC),
        build_dir=str(output),
        clean=False,  # already cleaned above; keep the sv2v output in place
    )
    result["design_source"] = "lowRISC Ibex ibex_alu.sv (Apache-2.0)"
    result["frontend"] = "sv2v -> {}".format(converted.name)
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(simulateDesign(clean=True), indent=2, sort_keys=True))
    print(json.dumps(synthesizeDesign(clean=True), indent=2, sort_keys=True))
