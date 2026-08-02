"""Bona — Infrastructure asset graph.

The Joern for cloud. Discovers, maps, and graphs CSP resources
via AWS Config (and Azure/GCP adapters in future).

Usage:
    from bona import AWSProvider, DiscoveryPipeline

    provider = AWSProvider(account_id="123456789012", region="us-east-1")
    result = provider.discover()
    print(f"Found {len(result.resources)} resources, {len(result.relationships)} relationships")

    # Or run the full pipeline (discover → transform → load → enrich)
    pipeline = DiscoveryPipeline(provider)
    pipeline.run()
"""

__version__ = "0.1.0"

from .providers.provider_base import AssetProvider
from .providers.aws.aws_provider import AWSProvider
from .pipeline.discovery_pipeline import DiscoveryPipeline
from .schema.model import AssetNode, AssetEdge, DiscoveryResult

__all__ = [
    "AssetProvider",
    "AWSProvider",
    "DiscoveryPipeline",
    "AssetNode",
    "AssetEdge",
    "DiscoveryResult",
]
