"""APGQL — Asset Property Graph Query Language.

Fluent Python API for traversing the Asset Property Graph.
Inspired by Joern's CPGQL: lazy traversals, chainable steps, execution directives.

Usage:
    from bona.query import APG

    apg = APG(nodes, edges)

    # All EC2 instances in a specific VPC
    apg.nodes("AWS::EC2::Instance").where(vpcId="vpc-abc").name.l

    # What models do agents use?
    apg.nodes("AWS::Bedrock::Agent").out("USES_MODEL").name.l

    # Trace from a resource outward
    apg.node("subnet-abc").repeat(lambda t: t.out()).until(lambda t: t.isType("AWS::KMS::Key")).path.l
"""

import fnmatch
import logging
from typing import Any, Callable, Optional, Self

from ..schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)


class Traversal:
    """A lazy graph traversal — chainable steps that execute on .l or .toList."""

    def __init__(self, nodes: list[AssetNode], edges: list[AssetEdge], all_nodes: list[AssetNode], all_edges: list[AssetEdge]):
        self._nodes = nodes          # current result set
        self._edges = edges          # all edges (for traversal)
        self._all_nodes = all_nodes  # full graph nodes
        self._all_edges = all_edges  # full graph edges
        self._path: list[list[str]] = []  # for path tracking

    # ==================================================================
    # Node-Type Steps
    # ==================================================================

    def isType(self, *types: str) -> "Traversal":
        """Filter to nodes matching type(s). Supports globs."""
        filtered = []
        for n in self._nodes:
            for t in types:
                if fnmatch.fnmatch(n.node_type, t) or fnmatch.fnmatch(n.resource_type, t):
                    filtered.append(n)
                    break
        return self._with(filtered)

    def notType(self, *types: str) -> "Traversal":
        """Exclude nodes matching type(s)."""
        excluded = set()
        for n in self._nodes:
            for t in types:
                if fnmatch.fnmatch(n.node_type, t) or fnmatch.fnmatch(n.resource_type, t):
                    excluded.add(n.id)
                    break
        return self._with([n for n in self._nodes if n.id not in excluded])

    # ==================================================================
    # Filter Steps
    # ==================================================================

    def where(self, **kwargs) -> "Traversal":
        """Filter nodes by property values. Supports exact match."""
        filtered = []
        for n in self._nodes:
            d = n.to_dict()
            match = all(d.get(k) == v for k, v in kwargs.items())
            if match:
                filtered.append(n)
        return self._with(filtered)

    def whereNot(self, **kwargs) -> "Traversal":
        """Exclude nodes matching property values."""
        filtered = []
        for n in self._nodes:
            d = n.to_dict()
            match = any(d.get(k) == v for k, v in kwargs.items())
            if not match:
                filtered.append(n)
        return self._with(filtered)

    def filter(self, fn: Callable[[AssetNode], bool]) -> "Traversal":
        """Filter nodes using a lambda function."""
        return self._with([n for n in self._nodes if fn(n)])

    def has(self, prop: str) -> "Traversal":
        """Filter to nodes that have a non-empty property."""
        return self._with([n for n in self._nodes if n.properties.get(prop) or n.to_dict().get(prop)])

    def hasNot(self, prop: str) -> "Traversal":
        """Filter to nodes that don't have a property."""
        return self._with([n for n in self._nodes if not n.properties.get(prop) and not n.to_dict().get(prop)])

    def id(self, *ids: str) -> "Traversal":
        """Filter by node ID (exact or partial match)."""
        filtered = []
        for n in self._nodes:
            for i in ids:
                if i == n.id or i in n.id or n.name == i:
                    filtered.append(n)
                    break
        return self._with(filtered)

    # ==================================================================
    # Edge Traversal Steps
    # ==================================================================

    def out(self, *edge_types: str) -> "Traversal":
        """Follow outgoing edges. Returns target nodes."""
        current_ids = {n.id for n in self._nodes}
        target_ids = set()
        for e in self._all_edges:
            if e.source_id in current_ids:
                if not edge_types or e.edge_type in edge_types:
                    target_ids.add(e.target_id)
        targets = [n for n in self._all_nodes if n.id in target_ids]
        return self._with(targets)

    def in_(self, *edge_types: str) -> "Traversal":
        """Follow incoming edges. Returns source nodes."""
        current_ids = {n.id for n in self._nodes}
        source_ids = set()
        for e in self._all_edges:
            if e.target_id in current_ids:
                if not edge_types or e.edge_type in edge_types:
                    source_ids.add(e.source_id)
        sources = [n for n in self._all_nodes if n.id in source_ids]
        return self._with(sources)

    def both(self, *edge_types: str) -> "Traversal":
        """Follow edges in both directions."""
        current_ids = {n.id for n in self._nodes}
        neighbor_ids = set()
        for e in self._all_edges:
            if not edge_types or e.edge_type in edge_types:
                if e.source_id in current_ids:
                    neighbor_ids.add(e.target_id)
                if e.target_id in current_ids:
                    neighbor_ids.add(e.source_id)
        neighbors = [n for n in self._all_nodes if n.id in neighbor_ids]
        return self._with(neighbors)

    def outEdges(self, *edge_types: str) -> "EdgeTraversal":
        """Get outgoing edges as EdgeTraversal."""
        current_ids = {n.id for n in self._nodes}
        matched = [e for e in self._all_edges
                   if e.source_id in current_ids and (not edge_types or e.edge_type in edge_types)]
        return EdgeTraversal(matched, self._all_nodes, self._all_edges)

    def inEdges(self, *edge_types: str) -> "EdgeTraversal":
        """Get incoming edges as EdgeTraversal."""
        current_ids = {n.id for n in self._nodes}
        matched = [e for e in self._all_edges
                   if e.target_id in current_ids and (not edge_types or e.edge_type in edge_types)]
        return EdgeTraversal(matched, self._all_nodes, self._all_edges)

    # ==================================================================
    # Repeat Steps
    # ==================================================================

    def repeat(self, step_fn: Callable[["Traversal"], "Traversal"], times: int = 0, until_fn: Callable[["Traversal"], bool] | None = None, max_depth: int = 10) -> "Traversal":
        """Repeat a traversal step.

        Args:
            step_fn: function that takes a Traversal and returns a Traversal
            times: exact number of repetitions (0 = use until_fn or max_depth)
            until_fn: stop when this returns True on the current traversal
            max_depth: safety cap
        """
        current = self
        all_collected = list(self._nodes)
        visited = {n.id for n in self._nodes}

        iterations = times if times > 0 else max_depth

        for i in range(iterations):
            next_t = step_fn(current)
            # Deduplicate
            new_nodes = [n for n in next_t._nodes if n.id not in visited]
            if not new_nodes:
                break
            for n in new_nodes:
                visited.add(n.id)
            all_collected.extend(new_nodes)
            current = self._with(new_nodes)

            if until_fn and until_fn(current):
                break
            if times > 0 and i + 1 >= times:
                break

        return self._with(all_collected)

    # ==================================================================
    # Map Steps
    # ==================================================================

    def map(self, fn: Callable[[AssetNode], Any]) -> list[Any]:
        """Map nodes through a function and return results."""
        return [fn(n) for n in self._nodes]

    def select(self, *keys: str) -> list[dict]:
        """Select specific properties from nodes."""
        results = []
        for n in self._nodes:
            d = n.to_dict()
            results.append({k: d.get(k, n.properties.get(k)) for k in keys})
        return results

    # ==================================================================
    # Property Directives
    # ==================================================================

    @property
    def name(self) -> "PropertyTraversal":
        """Access the name property."""
        return PropertyTraversal([n.name for n in self._nodes])

    @property
    def id_(self) -> "PropertyTraversal":
        """Access the id property."""
        return PropertyTraversal([n.id for n in self._nodes])

    @property
    def node_type(self) -> "PropertyTraversal":
        """Access the node_type property."""
        return PropertyTraversal([n.node_type for n in self._nodes])

    @property
    def state(self) -> "PropertyTraversal":
        """Access the state property."""
        return PropertyTraversal([n.state for n in self._nodes])

    def prop(self, key: str) -> "PropertyTraversal":
        """Access any property by key."""
        return PropertyTraversal([n.properties.get(key, n.to_dict().get(key, "")) for n in self._nodes])

    # ==================================================================
    # Execution Directives
    # ==================================================================

    @property
    def l(self) -> list[dict]:
        """Execute traversal — return results as list of dicts."""
        return [n.to_dict() for n in self._nodes]

    @property
    def toList(self) -> list[dict]:
        """Alias for .l"""
        return self.l

    @property
    def count(self) -> int:
        """Return count of nodes in traversal."""
        return len(self._nodes)

    @property
    def first(self) -> dict | None:
        """Return first node or None."""
        return self._nodes[0].to_dict() if self._nodes else None

    @property
    def ids(self) -> list[str]:
        """Return list of node IDs."""
        return [n.id for n in self._nodes]

    @property
    def names(self) -> list[str]:
        """Return list of node names."""
        return [n.name for n in self._nodes]

    @property
    def types(self) -> list[str]:
        """Return unique node types."""
        return sorted(set(n.node_type for n in self._nodes))

    def head(self, n: int = 5) -> list[dict]:
        """Return first N results."""
        return [node.to_dict() for node in self._nodes[:n]]

    def __repr__(self) -> str:
        types = set(n.node_type for n in self._nodes[:10])
        return f"Traversal({len(self._nodes)} nodes, types={types})"

    # ==================================================================
    # Internal
    # ==================================================================

    def _with(self, nodes: list[AssetNode]) -> "Traversal":
        """Create new traversal with different node set."""
        return Traversal(nodes, self._edges, self._all_nodes, self._all_edges)


class EdgeTraversal:
    """Traversal over edges."""

    def __init__(self, edges: list[AssetEdge], all_nodes: list[AssetNode], all_edges: list[AssetEdge]):
        self._edges = edges
        self._all_nodes = all_nodes
        self._all_edges = all_edges
        self._node_lookup = {n.id: n for n in all_nodes}

    def targets(self) -> Traversal:
        """Get target nodes of these edges."""
        target_ids = {e.target_id for e in self._edges}
        nodes = [self._node_lookup[tid] for tid in target_ids if tid in self._node_lookup]
        return Traversal(nodes, self._all_edges, self._all_nodes, self._all_edges)

    def sources(self) -> Traversal:
        """Get source nodes of these edges."""
        source_ids = {e.source_id for e in self._edges}
        nodes = [self._node_lookup[sid] for sid in source_ids if sid in self._node_lookup]
        return Traversal(nodes, self._all_edges, self._all_nodes, self._all_edges)

    @property
    def edge_types(self) -> list[str]:
        """Get unique edge types."""
        return sorted(set(e.edge_type for e in self._edges))

    @property
    def count(self) -> int:
        return len(self._edges)

    @property
    def l(self) -> list[dict]:
        return [e.to_dict() for e in self._edges]


class PropertyTraversal:
    """Traversal over property values."""

    def __init__(self, values: list[Any]):
        self._values = values

    @property
    def l(self) -> list[Any]:
        """Execute — return values as list."""
        return self._values

    @property
    def toList(self) -> list[Any]:
        return self._values

    @property
    def distinct(self) -> list[Any]:
        """Return unique values."""
        return sorted(set(v for v in self._values if v))

    @property
    def count(self) -> int:
        return len(self._values)

    def contains(self, substr: str) -> "PropertyTraversal":
        """Filter values containing a substring."""
        return PropertyTraversal([v for v in self._values if isinstance(v, str) and substr in v])

    def __repr__(self) -> str:
        return f"PropertyTraversal({len(self._values)} values)"


class APG:
    """Asset Property Graph — root object for APGQL queries.

    Usage:
        apg = APG(nodes, edges)
        apg.nodes("AWS::EC2::Instance").where(state="ACTIVE").name.l
    """

    def __init__(self, nodes: list[AssetNode], edges: list[AssetEdge]):
        self._nodes = nodes
        self._edges = edges

    def nodes(self, *types: str) -> Traversal:
        """Start traversal from nodes of given type(s). Supports globs."""
        if not types:
            return Traversal(self._nodes, self._edges, self._nodes, self._edges)
        filtered = []
        for n in self._nodes:
            for t in types:
                if fnmatch.fnmatch(n.node_type, t) or fnmatch.fnmatch(n.resource_type, t):
                    filtered.append(n)
                    break
        return Traversal(filtered, self._edges, self._nodes, self._edges)

    def node(self, id_or_name: str) -> Traversal:
        """Start traversal from a specific node by ID or name."""
        matched = [n for n in self._nodes if id_or_name == n.id or id_or_name in n.id or n.name == id_or_name]
        return Traversal(matched, self._edges, self._nodes, self._edges)

    def edges(self, *types: str) -> EdgeTraversal:
        """Start traversal from edges of given type(s)."""
        if not types:
            return EdgeTraversal(self._edges, self._nodes, self._edges)
        filtered = [e for e in self._edges if e.edge_type in types]
        return EdgeTraversal(filtered, self._nodes, self._edges)

    @property
    def all(self) -> Traversal:
        """All nodes."""
        return Traversal(self._nodes, self._edges, self._nodes, self._edges)

    @property
    def summary(self) -> str:
        """Quick summary of the graph."""
        from collections import Counter
        types = Counter(n.node_type for n in self._nodes)
        edge_types = Counter(e.edge_type for e in self._edges)
        return (
            f"APG: {len(self._nodes)} nodes ({len(types)} types), "
            f"{len(self._edges)} edges ({len(edge_types)} types)"
        )

    def __repr__(self) -> str:
        return self.summary
