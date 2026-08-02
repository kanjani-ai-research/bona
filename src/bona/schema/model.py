"""Bona Schema — core data types for the asset graph.

Defines the nodes, edges, and results that flow through the pipeline.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class AssetNode:
    """A node in the asset graph (resource, service, config, finding)."""

    id: str                         # unique identifier (ARN for AWS)
    node_type: str                  # CSPResource, CSPService, Configuration, Finding
    provider: str                   # aws, azure, gcp
    properties: dict = field(default_factory=dict)

    # Common properties (extracted for convenience)
    name: str = ""
    region: str = ""
    account_id: str = ""
    resource_type: str = ""         # e.g., "AWS::EC2::Instance"
    state: str = ""                 # ACTIVE, DELETED, NON_COMPLIANT
    created_at: Optional[str] = None
    tags: dict = field(default_factory=dict)


@dataclass
class AssetEdge:
    """An edge in the asset graph (relationship between nodes)."""

    source_id: str
    target_id: str
    edge_type: str                  # DEPENDS_ON, IN_ACCOUNT, HAS_FINDING, etc.
    properties: dict = field(default_factory=dict)


@dataclass
class DiscoveryResult:
    """Output of a discovery run — resources + relationships."""

    provider: str                   # aws, azure, gcp
    account_id: str = ""
    region: str = ""
    resources: list[AssetNode] = field(default_factory=list)
    relationships: list[AssetEdge] = field(default_factory=list)
    findings: list[AssetNode] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    discovered_at: str = ""
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
