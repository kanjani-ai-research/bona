"""Bona plugin — AWS Control Tower provider.

Discovers landing zones, enabled controls, and baselines from AWS Control Tower.
"""

import logging
from typing import Callable

from botocore.exceptions import ClientError

from bona.plugins import hookimpl
from bona.schema.model import AssetEdge, AssetNode

logger = logging.getLogger(__name__)


def enrich_control_tower(session, region, resources, refresh=False):
    """Discover AWS Control Tower resources: landing zones, enabled controls, and baselines.

    API calls:
      - controltower:ListLandingZones → LandingZone nodes
      - controltower:ListEnabledControls → EnabledControl nodes + ENFORCES_ON edges
      - controltower:ListEnabledBaselines → Baseline nodes
    """
    nodes: list[AssetNode] = []
    edges: list[AssetEdge] = []

    try:
        client = session.client("controltower", region_name=region)
    except ClientError as e:
        logger.debug("Control Tower not available in %s: %s", region, e)
        return [], []

    account_id = session.client("sts", region_name=region).get_caller_identity()["Account"]

    # --- Landing Zones ---
    try:
        paginator = client.get_paginator("list_landing_zones")
        for page in paginator.paginate():
            for lz in page.get("landingZones", []):
                arn = lz["arn"]
                nodes.append(AssetNode(
                    id=arn,
                    node_type="AWS::ControlTower::LandingZone",
                    name=lz.get("name", arn.split("/")[-1]),
                    provider="aws",
                    account_id=account_id,
                    region=region,
                    resource_type="AWS::ControlTower::LandingZone",
                    state=lz.get("status", "ACTIVE"),
                    properties={
                        "version": lz.get("latestAvailableVersion", ""),
                    },
                ))
    except ClientError as e:
        logger.debug("controltower:ListLandingZones unavailable in %s: %s", region, e)

    # --- Enabled Controls ---
    try:
        paginator = client.get_paginator("list_enabled_controls")
        for page in paginator.paginate():
            for ctrl in page.get("enabledControls", []):
                arn = ctrl["arn"]
                control_id = ctrl.get("controlIdentifier", "")
                target_id = ctrl.get("targetIdentifier", "")

                nodes.append(AssetNode(
                    id=arn,
                    node_type="AWS::ControlTower::EnabledControl",
                    name=control_id.split("/")[-1] if "/" in control_id else control_id,
                    provider="aws",
                    account_id=account_id,
                    region=region,
                    resource_type="AWS::ControlTower::EnabledControl",
                    state=ctrl.get("statusSummary", {}).get("status", "ACTIVE"),
                    properties={
                        "control_identifier": control_id,
                        "target_identifier": target_id,
                        "drift_status": ctrl.get("driftStatusSummary", {}).get("driftStatus", ""),
                    },
                ))

                # Create ENFORCES_ON edge from control to its target OU/account
                if target_id:
                    edges.append(AssetEdge(
                        source_id=arn,
                        target_id=target_id,
                        edge_type="ENFORCES_ON",
                        provider="aws",
                        account_id=account_id,
                    ))
    except ClientError as e:
        logger.debug("controltower:ListEnabledControls unavailable in %s: %s", region, e)

    # --- Enabled Baselines ---
    try:
        paginator = client.get_paginator("list_enabled_baselines")
        for page in paginator.paginate():
            for bl in page.get("enabledBaselines", []):
                arn = bl["arn"]
                nodes.append(AssetNode(
                    id=arn,
                    node_type="AWS::ControlTower::Baseline",
                    name=bl.get("baselineIdentifier", arn.split("/")[-1]),
                    provider="aws",
                    account_id=account_id,
                    region=region,
                    resource_type="AWS::ControlTower::Baseline",
                    state=bl.get("statusSummary", {}).get("status", "ACTIVE"),
                    properties={
                        "baseline_identifier": bl.get("baselineIdentifier", ""),
                        "baseline_version": bl.get("baselineVersion", ""),
                        "target_identifier": bl.get("targetIdentifier", ""),
                    },
                ))
    except ClientError as e:
        logger.debug("controltower:ListEnabledBaselines unavailable in %s: %s", region, e)

    logger.info(
        "Control Tower: %d nodes, %d edges in %s",
        len(nodes), len(edges), region,
    )
    return nodes, edges


# ==================================================================
# Plugin Registration
# ==================================================================


class BonaProviderControlTower:
    """Plugin class registered via entry_points."""

    @hookimpl
    def bona_reference_providers(self) -> dict[str, Callable]:
        """Register the Control Tower reference provider."""
        return {
            "control_tower": enrich_control_tower,
        }


# Module-level instance discovered by pluggy via entry_points
plugin = BonaProviderControlTower()
