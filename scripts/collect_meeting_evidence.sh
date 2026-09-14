#!/usr/bin/env bash

# Collect reproducible thesis-meeting evidence without modifying source files.
# Generated graphs and the combined transcript are written under /tmp.

set -uo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
output_path=${1:-/tmp/thesis-meeting-evidence.txt}
evidence_dir=$(mktemp -d /tmp/fu-meeting-evidence.XXXXXX)
quarantine_dir=""
failures=()

cd -- "$repo_root" || exit 1

exec > >(tee "$output_path") 2>&1

section() {
  printf '\n===== %s =====\n' "$1"
}

record_failure() {
  failures+=("$1")
  printf 'WARNING: %s failed; collection will continue.\n' "$1"
}

section "ACCIDENTAL FILE CLEANUP"
for stray in "'" "on" "t" "tatus --short --untracked-files=all"; do
  if [[ -e "$stray" ]]; then
    if [[ -z "$quarantine_dir" ]]; then
      quarantine_dir=$(mktemp -d /tmp/shell-paste-quarantine.XXXXXX)
    fi
    mv -- "$stray" "$quarantine_dir/"
    printf 'Moved accidental file %q to %s\n' "$stray" "$quarantine_dir"
  fi
done
if [[ -z "$quarantine_dir" ]]; then
  echo "No known accidental paste files were present."
fi

section "REPOSITORY STATE"
git log -1 --oneline || record_failure "git log"
git status --short --untracked-files=all || record_failure "git status"
git --no-pager diff --stat || record_failure "git diff --stat"
git --no-pager diff --check || record_failure "git diff --check"

section "TOOLCHAIN"
python3 toolchain.py doctor || record_failure "toolchain doctor"

section "UNIT TESTS"
make test || record_failure "unit tests"

section "FRESH GRAPH EXTRACTION"
separate_graph="$evidence_dir/separate_mul_add/separate_mul_add.aig.graph.json"
fused_graph="$evidence_dir/fused_mul_add/fused_mul_add.aig.graph.json"
match_file="$evidence_dir/separate-v-fused-aig.match.json"

python3 graph_extract.py \
  --design separate_mul_add \
  --level aig \
  --output "$evidence_dir" || record_failure "separate_mul_add graph extraction"

python3 graph_extract.py \
  --design fused_mul_add \
  --level aig \
  --output "$evidence_dir" || record_failure "fused_mul_add graph extraction"

section "GRAPH MATCHING"
if [[ -f "$separate_graph" && -f "$fused_graph" ]]; then
  if python3 graph_match.py \
    "$separate_graph" \
    "$fused_graph" \
    --max-subgraph-size 6 \
    -o "$match_file"; then
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("matched nodes:",d["common_node_count"]); print("coverage:",d["coverage"]); print("bounded subgraphs:",len(d["common_subgraphs"])); print("largest:",max((x["node_count"] for x in d["common_subgraphs"]),default=0))' "$match_file" \
      || record_failure "graph-match summary"
  else
    record_failure "graph matching"
  fi
else
  record_failure "graph matching inputs"
fi

section "MAPPED AREA"
area_reports=(
  build/synthesis/separate_mul_add/report.txt
  build/synthesis/scheduled_mul_add/report.txt
  build/synthesis/fused_mul_add/report.txt
  build/synthesis/shared_iterative_mul_add/report.txt
)
if [[ -f "${area_reports[0]}" && -f "${area_reports[1]}" &&
      -f "${area_reports[2]}" && -f "${area_reports[3]}" ]]; then
  if command -v rg >/dev/null 2>&1; then
    rg 'Chip area for module|sequential elements' "${area_reports[@]}" \
      || record_failure "mapped-area extraction"
  else
    grep -H -E 'Chip area for module|sequential elements' "${area_reports[@]}" \
      || record_failure "mapped-area extraction"
  fi
else
  echo "One or more mapped-area reports are missing:"
  for report in "${area_reports[@]}"; do
    [[ -f "$report" ]] || echo "  MISSING $report"
  done
  record_failure "mapped-area reports"
fi

section "IBEX TIMING"
timing_file=build/timing_closure/ibex_alu_wrapper/timing_closure.json
if [[ -f "$timing_file" ]]; then
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("core delay ns:",d["core_path_delay_ns"]); print("core fmax MHz:",d["fmax_core_hz"]/1e6); print("closing period ns:",d["min_closing_period_ns"]); print("closing slack ns:",d["closing_point"]["setup_slack_ns"])' "$timing_file" \
    || record_failure "Ibex timing summary"
else
  echo "MISSING $timing_file"
  record_failure "Ibex timing artifact"
fi

section "EXISTING RTL BINARIES"
rtl_binaries=(
  build/shared_mul_add_tb/Vtb_shared_iterative_mul_add
  build/tachyum_fma_tb_fixed3/Vtb_tachyum_fma_single
)
for binary in "${rtl_binaries[@]}"; do
  if [[ -x "$binary" ]]; then
    "$binary" || record_failure "$binary"
  else
    echo "MISSING OR NOT EXECUTABLE $binary"
    record_failure "$binary"
  fi
done

section "FINAL STATUS"
git status --short --untracked-files=all || record_failure "final git status"

section "LOCATIONS"
echo "Evidence transcript: $output_path"
echo "Generated evidence: $evidence_dir"
if [[ -n "$quarantine_dir" ]]; then
  echo "Quarantined accidental files: $quarantine_dir"
fi

section "COLLECTION RESULT"
if (( ${#failures[@]} == 0 )); then
  echo "All evidence-collection checks completed successfully."
  exit 0
fi

printf 'Completed with %d warning(s):\n' "${#failures[@]}"
printf '  - %s\n' "${failures[@]}"
exit 1
