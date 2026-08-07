"""Bona Inspector plugin — discovers vulnerability findings from Amazon Inspector.

Calls:
  - inspector2:ListFindings (filter: ACTIVE status)

Creates:
  - VulnerabilityFinding nodes with CVE, severity, package info
  - HAS_VULNERABILITY edges from affected resources to findings

Install:
    cd examples/bona-provider-inspector
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
# Inspector Reference Provider
# ==================================================================


def enrich_inspector_findings(session: boto3.Session, region: str, resources: list, refresh: bool = False):
    """Discover Inspector vulnerability findings and link them to affected resources.

    Returns (nodes, edges) where:
      - nodes: VulnerabilityFinding AssetNodes
      - edges: HAS_VULNERABILITY AssetEdges from resource → finding
    """
    client = session.client("inspector2", region_name=region)
    account_id = session.client("sts", region_name=region).get_caller_identity()["Account"]

    nodes: list[AssetNode] = []
    edges: list[AssetEdge] = []

    # Build a lookup of resource ARNs for edge matching
    resource_arn_map: dict[str, str] = {}
    for r in resources:
        resource_arn_map[r.id] = r.id
        # Index by short identifiers (instance ID, function name, etc.)
        short_id = r.id.split("/")[-1] if "/" in r.id else r.id.split(":")[-1]
        if short_id:
            resource_arn_map[short_id] = r.id

    # List ACTIVE findings with pagination
    try:
        next_token = None
        while True:
            kwargs = {
                "filterCriteria": {
                    "findingStatus": [
                        {"comparison": "EQUALS", "value": "ACTIVE"},
                    ],
                },
                "maxResults": 100,
            }
            if next_token:
                kwargs["nextToken"] = next_token

            resp = client.list_findings(**kwargs)

            for finding in resp.get("findings", []):
                finding_arn = finding.get("findingArn", "")
                finding_type = finding.get("type", "PACKAGE_VULNERABILITY")
                title = finding.get("title", "")
                description = finding.get("description", "")
                severity = finding.get("severity", "MEDIUM")
                status = finding.get("status", "ACTIVE")

                # Extract vulnerability details
                vuln_details = finding.get("packageVulnerabilityDetails", {})
                cve_id = ""
                cvss_score = 0.0
                vulnerable_packages = []

                if vuln_details:
                    # Get CVE IDs
                    vulnerability_id = vuln_details.get("vulnerabilityId", "")
                    cve_id = vulnerability_id  # Usually CVE-XXXX-YYYY

                    # Get CVSS score
                    cvss_list = vuln_details.get("cvss", [])
                    if cvss_list:
                        cvss_score = max(c.get("baseScore", 0.0) for c in cvss_list)

                    # Get vulnerable packages
                    for pkg in vuln_details.get("vulnerablePackages", []):
                        vulnerable_packages.append({
                            "name": pkg.get("name", ""),
                            "version": pkg.get("version", ""),
                            "epoch": pkg.get("epoch", 0),
                            "release": pkg.get("release", ""),
                            "arch": pkg.get("arch", ""),
                            "package_manager": pkg.get("packageManager", ""),
                            "fixed_in_version": pkg.get("fixedInVersion", ""),
                            "remediation": pkg.get("remediation", ""),
                        })

                # Extract affected resource info
                resource_info = finding.get("resources", [])
                affected_resource_id = ""
                affected_resource_type = ""
                if resource_info:
                    first_resource = resource_info[0]
                    affected_resource_id = first_resource.get("id", "")
                    affected_resource_type = first_resource.get("type", "")

                # Build properties
                properties = {
                    "finding_type": finding_type,
                    "severity": severity,
                    "cve_id": cve_id,
                    "cvss_score": cvss_score,
                    "description": description,
                    "affected_resource_type": affected_resource_type,
                    "affected_resource_id": affected_resource_id,
                    "status": status,
                    "first_observed_at": finding.get("firstObservedAt", ""),
                    "last_observed_at": finding.get("lastObservedAt", ""),
                    "inspector_score": finding.get("inspectorScore", 0.0),
                }

                # Add package info if present
                if vulnerable_packages:
                    properties["vulnerable_packages"] = vulnerable_packages
                    properties["package_name"] = vulnerable_packages[0].get("name", "")
                    properties["package_version"] = vulnerable_packages[0].get("version", "")
                    properties["fixed_in_version"] = vulnerable_packages[0].get("fixed_in_version", "")

                # Create VulnerabilityFinding node
                node = AssetNode(
                    id=finding_arn,
                    node_type="AWS::Inspector::VulnerabilityFinding",
                    name=title or cve_id or finding_arn.split("/")[-1],
                    provider="aws",
                    account_id=account_id,
                    region=region,
                    resource_type="AWS::Inspector::VulnerabilityFinding",
                    state="ACTIVE",
                    properties=properties,
                )
                nodes.append(node)

                # Create HAS_VULNERABILITY edge if we can match the affected resource
                if affected_resource_id:
                    # Try direct ARN match first, then short ID
                    source_arn = resource_arn_map.get(affected_resource_id)
                    if not source_arn:
                        # Try matching by short resource identifier
                        short = affected_resource_id.split("/")[-1] if "/" in affected_resource_id else affected_resource_id.split(":")[-1]
                        source_arn = resource_arn_map.get(short)

                    if source_arn:
                        edges.append(AssetEdge(
                            source_id=source_arn,
                            target_id=finding_arn,
                            edge_type="HAS_VULNERABILITY",
                            provider="aws",
                            account_id=account_id,
                            properties={
                                "severity": severity,
                                "cve_id": cve_id,
                                "cvss_score": cvss_score,
                            },
                        ))

            next_token = resp.get("nextToken")
            if not next_token:
                break
            # Cap total findings to prevent timeout
            if len(nodes) >= 1000:
                logger.info("Inspector: capped at 1000 findings")
                break

    except ClientError as e:
        logger.warning("Inspector list_findings failed: %s", e)
        return nodes, edges

    logger.info("Inspector: %d VulnerabilityFinding nodes, %d HAS_VULNERABILITY edges in %s",
                len(nodes), len(edges), region)
    return nodes, edges


# ==================================================================
# Plugin Registration
# ==================================================================


class BonaProviderInspector:
    """Plugin class registered via entry_points."""

    @hookimpl
    def bona_reference_providers(self) -> dict[str, Callable]:
        """Register the Inspector reference provider."""
        return {
            "inspector": enrich_inspector_findings,
        }


# Module-level instance discovered by pluggy via entry_points
plugin = BonaProviderInspector()
