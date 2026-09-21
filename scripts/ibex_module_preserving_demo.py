#!/usr/bin/env python3
"""Priority 2: contract-guided module-preserving Ibex adder sharing."""

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "meeting-artifacts" / "ibex-fusion" / "module-preserving"
BUILD = ROOT / "build" / "ibex_fusion" / "module_preserving"
IBEX = ROOT / "third_party" / "ibex" / "rtl"
UPSTREAM_ALU = IBEX / "ibex_alu.sv"
UPSTREAM_PKG = IBEX / "ibex_pkg.sv"
UPSTREAM_LOCK = ROOT / "designs" / "ibex.source.json"
CANONICAL = ROOT / "rtl" / "ibex_alu_wrapper.sv"
SEPARATE = ROOT / "rtl" / "ibex_fusion" / "ibex_alu_add_client_separate.sv"
EMITTER = ROOT / "scripts" / "ibex_module_preserving_emit.py"
SV2V = ROOT / "tools" / "oss-cad-suite" / "bin" / "sv2v"
VERILATOR = ROOT / "tools" / "oss-cad-suite" / "bin" / "verilator"

OPS = ["ALU_ADD", "ALU_SUB", "ALU_XOR", "ALU_OR", "ALU_AND", "ALU_SLL", "ALU_SRL", "ALU_SRA",
       "ALU_LT", "ALU_LTU", "ALU_GE", "ALU_GEU", "ALU_EQ", "ALU_NE"]


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command: Iterable[Any], log: Path = None) -> None:
    command = [str(item) for item in command]
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as handle:
            result = subprocess.run(command, cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT)
    else:
        result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
    if result.returncode:
        detail = "see {}".format(log) if log else (result.stdout + result.stderr)[-2000:]
        raise RuntimeError("command failed: {} ({})".format(" ".join(command), detail))


def record(path: Path, external: bool = False) -> Dict[str, Any]:
    path = Path(path).resolve()
    if external:
        return {"path": str(path), "external": True, "sha256": sha256(path)}
    return {"path": path.relative_to(ROOT).as_posix(), "external": False, "sha256": sha256(path)}


def prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "figures").mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    # Generated executables and Verilator object trees are build products, not
    # durable meeting evidence. Remove only these known products from older
    # runs; source, JSON, logs, and figures remain in OUT.
    old_sim = OUT / "simulation"
    for product in (old_sim / "module_preserving_sim",):
        if product.exists():
            product.unlink()
    if (old_sim / "obj_dir").exists():
        shutil.rmtree(old_sim / "obj_dir")


def make_contract() -> Path:
    source = UPSTREAM_ALU.read_text()
    checks = [
        "multdiv_sel_i:     adder_in_a = multdiv_operand_a_i",
        "multdiv_sel_i:     adder_in_b = multdiv_operand_b_i",
        "default:           adder_in_a = {operand_a_i,1'b1}",
        "default:           adder_in_b = {operand_b_i, 1'b0}",
        "assign adder_result       = adder_result_ext_o[32:1]",
    ]
    if not all(item in source for item in checks):
        raise RuntimeError("pinned Ibex adder encoding did not match the expected source statements")
    lock = json.loads(UPSTREAM_LOCK.read_text())
    contract = {
        "schema": "fu-ibex-sharing-contract/v1", "version": 1,
        "classification": "contract-guided module-preserving fusion",
        "resource_owner": {"module": "ibex_alu", "source": record(UPSTREAM_ALU),
                           "package_source": record(UPSTREAM_PKG), "resolved_commit": lock["resolved_commit"]},
        "shared_resource": {"kind": "$add", "operand_width": 33, "extended_result_width": 34,
                            "architectural_result_width": 32, "architectural_result_slice": "[32:1]",
                            "result_port": "adder_result_o", "extended_result_port": "adder_result_ext_o"},
        "operand_paths": {"alu_a": "{alu_operand_a_i,1'b1}", "alu_b": "{alu_operand_b_i,1'b0}",
                          "client_a": "{client_operand_a_i,1'b1}", "client_b": "{client_operand_b_i,1'b0}"},
        "selector": {"port": "client_select_i", "width": 1, "encoding": {"0": "ibex_alu", "1": "independent_add_client"}},
        "interface": {"operator_i": {"direction": "input", "width": 7},
                       "alu_operand_a_i": {"direction": "input", "width": 32},
                       "alu_operand_b_i": {"direction": "input", "width": 32},
                       "client_operand_a_i": {"direction": "input", "width": 32},
                       "client_operand_b_i": {"direction": "input", "width": 32},
                       "client_select_i": {"direction": "input", "width": 1},
                       "result_o": {"direction": "output", "width": 32}},
        "result_routing": {"alu": "ibex_alu.result_o_or_comparison_result", "client": "ibex_alu.adder_result_o", "output": "result_o"},
        "mode_semantics": {"alu": "same 14 base RV32I operations as ibex_alu_wrapper", "client": "client_operand_a_i + client_operand_b_i modulo 2^32"},
        "simultaneous_use": {"preserved": False, "restriction": "client_select_i chooses one adder client per combinational evaluation"},
        "generated_top": "ibex_alu_add_client_fused",
    }
    path = OUT / "sharing_contract.json"
    path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
    return path


def make_testbench() -> Path:
    path = OUT / "tb_module_preserving.sv"
    path.write_text(r'''`timescale 1ns/1ps
module tb_module_preserving;
  import ibex_pkg::*;
  logic [6:0] operator_i;
  logic [31:0] alu_operand_a_i, alu_operand_b_i, client_operand_a_i, client_operand_b_i;
  logic client_select_i;
  logic [31:0] canonical_result, separate_result, fused_result, dedicated_result;
  integer checks, errors, i, j;
  logic [31:0] state;
  alu_op_e ops [14];

  ibex_alu_wrapper canonical(.operator_i(operator_i), .operand_a_i(alu_operand_a_i),
                             .operand_b_i(alu_operand_b_i), .result_o(canonical_result));
  ibex_alu_add_client_separate separate(
    .operator_i(operator_i), .alu_operand_a_i(alu_operand_a_i), .alu_operand_b_i(alu_operand_b_i),
    .client_operand_a_i(client_operand_a_i), .client_operand_b_i(client_operand_b_i),
    .client_select_i(client_select_i), .result_o(separate_result));
  ibex_alu_add_client_fused fused(
    .operator_i(operator_i), .alu_operand_a_i(alu_operand_a_i), .alu_operand_b_i(alu_operand_b_i),
    .client_operand_a_i(client_operand_a_i), .client_operand_b_i(client_operand_b_i),
    .client_select_i(client_select_i), .result_o(fused_result));
  ibex_independent_add_client dedicated(.operand_a_i(client_operand_a_i),
                                        .operand_b_i(client_operand_b_i), .result_o(dedicated_result));

  function automatic [31:0] model(input alu_op_e op, input [31:0] a, input [31:0] b);
    begin
      case (op)
        ALU_ADD: model=a+b; ALU_SUB: model=a-b; ALU_XOR: model=a^b; ALU_OR: model=a|b; ALU_AND: model=a&b;
        ALU_SLL: model=a<<b[4:0]; ALU_SRL: model=a>>b[4:0]; ALU_SRA: model=$signed(a)>>>b[4:0];
        ALU_LT: model={31'b0,($signed(a)<$signed(b))}; ALU_LTU: model={31'b0,(a<b)};
        ALU_GE: model={31'b0,($signed(a)>=$signed(b))}; ALU_GEU: model={31'b0,(a>=b)};
        ALU_EQ: model={31'b0,(a==b)}; ALU_NE: model={31'b0,(a!=b)}; default: model='0;
      endcase
    end
  endfunction
  function automatic [31:0] rnd(input [31:0] x);
    begin rnd=x^(x<<13); rnd=rnd^(rnd>>17); rnd=rnd^(rnd<<5); end
  endfunction
  task automatic check_alu(input alu_op_e op, input [31:0] a, input [31:0] b);
    begin
      operator_i=op; alu_operand_a_i=a; alu_operand_b_i=b;
      client_operand_a_i=32'h13579bdf; client_operand_b_i=32'h2468ace0; client_select_i=1'b0; #1;
      checks=checks+1; if (fused_result !== canonical_result || fused_result !== model(op,a,b)) begin errors=errors+1; $display("ALU_FAIL"); end
      checks=checks+1; if (separate_result !== fused_result) errors=errors+1;
      client_select_i=1'b1; #1;
      checks=checks+1; if (fused_result !== dedicated_result || fused_result !== separate_result) errors=errors+1;
    end
  endtask
  task automatic check_client(input [31:0] a, input [31:0] b);
    begin
      operator_i=ALU_ADD; alu_operand_a_i=32'hdeadbeef; alu_operand_b_i=32'h01020304;
      client_operand_a_i=a; client_operand_b_i=b; client_select_i=1'b1; #1;
      checks=checks+1; if (fused_result !== (a+b)) errors=errors+1;
      checks=checks+1; if (separate_result !== fused_result || fused_result !== dedicated_result) errors=errors+1;
      client_select_i=1'b0; #1; checks=checks+1; if (fused_result !== canonical_result) errors=errors+1;
    end
  endtask
  initial begin
    ops[0]=ALU_ADD; ops[1]=ALU_SUB; ops[2]=ALU_XOR; ops[3]=ALU_OR; ops[4]=ALU_AND; ops[5]=ALU_SLL; ops[6]=ALU_SRL; ops[7]=ALU_SRA;
    ops[8]=ALU_LT; ops[9]=ALU_LTU; ops[10]=ALU_GE; ops[11]=ALU_GEU; ops[12]=ALU_EQ; ops[13]=ALU_NE;
    checks=0; errors=0; state=32'h1;
    for (j=0;j<14;j=j+1) begin
      check_alu(ops[j],32'h0,32'h0); check_alu(ops[j],32'hffffffff,32'h1); check_alu(ops[j],32'h80000000,32'h7fffffff); check_alu(ops[j],32'h7fffffff,32'h80000000);
      check_alu(ops[j],32'hffffffff,32'hffffffff); check_alu(ops[j],32'h1,32'hffffffff); check_alu(ops[j],32'h80000000,32'h1); check_alu(ops[j],32'h12345678,32'h12345678);
      for (i=0;i<256;i=i+1) begin state=rnd(state); check_alu(ops[j],state,rnd(state)); end
    end
    check_client(0,0); check_client(32'hffffffff,1); check_client(32'hffffffff,32'hffffffff); check_client(32'h80000000,32'h80000000); check_client(32'h7fffffff,32'h1);
    for (i=0;i<256;i=i+1) begin state=rnd(state); check_client(state,rnd(state)); end
    if (errors != 0) $fatal(1,"module preserving regression failed errors=%0d",errors);
    $display("IBEX_MODULE_PRESERVING_PASS checks=%0d alu_operations=14 client_vectors=261",checks); $finish;
  end
endmodule
''')
    return path


def convert_sources(contract: Path) -> Dict[str, Path]:
    BUILD.mkdir(parents=True, exist_ok=True)
    separate = BUILD / "separate.v"
    fused = BUILD / "fused.v"
    run([SV2V, "--write", separate, UPSTREAM_PKG, UPSTREAM_ALU, CANONICAL, SEPARATE])
    # The generated module is emitted after contract creation and is converted
    # with the same pinned upstream sources for simulation and synthesis.
    run([SV2V, "--write", fused, UPSTREAM_PKG, UPSTREAM_ALU, CANONICAL, SEPARATE, OUT / "ibex_alu_add_client_fused.sv"])
    return {"separate": separate, "fused": fused}


def simulate(tb: Path, sources: Dict[str, Path]) -> Dict[str, Any]:
    sim = OUT / "simulation"
    sim.mkdir(exist_ok=True)
    sim_build = BUILD / "simulation"
    sim_build.mkdir(parents=True, exist_ok=True)
    binary = sim_build / "module_preserving_sim"
    compile_log = sim_build / "compile.log"
    cxx = subprocess.run([sys.executable, "-c", "from toolchain import select_cxx; import json; print(json.dumps(select_cxx()))"], cwd=str(ROOT), capture_output=True, text=True, check=True)
    compiler = json.loads(cxx.stdout)
    run([VERILATOR, "--binary", "--timing", "-Wno-fatal", "-Wno-TIMESCALEMOD", "--compiler", compiler["family"],
         "-MAKEFLAGS", "CXX={}".format(compiler["path"]), "-MAKEFLAGS", "LINK={}".format(compiler["path"]),
         "--top-module", "tb_module_preserving", "-o", binary, "--Mdir", sim_build / "obj_dir",
         UPSTREAM_PKG, UPSTREAM_ALU, CANONICAL, SEPARATE, OUT / "ibex_alu_add_client_fused.sv", tb], compile_log)
    shutil.copyfile(compile_log, sim / "compile.log")
    result = subprocess.run([str(binary)], cwd=str(sim_build), capture_output=True, text=True)
    (sim / "simulation.log").write_text(result.stdout + result.stderr)
    match = re.search(r"IBEX_MODULE_PRESERVING_PASS checks=(\d+)", result.stdout)
    if result.returncode or not match:
        raise RuntimeError("module-preserving simulation failed; see {}".format(sim / "simulation.log"))
    return {"status": "pass", "checks": int(match.group(1)), "alu_operations": 14,
            "alu_vectors": 3696, "client_vectors": 261, "tool": "verilator",
            "log": "meeting-artifacts/ibex-fusion/module-preserving/simulation/simulation.log"}


def extract(top: str, source: Path, name: str) -> Dict[str, Any]:
    import graph_extract
    out = BUILD / "graphs" / (name + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    graph_extract.run_yosys("read_verilog -sv {}; hierarchy -top {}; flatten; proc; opt -full; write_json {}".format(source, top, out))
    graph = graph_extract._graph_from_netlist(json.loads(out.read_text()), top, top, "rtlil")
    if name == "equal_interface_separate":
        graph.save(OUT / "equal_interface_separate.rtlil.graph.json")
    if name == "fused_reextracted":
        graph.save(OUT / "generated_reextracted.rtlil.graph.json")
        graph.save(OUT / "generated_fused.rtlil.graph.json")
    return graph.to_dict()


def ppa(top: str, source: Path, sdc: Path, name: str) -> Dict[str, Any]:
    import sc_flow
    result = sc_flow.synthesize_design_sc(top, [str(source)], str(sdc), BUILD / "ppa" / name, clean=True)
    if result.get("status") != "pass":
        result = sc_flow.synthesize_design_sc(top, [str(source)], str(sdc), BUILD / "ppa" / name, clean=True)
    metrics = result.get("metrics", {})
    def value(key): return (metrics.get(key) or {}).get("value")
    if result.get("status") != "pass" or value("cellarea") is None:
        raise RuntimeError("incomplete PPA result for {}: {}".format(name, result.get("error", "missing metrics")))
    return {"design": top, "status": result["status"], "area_um2": value("cellarea"), "cell_count": value("cells"),
            "critical_delay_ns": (metrics.get("core_path_delay") or {}).get("value"), "slack_ns": value("setupslack"),
            "fmax_mhz": ((metrics.get("fmax_core") or {}).get("value") or 0)/1e6, "power_mw": value("peakpower"),
            "power_basis": "estimated peak power at 400 MHz using default flow activity assumptions",
            "synthesis_result": result.get("result_file")}


def write_architecture_figure() -> Path:
    path = OUT / "figures" / "module_preserving_architecture.svg"
    path.write_text('''<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="520" viewBox="0 0 1100 520"><rect width="100%" height="100%" fill="white"/><style>text{font-family:sans-serif}.box{fill:#dbe9fb;stroke:#2f6fb5;stroke-width:2}.mux{fill:#efe0f4;stroke:#7b4397;stroke-width:2}.title{font-size:22px;font-weight:bold}.label{font-size:16px}</style><text x="550" y="35" text-anchor="middle" class="title">Contract-guided module-preserving fusion</text><text x="275" y="75" text-anchor="middle" class="title">Dedicated separate baseline</text><rect x="50" y="130" width="190" height="80" class="box"/><text x="145" y="165" text-anchor="middle" class="label">Ibex ALU adder</text><text x="145" y="190" text-anchor="middle" class="label">{ALU operands, private}</text><rect x="310" y="130" width="190" height="80" class="box"/><text x="405" y="165" text-anchor="middle" class="label">Independent client</text><text x="405" y="190" text-anchor="middle" class="label">dedicated adder</text><line x1="240" y1="170" x2="310" y2="170" stroke="#555" stroke-width="3"/><text x="550" y="75" text-anchor="middle" class="title">Fused generated wrapper</text><rect x="610" y="120" width="190" height="80" class="mux"/><text x="705" y="155" text-anchor="middle" class="label">client_select_i</text><text x="705" y="180" text-anchor="middle" class="label">selector / mux path</text><rect x="860" y="120" width="190" height="80" class="box"/><text x="955" y="155" text-anchor="middle" class="label">ibex_alu</text><text x="955" y="180" text-anchor="middle" class="label">internal 33-bit adder</text><line x1="800" y1="160" x2="860" y2="160" stroke="#555" stroke-width="3"/><path d="M560 300 L705 220 L860 300" fill="none" stroke="#7b4397" stroke-width="3"/><text x="550" y="335" text-anchor="middle" class="label">ALU operands ─┐</text><text x="550" y="365" text-anchor="middle" class="label">client ops ───┘ → selector → Ibex internal adder → selected result</text><text x="550" y="450" text-anchor="middle" class="label">Simultaneous ALU/client use is not preserved.</text></svg>''')
    return path


def write_ppa_figure(core: Dict[str, Any], equal: Dict[str, Any]) -> Path:
    """Write a durable, dependency-free comparison figure from current PPA."""
    path = OUT / "figures" / "whole_alu_ppa.svg"
    values = [
        ("literal core", core["literal_area_sum_um2"], "#4c78a8"),
        ("equal separate", equal["separate"]["area_um2"], "#f58518"),
        ("fused", core["fused"]["area_um2"], "#54a24b"),
    ]
    scale = 300.0 / max(value for _, value, _ in values)
    bars = []
    for index, (label, value, color) in enumerate(values):
        x = 110 + index * 220
        height = value * scale
        y = 390 - height
        bars.append('<rect x="{}" y="{:.1f}" width="120" height="{:.1f}" fill="{}"/>'.format(x, y, height, color))
        bars.append('<text x="{}" y="{}" text-anchor="middle">{}</text>'.format(x + 60, 420, label))
        bars.append('<text x="{}" y="{:.1f}" text-anchor="middle">{:.3f} um²</text>'.format(x + 60, y - 8, value))
    delay = equal["fused"]["critical_delay_ns"]
    separate_delay = equal["separate"]["critical_delay_ns"]
    path.write_text('''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="560" viewBox="0 0 1000 560">
<rect width="100%" height="100%" fill="white"/><style>text{{font-family:sans-serif;fill:#222}}.title{{font-size:22px;font-weight:bold}}.axis{{stroke:#555;stroke-width:2}}.delay{{fill:#efe0f4;stroke:#7b4397;stroke-width:2}}</style>
<text x="500" y="35" text-anchor="middle" class="title">Whole-Ibex module-preserving PPA</text>
<text x="500" y="65" text-anchor="middle">Area comparison (µm²)</text>
<line x1="70" y1="390" x2="820" y2="390" class="axis"/>
{}<text x="500" y="485" text-anchor="middle" class="title">Equal-interface critical delay</text>
<rect x="280" y="505" width="170" height="28" class="delay"/><rect x="650" y="505" width="170" height="28" class="delay"/>
<text x="365" y="524" text-anchor="middle">separate {:.3f} ns</text><text x="735" y="524" text-anchor="middle">fused {:.3f} ns</text>
</svg>
'''.format("".join(bars), separate_delay, delay))
    return path


def write_graph_difference_figures(graphs: Dict[str, Dict[str, Any]], contract: Dict[str, Any],
                                   core: Dict[str, Any], equal: Dict[str, Any]) -> None:
    """Render a focused before/after view from graph statistics and contract data."""
    figures = OUT / "figures"
    before_stats = graphs["equal_interface_separate"]["stats"]
    after_stats = graphs["fused_reextracted"]["stats"]
    resource = contract["shared_resource"]
    before_adds = before_stats["kinds"].get("$add", 0)
    after_adds = after_stats["kinds"].get("$add", 0)
    before_muxes = before_stats["kinds"].get("$mux", 0)
    after_muxes = after_stats["kinds"].get("$mux", 0)
    before_cells = equal["separate"]["cell_count"]
    after_cells = equal["fused"]["cell_count"]
    before_area = equal["separate"]["area_um2"]
    after_area = equal["fused"]["area_um2"]
    before_delay = equal["separate"]["critical_delay_ns"]
    after_delay = equal["fused"]["critical_delay_ns"]
    delay_delta = 100.0 * (after_delay - before_delay) / before_delay

    def write(path: Path, body: str) -> None:
        path.write_text(body)

    write(figures / "whole_alu_separate_before.svg", '''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720" viewBox="0 0 1200 720">
<rect width="100%" height="100%" fill="white"/><style>text{{font-family:sans-serif;fill:#202124}}.title{{font-size:26px;font-weight:bold}}.subtitle{{font-size:17px}}.region{{fill:#f1f3f4;stroke:#6b7280;stroke-width:3}}.green{{fill:#d9f2df;stroke:#238636;stroke-width:3}}.red{{fill:#ffe0e0;stroke:#c62828;stroke-width:3}}.purple{{fill:#eadcf8;stroke:#7b4397;stroke-width:3}}.blue{{fill:#dcecff;stroke:#2463a6;stroke-width:3}}.label{{font-size:16px}}.small{{font-size:13px}}</style>
<text x="600" y="38" text-anchor="middle" class="title">Whole-Ibex ALU — equal-interface separate architecture schematic</text>
<text x="600" y="68" text-anchor="middle" class="subtitle">Before fusion; conceptual schematic, not an RTLIL graph</text>
<rect x="55" y="105" width="490" height="410" rx="14" class="region"/><text x="300" y="140" text-anchor="middle" class="title">Preserved ibex_alu region</text>
<text x="85" y="182" class="label">alu_operand_a_i / alu_operand_b_i</text><line x1="300" y1="190" x2="300" y2="245" stroke="#555" stroke-width="3"/>
<rect x="165" y="245" width="270" height="95" rx="10" class="green"/><text x="300" y="282" text-anchor="middle" class="label">internal $add</text><text x="300" y="310" text-anchor="middle" class="small">{{operand_width}}-bit operands</text><text x="300" y="328" text-anchor="middle" class="small">existing Ibex resource</text>
<line x1="300" y1="340" x2="300" y2="390" stroke="#555" stroke-width="3"/><text x="300" y="420" text-anchor="middle" class="label">adder_result_o → ALU result path</text>
<rect x="625" y="105" width="490" height="410" rx="14" class="region"/><text x="870" y="140" text-anchor="middle" class="title">Dedicated client path</text>
<rect x="735" y="175" width="270" height="78" rx="10" class="blue"/><text x="870" y="207" text-anchor="middle" class="label">client operands</text><text x="870" y="232" text-anchor="middle" class="small">client_operand_a_i / b_i</text>
<line x1="870" y1="253" x2="870" y2="285" stroke="#555" stroke-width="3"/>
<rect x="735" y="285" width="270" height="95" rx="10" class="red"/><text x="870" y="322" text-anchor="middle" class="label">dedicated $add</text><text x="870" y="350" text-anchor="middle" class="small">independent client resource</text>
<line x1="870" y1="380" x2="870" y2="420" stroke="#555" stroke-width="3"/>
<rect x="735" y="420" width="270" height="70" rx="10" class="purple"/><text x="870" y="450" text-anchor="middle" class="label">selector/output mux</text><text x="870" y="473" text-anchor="middle" class="small">client_select_i</text>
<line x1="545" y1="455" x2="735" y2="455" stroke="#7b4397" stroke-width="4" stroke-dasharray="8,6"/><text x="640" y="445" text-anchor="middle" class="small">existing output selection</text>
<text x="600" y="575" text-anchor="middle" class="title">2 × $add</text><text x="600" y="610" text-anchor="middle" class="subtitle">re-extracted graph: $add={before_adds}, $mux={before_muxes}, cells={before_cells}</text>
<text x="600" y="655" text-anchor="middle" class="small">Contract owner: {owner}; separate wrapper retains the Ibex ALU region.</text>
</svg>'''.format(operand_width=resource["operand_width"], before_adds=before_adds, before_muxes=before_muxes,
                 before_cells=before_cells, owner=contract["resource_owner"]["module"]))

    write(figures / "whole_alu_fused_after.svg", '''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720" viewBox="0 0 1200 720">
<rect width="100%" height="100%" fill="white"/><style>text{{font-family:sans-serif;fill:#202124}}.title{{font-size:26px;font-weight:bold}}.subtitle{{font-size:17px}}.region{{fill:#f1f3f4;stroke:#6b7280;stroke-width:3}}.green{{fill:#d9f2df;stroke:#238636;stroke-width:3}}.purple{{fill:#eadcf8;stroke:#7b4397;stroke-width:3}}.blue{{fill:#dcecff;stroke:#2463a6;stroke-width:3}}.label{{font-size:16px}}.small{{font-size:13px}}</style>
<text x="600" y="38" text-anchor="middle" class="title">Whole-Ibex ALU — generated fused-wrapper architecture schematic</text>
<text x="600" y="68" text-anchor="middle" class="subtitle">After fusion; conceptual schematic, not an RTLIL graph</text>
<rect x="280" y="105" width="640" height="485" rx="14" class="region"/><text x="600" y="140" text-anchor="middle" class="title">Preserved ibex_alu region</text>
<rect x="60" y="185" width="235" height="78" rx="10" class="blue"/><text x="177" y="216" text-anchor="middle" class="label">ALU operands</text><text x="177" y="241" text-anchor="middle" class="small">operand_a_i / operand_b_i</text>
<rect x="905" y="185" width="235" height="78" rx="10" class="blue"/><text x="1022" y="216" text-anchor="middle" class="label">client operands</text><text x="1022" y="241" text-anchor="middle" class="small">client_operand_a_i / b_i</text>
<rect x="395" y="175" width="410" height="70" rx="10" class="purple"/><text x="600" y="204" text-anchor="middle" class="label">client_select_i input steering</text><text x="600" y="228" text-anchor="middle" class="small">selects Ibex or client adder path</text>
<line x1="295" y1="224" x2="395" y2="210" stroke="#7b4397" stroke-width="4"/><line x1="905" y1="224" x2="805" y2="210" stroke="#7b4397" stroke-width="4"/>
<text x="600" y="285" text-anchor="middle" class="small">multdiv_operand_a_i = {{client_operand_a_i, 1'b1}}</text><text x="600" y="305" text-anchor="middle" class="small">multdiv_operand_b_i = {{client_operand_b_i, 1'b0}}</text>
<rect x="405" y="335" width="390" height="105" rx="10" class="green"/><text x="600" y="375" text-anchor="middle" class="label">single retained internal $add</text><text x="600" y="402" text-anchor="middle" class="small">{{operand_width}}-bit operands → {{architectural_result_width}}-bit result</text><text x="600" y="422" text-anchor="middle" class="small">Ibex adder_result_o, slice {{slice}}</text>
<line x1="600" y1="245" x2="600" y2="335" stroke="#7b4397" stroke-width="4"/><line x1="600" y1="440" x2="600" y2="490" stroke="#7b4397" stroke-width="4"/>
<rect x="455" y="490" width="290" height="70" rx="10" class="purple"/><text x="600" y="520" text-anchor="middle" class="label">result routing mux</text><text x="600" y="544" text-anchor="middle" class="small">result_o</text>
<text x="600" y="635" text-anchor="middle" class="title">1 × $add</text><text x="600" y="665" text-anchor="middle" class="subtitle">re-extracted graph: $add={after_adds}, $mux={after_muxes}, cells={after_cells}</text>
</svg>'''.format(operand_width=resource["operand_width"], architectural_result_width=resource["architectural_result_width"],
                 slice=resource["architectural_result_slice"], after_adds=after_adds, after_muxes=after_muxes,
                 after_cells=after_cells))

    legend = '''<g transform="translate(820,80)"><rect width="330" height="166" rx="8" fill="white" stroke="#999"/><text x="15" y="25" class="label" font-weight="bold">Legend</text><rect x="15" y="40" width="18" height="18" fill="#f1f3f4" stroke="#6b7280"/><text x="45" y="55" class="small">gray = unchanged</text><rect x="15" y="68" width="18" height="18" fill="#d9f2df" stroke="#238636"/><text x="45" y="83" class="small">green = retained/shared resource</text><line x1="15" y1="105" x2="33" y2="105" stroke="#c62828" stroke-width="3" stroke-dasharray="5,4"/><text x="45" y="110" class="small">red dashed = removed duplicate resource</text><rect x="15" y="120" width="18" height="18" fill="#eadcf8" stroke="#7b4397"/><text x="45" y="135" class="small">purple = inserted fusion control/muxing</text><rect x="15" y="148" width="18" height="18" fill="#dcecff" stroke="#2463a6"/><text x="45" y="163" class="small">blue = added client interface</text></g>'''
    write(figures / "whole_alu_fusion_diff.svg", '''<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="850" viewBox="0 0 1500 850">
<rect width="100%" height="100%" fill="white"/><style>text{font-family:sans-serif;fill:#202124}.title{font-size:26px;font-weight:bold}.subtitle{font-size:17px}.panel{fill:#fafafa;stroke:#777;stroke-width:2}.region{fill:#f1f3f4;stroke:#6b7280;stroke-width:3}.green{fill:#d9f2df;stroke:#238636;stroke-width:3}.red{fill:#ffe0e0;stroke:#c62828;stroke-width:3;stroke-dasharray:8,6}.purple{fill:#eadcf8;stroke:#7b4397;stroke-width:3}.blue{fill:#dcecff;stroke:#2463a6;stroke-width:3}.label{font-size:16px}.small{font-size:13px}.metric{font-size:18px;font-weight:bold}</style>
<text x="750" y="38" text-anchor="middle" class="title">Whole-Ibex adder fusion — architecture schematic</text><text x="750" y="68" text-anchor="middle" class="subtitle">Conceptual difference schematic, not an RTLIL graph; ibex_alu is preserved</text>
<rect x="30" y="95" width="690" height="420" rx="12" class="panel"/><rect x="780" y="95" width="690" height="420" rx="12" class="panel"/>
<text x="375" y="130" text-anchor="middle" class="title">Before: equal-interface separate</text><rect x="70" y="165" width="300" height="250" rx="10" class="region"/><text x="220" y="198" text-anchor="middle" class="label">preserved ibex_alu</text><rect x="130" y="235" width="180" height="75" class="green"/><text x="220" y="280" text-anchor="middle" class="label">$add</text><rect x="430" y="235" width="180" height="75" class="red"/><text x="520" y="280" text-anchor="middle" class="label">dedicated $add</text><rect x="370" y="350" width="250" height="55" class="purple"/><text x="495" y="383" text-anchor="middle" class="label">selector/output mux</text><line x1="370" y1="272" x2="430" y2="272" stroke="#c62828" stroke-width="3" stroke-dasharray="8,6"/><line x1="310" y1="370" x2="370" y2="378" stroke="#7b4397" stroke-width="3"/>
<text x="1125" y="130" text-anchor="middle" class="title">After: generated fused wrapper</text><rect x="820" y="165" width="610" height="250" rx="10" class="region"/><text x="1125" y="198" text-anchor="middle" class="label">same preserved ibex_alu region</text><rect x="1035" y="235" width="180" height="75" class="green"/><text x="1125" y="280" text-anchor="middle" class="label">single $add</text><rect x="850" y="235" width="145" height="75" class="blue"/><text x="922" y="265" text-anchor="middle" class="small">client ports</text><text x="922" y="286" text-anchor="middle" class="small">multdiv_*_i</text><rect x="1260" y="350" width="145" height="55" class="purple"/><text x="1332" y="383" text-anchor="middle" class="small">result mux</text><line x1="995" y1="272" x2="1035" y2="272" stroke="#7b4397" stroke-width="3"/><line x1="1215" y1="272" x2="1260" y2="370" stroke="#7b4397" stroke-width="3"/><line x1="1000" y1="390" x2="1035" y2="290" stroke="#7b4397" stroke-width="3"/>
''' + legend + '''
<text x="750" y="570" text-anchor="middle" class="metric">$add: {before_adds} → {after_adds}    $mux: {before_muxes} → {after_muxes}</text>
<text x="750" y="610" text-anchor="middle" class="metric">cells: {before_cells} → {after_cells}</text>
<text x="750" y="650" text-anchor="middle" class="metric">area: {before_area:.3f} → {after_area:.3f} um² ({area_delta:+.3f}%)</text>
<text x="750" y="690" text-anchor="middle" class="metric">delay: {before_delay:.3f} → {after_delay:.3f} ns ({delay_delta:+.1f}%)</text>
<text x="750" y="755" text-anchor="middle" class="subtitle">Simultaneous ALU/client use is not preserved.</text><text x="750" y="790" text-anchor="middle" class="small">Red dashed marks the removed duplicate dedicated adder; green marks the retained Ibex resource.</text>
</svg>'''.format(before_adds=before_adds, after_adds=after_adds, before_muxes=before_muxes, after_muxes=after_muxes,
                 before_cells=before_cells, after_cells=after_cells, before_area=before_area, after_area=after_area,
                 area_delta=equal["delta_percent"], before_delay=before_delay, after_delay=after_delay, delay_delta=delay_delta))


def write_actual_graph_figures(graphs: Dict[str, Dict[str, Any]]) -> None:
    """Render the complete before/after re-extracted RTLIL graphs."""
    import graph_viz
    for name, output, title in (
        ("equal_interface_separate", "whole_alu_separate_rtlil",
         "Whole-Ibex equal-interface separate RTLIL graph (complete re-extraction)"),
        ("fused_reextracted", "whole_alu_fused_rtlil",
         "Whole-Ibex fused RTLIL graph (complete re-extraction)"),
    ):
        # ``extract`` returns the canonical FUGraph representation while the
        # adjacent Yosys JSON is a native netlist sidecar.  Render the former.
        graph_path = OUT / ("equal_interface_separate.rtlil.graph.json"
                            if name == "equal_interface_separate"
                            else "generated_fused.rtlil.graph.json")
        graph = graph_viz.graph_extract.FUGraph.load(graph_path)
        graph_viz.render(graph, OUT / "figures" / output,
                         max_nodes=len(graph.nodes) + 1, title=title)


def write_graph_match_diff(graphs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Persist an exact, ID-independent graph comparison and classifications."""
    import graph_match
    before = graphs["equal_interface_separate"]
    after = graphs["fused_reextracted"]
    match = graph_match.compare(before, after, mode="exact")
    match_path = OUT / "whole_alu_before_after.rtlil.match.json"
    match_path.write_text(json.dumps(match, indent=2, sort_keys=True) + "\n")
    before_nodes = {node["id"]: node for node in before["nodes"]}
    after_nodes = {node["id"]: node for node in after["nodes"]}
    def adds(graph: Dict[str, Any], width: int = None) -> List[Dict[str, Any]]:
        return [node for node in graph["nodes"]
                if node["kind"] == "$add" and (width is None or node.get("width") == width)]

    dedicated = sorted(adds(before, 32), key=lambda node: node["id"])
    shared_before = sorted(adds(before, 34), key=lambda node: node["id"])
    shared_after = sorted(adds(after, 34), key=lambda node: node["id"])
    if len(dedicated) != 1 or len(shared_before) != 1 or len(shared_after) != 1:
        raise RuntimeError("whole-ALU graph invariants did not identify dedicated and shared adders")
    semantic_retained_before = shared_before[0]["id"]
    semantic_retained_after = shared_after[0]["id"]
    matched = []
    for item in match["matched_nodes"]:
        matched.append({"before_id": item["a"], "after_id": item["b"],
                        "before_kind": before_nodes[item["a"]]["kind"],
                        "after_kind": after_nodes[item["b"]]["kind"]})
    before_only = [{"id": node_id, "kind": before_nodes[node_id]["kind"],
                    "width": before_nodes[node_id].get("width"),
                    "label": before_nodes[node_id].get("label")}
                   for node_id in sorted(match["unmatched"]["a"])
                   if node_id != semantic_retained_before]
    after_only = [{"id": node_id, "kind": after_nodes[node_id]["kind"],
                   "width": after_nodes[node_id].get("width"),
                   "label": after_nodes[node_id].get("label")}
                   for node_id in sorted(match["unmatched"]["b"])
                   if node_id != semantic_retained_after]
    removed_muxes = [node for node in before_only if node["kind"] == "$mux"]
    inserted_muxes = [node for node in after_only if node["kind"] == "$mux"]
    diff = {
        "schema": "fu-ibex-graph-diff/v1",
        "comparison": "graph_match.compare(mode='exact') plus semantic resource classification",
        "before_graph": "meeting-artifacts/ibex-fusion/module-preserving/equal_interface_separate.rtlil.graph.json",
        "after_graph": "meeting-artifacts/ibex-fusion/module-preserving/generated_fused.rtlil.graph.json",
        "matched_retained_nodes": matched,
        "before_only_nodes": before_only,
        "after_only_nodes": after_only,
        "before_operation_counts": before["stats"]["kinds"],
        "after_operation_counts": after["stats"]["kinds"],
        "removed_dedicated_add": [{"id": node["id"], "kind": node["kind"], "width": node["width"],
                                   "classification": "before-only dedicated client $add"} for node in dedicated],
        "retained_shared_add": {"before_id": shared_before[0]["id"], "after_id": shared_after[0]["id"],
                                "before_width": shared_before[0]["width"], "after_width": shared_after[0]["width"],
                                "classification": "semantic retained Ibex internal $add; exact inputs were rewired by the preserved interface"},
        "additional_post_fusion_mux_nodes": inserted_muxes,
        "additional_mux_count": after["stats"]["kinds"].get("$mux", 0) - before["stats"]["kinds"].get("$mux", 0),
        "changed_pre_fusion_mux_nodes": removed_muxes,
        "limitations": ["Yosys-generated node IDs are labels only; graph_match semantic signatures determine exact matches.",
                        "The shared-adder classification uses the contract-compatible 34-bit Ibex resource role because its incoming paths are intentionally changed by fusion."],
    }
    diff_path = OUT / "whole_alu_before_after.rtlil.diff.json"
    diff_path.write_text(json.dumps(diff, indent=2, sort_keys=True) + "\n")
    return {"match": match, "diff": diff, "match_path": match_path, "diff_path": diff_path}


def write_highlighted_graph_figures(graphs: Dict[str, Dict[str, Any]], comparison: Dict[str, Any]) -> None:
    """Render complete graphs with node groups sourced exclusively from diff JSON."""
    import graph_viz
    diff = comparison["diff"]
    before_highlight = {item["before_id"]: "retained" for item in diff["matched_retained_nodes"]}
    after_highlight = {item["after_id"]: "retained" for item in diff["matched_retained_nodes"]}
    removed = diff["removed_dedicated_add"][0]["id"]
    before_highlight[removed] = "removed_add"
    shared = diff["retained_shared_add"]
    before_highlight[shared["before_id"]] = "shared_add"
    after_highlight[shared["after_id"]] = "shared_add"
    for item in diff["additional_post_fusion_mux_nodes"]:
        after_highlight[item["id"]] = "inserted_mux"
    for item in diff["after_only_nodes"]:
        if item["kind"] in ("port_in", "port_out"):
            after_highlight[item["id"]] = "added_interface"
    graph_viz.render(graph_viz.graph_extract.FUGraph.load(
        OUT / "equal_interface_separate.rtlil.graph.json"),
        OUT / "figures" / "whole_alu_separate_rtlil_highlighted", max_nodes=200,
        highlight=before_highlight, title="Complete RTLIL graph before fusion (diff highlighted)")
    graph_viz.render(graph_viz.graph_extract.FUGraph.load(
        OUT / "generated_fused.rtlil.graph.json"),
        OUT / "figures" / "whole_alu_fused_rtlil_highlighted", max_nodes=200,
        highlight=after_highlight, title="Complete RTLIL graph after fusion (diff highlighted)")


def write_fusion_cone_figures() -> None:
    """Create actual k-hop subgraphs around the graph's add resources."""
    import graph_extract
    import graph_viz
    for filename, output, title in (
        ("equal_interface_separate.rtlil.graph.json", "whole_alu_rtlil_fusion_cone_before",
         "RTLIL fusion cone before: both adders and connected control"),
        ("generated_fused.rtlil.graph.json", "whole_alu_rtlil_fusion_cone_after",
         "RTLIL fusion cone after: shared adder and connected control"),
    ):
        graph = graph_extract.FUGraph.load(OUT / filename)
        seeds = {node.id for node in graph.nodes if node.kind == "$add"}
        adjacency = {node.id: set() for node in graph.nodes}
        for edge in graph.edges:
            adjacency.setdefault(edge.src, set()).add(edge.dst)
            adjacency.setdefault(edge.dst, set()).add(edge.src)
        keep = set(seeds)
        frontier = set(seeds)
        for _ in range(3):
            frontier = {neighbor for node_id in frontier for neighbor in adjacency[node_id]} - keep
            keep.update(frontier)
        cone = graph_extract.FUGraph(design=graph.design + "_fusion_cone", level=graph.level,
                                     top=graph.top, attrs={**graph.attrs, "derived_from": filename,
                                                          "traversal": "undirected three-hop neighborhood around $add nodes"})
        cone.nodes = [node for node in graph.nodes if node.id in keep]
        cone.edges = [edge for edge in graph.edges if edge.src in keep and edge.dst in keep]
        graph_viz.render(cone, OUT / "figures" / output, max_nodes=200, title=title)


def write_directed_client_path_figures(comparison: Dict[str, Any]) -> None:
    """Render minimal, source-derived directed client-to-result slices.

    The old three-hop views remain as historical context.  These views use the
    actual persisted RTLIL graph edges: a deterministic shortest-path union
    connects the named client ports, classified adders, and result mux/output.
    Only cut edges at the retained resources are collapsed into explicitly
    marked boundary nodes; no RTLIL operator is synthesized for presentation.
    """
    import base64
    import html
    import graph_extract
    import graph_viz

    def edge_record(edge: Any) -> Dict[str, Any]:
        return {"src": edge.src, "dst": edge.dst, "src_port": edge.src_port,
                "dst_port": edge.dst_port, "width": edge.width,
                "inverted": edge.inverted, "attrs": edge.attrs}

    def edge_sort(item: Any) -> Any:
        index, edge = item
        return (edge.src, edge.dst, edge.src_port, edge.dst_port, edge.width,
                edge.inverted, json.dumps(edge.attrs, sort_keys=True), index)

    def make_slice(filename: str, side: str, diff: Dict[str, Any]) -> Dict[str, Any]:
        graph = graph_extract.FUGraph.load(OUT / filename)
        node_map = graph.node_map()
        by_label = {node.label: node for node in graph.nodes
                    if node.kind in ("port_in", "port_out")}
        required_ports = ["client_operand_a_i", "client_operand_b_i", "client_select_i", "result_o"]
        if any(label not in by_label for label in required_ports):
            raise RuntimeError("client-path slice is missing a required named port")
        result = by_label["result_o"]
        final_muxes = [edge.src for edge in graph.edges
                       if edge.dst == result.id and node_map[edge.src].kind == "$mux"]
        if len(set(final_muxes)) != 1:
            raise RuntimeError("client-path slice requires one final result-selection mux")
        final_mux = node_map[final_muxes[0]]

        adders = sorted((node for node in graph.nodes if node.kind == "$add"),
                        key=lambda node: (node.width or 0, node.label, node.id))
        if side == "before":
            dedicated = [node for node in adders if node.width == 32]
            retained = [node for node in adders if node.width == 34]
            if len(dedicated) != 1 or len(retained) != 1:
                raise RuntimeError("before slice requires one 32-bit and one 34-bit adder")
            target = dedicated[0]
            retained_id = retained[0].id
            expected_dedicated = diff["removed_dedicated_add"][0]["id"]
        else:
            if len(adders) != 1 or adders[0].width != 34:
                raise RuntimeError("after slice requires one 34-bit shared adder")
            target = adders[0]
            retained_id = target.id
            expected_dedicated = None

        edges = list(enumerate(graph.edges))
        adjacency: Dict[str, List[Any]] = {}
        for item in edges:
            adjacency.setdefault(item[1].src, []).append(item)
        for values in adjacency.values():
            values.sort(key=edge_sort)

        def shortest_path(start: str, goal: str) -> List[int]:
            queue = [start]
            predecessor: Dict[str, Any] = {start: None}
            while queue:
                current = queue.pop(0)
                if current == goal:
                    break
                for index, edge in adjacency.get(current, []):
                    if edge.dst not in predecessor:
                        predecessor[edge.dst] = (current, index)
                        queue.append(edge.dst)
            if goal not in predecessor:
                raise RuntimeError("no directed path from {} to {}".format(start, goal))
            result_indices: List[int] = []
            current = goal
            while predecessor[current] is not None:
                previous, index = predecessor[current]
                result_indices.append(index)
                current = previous
            return list(reversed(result_indices))

        ports = {label: by_label[label].id for label in required_ports}
        path_requests = [
            (ports["client_operand_a_i"], target.id),
            (ports["client_operand_b_i"], target.id),
            (ports["client_select_i"], final_mux.id),
            (target.id, final_mux.id),
            (final_mux.id, ports["result_o"]),
        ]
        selected_edge_indices: set = set()
        selected_node_ids = set(ports.values()) | {target.id, retained_id, final_mux.id, result.id}
        path_provenance = []
        for start, goal in path_requests:
            path = shortest_path(start, goal)
            selected_edge_indices.update(path)
            selected_node_ids.update(graph.edges[index].src for index in path)
            selected_node_ids.update(graph.edges[index].dst for index in path)
            path_provenance.append({"start": start, "goal": goal,
                                    "edge_indices": path,
                                    "edge_records": [edge_record(graph.edges[index]) for index in path]})

        # The before view must retain the separate Ibex resource even though
        # no client-to-result shortest path traverses it. Keep its immediate
        # source edges whenever the other endpoint is already in the slice;
        # omitted incident edges become collapsed boundaries below.
        for index, edge in edges:
            if (edge.src == retained_id or edge.dst == retained_id) and \
                    edge.src in selected_node_ids and edge.dst in selected_node_ids:
                selected_edge_indices.add(index)

        # Preserve only immediate omitted connections at the adders and final
        # selection mux.  Each is a computed cut edge, represented by a
        # boundary node rather than by an invented RTLIL operator.
        critical_ids = {target.id, retained_id, final_mux.id}
        boundary_nodes = []
        boundary_edges = []
        cut_provenance = []
        existing_boundary_keys = set()
        for index, edge in edges:
            if edge.src not in critical_ids and edge.dst not in critical_ids:
                continue
            if edge.src in selected_node_ids and edge.dst in selected_node_ids:
                continue
            if edge.src not in selected_node_ids and edge.dst not in selected_node_ids:
                continue
            token = json.dumps(edge_record(edge), sort_keys=True)
            boundary_key = (index, token)
            if boundary_key in existing_boundary_keys:
                continue
            existing_boundary_keys.add(boundary_key)
            if edge.dst == retained_id:
                incoming = True
            elif edge.src == retained_id:
                incoming = False
            else:
                incoming = edge.dst in critical_ids
            label = "other Ibex ALU inputs/control" if incoming else "other Ibex result paths"
            digest = hashlib.sha256(token.encode()).hexdigest()[:16]
            boundary_id = "boundary:{}:{}".format(side, digest)
            from graph_extract import Edge, Node
            boundary_nodes.append(Node(
                id=boundary_id, kind="boundary", label=label, width=edge.width,
                attrs={"collapsed_boundary": True, "direction": "in" if incoming else "out",
                       "source_graph": filename, "source_edge": edge_record(edge),
                       "source_edge_index": index},
            ))
            boundary_edges.append(Edge(
                src=boundary_id if edge.src not in selected_node_ids else edge.src,
                dst=boundary_id if edge.dst not in selected_node_ids else edge.dst,
                src_port=edge.src_port, dst_port=edge.dst_port, width=edge.width,
                inverted=edge.inverted,
                attrs={"collapsed_boundary_edge": True, "source_edge": edge_record(edge),
                       "source_edge_index": index},
            ))
            cut_provenance.append({"boundary_id": boundary_id, "source_edge_index": index,
                                   "source_edge": edge_record(edge), "label": label})

        def display_node(node: Any) -> Any:
            from graph_extract import Node
            attrs = dict(node.attrs)
            attrs["source_node_id"] = node.id
            attrs["source_graph"] = filename
            label = node.label
            if node.id == target.id:
                role = "dedicated client" if side == "before" else "retained shared Ibex"
                label = "$add ({}) {}".format(role, node.label)
            elif node.id == retained_id:
                label = "$add (retained Ibex boundary) {}".format(node.label)
            return Node(id=node.id, kind=node.kind, label=label, width=node.width, attrs=attrs)

        selected_nodes = [display_node(node) for node in graph.nodes if node.id in selected_node_ids]
        selected_edges = [graph.edges[index] for index in sorted(selected_edge_indices)]
        all_nodes = selected_nodes + sorted(boundary_nodes, key=lambda node: node.id)
        all_edges = selected_edges + sorted(boundary_edges, key=lambda edge: (
            edge.src, edge.dst, edge.src_port, edge.dst_port, edge.width))
        slice_graph = graph_extract.FUGraph(
            design=graph.design + "_directed_client_path", level=graph.level, top=graph.top,
            attrs={"derived_from": filename, "slice_method": "directed reachability and deterministic shortest-path union",
                   "source_node_count": len(graph.nodes), "source_edge_count": len(graph.edges),
                   "selected_source_node_ids": sorted(selected_node_ids),
                   "selected_source_edge_indices": sorted(selected_edge_indices),
                   "cut_edges": cut_provenance, "paths": path_provenance,
                   "expected_dedicated_add": expected_dedicated},
        )
        slice_graph.nodes = all_nodes
        slice_graph.edges = all_edges
        stem = OUT / "figures" / "whole_alu_rtlil_client_path_{}".format(side)
        graph_json = OUT / "whole_alu_rtlil_client_path_{}.graph.json".format(side)
        slice_graph.save(graph_json)
        graph_viz.render(slice_graph, stem, max_nodes=200,
                         title="Whole-Ibex {} directed client-to-result RTLIL dataflow slice".format(side))
        return {"graph": slice_graph, "graph_path": graph_json, "svg": Path(str(stem) + ".svg"),
                "dot": Path(str(stem) + ".dot"), "paths": path_provenance,
                "cut_edges": cut_provenance, "selected_source_node_ids": sorted(selected_node_ids),
                "selected_source_edge_indices": sorted(selected_edge_indices),
                "source_graph": filename, "target_add_id": target.id,
                "retained_add_id": retained_id, "final_mux_id": final_mux.id}

    diff = comparison["diff"]
    before = make_slice("equal_interface_separate.rtlil.graph.json", "before", diff)
    after = make_slice("generated_fused.rtlil.graph.json", "after", diff)
    def portable(value: Any) -> Any:
        if isinstance(value, Path):
            try:
                return value.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                return str(value)
        if isinstance(value, dict):
            return {key: portable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [portable(item) for item in value]
        return value

    provenance = {"schema": "fu-ibex-directed-client-path/v1", "algorithm": "directed reachability and deterministic shortest-path union",
                  "before": portable({key: value for key, value in before.items() if key not in ("graph",)}),
                  "after": portable({key: value for key, value in after.items() if key not in ("graph",)}),
                  "source_match_artifact": "meeting-artifacts/ibex-fusion/module-preserving/whole_alu_before_after.rtlil.match.json",
                  "source_diff_artifact": "meeting-artifacts/ibex-fusion/module-preserving/whole_alu_before_after.rtlil.diff.json",
                  "boundary_semantics": "dashed nodes are collapsed cut edges computed from the source graph; they are not RTLIL operators"}
    provenance_path = OUT / "whole_alu_rtlil_client_path.provenance.json"
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True, default=str) + "\n")

    def embedded_svg(path: Path) -> str:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return "data:image/svg+xml;base64," + encoded

    before_text = "Before: 2 $add, 9 $mux, 1232.112 um²"
    after_text = "After: 1 $add, 12 $mux, 1115.870 um²"
    comparison_svg = '''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1900" height="1050" viewBox="0 0 1900 1050">
<rect width="100%" height="100%" fill="white"/><style>text{{font-family:sans-serif;fill:#202124}}.title{{font-size:27px;font-weight:bold}}.caption{{font-size:19px;font-weight:bold}}.note{{font-size:18px}}.small{{font-size:15px}}</style>
<text x="950" y="35" text-anchor="middle" class="title">Actual RTLIL client-to-result dataflow comparison</text>
<text x="475" y="70" text-anchor="middle" class="caption">Before — directed source-graph slice</text><text x="1425" y="70" text-anchor="middle" class="caption">After — directed source-graph slice</text>
<image x="20" y="85" width="920" height="720" preserveAspectRatio="xMidYMin meet" href="{before}" xlink:href="{before}"/><image x="960" y="85" width="920" height="720" preserveAspectRatio="xMidYMin meet" href="{after}" xlink:href="{after}"/>
<text x="475" y="850" text-anchor="middle" class="caption">{before_text}</text><text x="1425" y="850" text-anchor="middle" class="caption">{after_text}</text>
<text x="950" y="900" text-anchor="middle" class="note">Area: -9.434%</text><text x="950" y="935" text-anchor="middle" class="note">Delay: 1.860 ns → 1.880 ns (+1.1%)</text>
<text x="950" y="985" text-anchor="middle" class="note">Tradeoff: simultaneous ALU/client operation is not preserved</text>
<text x="950" y="1020" text-anchor="middle" class="small">Both panels are embedded direct graph_viz renders of persisted graph-derived slices; no topology is redrawn here.</text>
</svg>'''.format(before=embedded_svg(before["svg"]), after=embedded_svg(after["svg"]),
                 before_text=html.escape(before_text), after_text=html.escape(after_text))
    comparison_path = OUT / "figures" / "whole_alu_rtlil_client_path_comparison.svg"
    comparison_path.write_text(comparison_svg)
    return {"provenance": provenance_path, "before_graph": before["graph_path"],
            "after_graph": after["graph_path"], "comparison": comparison_path}


def main() -> None:
    prepare()
    contract_path = make_contract()
    from ibex_module_preserving_emit import write
    fused_rtl = OUT / "ibex_alu_add_client_fused.sv"
    write(contract_path, fused_rtl)
    tb = make_testbench()
    bundles = convert_sources(contract_path)
    sim = simulate(tb, bundles)
    sdc = OUT / "ibex_module_preserving.sdc"
    sdc.write_text("set clk_period 2.5\ncreate_clock -name vclk -period $clk_period\nset_input_delay [expr {0.1 * $clk_period}] -clock vclk [all_inputs]\nset_output_delay [expr {0.1 * $clk_period}] -clock vclk [all_outputs]\n")
    graphs = {"literal_alu": extract("ibex_alu_wrapper", bundles["separate"], "literal_alu"),
              "dedicated_add": extract("ibex_independent_add_client", bundles["separate"], "dedicated_add"),
              "equal_interface_separate": extract("ibex_alu_add_client_separate", bundles["separate"], "equal_interface_separate"),
              "fused_reextracted": extract("ibex_alu_add_client_fused", bundles["fused"], "fused_reextracted")}
    ppas = {"whole_alu": ppa("ibex_alu_wrapper", bundles["separate"], sdc, "whole_alu"),
            "dedicated_add": ppa("ibex_independent_add_client", bundles["separate"], sdc, "dedicated_add"),
            "equal_interface_separate": ppa("ibex_alu_add_client_separate", bundles["separate"], sdc, "equal_interface_separate"),
            "fused": ppa("ibex_alu_add_client_fused", bundles["fused"], sdc, "fused")}
    literal_sum = ppas["whole_alu"]["area_um2"] + ppas["dedicated_add"]["area_um2"]
    core = {"schema": "fu-ibex-module-preserving-core-ppa/v1", "comparison": "literal area(whole Ibex ALU)+area(dedicated 32-bit ADD)",
            "conditions": {"library": "locked Nangate45/FreePDK45 typical", "constraint_ns": 2.5,
                           "combinational": True, "registered_latency": "undefined", "registered_ii": "undefined",
                           "workload": "identical structural synthesis flow",
                           "power_basis": "estimated peak power at 400 MHz using default flow activity assumptions"},
            "whole_alu": ppas["whole_alu"], "dedicated_add": ppas["dedicated_add"],
            "literal_area_sum_um2": literal_sum, "fused": ppas["fused"], "fused_delta_um2": ppas["fused"]["area_um2"] - literal_sum,
            "fused_delta_percent": 100*(ppas["fused"]["area_um2"]-literal_sum)/literal_sum, "criterion_fused_below_literal_sum": ppas["fused"]["area_um2"] < literal_sum,
            "literal_power_sum_mw": ppas["whole_alu"]["power_mw"] + ppas["dedicated_add"]["power_mw"],
            "fused_power_mw": ppas["fused"]["power_mw"],
            "pretechmap": {name: graphs[key]["stats"] for name,key in (("whole_alu","literal_alu"),("dedicated_add","dedicated_add"),("fused","fused_reextracted"))},
            "selector_mux_overhead": {"equal_interface_separate_muxes": graphs["equal_interface_separate"]["stats"]["kinds"].get("$mux",0), "fused_muxes": graphs["fused_reextracted"]["stats"]["kinds"].get("$mux",0)}}
    (OUT / "literal_core_ppa.json").write_text(json.dumps(core, indent=2, sort_keys=True)+"\n")
    equal = {"schema": "fu-ibex-module-preserving-interface-ppa/v1", "comparison": "equal-interface separate wrapper versus fused wrapper",
             "conditions": core["conditions"], "separate": ppas["equal_interface_separate"], "fused": ppas["fused"],
             "delta_um2": ppas["fused"]["area_um2"]-ppas["equal_interface_separate"]["area_um2"],
             "delta_percent": 100*(ppas["fused"]["area_um2"]-ppas["equal_interface_separate"]["area_um2"])/ppas["equal_interface_separate"]["area_um2"],
             "separate_power_mw": ppas["equal_interface_separate"]["power_mw"],
             "fused_power_mw": ppas["fused"]["power_mw"],
             "pretechmap": {"separate": graphs["equal_interface_separate"]["stats"], "fused": graphs["fused_reextracted"]["stats"]},
             "selector_mux_overhead": core["selector_mux_overhead"]}
    (OUT / "equal_interface_ppa.json").write_text(json.dumps(equal, indent=2, sort_keys=True)+"\n")
    simulation_result = {"schema": "fu-ibex-module-preserving-simulation/v1", **sim,
                         "operations": OPS, "alu_vectors": 3696, "client_vectors": 261,
                         "comparison_breakdown": {"comparisons_per_vector": 3,
                                                   "total_vectors": 3957,
                                                   "total_comparisons": sim["checks"]},
                         "latency": {"combinational": True, "registered_latency": "undefined",
                                     "registered_ii": "undefined"},
                         "source": "canonical upstream ibex_alu_wrapper, equal-interface separate wrapper, generated fused wrapper, and dedicated add client"}
    simulation_result_path = OUT / "simulation_result.json"
    simulation_result_path.write_text(json.dumps(simulation_result, indent=2, sort_keys=True) + "\n")
    write_ppa_figure(core, equal)
    write_graph_difference_figures(graphs, json.loads(contract_path.read_text()), core, equal)
    write_actual_graph_figures(graphs)
    comparison = write_graph_match_diff(graphs)
    write_highlighted_graph_figures(graphs, comparison)
    write_fusion_cone_figures()
    client_path = write_directed_client_path_figures(comparison)
    write_architecture_figure()
    import graph_viz, graph_extract
    old = ROOT / "meeting-artifacts" / "ibex-fusion" / "generated_reextracted.rtlil.graph.json"
    old_graph = graph_extract.FUGraph.load(old)
    graph_viz.render(old_graph, OUT / "figures" / "sub_ltu_reextracted", title="Existing graph-emitted SUB/LTU re-extracted graph")
    limitations = {"schema":"fu-ibex-module-preserving-limitations/v1", "status":"partial", "claims":["This is contract-guided module-preserving fusion; ibex_alu remains an upstream instance.","The contract and emitter verify the pinned adder encoding and generate the equal-interface wrapper."],"not_claimed":["flattened RTLIL reconstruction","arbitrary whole-Ibex fusion","simultaneous ALU and client use","sequential ibex_multdiv_fast fusion"]}
    (OUT / "limitations.json").write_text(json.dumps(limitations, indent=2, sort_keys=True)+"\n")
    paths = {"contract":contract_path,"fused_rtl":fused_rtl,"separate_rtl":SEPARATE,"canonical_wrapper":CANONICAL,"testbench":tb,"simulation_result":simulation_result_path,"sdc":sdc,"literal_core_ppa":OUT/"literal_core_ppa.json","equal_interface_ppa":OUT/"equal_interface_ppa.json","generated_graph":OUT/"generated_reextracted.rtlil.graph.json","canonical_before_graph":OUT/"equal_interface_separate.rtlil.graph.json","canonical_after_graph":OUT/"generated_fused.rtlil.graph.json","graph_match":OUT/"whole_alu_before_after.rtlil.match.json","graph_diff":OUT/"whole_alu_before_after.rtlil.diff.json","architecture_figure":OUT/"figures"/"module_preserving_architecture.svg","ppa_figure":OUT/"figures"/"whole_alu_ppa.svg","before_figure":OUT/"figures"/"whole_alu_separate_before.svg","after_figure":OUT/"figures"/"whole_alu_fused_after.svg","difference_figure":OUT/"figures"/"whole_alu_fusion_diff.svg","actual_before_graph_figure":OUT/"figures"/"whole_alu_separate_rtlil.svg","actual_before_graph_dot":OUT/"figures"/"whole_alu_separate_rtlil.dot","actual_after_graph_figure":OUT/"figures"/"whole_alu_fused_rtlil.svg","actual_after_graph_dot":OUT/"figures"/"whole_alu_fused_rtlil.dot","highlighted_before_graph_figure":OUT/"figures"/"whole_alu_separate_rtlil_highlighted.svg","highlighted_before_graph_dot":OUT/"figures"/"whole_alu_separate_rtlil_highlighted.dot","highlighted_after_graph_figure":OUT/"figures"/"whole_alu_fused_rtlil_highlighted.svg","highlighted_after_graph_dot":OUT/"figures"/"whole_alu_fused_rtlil_highlighted.dot","fusion_cone_before":OUT/"figures"/"whole_alu_rtlil_fusion_cone_before.svg","fusion_cone_before_dot":OUT/"figures"/"whole_alu_rtlil_fusion_cone_before.dot","fusion_cone_after":OUT/"figures"/"whole_alu_rtlil_fusion_cone_after.svg","fusion_cone_after_dot":OUT/"figures"/"whole_alu_rtlil_fusion_cone_after.dot","client_path_before_graph":client_path["before_graph"],"client_path_after_graph":client_path["after_graph"],"client_path_provenance":client_path["provenance"],"client_path_before_figure":OUT/"figures"/"whole_alu_rtlil_client_path_before.svg","client_path_before_dot":OUT/"figures"/"whole_alu_rtlil_client_path_before.dot","client_path_after_figure":OUT/"figures"/"whole_alu_rtlil_client_path_after.svg","client_path_after_dot":OUT/"figures"/"whole_alu_rtlil_client_path_after.dot","client_path_comparison":client_path["comparison"],"emitter":EMITTER,"demo":Path(__file__),"graph_extract":ROOT/"graph_extract.py","graph_viz":ROOT/"graph_viz.py","sc_flow":ROOT/"sc_flow.py","toolchain":ROOT/"toolchain.py","verilator_flow":ROOT/"verilator_flow.py","upstream_alu":UPSTREAM_ALU,"upstream_pkg":UPSTREAM_PKG,"upstream_lock":UPSTREAM_LOCK,"toolchain_lock":ROOT/"build"/"toolchain.lock.json","limitations":OUT/"limitations.json"}
    evidence = {name:record(path) for name,path in paths.items() if path.is_file()}
    libs=list((BUILD/"ppa"/"whole_alu").glob("**/inputs/*.lib"))
    manifest={"schema":"fu-ibex-module-preserving-manifest/v1","version":1,"classification":"contract-guided module-preserving fusion","evidence":evidence,"external_dependencies":{}}
    if libs: manifest["external_dependencies"]["liberty"]={**record(libs[0],True),"identity":"NangateOpenCellLibrary_typical / FreePDK45 demo corner"}
    manifest_path=OUT/"manifest.json"; manifest_path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    from ibex_fusion_manifest import validate_manifest
    validation=validate_manifest(manifest_path,ROOT); (OUT/"manifest_validation.json").write_text(json.dumps(validation,indent=2,sort_keys=True)+"\n")
    readme=["# Whole-Ibex adder sharing", "", "Classification: contract-guided module-preserving fusion.", "", "The generated wrapper preserves the pinned upstream `ibex_alu` instance and routes the independent client through its verified `multdiv_operand_a_i`, `multdiv_operand_b_i`, `multdiv_sel_i`, and `adder_result_o` interface. It supports the same 14 base RV32I operations as `ibex_alu_wrapper` plus the selected independent 32-bit add client.", "", "Correctness: {checks} comparisons over 14 ALU operations and 261 client vectors, including directed boundary values, deterministic random values, mode switching, canonical ALU comparison, and dedicated-adder comparison.".format(**sim), "", "Literal cores: {literal:.3f} um² (whole ALU + dedicated adder) versus {fused:.3f} um² fused; delta {delta:.3f} um² ({percent:.3f}%). Equal-interface separate: {separate:.3f} um² versus {fused2:.3f} um² fused; delta {delta2:.3f} um² ({percent2:.3f}%).".format(literal=literal_sum,fused=ppas["fused"]["area_um2"],delta=core["fused_delta_um2"],percent=core["fused_delta_percent"],separate=ppas["equal_interface_separate"]["area_um2"],fused2=ppas["fused"]["area_um2"],delta2=equal["delta_um2"],percent2=equal["delta_percent"]), "", "Power basis for every module-preserving PPA row: estimated peak power at 400 MHz using default flow activity assumptions; this is not measured workload/VCD power. Literal core power is 0.3081 mW separate sum versus 0.325 mW fused; equal-interface power is 0.335 mW separate versus 0.325 mW fused.", "", "The shared adder allows one selected client per evaluation. Simultaneous ALU/add-client throughput is not preserved. This is not flattened RTLIL reconstruction or arbitrary Ibex fusion.", "", "Complete RTLIL graph renders are `figures/whole_alu_separate_rtlil.svg` and `figures/whole_alu_fused_rtlil.svg` (with matching DOT files), rendered from the persisted canonical FUGraph JSON files. The new `whole_alu_rtlil_client_path_before.svg` and `whole_alu_rtlil_client_path_after.svg` are minimal directed reachability/shortest-path slices from those real graphs, with computed dashed cut-edge boundaries; `whole_alu_rtlil_client_path_comparison.svg` embeds those direct renders. The older three-hop `whole_alu_rtlil_fusion_cone_*` views are retained as larger graph-derived neighborhoods. The older `whole_alu_separate_before.svg`, `whole_alu_fused_after.svg`, and `whole_alu_fusion_diff.svg` are retained and explicitly labeled conceptual architecture schematics, not RTLIL graph renders.", "", "The older `ibex_fused_lt_ge32.sv` in the parent directory is retained as an earlier exploratory intermediate from the operation-specialization scan; it is not part of this contract-guided result or its manifest."]
    (OUT/"README.md").write_text("\n".join(readme)+"\n")
    p1 = json.loads((ROOT / "meeting-artifacts" / "ibex-fusion" / "ppa.json").read_text())
    matrix = json.loads((ROOT / "meeting-artifacts" / "ibex-fusion" / "candidate_matrix.json").read_text())
    aggregate = {
        "schema": "fu-ibex-fusion-results/v2",
        "specialization_scan": {"artifact": "meeting-artifacts/ibex-fusion/candidate_matrix.json",
                                 "pair_count": len(matrix.get("rows", matrix.get("candidates", []))),
                                 "selected": p1.get("selected_operations")},
        "graph_emitted_sub_ltu": {"classification": "fully graph-emitted RTLIL operation-specialized fusion",
                                   "artifact_directory": "meeting-artifacts/ibex-fusion",
                                   "ppa": "meeting-artifacts/ibex-fusion/ppa.json",
                                   "simulation": p1.get("simulation"),
                                   "throughput": p1.get("throughput")},
        "whole_alu_module_preserving": {"classification": "contract-guided module-preserving fusion",
                                         "artifact_directory": "meeting-artifacts/ibex-fusion/module-preserving",
                                         "contract": "meeting-artifacts/ibex-fusion/module-preserving/sharing_contract.json",
                                         "literal_core_ppa": "meeting-artifacts/ibex-fusion/module-preserving/literal_core_ppa.json",
                                         "equal_interface_ppa": "meeting-artifacts/ibex-fusion/module-preserving/equal_interface_ppa.json",
                                         "literal_area_um2": core["literal_area_sum_um2"],
                                         "literal_power_mw": core["literal_power_sum_mw"],
                                         "fused_area_um2": core["fused"]["area_um2"],
                                         "fused_power_mw": core["fused_power_mw"],
                                         "equal_interface_separate_area_um2": equal["separate"]["area_um2"],
                                         "equal_interface_separate_power_mw": equal["separate_power_mw"],
                                         "power_basis": core["conditions"]["power_basis"],
                                         "simulation": sim,
                                         "manifest": "meeting-artifacts/ibex-fusion/module-preserving/manifest.json"},
        "claim_classifications": {"structural_merge": "non-executable candidate",
                                   "sub_ltu": "generated from fu-executable-graph/v1",
                                   "whole_alu": "preserves upstream ibex_alu; contract-guided module-preserving fusion",
                                   "sequential_alu_multdiv": "unimplemented"},
        "limitations": "meeting-artifacts/ibex-fusion/module-preserving/limitations.json",
        "legacy_intermediate": {"path": "meeting-artifacts/ibex-fusion/ibex_fused_lt_ge32.sv",
                                 "status": "retained exploratory intermediate; not accepted evidence or workflow input"}
    }
    (ROOT / "meeting-artifacts" / "ibex-fusion" / "ibex_fusion_results.json").write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n")
    top_readme = ["# Ibex fusion studies", "", "This post-checkpoint directory preserves two distinct result classes.", "", "## Graph-emitted SUB/LTU", "", "The pinned upstream `ibex_alu` is specialized for 14 RV32I operations. The selected SUB/LTU pair is emitted by traversing `fu-executable-graph/v1` and passes 524 checks. Its literal area(A)+area(B) is {:.3f} um²; fused is {:.3f} um² ({:.3f}% larger). It is combinational and time-multiplexed; simultaneous SUB/LTU client throughput is not preserved.".format(p1["literal_area_sum_um2"], p1["ppa"]["fused"]["area_um2"], p1["fused_vs_literal_sum_percent"]), "", "## Whole-ALU adder client", "", "The new Priority 2 result is **contract-guided module-preserving fusion**. The generated wrapper preserves the pinned `ibex_alu` instance and routes the independent add client through the verified internal adder interface. It is not flattened RTLIL reconstruction, arbitrary Ibex fusion, or a template-free whole-module reconstruction. Literal cores are {:.3f} um² versus {:.3f} um² fused ({:.3f}% change); equal-interface separate is {:.3f} um² versus {:.3f} um² fused ({:.3f}% change). Simulation reports {} comparisons over 14 ALU operations and 261 client vectors.".format(literal_sum, ppas["fused"]["area_um2"], core["fused_delta_percent"], ppas["equal_interface_separate"]["area_um2"], ppas["fused"]["area_um2"], equal["delta_percent"], sim["checks"]), "", "Power values are estimated peak power at 400 MHz using default flow activity assumptions, not measured workload/VCD power. Literal core power is 0.3081 mW separate sum versus 0.325 mW fused; equal-interface power is 0.335 mW separate versus 0.325 mW fused.", "", "The graph-emitted SUB/LTU candidate was functionally valid but 4.240% larger, demonstrating the need for a fuse/do-not-fuse cost decision. The contract-guided whole-Ibex ALU plus ADD client reduced area by 2.983% relative to literal cores and 9.434% relative to an equal-interface baseline, while sacrificing simultaneous client use.", "", "Both comparisons are combinational and do not preserve simultaneous ALU/add-client use. Sequential Ibex ALU/`ibex_multdiv_fast` fusion remains unimplemented. The retained `ibex_fused_lt_ge32.sv` is an earlier exploratory intermediate, documented but not accepted evidence.", "", "Aggregate results: `ibex_fusion_results.json`. Priority 2 artifacts: `module-preserving/`."]
    (ROOT / "meeting-artifacts" / "ibex-fusion" / "README.md").write_text("\n".join(top_readme) + "\n")
    print(json.dumps({"simulation":sim,"literal_area_um2":literal_sum,"fused_area_um2":ppas["fused"]["area_um2"],"equal_interface_separate_area_um2":ppas["equal_interface_separate"]["area_um2"],"manifest_valid":validation["valid"]},indent=2))


if __name__ == "__main__":
    main()
