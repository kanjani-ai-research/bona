"""Built-in query database — security, compliance, and best-practice checks.

Each query is a function that takes (nodes, edges) and returns findings.
Queries are organized by tag: security, compliance, cost, best-practice.
"""

import json
import re
from typing import Any

from ..schema.model import AssetNode, AssetEdge
from .engine import Query, Finding


def _nodes_of_type(nodes: list[AssetNode], *types: str) -> list[AssetNode]:
    """Filter nodes by resource type."""
    return [n for n in nodes if n.resource_type in types]


def _prop(node: AssetNode, key: str, default: Any = None) -> Any:
    """Get a property from a node (case-insensitive search)."""
    # Exact match
    if key in node.properties:
        return node.properties[key]
    # Case-insensitive
    key_lower = key.lower()
    for k, v in node.properties.items():
        if k.lower() == key_lower:
            return v
    return default


# ==================================================================
# IAM Queries
# ==================================================================


def _iam_overpermissive_role(nodes, edges):
    """Detect IAM roles with Action: * or Resource: *."""
    findings = []
    for node in _nodes_of_type(nodes, "AWS::IAM::Role"):
        # Check inline policies or assume role policy
        policy_str = json.dumps(node.properties, default=str)
        if '"Action": "*"' in policy_str or '"Action":"*"' in policy_str:
            findings.append(Finding(
                query_name="iam-overpermissive-role",
                score=9,
                title="IAM role with Action: *",
                resource_id=node.id,
                resource_type=node.resource_type,
                resource_name=node.name,
                region=node.region,
                account_id=node.account_id,
                detail="Role has a policy with Action: * granting full access",
                remediation="Apply least-privilege: restrict Action to specific API calls",
            ))
        if '"Resource": "*"' in policy_str or '"Resource":"*"' in policy_str:
            if '"Action": "*"' not in policy_str:  # avoid double-reporting
                findings.append(Finding(
                    query_name="iam-overpermissive-role",
                    score=7,
                    title="IAM role with Resource: *",
                    resource_id=node.id,
                    resource_type=node.resource_type,
                    resource_name=node.name,
                    region=node.region,
                    account_id=node.account_id,
                    detail="Role has a policy with Resource: * (applies to all resources)",
                    remediation="Scope Resource to specific ARNs",
                ))
    return findings


def _iam_unused_role(nodes, edges):
    """Detect IAM roles not referenced by any other resource."""
    role_nodes = _nodes_of_type(nodes, "AWS::IAM::Role")
    # Find roles that are targets of USES_ROLE or ASSUMES_ROLE edges
    used_role_ids = {e.target_id for e in edges if e.edge_type in ("USES_ROLE", "ASSUMES_ROLE")}
    findings = []
    for node in role_nodes:
        if node.id not in used_role_ids:
            # Skip service-linked roles
            if "aws-service-role" in node.id:
                continue
            findings.append(Finding(
                query_name="iam-unused-role",
                score=3,
                title="IAM role not referenced by any resource",
                resource_id=node.id,
                resource_type=node.resource_type,
                resource_name=node.name,
                region=node.region,
                account_id=node.account_id,
                detail="No discovered resource references this role",
                remediation="Review and remove if unused, or check if Config is tracking the consumer",
            ))
    return findings


# ==================================================================
# S3 Queries
# ==================================================================


def _s3_public_bucket(nodes, edges):
    """Detect S3 buckets with public access."""
    findings = []
    for node in _nodes_of_type(nodes, "AWS::S3::Bucket"):
        props = node.properties
        # Check for public access block disabled
        public_block = _prop(node, "supp.PublicAccessBlockConfiguration")
        if public_block and isinstance(public_block, dict):
            if not all([
                public_block.get("blockPublicAcls", False),
                public_block.get("blockPublicPolicy", False),
                public_block.get("ignorePublicAcls", False),
                public_block.get("restrictPublicBuckets", False),
            ]):
                findings.append(Finding(
                    query_name="s3-public-bucket",
                    score=9,
                    title="S3 bucket without full public access block",
                    resource_id=node.id,
                    resource_type=node.resource_type,
                    resource_name=node.name,
                    region=node.region,
                    account_id=node.account_id,
                    detail="Public access block is not fully enabled",
                    remediation="Enable all 4 public access block settings",
                ))
        # No public access block at all
        elif not public_block:
            # Check if any public access indicator exists
            acl = _prop(node, "supp.AccessControlList")
            if acl and "AllUsers" in str(acl):
                findings.append(Finding(
                    query_name="s3-public-bucket",
                    score=10,
                    title="S3 bucket with public ACL",
                    resource_id=node.id,
                    resource_type=node.resource_type,
                    resource_name=node.name,
                    region=node.region,
                    account_id=node.account_id,
                    detail="Bucket ACL grants access to AllUsers",
                    remediation="Remove public ACL grants and enable public access block",
                ))
    return findings


def _s3_no_encryption(nodes, edges):
    """Detect S3 buckets without default encryption."""
    findings = []
    for node in _nodes_of_type(nodes, "AWS::S3::Bucket"):
        encryption = _prop(node, "supp.ServerSideEncryptionConfiguration")
        if not encryption:
            findings.append(Finding(
                query_name="s3-no-encryption",
                score=6,
                title="S3 bucket without default encryption configured",
                resource_id=node.id,
                resource_type=node.resource_type,
                resource_name=node.name,
                region=node.region,
                account_id=node.account_id,
                detail="No ServerSideEncryptionConfiguration found",
                remediation="Enable default encryption (AES-256 or KMS)",
            ))
    return findings


def _s3_no_versioning(nodes, edges):
    """Detect S3 buckets without versioning."""
    findings = []
    for node in _nodes_of_type(nodes, "AWS::S3::Bucket"):
        versioning = _prop(node, "supp.BucketVersioningConfiguration")
        if not versioning or (isinstance(versioning, dict) and versioning.get("status") != "Enabled"):
            findings.append(Finding(
                query_name="s3-no-versioning",
                score=4,
                title="S3 bucket without versioning enabled",
                resource_id=node.id,
                resource_type=node.resource_type,
                resource_name=node.name,
                region=node.region,
                account_id=node.account_id,
                detail="Versioning is not enabled — no protection against accidental deletion",
                remediation="Enable versioning on the bucket",
            ))
    return findings


# ==================================================================
# EC2 / Network Queries
# ==================================================================


def _ec2_unencrypted_volume(nodes, edges):
    """Detect EBS volumes without encryption."""
    findings = []
    for node in _nodes_of_type(nodes, "AWS::EC2::Volume"):
        encrypted = _prop(node, "encrypted", False)
        if not encrypted:
            findings.append(Finding(
                query_name="ec2-unencrypted-volume",
                score=7,
                title="EBS volume without encryption",
                resource_id=node.id,
                resource_type=node.resource_type,
                resource_name=node.name,
                region=node.region,
                account_id=node.account_id,
                detail="Volume is not encrypted at rest",
                remediation="Enable encryption on the volume or set account-level default encryption",
            ))
    return findings


def _ec2_public_ip(nodes, edges):
    """Detect EC2 instances with public IP addresses."""
    findings = []
    for node in _nodes_of_type(nodes, "AWS::EC2::Instance"):
        public_ip = _prop(node, "publicIpAddress") or _prop(node, "publicIp")
        if public_ip:
            findings.append(Finding(
                query_name="ec2-public-ip",
                score=5,
                title="EC2 instance with public IP",
                resource_id=node.id,
                resource_type=node.resource_type,
                resource_name=node.name,
                region=node.region,
                account_id=node.account_id,
                detail=f"Instance has public IP: {public_ip}",
                remediation="Use private subnets with NAT gateway, or restrict via security groups",
            ))
    return findings


def _sg_open_to_world(nodes, edges):
    """Detect security groups with 0.0.0.0/0 ingress on sensitive ports."""
    SENSITIVE_PORTS = {22, 3389, 3306, 5432, 1433, 6379, 27017, 9200}
    findings = []
    for node in _nodes_of_type(nodes, "AWS::EC2::SecurityGroup"):
        # Check ingress rules
        ip_permissions = _prop(node, "ipPermissions", [])
        if not isinstance(ip_permissions, list):
            continue
        for rule in ip_permissions:
            if not isinstance(rule, dict):
                continue
            # Check for 0.0.0.0/0 or ::/0
            ip_ranges = rule.get("ipRanges", [])
            ipv6_ranges = rule.get("ipv6Ranges", [])
            open_cidrs = [r for r in ip_ranges if isinstance(r, dict) and r.get("cidrIp") == "0.0.0.0/0"]
            open_ipv6 = [r for r in ipv6_ranges if isinstance(r, dict) and r.get("cidrIpv6") == "::/0"]

            if not open_cidrs and not open_ipv6:
                continue

            from_port = rule.get("fromPort", 0)
            to_port = rule.get("toPort", 65535)

            # Check if sensitive ports are in range
            exposed_ports = [p for p in SENSITIVE_PORTS if from_port <= p <= to_port]
            if exposed_ports:
                findings.append(Finding(
                    query_name="sg-open-to-world",
                    score=9,
                    title=f"Security group open to 0.0.0.0/0 on port(s) {exposed_ports}",
                    resource_id=node.id,
                    resource_type=node.resource_type,
                    resource_name=node.name,
                    region=node.region,
                    account_id=node.account_id,
                    detail=f"Ingress rule allows 0.0.0.0/0 on ports {from_port}-{to_port}",
                    remediation="Restrict source CIDR to specific IPs or use a bastion/VPN",
                ))
            elif from_port == 0 and to_port == 65535:
                findings.append(Finding(
                    query_name="sg-open-all-ports",
                    score=10,
                    title="Security group open to 0.0.0.0/0 on ALL ports",
                    resource_id=node.id,
                    resource_type=node.resource_type,
                    resource_name=node.name,
                    region=node.region,
                    account_id=node.account_id,
                    detail="Ingress rule allows all traffic from the internet",
                    remediation="Never allow 0.0.0.0/0 on all ports. Restrict to specific ports and sources.",
                ))
    return findings


def _subnet_public_auto_assign(nodes, edges):
    """Detect subnets that auto-assign public IPs."""
    findings = []
    for node in _nodes_of_type(nodes, "AWS::EC2::Subnet"):
        auto_assign = _prop(node, "mapPublicIpOnLaunch", False)
        if auto_assign:
            findings.append(Finding(
                query_name="subnet-public-auto-assign",
                score=4,
                title="Subnet auto-assigns public IPs",
                resource_id=node.id,
                resource_type=node.resource_type,
                resource_name=node.name,
                region=node.region,
                account_id=node.account_id,
                detail="mapPublicIpOnLaunch is true — instances get public IPs by default",
                remediation="Disable auto-assign unless this is intentionally a public subnet",
            ))
    return findings


# ==================================================================
# KMS / Encryption Queries
# ==================================================================


def _kms_key_rotation_disabled(nodes, edges):
    """Detect KMS keys without automatic rotation."""
    findings = []
    for node in _nodes_of_type(nodes, "AWS::KMS::Key"):
        rotation = _prop(node, "keyRotationEnabled", _prop(node, "enableKeyRotation", None))
        if rotation is False:
            findings.append(Finding(
                query_name="kms-no-rotation",
                score=5,
                title="KMS key without automatic rotation",
                resource_id=node.id,
                resource_type=node.resource_type,
                resource_name=node.name,
                region=node.region,
                account_id=node.account_id,
                detail="Key rotation is not enabled",
                remediation="Enable automatic key rotation (annual)",
            ))
    return findings


# ==================================================================
# Bedrock Queries
# ==================================================================


def _bedrock_no_guardrail(nodes, edges):
    """Detect Bedrock agents without a guardrail association."""
    findings = []
    agent_nodes = _nodes_of_type(nodes, "AWS::Bedrock::Agent")
    guardrail_nodes = _nodes_of_type(nodes, "AWS::Bedrock::Guardrail")

    if not guardrail_nodes:
        # No guardrails at all
        for node in agent_nodes:
            findings.append(Finding(
                query_name="bedrock-no-guardrail",
                score=7,
                title="Bedrock agent without guardrail protection",
                resource_id=node.id,
                resource_type=node.resource_type,
                resource_name=node.name,
                region=node.region,
                account_id=node.account_id,
                detail="No guardrails configured in this account/region",
                remediation="Create and associate a Bedrock Guardrail to control model output",
            ))
    return findings


def _bedrock_legacy_model(nodes, edges):
    """Detect usage of legacy/deprecated Bedrock models."""
    findings = []
    # Find USES_MODEL edges pointing to LEGACY models
    legacy_models = {n.id for n in nodes
                     if n.node_type == "AWS::Bedrock::FoundationModel"
                     and n.state == "LEGACY"}

    for edge in edges:
        if edge.edge_type == "USES_MODEL" and edge.target_id in legacy_models:
            # Find source node
            source = next((n for n in nodes if n.id == edge.source_id), None)
            if source:
                findings.append(Finding(
                    query_name="bedrock-legacy-model",
                    score=5,
                    title="Resource using a legacy/deprecated Bedrock model",
                    resource_id=source.id,
                    resource_type=source.resource_type,
                    resource_name=source.name,
                    region=source.region,
                    account_id=source.account_id,
                    detail=f"Uses model {edge.target_id} which has LEGACY status",
                    remediation="Migrate to the latest model version before end-of-life",
                ))
    return findings


# ==================================================================
# Query Database — all built-in queries
# ==================================================================


BUILTIN_QUERIES: list[Query] = [
    # IAM
    Query(
        name="iam-overpermissive-role",
        score=9,
        title="IAM role with overly permissive policy",
        description="Detects IAM roles with Action: * or Resource: * in their policies",
        tags=["security", "iam", "compliance"],
        remediation="Apply least-privilege: restrict to specific actions and resources",
        traversal=_iam_overpermissive_role,
    ),
    Query(
        name="iam-unused-role",
        score=3,
        title="IAM role not referenced by any resource",
        description="Finds roles that no discovered resource uses",
        tags=["best-practice", "iam", "hygiene"],
        remediation="Review and remove unused roles",
        traversal=_iam_unused_role,
    ),
    # S3
    Query(
        name="s3-public-bucket",
        score=9,
        title="S3 bucket with public access",
        description="Detects buckets without full public access block or with public ACLs",
        tags=["security", "s3", "compliance"],
        remediation="Enable all 4 public access block settings",
        traversal=_s3_public_bucket,
    ),
    Query(
        name="s3-no-encryption",
        score=6,
        title="S3 bucket without default encryption",
        description="Detects buckets without server-side encryption configured",
        tags=["security", "s3", "encryption"],
        remediation="Enable default encryption (AES-256 or KMS)",
        traversal=_s3_no_encryption,
    ),
    Query(
        name="s3-no-versioning",
        score=4,
        title="S3 bucket without versioning",
        description="Detects buckets without versioning enabled",
        tags=["best-practice", "s3", "data-protection"],
        remediation="Enable versioning for protection against accidental deletion",
        traversal=_s3_no_versioning,
    ),
    # EC2 / Network
    Query(
        name="ec2-unencrypted-volume",
        score=7,
        title="EBS volume without encryption",
        description="Detects EBS volumes that are not encrypted at rest",
        tags=["security", "ec2", "encryption"],
        remediation="Enable encryption or set account-level default encryption",
        traversal=_ec2_unencrypted_volume,
    ),
    Query(
        name="ec2-public-ip",
        score=5,
        title="EC2 instance with public IP",
        description="Detects instances directly exposed to the internet",
        tags=["security", "ec2", "network"],
        remediation="Use private subnets with NAT gateway",
        traversal=_ec2_public_ip,
    ),
    Query(
        name="sg-open-to-world",
        score=9,
        title="Security group open to 0.0.0.0/0 on sensitive ports",
        description="Detects security groups allowing internet access to SSH, RDP, databases, etc.",
        tags=["security", "network", "compliance"],
        remediation="Restrict source CIDR to specific IPs or use VPN/bastion",
        traversal=_sg_open_to_world,
    ),
    Query(
        name="subnet-public-auto-assign",
        score=4,
        title="Subnet auto-assigns public IPs",
        description="Detects subnets that give instances public IPs by default",
        tags=["best-practice", "network"],
        remediation="Disable auto-assign unless intentionally public",
        traversal=_subnet_public_auto_assign,
    ),
    # KMS
    Query(
        name="kms-no-rotation",
        score=5,
        title="KMS key without automatic rotation",
        description="Detects KMS keys that don't have automatic annual rotation enabled",
        tags=["security", "encryption", "compliance"],
        remediation="Enable automatic key rotation",
        traversal=_kms_key_rotation_disabled,
    ),
    # Bedrock
    Query(
        name="bedrock-no-guardrail",
        score=7,
        title="Bedrock agent without guardrail",
        description="Detects agents running without content safety guardrails",
        tags=["security", "bedrock", "ai-safety"],
        remediation="Associate a Bedrock Guardrail to control model output",
        traversal=_bedrock_no_guardrail,
    ),
    Query(
        name="bedrock-legacy-model",
        score=5,
        title="Resource using deprecated Bedrock model",
        description="Detects agents, prompts, or KBs using models with LEGACY lifecycle status",
        tags=["best-practice", "bedrock"],
        remediation="Migrate to the latest model version",
        traversal=_bedrock_legacy_model,
    ),
]
