"""Compliance Discovery — conformance packs, rules, and per-resource compliance.

Closes all gaps in AWS Config compliance APIs:
  - Conformance packs as graph nodes
  - Config rules as graph nodes
  - Compliance results as edges (PASSES/FAILS)
  - Compliance scores as properties
  - Multi-account aggregated compliance

Graph structure:
  [ConformancePack "NIST-800-53"] --CONTAINS_RULE--> [ConfigRule "s3-bucket-ssl"]
  [Resource "my-bucket"] --FAILS--> [ConfigRule "s3-bucket-ssl"]
  [Resource "my-bucket"] --EVALUATED_BY--> [ConformancePack "NIST-800-53"]
"""

import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from .schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)


def discover_compliance(
    session: boto3.Session,
    region: str,
    resources: list[AssetNode],
    aggregator: str = "",
) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Full compliance discovery — packs, rules, scores, per-resource results.

    Returns (compliance_nodes, compliance_edges) to merge into the graph.
    """
    config = session.client("config", region_name=region)
    all_nodes: list[AssetNode] = []
    all_edges: list[AssetEdge] = []

    # 1. Discover conformance packs
    pack_nodes, pack_rule_edges = _discover_conformance_packs(config)
    all_nodes.extend(pack_nodes)
    all_edges.extend(pack_rule_edges)

    # 2. Discover config rules
    rule_nodes = _discover_config_rules(config)
    all_nodes.extend(rule_nodes)

    # 3. Per-resource compliance (PASSES/FAILS edges)
    compliance_edges = _discover_resource_compliance(config, resources, rule_nodes)
    all_edges.extend(compliance_edges)

    # 4. Compliance summary by resource type
    summary_nodes = _discover_compliance_summaries(config)
    all_nodes.extend(summary_nodes)

    # 5. Aggregated compliance (if aggregator specified)
    if aggregator:
        agg_nodes, agg_edges = _discover_aggregate_compliance(config, aggregator)
        all_nodes.extend(agg_nodes)
        all_edges.extend(agg_edges)

    logger.info("Compliance discovery: %d nodes, %d edges (packs=%d, rules=%d)",
                len(all_nodes), len(all_edges), len(pack_nodes), len(rule_nodes))
    return all_nodes, all_edges


# ==================================================================
# 1. Conformance Packs
# ==================================================================


def _discover_conformance_packs(config) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Discover conformance packs + their compliance scores + CONTAINS_RULE edges."""
    nodes = []
    edges = []

    try:
        resp = config.describe_conformance_packs()
        packs = resp.get("ConformancePackDetails", [])
    except ClientError as e:
        logger.debug("describe_conformance_packs: %s", e)
        return [], []

    # Get compliance scores
    scores = {}
    try:
        score_resp = config.list_conformance_pack_compliance_scores()
        for s in score_resp.get("ConformancePackComplianceScores", []):
            scores[s["ConformancePackName"]] = s.get("Score", "")
    except ClientError:
        pass

    for pack in packs:
        pack_name = pack.get("ConformancePackName", "")
        pack_arn = pack.get("ConformancePackArn", "")

        nodes.append(AssetNode(
            id=pack_arn or f"conformance-pack:{pack_name}",
            node_type="AWS::Config::ConformancePack",
            name=pack_name,
            provider="aws",
            account_id=pack_arn.split(":")[4] if ":" in pack_arn else "",
            region=pack_arn.split(":")[3] if ":" in pack_arn else "",
            resource_type="AWS::Config::ConformancePack",
            state="ACTIVE",
            properties={
                "conformance_pack_id": pack.get("ConformancePackId", ""),
                "compliance_score": scores.get(pack_name, ""),
            },
        ))

        # Get rules in this pack + their compliance
        try:
            comp_resp = config.describe_conformance_pack_compliance(
                ConformancePackName=pack_name
            )
            for rule_comp in comp_resp.get("ConformancePackRuleComplianceList", []):
                rule_name = rule_comp.get("ConfigRuleName", "")
                compliance_type = rule_comp.get("ComplianceType", "")
                if rule_name:
                    edges.append(AssetEdge(
                        source_id=pack_arn or f"conformance-pack:{pack_name}",
                        target_id=f"config-rule:{rule_name}",
                        edge_type="CONTAINS_RULE",
                        provider="aws",
                        properties={"compliance_type": compliance_type},
                    ))
        except ClientError:
            pass

    logger.info("Conformance packs: %d packs, %d CONTAINS_RULE edges", len(nodes), len(edges))
    return nodes, edges


# ==================================================================
# 2. Config Rules
# ==================================================================


def _discover_config_rules(config) -> list[AssetNode]:
    """Discover all Config rules as graph nodes."""
    nodes = []

    try:
        paginator = config.get_paginator("describe_config_rules")
        for page in paginator.paginate():
            for rule in page.get("ConfigRules", []):
                rule_name = rule.get("ConfigRuleName", "")
                rule_arn = rule.get("ConfigRuleArn", "")
                source = rule.get("Source", {})

                nodes.append(AssetNode(
                    id=rule_arn or f"config-rule:{rule_name}",
                    node_type="AWS::Config::ConfigRule",
                    name=rule_name,
                    provider="aws",
                    account_id=rule_arn.split(":")[4] if ":" in rule_arn else "",
                    region=rule_arn.split(":")[3] if ":" in rule_arn else "",
                    resource_type="AWS::Config::ConfigRule",
                    state=rule.get("ConfigRuleState", "ACTIVE"),
                    properties={
                        "description": rule.get("Description", ""),
                        "source_owner": source.get("Owner", ""),
                        "source_identifier": source.get("SourceIdentifier", ""),
                        "scope_resource_types": rule.get("Scope", {}).get("ComplianceResourceTypes", []),
                        "input_parameters": rule.get("InputParameters", ""),
                    },
                ))
    except ClientError as e:
        logger.debug("describe_config_rules: %s", e)

    return nodes


# ==================================================================
# 3. Per-Resource Compliance
# ==================================================================


def _discover_resource_compliance(config, resources: list[AssetNode], rule_nodes: list[AssetNode]) -> list[AssetEdge]:
    """Create PASSES/FAILS edges between resources and config rules."""
    edges = []
    rule_ids = {n.name: n.id for n in rule_nodes}

    # Get compliance by resource for each non-compliant resource
    try:
        resp = config.describe_compliance_by_resource(ComplianceTypes=["NON_COMPLIANT"])
        for item in resp.get("ComplianceByResources", []):
            resource_type = item.get("ResourceType", "")
            resource_id = item.get("ResourceId", "")

            # Find matching resource node
            source_node = next(
                (r for r in resources if resource_id in r.id or r.name == resource_id),
                None
            )
            if not source_node:
                continue

            # Get which rules this resource fails
            try:
                detail_resp = config.get_compliance_details_by_resource(
                    ResourceType=resource_type,
                    ResourceId=resource_id,
                    ComplianceTypes=["NON_COMPLIANT"],
                )
                for eval_result in detail_resp.get("EvaluationResults", []):
                    rule_name = (eval_result.get("EvaluationResultIdentifier", {})
                                 .get("EvaluationResultQualifier", {})
                                 .get("ConfigRuleName", ""))
                    if rule_name:
                        target_id = rule_ids.get(rule_name, f"config-rule:{rule_name}")
                        edges.append(AssetEdge(
                            source_id=source_node.id,
                            target_id=target_id,
                            edge_type="FAILS",
                            provider="aws",
                            account_id=source_node.account_id,
                            properties={
                                "annotation": eval_result.get("Annotation", ""),
                                "result_time": str(eval_result.get("ResultRecordedTime", "")),
                            },
                        ))
            except ClientError:
                pass
    except ClientError as e:
        logger.debug("describe_compliance_by_resource: %s", e)

    if edges:
        logger.info("Resource compliance: %d FAILS edges", len(edges))
    return edges


# ==================================================================
# 4. Compliance Summaries
# ==================================================================


def _discover_compliance_summaries(config) -> list[AssetNode]:
    """Get compliance summary by resource type — creates summary nodes."""
    nodes = []

    try:
        resp = config.get_compliance_summary_by_resource_type()
        for summary in resp.get("ComplianceSummariesByResourceType", []):
            resource_type = summary.get("ResourceType", "")
            compliance = summary.get("ComplianceSummary", {})
            compliant = compliance.get("CompliantResourceCount", {}).get("CappedCount", 0)
            non_compliant = compliance.get("NonCompliantResourceCount", {}).get("CappedCount", 0)
            total = compliant + non_compliant
            score = round((compliant / total * 100), 1) if total > 0 else 0

            nodes.append(AssetNode(
                id=f"compliance-summary:{resource_type}",
                node_type="AWS::Config::ComplianceSummary",
                name=f"{resource_type} Compliance",
                provider="aws",
                account_id="",
                region="",
                resource_type="AWS::Config::ComplianceSummary",
                state="ACTIVE",
                properties={
                    "resource_type": resource_type,
                    "compliant_count": compliant,
                    "non_compliant_count": non_compliant,
                    "total_count": total,
                    "compliance_score": score,
                },
            ))
    except ClientError as e:
        logger.debug("get_compliance_summary_by_resource_type: %s", e)

    return nodes


# ==================================================================
# 5. Aggregated Compliance (Multi-Account)
# ==================================================================


def _discover_aggregate_compliance(config, aggregator: str) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Get org-wide conformance pack compliance via aggregator."""
    nodes = []
    edges = []

    try:
        resp = config.get_aggregate_conformance_pack_compliance_summary(
            ConfigurationAggregatorName=aggregator
        )
        summary = resp.get("AggregateConformancePackComplianceSummary", {})
        for item in summary:
            group = item.get("GroupName", "")
            compliant = item.get("ComplianceSummary", {}).get("CompliantConformancePackCount", 0)
            non_compliant = item.get("ComplianceSummary", {}).get("NonCompliantConformancePackCount", 0)

            nodes.append(AssetNode(
                id=f"aggregate-compliance:{group}",
                node_type="AWS::Config::AggregateCompliance",
                name=f"Aggregate: {group}",
                provider="aws",
                resource_type="AWS::Config::AggregateCompliance",
                state="ACTIVE",
                properties={
                    "group_name": group,
                    "compliant_packs": compliant,
                    "non_compliant_packs": non_compliant,
                },
            ))
    except ClientError as e:
        logger.debug("get_aggregate_conformance_pack_compliance_summary: %s", e)

    return nodes, edges
