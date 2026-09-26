"""Bona GuardDuty plugin — discovers threat findings from Amazon GuardDuty.

Calls:
  - guardduty:ListDetectors
  - guardduty:ListFindings
  - guardduty:GetFindings

Creates:
  - ThreatFinding nodes with severity, type, resource affected
  - HAS_THREAT edges from affected resources to findings

Install:
    cd examples/bona-provider-guardduty
    pip install -e .
"""

import logging
from typing import Callable

import boto3
from botocore.exceptions import ClientError

from bona.plugins import hookimpl
from bona.schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)


# ==================================================================
# GuardDuty Reference Provider
# ==================================================================


def enrich_guardduty_findings(session: boto3.Session, region: str, resources: list, refresh: bool = False):
    """Discover GuardDuty threat findings and link them to affected resources.

    Returns (nodes, edges) where:
      - nodes: ThreatFinding AssetNodes
      - edges: HAS_THREAT AssetEdges from resource → finding
    """
    client = session.client("guardduty", region_name=region)
    account_id = session.client("sts", region_name=region).get_caller_identity()["Account"]

    nodes: list[AssetNode] = []
    edges: list[AssetEdge] = []

    # Step 1: List detectors
    try:
        detector_ids = client.list_detectors().get("DetectorIds", [])
    except ClientError as e:
        logger.warning("GuardDuty list_detectors failed: %s", e)
        return nodes, edges

    if not detector_ids:
        logger.info("GuardDuty: no detectors found in %s", region)
        return nodes, edges

    # Build a lookup of resource ARNs for edge matching
    resource_arn_map: dict[str, str] = {}
    for r in resources:
        # Index by ARN and short identifiers for flexible matching
        resource_arn_map[r.id] = r.id
        # Also index by instance ID, bucket name, etc. from the ARN
        short_id = r.id.split("/")[-1] if "/" in r.id else r.id.split(":")[-1]
        if short_id:
            resource_arn_map[short_id] = r.id

    for detector_id in detector_ids:
        # Step 2: List findings for this detector
        try:
            finding_ids: list[str] = []
            paginator_token = None
            while True:
                kwargs = {"DetectorId": detector_id, "MaxResults": 50}
                if paginator_token:
                    kwargs["NextToken"] = paginator_token
                resp = client.list_findings(**kwargs)
                finding_ids.extend(resp.get("FindingIds", []))
                paginator_token = resp.get("NextToken")
                if not paginator_token:
                    break
        except ClientError as e:
            logger.warning("GuardDuty list_findings failed for detector %s: %s", detector_id, e)
            continue

        if not finding_ids:
            continue

        # Step 3: Get finding details in batches of 50
        for i in range(0, len(finding_ids), 50):
            batch = finding_ids[i:i + 50]
            try:
                details_resp = client.get_findings(
                    DetectorId=detector_id,
                    FindingIds=batch,
                )
            except ClientError as e:
                logger.warning("GuardDuty get_findings failed: %s", e)
                continue

            for finding in details_resp.get("Findings", []):
                finding_id = finding.get("Id", "")
                finding_arn = finding.get("Arn", f"arn:aws:guardduty:{region}:{account_id}:finding/{finding_id}")
                finding_type = finding.get("Type", "Unknown")
                severity = finding.get("Severity", 0)
                title = finding.get("Title", "")
                description = finding.get("Description", "")

                # Map numeric severity to label
                if severity >= 7.0:
                    severity_label = "High"
                elif severity >= 4.0:
                    severity_label = "Medium"
                else:
                    severity_label = "Low"

                # Extract resource info from the finding
                resource_info = finding.get("Resource", {})
                resource_type = resource_info.get("ResourceType", "")
                affected_resource_id = ""

                # Try to identify the affected resource
                if resource_type == "Instance":
                    instance_details = resource_info.get("InstanceDetails", {})
                    instance_id = instance_details.get("InstanceId", "")
                    affected_resource_id = instance_id
                elif resource_type == "AccessKey":
                    access_key_details = resource_info.get("AccessKeyDetails", {})
                    affected_resource_id = access_key_details.get("PrincipalId", "")
                elif resource_type == "S3Bucket":
                    s3_details = resource_info.get("S3BucketDetails", [])
                    if s3_details:
                        affected_resource_id = s3_details[0].get("Name", "")
                elif resource_type == "EKSCluster":
                    eks_details = resource_info.get("EksClusterDetails", {})
                    affected_resource_id = eks_details.get("Name", "")
                elif resource_type == "Container":
                    container_details = resource_info.get("ContainerDetails", {})
                    affected_resource_id = container_details.get("Id", "")

                # Create ThreatFinding node
                node = AssetNode(
                    id=finding_arn,
                    node_type="AWS::GuardDuty::ThreatFinding",
                    name=title,
                    provider="aws",
                    account_id=account_id,
                    region=region,
                    resource_type="AWS::GuardDuty::ThreatFinding",
                    state="ACTIVE",
                    properties={
                        "finding_id": finding_id,
                        "finding_type": finding_type,
                        "severity": severity,
                        "severity_label": severity_label,
                        "description": description,
                        "affected_resource_type": resource_type,
                        "affected_resource_id": affected_resource_id,
                        "detector_id": detector_id,
                        "created_at": finding.get("CreatedAt", ""),
                        "updated_at": finding.get("UpdatedAt", ""),
                        "confidence": finding.get("Confidence", 0),
                    },
                )
                nodes.append(node)

                # Create HAS_THREAT edge if we can match the affected resource
                if affected_resource_id and affected_resource_id in resource_arn_map:
                    source_arn = resource_arn_map[affected_resource_id]
                    edges.append(AssetEdge(
                        source_id=source_arn,
                        target_id=finding_arn,
                        edge_type="HAS_THREAT",
                        provider="aws",
                        account_id=account_id,
                        properties={
                            "severity_label": severity_label,
                            "finding_type": finding_type,
                        },
                    ))

    logger.info("GuardDuty: %d ThreatFinding nodes, %d HAS_THREAT edges in %s",
                len(nodes), len(edges), region)
    return nodes, edges


# ==================================================================
# Plugin Registration
# ==================================================================


class BonaProviderGuardDuty:
    """Plugin class registered via entry_points."""

    @hookimpl
    def bona_reference_providers(self) -> dict[str, Callable]:
        """Register the GuardDuty reference provider."""
        return {
            "guardduty": enrich_guardduty_findings,
        }


# Module-level instance discovered by pluggy via entry_points
plugin = BonaProviderGuardDuty()
