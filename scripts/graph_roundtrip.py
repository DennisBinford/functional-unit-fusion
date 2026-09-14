#!/usr/bin/env python3
"""Check Yosys-native RTLIL/JSON read-back and write_verilog round-tripping."""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YOSYS = ROOT / "tools" / "oss-cad-suite" / "bin" / "yosys"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--top", required=True)
    p.add_argument("-o", "--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    native = args.output / (args.top + ".rtlil")
    data = args.output / (args.top + ".yosys.json")
    reconstructed = args.output / (args.top + ".readback.v")
    script = ("read_verilog -sv {}; hierarchy -top {}; proc; opt -fast; "
              "write_rtlil {}; write_json {}; design -reset; read_rtlil {}; "
              "hierarchy -top {}; write_verilog -noattr {}"
              .format(args.source.resolve(), args.top, native, data, native, args.top, reconstructed))
    proc = subprocess.run([str(YOSYS), "-p", script], cwd=ROOT,
                          capture_output=True, text=True)
    if proc.returncode or not native.is_file() or not data.is_file() or not reconstructed.is_file():
        raise SystemExit(proc.stderr[-2000:] or proc.stdout[-2000:])
    result = {
        "schema": "fu-graph-roundtrip/v1",
        "top": args.top,
        "source": str(args.source.resolve()),
        "tool": str(YOSYS),
        "stages": {"rtlil": str(native), "yosys_json": str(data),
                   "readback_verilog": str(reconstructed)},
        "fu_graph_v1_lossless": False,
        "reason": [
            "fu-graph/v1 is a semantic analysis graph, not a complete RTLIL wire/process/netname serialization.",
            "Yosys-native RTLIL and JSON sidecars retain exact tool-native connectivity and parameters for read-back.",
            "write_verilog read-back is a synthesized/netlist-style reconstruction, not source-text preservation.",
        ],
        "status": "pass",
    }
    (args.output / "roundtrip.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
