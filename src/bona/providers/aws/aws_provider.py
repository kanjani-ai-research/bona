"""AWS Provider — discovers infrastructure via AWS Config Advanced Query.

Primary method: select_resource_config (SQL query)
  - SELECT * WHERE resourceType = 'AWS::EC2::Instance'
  - Returns full configuration, relationships, tags, ARN in one call
  - Paginated via NextToken, Limit (max 100 per page)

Fallback: list_discovered_resources + batch_get_resource_config
"""

import fnmatch
import json
import logging
import time
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from ...schema.model import AssetNode, AssetEdge, DiscoveryResult

logger = logging.getLogger(__name__)

# AWS Config relationship name → edge type mapping
_RELATIONSHIP_MAP = {
    "Is contained in": "IS_CONTAINED_IN",
    "Contains": "CONTAINS",
    "Is attached to": "IS_ATTACHED_TO",
    "Is associated with": "IS_ASSOCIATED_WITH",
    "References": "REFERENCES",
}

# Inferred edge rules: config property → (edge_type, arn_hint)
_INFERRED_EDGES = {
    "vpcId": "IN_VPC",
    "subnetId": "IN_SUBNET",
    "iamInstanceProfile": "USES_ROLE",
    "role": "USES_ROLE",
    "roleArn": "USES_ROLE",
    "executionRoleArn": "USES_ROLE",
    "taskRoleArn": "USES_ROLE",
    "kmsKeyId": "ENCRYPTED_BY",
    "kmsMasterKeyId": "ENCRYPTED_BY",
    "targetGroupArn": "ROUTES_TO",
    "loadBalancerArn": "ATTACHED_TO",
    "clusterArn": "IN_CLUSTER",
    "dbClusterIdentifier": "IN_CLUSTER",
}

# Throttle: AWS Config advanced query limit is 100 results per page
_PAGE_LIMIT = 100
_THROTTLE_DELAY = 0.2


class AWSProvider:
    """AWS Config asset discovery via advanced SQL queries.

    Usage:
        provider = AWSProvider(profile="nw", region="us-east-1")
        result = provider.discover()
    """

    def __init__(
        self,
        profile: str = "",
        region: str = "us-east-1",
        account_id: str = "",
        max_resources: int = 10000,
        resource_types: list[str] | None = None,
        include_compliance: bool = False,
        aggregator: str = "",
    ):
        self.region = region
        self.max_resources = max_resources
        self.resource_type_filter = resource_types
        self.include_compliance = include_compliance
        self.aggregator = aggregator
        self.refresh_cache = False
        self.validate_schemas = False
        self.enrichment_config = None  # Set from BonaConfig
        self.learn_mode = False  # Update graph schema from discoveries
        self._graph_schema_path = ""  # Local path override
        self._graph_schema_s3_uri = ""  # S3 URI for upload
        self._profile = profile  # Store for S3 uploads

        session_kwargs = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile
        self._session = boto3.Session(**session_kwargs)
        self._config = self._session.client("config", region_name=region)

        if account_id:
            self.account_id = account_id
        else:
            sts = self._session.client("sts", region_name=region)
            self.account_id = sts.get_caller_identity()["Account"]

    def discover(self) -> DiscoveryResult:
        """Full discovery: enumerate types → query each → extract nodes + edges."""
        t0 = time.time()

        # 1. Get resource types tracked by Config
        resource_types = self._get_resource_types()
        logger.info("Discovered %d resource types in Config", len(resource_types))

        # 2. Query each type via select_resource_config
        all_nodes = []
        all_edges = []

        for rtype in resource_types:
            if len(all_nodes) >= self.max_resources:
                logger.info("Hit max_resources cap (%d), stopping", self.max_resources)
                break

            nodes, edges = self._query_resource_type(rtype)
            all_nodes.extend(nodes)
            all_edges.extend(edges)

            if nodes:
                logger.info("  %s: %d resources, %d edges", rtype, len(nodes), len(edges))

        # 3. Infer additional edges from configuration properties
        inferred = self._infer_edges(all_nodes)
        all_edges.extend(inferred)

        # 4. Enrich: type schemas + supplemental data (unified, config-driven)
        if self.validate_schemas:
            from bona.enrichment.type_schemas import enrich_with_type_schemas
            all_nodes, ref_nodes, enrich_edges = enrich_with_type_schemas(
                all_nodes, self._session, self.region,
                refresh_cache=self.refresh_cache,
                config=self.enrichment_config,
            )
            all_nodes.extend(ref_nodes)
            all_edges.extend(enrich_edges)

        # 5. Optional compliance findings
        findings = []
        if self.include_compliance:
            findings = self._get_compliance_findings()

        # 6. Learn mode — update graph schema from discoveries
        if self.learn_mode:
            from bona.graph_schema import load_graph_schema, save_graph_schema, learn_from_discovery
            from bona.relationship_learner import (
                load_relationship_rules, save_relationship_rules,
                learn_relationships, apply_relationship_rules,
            )
            # Learn graph schema (node/edge types)
            schema_path = self._graph_schema_path or None
            graph_schema = load_graph_schema(schema_path)
            graph_schema = learn_from_discovery(graph_schema, all_nodes, all_edges)
            save_graph_schema(
                graph_schema,
                path=schema_path,
                s3_uri=self._graph_schema_s3_uri,
                profile=self._profile,
                region=self.region,
            )

            # Learn relationship rules (property → edge promotion)
            rules = load_relationship_rules()
            rules = learn_relationships(rules, all_nodes)
            learned_edges = apply_relationship_rules(rules, all_nodes)
            all_edges.extend(learned_edges)
            save_relationship_rules(rules)

        duration = int((time.time() - t0) * 1000)

        result = DiscoveryResult(
            provider="aws",
            account_id=self.account_id,
            region=self.region,
            resources=all_nodes,
            relationships=all_edges,
            findings=findings,
            duration_ms=duration,
        )
        logger.info("Discovery complete: %s", result.summary())
        return result

    # ------------------------------------------------------------------
    # Get resource types
    # ------------------------------------------------------------------

    def _get_resource_types(self) -> list[str]:
        """Enumerate resource types tracked by Config."""
        # If filter has no wildcards, use it directly
        if self.resource_type_filter:
            has_glob = any("*" in t or "?" in t for t in self.resource_type_filter)
            if not has_glob:
                return self.resource_type_filter
            # Has globs — need to enumerate all types first, then filter
            all_types = self._enumerate_types()
            filtered = []
            for t in all_types:
                for pattern in self.resource_type_filter:
                    if fnmatch.fnmatch(t, pattern):
                        filtered.append(t)
                        break
            return filtered if filtered else self.resource_type_filter

        # No filter — enumerate what Config has
        return self._enumerate_types()

    def _enumerate_types(self) -> list[str]:
        """Get all resource types from Config via SQL or fallback."""
        try:
            resp = self._config.select_resource_config(
                Expression="SELECT resourceType, COUNT(*) WHERE resourceType LIKE 'AWS%' GROUP BY resourceType",
                Limit=100,
            )
            types = []
            for result_str in resp.get("Results", []):
                try:
                    item = json.loads(result_str)
                    rt = item.get("resourceType", "")
                    if rt:
                        types.append(rt)
                except (json.JSONDecodeError, TypeError):
                    continue
            if types:
                return types
        except ClientError as e:
            logger.debug("SQL enumerate failed: %s", e)

        return self._COMMON_TYPES

    _COMMON_TYPES = [
        "AWS::EC2::Instance", "AWS::EC2::Volume", "AWS::EC2::SecurityGroup",
        "AWS::EC2::VPC", "AWS::EC2::Subnet", "AWS::EC2::NetworkInterface",
        "AWS::EC2::InternetGateway", "AWS::EC2::RouteTable", "AWS::EC2::NatGateway",
        "AWS::S3::Bucket", "AWS::IAM::Role", "AWS::IAM::Policy", "AWS::IAM::User",
        "AWS::RDS::DBInstance", "AWS::RDS::DBCluster",
        "AWS::Lambda::Function", "AWS::Lambda::LayerVersion",
        "AWS::ECS::Cluster", "AWS::ECS::Service", "AWS::ECS::TaskDefinition",
        "AWS::EKS::Cluster",
        "AWS::ElasticLoadBalancingV2::LoadBalancer", "AWS::ElasticLoadBalancingV2::TargetGroup",
        "AWS::CloudFormation::Stack",
        "AWS::SNS::Topic", "AWS::SQS::Queue",
        "AWS::DynamoDB::Table",
        "AWS::KMS::Key",
        "AWS::CloudTrail::Trail",
        "AWS::CloudWatch::Alarm",
        "AWS::Logs::LogGroup",
        "AWS::SecretsManager::Secret",
    ]

    # ------------------------------------------------------------------
    # Query a resource type via select_resource_config
    # ------------------------------------------------------------------

    def _query_resource_type(self, resource_type: str) -> tuple[list[AssetNode], list[AssetEdge]]:
        """Query all resources of a type using Config advanced query SQL.

        If SQL returns resources with empty configuration, backfills via
        batch_get_resource_config which always has the full data.
        """
        nodes = []
        edges = []
        empty_config_ids = []  # Track resources needing backfill

        sql = f"SELECT * WHERE resourceType = '{resource_type}'"

        try:
            next_token = None
            while True:
                if self.aggregator:
                    # Multi-account: use aggregate query
                    kwargs = {
                        "Expression": sql,
                        "ConfigurationAggregatorName": self.aggregator,
                        "Limit": _PAGE_LIMIT,
                    }
                    if next_token:
                        kwargs["NextToken"] = next_token
                    resp = self._config.select_aggregate_resource_config(**kwargs)
                else:
                    # Single account
                    kwargs = {"Expression": sql, "Limit": _PAGE_LIMIT}
                    if next_token:
                        kwargs["NextToken"] = next_token
                    resp = self._config.select_resource_config(**kwargs)

                for result_str in resp.get("Results", []):
                    try:
                        item = json.loads(result_str)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    # Check if configuration is empty (SQL lag for new types)
                    config = item.get("configuration", "")
                    if not config or config == "{}" or config == "null":
                        rid = item.get("resourceId", "")
                        if rid:
                            empty_config_ids.append(rid)
                            continue

                    node = self._item_to_node(item)
                    if node:
                        nodes.append(node)
                        if len(nodes) >= self.max_resources:
                            return nodes, edges

                    # Extract relationships
                    item_edges = self._extract_relationships(item)
                    edges.extend(item_edges)

                next_token = resp.get("NextToken")
                if not next_token:
                    break

                time.sleep(_THROTTLE_DELAY)

        except ClientError as e:
            # Fallback to list + batch_get if advanced query not available
            if "InvalidExpression" in str(e) or "AccessDenied" in str(e):
                logger.debug("Advanced query failed for %s, trying fallback: %s", resource_type, e)
                return self._fallback_query(resource_type)
            logger.warning("Query failed for %s: %s", resource_type, e)

        # Backfill: SQL returned empty configs, use batch_get for full data
        if empty_config_ids:
            logger.debug("Backfilling %d resources with empty config via batch_get: %s",
                         len(empty_config_ids), resource_type)
            backfill_nodes, backfill_edges = self._batch_get_resources(resource_type, empty_config_ids)
            nodes.extend(backfill_nodes)
            edges.extend(backfill_edges)

        return nodes, edges

    # ------------------------------------------------------------------
    # Convert query result item to AssetNode
    # ------------------------------------------------------------------

    def _item_to_node(self, item: dict) -> Optional[AssetNode]:
        """Convert a select_resource_config result row to AssetNode."""
        arn = item.get("arn", "")
        resource_type = item.get("resourceType", "")
        resource_id = item.get("resourceId", "")
        resource_name = item.get("resourceName", resource_id)
        account_id = item.get("accountId", self.account_id)
        region = item.get("awsRegion", self.region)

        if not resource_id:
            return None

        # ID is ARN if available, otherwise construct one
        node_id = arn or f"arn:aws:{resource_type.split('::')[1].lower() if '::' in resource_type else 'unknown'}:{region}:{account_id}:{resource_id}"

        # Parse configuration (JSON string or dict)
        config = item.get("configuration", {})
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except (json.JSONDecodeError, TypeError):
                config = {}

        # Flatten config to properties
        properties = self._flatten_config(config, max_depth=2)

        # Add supplementaryConfiguration (bucket policies, encryption, etc.)
        supp = item.get("supplementaryConfiguration", {})
        if isinstance(supp, str):
            try:
                supp = json.loads(supp)
            except (json.JSONDecodeError, TypeError):
                supp = {}
        if isinstance(supp, dict):
            for k, v in supp.items():
                if isinstance(v, str):
                    # Try to parse nested JSON in supplementary config
                    try:
                        parsed = json.loads(v)
                        if isinstance(parsed, dict):
                            for sk, sv in self._flatten_config(parsed, max_depth=1, prefix=f"supp.{k}.").items():
                                properties[sk] = sv
                        else:
                            properties[f"supp.{k}"] = parsed
                    except (json.JSONDecodeError, TypeError):
                        if len(v) < 1000:
                            properties[f"supp.{k}"] = v
                elif isinstance(v, (int, float, bool)):
                    properties[f"supp.{k}"] = v

        # Availability zone
        az = item.get("availabilityZone", "")
        if az and az != "Not Applicable":
            properties["availabilityZone"] = az

        # Resource creation time
        creation_time = item.get("resourceCreationTime", "")
        if creation_time:
            properties["resourceCreationTime"] = creation_time

        # Config MD5 hash (useful for quick drift detection without full comparison)
        config_md5 = item.get("configurationItemMD5Hash", "")
        if config_md5:
            properties["configurationItemMD5Hash"] = config_md5

        # State
        state = item.get("configurationItemStatus", "OK")
        if state == "OK":
            state = "ACTIVE"
        elif state == "ResourceDiscovered":
            state = "ACTIVE"
        elif state == "ResourceDeleted":
            state = "DELETED"
        elif state == "ResourceDeletedNotRecorded":
            state = "DELETED"

        # Tags — from top-level tags field OR from configuration.tags
        tags = {}
        raw_tags = item.get("tags", {})
        if isinstance(raw_tags, list):
            for t in raw_tags:
                if isinstance(t, dict):
                    tags[t.get("key", t.get("Key", ""))] = t.get("value", t.get("Value", ""))
        elif isinstance(raw_tags, dict):
            tags = raw_tags
        # Also check config.tags if top-level is empty
        if not tags and isinstance(config, dict):
            config_tags = config.get("tags", [])
            if isinstance(config_tags, list):
                for t in config_tags:
                    if isinstance(t, dict):
                        tags[t.get("key", t.get("Key", ""))] = t.get("value", t.get("Value", ""))
            elif isinstance(config_tags, dict):
                tags = config_tags

        capture_time = item.get("configurationItemCaptureTime", "")

        return AssetNode(
            id=node_id,
            node_type=resource_type,
            name=resource_name,
            provider="aws",
            account_id=account_id,
            region=region,
            resource_type=resource_type,
            state=state,
            tags=tags,
            properties=properties,
            configuration_capture_time=capture_time,
        )

    # ------------------------------------------------------------------
    # Extract relationships
    # ------------------------------------------------------------------

    def _extract_relationships(self, item: dict) -> list[AssetEdge]:
        """Extract relationships from a query result item."""
        edges = []
        source_arn = item.get("arn", "")
        if not source_arn:
            return edges

        relationships = item.get("relationships", [])
        if isinstance(relationships, str):
            try:
                relationships = json.loads(relationships)
            except (json.JSONDecodeError, TypeError):
                return edges

        for rel in relationships:
            if not isinstance(rel, dict):
                continue

            rel_name = rel.get("relationshipName", "")
            target_id = rel.get("resourceId", "")
            target_type = rel.get("resourceType", "")
            target_name = rel.get("resourceName", "")

            if not target_id:
                continue

            edge_type = _RELATIONSHIP_MAP.get(rel_name, rel_name.upper().replace(" ", "_"))

            # Best-effort target ARN
            if target_id.startswith("arn:"):
                target_arn = target_id
            elif target_type and "::" in target_type:
                svc = target_type.split("::")[1].lower()
                target_arn = f"arn:aws:{svc}:{self.region}:{self.account_id}:{target_id}"
            else:
                target_arn = target_id

            edges.append(AssetEdge(
                source_id=source_arn,
                target_id=target_arn,
                edge_type=edge_type,
                provider="aws",
                account_id=self.account_id,
                properties={
                    "target_resource_type": target_type,
                    "target_resource_name": target_name,
                },
            ))

        return edges

    # ------------------------------------------------------------------
    # Infer edges from properties
    # ------------------------------------------------------------------

    def _infer_edges(self, resources: list[AssetNode]) -> list[AssetEdge]:
        """Infer edges from resource configuration properties."""
        edges = []
        for resource in resources:
            for prop_key, edge_type in _INFERRED_EDGES.items():
                value = resource.properties.get(prop_key, "")
                if not value:
                    continue
                targets = value if isinstance(value, list) else [value]
                for target in targets:
                    if isinstance(target, str) and target:
                        edges.append(AssetEdge(
                            source_id=resource.id,
                            target_id=target,
                            edge_type=edge_type,
                            provider="aws",
                            account_id=resource.account_id,
                        ))
        return edges

    # ------------------------------------------------------------------
    # Compliance findings
    # ------------------------------------------------------------------

    def _get_compliance_findings(self) -> list[AssetNode]:
        """Get compliance findings — per-rule details with annotations."""
        findings = []

        # Method 1: Query AWS::Config::ResourceCompliance as a resource type
        # This gives compliance state as graph nodes with relationship edges
        try:
            sql = "SELECT * WHERE resourceType = 'AWS::Config::ResourceCompliance'"
            resp = self._config.select_resource_config(Expression=sql, Limit=_PAGE_LIMIT)
            for result_str in resp.get("Results", []):
                try:
                    item = json.loads(result_str)
                    config = item.get("configuration", "")
                    if isinstance(config, str):
                        config = json.loads(config)

                    compliance_type = config.get("complianceType", "")
                    if compliance_type == "NON_COMPLIANT":
                        target_id = config.get("targetResourceId", "")
                        target_type = config.get("targetResourceType", "")
                        rules = config.get("configRuleList", [])
                        rule_names = [r.get("configRuleName", "") for r in rules if r.get("complianceType") == "NON_COMPLIANT"]

                        findings.append(AssetNode(
                            id=f"compliance:{target_type}/{target_id}",
                            node_type="Finding",
                            name=f"NON_COMPLIANT: {target_type}/{target_id}",
                            provider="aws",
                            account_id=self.account_id,
                            region=self.region,
                            resource_type="AWS::Config::ResourceCompliance",
                            state="NON_COMPLIANT",
                            properties={
                                "target_resource_id": target_id,
                                "target_resource_type": target_type,
                                "compliance_type": "NON_COMPLIANT",
                                "failing_rules": rule_names,
                                "failing_rule_count": len(rule_names),
                            },
                        ))
                except (json.JSONDecodeError, TypeError):
                    continue
        except ClientError as e:
            logger.debug("ResourceCompliance query: %s", e)

        # Method 2: Fallback — describe_compliance_by_resource
        if not findings:
            try:
                resp = self._config.describe_compliance_by_resource(
                    ComplianceTypes=["NON_COMPLIANT"], Limit=100
                )
                for item in resp.get("ComplianceByResources", []):
                    resource_id = item.get("ResourceId", "")
                    resource_type = item.get("ResourceType", "")

                    # Get per-rule details
                    rule_details = []
                    try:
                        detail_resp = self._config.get_compliance_details_by_resource(
                            ResourceType=resource_type,
                            ResourceId=resource_id,
                            ComplianceTypes=["NON_COMPLIANT"],
                        )
                        for eval_result in detail_resp.get("EvaluationResults", []):
                            rule_details.append({
                                "rule": eval_result.get("EvaluationResultIdentifier", {}).get("EvaluationResultQualifier", {}).get("ConfigRuleName", ""),
                                "annotation": eval_result.get("Annotation", ""),
                                "result_recorded_time": eval_result.get("ResultRecordedTime", ""),
                            })
                    except ClientError:
                        pass

                    findings.append(AssetNode(
                        id=f"compliance:{resource_type}/{resource_id}",
                        node_type="Finding",
                        name=f"NON_COMPLIANT: {resource_type}/{resource_id}",
                        provider="aws",
                        account_id=self.account_id,
                        region=self.region,
                        resource_type="AWS::Config::ResourceCompliance",
                        state="NON_COMPLIANT",
                        properties={
                            "target_resource_id": resource_id,
                            "target_resource_type": resource_type,
                            "compliance_type": "NON_COMPLIANT",
                            "failing_rules": [d["rule"] for d in rule_details],
                            "failing_rule_count": len(rule_details),
                            "annotations": [d["annotation"] for d in rule_details if d["annotation"]],
                        },
                    ))
            except ClientError as e:
                logger.debug("Compliance fallback: %s", e)

        # Enrich findings with rule metadata (description, source, scope)
        if findings:
            findings = self._enrich_findings_with_rule_metadata(findings)

        return findings

    def _enrich_findings_with_rule_metadata(self, findings: list[AssetNode]) -> list[AssetNode]:
        """Add describe_config_rules metadata to Finding nodes."""
        # Collect all unique rule names across findings
        all_rules = set()
        for f in findings:
            for rule in f.properties.get("failing_rules", []):
                if rule:
                    all_rules.add(rule)

        if not all_rules:
            return findings

        # Fetch rule metadata in batches of 25 (API limit)
        rule_metadata = {}
        rule_list = list(all_rules)
        for i in range(0, len(rule_list), 25):
            batch = rule_list[i:i + 25]
            try:
                resp = self._config.describe_config_rules(ConfigRuleNames=batch)
                for rule in resp.get("ConfigRules", []):
                    name = rule.get("ConfigRuleName", "")
                    rule_metadata[name] = {
                        "description": rule.get("Description", ""),
                        "source_owner": rule.get("Source", {}).get("Owner", ""),
                        "source_identifier": rule.get("Source", {}).get("SourceIdentifier", ""),
                        "scope_resource_types": rule.get("Scope", {}).get("ComplianceResourceTypes", []),
                        "input_parameters": rule.get("InputParameters", ""),
                        "rule_arn": rule.get("ConfigRuleArn", ""),
                        "rule_state": rule.get("ConfigRuleState", ""),
                    }
            except ClientError as e:
                logger.debug("describe_config_rules failed: %s", e)

        # Attach metadata to findings
        if rule_metadata:
            for f in findings:
                rules = f.properties.get("failing_rules", [])
                rule_details = []
                for rule_name in rules:
                    meta = rule_metadata.get(rule_name, {})
                    if meta:
                        rule_details.append({
                            "rule_name": rule_name,
                            "description": meta["description"],
                            "source": f"{meta['source_owner']}:{meta['source_identifier']}",
                            "scope": meta["scope_resource_types"],
                        })
                if rule_details:
                    f.properties["rule_details"] = rule_details

        return findings

    # ------------------------------------------------------------------
    # Fallback: list + batch_get (for accounts without advanced query)
    # ------------------------------------------------------------------

    def _fallback_query(self, resource_type: str) -> tuple[list[AssetNode], list[AssetEdge]]:
        """Fallback when select_resource_config is not available."""
        nodes = []
        edges = []
        try:
            paginator = self._config.get_paginator("list_discovered_resources")
            identifiers = []
            for page in paginator.paginate(resourceType=resource_type):
                for r in page.get("resourceIdentifiers", []):
                    identifiers.append({"resourceType": r["resourceType"], "resourceId": r["resourceId"]})
                    if len(identifiers) >= 100:
                        break
                if len(identifiers) >= 100:
                    break

            if identifiers:
                resp = self._config.batch_get_resource_config(resourceKeys=identifiers)
                for item in resp.get("baseConfigurationItems", []):
                    node = self._item_to_node(item)
                    if node:
                        nodes.append(node)
                    item_edges = self._extract_relationships(item)
                    edges.extend(item_edges)
        except ClientError as e:
            logger.debug("Fallback query failed for %s: %s", resource_type, e)
        return nodes, edges

    def _batch_get_resources(self, resource_type: str, resource_ids: list[str]) -> tuple[list[AssetNode], list[AssetEdge]]:
        """Fetch full config for specific resource IDs via batch_get_resource_config.

        Used to backfill resources where SQL returned empty configuration.
        Processes in batches of 100 (API limit).
        """
        nodes = []
        edges = []

        # batch_get_resource_config accepts max 100 keys per call
        for i in range(0, len(resource_ids), 100):
            batch = resource_ids[i:i + 100]
            keys = [{"resourceType": resource_type, "resourceId": rid} for rid in batch]

            try:
                resp = self._config.batch_get_resource_config(resourceKeys=keys)
                for item in resp.get("baseConfigurationItems", []):
                    node = self._item_to_node(item)
                    if node:
                        nodes.append(node)
                    item_edges = self._extract_relationships(item)
                    edges.extend(item_edges)
            except ClientError as e:
                logger.warning("batch_get_resource_config failed for %s: %s", resource_type, e)

            if i + 100 < len(resource_ids):
                time.sleep(_THROTTLE_DELAY)

        if nodes:
            logger.info("  Backfilled %s: %d resources, %d edges via batch_get",
                        resource_type, len(nodes), len(edges))
        return nodes, edges

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _flatten_config(self, config: dict, max_depth: int = 2, prefix: str = "") -> dict:
        """Flatten nested config to top-level keys."""
        flat = {}
        if not isinstance(config, dict):
            return flat
        for k, v in config.items():
            key = f"{prefix}{k}" if prefix else k
            if v is None:
                continue
            elif isinstance(v, (str, int, float, bool)):
                flat[key] = v
            elif isinstance(v, list) and len(v) <= 20:
                if all(isinstance(i, (str, int, float)) for i in v):
                    flat[key] = v
                elif len(v) <= 5:
                    flat[key] = v
                else:
                    flat[f"{key}_count"] = len(v)
            elif isinstance(v, list):
                flat[f"{key}_count"] = len(v)
            elif isinstance(v, dict) and max_depth > 0:
                nested = self._flatten_config(v, max_depth - 1, prefix=f"{key}.")
                flat.update(nested)
        return flat
