"""Bona Diff — drift detection between two exports.

Compares two export snapshots (nodes.json + edges.json) using stable_id and
semantic_hash to detect:
  - Added resources (new stable_id)
  - Removed resources (missing stable_id)
  - Modified resources (same stable_id, different semantic_hash)
  - Unchanged resources

Usage:
    from bona.diff import diff_exports, DiffResult
    result = diff_exports("./export-baseline/", "./export-current/")
    print(result.summary())

CLI:
    bona diff ./export-baseline ./export-current
    bona diff ./export-baseline ./export-current --format json
    bona diff ./export-baseline ./export-current --only modified
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class NodeDiff:
    """A single node difference."""
    stable_id: str
    node_id: str
    node_type: str
    name: str
    change: str  # "added", "removed", "modified"
    old_hash: str = ""
    new_hash: str = ""
    changed_properties: dict = field(default_factory=dict)  # key -> {old, new}


@dataclass
class EdgeDiff:
    """A single edge difference."""
    stable_id: str
    source_id: str
    target_id: str
    edge_type: str
    change: str  # "added", "removed"


@dataclass
class DiffResult:
    """Result of comparing two exports."""
    added_nodes: list[NodeDiff] = field(default_factory=list)
    removed_nodes: list[NodeDiff] = field(default_factory=list)
    modified_nodes: list[NodeDiff] = field(default_factory=list)
    unchanged_count: int = 0

    added_edges: list[EdgeDiff] = field(default_factory=list)
    removed_edges: list[EdgeDiff] = field(default_factory=list)
    unchanged_edges_count: int = 0

    old_path: str = ""
    new_path: str = ""

    @property
    def has_changes(self) -> bool:
        return bool(self.added_nodes or self.removed_nodes or self.modified_nodes
                    or self.added_edges or self.removed_edges)

    @property
    def total_changes(self) -> int:
        return (len(self.added_nodes) + len(self.removed_nodes) + len(self.modified_nodes)
                + len(self.added_edges) + len(self.removed_edges))

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Drift Detection: {self.old_path} → {self.new_path}",
            f"",
            f"Nodes:",
            f"  + {len(self.added_nodes)} added",
            f"  - {len(self.removed_nodes)} removed",
            f"  ~ {len(self.modified_nodes)} modified",
            f"  = {self.unchanged_count} unchanged",
            f"",
            f"Edges:",
            f"  + {len(self.added_edges)} added",
            f"  - {len(self.removed_edges)} removed",
            f"  = {self.unchanged_edges_count} unchanged",
        ]
        if not self.has_changes:
            lines.append("")
            lines.append("No drift detected.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize to dict for JSON output."""
        return {
            "old_path": self.old_path,
            "new_path": self.new_path,
            "has_changes": self.has_changes,
            "total_changes": self.total_changes,
            "nodes": {
                "added": [_node_diff_dict(n) for n in self.added_nodes],
                "removed": [_node_diff_dict(n) for n in self.removed_nodes],
                "modified": [_node_diff_dict(n) for n in self.modified_nodes],
                "unchanged_count": self.unchanged_count,
            },
            "edges": {
                "added": [_edge_diff_dict(e) for e in self.added_edges],
                "removed": [_edge_diff_dict(e) for e in self.removed_edges],
                "unchanged_count": self.unchanged_edges_count,
            },
        }


def _node_diff_dict(n: NodeDiff) -> dict:
    d = {"stable_id": n.stable_id, "id": n.node_id, "node_type": n.node_type,
         "name": n.name, "change": n.change}
    if n.change == "modified":
        d["old_hash"] = n.old_hash
        d["new_hash"] = n.new_hash
        if n.changed_properties:
            d["changed_properties"] = n.changed_properties
    return d


def _edge_diff_dict(e: EdgeDiff) -> dict:
    return {"stable_id": e.stable_id, "source_id": e.source_id,
            "target_id": e.target_id, "edge_type": e.edge_type, "change": e.change}


def diff_exports(old_path: str, new_path: str, include_properties: bool = True) -> DiffResult:
    """Compare two export directories and return drift results.

    Args:
        old_path: path to baseline export (directory with nodes.json + edges.json)
        new_path: path to current export
        include_properties: if True, compute per-property changes for modified nodes

    Returns:
        DiffResult with added/removed/modified nodes and edges
    """
    old_dir = Path(old_path)
    new_dir = Path(new_path)

    # Load nodes
    old_nodes = _load_json(old_dir / "nodes.json")
    new_nodes = _load_json(new_dir / "nodes.json")

    # Load edges
    old_edges = _load_json(old_dir / "edges.json")
    new_edges = _load_json(new_dir / "edges.json")

    # Diff nodes
    result = DiffResult(old_path=str(old_dir), new_path=str(new_dir))
    _diff_nodes(old_nodes, new_nodes, result, include_properties)
    _diff_edges(old_edges, new_edges, result)

    logger.info("Diff complete: %d changes (%d added, %d removed, %d modified nodes; %d added, %d removed edges)",
                result.total_changes, len(result.added_nodes), len(result.removed_nodes),
                len(result.modified_nodes), len(result.added_edges), len(result.removed_edges))

    return result


def _load_json(path: Path) -> list[dict]:
    """Load a JSON file, return empty list if not found."""
    if not path.exists():
        logger.warning("File not found: %s", path)
        return []
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return []


def _diff_nodes(old_nodes: list[dict], new_nodes: list[dict], result: DiffResult,
                include_properties: bool):
    """Compare nodes by stable_id and semantic_hash."""
    old_index: dict[str, dict] = {}
    for n in old_nodes:
        sid = n.get("stable_id", n.get("id", ""))
        if sid:
            old_index[sid] = n

    new_index: dict[str, dict] = {}
    for n in new_nodes:
        sid = n.get("stable_id", n.get("id", ""))
        if sid:
            new_index[sid] = n

    # Added: in new but not in old
    for sid, node in new_index.items():
        if sid not in old_index:
            result.added_nodes.append(NodeDiff(
                stable_id=sid,
                node_id=node.get("id", ""),
                node_type=node.get("node_type", ""),
                name=node.get("name", ""),
                change="added",
            ))

    # Removed: in old but not in new
    for sid, node in old_index.items():
        if sid not in new_index:
            result.removed_nodes.append(NodeDiff(
                stable_id=sid,
                node_id=node.get("id", ""),
                node_type=node.get("node_type", ""),
                name=node.get("name", ""),
                change="removed",
            ))

    # Modified or unchanged: in both
    for sid in old_index:
        if sid in new_index:
            old_node = old_index[sid]
            new_node = new_index[sid]
            old_hash = old_node.get("semantic_hash", "")
            new_hash = new_node.get("semantic_hash", "")

            if old_hash != new_hash:
                changed_props = {}
                if include_properties:
                    changed_props = _compute_property_diff(old_node, new_node)
                result.modified_nodes.append(NodeDiff(
                    stable_id=sid,
                    node_id=new_node.get("id", ""),
                    node_type=new_node.get("node_type", ""),
                    name=new_node.get("name", ""),
                    change="modified",
                    old_hash=old_hash,
                    new_hash=new_hash,
                    changed_properties=changed_props,
                ))
            else:
                result.unchanged_count += 1


def _diff_edges(old_edges: list[dict], new_edges: list[dict], result: DiffResult):
    """Compare edges by stable_id (or source+target+type composite key)."""
    def edge_key(e: dict) -> str:
        return e.get("stable_id", f"{e.get('source_id', '')}_{e.get('edge_type', '')}_{e.get('target_id', '')}")

    old_set = {edge_key(e): e for e in old_edges}
    new_set = {edge_key(e): e for e in new_edges}

    for key, edge in new_set.items():
        if key not in old_set:
            result.added_edges.append(EdgeDiff(
                stable_id=key,
                source_id=edge.get("source_id", ""),
                target_id=edge.get("target_id", ""),
                edge_type=edge.get("edge_type", ""),
                change="added",
            ))

    for key, edge in old_set.items():
        if key not in new_set:
            result.removed_edges.append(EdgeDiff(
                stable_id=key,
                source_id=edge.get("source_id", ""),
                target_id=edge.get("target_id", ""),
                edge_type=edge.get("edge_type", ""),
                change="removed",
            ))

    result.unchanged_edges_count = len(set(old_set.keys()) & set(new_set.keys()))


def _compute_property_diff(old_node: dict, new_node: dict) -> dict:
    """Compute which properties changed between two versions of a node."""
    skip_keys = {"id", "stable_id", "semantic_hash", "configuration_capture_time"}
    changed = {}

    all_keys = set(old_node.keys()) | set(new_node.keys())
    for key in all_keys:
        if key in skip_keys:
            continue
        old_val = old_node.get(key)
        new_val = new_node.get(key)
        if old_val != new_val:
            changed[key] = {"old": old_val, "new": new_val}

    return changed
