#!/usr/bin/env python3
"""SiliconCompiler synthesis + STA backend for the evaluation framework.

This module gives the framework a real PPA path: it drives SiliconCompiler's
synthesis + static-timing flow (Yosys -> OpenSTA) on the FreePDK45 (Nangate45)
open PDK and returns area / timing / power numbers in the framework's result-dict
contract (a JSON-serializable dict carrying at least a ``status`` field).

It is intentionally free of design-specific knowledge: the adapter passes the
top module, RTL sources, and an SDC, and this module runs the flow. Tool
binaries are resolved from the project-local, no-sudo toolchain installed under
``tools/`` (OSS CAD Suite Yosys + a locally built OpenSTA); the FreePDK45
Nangate45 standard cells and timing corners are supplied by SiliconCompiler.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent

# Project-local tool locations (installed without root; see README/action_items).
_EDA_ENV = ROOT / "tools" / "mm" / "root" / "envs" / "eda"      # OpenSTA + PDK deps
_OSS_BIN = ROOT / "tools" / "oss-cad-suite" / "bin"             # Yosys 0.67 (>= SC floor)


class SCFlowError(RuntimeError):
    """Raised when the SiliconCompiler PPA flow cannot produce valid results."""


def tool_environment() -> Dict[str, str]:
    """Return an os.environ-style dict with the local EDA tools on PATH."""
    env = dict(os.environ)
    parts = [str(_OSS_BIN), str(_EDA_ENV / "bin")]
    env["PATH"] = os.pathsep.join(parts + [env.get("PATH", "")])
    # OpenSTA is built with an rpath to the env libs, but set this defensively
    # so tcl/cudd/libstdc++ resolve regardless of how the process was launched.
    libdir = str(_EDA_ENV / "lib")
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        [libdir, env.get("LD_LIBRARY_PATH", "")]
    ).strip(os.pathsep)
    return env


def _apply_tool_environment() -> None:
    os.environ.update(tool_environment())


# Metrics we surface as the PPA result, mapped to the SC flow step that records
# them. SC records timing/power on the 'timing' step and cell area there too.
_TIMING_METRICS = (
    "cellarea",      # mapped standard-cell area, um^2  (Area)
    "setupslack",    # worst setup slack, ns            (Performance / timing)
    "setuptns",      # total negative setup slack, ns
    "fmax",          # max frequency, if reported
    "peakpower",     # total power at typical corner, W (Power)
    "leakagepower",  # leakage power, W
    "cells",         # instance count
    "logicdepth",    # logic levels on critical path
)


def _read_metric(history, metric: str, step: str, index: str = "0"):
    try:
        value = history.get("metric", metric, step=step, index=index)
    except Exception:
        return None, None
    unit = None
    try:
        unit = history.get("metric", metric, field="unit")
    except Exception:
        pass
    return value, unit


def synthesize_design_sc(
    design_top: str,
    rtl_sources: Sequence[str],
    sdc: str,
    build_dir: Optional[str] = None,
    clean: bool = False,
) -> Dict[str, Any]:
    """Run the SiliconCompiler synthesis+STA flow and return a PPA result dict.

    Args:
        design_top: top module name.
        rtl_sources: RTL source files (Verilog/SystemVerilog).
        sdc: timing-constraint file (SDC) used by STA.
        build_dir: SC build directory (default: build/sc).
        clean: remove the build directory before running.
    """
    _apply_tool_environment()

    # Imported here so the module can be inspected without SC installed.
    from siliconcompiler import ASIC, Design
    from siliconcompiler.targets import freepdk45_demo
    from siliconcompiler.flows import synflow

    sources = [str(Path(s).resolve()) for s in rtl_sources]
    missing = [s for s in sources if not Path(s).is_file()]
    if missing:
        raise SCFlowError("RTL source(s) not found: {}".format(", ".join(missing)))
    sdc_path = str(Path(sdc).resolve())
    if not Path(sdc_path).is_file():
        raise SCFlowError("SDC file not found: {}".format(sdc_path))

    builddir = Path(build_dir).resolve() if build_dir else ROOT / "build" / "sc"
    if clean and builddir.exists():
        import shutil
        shutil.rmtree(builddir)
    builddir.mkdir(parents=True, exist_ok=True)

    design = Design(design_top)
    design.set_topmodule(design_top, fileset="rtl")
    for src in sources:
        design.add_file(src, fileset="rtl")
    design.add_file(sdc_path, fileset="sdc")

    proj = ASIC(design)
    proj.add_fileset(["rtl", "sdc"])
    freepdk45_demo(proj)                         # FreePDK45 PDK + Nangate45 stdcells + corners
    proj.set_flow(synflow.SynthesisFlow())       # synthesis + STA only (no P&R)
    proj.set("option", "builddir", str(builddir))
    # Locally built OpenSTA reports version 3.1.0 (>= SC floor) so no bypass is
    # needed; Yosys 0.67 also clears the floor. novercheck stays off on purpose.

    # SiliconCompiler's scheduler occasionally hiccups on a run (a rare, transient
    # failure that succeeds on a re-run). Retry once before reporting failure so
    # the demo is reproducible; capture the reason to a log if it truly fails.
    last_error = None
    for attempt in range(2):
        try:
            proj.run()
            last_error = None
            break
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        error_log = builddir / "sc_error.log"
        try:
            error_log.write_text("SiliconCompiler run failed after retry:\n{}\n".format(last_error))
        except OSError:
            pass
        return {
            "status": "fail",
            "operation": "synthesis",
            "tool": "siliconcompiler",
            "flow": "synflow.SynthesisFlow",
            "error": str(last_error),
            "error_log": str(error_log),
            "build_dir": str(builddir),
        }

    jobname = proj.option.get_jobname()
    history = proj.history(jobname)

    metrics: Dict[str, Any] = {}
    for metric in _TIMING_METRICS:
        value, unit = _read_metric(history, metric, step="timing")
        if value is None:
            value, unit = _read_metric(history, metric, step="synthesis")
        if value is not None:
            metrics[metric] = {"value": value, "unit": unit}

    # A valid PPA point must at least have real mapped area.
    area = metrics.get("cellarea", {}).get("value")
    ppa_valid = area is not None and float(area) > 0.0

    setup_slack = metrics.get("setupslack", {}).get("value")
    timing_met = None if setup_slack is None else (float(setup_slack) >= 0.0)

    result = {
        "status": "pass" if ppa_valid else "fail",
        "operation": "synthesis",
        "tool": "siliconcompiler",
        "flow": "synflow.SynthesisFlow",
        "pdk": "freepdk45",
        "stdcell_library": "nangate45 (NangateOpenCellLibrary_typical)",
        "synthesis_tool": "yosys",
        "timing_tool": "opensta",
        "design": design_top,
        "rtl_sources": sources,
        "sdc": sdc_path,
        "ppa_validated": ppa_valid,
        "timing_met": timing_met,
        "metrics": metrics,
        "build_dir": str(builddir),
        "manifest": str(builddir / design_top / jobname / f"{design_top}.pkg.json"),
    }

    # Durable PPA artifact for demo transcripts, mirroring the Yosys path.
    import json
    result_file = builddir / "ppa.json"
    result_file.write_text(json.dumps(result, indent=2, sort_keys=True))
    result["result_file"] = str(result_file)
    return result


if __name__ == "__main__":
    import json
    result = synthesize_design_sc(
        "demo_alu",
        [str(ROOT / "rtl" / "demo_alu.sv")],
        str(ROOT / "rtl" / "demo_alu.sdc"),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
