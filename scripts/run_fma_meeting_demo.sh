#!/usr/bin/env bash
# Reproduce the graph-merge and mapped-area evidence used for the FMA meeting.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

output_dir=${1:-build/fma_meeting_demo}
graph_dir="$output_dir/graphs"
area_dir="$output_dir/area"
mkdir -p "$graph_dir" "$area_dir"

python_bin=${PYTHON:-python3}
yosys_bin=${YOSYS:-tools/oss-cad-suite/bin/yosys}

section() {
  printf '\n===== %s =====\n' "$1"
}

if [[ ! -x "$yosys_bin" ]]; then
  echo "Yosys is unavailable at $yosys_bin" >&2
  exit 2
fi

liberty=${FMA_LIBERTY:-}
if [[ -z "$liberty" ]]; then
  liberty=$(find build -type f -name 'sc_nangate45_NangateOpenCellLibrary_typical.lib' -print -quit 2>/dev/null || true)
fi
if [[ -z "$liberty" || ! -f "$liberty" ]]; then
  echo "No workspace Nangate45 typical Liberty file was found." >&2
  echo "Run 'make ppa-sc' once, or set FMA_LIBERTY=/path/to/NangateOpenCellLibrary_typical.lib." >&2
  exit 2
fi
liberty=$(realpath "$liberty")

section "FUNCTIONAL REGRESSION"
make --no-print-directory integer-fma-sim

section "READABLE RTLIL GRAPHS"
for design in separate_mul_add fused_mul_add shared_iterative_mul_add; do
  "$python_bin" graph_viz.py \
    --design "$design" --level rtlil --format svg --max-nodes 200 \
    --output "$graph_dir"
done

separate_rtlil="$graph_dir/separate_mul_add/separate_mul_add.rtlil.graph.json"
fused_rtlil="$graph_dir/fused_mul_add/fused_mul_add.rtlil.graph.json"
rtlil_match="$graph_dir/separate-v-fused.rtlil.match.json"
rtlil_merge="$graph_dir/separate-v-fused.rtlil.merged.graph.json"

section "STRUCTURAL MATCH AND MERGED GRAPH"
"$python_bin" graph_match.py \
  "$separate_rtlil" "$fused_rtlil" \
  -o "$rtlil_match"
"$python_bin" graph_merge.py \
  "$separate_rtlil" "$fused_rtlil" "$rtlil_match" \
  -o "$rtlil_merge" \
  --render "$graph_dir/separate-v-fused.rtlil.merged" --format svg

section "FRESH SYNTHESIZED AIG MATCH"
for design in separate_mul_add fused_mul_add; do
  "$python_bin" graph_extract.py \
    --design "$design" --level aig --output "$graph_dir"
done
"$python_bin" graph_match.py \
  "$graph_dir/separate_mul_add/separate_mul_add.aig.graph.json" \
  "$graph_dir/fused_mul_add/fused_mul_add.aig.graph.json" \
  --max-subgraph-size 6 \
  -o "$graph_dir/separate-v-fused.aig.match.json"

section "FRESH COMPARABLE AREA SYNTHESIS"
designs=(separate_mul_add fused_mul_add shared_iterative_mul_add)
for design in "${designs[@]}"; do
  source_file="rtl/fma_experiment/${design}.sv"
  report="$area_dir/${design}.report.txt"
  "$yosys_bin" -p \
    "read_verilog -sv $source_file; synth -top $design -flatten; dfflibmap -liberty $liberty; abc -liberty $liberty; stat -liberty $liberty" \
    >"$report"
  grep -E 'Chip area for module|sequential elements' "$report"
done

section "MEETING SUMMARY"
"$python_bin" - "$output_dir" "$liberty" <<'PY' | tee "$output_dir/meeting_summary.txt"
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
liberty = Path(sys.argv[2])
area_pattern = re.compile(r"Chip area for module '[^']+':\s+([0-9.]+)")
sequential_pattern = re.compile(
    r"of which used for sequential elements:\s+([0-9.]+)\s+\(([0-9.]+)%\)"
)
areas = {}
for design in ("separate_mul_add", "fused_mul_add", "shared_iterative_mul_add"):
    report = root / "area" / (design + ".report.txt")
    text = report.read_text(errors="replace")
    area_match = area_pattern.search(text)
    sequential_match = sequential_pattern.search(text)
    if not area_match or not sequential_match:
        raise SystemExit("could not parse mapped area from {}".format(report))
    areas[design] = {
        "area_um2": float(area_match.group(1)),
        "sequential_area_um2": float(sequential_match.group(1)),
        "sequential_percent": float(sequential_match.group(2)),
        "report": str(report),
    }

baseline = areas["separate_mul_add"]["area_um2"]
shared = areas["shared_iterative_mul_add"]["area_um2"]
area_saving = baseline - shared
area_saving_percent = 100.0 * area_saving / baseline

rtlil_match_path = root / "graphs" / "separate-v-fused.rtlil.match.json"
aig_match_path = root / "graphs" / "separate-v-fused.aig.match.json"
merged_path = root / "graphs" / "separate-v-fused.rtlil.merged.graph.json"
rtlil_match = json.loads(rtlil_match_path.read_text())
aig_match = json.loads(aig_match_path.read_text())
merged = json.loads(merged_path.read_text())

summary = {
    "scope": {
        "area_claim": "separate selectable ADD/MUL versus shared iterative ADD/MUL",
        "not_area_claim": "fused_mul_add implements a different a*b+c contract",
    },
    "technology": {
        "library": str(liberty),
        "flow": "Yosys synth/flatten, dfflibmap, ABC, stat with one Liberty file",
    },
    "correctness": {
        "status": "pass",
        "checks": 70,
        "log": "build/integer_fma_regression/simulation.log",
    },
    "areas": areas,
    "equivalent_add_or_mul_comparison": {
        "baseline": "separate_mul_add",
        "candidate": "shared_iterative_mul_add",
        "area_saved_um2": area_saving,
        "area_saved_percent": area_saving_percent,
        "tradeoff": "ADD latency 1 cycle; unsigned MUL latency and initiation interval 32 cycles",
    },
    "graph_merge_demo": {
        "inputs": ["separate_mul_add", "fused_mul_add"],
        "contract_warning": "these two graphs do not implement equivalent interfaces",
        "rtlil_exact_matches": rtlil_match["common_node_count"],
        "rtlil_merged_nodes": merged["stats"]["nodes"],
        "aig_exact_matches": aig_match["common_node_count"],
        "aig_coverage": aig_match["coverage"],
        "aig_bounded_subgraphs": len(aig_match["common_subgraphs"]),
        "merged_graph_executable": merged["attrs"]["hardware_status"]["executable"],
    },
}
(root / "meeting_summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n"
)

print("Correctness: INTEGER_FMA_TEST_PASS checks=70")
print("Equivalent ADD-or-MUL area comparison:")
print("  separate selectable: {:.3f} um^2".format(baseline))
print("  shared iterative:    {:.3f} um^2".format(shared))
print("  saved:               {:.3f} um^2 ({:.2f}%)".format(
    area_saving, area_saving_percent
))
print("  tradeoff: ADD=1 cycle; unsigned MUL latency/II=32 cycles")
print("Structural graph demonstration (not the area comparison):")
print("  RTLIL exact nodes coalesced:", rtlil_match["common_node_count"])
print("  merged RTLIL candidate nodes:", merged["stats"]["nodes"])
print("  synthesized AIG exact matches:", aig_match["common_node_count"])
print("  merged graph executable RTL:", merged["attrs"]["hardware_status"]["executable"])
print("Artifacts:")
print(" ", root / "meeting_summary.json")
print(" ", root / "graphs" / "separate_mul_add" / "separate_mul_add.rtlil.svg")
print(" ", root / "graphs" / "fused_mul_add" / "fused_mul_add.rtlil.svg")
print(" ", root / "graphs" / "separate-v-fused.rtlil.merged.svg")
print(" ", root / "graphs" / "shared_iterative_mul_add" / "shared_iterative_mul_add.rtlil.svg")
PY

section "PRESENTATION COPIES"
presentation_dir=${FMA_PRESENTATION_DIR:-meeting-artifacts/fma-graphs}
mkdir -p "$presentation_dir"

declare -A presentation_graphs=(
  [01_separate_add_or_mul]="$graph_dir/separate_mul_add/separate_mul_add.rtlil"
  [02_true_integer_fma]="$graph_dir/fused_mul_add/fused_mul_add.rtlil"
  [03_structural_merged_candidate]="$graph_dir/separate-v-fused.rtlil.merged"
  [04_shared_iterative_area_candidate]="$graph_dir/shared_iterative_mul_add/shared_iterative_mul_add.rtlil"
)
for name in "${!presentation_graphs[@]}"; do
  stem=${presentation_graphs[$name]}
  cp "${stem}.svg" "$presentation_dir/${name}.svg"
  dot -Tpng "${stem}.dot" -o "$presentation_dir/${name}.png"
done
cp "$output_dir/meeting_summary.txt" "$presentation_dir/meeting_summary.txt"
cp "$output_dir/meeting_summary.json" "$presentation_dir/meeting_summary.json"
find "$presentation_dir" -maxdepth 1 -type f -printf '  %p\n' | sort

section "LIMITATION"
echo "The merged JSON/SVG is a structural candidate, not generated executable RTL."
echo "The area result comes from the manually realized and functionally tested shared iterative RTL."
