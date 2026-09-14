#!/usr/bin/env python3
"""Rebuild the 2026-09-14 professor-meeting evidence bundle."""
import json
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "meeting-artifacts" / "2026-09-14"
BUILD = ROOT / "build" / "professor_meeting_demo"
PYTHON = Path(sys.executable)
SDC = ROOT / "rtl" / "fma_experiment" / "generated_fused_10ns.sdc"
LOCK = ROOT / "build" / "toolchain.lock.json"
EXECUTED_COMMANDS = []


def run(command, log_name=None):
    command = [str(x) for x in command]
    EXECUTED_COMMANDS.append(command)
    if log_name:
        path = BUILD / "logs" / log_name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as stream:
            completed = subprocess.run(command, cwd=ROOT, stdout=stream,
                                       stderr=subprocess.STDOUT)
    else:
        completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        raise RuntimeError("command failed ({}): {}".format(completed.returncode, " ".join(command)))
    return command


def graph(design, level, directory):
    return run([PYTHON, "graph_viz.py", "--design", design, "--level", level,
                "--format", "svg", "--max-nodes", "300", "--output", directory],
               "graph_{}_{}.log".format(design, level))


def load(path):
    return json.loads(Path(path).read_text())


def match_metrics(left, right, output, mode="exact"):
    started = time.perf_counter()
    run([PYTHON, "graph_match.py", left, right, "--mode", mode,
         "--max-subgraph-size", "6", "-o", output],
        "match_{}.log".format(Path(output).stem))
    result = load(output)
    result["runtime_seconds"] = time.perf_counter() - started
    Path(output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def fresh_graph_evidence():
    directory = OUT / "evidence_graphs"
    directory.mkdir(parents=True, exist_ok=True)
    # The source graph pair is the existing small ADD/MUL structural evidence;
    # it is recreated so the generator consumes current tool output.
    for design in ("separate_mul_add", "graph_candidate_add_mul"):
        graph(design, "rtlil", directory)
    separate = directory / "separate_mul_add" / "separate_mul_add.rtlil.graph.json"
    fused = directory / "graph_candidate_add_mul" / "graph_candidate_add_mul.rtlil.graph.json"
    match_path = OUT / "generated" / "source_match.json"
    merge_path = OUT / "generated" / "source_merge.json"
    match_path.parent.mkdir(parents=True, exist_ok=True)
    match_metrics(separate, fused, match_path)
    run([PYTHON, "graph_merge.py", separate, fused, match_path, "-o", merge_path,
         "--render", OUT / "generated" / "source_merge", "--format", "svg"],
        "source_merge.log")
    return separate, fused, match_path, merge_path


def generate_and_simulate(graph_a, graph_b, match_path, merge_path):
    generated = ROOT / "rtl" / "fma_experiment" / "generated_fused_add_mul_mac.sv"
    manifest = generated.with_suffix(generated.suffix + ".manifest.json")
    run([PYTHON, "rtl_generator.py", "--graph-a", graph_a, "--graph-b", graph_b,
         "--match", match_path, "--merge", merge_path, "-o", generated],
        "rtl_generator.log")
    (OUT / "generated").mkdir(parents=True, exist_ok=True)
    shutil.copy2(generated, OUT / "generated" / generated.name)
    shutil.copy2(manifest, OUT / "generated" / manifest.name)
    simulation_output = BUILD / "generated_simulation"
    run([PYTHON, "verilator_flow.py", "run", "--rtl", generated,
         "--rtl", ROOT / "rtl/fma_experiment/parallel_add_mul_mac_baseline.sv",
         "--testbench", ROOT / "tb/tb_generated_fused_add_mul_mac.sv",
         "--design-top", "generated_fused_add_mul_mac",
         "--testbench-top", "tb_generated_fused_add_mul_mac", "--output",
         simulation_output, "--jobs", "1", "--seed", "1", "--lint-testbench"],
        "generated_simulation.log")
    return generated, manifest, simulation_output


def ppa_pair(generated, simulation_output):
    import adapter
    import sc_flow
    import toolchain
    vcd = simulation_output / "generated_fused_activity.vcd"
    if not vcd.is_file():
        candidates = list(simulation_output.glob("*.vcd"))
        if len(candidates) != 1:
            raise RuntimeError("expected one generated activity VCD, found {}".format(candidates))
        vcd = candidates[0]
    liberty = toolchain.verify_lock(LOCK)["locked_fingerprint"]["liberty"]["path"]
    rows = {}
    for name, top, source in (
        ("fused", "generated_fused_add_mul_mac", generated),
        ("duplicate_adder_parallel", "parallel_add_mul_mac_baseline",
         ROOT / "rtl/fma_experiment/parallel_add_mul_mac_baseline.sv"),
    ):
        sc_dir = BUILD / "sc" / name
        synthesis = None
        for attempt in range(2):
            synthesis = sc_flow.synthesize_design_sc(
                top, [str(source)], str(SDC), build_dir=str(sc_dir), clean=True)
            if synthesis.get("status") == "pass":
                break
        if synthesis.get("status") != "pass":
            raise RuntimeError(synthesis.get("error", "SC synthesis failed"))
        netlists = list(sc_dir.glob("{}/job*/synthesis/0/outputs/{}.vg".format(top, top)))
        if len(netlists) != 1:
            raise RuntimeError("expected one mapped netlist for {}".format(top))
        sta_dir = BUILD / "activity_sta" / name
        sta_dir.mkdir(parents=True, exist_ok=True)
        netlist_name = top + "_netlist.v"
        shutil.copy2(netlists[0], sta_dir / netlist_name)
        _, seconds, sta = adapter._run_opensta(
            sta_dir, liberty, 10.0, vcd, design_top=top,
            netlist_name=netlist_name,
            activity_required_signals=("clk_i", "rst_ni", "valid_i", "mode_i", "a_i", "b_i", "c_i"),
            expected_activity_input_pins=(
                {"clk_i", "rst_ni", "valid_i"}
                | {"mode_i[{}]".format(i) for i in range(2)}
                | {"a_i[{}]".format(i) for i in range(32)}
                | {"b_i[{}]".format(i) for i in range(32)}
                | {"c_i[{}]".format(i) for i in range(32)}),
            clock_port="clk_i")
        rows[name] = {
            "area_um2": float(synthesis["metrics"]["cellarea"]["value"]),
            "cell_count": int(synthesis["metrics"]["cells"]["value"]),
            "critical_delay_ns": float(sta["critical_path_delay"]["value"]),
            "slack_ns": float(sta["slack"]["value"]),
            "estimated_fmax_mhz": 1000.0 / float(sta["critical_path_delay"]["value"]),
            "power_mw": float(sta["total_power"]["value"]),
            "internal_power_mw": float(sta["internal_power"]["value"]),
            "switching_power_mw": float(sta["switching_power"]["value"]),
            "leakage_power_mw": float(sta["leakage_power"]["value"]),
            "activity_annotation_fraction": sta["activity_annotation"]["primary_input_annotation_fraction"],
            "opensta_seconds": seconds,
            "synthesis_result": synthesis["result_file"],
        }
    duplicate, fused = rows["duplicate_adder_parallel"], rows["fused"]
    area_delta = duplicate["area_um2"] - fused["area_um2"]
    return {
        "assumptions": {"library": liberty, "clock_period_ns": 10.0,
                        "frequency_mhz": 100.0, "workload": "70 deterministic ADD/MUL/MAC operations",
                        "latency_cycles": 1, "initiation_interval_cycles": 1},
        "duplicate_adder_parallel_area_um2": duplicate["area_um2"],
        "fused_area_um2": fused["area_um2"],
        "area_delta_um2_duplicate_adder_minus_fused": area_delta,
        "area_delta_percent": 100.0 * area_delta / duplicate["area_um2"],
        "mux_control_overhead": {"rtl_description": "mode mux and shared-adder steering",
                                  "realized_in_area_delta": True,
                                  "isolated_area_not_measured": True},
        "duplicate_adder_triple_mode_parallel": duplicate, "fused": fused,
        "operation_latency_and_ii": {"ADD": {"latency_cycles": 1, "ii_cycles": 1},
                                      "MUL": {"latency_cycles": 1, "ii_cycles": 1},
                                      "MAC": {"latency_cycles": 1, "ii_cycles": 1}},
        "workload_normalized_throughput_ops_per_second": 100e6,
        "honest_result": ("fused shares one final adder and is smaller than the duplicate-adder triple-mode parallel baseline"
                          if area_delta > 0 else
                          "fused is not smaller than the duplicate-adder triple-mode parallel baseline"),
        "scientific_scope": "This is an adder-sharing comparison against a duplicate-adder triple-mode parallel architecture; it is not literal area(A)+area(B) and does not prove the general area criterion.",
    }


def studies():
    directory = BUILD / "studies"
    directory.mkdir(parents=True, exist_ok=True)
    # Horizontal: larger Ibex execution block against isolated ALU, RTLIL only.
    for design in ("ibex_fused_ex_block_wrapper", "ibex_alu_wrapper"):
        graph(design, "rtlil", directory)
    large = directory / "ibex_fused_ex_block_wrapper" / "ibex_fused_ex_block_wrapper.rtlil.graph.json"
    alu = directory / "ibex_alu_wrapper" / "ibex_alu_wrapper.rtlil.graph.json"
    horizontal_match = directory / "horizontal.rtlil.match.json"
    h = match_metrics(large, alu, horizontal_match)
    horizontal = {"designs": ["ibex_fused_ex_block_wrapper", "ibex_alu_wrapper"],
                  "level": "rtlil", "graph_a_stats": load(large)["stats"],
                  "graph_b_stats": load(alu)["stats"], "match": h,
                  "largest_bounded_subgraph": max((s["node_count"] for s in h["common_subgraphs"]), default=0),
                  "limitations": ["Ibex wrapper includes execution-block controls and fast multiplier/divider inputs; isolated ALU is a narrower interface.",
                                  "Exact rooted structural matching only; no claim of area savings."]}
    # Vertical: same generated small case versus its matched triple-mode baseline.
    vertical_rows = []
    for level in ("rtlil", "gate", "aig"):
        graph("generated_fused_add_mul_mac", level, directory)
        graph("parallel_add_mul_mac_baseline", level, directory)
        a = directory / "generated_fused_add_mul_mac" / ("generated_fused_add_mul_mac.{}.graph.json".format(level))
        b = directory / "parallel_add_mul_mac_baseline" / ("parallel_add_mul_mac_baseline.{}.graph.json".format(level))
        out = directory / ("vertical.{}.match.json".format(level))
        m = match_metrics(a, b, out)
        vertical_rows.append({"level": level, "graph_a_stats": load(a)["stats"],
                              "graph_b_stats": load(b)["stats"], "match": m,
                              "largest_bounded_subgraph": max((s["node_count"] for s in m["common_subgraphs"]), default=0)})
    vertical = {"designs": ["generated_fused_add_mul_mac", "parallel_add_mul_mac_baseline"],
                "rows": vertical_rows,
                "useful_sharing_level": "rtlil" if any(r["match"]["common_node_count"] for r in vertical_rows if r["level"] == "rtlil") else "none exposed by exact baseline",
                "limitations": ["Gate/AIG node commonality is a structural observation, not an area saving.",
                                "Realized PPA is reported separately from graph commonality."]}
    return horizontal, vertical


def capability_study():
    """Generate the real-port ADD32/ADD64 directional capability evidence."""
    directory = BUILD / "studies"
    for design in ("capability_add32", "capability_add64"):
        graph(design, "rtlil", directory)
    narrow = directory / "capability_add32" / "capability_add32.rtlil.graph.json"
    wide = directory / "capability_add64" / "capability_add64.rtlil.graph.json"
    wide_match = directory / "capability.wide_to_narrow.match.json"
    narrow_match = directory / "capability.narrow_to_wide.match.json"
    match_metrics(wide, narrow, wide_match, mode="capability")
    match_metrics(narrow, wide, narrow_match, mode="capability")
    wide_merge = directory / "capability.wide_to_narrow.merge.json"
    narrow_merge = directory / "capability.narrow_to_wide.merge.json"
    run([PYTHON, "graph_merge.py", wide, narrow, wide_match, "-o", wide_merge],
        "capability_wide_merge.log")
    run([PYTHON, "graph_merge.py", narrow, wide, narrow_match, "-o", narrow_merge],
        "capability_narrow_merge.log")
    return {
        "graphs": {"add32": str(narrow), "add64": str(wide)},
        "wide_to_narrow": load(wide_match), "narrow_to_wide": load(narrow_match),
        "merges": {"wide_to_narrow": str(wide_merge), "narrow_to_wide": str(narrow_merge)},
        "proves": "An explicitly unsigned wider named-interface ADD can implement the narrower ADD with zero-extension inputs and output slicing; reverse implementation is rejected.",
        "does_not_prove": "Capability is not equivalence, area saving, signed support, or general graph-to-RTL synthesis.",
    }


def _check_ibex_intake():
    lock_path = ROOT / "designs" / "ibex.source.json"
    source = ROOT / "third_party" / "ibex" / "rtl" / "ibex_alu.sv"
    if not lock_path.is_file() or not source.is_file():
        raise RuntimeError("pinned Ibex intake is missing; run make ibex-fetch first")
    record = load(lock_path)
    if record.get("resolved_commit") != record.get("requested_revision"):
        raise RuntimeError("Ibex intake lock is not pinned to its requested revision")
    return {"lock": str(lock_path.resolve()), "source": str(source.resolve()),
            "resolved_commit": record.get("resolved_commit")}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _marker(log_path, pattern, label):
    import re
    text = Path(log_path).read_text(errors="replace")
    match = re.search(pattern, text)
    if not match:
        raise RuntimeError("{} marker missing from {}".format(label, log_path))
    return {name: int(value) for name, value in match.groupdict().items()}


def cached():
    from meeting_cache import validate_bundle
    validate_bundle(OUT, ROOT)
    print("validated cached meeting evidence in {}".format(OUT))


def write_bundle_manifest():
    """Hash every study/result plus the implementation and tool inputs it uses."""
    from meeting_cache import build_manifest, BUNDLE_MANIFEST_NAME
    paths = []
    for path in sorted(OUT.rglob("*")):
        if path.is_file() and path.name != BUNDLE_MANIFEST_NAME:
            paths.append(("bundle_artifact", path))
    implementation = [
        ROOT / "Makefile", ROOT / "adapter.py", ROOT / "sc_flow.py",
        ROOT / "toolchain.py", ROOT / "verilator_flow.py", ROOT / "graph_viz.py",
        ROOT / "designs/fetch_ibex.py", ROOT / "adapters/ibex_adapter.py",
        ROOT / "rtl_generator.py", ROOT / "graph_extract.py", ROOT / "graph_match.py",
        ROOT / "graph_merge.py", ROOT / "scripts/meeting_cache.py",
        ROOT / "scripts/professor_meeting_demo.py", ROOT / "scripts/run_fair_baselines.py",
        ROOT / "scripts/run_fma_ppa.py", ROOT / "scripts/graph_roundtrip.py",
        ROOT / "scripts/measurement_guardrails.py",
        ROOT / "scripts/timing_opensta.tcl",
        ROOT / "build/toolchain.lock.json",
    ]
    implementation.extend(sorted((ROOT / "scripts").glob("*.py")))
    implementation.extend(sorted((ROOT / "scripts").glob("*.sh")))
    implementation.extend(sorted((ROOT / "rtl/fma_experiment").glob("*.sv")))
    implementation.extend(sorted((ROOT / "rtl/fma_experiment").glob("*.sdc")))
    implementation.extend(sorted((ROOT / "tb").glob("*.sv")))
    implementation.extend(sorted((ROOT / "third_party/ibex").glob("*.lock*")))
    implementation.extend(sorted((ROOT / "third_party/ibex").glob("*.toml")))
    implementation.extend([ROOT / "build/sc_ibex/ibex_alu_wrapper.v",
                          ROOT / "build/ibex_fused_ex_block/ibex_fused_ex_block_wrapper.v"])
    for path in implementation:
        if path.is_file():
            paths.append(("implementation_input", path))
    for path in sorted((ROOT / "third_party/ibex").rglob("*.sv")):
        paths.append(("ibex_source", path))
    lock = load(LOCK)
    liberty = Path(lock["fingerprint"]["liberty"]["path"])
    paths.append(("locked_library", liberty))
    manifest = build_manifest(paths)
    (OUT / BUNDLE_MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def full():
    OUT.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    # Full mode records and executes every prerequisite instead of loading
    # stale meeting JSON. The pinned Ibex intake and toolchain lock are checked
    # before any research result is summarized.
    run(["make", "test"], "make_test.log")
    run(["make", "integer-fma-sim"], "make_integer_fma_sim.log")
    run([PYTHON, "scripts/run_fma_ppa.py", "--clean"], "run_fma_ppa.log")
    run(["make", "ibex-fetch"], "ibex_fetch.log")
    ibex = _check_ibex_intake()
    run([PYTHON, "toolchain.py", "verify", "--lock-file", LOCK], "verify_lock.log")
    source_graph, graph_b, match_path, merge_path = fresh_graph_evidence()
    generated, manifest, simulation = generate_and_simulate(source_graph, graph_b, match_path, merge_path)
    run([PYTHON, "scripts/graph_roundtrip.py", "--source", generated,
         "--top", "generated_fused_add_mul_mac", "-o", OUT / "roundtrip"], "roundtrip.log")
    ppa = ppa_pair(generated, simulation)
    run([PYTHON, "scripts/run_fair_baselines.py", "--generated", generated,
         "--output", OUT / "fair_baselines.json", "--clean"], "fair_baselines.log")
    fair = load(OUT / "fair_baselines.json")
    horizontal, vertical = studies()
    capability = capability_study()
    if (BUILD / "studies").is_dir():
        shutil.copytree(BUILD / "studies", OUT / "studies", dirs_exist_ok=True)
    provenance = load(OUT / "roundtrip" / "roundtrip.json")
    import toolchain
    verification = {
        "python_tests": _marker(BUILD / "logs/make_test.log",
                                 r"Ran (?P<tests>\d+) tests", "test count")["tests"],
        "integer_fma_checks": _marker(BUILD / "logs/make_integer_fma_sim.log",
                                       r"INTEGER_FMA_TEST_PASS checks=(?P<checks>\d+)",
                                       "integer regression")["checks"],
        "generated_checks": _marker(simulation / "simulation.log",
                                     r"GENERATED_FUSED_TEST_PASS checks=(?P<checks>\d+)",
                                     "generated regression")["checks"],
        "interface_checks": fair["interface_level"]["generated_triple_mode"]["simulation"]["checks"],
    }
    summary = {
        "schema": "professor-meeting/v2", "meeting_date": "2026-09-14",
        "commands": EXECUTED_COMMANDS,
        "ibex_intake": ibex,
        "toolchain": toolchain.verify_lock(LOCK),
        "ppa_four_variants": load(ROOT / "meeting-artifacts/fma-ppa/fma_ppa_results.json"),
        "fair_baselines": fair,
        "generated_design": {"rtl": str(OUT / "generated" / generated.name),
                             "source_rtl": str(generated), "manifest": str(OUT / "generated" / manifest.name),
                             "simulation_log": str(simulation / "simulation.log"),
                             "comparison": ppa},
        "roundtrip": provenance, "capability_study": capability,
        "verification": verification,
        "horizontal_study": horizontal,
        "vertical_study": vertical,
        "requirement_status": {
            "duplicate_adder_labeling": "complete; comparison is explicitly one-final-adder sharing against the duplicate-adder triple-mode baseline",
            "fair_core_comparison": "complete measurement; literal area(A)+area(B) criterion FAILED because the fused core is larger than the mapped standalone sum",
            "interface_comparison": "complete; Yosys confirms one MUL and one ADD in each interface design, but separate-system MAC is a two-cycle schedule",
            "capability_matching": "complete for unsigned directional width baseline with real ADD32/ADD64 ports and edge adapters",
            "graph_validated_rtl": "PARTIAL; graph-gated specialized RTL template. Graph evidence gates the supported unsigned ADD/MUL eligibility, while c_i/MAC topology is template-backed and not reconstructed from the structural merge",
            "general_fusion": "future work; not claimed",
        },
        "limitations": ["The RTL generator remains intentionally specialized for unsigned 32-bit ADD/MUL graph evidence.",
                        "The RTLIL/JSON/fu-graph round trip is evidence-backed with a Yosys-native sidecar; fu-graph/v1 is not lossless source RTL.",
                        "No common graph node is labeled an area saving without realized synthesis.",
                        "No complete NetworkX migration, selective mux optimization, or advanced scheduling was attempted."],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    ppa_rows = ppa
    text = ["Professor meeting evidence — 2026-09-14", "", "Executed commands:"]
    text.extend("  " + " ".join(command) for command in EXECUTED_COMMANDS)
    text.extend(["", "Duplicate-adder triple-mode comparison:",
            "  duplicate-adder parallel area: {:.3f} um^2".format(ppa_rows["duplicate_adder_parallel_area_um2"]),
            "  fused area: {:.3f} um^2".format(ppa_rows["fused_area_um2"]),
            "  delta (duplicate-adder minus fused): {:.3f} um^2 ({:.2f}%)".format(ppa_rows["area_delta_um2_duplicate_adder_minus_fused"], ppa_rows["area_delta_percent"]),
            "  result: {}".format(ppa_rows["honest_result"]),
            "  This does not prove area(AB) < area(A)+area(B).", "", "Literal core-only comparison:",
            "  area(A)+area(B) = {:.3f} um^2; fused core = {:.3f} um^2; delta = {:.3f} um^2 ({:.2f}%)".format(
                fair["core_only"]["literal_area_A_plus_B_um2"], fair["core_only"]["fused_core_area_um2"],
                fair["core_only"]["delta_um2_A_plus_B_minus_fused"], fair["core_only"]["delta_percent"]),
            "Interface-level separate-system MAC is 2 cycles/II=2; generated ADD/MUL/MAC are 1 cycle/II=1.", "", "Horizontal Ibex study: {} nodes/{} edges vs {} nodes/{} edges; matches {}; largest bounded subgraph {}".format(
                horizontal["graph_a_stats"]["nodes"], horizontal["graph_a_stats"]["edges"], horizontal["graph_b_stats"]["nodes"], horizontal["graph_b_stats"]["edges"], horizontal["match"]["common_node_count"], horizontal["largest_bounded_subgraph"]),
            "Vertical study levels: " + ", ".join("{} matches={}".format(r["level"], r["match"]["common_node_count"]) for r in vertical["rows"]),
            "Capability: ADD64 implements ADD32 with zero-extension/slicing; ADD32->ADD64 and signed capability are rejected.",
            "Verification: Python tests={}; integer checks={}; core semantic checks={}; generated checks={}; interface checks={}.".format(
                verification["python_tests"], verification["integer_fma_checks"],
                fair["core_semantics_simulation"]["checks"], verification["generated_checks"], verification["interface_checks"]),
            "", "Artifacts: summary.json, generated/, roundtrip/, studies/, fair_baselines.json, bundle_manifest.json, and meeting-artifacts/fma-ppa/", "", "Limitations: graph-gated specialized RTL template; c_i/MAC topology is template-backed rather than reconstructed from the structural merge; graph commonality is not an area claim until synthesis; Yosys-native sidecar retained."])
    (OUT / "summary.txt").write_text("\n".join(text) + "\n")
    write_bundle_manifest()
    print("wrote {}".format(OUT / "summary.json"))
    print("wrote {}".format(OUT / "summary.txt"))


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("full", "cached"), default="full")
    args = parser.parse_args(argv)
    EXECUTED_COMMANDS.append(["make", "professor-meeting-demo-{}".format(args.mode)])
    if args.mode == "cached":
        cached()
    else:
        full()


if __name__ == "__main__":
    main()
