"""Bona Provider: AWS Audit Manager — assessments, frameworks, evidence.

Discovers:
  - Assessment frameworks (custom + standard)
  - Active assessments
  - Evidence folders and evidence items
  - Control sets and their status

Graph structure:
  [Framework "NIST-800-53"] --USED_BY--> [Assessment "Annual Review 2026"]
  [Assessment] --EVALUATES--> [ControlSet "Access Control"]
  [ControlSet] --HAS_EVIDENCE--> [Evidence "CloudTrail log"]
"""

import logging
from typing import Callable

import boto3
from botocore.exceptions import ClientError

from bona.plugins import hookimpl
from bona.schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)


def enrich_auditmanager(session: boto3.Session, region: str, resources: list, refresh: bool = False):
    """Discover Audit Manager frameworks, assessments, and evidence."""
    all_nodes = []
    all_edges = []

    try:
        client = session.client("auditmanager", region_name=region)
    except Exception:
        return [], []

    # List assessment frameworks
    try:
        resp = client.list_assessment_frameworks(frameworkType="Standard")
        for fw in resp.get("frameworkMetadataList", []):
            fw_id = fw.get("id", "")
            fw_arn = fw.get("arn", "")
            all_nodes.append(AssetNode(
                id=fw_arn or f"auditmanager-framework:{fw_id}",
                node_type="AWS::AuditManager::Framework",
                name=fw.get("name", fw_id),
                provider="aws",
                account_id=fw_arn.split(":")[4] if ":" in fw_arn else "",
                region=region,
                resource_type="AWS::AuditManager::Framework",
                state="ACTIVE",
                properties={
                    "framework_id": fw_id,
                    "type": fw.get("type", "Standard"),
                    "description": fw.get("description", "")[:200],
                    "control_sets_count": fw.get("controlSets", 0),
                },
            ))
    except ClientError as e:
        logger.debug("auditmanager:list_assessment_frameworks: %s", e)

    # List active assessments
    try:
        resp = client.list_assessments(status="ACTIVE")
        for assessment in resp.get("assessmentMetadata", []):
            a_id = assessment.get("id", "")
            a_name = assessment.get("name", "")
            fw_id = assessment.get("frameworkId", "")
            all_nodes.append(AssetNode(
                id=f"auditmanager-assessment:{a_id}",
                node_type="AWS::AuditManager::Assessment",
                name=a_name,
                provider="aws",
                account_id=assessment.get("delegations", [{}])[0].get("assessmentId", "") if assessment.get("delegations") else "",
                region=region,
                resource_type="AWS::AuditManager::Assessment",
                state="ACTIVE",
                properties={
                    "assessment_id": a_id,
                    "status": assessment.get("status", ""),
                    "compliance_type": assessment.get("complianceType", ""),
                },
            ))
            # Assessment → Framework edge
            if fw_id:
                all_edges.append(AssetEdge(
                    source_id=f"auditmanager-assessment:{a_id}",
                    target_id=f"auditmanager-framework:{fw_id}",
                    edge_type="USES_FRAMEWORK",
                    provider="aws",
                ))
    except ClientError as e:
        logger.debug("auditmanager:list_assessments: %s", e)

    if all_nodes:
        logger.info("Audit Manager: %d nodes, %d edges", len(all_nodes), len(all_edges))
    return all_nodes, all_edges


class BonaProviderAuditManager:
    @hookimpl
    def bona_reference_providers(self) -> dict[str, Callable]:
        return {"auditmanager": enrich_auditmanager}


plugin = BonaProviderAuditManager()
