#!/usr/bin/env python3
"""Run one isolated egglog -> executable-graph -> RTL simulation spike.

This script is intentionally not part of the default test flow. It is run
with a separately created virtual environment containing a pinned egglog.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graph_rtl_emit import emit
from verilator_flow import create_plan, execute_plan
import toolchain


ARTIFACT = ROOT / "meeting-artifacts" / "graph-backends" / "egglog-spike"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_plan():
    from egglog import EGraph, Expr, String, StringLike, i64, i64Like, rewrite

    egraph = EGraph()

    @egraph.class_
    class BV8(Expr):
        """An explicitly eight-bit unsigned word sort."""

        def __init__(self, value: i64Like) -> None:
            ...

        @classmethod
        def var(cls, name: StringLike) -> "BV8":
            ...

        def __add__(self, other: "BV8") -> "BV8":
            ...

    a, = __import__("egglog").vars_("a", BV8)
    egraph.register(rewrite(a + BV8(0)).to(a))
    original = BV8.var("x") + BV8(0)
    root = egraph.let("root", original)
    egraph.run(1)
    selected = egraph.extract(root)
    selected_text = str(selected)
    if "+" in selected_text:
        raise RuntimeError("egglog did not extract the lower-cost x+0 rewrite: {}".format(selected_text))

    plan = {
        "schema": "fu-executable-graph/v1", "version": 1,
        "design": "egglog_spike", "level": "rtlil", "top": "egglog_spike",
        "source_graphs": {"egraph": {"language": "fu-egraph-language/v1", "width": 8}},
        "match_provenance": {"engine": "egglog", "rewrite": "BV8(x) + BV8(0) -> BV8(x)"},
        "structural_merge_provenance": {"executable": False, "artifact_type": "egraph_selected_term"},
        "control_spec": {"schema": "fu-egraph-control/v1", "outputs": ["y_o"]},
        "nodes": [
            {"id": "port:x_i", "kind": "port_in", "label": "x_i", "width": 8,
             "attrs": {"direction": "input", "signed": False,
                       "provenance": {"egraph_term": "BV8.var(\\\"x\\\")", "width": 8}}},
            {"id": "port:y_o", "kind": "port_out", "label": "y_o", "width": 8,
             "attrs": {"direction": "output", "signed": False,
                       "provenance": {"egraph_term": selected_text, "width": 8}}},
        ],
        "edges": [{"src": "port:x_i", "dst": "port:y_o", "src_port": "x_i",
                   "dst_port": "y_o", "width": 8, "inverted": False,
                   "attrs": {"provenance": {"egraph_term": selected_text}}}],
        "outputs": [{"name": "y_o", "source_node": "port:y_o", "source_graph": "egraph",
                      "source_port": "y_o", "width": 8}],
        "topological_order": ["port:x_i", "port:y_o"],
        "adapters": {"zero_extensions": 0, "shared_input_muxes": [], "output_selection_muxes": []},
        "hardware_status": {"executable": True, "template_backed": False,
                             "graph_to_rtl_status": "executable"},
    }
    return plan, {"original_expression": str(original), "selected_expression": selected_text,
                  "rewrite": "BV8(x) + BV8(0) -> BV8(x)",
                  "width": 8, "signed": False,
                  "cost_model": "egglog default lowest-cost extractor; local AST estimate add=1, variable=1",
                  "estimated_cost_before": 2, "estimated_cost_after": 1}


def main():
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    plan, evidence = make_plan()
    plan_path = ARTIFACT / "selected_executable_graph.json"
    rtl_path = ARTIFACT / "egglog_spike.sv"
    tb_path = ARTIFACT / "tb_egglog_spike.sv"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    rtl_path.write_text(emit(plan))
    tb_path.write_text("""module tb_egglog_spike;
  logic [7:0] x_i;
  logic [7:0] y_o;
  integer i;
  egglog_spike dut (.x_i(x_i), .y_o(y_o));
  initial begin
    for (i = 0; i < 256; i = i + 1) begin
      x_i = i[7:0]; #1;
      if (y_o !== x_i) $fatal(1, \"egglog mismatch x=%h y=%h\", x_i, y_o);
    end
    $display(\"EGGLOG_SPIKE_PASS checks=256\");
    $finish;
  end
endmodule
""")
    verilator = toolchain.find_executable("verilator", "FU_VERILATOR")
    cxx = toolchain.select_cxx()
    if not verilator or not cxx.get("coroutine_support"):
        raise RuntimeError("locked Verilator/C++20 toolchain unavailable")
    result = execute_plan(create_plan(
        executable=verilator, rtl_sources=[rtl_path], testbench_sources=[tb_path],
        design_top="egglog_spike", testbench_top="tb_egglog_spike",
        output_dir=ARTIFACT / "simulation", jobs=1, trace=False,
        cxx=cxx["path"], cxx_family=cxx.get("family"), lint_testbench=True))
    log = Path(result["simulation_log"]).read_text(errors="replace")
    if "EGGLOG_SPIKE_PASS checks=256" not in log:
        raise RuntimeError("egglog spike simulation marker missing")
    manifest = {"schema": "fu-egraph-spike/v1", "egglog_version": "1.0.0",
                "status": "pass", "evidence": evidence,
                "artifacts": {"executable_graph": str(plan_path), "rtl": str(rtl_path),
                               "testbench": str(tb_path), "simulation": result},
                "hashes": {name: sha(path) for name, path in
                           (("executable_graph", plan_path), ("rtl", rtl_path),
                            ("testbench", tb_path), ("script", Path(__file__)))}}
    (ARTIFACT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "pass", "selected": evidence["selected_expression"],
                      "checks": 256, "manifest": str(ARTIFACT / "manifest.json")}, indent=2))


if __name__ == "__main__":
    main()
