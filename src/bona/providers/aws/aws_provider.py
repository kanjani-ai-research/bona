"""AWS Provider — discovers infrastructure assets via AWS Config.

AWS Config is the "Joern for cloud":
  - Already tracks 400+ resource types
  - Already records configuration changes
  - Already has relationship data (resource → resource)
  - Already has compliance rules
  - Already aggregates across accounts

Bona just reads it and maps to our graph schema.
"""

import logging
import time
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from ..providers.provider_base import AssetProvider
from ..schema.model import AssetNode, AssetEdge, DiscoveryResult

logger = logging.getLogger(__name__)


class AWSProvider(AssetProvider):
    """AWS asset discovery via Config + Resource Explorer.

    Usage:
        provider = AWSProvider(account_id="123456789012", region="us-east-1")
        result = provider.discover()
        print(result.summary())
    """

    def __init__(self, account_id: str = "", region: str = "us-east-1",
                 session: Optional[boto3.Session] = None):
        super().__init__(account_id=account_id, region=region)
        self._session = session or boto3.Session(region_name=region)
        self._config = self._session.client("config", region_name=region)

    def discover(self) -> DiscoveryResult:
        """Full discovery: resources + relationships + compliance."""
        t0 = time.time()

        resources = self._discover_resources()
        relationships = self._discover_relationships(resources)
        findings = self._discover_compliance()

        duration = int((time.time() - t0) * 1000)

        result = DiscoveryResult(
            provider="aws",
            account_id=self.account_id,
            region=self.region,
            resources=resources,
            relationships=relationships,
            findings=findings,
            duration_ms=duration,
        )
        logger.info("AWS discovery: %s", result.summary())
        return result

    def discover_services(self) -> list[dict]:
        """Enumerate all AWS services via boto3."""
        services = self._session.get_available_services()
        results = []
        for svc_name in services:
            try:
                client = self._session.client(svc_name, region_name=self.region)
                operations = client.meta.service_model.operation_names
                results.append({
                    "service_name": svc_name,
                    "operation_count": len(operations),
                    "operations": operations,
                })
            except Exception:
                continue
        return results

    def discover_resources(self, resource_types: list[str] | None = None) -> DiscoveryResult:
        """Discover resources via AWS Config."""
        resources = self._discover_resources(resource_types)
        return DiscoveryResult(
            provider="aws",
            account_id=self.account_id,
            region=self.region,
            resources=resources,
        )

    def discover_relationships(self, resource_ids: list[str]) -> list[AssetEdge]:
        """Get relationships for specific resources from Config."""
        edges = []
        for rid in resource_ids:
            try:
                # Parse resource type from ARN or use get_resource_config_history
                resp = self._config.get_discovered_resource_counts()
                # Config relationships come via get_resource_config_history
                # or select_aggregate_resource_config
            except Exception:
                continue
        return edges

    def discover_compliance(self) -> DiscoveryResult:
        """Get compliance state from Config rules."""
        findings = self._discover_compliance()
        return DiscoveryResult(
            provider="aws",
            account_id=self.account_id,
            region=self.region,
            findings=findings,
        )

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _discover_resources(self, resource_types: list[str] | None = None) -> list[AssetNode]:
        """List all discovered resources from AWS Config."""
        resources = []

        try:
            paginator = self._config.get_paginator("list_discovered_resources")

            # If no specific types, get all types first
            if not resource_types:
                try:
                    counts = self._config.get_discovered_resource_counts(
                        groupByKey="RESOURCE_TYPE"
                    )
                    resource_types = [
                        g["GroupName"]
                        for g in counts.get("GroupedResourceCounts", [])
                    ]
                except ClientError:
                    # Fallback: common resource types
                    resource_types = [
                        "AWS::EC2::Instance", "AWS::EC2::Volume",
                        "AWS::EC2::SecurityGroup", "AWS::EC2::VPC",
                        "AWS::S3::Bucket", "AWS::IAM::Role",
                        "AWS::RDS::DBInstance", "AWS::Lambda::Function",
                    ]

            for rtype in resource_types:
                try:
                    for page in paginator.paginate(resourceType=rtype):
                        for r in page.get("resourceIdentifiers", []):
                            node = AssetNode(
                                id=r.get("resourceId", ""),
                                node_type="CSPResource",
                                provider="aws",
                                name=r.get("resourceName", r.get("resourceId", "")),
                                region=self.region,
                                account_id=self.account_id,
                                resource_type=rtype,
                                state="ACTIVE",
                                properties={
                                    "resource_type": rtype,
                                    "resource_id": r.get("resourceId", ""),
                                    "resource_name": r.get("resourceName", ""),
                                },
                            )
                            resources.append(node)
                except ClientError as e:
                    logger.debug("Skipped %s: %s", rtype, e)
                    continue

        except ClientError as e:
            logger.warning("Config discovery failed: %s", e)

        return resources

    def _discover_relationships(self, resources: list[AssetNode]) -> list[AssetEdge]:
        """Discover relationships between resources via Config."""
        edges = []

        for resource in resources[:100]:  # Limit to avoid throttling
            try:
                resp = self._config.get_resource_config_history(
                    resourceType=resource.resource_type,
                    resourceId=resource.id,
                    limit=1,
                )
                items = resp.get("configurationItems", [])
                if items:
                    item = items[0]
                    # Extract relationships from config item
                    for rel in item.get("relationships", []):
                        edge = AssetEdge(
                            source_id=resource.id,
                            target_id=rel.get("resourceId", ""),
                            edge_type=rel.get("relationshipName", "RELATED_TO"),
                            properties={
                                "resource_type": rel.get("resourceType", ""),
                                "resource_name": rel.get("resourceName", ""),
                            },
                        )
                        edges.append(edge)
            except ClientError:
                continue

        return edges

    def _discover_compliance(self) -> list[AssetNode]:
        """Get non-compliant resources from Config rules."""
        findings = []

        try:
            resp = self._config.describe_compliance_by_resource(
                ComplianceTypes=["NON_COMPLIANT"],
                Limit=100,
            )
            for item in resp.get("ComplianceByResources", []):
                finding = AssetNode(
                    id=f"finding-{item.get('ResourceId', '')}",
                    node_type="Finding",
                    provider="aws",
                    name=f"NON_COMPLIANT: {item.get('ResourceType', '')}",
                    resource_type=item.get("ResourceType", ""),
                    state="NON_COMPLIANT",
                    properties={
                        "resource_id": item.get("ResourceId", ""),
                        "resource_type": item.get("ResourceType", ""),
                        "compliance_type": "NON_COMPLIANT",
                    },
                )
                findings.append(finding)
        except ClientError as e:
            logger.debug("Compliance discovery: %s", e)

        return findings
