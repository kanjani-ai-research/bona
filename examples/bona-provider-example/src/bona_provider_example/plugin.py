"""Example bona plugin — adds a custom reference provider.

This demonstrates how to create a bona plugin that:
1. Registers a reference provider (discovers external resources)
2. Registers a relationship classifier (teaches bona new edge patterns)

Install this plugin:
    cd examples/bona-provider-example
    pip install -e .

Then run bona — the plugin is auto-discovered:
    bona export --profile myprofile --region us-east-1 --enrich

Your custom provider will run alongside bona's built-in providers.
"""

import logging
from typing import Callable, Optional

import pluggy

# Import the hookimpl marker from bona
from bona.plugins import hookimpl

logger = logging.getLogger(__name__)


# ==================================================================
# Example Reference Provider
# ==================================================================


def enrich_cost_tags(session, region, resources, refresh=False):
    """Example: discover cost allocation tags and create CostCenter reference nodes.

    This is a simple example — a real provider might call:
    - ce:GetCostAndUsage
    - ce:GetTags
    - organizations:ListAccounts
    """
    from bona.schema.model import AssetNode, AssetEdge

    # Discover unique cost center tags from existing resources
    cost_centers: dict[str, int] = {}
    for resource in resources:
        tags = resource.tags or {}
        cc = tags.get("CostCenter", tags.get("cost-center", tags.get("cost_center", "")))
        if cc:
            cost_centers[cc] = cost_centers.get(cc, 0) + 1

    if not cost_centers:
        return [], []

    # Create CostCenter reference nodes
    nodes = []
    edges = []
    for cc_name, count in cost_centers.items():
        nodes.append(AssetNode(
            id=f"cost-center:{cc_name}",
            node_type="Organization::CostCenter",
            name=cc_name,
            provider="custom",
            account_id="global",
            region="global",
            resource_type="Organization::CostCenter",
            state="ACTIVE",
            properties={"resource_count": count},
        ))

    # Create FUNDED_BY edges
    for resource in resources:
        tags = resource.tags or {}
        cc = tags.get("CostCenter", tags.get("cost-center", tags.get("cost_center", "")))
        if cc:
            edges.append(AssetEdge(
                source_id=resource.id,
                target_id=f"cost-center:{cc_name}",
                edge_type="FUNDED_BY",
                provider="custom",
                account_id=resource.account_id,
            ))

    logger.info("Example plugin: %d CostCenter nodes, %d FUNDED_BY edges", len(nodes), len(edges))
    return nodes, edges


# ==================================================================
# Example Relationship Classifier
# ==================================================================


def classify_url_properties(node_type: str, property_name: str, value: str) -> Optional[tuple[str, str]]:
    """Example classifier: detect HTTPS URLs as service connections.

    Returns (target_type, edge_type) or None.
    """
    if not isinstance(value, str):
        return None
    if value.startswith("https://") and "amazonaws.com" not in value:
        return ("External::Service", "CONNECTS_TO")
    return None


# ==================================================================
# Plugin Registration — this is what bona discovers
# ==================================================================


class BonaProviderExample:
    """Plugin class registered via entry_points."""

    @hookimpl
    def bona_reference_providers(self) -> dict[str, Callable]:
        """Register our custom reference provider."""
        return {
            "cost_tags": enrich_cost_tags,
        }

    @hookimpl
    def bona_relationship_classifiers(self) -> list[Callable]:
        """Register our custom relationship classifier."""
        return [classify_url_properties]


# The module-level instance that pluggy discovers via entry_points
# (entry_points points to this module, pluggy imports it and finds this)
plugin = BonaProviderExample()
