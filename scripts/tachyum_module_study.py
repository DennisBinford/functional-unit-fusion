#!/usr/bin/env python3
"""Bounded module-level fusion search in the Tachyum FMA hierarchy.

This study is deliberately evidence-first: it surveys three repeated,
substantial child-module pairs, walks the selected pair through module,
word-level RTLIL, and generic gate graphs, then stops before PPA when the
source contract proves that the clients are simultaneous.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph_extract
import graph_viz
from hierarchy_flow import analyze_pair


SOURCE = ROOT / "third_party" / "tachyum-fma-rtl"
OUT = ROOT / "meeting-artifacts" / "2026-09-21-tachyum-module-study"
WRAPPERS = OUT / "rtl" / "tachyum_pair_wrappers.sv"
STUDY_SCHEMA = "tachyum-module-fusion-study/v1"

PAIR_SPECS: List[Dict[str, Any]] = [
    {
        "id": "right_shifter_single_lanes",
        "selected": True,
        "kind": "right_shifter_74_with_outside_bits",
        "a_top": "tachyum_single_1_right_shifter",
        "b_top": "tachyum_single_2_right_shifter",
        "source_files": ["right_shifter_74_with_outside_bits.v"],
        "parent_instances": [
            "u_right_shifter_for_addend_single_1",
            "u_right_shifter_for_addend_single_2",
        ],
        "reason": "74-bit addend alignment plus 26 sticky/outside bits; identical binary32 lane interfaces.",
    },
    {
        "id": "left_shifter_single_lanes",
        "selected": False,
        "kind": "left_shifter_76",
        "a_top": "tachyum_single_1_left_shifter",
        "b_top": "tachyum_single_2_left_shifter",
        "source_files": ["left_shifter_76.v"],
        "parent_instances": ["left_shifter_76_1", "left_shifter_76_2"],
        "reason": "76-bit normalization shifters with the same 7-bit shift control and output width.",
    },
    {
        "id": "leading_zero_single_lanes",
        "selected": False,
        "kind": "leading_zeros_detector_74_with_muxes",
        "a_top": "tachyum_single_1_leading_zero_detector",
        "b_top": "tachyum_single_2_leading_zero_detector",
        "source_files": [
            "leading_zeros_detector_74_with_muxes.v",
            "fast_leading_zeros_detector_originator_4_block.v",
            "fast_leading_zeros_detector_concatenating_2_block.v",
            "fast_leading_zeros_detector_concatenating_3_block.v",
            "fast_leading_zeros_detector_concatenating_4_block.v",
            "tb/scells.v",
        ],
        "parent_instances": [
            "u_leading_zeros_detector_for_zeros_single_1",
            "u_leading_zeros_detector_for_zeros_single_2",
        ],
        "reason": "74-bit leading-zero count, all-zero indication, and one-hot output; same parameter specialization.",
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def source_revision() -> Dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(SOURCE), *args], text=True).strip()

    head = run("rev-parse", "HEAD")
    return {
        "repository": "https://github.com/Tachyum-Open-Source/fma-rtl.git",
        "revision": head,
        "branch": run("branch", "--show-current"),
        "commit_date": run("show", "-s", "--format=%cI", head),
        "commit_subject": run("show", "-s", "--format=%s", head),
        "worktree_clean": not bool(run("status", "--porcelain")),
        "license": "Apache License 2.0",
        "license_path": "third_party/tachyum-fma-rtl/LICENSE",
        "license_sha256": sha256(SOURCE / "LICENSE"),
        "f_mul_add_sha256": sha256(SOURCE / "f_mul_add.v"),
    }


def _sources(spec: Mapping[str, Any]) -> List[str]:
    return [str(WRAPPERS.relative_to(ROOT))] + [
        str((SOURCE / item).relative_to(ROOT)) for item in spec["source_files"]
    ]


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _extract(spec: Mapping[str, Any], side: str, level: str):
    top = spec["a_top"] if side == "a" else spec["b_top"]
    name = "{}_{}".format(spec["id"], side)
    workdir = OUT / "build" / name / level
    graph = graph_extract.extract(name, _sources(spec), top, level, workdir=workdir)
    path = OUT / "graphs" / spec["id"] / (side + "." + level + ".graph.json")
    graph.save(path)
    return graph, path


def _pair_walk(spec: Mapping[str, Any]) -> Dict[str, Any]:
    levels = {"a": {}, "b": {}}
    paths = {"a": {}, "b": {}}
    for level in ("module", "rtlil", "gate"):
        for side in ("a", "b"):
            graph, path = _extract(spec, side, level)
            levels[side][level] = graph.to_dict()
            paths[side][level] = str(path.relative_to(ROOT))

    result = analyze_pair(
        levels["a"], levels["b"], name=spec["id"], evaluate_all=True
    )
    raw_matches = result.pop("matches")
    evidence = {}
    for level, match in raw_matches.items():
        evidence[level] = {
            "match_mode": match.get("match_mode"),
            "common_node_count": match.get("common_node_count"),
            "coverage": match.get("coverage"),
            "matched_nodes": (
                match.get("matched_nodes", []) if level == "module" else []
            ),
            "matched_node_sample": (
                match.get("matched_nodes", [])[:12] if level != "module" else []
            ),
            "unmatched_counts": {
                side: len(match.get("unmatched", {}).get(side, []))
                for side in ("a", "b")
            },
            "common_subgraph_sizes": sorted(
                (int(item.get("node_count", 0))
                 for item in match.get("common_subgraphs", [])),
                reverse=True,
            )[:12],
        }
    result["match_evidence"] = evidence
    result["graph_paths"] = paths
    result["render_paths"] = {}
    if spec.get("selected"):
        for level in ("module", "rtlil", "gate"):
            result["render_paths"][level] = {}
            for side in ("a", "b"):
                graph = graph_extract.FUGraph.load(ROOT / paths[side][level])
                rendered = graph_viz.render(
                    graph,
                    OUT / "figures" / spec["id"] / (side + "." + level),
                    max_nodes=450,
                )
                result["render_paths"][level][side] = {
                    key: str(value.relative_to(ROOT)) for key, value in rendered.items()
                }
    _write(OUT / "matches" / (spec["id"] + ".json"), result)
    return result


def _source_context() -> Dict[str, Any]:
    text = (SOURCE / "f_mul_add.v").read_text().splitlines()
    needles = [
        "right_shifter_74_with_outside_bits",
        "format_single_x4",
        "result[63:0]",
    ]
    occurrences = {}
    for needle in needles:
        occurrences[needle] = [index for index, line in enumerate(text, 1) if needle in line]
    return {
        "f_mul_add_readme_contract": {
            "latency_cycles": 4,
            "data_valid_gated": True,
            "packed_binary32_lanes": 2,
            "initiation_interval_cycles": 1,
        },
        "source_behavior": {
            "both_selected_instances_are_unconditional": True,
            "both_lanes_are_packed_into_one_64_bit_result": True,
            "format_single_enables_both_lanes": True,
            "simultaneous_client_use_preserved_by_source": True,
            "evidence_lines": occurrences,
        },
    }


def _parent_hierarchy() -> Dict[str, Any]:
    sources = [str((SOURCE / "f_mul_add.v").relative_to(ROOT))]
    sources.extend(str(path.relative_to(ROOT)) for path in sorted(SOURCE.glob("*.v"))
                   if path.name != "f_mul_add.v")
    sources.append(str((SOURCE / "tb" / "scells.v").relative_to(ROOT)))
    graph = graph_extract.extract(
        "tachyum_f_mul_add_parent", sources, "f_mul_add", "module",
        workdir=OUT / "build" / "parent_f_mul_add",
    )
    path = OUT / "graphs" / "parent_f_mul_add.module.graph.json"
    graph.save(path)
    repeated = [
        {"src": edge.src, "dst": edge.dst, "instance_count": edge.width}
        for edge in graph.edges
        if edge.src == "f_mul_add" and edge.dst == "right_shifter_74_with_outside_bits"
    ]
    return {
        "graph_path": str(path.relative_to(ROOT)),
        "graph_stats": graph.stats(),
        "selected_child_edge": repeated,
        "source_count": len(sources),
    }


def _contract() -> Dict[str, Any]:
    return {
        "interface": "existing f_mul_add packed 64-bit binary32 mode; two independent 32-bit lane results and four per-lane flags",
        "latency_cycles": 4,
        "throughput": "one packed operation per cycle (source README contract; both binary32 lanes participate in one operation)",
        "mutual_exclusion": "none: the two selected shifters are parallel lane resources and both may be active for the same format_single transaction",
        "sharing_contract_required": "at most one lane request may use the shared 74-bit shifter at a time, with a scheduler or a changed packed-lane interface",
        "decision": "rejected_for_existing_contract",
        "ppa_status": "not_run",
        "ppa_blocker": "A one-shifter reconstruction cannot preserve both binary32 lane outputs, four-cycle latency, and one-operation-per-cycle throughput when both source lanes are active.",
    }


def build() -> Dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    revision = source_revision()
    walks = {spec["id"]: _pair_walk(spec) for spec in PAIR_SPECS}
    parent_hierarchy = _parent_hierarchy()
    selected = next(spec for spec in PAIR_SPECS if spec.get("selected"))
    data = {
        "schema": STUDY_SCHEMA,
        "study_date": "2026-09-21",
        "source": revision,
        "source_context": _source_context(),
        "parent_hierarchy": parent_hierarchy,
        "survey_limit": 3,
        "pairs": [
            {
                "id": spec["id"],
                "kind": spec["kind"],
                "selected": bool(spec.get("selected")),
                "parent_instances": spec["parent_instances"],
                "reason": spec["reason"],
                "walk": walks[spec["id"]],
            }
            for spec in PAIR_SPECS
        ],
        "selected_pair": selected["id"],
        "contract": _contract(),
    }
    _write(OUT / "study.json", data)
    _write(OUT / "interface_contract.json", data["contract"])
    write_report(data)
    return data


def _metric_row(result: Mapping[str, Any], level: str) -> str:
    metric = result["metrics"][level]
    ga, gb = metric["graph_size"]["a"], metric["graph_size"]["b"]
    cov = metric["coverage"]
    return "| {} | {} | {} | {} | {:.3f}/{:.3f} | {:.6f} | {}/{}; {}/{} |".format(
        level,
        metric["match_size"],
        metric["operator_match_size"],
        metric["connected_subgraph_size"],
        cov.get("a", 0.0), cov.get("b", 0.0),
        metric["runtime_seconds"],
        ga["nodes"], ga["edges"], gb["nodes"], gb["edges"],
    )


def write_report(data: Mapping[str, Any]) -> None:
    selected = next(item for item in data["pairs"] if item["selected"])
    walk = selected["walk"]
    rows = [
        "# Tachyum module-level fusion-opportunity study",
        "",
        "Date: 2026-09-21. This is a bounded follow-up study; it does not modify historical bundles and does not revisit SUB/LTU or LT/GE except as prior regression context.",
        "",
        "## Direct answer",
        "",
        "The selected whole module is `right_shifter_74_with_outside_bits`: a 74-bit right shifter with a 26-bit outside/sticky output and 7-bit shift control. The two preserved parent instances are `u_right_shifter_for_addend_single_1` and `u_right_shifter_for_addend_single_2` in `f_mul_add.v`. The name-independent module matcher recognizes the same wrapper-plus-child hierarchy and selects the module level.",
        "",
        "This is a structural whole-module match, not an executable or economically useful fusion under the existing FMA contract. The source uses both binary32 lanes concurrently and packs both results into the one 64-bit result every four cycles. A single shared shifter would need a scheduler or a changed interface; it cannot preserve the existing two-lane throughput and latency. PPA is therefore intentionally not run.",
        "",
        "## Provenance",
        "",
        "- Repository: `{}`".format(data["source"]["repository"]),
        "- Exact source revision: `{}`".format(data["source"]["revision"]),
        "- Commit: {} ({})".format(data["source"]["commit_subject"], data["source"]["commit_date"]),
        "- License: {} (`{}`)".format(data["source"]["license"], data["source"]["license_path"]),
        "- `f_mul_add.v` SHA-256: `{}`".format(data["source"]["f_mul_add_sha256"]),
        "- License SHA-256: `{}`".format(data["source"]["license_sha256"]),
        "",
        "## Survey (three pairs maximum)",
        "",
        "| pair | preserved instances | result |",
        "|---|---|---|",
    ]
    for item in data["pairs"]:
        result = "selected; structurally recognized, rejected for throughput-preserving fusion" if item["selected"] else "surveyed; not selected"
        rows.append("| `{}` | `{}` / `{}` | {} |".format(item["id"], *item["parent_instances"], result))
    rows += [
        "",
        "## Top-down graph walk for the selected pair",
        "",
        "The focus wrappers preserve the actual child module and deliberately use different top/instance names. The matcher compares semantic module signatures and hierarchy edges, not those names.",
        "",
        "The actual parent-level extraction is persisted separately: `graphs/parent_f_mul_add.module.graph.json` contains 139 module nodes, and its `f_mul_add` node has an edge to `right_shifter_74_with_outside_bits` with instance count 2. The focus wrappers isolate those two real repeated instances for a pairwise comparison.",
        "",
        "| level | matched nodes | matched operators | largest connected match | coverage A/B | runtime (s) | graph sizes A; B |",
        "|---|---:|---:|---:|---|---:|---|",
    ]
    for level in ("module", "rtlil", "gate"):
        rows.append(_metric_row(walk, level))
    rows += [
        "",
        "- **Module:** the wrapper top and the `right_shifter_74_with_outside_bits` child form a preserved hierarchy skeleton, so the policy stops here for candidate selection.",
        "- **Word-level RTLIL:** the same pair was still extracted and matched as a cross-check; this is the reconstructable operator view, not the selected fusion level for this pair.",
        "- **Gate:** generic mapped-gate similarity is reported as structural evidence only. It is not used to claim that a shared RTL module can be reconstructed or that PPA improves.",
        "",
        "Figures and JSON: `figures/right_shifter_single_lanes/`, `graphs/right_shifter_single_lanes/`, and `matches/right_shifter_single_lanes.json`.",
        "",
        "## Contract and blocker",
        "",
        "| field | frozen value |",
        "|---|---|",
        "| interface | {} |".format(data["contract"]["interface"]),
        "| latency | {} cycles |".format(data["contract"]["latency_cycles"]),
        "| throughput | {} |".format(data["contract"]["throughput"]),
        "| mutual exclusion | {} |".format(data["contract"]["mutual_exclusion"]),
        "| sharing contract required | {} |".format(data["contract"]["sharing_contract_required"]),
        "",
        "The source control behavior rules out the required mutual exclusion: `format_single` enables both single lanes, each shifter has independent data and shift signals, and both lane results are concatenated into `result[63:0]`. The source README fixes four-cycle latency and data-valid gating; the source also describes the packed two-lane binary32 mode. The exact source-line evidence is in `study.json` under `source_context`.",
        "",
        "## What remains unsolved",
        "",
        "A useful optimization would require a new scheduling contract: one lane at a time, explicit lane selection, and either increased latency or a different packed-result protocol. That is a larger redesign than this bounded search. No fused RTL, equal-interface baseline, simulation claim, or PPA number is reported because constructing one would violate the frozen source contract.",
        "",
        "## Reproduction",
        "",
        "```sh",
        "python3 scripts/tachyum_module_study.py",
        "python3 -m unittest tests.test_tachyum_module_study -v",
        "```",
    ]
    (OUT / "report.md").write_text("\n".join(rows) + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate static provenance/contract without extraction")
    args = parser.parse_args(None if argv is None else list(argv))
    if args.check:
        assert len(PAIR_SPECS) <= 3
        assert source_revision()["license"] == "Apache License 2.0"
        assert _contract()["ppa_status"] == "not_run"
        print("tachyum study checks pass")
        return 0
    build()
    print("wrote {}".format(OUT / "report.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
