"""Export formats — neo4jcsv, graphml, dot.

Converts bona's nodes.json/edges.json into formats for graph databases
and visualization tools.

Formats:
  - neo4jcsv: CSV files for neo4j-admin import (nodes.csv + relationships.csv)
  - graphml: XML-based graph interchange format
  - dot: Graphviz DOT for visualization
"""

import csv
import io
import json
import logging
from pathlib import Path
from typing import Any

from .schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)


# ==================================================================
# Neo4j CSV
# ==================================================================


def export_neo4jcsv(nodes: list[AssetNode], edges: list[AssetEdge], output_dir: str) -> dict:
    """Export as Neo4j admin import CSV files.

    Produces:
      - nodes.csv (with :ID, :LABEL, and property columns)
      - relationships.csv (with :START_ID, :END_ID, :TYPE)
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Collect all property keys across nodes
    all_props = set()
    for n in nodes:
        all_props.update(k for k, v in n.to_dict().items()
                         if isinstance(v, (str, int, float, bool)) and k not in ("id", "node_type"))

    prop_cols = sorted(all_props)

    # Write nodes.csv
    with open(out / "nodes.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([":ID", ":LABEL"] + prop_cols)
        for n in nodes:
            d = n.to_dict()
            row = [d.get("id", ""), d.get("node_type", "")]
            for col in prop_cols:
                val = d.get(col, "")
                if isinstance(val, (list, dict)):
                    val = json.dumps(val, default=str)
                writer.writerow(row + [str(val) if val else ""])

    # Write relationships.csv
    with open(out / "relationships.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([":START_ID", ":END_ID", ":TYPE"])
        for e in edges:
            writer.writerow([e.source_id, e.target_id, e.edge_type])

    logger.info("Exported neo4jcsv: %d nodes, %d relationships → %s", len(nodes), len(edges), out)
    return {"format": "neo4jcsv", "nodes_file": str(out / "nodes.csv"), "edges_file": str(out / "relationships.csv")}


# ==================================================================
# Neptune CSV (Gremlin bulk loader format)
# ==================================================================


def export_neptunecsv(nodes: list[AssetNode], edges: list[AssetEdge], output_dir: str) -> dict:
    """Export as Amazon Neptune bulk loader CSV (Gremlin format).

    Produces:
      - vertices.csv (~id, ~label, property:type columns)
      - edges.csv (~id, ~from, ~to, ~label, property:type columns)

    Follows: https://docs.aws.amazon.com/neptune/latest/userguide/bulk-load-tutorial-format-gremlin.html
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Collect property keys and infer types
    all_props: dict[str, str] = {}  # prop_name -> Neptune type
    for n in nodes:
        d = n.to_dict()
        for k, v in d.items():
            if k in ("id", "node_type") or k.startswith("_"):
                continue
            if k not in all_props:
                all_props[k] = _neptune_type(v)

    prop_cols = sorted(all_props.keys())

    # Write vertices.csv
    header = ["~id", "~label"] + [f"{col}:{all_props[col]}" for col in prop_cols]
    with open(out / "vertices.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for n in nodes:
            d = n.to_dict()
            row = [d.get("id", ""), d.get("node_type", "").replace("::", "_")]
            for col in prop_cols:
                val = d.get(col, "")
                if isinstance(val, (list, dict)):
                    val = json.dumps(val, default=str)
                elif isinstance(val, bool):
                    val = str(val).lower()
                row.append(str(val) if val is not None and val != "" else "")
            writer.writerow(row)

    # Write edges.csv
    with open(out / "edges.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["~id", "~from", "~to", "~label"])
        for i, e in enumerate(edges):
            edge_id = f"{e.source_id}_{e.edge_type}_{e.target_id}"[:200]
            writer.writerow([edge_id, e.source_id, e.target_id, e.edge_type])

    logger.info("Exported neptunecsv: %d vertices, %d edges → %s", len(nodes), len(edges), out)
    return {
        "format": "neptunecsv",
        "vertices_file": str(out / "vertices.csv"),
        "edges_file": str(out / "edges.csv"),
    }


def _neptune_type(value: Any) -> str:
    """Infer Neptune property type from a Python value."""
    if isinstance(value, bool):
        return "Bool"
    elif isinstance(value, int):
        return "Int"
    elif isinstance(value, float):
        return "Double"
    else:
        return "String"


# ==================================================================
# GraphML
# ==================================================================


def export_graphml(nodes: list[AssetNode], edges: list[AssetEdge], output_dir: str) -> dict:
    """Export as GraphML XML format.

    Produces a single graph.graphml file loadable by yEd, Gephi, NetworkX, etc.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Collect property keys for key declarations
    node_keys = {"node_type", "name", "resource_type", "state", "region", "account_id"}
    for n in nodes:
        for k, v in n.to_dict().items():
            if isinstance(v, (str, int, float, bool)):
                node_keys.add(k)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns"',
        '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '  xsi:schemaLocation="http://graphml.graphdrawing.org/xmlns http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd">',
    ]

    # Key declarations
    for key in sorted(node_keys):
        lines.append(f'  <key id="{key}" for="node" attr.name="{key}" attr.type="string"/>')
    lines.append('  <key id="edge_type" for="edge" attr.name="edge_type" attr.type="string"/>')

    lines.append('  <graph id="G" edgedefault="directed">')

    # Nodes
    for n in nodes:
        d = n.to_dict()
        lines.append(f'    <node id="{_xml_escape(d["id"])}">')
        for key in sorted(node_keys):
            val = d.get(key, "")
            if val and isinstance(val, (str, int, float, bool)):
                lines.append(f'      <data key="{key}">{_xml_escape(str(val))}</data>')
        lines.append('    </node>')

    # Edges
    for i, e in enumerate(edges):
        lines.append(f'    <edge id="e{i}" source="{_xml_escape(e.source_id)}" target="{_xml_escape(e.target_id)}">')
        lines.append(f'      <data key="edge_type">{e.edge_type}</data>')
        lines.append('    </edge>')

    lines.append('  </graph>')
    lines.append('</graphml>')

    graphml_path = out / "graph.graphml"
    graphml_path.write_text("\n".join(lines))

    logger.info("Exported graphml: %d nodes, %d edges → %s", len(nodes), len(edges), graphml_path)
    return {"format": "graphml", "file": str(graphml_path)}


# ==================================================================
# Graphviz DOT
# ==================================================================


def export_dot(nodes: list[AssetNode], edges: list[AssetEdge], output_dir: str, max_nodes: int = 200) -> dict:
    """Export as Graphviz DOT format.

    Produces graph.dot for visualization with `dot`, `neato`, etc.
    Limited to max_nodes to keep visualizations readable.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Limit for readability
    display_nodes = nodes[:max_nodes]
    display_ids = {n.id for n in display_nodes}
    display_edges = [e for e in edges if e.source_id in display_ids and e.target_id in display_ids]

    lines = ["digraph bona {", '  rankdir=LR;', '  node [shape=box, style=filled, fillcolor=lightblue];', ""]

    # Group by node_type using subgraphs
    type_groups: dict[str, list[AssetNode]] = {}
    for n in display_nodes:
        type_groups.setdefault(n.node_type, []).append(n)

    # Color map
    colors = ["lightblue", "lightyellow", "lightgreen", "lightsalmon", "lightpink",
              "lightcyan", "lavender", "wheat", "palegreen", "mistyrose"]

    for i, (node_type, group_nodes) in enumerate(sorted(type_groups.items())):
        color = colors[i % len(colors)]
        short_type = node_type.split("::")[-1]
        lines.append(f'  subgraph cluster_{i} {{')
        lines.append(f'    label="{short_type}";')
        lines.append(f'    style=filled; fillcolor="{color}";')
        for n in group_nodes:
            label = _dot_escape(n.name or n.id.split("/")[-1])
            node_id = _dot_id(n.id)
            lines.append(f'    {node_id} [label="{label}"];')
        lines.append("  }")
        lines.append("")

    # Edges
    for e in display_edges:
        src = _dot_id(e.source_id)
        tgt = _dot_id(e.target_id)
        label = e.edge_type
        lines.append(f'  {src} -> {tgt} [label="{label}", fontsize=8];')

    lines.append("}")

    dot_path = out / "graph.dot"
    dot_path.write_text("\n".join(lines))

    if len(nodes) > max_nodes:
        logger.info("Exported dot: %d/%d nodes (truncated), %d edges → %s",
                    len(display_nodes), len(nodes), len(display_edges), dot_path)
    else:
        logger.info("Exported dot: %d nodes, %d edges → %s", len(nodes), len(display_edges), dot_path)

    return {"format": "dot", "file": str(dot_path), "truncated": len(nodes) > max_nodes}


# ==================================================================
# Helpers
# ==================================================================


def _xml_escape(s: str) -> str:
    """Escape XML special characters."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


def _dot_escape(s: str) -> str:
    """Escape DOT label characters."""
    return s.replace('"', '\\"').replace('\n', '\\n')[:50]


def _dot_id(s: str) -> str:
    """Convert an ID to a valid DOT node identifier."""
    import hashlib
    return f'n{hashlib.md5(s.encode()).hexdigest()[:12]}'
