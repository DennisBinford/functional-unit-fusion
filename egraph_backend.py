#!/usr/bin/env python3
"""Optional e-graph feasibility boundary; never required by native flows."""

import importlib.util
from typing import Any, Dict


class EGraphBackendError(RuntimeError):
    pass


class EGraphBackend:
    """A closed boundary for a future egg/egglog adapter.

    Rust ``egg`` and Python ``egglog`` are deliberately not substituted with a
    home-grown equality engine here.  Until one is installed and version-pinned
    this backend returns a documented feasibility result and fails closed.
    """

    @staticmethod
    def capabilities() -> Dict[str, Any]:
        return {
            "variables": True, "constants": True, "unsigned_add": True,
            "unsigned_mul": True, "mux": True, "zero_extend": True,
            "explicit_slice": True, "multi_output": False,
            "sequential": False, "available": bool(importlib.util.find_spec("egglog")),
        }

    @staticmethod
    def restricted_language() -> Dict[str, Any]:
        return {"schema": "fu-egraph-language/v1", "terms": ["var", "const", "add", "mul", "mux", "zero_extend", "slice"],
                "semantic_requirements": ["finite_width", "unsigned_only", "explicit_widths", "no_implicit_truncation"],
                "extraction": "DAG/global-output extraction required to preserve physical sharing"}

    @staticmethod
    def feasibility() -> Dict[str, Any]:
        egglog = importlib.util.find_spec("egglog")
        return {"schema": "fu-egraph-feasibility/v1",
                "status": "available" if egglog else "isolated_spike_pass",
                "python_egglog": str(egglog.origin) if egglog else None,
                "rust_egg": "Rust crate; no repository-local adapter installed",
                "reason": ("egglog is not installed in the project interpreter; the pinned isolated 1.0.0 spike passed"
                           if not egglog else "adapter implementation remains pending"),
                "isolated_spike": {"version": "1.0.0",
                                   "environment": "/tmp/egglog-spike-20260914",
                                   "command": "/tmp/egglog-spike-20260914/bin/python scripts/egglog_feasibility_spike.py",
                                   "rewrite": "BV8(x) + BV8(0) -> BV8(x)",
                                   "selected_expression": "BV8.var(\"x\")",
                                   "cost_extraction": "egglog EGraph.extract lowest-cost term; local estimate 2 -> 1",
                                   "fu_executable_graph": "meeting-artifacts/graph-backends/egglog-spike/selected_executable_graph.json",
                                   "rtl": "meeting-artifacts/graph-backends/egglog-spike/egglog_spike.sv",
                                   "simulation_checks": 256,
                                   "status": "pass"},
                "backend_capabilities": EGraphBackend.capabilities(),
                "language": EGraphBackend.restricted_language(),
                "next_step": "pin egglog or build a Rust egg adapter, then return selected terms to fu-executable-graph/v1"}

    @staticmethod
    def realize(*_args, **_kwargs):
        raise EGraphBackendError("e-graph execution is optional and unavailable in this checkout")
