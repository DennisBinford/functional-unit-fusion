#!/usr/bin/env python3
"""Render the six post-checkpoint vertical graph study figures."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import graph_extract
import graph_viz


SOURCE = ROOT / "meeting-artifacts" / "graph-to-rtl" / "studies"
OUTPUT = ROOT / "meeting-artifacts" / "graph-to-rtl" / "post-checkpoint-figures"
CASES = [(design, level) for level in ("rtlil", "gate", "aig")
         for design in ("graph_unit_a_mul", "graph_unit_b_add_mul_mac")]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = []
    for design, level in CASES:
        graph_path = SOURCE / level / "{}.{}.graph.json".format(design, level)
        if not graph_path.is_file():
            raise SystemExit("missing study graph: {}".format(graph_path))
        graph = graph_extract.FUGraph.load(graph_path)
        stem = OUTPUT / "{}__{}".format(design, level)
        written = graph_viz.render(
            graph, stem, image_format="svg", max_nodes=250,
            title="{} — {} level ({} nodes, {} edges)".format(
                design, level, len(graph.nodes), len(graph.edges)))
        records.append({"design": design, "level": level,
                        "source": str(graph_path), "nodes": len(graph.nodes),
                        "edges": len(graph.edges), "svg": str(written["svg"]),
                        "dot": str(written["dot"])})
    index = {"schema": "graph-to-rtl-figures/v1", "count": len(records),
             "records": records,
             "note": "Six post-checkpoint figures; gate/AIG views are structural observations only."}
    (OUTPUT / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"directory": str(OUTPUT), "figures": len(records)}, indent=2))


if __name__ == "__main__":
    main()
