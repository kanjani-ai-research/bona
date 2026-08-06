"""Graph Schema — defines known node types and edge types.

Maintains a schema file (graph-schema.json) that describes valid
node types and edge types in the Asset Property Graph.

Learn mode: when enabled, bona automatically updates the schema file
as it discovers new node types, edge types, and relationships.
This evolves the schema over time without manual maintenance.

Schema file location:
  - ~/.bona/graph-schema.json (default)
  - Configurable via bona.yaml: schema.path

Schema structure:
{
  "version": "1.0",
  "last_updated": "2026-08-06T10:00:00Z",
  "node_types": {
    "AWS::EC2::Instance": {
      "description": "...",
      "observed_properties": ["instanceType", "vpcId", ...],
      "observed_count": 42,
      "first_seen": "2026-08-06T10:00:00Z",
      "last_seen": "2026-08-06T12:00:00Z"
    }
  },
  "edge_types": {
    "IS_CONTAINED_IN": {
      "description": "Hierarchy — child contained in parent",
      "observed_pairs": [
        {"source_type": "AWS::EC2::Subnet", "target_type": "AWS::EC2::VPC"}
      ],
      "observed_count": 15,
      "first_seen": "...",
      "last_seen": "..."
    }
  }
}
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA_PATH = Path.home() / ".bona" / "graph-schema.json"


@dataclass
class NodeTypeSchema:
    """Schema for a single node type."""

    type_name: str
    description: str = ""
    observed_properties: list[str] = field(default_factory=list)
    observed_count: int = 0
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "observed_properties": sorted(set(self.observed_properties)),
            "observed_count": self.observed_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, type_name: str, data: dict) -> "NodeTypeSchema":
        return cls(
            type_name=type_name,
            description=data.get("description", ""),
            observed_properties=data.get("observed_properties", []),
            observed_count=data.get("observed_count", 0),
            first_seen=data.get("first_seen", ""),
            last_seen=data.get("last_seen", ""),
        )


@dataclass
class EdgeTypePair:
    """An observed source→target type pair for an edge type."""

    source_type: str
    target_type: str

    def to_dict(self) -> dict:
        return {"source_type": self.source_type, "target_type": self.target_type}

    @classmethod
    def from_dict(cls, data: dict) -> "EdgeTypePair":
        return cls(source_type=data.get("source_type", ""), target_type=data.get("target_type", ""))


@dataclass
class EdgeTypeSchema:
    """Schema for a single edge type."""

    edge_type: str
    description: str = ""
    observed_pairs: list[EdgeTypePair] = field(default_factory=list)
    observed_count: int = 0
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict:
        # Deduplicate pairs
        seen = set()
        unique_pairs = []
        for p in self.observed_pairs:
            key = (p.source_type, p.target_type)
            if key not in seen:
                seen.add(key)
                unique_pairs.append(p.to_dict())
        return {
            "description": self.description,
            "observed_pairs": unique_pairs,
            "observed_count": self.observed_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, edge_type: str, data: dict) -> "EdgeTypeSchema":
        pairs = [EdgeTypePair.from_dict(p) for p in data.get("observed_pairs", [])]
        return cls(
            edge_type=edge_type,
            description=data.get("description", ""),
            observed_pairs=pairs,
            observed_count=data.get("observed_count", 0),
            first_seen=data.get("first_seen", ""),
            last_seen=data.get("last_seen", ""),
        )


@dataclass
class GraphSchema:
    """Full graph schema — all known node and edge types.

    Version follows semver (MAJOR.MINOR.PATCH):
      - PATCH: new observations of existing types (count/timestamp updates)
      - MINOR: new node types, edge types, or relationship pairs discovered
      - MAJOR: manual bump only (breaking schema structure changes)
    """

    version: str = "0.1.0"
    last_updated: str = ""
    node_types: dict[str, NodeTypeSchema] = field(default_factory=dict)
    edge_types: dict[str, EdgeTypeSchema] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "last_updated": self.last_updated,
            "node_types": {k: v.to_dict() for k, v in sorted(self.node_types.items())},
            "edge_types": {k: v.to_dict() for k, v in sorted(self.edge_types.items())},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphSchema":
        node_types = {}
        for name, nd in data.get("node_types", {}).items():
            node_types[name] = NodeTypeSchema.from_dict(name, nd)

        edge_types = {}
        for name, ed in data.get("edge_types", {}).items():
            edge_types[name] = EdgeTypeSchema.from_dict(name, ed)

        return cls(
            version=data.get("version", "0.1.0"),
            last_updated=data.get("last_updated", ""),
            node_types=node_types,
            edge_types=edge_types,
        )

    @classmethod
    def empty(cls) -> "GraphSchema":
        return cls()

    @property
    def node_type_count(self) -> int:
        return len(self.node_types)

    @property
    def edge_type_count(self) -> int:
        return len(self.edge_types)

    def summary(self) -> str:
        total_pairs = sum(len(e.observed_pairs) for e in self.edge_types.values())
        return (
            f"v{self.version} — {self.node_type_count} node types, "
            f"{self.edge_type_count} edge types, "
            f"{total_pairs} observed relationship pairs"
        )

    def bump_version(self, level: str = "patch") -> str:
        """Bump schema version following semver.

        Args:
            level: 'major', 'minor', or 'patch'

        Returns:
            New version string
        """
        parts = self.version.split(".")
        if len(parts) != 3:
            parts = ["0", "1", "0"]

        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

        if level == "major":
            major += 1
            minor = 0
            patch = 0
        elif level == "minor":
            minor += 1
            patch = 0
        else:  # patch
            patch += 1

        self.version = f"{major}.{minor}.{patch}"
        return self.version


# ==================================================================
# Load / Save
# ==================================================================


def load_graph_schema(path: Optional[str] = None) -> GraphSchema:
    """Load graph schema from file. Returns empty schema if not found."""
    schema_path = Path(path) if path else DEFAULT_SCHEMA_PATH

    if not schema_path.exists():
        logger.debug("No graph schema found at %s, starting fresh", schema_path)
        return GraphSchema.empty()

    try:
        data = json.loads(schema_path.read_text())
        schema = GraphSchema.from_dict(data)
        logger.info("Loaded graph schema: %s (%s)", schema_path, schema.summary())
        return schema
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load graph schema: %s", e)
        return GraphSchema.empty()


def save_graph_schema(schema: GraphSchema, path: Optional[str] = None) -> Path:
    """Save graph schema to file."""
    schema_path = Path(path) if path else DEFAULT_SCHEMA_PATH
    schema_path.parent.mkdir(parents=True, exist_ok=True)

    schema.last_updated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    schema_path.write_text(json.dumps(schema.to_dict(), indent=2))
    logger.info("Saved graph schema: %s (%s)", schema_path, schema.summary())
    return schema_path


# ==================================================================
# Learn mode — update schema from discovered data
# ==================================================================


def learn_from_discovery(
    schema: GraphSchema,
    nodes: list[AssetNode],
    edges: list[AssetEdge],
) -> GraphSchema:
    """Update graph schema by learning from discovered nodes and edges.

    Adds new node types, edge types, observed properties, and relationship pairs.
    Increments counts and updates last_seen timestamps.

    Version bumping (semver):
      - MINOR bump: new node types, edge types, or relationship pairs discovered
      - PATCH bump: only counts/timestamps updated (no structural changes)

    Args:
        schema: existing graph schema (will be mutated)
        nodes: discovered asset nodes
        edges: discovered edges

    Returns:
        Updated schema (same object, mutated)
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Build a lookup: node_id → node_type for edge pair resolution
    node_type_lookup = {n.id: n.node_type for n in nodes}

    # Track what's new for version bumping
    new_node_types = 0
    new_edge_types = 0
    new_pairs = 0

    # Learn node types
    for node in nodes:
        nt = node.node_type
        if nt not in schema.node_types:
            schema.node_types[nt] = NodeTypeSchema(
                type_name=nt,
                first_seen=now,
            )
            new_node_types += 1

        type_schema = schema.node_types[nt]
        type_schema.last_seen = now
        type_schema.observed_count += 1

        # Learn properties
        for prop in node.properties.keys():
            if prop not in type_schema.observed_properties and not prop.startswith("_"):
                type_schema.observed_properties.append(prop)

    # Learn edge types
    for edge in edges:
        et = edge.edge_type
        if et not in schema.edge_types:
            schema.edge_types[et] = EdgeTypeSchema(
                edge_type=et,
                first_seen=now,
            )
            new_edge_types += 1

        edge_schema = schema.edge_types[et]
        edge_schema.last_seen = now
        edge_schema.observed_count += 1

        # Learn relationship pairs (source_type → target_type)
        source_type = node_type_lookup.get(edge.source_id, "")
        target_type = node_type_lookup.get(edge.target_id, "")

        if source_type and target_type:
            pair = EdgeTypePair(source_type=source_type, target_type=target_type)
            existing = any(
                p.source_type == pair.source_type and p.target_type == pair.target_type
                for p in edge_schema.observed_pairs
            )
            if not existing:
                edge_schema.observed_pairs.append(pair)
                new_pairs += 1

    # Auto-bump version based on what changed
    if new_node_types > 0 or new_edge_types > 0 or new_pairs > 0:
        old_version = schema.version
        schema.bump_version("minor")
        logger.info(
            "Learn mode: v%s → v%s (+%d node types, +%d edge types, +%d pairs)",
            old_version, schema.version, new_node_types, new_edge_types, new_pairs,
        )
    else:
        old_version = schema.version
        schema.bump_version("patch")
        logger.info(
            "Learn mode: v%s → v%s (observations updated, no structural changes)",
            old_version, schema.version,
        )

    logger.debug("Schema: %s", schema.summary())

    return schema
