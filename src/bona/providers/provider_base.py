"""Asset Provider Base — abstract interface for all providers.

This is bona's equivalent of Joern's X2CPG: the base framework that all
frontends (providers) must implement to produce an Asset Property Graph.

Contract:
  - All providers produce AssetNode + AssetEdge objects
  - All nodes have: id, node_type, name, stable_id, semantic_hash
  - All edges have: source_id, target_id, edge_type, stable_id
  - The discover() method is the main entry point

Writing a new provider:
  1. Subclass AssetProvider
  2. Implement discover() → DiscoveryResult
  3. Register via pluggy (bona_reference_providers hook) or as a package

Example:
    class GCPProvider(AssetProvider):
        name = "gcp"
        
        def discover(self) -> DiscoveryResult:
            nodes, edges = self._query_asset_inventory()
            return DiscoveryResult(resources=nodes, relationships=edges)
"""

from abc import ABC, abstractmethod
from typing import Optional

from ..schema.model import AssetNode, AssetEdge, DiscoveryResult


class AssetProvider(ABC):
    """Abstract base for all asset discovery providers.

    Subclass this to add support for a new cloud platform,
    SaaS service, or application type.
    """

    # Provider identity
    name: str = ""          # e.g. "aws", "azure", "gcp", "k8s"
    description: str = ""   # Human-readable description

    def __init__(self, region: str = "", account_id: str = "", **kwargs):
        self.region = region
        self.account_id = account_id

    @abstractmethod
    def discover(self) -> DiscoveryResult:
        """Discover all resources and relationships.

        This is the main entry point. Returns:
          - resources: list[AssetNode] — discovered assets
          - relationships: list[AssetEdge] — connections between assets
          - findings: list[AssetNode] — compliance/security findings (optional)
          - duration_ms: int — how long discovery took
        """
        ...

    def get_resource_types(self) -> list[str]:
        """List available resource types this provider can discover.

        Override to support type filtering (--types flag).
        Default: returns empty list (discovers all).
        """
        return []

    def validate(self) -> list[str]:
        """Validate that this provider can run (credentials, permissions, etc).

        Returns list of error messages. Empty list = all good.
        Used by `bona doctor`.
        """
        return []
