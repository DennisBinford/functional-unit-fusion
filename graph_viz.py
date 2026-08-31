#!/usr/bin/env python3
"""Render extracted functional-unit graphs to DOT / SVG / PNG.

Two renderers, because they answer different questions:

  * ``render`` draws :class:`graph_extract.FUGraph` objects. Nodes are coloured
    by operator class (arithmetic / compare / logic / mux / shift), so the shape
    of a datapath is readable at a glance and two units can be compared by eye.
    Because the same container holds every abstraction level, one renderer
    covers the word-level datapath, the gate netlist, the AIG, the hierarchy and
    the AST.

  * ``yosys_show`` calls Yosys's own ``show`` command, which is the tool-native
    view and a useful cross-check that the extracted graph matches what Yosys
    thinks the design is.

Bit-level graphs run to thousands of nodes, which no layout engine renders
legibly. Rather than emit an unreadable hairball, large graphs are reduced to a
logic cone -- a backward traversal from chosen outputs -- and the caption says so
explicitly, so a figure is never mistaken for the whole design.

Run:
    python3 graph_viz.py --design demo_alu --level rtlil
    python3 graph_viz.py --design demo_alu --level all --format svg
    python3 graph_viz.py --design demo_alu --level aig --max-nodes 120
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

import graph_extract
from graph_extract import Edge, FUGraph, Node, GraphExtractError

ROOT = Path(__file__).resolve().parent

# Operator classes share a colour so a datapath reads as coloured regions rather
# than as several hundred individually-labelled boxes. Hues are kept far apart
# in lightness as well as hue so the figures survive greyscale printing.
_CLASS_STYLE = {
    "arith":   {"fill": "#dbe9fb", "line": "#2f6fb5"},   # blue    - add/sub
    "compare": {"fill": "#fde5d4", "line": "#c1651a"},   # orange  - lt/eq/ge
    "logic":   {"fill": "#e2f0dc", "line": "#3f7d35"},   # green   - and/or/xor
    "mux":     {"fill": "#efe0f4", "line": "#7b4397"},   # purple  - selection
    "shift":   {"fill": "#fdf3cf", "line": "#a8880e"},   # yellow  - shl/shr
    "reduce":  {"fill": "#d9f2f2", "line": "#1f7a7a"},   # teal    - reductions
    "port_in": {"fill": "#ffffff", "line": "#333333"},
    "port_out": {"fill": "#333333", "line": "#333333"},
    "const":   {"fill": "#f2f2f2", "line": "#999999"},
    "module":  {"fill": "#e8eaf6", "line": "#3949ab"},
    "ast":     {"fill": "#f7f7f7", "line": "#666666"},
    "other":   {"fill": "#eeeeee", "line": "#555555"},
}

# Highlight colours used when a caller passes a node->group mapping (the
# shared-subgraph analysis uses this to paint what two designs have in common).
_HIGHLIGHT_STYLE = {
    "shared":   {"fill": "#ffe08a", "line": "#b8860b", "penwidth": "2.5"},
    "unique_a": {"fill": "#cfe3f7", "line": "#2f6fb5", "penwidth": "1.4"},
    "unique_b": {"fill": "#f7d6d6", "line": "#b03030", "penwidth": "1.4"},
}

_ARITH = {"$add", "$sub", "$neg", "$mul", "$div", "$mod", "$alu", "$fa", "$lcu"}
_COMPARE = {"$lt", "$le", "$gt", "$ge", "$eq", "$ne", "$eqx", "$nex"}
_SHIFT = {"$shl", "$shr", "$sshl", "$sshr", "$shift", "$shiftx"}
_MUX = {"$mux", "$pmux", "$bmux", "$demux", "$_MUX_", "$_NMUX_", "$_MUX4_"}


def node_class(node: Node) -> str:
    """Map a node's cell type onto a colour class."""
    kind = node.kind
    if kind in ("port_in", "port_out", "const", "module"):
        return kind
    if kind.startswith("AST_"):
        return "ast"
    if kind in _ARITH:
        return "arith"
    if kind in _COMPARE:
        return "compare"
    if kind in _SHIFT:
        return "shift"
    if kind in _MUX:
        return "mux"
    if kind.startswith("$reduce_"):
        return "reduce"
    base = kind.lstrip("$").rstrip("_").upper()
    if base in ("AND", "OR", "XOR", "XNOR", "NOT", "NAND", "NOR",
                "ANDNOT", "ORNOT", "AOI3", "OAI3", "AOI4", "OAI4",
                "LOGIC_NOT", "LOGIC_AND", "LOGIC_OR", "BUF"):
        return "logic"
    return "other"


def _shape(node: Node) -> str:
    cls = node_class(node)
    if cls in ("port_in", "port_out"):
        return "invhouse" if cls == "port_out" else "house"
    if cls == "const":
        return "plaintext"
    if cls == "module":
        return "box3d"
    if cls == "ast":
        return "ellipse"
    return "box"


def _escape(text: str) -> str:
    # Order matters: double the backslashes first, then turn real newlines into
    # DOT's ``\n`` line break. Building the label with a literal ``\\n`` instead
    # would get caught by the first replace and print as the characters "\n".
    return (str(text).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n"))


# --- cone extraction for graphs too large to draw ----------------------------

def select_cone(
    graph: FUGraph,
    max_nodes: int,
    roots: Optional[Sequence[str]] = None,
) -> FUGraph:
    """Reduce a graph to a readable logic cone feeding ``roots``.

    Walks backwards from the outputs breadth-first and stops once ``max_nodes``
    vertices have been collected, so what survives is the logic nearest the
    outputs rather than an arbitrary slice. The returned graph records the
    truncation in ``attrs`` and callers are expected to surface it -- a cropped
    figure presented as a whole design is a lie the reader cannot detect.
    """
    if len(graph.nodes) <= max_nodes:
        return graph

    incoming: Dict[str, List[Edge]] = {}
    for edge in graph.edges:
        incoming.setdefault(edge.dst, []).append(edge)

    if roots is None:
        roots = [n.id for n in graph.nodes if n.kind == "port_out"]
        if not roots:
            sinks = {e.src for e in graph.edges}
            roots = [n.id for n in graph.nodes if n.id not in sinks][:8]

    keep: Set[str] = set()
    frontier = list(roots)
    while frontier and len(keep) < max_nodes:
        node_id = frontier.pop(0)
        if node_id in keep:
            continue
        keep.add(node_id)
        for edge in incoming.get(node_id, []):
            if edge.src not in keep:
                frontier.append(edge.src)

    cone = FUGraph(
        design=graph.design, level=graph.level, top=graph.top,
        attrs=dict(graph.attrs),
    )
    cone.nodes = [n for n in graph.nodes if n.id in keep]
    cone.edges = [e for e in graph.edges if e.src in keep and e.dst in keep]
    cone.attrs["truncated"] = {
        "shown_nodes": len(cone.nodes),
        "total_nodes": len(graph.nodes),
        "shown_edges": len(cone.edges),
        "total_edges": len(graph.edges),
        "method": "backward logic cone from outputs, breadth-first",
    }
    return cone


# --- DOT emission ------------------------------------------------------------

def to_dot(
    graph: FUGraph,
    title: Optional[str] = None,
    highlight: Optional[Dict[str, str]] = None,
    rankdir: str = "LR",
) -> str:
    """Render a graph to Graphviz DOT source.

    Args:
        graph: the graph to draw.
        title: caption; defaults to a generated one naming the level and size.
        highlight: node id -> highlight group ("shared" / "unique_a" /
            "unique_b"), used to paint a cross-design comparison onto one graph.
        rankdir: "LR" reads like a datapath (inputs left, outputs right); "TB"
            suits trees such as the AST and the hierarchy.
    """
    highlight = highlight or {}
    truncation = graph.attrs.get("truncated")
    if title is None:
        title = "{} - {} level ({} nodes, {} edges)".format(
            graph.design, graph.level, len(graph.nodes), len(graph.edges)
        )
        if truncation:
            title += "\nLOGIC CONE ONLY: {shown_nodes} of {total_nodes} nodes shown".format(
                **truncation
            )

    lines = [
        "digraph {} {{".format(_dot_id(graph.design + "_" + graph.level)),
        '  graph [rankdir={}, fontname="Helvetica", labelloc="t", '
        'label="{}", fontsize=16, nodesep=0.25, ranksep=0.6, bgcolor="white"];'
        .format(rankdir, _escape(title)),
        '  node  [fontname="Helvetica", fontsize=10, style="filled", '
        'penwidth=1.2, margin="0.08,0.04"];',
        '  edge  [fontname="Helvetica", fontsize=8, color="#666666", '
        'arrowsize=0.6];',
    ]

    for node in graph.nodes:
        cls = node_class(node)
        style = dict(_CLASS_STYLE.get(cls, _CLASS_STYLE["other"]))
        group = highlight.get(node.id)
        if group and group in _HIGHLIGHT_STYLE:
            style.update(_HIGHLIGHT_STYLE[group])
        label = node.label
        if node.width and node.width > 1 and "[" not in label:
            label = "{}\n[{}]".format(label, node.width)
        lines.append(
            '  {} [label="{}", shape={}, fillcolor="{}", color="{}"{}{}];'.format(
                _dot_id(node.id), _escape(label), _shape(node),
                style["fill"], style["line"],
                ', penwidth={}'.format(style["penwidth"]) if "penwidth" in style else "",
                ', fontcolor="white"' if cls == "port_out" and not group else "",
            )
        )

    for edge in graph.edges:
        attrs = []
        if edge.width > 1:
            attrs.append('label="{}"'.format(edge.width))
            attrs.append('penwidth={:.1f}'.format(min(1.0 + edge.width / 16.0, 3.0)))
        if edge.dst_port and graph.level in ("rtlil", "gate", "aig"):
            attrs.append('headlabel="{}"'.format(_escape(edge.dst_port)))
            attrs.append('labelfontsize=7')
            attrs.append('labeldistance=1.4')
        if edge.inverted:
            attrs.append('style=dashed')
        lines.append("  {} -> {}{};".format(
            _dot_id(edge.src), _dot_id(edge.dst),
            " [{}]".format(", ".join(attrs)) if attrs else "",
        ))

    lines.append(_legend_block(graph, bool(highlight)))
    lines.append("}")
    return "\n".join(lines)


def _legend_block(graph: FUGraph, with_highlight: bool) -> str:
    """A small key so a figure stands on its own in a slide or a paper."""
    present = sorted({node_class(n) for n in graph.nodes})
    rows = []
    for cls in present:
        style = _CLASS_STYLE.get(cls, _CLASS_STYLE["other"])
        rows.append(
            '<TR><TD BGCOLOR="{}" WIDTH="16"></TD><TD ALIGN="LEFT">{}</TD></TR>'
            .format(style["fill"], cls)
        )
    if with_highlight:
        for name, style in _HIGHLIGHT_STYLE.items():
            rows.append(
                '<TR><TD BGCOLOR="{}" WIDTH="16"></TD><TD ALIGN="LEFT">{}</TD></TR>'
                .format(style["fill"], name)
            )
    return (
        '  subgraph cluster_legend {\n'
        '    label="key"; fontname="Helvetica"; fontsize=10; color="#cccccc";\n'
        '    legend [shape=plaintext, fillcolor="white", label=<\n'
        '      <TABLE BORDER="0" CELLBORDER="0" CELLSPACING="2">\n'
        '        ' + "\n        ".join(rows) + '\n'
        '      </TABLE>>];\n'
        '  }'
    )


def _dot_id(name: str) -> str:
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in str(name))
    return "n_" + safe if not safe or safe[0].isdigit() else safe


# --- rendering ---------------------------------------------------------------

def _graphviz_binary() -> str:
    binary = shutil.which("dot") or shutil.which(str(graph_extract.OSS_BIN / "dot"))
    if not binary:
        raise GraphExtractError(
            "Graphviz 'dot' not found on PATH; install graphviz to render images "
            "(DOT source is still written)."
        )
    return binary


def render(
    graph: FUGraph,
    output: Path,
    image_format: str = "svg",
    max_nodes: int = 400,
    highlight: Optional[Dict[str, str]] = None,
    title: Optional[str] = None,
) -> Dict[str, Path]:
    """Write DOT for a graph and compile it to an image.

    ``output`` is a path *stem*: suffixes are appended, not replaced, because
    stems here carry dotted level names ("demo_alu.rtlil") that ``with_suffix``
    would silently eat.

    Returns the paths written. The DOT file is always produced, even if
    Graphviz is missing, so the artifact survives a partial toolchain.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    drawn = select_cone(graph, max_nodes)
    rankdir = "TB" if drawn.level in ("ast", "module") else "LR"
    dot_path = Path(str(output) + ".dot")
    dot_path.write_text(to_dot(drawn, title=title, highlight=highlight, rankdir=rankdir))
    written = {"dot": dot_path}

    image_path = Path("{}.{}".format(output, image_format))
    proc = subprocess.run(
        [_graphviz_binary(), "-T" + image_format, str(dot_path), "-o", str(image_path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise GraphExtractError(
            "graphviz failed on {}:\n{}".format(dot_path, proc.stderr[-800:])
        )
    written[image_format] = image_path
    return written


def yosys_show(
    design: str,
    sources: Sequence[str],
    top: str,
    level: str,
    output: Path,
    image_format: str = "svg",
) -> Path:
    """Render the same design with Yosys's own ``show`` command.

    Kept alongside the custom renderer as a cross-check: if the two disagree
    about what the design contains, the extraction is wrong.
    """
    prefix = Path(output)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    passes = graph_extract._LEVEL_PASSES.get(level, "").format(top=top)
    script = "{read}; {passes}; show -format {fmt} -prefix {prefix} -viewer none".format(
        read="; ".join("read_verilog -sv {}".format(s) for s in sources),
        passes=passes, fmt=image_format, prefix=prefix,
    )
    graph_extract.run_yosys(script)
    produced = Path("{}.{}".format(prefix, image_format))
    if not produced.is_file():
        raise GraphExtractError("yosys show produced no {} at {}".format(image_format, produced))
    return produced


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--design", default="demo_alu", choices=sorted(graph_extract.DESIGNS))
    parser.add_argument("--level", default="rtlil",
                        choices=list(graph_extract.LEVELS) + ["all"])
    parser.add_argument("--format", default="svg", choices=("svg", "png", "pdf"))
    parser.add_argument("--max-nodes", type=int, default=400,
                        help="Reduce to a logic cone above this size (default: 400)")
    parser.add_argument("--yosys-show", action="store_true",
                        help="Also render with Yosys's native show command")
    parser.add_argument("--output", default="build/graphs")
    args = parser.parse_args(argv)

    top, sources = graph_extract.resolve_design(args.design)
    outdir = Path(args.output).resolve() / args.design
    levels = graph_extract.LEVELS if args.level == "all" else (args.level,)

    for level in levels:
        graph = graph_extract.extract(args.design, sources, top, level, workdir=outdir)
        graph.save(outdir / "{}.{}.graph.json".format(args.design, level))
        written = render(
            graph, outdir / "{}.{}".format(args.design, level),
            image_format=args.format, max_nodes=args.max_nodes,
        )
        note = ""
        drawn = select_cone(graph, args.max_nodes)
        if drawn.attrs.get("truncated"):
            note = "  (cone: {shown_nodes}/{total_nodes} nodes)".format(
                **drawn.attrs["truncated"])
        print("{:<8} -> {}{}".format(level, written[args.format], note))

        if args.yosys_show and level not in ("ast",):
            try:
                path = yosys_show(args.design, sources, top, level,
                                  outdir / "{}.{}.yosys".format(args.design, level),
                                  args.format)
                print("{:<8} -> {} (yosys show)".format("", path))
            except GraphExtractError as exc:
                print("{:<8}    yosys show skipped: {}".format("", exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
