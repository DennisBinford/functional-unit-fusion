#!/usr/bin/env python3
"""Discover and record the open-source EDA toolchain used by the framework."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parent
TOOL_SPECS = {
    "verilator": {"binary": "verilator", "env": "FU_VERILATOR", "args": ["--version"]},
    "yosys": {"binary": "yosys", "env": "FU_YOSYS", "args": ["-V"]},
    "opensta": {"binary": "sta", "env": "FU_OPENSTA", "args": ["-version"]},
    "git": {"binary": "git", "env": None, "args": ["--version"]},
    "make": {"binary": "make", "env": None, "args": ["--version"]},
}


_COROUTINE_PROBE = r"""
#include <coroutine>
struct task {
  struct promise_type {
    task get_return_object() { return {}; }
    std::suspend_never initial_suspend() noexcept { return {}; }
    std::suspend_never final_suspend() noexcept { return {}; }
    void return_void() {}
    void unhandled_exception() {}
  };
};
task probe() { co_return; }
int main() { probe(); }
"""


def _candidate_directories(extra_paths: Optional[Sequence[str]] = None) -> List[str]:
    candidates = list(extra_paths or [])
    suite = os.environ.get("OSS_CAD_SUITE")
    if suite:
        candidates.append(str(Path(suite).expanduser() / "bin"))
    candidates.append(str(ROOT / "tools" / "oss-cad-suite" / "bin"))
    return candidates


def find_executable(
    binary: str,
    environment_name: Optional[str] = None,
    extra_paths: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """Find an executable from an explicit env var, local suite, or PATH."""

    if environment_name:
        configured = os.environ.get(environment_name)
        if configured:
            path = Path(configured).expanduser()
            if path.is_file() and os.access(str(path), os.X_OK):
                return str(path.resolve())
    for directory in _candidate_directories(extra_paths):
        candidate = Path(directory) / binary
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate.resolve())
    resolved = shutil.which(binary)
    return str(Path(resolved).resolve()) if resolved else None


def tool_version(name: str, executable: str) -> str:
    spec = TOOL_SPECS.get(name, {})
    arguments = spec.get("args", ["--version"])
    try:
        completed = subprocess.run(
            [executable] + list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unavailable: {}".format(exc)
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return lines[0] if lines else "unknown"


def inspect_cxx(executable: str, source: str) -> Dict[str, Any]:
    """Check that a C++ compiler can compile Verilator timing coroutines."""

    version = tool_version("cxx", executable)
    try:
        completed = subprocess.run(
            [executable, "-std=c++20", "-x", "c++", "-fsyntax-only", "-"],
            input=_COROUTINE_PROBE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=20,
        )
        probe_output = "\n".join(completed.stdout.splitlines()[-8:])
        coroutine_support = completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        probe_output = str(exc)
        coroutine_support = False
    lowered = version.lower()
    family = "clang" if "clang" in lowered else "gcc" if "gcc" in lowered or "g++" in lowered else "unknown"
    return {
        "found": True,
        # Preserve a clang++/g++ symlink name. Invoking the resolved clang
        # binary as `clang` can omit the C++ standard library during linking.
        "path": os.path.abspath(executable),
        "version": version,
        "family": family,
        "source": source,
        "coroutine_support": coroutine_support,
        "probe": "c++20_coroutine_compile",
        "probe_output": probe_output or None,
    }


def select_cxx() -> Dict[str, Any]:
    """Select the first configured or installed C++20 coroutine compiler."""

    candidates: List[tuple] = []
    for environment_name in ("FU_CXX", "CXX"):
        configured = os.environ.get(environment_name)
        if configured:
            path = Path(configured).expanduser()
            if path.is_file() and os.access(str(path), os.X_OK):
                candidates.append((os.path.abspath(str(path)), environment_name))
    for binary in ("clang++", "g++", "c++"):
        executable = None
        for directory in _candidate_directories():
            candidate = Path(directory) / binary
            if candidate.is_file() and os.access(str(candidate), os.X_OK):
                executable = os.path.abspath(str(candidate))
                break
        if executable is None:
            located = shutil.which(binary)
            executable = os.path.abspath(located) if located else None
        if executable:
            candidates.append((executable, "auto"))

    seen = set()
    first_result: Optional[Dict[str, Any]] = None
    for executable, source in candidates:
        if executable in seen:
            continue
        seen.add(executable)
        result = inspect_cxx(executable, source)
        if first_result is None:
            first_result = result
        if result["coroutine_support"]:
            return result
    return first_result or {
        "found": False,
        "path": None,
        "version": None,
        "family": None,
        "source": None,
        "coroutine_support": False,
        "probe": "c++20_coroutine_compile",
        "probe_output": "No C++ compiler found",
    }


def inspect_liberty(path: str) -> Dict[str, Any]:
    liberty = Path(path).expanduser().resolve()
    result: Dict[str, Any] = {"path": str(liberty), "exists": liberty.is_file()}
    if not liberty.is_file():
        return result
    text = liberty.read_text(errors="replace")
    patterns = {
        "library": r"\blibrary\s*\(\s*([^\s)]+)",
        "time_unit": r"\btime_unit\s*:\s*\"([^\"]+)\"",
        "capacitive_load_unit": r"\bcapacitive_load_unit\s*\(\s*([^\)]+)\)",
        "voltage_unit": r"\bvoltage_unit\s*:\s*\"([^\"]+)\"",
        "current_unit": r"\bcurrent_unit\s*:\s*\"([^\"]+)\"",
        "leakage_power_unit": r"\bleakage_power_unit\s*:\s*\"([^\"]+)\"",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        result[key] = match.group(1) if match else None
    result["size_bytes"] = liberty.stat().st_size
    result["sha256"] = sha256_file(liberty)
    result["time_unit_is_1ns"] = (
        str(result.get("time_unit") or "").replace(" ", "").lower() == "1ns"
    )
    return result


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest for a reproducibility manifest."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(paths: Sequence[Path], root: Path = ROOT) -> List[Dict[str, Any]]:
    """Describe immutable run inputs without depending on a Git checkout."""

    manifest = []
    resolved_root = root.resolve()
    for candidate in sorted({Path(path).resolve() for path in paths}, key=str):
        if not candidate.is_file():
            raise FileNotFoundError("Source manifest input does not exist: {}".format(candidate))
        try:
            relative = str(candidate.relative_to(resolved_root))
        except ValueError:
            relative = None
        manifest.append({
            "path": str(candidate),
            "relative_path": relative,
            "size_bytes": candidate.stat().st_size,
            "sha256": sha256_file(candidate),
        })
    return manifest


def snapshot(liberty: Optional[str] = None) -> Dict[str, Any]:
    tools: Dict[str, Any] = {}
    for name, spec in TOOL_SPECS.items():
        executable = find_executable(str(spec["binary"]), spec.get("env"))
        tools[name] = {
            "found": bool(executable),
            "path": executable,
            "version": tool_version(name, executable) if executable else None,
        }
    tools["cxx"] = select_cxx()
    verilator_ready = bool(
        tools["verilator"]["found"] and tools["cxx"]["coroutine_support"]
    )
    simulators = ["verilator"] if verilator_ready else []
    liberty_details = inspect_liberty(liberty) if liberty else None
    liberty_ready = bool(
        liberty_details
        and liberty_details.get("exists")
        and liberty_details.get("time_unit_is_1ns")
    )
    checks = {
        "simulation_ready": verilator_ready,
        "simulation_choices": simulators,
        "verilator_cxx_ready": bool(tools["cxx"]["coroutine_support"]),
        "synthesis_ready": bool(tools["yosys"]["found"]),
        "liberty_ready": liberty_ready,
        "timing_power_ready": bool(tools["opensta"]["found"] and liberty_ready),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": "open_source_tools_only",
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "tools": tools,
        "liberty": liberty_details,
        "checks": checks,
    }


def reproducibility_fingerprint(data: Dict[str, Any]) -> Dict[str, Any]:
    """Select stable fields that an accepted run must match exactly."""

    tools = data.get("tools") or {}
    selected_tools = {}
    for name in ("verilator", "yosys", "opensta", "cxx"):
        details = tools.get(name) or {}
        selected_tools[name] = {
            "path": details.get("path"),
            "version": details.get("version"),
            "coroutine_support": details.get("coroutine_support") if name == "cxx" else None,
        }
    liberty = data.get("liberty") or {}
    return {
        "policy": data.get("policy"),
        "host": data.get("host"),
        "tools": selected_tools,
        "liberty": {
            "path": liberty.get("path"),
            "sha256": liberty.get("sha256"),
            "library": liberty.get("library"),
            "time_unit": liberty.get("time_unit"),
            "capacitive_load_unit": liberty.get("capacitive_load_unit"),
            "voltage_unit": liberty.get("voltage_unit"),
            "current_unit": liberty.get("current_unit"),
            "leakage_power_unit": liberty.get("leakage_power_unit"),
        },
    }


def create_lock(liberty: str, output: Path) -> Dict[str, Any]:
    """Create an immutable environment record only from a fully ready flow."""

    data = snapshot(liberty)
    required = ("simulation_ready", "synthesis_ready", "liberty_ready", "timing_power_ready")
    missing = [name for name in required if not data["checks"].get(name)]
    if missing:
        raise RuntimeError(
            "Cannot lock an incomplete toolchain; failed check(s): {}".format(
                ", ".join(missing)
            )
        )
    lock = {
        "schema_version": 1,
        "kind": "open_source_eda_toolchain_lock",
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": reproducibility_fingerprint(data),
        "snapshot": data,
    }
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return lock


def verify_lock(lock_path: Path, liberty: Optional[str] = None) -> Dict[str, Any]:
    """Compare the active tools/library against an existing lock file."""

    path = lock_path.expanduser().resolve()
    if not path.is_file():
        return {"status": "fail", "lock_file": str(path), "differences": ["lock file missing"]}
    try:
        lock = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return {"status": "fail", "lock_file": str(path), "differences": [str(exc)]}
    if lock.get("kind") != "open_source_eda_toolchain_lock" or lock.get("schema_version") != 1:
        return {
            "status": "fail",
            "lock_file": str(path),
            "differences": ["unsupported lock schema"],
        }
    locked_fingerprint = lock.get("fingerprint") or {}
    locked_liberty = (locked_fingerprint.get("liberty") or {}).get("path")
    current = snapshot(liberty or locked_liberty)
    current_fingerprint = reproducibility_fingerprint(current)
    differences = []
    if current_fingerprint != locked_fingerprint:
        for key in ("policy", "host", "tools", "liberty"):
            if current_fingerprint.get(key) != locked_fingerprint.get(key):
                differences.append("{} differs".format(key))
    for check in ("simulation_ready", "synthesis_ready", "liberty_ready", "timing_power_ready"):
        if not current["checks"].get(check):
            differences.append("current {} is false".format(check))
    return {
        "status": "pass" if not differences else "fail",
        "lock_file": str(path),
        "lock_sha256": sha256_file(path),
        "differences": differences,
        "locked_fingerprint": locked_fingerprint,
        "current_fingerprint": current_fingerprint,
    }
def _print_human(data: Dict[str, Any]) -> None:
    print("Open-source EDA toolchain doctor")
    for name, details in data["tools"].items():
        if name == "cxx" and details["found"] and not details["coroutine_support"]:
            marker = "OLD"
        else:
            marker = "OK" if details["found"] else "MISSING"
        suffix = " — {}".format(details["version"]) if details["version"] else ""
        print("  {:9s} {:7s}{}".format(name, marker, suffix))
    print("  simulation ready: {}".format(data["checks"]["simulation_ready"]))
    print("  synthesis ready: {}".format(data["checks"]["synthesis_ready"]))
    print("  Liberty ready: {}".format(data["checks"]["liberty_ready"]))
    print("  timing/power ready: {}".format(data["checks"]["timing_power_ready"]))
    if not data["checks"]["simulation_ready"] or not data["checks"]["synthesis_ready"]:
        print("See README.md section 'Install the open-source toolchain'.")


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        choices=("doctor", "snapshot", "lock", "verify"),
        default="doctor",
    )
    parser.add_argument("--liberty", help="Absolute or relative path to the selected .lib corner")
    parser.add_argument("--output")
    parser.add_argument("--lock-file", default="toolchain.lock.json")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Fail if simulation or synthesis is unavailable")
    args = parser.parse_args(argv)

    if args.action == "verify":
        verification = verify_lock(Path(args.lock_file), args.liberty)
        if args.json:
            print(json.dumps(verification, indent=2, sort_keys=True))
        else:
            print("toolchain lock: {}".format(verification["status"]))
            print("  {}".format(verification["lock_file"]))
            for difference in verification["differences"]:
                print("  - {}".format(difference))
        return 0 if verification["status"] == "pass" else 1

    if args.action == "lock":
        if not args.liberty:
            print("ERROR: --liberty is required for lock", file=sys.stderr)
            return 2
        output = Path(args.output or args.lock_file)
        try:
            create_lock(args.liberty, output)
        except RuntimeError as exc:
            print("ERROR: {}".format(exc), file=sys.stderr)
            return 1
        print(str(output.expanduser().resolve()))
        return 0

    data = snapshot(args.liberty)
    if args.action == "snapshot":
        output = Path(args.output or "build/toolchain.json").expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print(str(output))
    elif args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        _print_human(data)

    strict_failed = (
        not data["checks"]["simulation_ready"]
        or not data["checks"]["synthesis_ready"]
        or bool(args.liberty and not data["checks"]["timing_power_ready"])
    )
    if args.strict and strict_failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
