#!/usr/bin/env python3
"""Quantify 'is there a lot of shared logic?' for the demo_alu operations.

Maps designs to the SAME sky130 standard cells as the main flow (Yosys + ABC) and
reads back mapped area (um^2). It answers the shareability question three ways:

  1. Aggregate: fused ALU vs the fair separate-selected baseline (N units behind
     a selector). area_saving = (A_separate - A_fused) / A_separate.
  2. Fused vs naive sum-of-ops.
  3. Pairwise shareability MATRIX: for each op pair, synthesize a single module
     that outputs BOTH results (multi-output synthesis lets Yosys share logic),
     and compare to the two ops synthesized separately. This is the AIG-level
     "shared-node" idea (Mishchenko DAG-aware rewriting) with no mux confound:
        shareability(i,j) = (A_i + A_j - A_both) / (A_i + A_j)

Fix vs the earlier version: area is now summed over ALL modules in the design,
not just the top. The separate-selected baseline keeps op boundaries
(keep_hierarchy) so Yosys can't flatten them, which means the op submodules must
be counted too. See ../02_quantifying_shared_logic.md.

Run:  .venv/bin/python brainstorm/experiments/shareability.py
"""

import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OSS_BIN = ROOT / "tools" / "oss-cad-suite" / "bin"
YOSYS = OSS_BIN / "yosys"
LIB = ROOT / "brainstorm" / "experiments" / "sky130hd_tt.lib"
GEN = ROOT / "brainstorm" / "experiments" / "gen"

# Operation semantics, coded to match rtl/demo_alu.sv exactly (WIDTH=32).
# Each maps an output name to a SystemVerilog expression over a_i, b_i.
OPS = {
    "add": "a_i + b_i",
    "sub": "a_i - b_i",
    "and": "a_i & b_i",
    "or":  "a_i | b_i",
    "xor": "a_i ^ b_i",
    "slt": "{31'b0, ($signed(a_i) < $signed(b_i))}",
    "sll": "a_i << b_i[4:0]",
}
OP_NAMES = list(OPS)


def _yosys(script: str) -> str:
    env = dict(os.environ)
    env["PATH"] = f"{OSS_BIN}{os.pathsep}" + env.get("PATH", "")
    out = subprocess.run([str(YOSYS), "-p", script], capture_output=True,
                         text=True, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"yosys failed:\n{script}\n{out.stdout[-1500:]}\n{out.stderr[-400:]}")
    return out.stdout


def total_mapped_area(sources, top: str, flatten: bool = True) -> float:
    """Map a design and return TOTAL mapped area (summed over every module)."""
    read = "; ".join(f"read_verilog -sv {s}" for s in sources)
    synth = f"synth -top {top}" + (" -flatten" if flatten else "")
    out = _yosys(f"{read}; {synth}; dfflibmap -liberty {LIB}; "
                 f"abc -liberty {LIB}; stat -liberty {LIB}")
    areas = [float(x) for x in re.findall(r"Chip area for module '[^']+':\s*([0-9.]+)", out)]
    if not areas:
        raise RuntimeError(f"no area parsed for {top}:\n{out[-800:]}")
    return sum(areas)


def top_glue_area(sources, top: str) -> float:
    """Area of ONLY the top module's own cells (op instances kept as hierarchy).

    For the separate-selected wrapper this is the selection mux / glue overhead:
    the op leaves stay as separate modules (keep_hierarchy), so the top-module
    area is exactly the cost of the selector, with no op logic double-counted.
    """
    read = "; ".join(f"read_verilog -sv {s}" for s in sources)
    out = _yosys(f"{read}; synth -top {top}; dfflibmap -liberty {LIB}; "
                 f"abc -liberty {LIB}; stat -liberty {LIB}")
    m = re.search(r"Chip area for module '\\?" + re.escape(top) + r"':\s*([0-9.]+)", out)
    if not m:
        raise RuntimeError(f"no top-module area for {top}:\n{out[-800:]}")
    return float(m.group(1))


def _write_module(name: str, outputs: dict) -> Path:
    """Write a combinational module exposing one output per (name->expr) entry."""
    GEN.mkdir(parents=True, exist_ok=True)
    ports = ", ".join(f"output logic [31:0] {o}" for o in outputs)
    body = "\n".join(f"  assign {o} = {expr};" for o, expr in outputs.items())
    src = (f"`timescale 1ns/1ps\nmodule {name} "
           f"(input logic [31:0] a_i, input logic [31:0] b_i, {ports});\n{body}\nendmodule\n")
    path = GEN / f"{name}.sv"
    path.write_text(src)
    return path


def op_area(op: str) -> float:
    src = _write_module(f"op_{op}", {"r": OPS[op]})
    return total_mapped_area([src], f"op_{op}")


def pair_area(i: str, j: str) -> float:
    """Area of a single module outputting both ops' results (Yosys may share)."""
    src = _write_module(f"pair_{i}_{j}", {"ri": OPS[i], "rj": OPS[j]})
    return total_mapped_area([src], f"pair_{i}_{j}")


def main() -> int:
    if not LIB.is_file():
        import gzip
        s = next(Path.home().glob(".sc/cache/lambdapdk-*/lambdapdk/sky130/libs/sky130hd/"
                                  "nldm/sky130_fd_sc_hd__tt_025C_1v80.lib.gz"))
        LIB.write_bytes(gzip.decompress(s.read_bytes()))

    # --- individual op areas ---
    print("individual op areas (um^2):")
    a = {op: op_area(op) for op in OP_NAMES}
    for op in OP_NAMES:
        print(f"  {op:4s} {a[op]:9.1f}")
    sum_ops = sum(a.values())

    # --- fused ALU (single opcode-selected datapath) ---
    a_fused = total_mapped_area([ROOT / "rtl" / "demo_alu.sv"], "demo_alu")

    # --- fair separate-selected baseline: each op optimized alone + the selector.
    # Measure the selector's own glue (op leaves kept as hierarchy) and add the
    # independently-synthesized op areas. This avoids fragile flatten behavior and
    # is exactly "N separate units behind one output mux".
    sep_sources = ([ROOT / "rtl" / "baselines" / f"demo_op_{op}.sv" for op in OP_NAMES]
                   + [ROOT / "rtl" / "baselines" / "demo_alu_separate_locked.sv"])
    selector_glue = top_glue_area(sep_sources, "demo_alu_separate_locked")
    a_sep = sum_ops + selector_glue

    # --- pairwise shareability matrix (multi-output synthesis, no mux) ---
    pair_share = {}
    for i, j in itertools.combinations(OP_NAMES, 2):
        both = pair_area(i, j)
        pair_share[f"{i}+{j}"] = {
            "A_i": a[i], "A_j": a[j], "A_both": both,
            "shareability": (a[i] + a[j] - both) / (a[i] + a[j]),
        }

    metrics = {
        "library": "sky130_fd_sc_hd (tt, 25C, 1.80V)",
        "individual_op_um2": a,
        "sum_of_individual_ops_um2": sum_ops,
        "fused_um2": a_fused,
        "separate_selected_um2": a_sep,
        "selection_overhead_um2": a_sep - sum_ops,
        "area_saving_vs_separate_selected": (a_sep - a_fused) / a_sep,
        "fused_vs_sum_individual": 1.0 - a_fused / sum_ops,
        "pairwise_shareability": pair_share,
    }
    out_path = Path(__file__).resolve().parent / "shareability.json"
    out_path.write_text(json.dumps(metrics, indent=2, sort_keys=True))

    print("\n--- aggregate (sky130hd) ---")
    print(f"sum of 7 individual ops : {sum_ops:8.1f} um^2")
    print(f"separate-selected (fair): {a_sep:8.1f} um^2  (+{metrics['selection_overhead_um2']:.0f} selection overhead)")
    print(f"fused demo_alu          : {a_fused:8.1f} um^2")
    print(f"AREA SAVING vs separate : {metrics['area_saving_vs_separate_selected']*100:+.1f}%   <- fusion benefit")
    print(f"fused vs sum-of-ops     : {metrics['fused_vs_sum_individual']*100:+.1f}%")

    print("\n--- top pairwise shareability (logic shared when synthesized together) ---")
    ranked = sorted(pair_share.items(), key=lambda kv: kv[1]["shareability"], reverse=True)
    for name, v in ranked[:8]:
        print(f"  {name:10s} {v['shareability']*100:+5.1f}%   "
              f"(A_i={v['A_i']:.0f} A_j={v['A_j']:.0f} A_both={v['A_both']:.0f})")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
