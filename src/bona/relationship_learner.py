"""Relationship Learner — discovers edge inference rules from property values.

Scans node properties for ARNs, URIs, and IDs that reference other resources.
Learns patterns over time and promotes them to automatic edge creation rules.

Stored in: ~/.bona/relationship-rules.json
"""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path.home() / ".bona" / "relationship-rules.json"

# Minimum observations before a candidate becomes a rule
PROMOTION_THRESHOLD = 3

# Patterns that indicate a value is a reference to another resource
ARN_PATTERN = re.compile(r"^arn:aws[a-z\-]*:[a-z0-9\-]+:[a-z0-9\-]*:\d*:.+")
ECR_PATTERN = re.compile(r"^\d+\.dkr\.ecr\.[a-z0-9\-]+\.amazonaws\.com/.+")
S3_URI_PATTERN = re.compile(r"^s3://[a-z0-9\-\.]+/.+")
VPC_ID_PATTERN = re.compile(r"^vpc-[a-f0-9]+$")
SUBNET_ID_PATTERN = re.compile(r"^subnet-[a-f0-9]+$")
SG_ID_PATTERN = re.compile(r"^sg-[a-f0-9]+$")
ROLE_ARN_PATTERN = re.compile(r"^arn:aws:iam::\d+:role/.+")


@dataclass
class RelationshipCandidate:
    """A candidate relationship rule learned from property observations."""

    source_type: str
    property_name: str
    target_type: str  # inferred from value pattern
    edge_type: str  # inferred
    pattern: str  # regex pattern that matched
    observed_count: int = 0
    promoted: bool = False  # True = auto-create edges
    first_seen: str = ""
    last_seen: str = ""
    sample_values: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "property_name": self.property_name,
            "target_type": self.target_type,
            "edge_type": self.edge_type,
            "pattern": self.pattern,
            "observed_count": self.observed_count,
            "promoted": self.promoted,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "sample_values": self.sample_values[:5],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RelationshipCandidate":
        return cls(
            source_type=data.get("source_type", ""),
            property_name=data.get("property_name", ""),
            target_type=data.get("target_type", ""),
            edge_type=data.get("edge_type", ""),
            pattern=data.get("pattern", ""),
            observed_count=data.get("observed_count", 0),
            promoted=data.get("promoted", False),
            first_seen=data.get("first_seen", ""),
            last_seen=data.get("last_seen", ""),
            sample_values=data.get("sample_values", []),
        )


@dataclass
class RelationshipRules:
    """All learned relationship rules."""

    version: str = "0.1.0"
    last_updated: str = ""
    rules: dict[str, RelationshipCandidate] = field(default_factory=dict)

    def _key(self, source_type: str, property_name: str) -> str:
        return f"{source_type}|{property_name}"

    def get(self, source_type: str, property_name: str) -> Optional[RelationshipCandidate]:
        return self.rules.get(self._key(source_type, property_name))

    def promoted_rules(self) -> list[RelationshipCandidate]:
        return [r for r in self.rules.values() if r.promoted]

    def candidates(self) -> list[RelationshipCandidate]:
        return [r for r in self.rules.values() if not r.promoted]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "last_updated": self.last_updated,
            "rules": {k: v.to_dict() for k, v in sorted(self.rules.items())},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RelationshipRules":
        rules = {}
        for k, v in data.get("rules", {}).items():
            rules[k] = RelationshipCandidate.from_dict(v)
        return cls(
            version=data.get("version", "0.1.0"),
            last_updated=data.get("last_updated", ""),
            rules=rules,
        )


# ==================================================================
# Load / Save
# ==================================================================


def load_relationship_rules(path: Optional[str] = None) -> RelationshipRules:
    rules_path = Path(path) if path else DEFAULT_RULES_PATH
    if not rules_path.exists():
        return RelationshipRules()
    try:
        data = json.loads(rules_path.read_text())
        rules = RelationshipRules.from_dict(data)
        logger.info("Loaded relationship rules: %d rules (%d promoted)",
                    len(rules.rules), len(rules.promoted_rules()))
        return rules
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load relationship rules: %s", e)
        return RelationshipRules()


def save_relationship_rules(rules: RelationshipRules, path: Optional[str] = None) -> None:
    rules_path = Path(path) if path else DEFAULT_RULES_PATH
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules.last_updated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rules_path.write_text(json.dumps(rules.to_dict(), indent=2))
    logger.info("Saved relationship rules: %d rules (%d promoted)",
                len(rules.rules), len(rules.promoted_rules()))


# ==================================================================
# Learn — scan properties, detect references, record candidates
# ==================================================================


def learn_relationships(rules: RelationshipRules, nodes: list[AssetNode]) -> RelationshipRules:
    """Scan node properties for values that look like references to other resources."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    new_candidates = 0

    for node in nodes:
        for prop_name, value in node.properties.items():
            if not isinstance(value, str) or not value:
                continue
            if prop_name.startswith("_"):
                continue

            # Try to classify this value as a reference
            classification = _classify_value(value)
            if not classification:
                continue

            target_type, edge_type, pattern = classification
            key = rules._key(node.node_type, prop_name)

            if key not in rules.rules:
                rules.rules[key] = RelationshipCandidate(
                    source_type=node.node_type,
                    property_name=prop_name,
                    target_type=target_type,
                    edge_type=edge_type,
                    pattern=pattern,
                    first_seen=now,
                )
                new_candidates += 1

            candidate = rules.rules[key]
            candidate.observed_count += 1
            candidate.last_seen = now
            if value not in candidate.sample_values and len(candidate.sample_values) < 5:
                candidate.sample_values.append(value)

            # Promote if threshold reached
            if candidate.observed_count >= PROMOTION_THRESHOLD and not candidate.promoted:
                candidate.promoted = True
                logger.info("Promoted relationship: %s.%s → %s (%s) after %d observations",
                            candidate.source_type, candidate.property_name,
                            candidate.target_type, candidate.edge_type, candidate.observed_count)

    if new_candidates:
        logger.info("Learned %d new relationship candidates", new_candidates)

    return rules


# ==================================================================
# Apply — create edges from promoted rules
# ==================================================================


def apply_relationship_rules(rules: RelationshipRules, nodes: list[AssetNode]) -> list[AssetEdge]:
    """Create edges from promoted relationship rules."""
    edges = []
    promoted = rules.promoted_rules()
    if not promoted:
        return edges

    # Build lookup for target resolution
    node_lookup = {n.id: n for n in nodes}
    # Also index by short IDs for matching
    short_id_lookup: dict[str, str] = {}
    for n in nodes:
        # Extract short resource ID from ARN
        if "/" in n.id:
            short_id_lookup[n.id.split("/")[-1]] = n.id
        if ":" in n.id:
            short_id_lookup[n.id.split(":")[-1]] = n.id

    for node in nodes:
        for rule in promoted:
            if node.node_type != rule.source_type:
                continue
            value = node.properties.get(rule.property_name, "")
            if not value or not isinstance(value, str):
                continue

            # Resolve target
            target_id = _resolve_target(value, node_lookup, short_id_lookup)
            if target_id:
                edges.append(AssetEdge(
                    source_id=node.id,
                    target_id=target_id,
                    edge_type=rule.edge_type,
                    provider="aws",
                    account_id=node.account_id,
                    properties={"learned_from": rule.property_name},
                ))

    if edges:
        logger.info("Applied relationship rules: %d edges from %d promoted rules",
                    len(edges), len(promoted))
    return edges


# ==================================================================
# Helpers
# ==================================================================


def _classify_value(value: str) -> Optional[tuple[str, str, str]]:
    """Classify a property value as a reference. Returns (target_type, edge_type, pattern) or None."""

    if ROLE_ARN_PATTERN.match(value):
        return ("AWS::IAM::Role", "ASSUMES_ROLE", "arn:aws:iam::*:role/*")

    if ARN_PATTERN.match(value):
        # Parse ARN to determine target type
        parts = value.split(":")
        if len(parts) >= 6:
            service = parts[2]
            resource = parts[5].split("/")[0] if "/" in parts[5] else parts[5]
            target_type = _arn_to_type(service, resource)
            edge_type = _infer_edge_type(service, resource)
            return (target_type, edge_type, f"arn:aws:{service}:*")

    if ECR_PATTERN.match(value):
        return ("AWS::ECR::Image", "RUNS_CONTAINER", "*.dkr.ecr.*.amazonaws.com/*")

    if S3_URI_PATTERN.match(value):
        return ("AWS::S3::Object", "READS_FROM", "s3://*")

    return None


def _arn_to_type(service: str, resource: str) -> str:
    """Map ARN service+resource to a CloudFormation-style type."""
    mapping = {
        ("iam", "role"): "AWS::IAM::Role",
        ("iam", "policy"): "AWS::IAM::Policy",
        ("iam", "user"): "AWS::IAM::User",
        ("s3", ""): "AWS::S3::Bucket",
        ("ec2", "instance"): "AWS::EC2::Instance",
        ("ec2", "vpc"): "AWS::EC2::VPC",
        ("ec2", "subnet"): "AWS::EC2::Subnet",
        ("ec2", "security-group"): "AWS::EC2::SecurityGroup",
        ("lambda", "function"): "AWS::Lambda::Function",
        ("bedrock", "foundation-model"): "AWS::Bedrock::FoundationModel",
        ("bedrock", "guardrail"): "AWS::Bedrock::Guardrail",
        ("bedrock-agentcore", "runtime"): "AWS::Bedrock::AgentRuntime",
        ("bedrock-agentcore", "workload-identity-directory"): "AWS::Bedrock::WorkloadIdentity",
        ("kms", "key"): "AWS::KMS::Key",
        ("sns", ""): "AWS::SNS::Topic",
        ("sqs", ""): "AWS::SQS::Queue",
    }
    return mapping.get((service, resource), f"AWS::{service.title()}::{resource.title()}")


def _infer_edge_type(service: str, resource: str) -> str:
    """Infer an edge type from the ARN's service and resource."""
    mapping = {
        ("iam", "role"): "ASSUMES_ROLE",
        ("iam", "policy"): "HAS_POLICY",
        ("kms", "key"): "ENCRYPTED_BY",
        ("ec2", "vpc"): "IN_VPC",
        ("ec2", "subnet"): "IN_SUBNET",
        ("ec2", "security-group"): "PROTECTED_BY",
        ("bedrock", "foundation-model"): "USES_MODEL",
        ("bedrock-agentcore", "runtime"): "RUNS_ON",
        ("bedrock-agentcore", "workload-identity-directory"): "RUNS_AS",
        ("lambda", "function"): "INVOKES",
        ("s3", ""): "READS_FROM",
        ("sns", ""): "PUBLISHES_TO",
        ("sqs", ""): "READS_FROM",
    }
    return mapping.get((service, resource), "REFERENCES")


def _resolve_target(value: str, node_lookup: dict, short_id_lookup: dict) -> str:
    """Resolve a reference value to a known node ID."""
    # Direct match
    if value in node_lookup:
        return value

    # Try short ID from ARN
    if "/" in value:
        short = value.split("/")[-1]
        if short in short_id_lookup:
            return short_id_lookup[short]

    # For ARNs, use the full ARN as target (even if node not in current graph)
    if ARN_PATTERN.match(value):
        return value

    # ECR URI as-is
    if ECR_PATTERN.match(value):
        return value

    return ""
