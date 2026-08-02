"""Asset Provider Base — abstract interface for all CSP/Application providers."""

from abc import ABC, abstractmethod
from ..schema.model import DiscoveryResult


class AssetProvider(ABC):
    """Abstract base class for asset discovery providers.

    Each CSP (AWS, Azure, GCP) and application type (K8s, SaaS)
    implements this interface. The pipeline calls these methods
    in sequence to build the asset graph.

    Usage:
        provider = AWSProvider(account_id="123456789012", region="us-east-1")
        result = provider.discover()
    """

    def __init__(self, account_id: str = "", region: str = ""):
        self.account_id = account_id
        self.region = region

    @abstractmethod
    def discover(self) -> DiscoveryResult:
        """Discover all resources, configurations, and relationships.

        Returns a DiscoveryResult with:
          - resources: list of AssetNode (the things)
          - relationships: list of AssetEdge (how they connect)
          - findings: list of AssetNode (compliance/security issues)
        """
        ...

    @abstractmethod
    def discover_services(self) -> list[dict]:
        """Enumerate available services and their operations.

        Returns list of {service_name, operation_count, operations[]}.
        Used by AI recipe generation.
        """
        ...

    @abstractmethod
    def discover_resources(self, resource_types: list[str] | None = None) -> DiscoveryResult:
        """Discover resources, optionally filtered by type.

        Args:
            resource_types: e.g., ["AWS::EC2::Instance", "AWS::S3::Bucket"]
                           None = discover all.
        """
        ...

    @abstractmethod
    def discover_relationships(self, resource_ids: list[str]) -> list:
        """Discover relationships for specific resources.

        Uses AWS Config relationship data.
        """
        ...

    @abstractmethod
    def discover_compliance(self) -> DiscoveryResult:
        """Discover compliance state — which resources pass/fail rules.

        Sources: AWS Config rules, Inspector findings, Security Hub.
        """
        ...
