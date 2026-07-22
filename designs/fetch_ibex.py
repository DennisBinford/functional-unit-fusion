#!/usr/bin/env python3
"""Fetch and lock the open-source lowRISC Ibex research design."""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/lowRISC/ibex.git"
# Latest public master observed during the 2026-07-15 background search.
DEFAULT_REVISION = "8ed87e07e3331561bce93af1568d9b376948e701"


class FetchError(RuntimeError):
    pass


def _run(command: List[str], cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    if completed.returncode != 0:
        raise FetchError("Command failed: {}\n{}".format(" ".join(command), completed.stdout))
    return completed.stdout.strip()


def fetch(destination: Path, revision: str, force: bool) -> Path:
    git = shutil.which("git")
    if not git:
        raise FetchError("git is required")
    if destination.exists():
        if not force:
            raise FetchError("Destination exists: {} (use --force to replace it)".format(destination))
        shutil.rmtree(str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run([git, "clone", "--filter=blob:none", "--no-checkout", REPOSITORY, str(destination)], ROOT)
    _run([git, "checkout", "--detach", revision], destination)
    resolved = _run([git, "rev-parse", "HEAD"], destination)
    if len(revision) == 40 and resolved.lower() != revision.lower():
        raise FetchError("Resolved SHA {} does not match requested {}".format(resolved, revision))

    required = [
        destination / "LICENSE",
        destination / "NOTICE",
        destination / "rtl" / "ibex_pkg.sv",
        destination / "rtl" / "ibex_alu.sv",
        destination / "syn" / "README.md",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FetchError("Pinned source is missing expected files: {}".format(", ".join(missing)))
    if "Apache License" not in (destination / "LICENSE").read_text(errors="replace"):
        raise FetchError("Expected Apache license text was not found")

    lock = {
        "name": "lowRISC Ibex",
        "repository": REPOSITORY,
        "requested_revision": revision,
        "resolved_commit": resolved,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "license": "Apache-2.0",
        "destination": str(destination.resolve()),
        "primary_unit": "rtl/ibex_alu.sv",
        "package_dependency": "rtl/ibex_pkg.sv",
        "upstream_open_source_flow": "syn/README.md",
    }
    lock_path = ROOT / "designs" / "ibex.source.json"
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return lock_path


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--destination", default=str(ROOT / "third_party" / "ibex"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        lock = fetch(Path(args.destination).expanduser().resolve(), args.revision, args.force)
    except FetchError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    print(lock)
    return 0


if __name__ == "__main__":
    sys.exit(main())
