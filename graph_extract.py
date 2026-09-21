#!/usr/bin/env python3
"""Functional-unit RTL -> graph, at four levels of abstraction.

The research question -- "given two functional units, what logic can they
share?" -- is a subgraph question, so the first thing the framework needs is a
graph. Sharing is visible at different granularities, and an analysis is only as
good as the level it runs at, so this module extracts all four and puts them in
one representation:

  ``module``  Instance hierarchy. Nodes are module definitions, edges are
              instantiations. Answers "what blocks exist", the coarsest level at
              which two units can share (share a whole submodule).

  ``rtlil``   Word-level operator graph, taken after ``proc``/``opt`` but before
              technology mapping, so cells are still ``$add`` / ``$sub`` /
              ``$mux`` / ``$lt`` on 32-bit buses. This is the *datapath* graph
              the classical merging literature operates on (Moreano/Araujo), and
              the level where "these two units both need an adder" is a
              statement you can act on.

  ``gate``    Post-synthesis bit-level netlist of generic gates. Word structure
              is gone; what remains is what the tool will actually build.

  ``aig``     And-Inverter Graph: every node a 2-input AND, every edge
              optionally inverted. The canonical form for structural comparison,
              because logically identical cones hash to identical nodes
              regardless of how they were written (Mishchenko).

  ``ast``     Also available: the pre-elaboration syntax tree from the Verilog
              frontend. Closest to source intent, furthest from what gets built.

All levels land in the same :class:`FUGraph` container so downstream comparison,
merging, and visualization code is written once and runs at any level.

Run:
    python3 graph_extract.py --design demo_alu --level rtlil
    python3 graph_extract.py --design demo_alu --level all --output build/graphs
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent
OSS_BIN = ROOT / "tools" / "oss-cad-suite" / "bin"
YOSYS = OSS_BIN / "yosys"

LEVELS = ("ast", "module", "rtlil", "gate", "aig")
GRAPH_SCHEMA = "fu-graph/v1"


class GraphExtractError(RuntimeError):
    """Raised when Yosys cannot produce a graph for the requested design."""


# --- common representation ---------------------------------------------------

@dataclass
class Node:
    """One vertex: an operator, a gate, a port, a constant, or a module."""

    id: str
    kind: str                                   # "$add", "$_AND_", "port", ...
    label: str
    width: Optional[int] = None
    attrs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Edge:
    """One directed signal connection, carrying the bits it transports."""

    src: str
    dst: str
    src_port: str = ""
    dst_port: str = ""
    width: int = 1
    inverted: bool = False
    attrs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FUGraph:
    """A functional unit at one level of abstraction."""

    design: str
    level: str
    top: str
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    attrs: Dict[str, Any] = field(default_factory=dict)

    def node_map(self) -> Dict[str, Node]:
        return {n.id: n for n in self.nodes}

    def kind_histogram(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for node in self.nodes:
            counts[node.kind] = counts.get(node.kind, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def operator_histogram(self) -> Dict[str, int]:
        """Kind counts excluding ports and constants -- the logic content."""
        return {
            kind: count
            for kind, count in self.kind_histogram().items()
            if kind not in ("port_in", "port_out", "const")
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "operators": sum(self.operator_histogram().values()),
            "kinds": self.kind_histogram(),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": GRAPH_SCHEMA,
            "design": self.design,
            "level": self.level,
            "top": self.top,
            "attrs": self.attrs,
            "stats": self.stats(),
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
        }

    def node_signature(self, node_id: str) -> Tuple[Any, ...]:
        """Return an ID-independent semantic signature for one node.

        This is deliberately a local signature: graph matching will later add
        predecessor signatures and canonicalize commutative operands. Tool-
        generated node IDs must never be used as cross-design identities.
        """
        node = self.node_map()[node_id]
        attrs = node.attrs or {}
        parameters = attrs.get("parameters", {})
        parameter_items = tuple(sorted((str(k), str(v)) for k, v in parameters.items()))
        operation = attrs.get("operation") or normalize_operation(node.kind)
        return (
            self.level,
            operation,
            node.kind,
            node.width,
            attrs.get("signed"),
            parameter_items,
        )

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))
        return path

    @classmethod
    def load(cls, path: Path) -> "FUGraph":
        data = json.loads(Path(path).read_text())
        graph = cls(
            design=data["design"],
            level=data["level"],
            top=data["top"],
            attrs=data.get("attrs", {}),
        )
        graph.nodes = [Node(**n) for n in data["nodes"]]
        graph.edges = [Edge(**e) for e in data["edges"]]
        return graph


def normalize_operation(kind: str) -> str:
    """Map common Yosys/tool spellings to a stable semantic operation name."""
    value = str(kind).lstrip("$").strip("_").lower()
    aliases = {
        "add": "add", "sub": "sub", "mul": "mul", "div": "div",
        "mod": "mod", "lt": "compare", "le": "compare", "gt": "compare",
        "ge": "compare", "eq": "compare", "ne": "compare",
        "mux": "mux", "pmux": "mux", "bmux": "mux",
        "shl": "shift", "shr": "shift", "sshl": "shift", "sshr": "shift",
        "and": "and", "or": "or", "xor": "xor", "xnor": "xnor",
        "not": "not", "buf": "buf", "port_in": "port_in",
        "port_out": "port_out", "const": "const", "module": "module",
    }
    return aliases.get(value, value)


# --- Yosys driver ------------------------------------------------------------

def _yosys_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = "{}{}{}".format(OSS_BIN, os.pathsep, env.get("PATH", ""))
    return env


def run_yosys(script: str) -> str:
    """Run a Yosys script and return stdout, raising with context on failure."""
    if not YOSYS.is_file():
        raise GraphExtractError(
            "Yosys not found at {}. The project-local OSS CAD Suite provides it; "
            "see README toolchain setup.".format(YOSYS)
        )
    proc = subprocess.run(
        [str(YOSYS), "-p", script],
        capture_output=True, text=True, env=_yosys_env(), cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise GraphExtractError(
            "yosys failed:\n--- script ---\n{}\n--- stdout ---\n{}\n--- stderr ---\n{}"
            .format(script, proc.stdout[-2000:], proc.stderr[-800:])
        )
    return proc.stdout


def _read_commands(sources: Sequence[str]) -> str:
    return "; ".join("read_verilog -sv {}".format(s) for s in sources)


# Per-level Yosys passes between reading the RTL and dumping JSON. Each stops at
# the abstraction the level names -- that choice IS the level definition.
_LEVEL_PASSES = {
    # Elaborate and keep the instance hierarchy intact. proc is required only
    # because the JSON backend cannot serialize processes; it rewrites
    # always-blocks into cells without touching module boundaries, so the
    # hierarchy this level reports is still the one the source declares.
    "module": "hierarchy -top {top}; proc; opt_clean",
    # proc turns always-blocks into $mux/$pmux; opt cleans up. No techmap, so
    # arithmetic stays as word-level $add/$sub/$lt cells. flatten is required
    # for this to be the *unit's* operator graph rather than its top wrapper's:
    # a unit like Ibex's ALU sits inside a thin wrapper, and without flattening
    # the graph is one opaque instance node. The hierarchy that flatten discards
    # is what the ``module`` level records.
    "rtlil": ("hierarchy -top {top}; proc; opt -fast; memory -nomap; "
              "flatten; opt -fast"),
    # Full synthesis to generic gates, flattened so the whole unit is one graph.
    "gate": "synth -top {top} -flatten; opt -full",
    # Same, then reduce to AND + NOT only. simplemap first guarantees the
    # primitive cell types aigmap expects.
    "aig": "synth -top {top} -flatten; simplemap; aigmap; opt_clean",
}


def _yosys_json(sources: Sequence[str], top: str, level: str, workdir: Path) -> Dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "{}_{}.yosys.json".format(top, level)
    script = "{read}; {passes}; write_json {out}".format(
        read=_read_commands(sources),
        passes=_LEVEL_PASSES[level].format(top=top),
        out=out,
    )
    run_yosys(script)
    return json.loads(out.read_text())


# --- netlist JSON -> FUGraph -------------------------------------------------

_CONST_BITS = ("0", "1", "x", "z")


def _port_width(bits: Sequence[Any]) -> int:
    return len(bits)


def _graph_from_netlist(
    netlist: Dict[str, Any], design: str, top: str, level: str
) -> FUGraph:
    """Build a graph by resolving which cell output drives each signal bit.

    Yosys JSON gives every connection as a list of bit ids, so a net is not an
    object to look up but an identity shared between a driver's output bits and
    a consumer's input bits. Walk the cells once to record who drives each bit,
    then walk them again to attach consumers to those drivers.
    """
    modules = netlist.get("modules", {})
    if top not in modules:
        raise GraphExtractError(
            "top module {!r} not in Yosys output (have: {})".format(
                top, ", ".join(sorted(modules))
            )
        )
    module = modules[top]
    graph = FUGraph(design=design, level=level, top=top)

    # Module ports become source/sink nodes so the graph has real boundaries.
    driver_of_bit: Dict[Any, Tuple[str, str, int]] = {}
    for port_name, port in module.get("ports", {}).items():
        direction = port.get("direction", "input")
        kind = "port_in" if direction == "input" else "port_out"
        node_id = "port:{}".format(port_name)
        graph.nodes.append(Node(
            id=node_id, kind=kind, label=port_name,
            width=_port_width(port.get("bits", [])),
            attrs={"direction": direction},
        ))
        if direction == "input":
            for source_position, bit in enumerate(port.get("bits", [])):
                driver_of_bit[bit] = (node_id, port_name, source_position)

    # $scopeinfo carries source-hierarchy annotations, not logic. It survives
    # flatten and would otherwise show up as a stray node in every graph.
    cells = {
        name: cell for name, cell in module.get("cells", {}).items()
        if cell.get("type") != "$scopeinfo"
    }
    for cell_name, cell in cells.items():
        directions = cell.get("port_directions", {})
        for port_name, bits in cell.get("connections", {}).items():
            if directions.get(port_name) != "output":
                continue
            for source_position, bit in enumerate(bits):
                if bit not in _CONST_BITS:
                    driver_of_bit[bit] = (cell_name, port_name, source_position)

    for cell_name, cell in cells.items():
        cell_type = cell.get("type", "?")
        params = cell.get("parameters", {})
        graph.nodes.append(Node(
            id=cell_name,
            kind=cell_type,
            label=_cell_label(cell_name, cell_type, params),
            width=_cell_width(params, cell),
            attrs={
                "src": cell.get("attributes", {}).get("src"),
                "parameters": {k: _param_int(v) for k, v in params.items()},
            },
        ))

    # Second pass: every input port of every cell becomes edges from its drivers.
    for cell_name, cell in cells.items():
        directions = cell.get("port_directions", {})
        for port_name, bits in cell.get("connections", {}).items():
            if directions.get(port_name) == "output":
                continue
            graph.edges.extend(
                _edges_for_input(graph, driver_of_bit, cell_name, port_name, bits)
            )

    # Output ports consume in exactly the same way.
    for port_name, port in module.get("ports", {}).items():
        if port.get("direction") == "input":
            continue
        graph.edges.extend(_edges_for_input(
            graph, driver_of_bit, "port:{}".format(port_name), port_name,
            port.get("bits", []),
        ))

    graph.attrs["cell_count"] = len(cells)
    return graph


def _edges_for_input(
    graph: FUGraph,
    driver_of_bit: Dict[Any, Tuple[str, str, int]],
    consumer: str,
    port_name: str,
    bits: Sequence[Any],
) -> List[Edge]:
    """Turn one consumed bit vector into edges, one per distinct driver.

    A 32-bit operand driven by one adder is a single edge of width 32, not 32
    edges: the graph stays readable and the width carries the same information.
    Bits with no driver are literals, which get their own small constant node so
    the visualization shows where a design pins a value.
    """
    grouped: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
    constant_bits: List[Tuple[int, str]] = []
    for position, bit in enumerate(bits):
        if bit in _CONST_BITS:
            constant_bits.append((position, str(bit)))
            continue
        driver = driver_of_bit.get(bit)
        if driver is None:
            continue                      # undriven (dangling) bit
        grouped.setdefault(driver[:2], []).append((position, driver[2]))

    edges = [
        Edge(src=src, dst=consumer, src_port=src_port,
             dst_port=port_name, width=len(positions),
             attrs={"bit_lsb": min(dst_positions), "bit_positions": dst_positions,
                    "source_bit_positions": source_positions})
        for (src, src_port), pairs in grouped.items()
        for positions, dst_positions, source_positions in [(pairs, [item[0] for item in pairs], [item[1] for item in pairs])]
    ]

    if constant_bits:
        const_id = "const:{}:{}".format(consumer, port_name)
        value = "".join(bit for _, bit in reversed(constant_bits))
        graph.nodes.append(Node(
            id=const_id, kind="const", label="{}'b{}".format(len(constant_bits), value),
            width=len(constant_bits), attrs={"value": value},
        ))
        edges.append(Edge(src=const_id, dst=consumer, src_port="Y",
                          dst_port=port_name, width=len(constant_bits),
                          attrs={"bit_lsb": min(position for position, _ in constant_bits),
                                 "bit_positions": [position for position, _ in constant_bits],
                                 "source_bit_positions": list(range(len(constant_bits)))}))
    return edges


def _param_int(value: Any) -> Any:
    """Yosys writes parameters as bit strings; show the number when it is one."""
    if isinstance(value, str) and value and set(value) <= {"0", "1"}:
        return int(value, 2)
    return value


def _cell_width(params: Dict[str, Any], cell: Dict[str, Any]) -> Optional[int]:
    for key in ("Y_WIDTH", "WIDTH", "A_WIDTH"):
        if key in params:
            width = _param_int(params[key])
            if isinstance(width, int):
                return width
    outputs = [
        bits for port, bits in cell.get("connections", {}).items()
        if cell.get("port_directions", {}).get(port) == "output"
    ]
    return len(outputs[0]) if outputs else None


def _cell_label(name: str, cell_type: str, params: Dict[str, Any]) -> str:
    """Short human label: the operator and its width, not the mangled net name."""
    operator = cell_type.lstrip("$").rstrip("_").replace("_", "")
    width = params.get("Y_WIDTH") or params.get("WIDTH")
    width_int = _param_int(width) if width is not None else None
    if isinstance(width_int, int) and width_int > 1:
        return "{}[{}]".format(operator, width_int)
    return operator


def _parameterized_type_signature(cell_type: str) -> Optional[str]:
    """Keep parameter specialization while ignoring the child module name."""
    if not cell_type.startswith("$paramod"):
        return None
    parts = cell_type.split("\\", 2)
    if len(parts) < 3:
        return "$paramod"
    return "$paramod\\" + parts[2]


# --- module / hierarchy level ------------------------------------------------

def _graph_from_hierarchy(
    netlist: Dict[str, Any], design: str, top: str
) -> FUGraph:
    """Instance hierarchy: nodes are modules, edges are instantiations."""
    graph = FUGraph(design=design, level="module", top=top)
    modules = netlist.get("modules", {})
    defined = set(modules)

    for name, module in modules.items():
        ports = module.get("ports", {})
        port_signature = [
            {
                "direction": port.get("direction", ""),
                "width": len(port.get("bits", [])),
            }
            for _, port in sorted(ports.items())
        ]
        child_signature = []
        for cell in module.get("cells", {}).values():
            cell_type = str(cell.get("type", ""))
            # Yosys parameterized module specializations are named
            # ``$paramod...`` but are real hierarchy children. Keep their
            # specialization identity while still ignoring generic RTLIL
            # primitives such as ``$add`` at the module level.
            if cell_type.startswith("$") and not cell_type.startswith("$paramod"):
                continue
            parameters = cell.get("parameters", {}) or {}
            # The child module name is intentionally omitted: module matching
            # must survive harmless renaming. Parameter values are retained,
            # because two parameterized instances can have different behavior
            # even when their hierarchy skeleton is identical (e.g. SUB/LTU).
            child_signature.append({
                "parameterized_type": _parameterized_type_signature(cell_type),
                "parameters": sorted((str(key), str(value))
                                     for key, value in parameters.items()),
            })
        child_signature.sort(key=lambda item: repr(item))
        graph.nodes.append(Node(
            id=name, kind="module", label=name,
            width=None,
            attrs={
                "is_top": name == top,
                "cells": len(module.get("cells", {})),
                "ports": len(ports),
                "port_bits": sum(len(p.get("bits", [])) for p in ports.values()),
                "module_signature": json.dumps({
                    "ports": port_signature,
                    "children": child_signature,
                }, sort_keys=True, separators=(",", ":")),
            },
        ))

    for name, module in modules.items():
        instantiated: Dict[str, int] = {}
        for cell in module.get("cells", {}).values():
            cell_type = cell.get("type", "")
            if cell_type in defined:
                instantiated[cell_type] = instantiated.get(cell_type, 0) + 1
        for child, count in instantiated.items():
            graph.edges.append(Edge(src=name, dst=child, src_port="",
                                    dst_port="instantiates", width=count))
    return graph


# --- AST level ---------------------------------------------------------------

_AST_LINE = re.compile(
    r"^(?P<indent>\s*)AST_(?P<kind>[A-Z_0-9]+)"
    r"(?:\s+<(?P<src>[^>]*)>)?"
    r"(?:\s+\[(?P<ptr>0x[0-9a-f]+)\])?"
    r"(?P<rest>.*)$"
)
_AST_STR = re.compile(r"str='([^']*)'")
_AST_INT = re.compile(r"\bint=(-?\d+)")


def _graph_from_ast(sources: Sequence[str], design: str, top: str) -> FUGraph:
    """Parse Yosys's pre-simplification AST dump into a tree graph.

    This is the syntax level: it still contains the parameters, localparams and
    the ``case`` structure as written, before any of it is elaborated away. Good
    for seeing author intent; bad for comparing two units, because two designs
    that build the same hardware from different source constructs look nothing
    alike here. That contrast is exactly why the other levels exist.
    """
    # -dump_ast1 is a read_verilog flag, so it goes before each filename.
    output = run_yosys("; ".join(
        "read_verilog -sv -dump_ast1 {}".format(s) for s in sources
    ))

    graph = FUGraph(design=design, level="ast", top=top)
    # The dump repeats per module; keep the block for the requested top.
    marker = "Dumping AST before simplification:"
    if marker not in output:
        raise GraphExtractError("Yosys produced no AST dump for {}".format(top))

    counter = 0
    stack: List[Tuple[int, str]] = []          # (indent, node id)
    in_dump = False
    for line in output.splitlines():
        if marker in line:
            in_dump = True
            stack = []
            continue
        if not in_dump:
            continue
        match = _AST_LINE.match(line)
        if not match:
            if line.strip() and not line.startswith(" "):
                in_dump = False               # left the dump block
            continue

        indent = len(match.group("indent"))
        kind = "AST_" + match.group("kind")
        rest = match.group("rest") or ""
        name = _AST_STR.search(rest)
        value = _AST_INT.search(rest)

        counter += 1
        node_id = "ast{}".format(counter)
        label = kind[4:].lower()
        if name:
            label = "{} {}".format(label, name.group(1).lstrip("\\"))
        elif value:
            label = "{} = {}".format(label, value.group(1))

        graph.nodes.append(Node(
            id=node_id, kind=kind, label=label,
            attrs={
                "src": match.group("src"),
                "name": name.group(1).lstrip("\\") if name else None,
                "value": int(value.group(1)) if value else None,
            },
        ))

        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            graph.edges.append(Edge(src=stack[-1][1], dst=node_id,
                                    dst_port="child", width=1))
        stack.append((indent, node_id))

    if not graph.nodes:
        raise GraphExtractError("AST dump for {} parsed to zero nodes".format(top))
    graph.attrs["note"] = "pre-elaboration syntax tree (read_verilog -dump_ast1)"
    return graph


# --- public API --------------------------------------------------------------

def extract(
    design: str,
    sources: Sequence[str],
    top: str,
    level: str,
    workdir: Optional[Path] = None,
) -> FUGraph:
    """Extract one graph level for a design.

    Args:
        design: label used in outputs (usually the top module name).
        sources: RTL files, in dependency order.
        top: top module name.
        level: one of :data:`LEVELS`.
        workdir: scratch directory for Yosys intermediates.
    """
    if level not in LEVELS:
        raise GraphExtractError(
            "unknown level {!r}; expected one of {}".format(level, ", ".join(LEVELS))
        )
    workdir = Path(workdir) if workdir else ROOT / "build" / "graphs" / design
    sources = [str(Path(s)) for s in sources]
    missing = [s for s in sources if not Path(s).is_file()]
    if missing:
        raise GraphExtractError("RTL source(s) not found: {}".format(", ".join(missing)))

    if level == "ast":
        graph = _graph_from_ast(sources, design, top)
    elif level == "module":
        graph = _graph_from_hierarchy(
            _yosys_json(sources, top, "module", workdir), design, top
        )
    else:
        graph = _graph_from_netlist(
            _yosys_json(sources, top, level, workdir), design, top, level
        )
    graph.attrs["sources"] = sources
    graph.attrs["yosys_passes"] = _LEVEL_PASSES.get(level, "read_verilog -dump_ast1")
    return graph


def extract_all(
    design: str,
    sources: Sequence[str],
    top: str,
    workdir: Optional[Path] = None,
    levels: Sequence[str] = LEVELS,
) -> Dict[str, FUGraph]:
    return {level: extract(design, sources, top, level, workdir) for level in levels}


# --- design registry ---------------------------------------------------------

def _ibex_sources() -> List[str]:
    converted = ROOT / "build" / "sc_ibex" / "ibex_alu_wrapper.v"
    if converted.is_file():
        return [str(converted)]
    raise GraphExtractError(
        "Ibex needs its sv2v-converted Verilog. Run `make ppa-ibex` once "
        "(expected at {}).".format(converted)
    )


def _ibex_fused_ex_sources() -> List[str]:
    rtl = ROOT / "third_party" / "ibex" / "rtl"
    wrapper = ROOT / "rtl" / "ibex_fused_ex_block_wrapper.sv"
    prim = ROOT / "third_party" / "ibex" / "vendor" / "lowrisc_ip" / "ip" / "prim" / "rtl" / "prim_assert.sv"
    converted = ROOT / "build" / "ibex_fused_ex_block" / "ibex_fused_ex_block_wrapper.v"
    if not converted.is_file():
        converted.parent.mkdir(parents=True, exist_ok=True)
        sv2v = OSS_BIN / "sv2v"
        inputs = [prim, rtl / "ibex_pkg.sv", rtl / "ibex_alu.sv", rtl / "ibex_multdiv_fast.sv", rtl / "ibex_ex_block.sv", wrapper]
        command = [str(sv2v), "-I", str(prim.parent), "--write", str(converted)] + [str(path) for path in inputs]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0 or not converted.is_file():
            raise GraphExtractError("sv2v conversion failed: {}".format(result.stderr.strip()))
    return [str(converted)]


def _tachyum_fma_sources() -> List[str]:
    reference = ROOT / "third_party" / "tachyum-fma-rtl"
    wrapper = ROOT / "rtl" / "fma_experiment" / "tachyum_fma_single_wrapper.sv"
    sources = sorted(reference.glob("*.v"))
    sources.append(reference / "tb" / "scells.v")
    sources.insert(0, wrapper)
    return [str(path) for path in sources]


DESIGNS: Dict[str, Dict[str, Any]] = {
    "demo_alu": {
        "top": "demo_alu",
        "sources": lambda: [str(ROOT / "rtl" / "demo_alu.sv")],
    },
    "demo_alu_manual_fused": {
        "top": "demo_alu_manual_fused",
        "sources": lambda: [str(ROOT / "rtl" / "demo_alu_manual_fused.sv")],
    },
    "ibex_alu_wrapper": {
        "top": "ibex_alu_wrapper",
        "sources": _ibex_sources,
    },
    "ibex_fused_ex_block_wrapper": {
        "top": "ibex_fused_ex_block_wrapper",
        "sources": _ibex_fused_ex_sources,
    },
    "tachyum_fma_single_wrapper": {
        "top": "tachyum_fma_single_wrapper",
        "sources": _tachyum_fma_sources,
    },
    "separate_mul_add": {
        "top": "separate_mul_add",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "separate_mul_add.sv")],
    },
    "scheduled_mul_add": {
        "top": "scheduled_mul_add",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "scheduled_mul_add.sv")],
    },
    "fused_mul_add": {
        "top": "fused_mul_add",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "fused_mul_add.sv")],
    },
    "graph_candidate_add_mul": {
        "top": "graph_candidate_add_mul",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "graph_candidate_add_mul.sv")],
    },
    "shared_iterative_mul_add": {
        "top": "shared_iterative_mul_add",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "shared_iterative_mul_add.sv")],
    },
    "generated_fused_add_mul_mac": {
        "top": "generated_fused_add_mul_mac",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "generated_fused_add_mul_mac.sv")],
    },
    "parallel_add_mul_mac_baseline": {
        "top": "parallel_add_mul_mac_baseline",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "parallel_add_mul_mac_baseline.sv")],
    },
    "capability_add32": {
        "top": "capability_add32",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "capability_add32.sv")],
    },
    "capability_add64": {
        "top": "capability_add64",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "capability_add64.sv")],
    },
    "graph_unit_a_mul": {
        "top": "graph_unit_a_mul",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "graph_unit_a_mul.sv")],
    },
    "graph_unit_b_add_mul_mac": {
        "top": "graph_unit_b_add_mul_mac",
        "sources": lambda: [str(ROOT / "rtl" / "fma_experiment" / "graph_unit_b_add_mul_mac.sv")],
    },
}


def resolve_design(name: str) -> Tuple[str, List[str]]:
    """Return (top, sources) for a registered design name."""
    if name not in DESIGNS:
        raise GraphExtractError(
            "unknown design {!r}; registered: {}".format(name, ", ".join(sorted(DESIGNS)))
        )
    entry = DESIGNS[name]
    return entry["top"], list(entry["sources"]())


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--design", default="demo_alu", choices=sorted(DESIGNS))
    parser.add_argument("--level", default="rtlil", choices=list(LEVELS) + ["all"])
    parser.add_argument("--output", default="build/graphs")
    args = parser.parse_args(argv)

    top, sources = resolve_design(args.design)
    outdir = Path(args.output).resolve() / args.design
    levels = LEVELS if args.level == "all" else (args.level,)

    for level in levels:
        graph = extract(args.design, sources, top, level, workdir=outdir)
        path = graph.save(outdir / "{}.{}.graph.json".format(args.design, level))
        stats = graph.stats()
        print("{:<8} {:>6} nodes {:>6} edges   {}".format(
            level, stats["nodes"], stats["edges"],
            ", ".join("{}={}".format(k, v)
                      for k, v in list(graph.operator_histogram().items())[:6]),
        ))
        print("         -> {}".format(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
