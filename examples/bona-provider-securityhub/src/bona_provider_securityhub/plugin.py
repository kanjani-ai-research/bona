"""Bona plugin provider for AWS Security Hub."""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from bona.plugins import hookimpl

logger = logging.getLogger(__name__)


def enrich_securityhub(
    session: Any,
    region: str,
    resources: dict[str, Any],
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Enrich the graph with AWS Security Hub findings, standards, and controls.

    Args:
        session: A boto3 session.
        region: The AWS region to query.
        resources: Existing resource nodes keyed by ARN or ID.
        refresh: Whether to force-refresh cached data.

    Returns:
        A tuple of (nodes, edges) to merge into the graph.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    client = session.client("securityhub", region_name=region)

    # --- Get active findings → SecurityFinding nodes + HAS_FINDING edges ---
    try:
        paginator = client.get_paginator("get_findings")
        page_iterator = paginator.paginate(
            Filters={
                "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
            },
            PaginationConfig={"MaxItems": 100, "PageSize": 100},
        )
        for page in page_iterator:
            for finding in page.get("Findings", []):
                finding_id = finding["Id"]
                node = {
                    "id": finding_id,
                    "label": "SecurityFinding",
                    "properties": {
                        "title": finding.get("Title", ""),
                        "severity": finding.get("Severity", {}).get("Label", ""),
                        "status": finding.get("Workflow", {}).get("Status", ""),
                        "product_arn": finding.get("ProductArn", ""),
                        "region": region,
                        "created_at": finding.get("CreatedAt", ""),
                        "updated_at": finding.get("UpdatedAt", ""),
                    },
                }
                nodes.append(node)

                # Link findings to affected resources
                for resource in finding.get("Resources", []):
                    resource_id = resource.get("Id", "")
                    if resource_id in resources:
                        edges.append({
                            "source": resource_id,
                            "target": finding_id,
                            "label": "HAS_FINDING",
                        })
    except ClientError as exc:
        logger.warning("securityhub:get_findings failed: %s", exc)

    # --- Describe standards → SecurityStandard nodes ---
    standards_map: dict[str, str] = {}
    try:
        paginator = client.get_paginator("describe_standards")
        page_iterator = paginator.paginate()
        for page in page_iterator:
            for standard in page.get("Standards", []):
                standard_arn = standard["StandardsArn"]
                standards_map[standard_arn] = standard_arn
                nodes.append({
                    "id": standard_arn,
                    "label": "SecurityStandard",
                    "properties": {
                        "name": standard.get("Name", ""),
                        "description": standard.get("Description", ""),
                        "enabled_by_default": standard.get("EnabledByDefault", False),
                        "region": region,
                    },
                })
    except ClientError as exc:
        logger.warning("securityhub:describe_standards failed: %s", exc)

    # --- Describe standards controls → StandardControl nodes + CONTAINS_CONTROL edges ---
    try:
        # List enabled standard subscriptions to get subscription ARNs
        subs_paginator = client.get_paginator("get_enabled_standards")
        subs_pages = subs_paginator.paginate()
        for subs_page in subs_pages:
            for subscription in subs_page.get("StandardsSubscriptions", []):
                subscription_arn = subscription["StandardsSubscriptionArn"]
                standard_arn = subscription.get("StandardsArn", "")

                ctrl_paginator = client.get_paginator(
                    "describe_standards_controls"
                )
                ctrl_pages = ctrl_paginator.paginate(
                    StandardsSubscriptionArn=subscription_arn
                )
                for ctrl_page in ctrl_pages:
                    for control in ctrl_page.get("Controls", []):
                        control_id = control["StandardsControlArn"]
                        nodes.append({
                            "id": control_id,
                            "label": "StandardControl",
                            "properties": {
                                "control_id": control.get("ControlId", ""),
                                "title": control.get("Title", ""),
                                "description": control.get("Description", ""),
                                "severity": control.get("SeverityRating", ""),
                                "status": control.get("ControlStatus", ""),
                                "region": region,
                            },
                        })
                        # Link control to its parent standard
                        if standard_arn in standards_map:
                            edges.append({
                                "source": standard_arn,
                                "target": control_id,
                                "label": "CONTAINS_CONTROL",
                            })
    except ClientError as exc:
        logger.warning("securityhub:describe_standards_controls failed: %s", exc)

    return nodes, edges


@hookimpl
def bona_reference_providers() -> list[dict[str, Any]]:
    """Register the Security Hub enrichment provider with bona."""
    return [
        {
            "name": "securityhub",
            "fn": enrich_securityhub,
            "description": "AWS Security Hub findings, standards, and controls",
        }
    ]
