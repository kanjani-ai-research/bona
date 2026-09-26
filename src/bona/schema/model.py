"""Bona Schema — Joern-compatible node/edge types for the Asset Property Graph.

Output format matches joern-cpg cpg_exporter.py:
  nodes.json: [{"id", "node_type", "stable_id", "semantic_hash", ...}]
  edges.json: [{"source_id", "target_id", "edge_type", "stable_id", ...}]
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AssetNode:
    """A node in the Asset Property Graph."""

    id: str                         # ARN (globally unique)
    node_type: str                  # AWS::EC2::Instance, AWS::S3::Bucket, etc.
    name: str = ""
    provider: str = "aws"
    account_id: str = ""
    region: str = ""
    resource_type: str = ""         # same as node_type for AWS
    state: str = "ACTIVE"
    tags: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)
    configuration_capture_time: str = ""

    @property
    def stable_id(self) -> str:
        """Deterministic identity for delta reconciliation."""
        # Extract short resource ID from ARN
        short_id = self.id.split("/")[-1] if "/" in self.id else self.id.split(":")[-1]
        return f"{self.provider}|{self.account_id}|{self.region}|{self.node_type}|{short_id}"

    @property
    def semantic_hash(self) -> str:
        """SHA-256 of sorted config properties — detects config drift."""
        # Include key properties that indicate configuration state
        hashable = {
            "id": self.id,
            "node_type": self.node_type,
            "state": self.state,
            "properties": self.properties,
        }
        raw = json.dumps(hashable, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to Joern-compatible flat dict for nodes.json."""
        d = {
            "id": self.id,
            "node_type": self.node_type,
            "name": self.name,
            "stable_id": self.stable_id,
            "semantic_hash": self.semantic_hash,
            "provider": self.provider,
            "account_id": self.account_id,
            "region": self.region,
            "resource_type": self.resource_type or self.node_type,
            "state": self.state,
        }
        if self.tags:
            d["tags"] = self.tags
        if self.configuration_capture_time:
            d["configuration_capture_time"] = self.configuration_capture_time
        # Flatten properties into top-level keys (like Joern does)
        for k, v in self.properties.items():
            if k not in d and v is not None and v != "" and v != []:
                d[k] = v
        return d


@dataclass
class AssetEdge:
    """An edge in the Asset Property Graph."""

    source_id: str
    target_id: str
    edge_type: str                  # IS_CONTAINED_IN, IS_ATTACHED_TO, etc.
    provider: str = "aws"
    account_id: str = ""
    properties: dict = field(default_factory=dict)

    @property
    def stable_id(self) -> str:
        """Deterministic identity for delta reconciliation."""
        src_short = self.source_id.split("/")[-1] if "/" in self.source_id else self.source_id.split(":")[-1]
        tgt_short = self.target_id.split("/")[-1] if "/" in self.target_id else self.target_id.split(":")[-1]
        return f"{self.provider}|{self.account_id}|EDGE|{self.edge_type}|{src_short}\u2192{tgt_short}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to Joern-compatible flat dict for edges.json."""
        d = {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type,
            "stable_id": self.stable_id,
        }
        for k, v in self.properties.items():
            if v is not None and v != "":
                d[k] = v
        return d


@dataclass
class DiscoveryResult:
    """Output of a discovery run."""

    provider: str = "aws"
    account_id: str = ""
    region: str = ""
    resources: list[AssetNode] = field(default_factory=list)
    relationships: list[AssetEdge] = field(default_factory=list)
    findings: list[AssetNode] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def resource_count(self) -> int:
        return len(self.resources)

    @property
    def relationship_count(self) -> int:
        return len(self.relationships)

    def summary(self) -> str:
        return (
            f"Discovered {self.resource_count} resources, "
            f"{self.relationship_count} relationships, "
            f"{len(self.findings)} findings "
            f"in {self.duration_ms}ms"
        )
