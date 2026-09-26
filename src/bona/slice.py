"""Bona Slice — subgraph extraction from the Asset Property Graph.

Inspired by Joern's cpg-slicing: extract useful subsets around resources.

Modes:
  - neighborhood: N-hop subgraph around a resource
  - path: shortest path between two resources
  - type: all nodes/edges of a specific type
  - impact: upstream (what depends on X) or downstream (what X depends on)

Usage:
    bona slice --resource arn:aws:ec2:...:instance/i-abc --depth 2
    bona slice --path arn:aws:iam::role/X arn:aws:kms::key/Y
    bona slice --type AWS::Bedrock::Agent
"""

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)


@dataclass
class SliceResult:
    """Result of a graph slice operation."""

    nodes: list[AssetNode] = field(default_factory=list)
    edges: list[AssetEdge] = field(default_factory=list)
    root: str = ""
    mode: str = ""
    depth: int = 0

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def summary(self) -> str:
        return f"{self.mode} slice: {self.node_count} nodes, {self.edge_count} edges (depth={self.depth})"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "root": self.root,
            "depth": self.depth,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }


def neighborhood_slice(
    nodes: list[AssetNode],
    edges: list[AssetEdge],
    resource_id: str,
    depth: int = 2,
) -> SliceResult:
    """Extract N-hop subgraph around a resource.

    Follows edges in both directions from the given resource.
    """
    # Build adjacency (both directions)
    node_lookup = {n.id: n for n in nodes}
    adj: dict[str, set[str]] = {}
    edge_lookup: dict[tuple[str, str], AssetEdge] = {}

    for e in edges:
        adj.setdefault(e.source_id, set()).add(e.target_id)
        adj.setdefault(e.target_id, set()).add(e.source_id)
        edge_lookup[(e.source_id, e.target_id)] = e

    # BFS from resource_id
    # Match by full ID or partial (name/short id)
    root_id = _resolve_resource_id(resource_id, node_lookup)
    if not root_id:
        logger.warning("Resource not found: %s", resource_id)
        return SliceResult(mode="neighborhood", root=resource_id, depth=depth)

    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(root_id, 0)])
    visited.add(root_id)

    while queue:
        current, d = queue.popleft()
        if d >= depth:
            continue
        for neighbor in adj.get(current, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, d + 1))

    # Collect nodes and edges within the slice
    slice_nodes = [node_lookup[nid] for nid in visited if nid in node_lookup]
    slice_edges = [e for e in edges if e.source_id in visited and e.target_id in visited]

    return SliceResult(
        nodes=slice_nodes,
        edges=slice_edges,
        root=root_id,
        mode="neighborhood",
        depth=depth,
    )


def path_slice(
    nodes: list[AssetNode],
    edges: list[AssetEdge],
    source_id: str,
    target_id: str,
) -> SliceResult:
    """Find shortest path between two resources."""
    node_lookup = {n.id: n for n in nodes}
    adj: dict[str, set[str]] = {}

    for e in edges:
        adj.setdefault(e.source_id, set()).add(e.target_id)
        adj.setdefault(e.target_id, set()).add(e.source_id)

    src = _resolve_resource_id(source_id, node_lookup)
    tgt = _resolve_resource_id(target_id, node_lookup)

    if not src or not tgt:
        logger.warning("Could not resolve source=%s or target=%s", source_id, target_id)
        return SliceResult(mode="path")

    # BFS shortest path
    visited = {src}
    queue: deque[list[str]] = deque([[src]])

    while queue:
        path = queue.popleft()
        current = path[-1]

        if current == tgt:
            # Found path — collect nodes and edges along it
            path_nodes = [node_lookup[nid] for nid in path if nid in node_lookup]
            path_edges = []
            for i in range(len(path) - 1):
                for e in edges:
                    if (e.source_id == path[i] and e.target_id == path[i + 1]) or \
                       (e.source_id == path[i + 1] and e.target_id == path[i]):
                        path_edges.append(e)
                        break

            return SliceResult(
                nodes=path_nodes,
                edges=path_edges,
                root=src,
                mode="path",
                depth=len(path) - 1,
            )

        for neighbor in adj.get(current, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(path + [neighbor])

    logger.info("No path found between %s and %s", src, tgt)
    return SliceResult(mode="path", root=src)


def type_slice(
    nodes: list[AssetNode],
    edges: list[AssetEdge],
    resource_type: str,
) -> SliceResult:
    """Extract all nodes of a specific type and their interconnecting edges."""
    type_nodes = [n for n in nodes if n.resource_type == resource_type or n.node_type == resource_type]
    type_ids = {n.id for n in type_nodes}

    # Include edges where both ends are in the type set, OR edges connecting to/from the type
    type_edges = [e for e in edges if e.source_id in type_ids or e.target_id in type_ids]

    # Also include connected nodes (1-hop neighbors that aren't the same type)
    connected_ids = set()
    for e in type_edges:
        connected_ids.add(e.source_id)
        connected_ids.add(e.target_id)

    all_nodes = [n for n in nodes if n.id in connected_ids]

    return SliceResult(
        nodes=all_nodes,
        edges=type_edges,
        root=resource_type,
        mode="type",
        depth=1,
    )


def impact_slice(
    nodes: list[AssetNode],
    edges: list[AssetEdge],
    resource_id: str,
    direction: str = "downstream",
    depth: int = 5,
) -> SliceResult:
    """Trace impact — what depends on this resource (upstream) or what it depends on (downstream).

    downstream: follow outgoing edges (what does this resource USE/CONNECT TO?)
    upstream: follow incoming edges (what USES/CONNECTS TO this resource?)
    """
    node_lookup = {n.id: n for n in nodes}

    # Build directed adjacency
    if direction == "downstream":
        adj: dict[str, set[str]] = {}
        for e in edges:
            adj.setdefault(e.source_id, set()).add(e.target_id)
    else:  # upstream
        adj = {}
        for e in edges:
            adj.setdefault(e.target_id, set()).add(e.source_id)

    root_id = _resolve_resource_id(resource_id, node_lookup)
    if not root_id:
        return SliceResult(mode=f"impact-{direction}", root=resource_id, depth=depth)

    # BFS in the specified direction
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(root_id, 0)])
    visited.add(root_id)

    while queue:
        current, d = queue.popleft()
        if d >= depth:
            continue
        for neighbor in adj.get(current, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, d + 1))

    slice_nodes = [node_lookup[nid] for nid in visited if nid in node_lookup]
    slice_edges = [e for e in edges if e.source_id in visited and e.target_id in visited]

    return SliceResult(
        nodes=slice_nodes,
        edges=slice_edges,
        root=root_id,
        mode=f"impact-{direction}",
        depth=depth,
    )


def _resolve_resource_id(resource_id: str, node_lookup: dict[str, AssetNode]) -> str:
    """Resolve a resource ID (full ARN, short ID, or name) to a node ID."""
    # Exact match
    if resource_id in node_lookup:
        return resource_id

    # Partial match on ID
    for nid in node_lookup:
        if resource_id in nid:
            return nid

    # Match by name
    for nid, node in node_lookup.items():
        if node.name == resource_id:
            return nid

    return ""
