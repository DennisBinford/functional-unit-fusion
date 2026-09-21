#!/usr/bin/env python3
"""Report optional graph-backend availability without installing anything."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graph_backend import backend_capabilities
from egraph_backend import EGraphBackend


def main():
    report = {
        "schema": "fu-graph-backend-doctor/v1",
        "repository": str(ROOT),
        "backends": backend_capabilities(),
        "egraph": EGraphBackend.feasibility(),
        "install_policy": "optional dependencies are reported, never installed by tests or Make targets",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
