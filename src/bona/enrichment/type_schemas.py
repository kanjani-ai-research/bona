"""Type Enrichment — unified enrichment for all resource types.

Downloads CloudFormation type schemas and supplemental data sources.
Emits TypeDefinition reference nodes + INSTANCE_OF edges.

Supplemental data sources (e.g. EC2 instance specs) are registered as
providers that attach extra properties to TypeDefinition nodes or create
sub-type reference nodes.

Soft inquiry: reads from ~/.bona/cache/
Hard inquiry: calls AWS APIs, saves to cache

Graph structure:
  [S3 Bucket instance] --INSTANCE_OF--> [TypeDefinition "AWS::S3::Bucket"]
  [EC2 Instance]       --INSTANCE_OF--> [TypeDefinition "AWS::EC2::Instance"]
  [EC2 Instance]       --RUNS_ON------> [InstanceSpec "t3.medium"]
"""

import json
import logging
from typing import Any, Callable, Optional

import boto3
from botocore.exceptions import ClientError

from ..cache import read_cache, write_cache
from ..schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)

CACHE_PREFIX = "type-schema"


def _cache_name(type_name: str) -> str:
    """Cache key for a given resource type."""
    safe = type_name.replace("::", "--")
    return f"{CACHE_PREFIX}-{safe}"


# ==================================================================
# Type Schema — CloudFormation describe_type
# ==================================================================


def fetch_type_schema(session: boto3.Session, region: str, type_name: str) -> Optional[dict]:
    """Hard inquiry: call cloudformation.describe_type for a resource type schema."""
    cfn = session.client("cloudformation", region_name=region)

    logger.debug("Fetching type schema: %s (region=%s)", type_name, region)

    try:
        resp = cfn.describe_type(Type="RESOURCE", TypeName=type_name)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("TypeNotFoundException", "CFNRegistryException"):
            logger.debug("Type not found in registry: %s", type_name)
            return None
        raise

    schema_str = resp.get("Schema", "")
    if not schema_str:
        logger.debug("Empty schema returned for: %s", type_name)
        return None

    try:
        schema = json.loads(schema_str)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse schema JSON for %s", type_name)
        return None

    prop_count = len(schema.get("properties", {}))
    logger.debug("Schema fetched: %s — %d properties, %d required, %d read-only",
                 type_name, prop_count,
                 len(schema.get("required", [])),
                 len(schema.get("readOnlyProperties", [])))

    schema["_bona_meta"] = {
        "type_name": type_name,
        "arn": resp.get("Arn", ""),
        "type_version": resp.get("TypeVersionArn", ""),
        "description": resp.get("Description", ""),
        "provisioning_type": resp.get("ProvisioningType", ""),
        "deprecated_status": resp.get("DeprecatedStatus", ""),
        "is_default_version": resp.get("IsDefaultVersion", True),
        "source_url": resp.get("SourceUrl", ""),
    }

    return schema


def get_type_schema(
    session: boto3.Session, region: str, type_name: str, refresh: bool = False
) -> Optional[dict]:
    """Get type schema — soft (cache) or hard (CloudFormation call)."""
    cache_name = _cache_name(type_name)

    if not refresh:
        cached = read_cache(cache_name, region)
        if cached:
            return {k: v for k, v in cached.items() if k != "_metadata"}

    schema = fetch_type_schema(session, region, type_name)
    if schema:
        write_cache(cache_name, region, schema)
    return schema


def fetch_all_type_schemas(
    session: boto3.Session, region: str, type_names: list[str], refresh: bool = False
) -> dict[str, dict]:
    """Batch fetch schemas for multiple resource types."""
    schemas = {}
    to_fetch = []

    if not refresh:
        for type_name in type_names:
            schema = get_type_schema(session, region, type_name, refresh=False)
            if schema:
                schemas[type_name] = schema
            else:
                to_fetch.append(type_name)
    else:
        to_fetch = list(type_names)

    if to_fetch:
        logger.info("Fetching %d type schemas from CloudFormation (hard inquiry)...", len(to_fetch))
        for type_name in to_fetch:
            schema = get_type_schema(session, region, type_name, refresh=True)
            if schema:
                schemas[type_name] = schema

    logger.info("Resolved %d / %d type schemas", len(schemas), len(type_names))
    return schemas


# ==================================================================
# Schema parsing helpers
# ==================================================================


def _get_schema_properties(schema: dict) -> dict[str, Any]:
    return schema.get("properties", {})


def _get_required_properties(schema: dict) -> list[str]:
    return schema.get("required", [])


def _get_read_only_properties(schema: dict) -> list[str]:
    paths = schema.get("readOnlyProperties", [])
    return [p.split("/")[-1] for p in paths if p.startswith("/properties/")]


def _get_create_only_properties(schema: dict) -> list[str]:
    paths = schema.get("createOnlyProperties", [])
    return [p.split("/")[-1] for p in paths if p.startswith("/properties/")]


def _get_primary_identifier(schema: dict) -> list[str]:
    paths = schema.get("primaryIdentifier", [])
    return [p.split("/")[-1] for p in paths if p.startswith("/properties/")]


def _get_additional_identifiers(schema: dict) -> list[list[str]]:
    result = []
    for id_set in schema.get("additionalIdentifiers", []):
        parsed = [p.split("/")[-1] for p in id_set if p.startswith("/properties/")]
        if parsed:
            result.append(parsed)
    return result


# ==================================================================
# Supplemental Data Providers
# ==================================================================
# Each provider is a function that:
#   - Takes (session, region, resources, refresh) 
#   - Returns (extra_nodes, extra_edges)
# Providers are keyed by the resource type they enrich.


def _fetch_ec2_instance_specs(session: boto3.Session, region: str) -> dict:
    """Fetch EC2 instance type specs (vCPUs, memory, GPU, etc.)."""
    ec2 = session.client("ec2", region_name=region)
    types = {}

    logger.info("Fetching instance type specs from EC2 (hard inquiry)...")
    paginator = ec2.get_paginator("describe_instance_types")

    for page in paginator.paginate():
        for it in page.get("InstanceTypes", []):
            name = it.get("InstanceType", "")
            if not name:
                continue

            vcpu_info = it.get("VCpuInfo", {})
            mem_info = it.get("MemoryInfo", {})
            net_info = it.get("NetworkInfo", {})
            gpu_info = it.get("GpuInfo", {})
            proc_info = it.get("ProcessorInfo", {})
            ebs_info = it.get("EbsInfo", {})

            gpus = gpu_info.get("Gpus", [])
            gpu_first = gpus[0] if gpus else {}
            architectures = proc_info.get("SupportedArchitectures", [])

            types[name] = {
                "instance_type": name,
                "vcpus": vcpu_info.get("DefaultVCpus", 0),
                "memory_mib": mem_info.get("SizeInMiB", 0),
                "architecture": architectures[0] if architectures else "",
                "hypervisor": it.get("Hypervisor", ""),
                "network_performance": net_info.get("NetworkPerformance", ""),
                "max_network_interfaces": net_info.get("MaximumNetworkInterfaces", 0),
                "ipv6_supported": net_info.get("Ipv6Supported", False),
                "ebs_optimized_support": ebs_info.get("EbsOptimizedSupport", ""),
                "ebs_encryption_support": ebs_info.get("EncryptionSupport", ""),
                "burstable_performance": it.get("BurstablePerformanceSupported", False),
                "bare_metal": it.get("BareMetal", False),
                "current_generation": it.get("CurrentGeneration", True),
                "gpu_count": gpu_first.get("Count", 0),
                "gpu_manufacturer": gpu_first.get("Manufacturer", ""),
                "gpu_name": gpu_first.get("Name", ""),
                "gpu_memory_mib": gpu_first.get("MemoryInfo", {}).get("SizeInMiB", 0),
                "storage_supported": it.get("InstanceStorageSupported", False),
                "instance_storage_gb": it.get("InstanceStorageInfo", {}).get("TotalSizeInGB", 0),
                "storage_type": "local" if it.get("InstanceStorageSupported") else "ebs-only",
            }

    logger.info("Fetched %d instance type specs", len(types))
    return types


def _get_ec2_instance_specs(session: boto3.Session, region: str, refresh: bool = False) -> dict:
    """Get EC2 instance specs — soft (cache) or hard."""
    cache_name = "instance-specs"

    if not refresh:
        cached = read_cache(cache_name, region)
        if cached:
            return {k: v for k, v in cached.items() if k != "_metadata"}

    types = _fetch_ec2_instance_specs(session, region)
    write_cache(cache_name, region, types)
    return types


def _enrich_ec2_instances(
    session: boto3.Session,
    region: str,
    resources: list[AssetNode],
    refresh: bool = False,
) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Supplemental enrichment for EC2: create InstanceSpec nodes + RUNS_ON edges."""
    ec2_nodes = [r for r in resources if r.resource_type == "AWS::EC2::Instance"]
    if not ec2_nodes:
        return [], []

    # Collect unique instance types needed
    needed_types = set()
    for node in ec2_nodes:
        it = node.properties.get("instanceType", "")
        if it:
            needed_types.add(it)

    if not needed_types:
        return [], []

    logger.debug("EC2 supplemental: %d instances use %d unique instance types",
                 len(ec2_nodes), len(needed_types))
    logger.debug("Instance types needed: %s", sorted(needed_types))

    # Get instance type specs (soft or hard)
    all_specs = _get_ec2_instance_specs(session, region, refresh=refresh)

    # Create InstanceSpec reference nodes (one per instance type in use)
    spec_nodes = []
    for type_name in sorted(needed_types):
        specs = all_specs.get(type_name, {})
        if not specs:
            continue

        spec_nodes.append(AssetNode(
            id=f"instance-spec:{type_name}",
            node_type="AWS::EC2::InstanceSpec",
            name=type_name,
            provider="aws",
            account_id="global",
            region="global",
            resource_type="AWS::EC2::InstanceSpec",
            state="ACTIVE",
            properties=specs,
        ))

    # Create RUNS_ON edges from EC2 instances to their spec
    runs_on_edges = []
    for node in ec2_nodes:
        it = node.properties.get("instanceType", "")
        if it and it in all_specs:
            runs_on_edges.append(AssetEdge(
                source_id=node.id,
                target_id=f"instance-spec:{it}",
                edge_type="RUNS_ON",
                provider="aws",
                account_id=node.account_id,
            ))

    logger.info("EC2 supplemental: %d InstanceSpec nodes, %d RUNS_ON edges", len(spec_nodes), len(runs_on_edges))
    return spec_nodes, runs_on_edges


# ------------------------------------------------------------------
# Supplemental provider registry
# ------------------------------------------------------------------
# Maps resource_type → enrichment function
# Each function: (session, region, resources, refresh) → (nodes, edges)

_SUPPLEMENTAL_PROVIDERS: dict[str, Callable] = {
    "AWS::EC2::Instance": _enrich_ec2_instances,
}

# Maps resource_type → config key in enrichment.supplemental
_SUPPLEMENTAL_PROVIDER_KEYS: dict[str, str] = {
    "AWS::EC2::Instance": "ec2_instance_specs",
}


# ==================================================================
# Main enrichment entry point
# ==================================================================


def enrich_with_type_schemas(
    resources: list[AssetNode],
    session: boto3.Session,
    region: str,
    refresh_cache: bool = False,
    config: Optional["EnrichmentConfig"] = None,
) -> tuple[list[AssetNode], list[AssetNode], list[AssetEdge]]:
    """Unified enrichment: type schemas + supplemental data.

    1. Fetch CloudFormation type schemas → TypeDefinition reference nodes
    2. Create INSTANCE_OF edges from each resource to its TypeDefinition
    3. Run supplemental providers (e.g. EC2 instance specs) → extra nodes + edges

    Uses config.include/exclude to filter which types get enriched.

    Args:
        resources: all discovered AssetNode instances
        session: boto3 session
        region: AWS region
        refresh_cache: force hard inquiry for all sources
        config: EnrichmentConfig with include/exclude patterns (None = enrich all)

    Returns:
        (resources, reference_nodes, edges)
    """
    from ..config import EnrichmentConfig

    if config is None:
        config = EnrichmentConfig(enabled=True)

    # Collect unique resource types, filtered by config
    all_types = {r.resource_type for r in resources if r.resource_type.startswith("AWS::")}
    type_names = [t for t in all_types if config.should_enrich(t)]

    skipped = all_types - set(type_names)
    logger.debug("Enrichment filter: %d types matched, %d skipped", len(type_names), len(skipped))
    if skipped:
        logger.debug("Skipped types: %s", sorted(skipped))

    if not type_names:
        logger.info("No types matched enrichment include/exclude filter")
        return resources, [], []

    logger.debug("Types to enrich: %s", sorted(type_names))

    # --- Step 1: Type schemas → TypeDefinition nodes ---
    schemas = fetch_all_type_schemas(session, region, type_names, refresh=refresh_cache)

    type_def_nodes = []
    for type_name, schema in schemas.items():
        node = _schema_to_type_definition_node(type_name, schema)
        type_def_nodes.append(node)
        logger.debug("Created TypeDefinition node: %s (id=%s, %d properties in schema)",
                     type_name, node.id, node.properties.get("property_count", 0))

    # --- Step 2: INSTANCE_OF edges ---
    instance_of_edges: list[AssetEdge] = []
    for resource in resources:
        if resource.resource_type in schemas:
            instance_of_edges.append(AssetEdge(
                source_id=resource.id,
                target_id=f"type-definition:{resource.resource_type}",
                edge_type="INSTANCE_OF",
                provider="aws",
                account_id=resource.account_id,
            ))

    logger.debug("Created %d INSTANCE_OF edges linking resources to TypeDefinitions", len(instance_of_edges))

    # --- Step 3: Supplemental providers ---
    supplemental_nodes: list[AssetNode] = []
    supplemental_edges: list[AssetEdge] = []

    for resource_type, provider_fn in _SUPPLEMENTAL_PROVIDERS.items():
        # Check if this supplemental provider is enabled in config
        provider_key = _SUPPLEMENTAL_PROVIDER_KEYS.get(resource_type, "")
        if provider_key and not config.supplemental.get(provider_key, True):
            continue
        if resource_type in all_types and config.should_enrich(resource_type):
            extra_nodes, extra_edges = provider_fn(session, region, resources, refresh_cache)
            supplemental_nodes.extend(extra_nodes)
            supplemental_edges.extend(extra_edges)

    # Combine all reference nodes and edges
    all_ref_nodes = type_def_nodes + supplemental_nodes
    all_edges = instance_of_edges + supplemental_edges

    logger.info(
        "Enrichment complete: %d TypeDefinition + %d supplemental nodes, %d edges",
        len(type_def_nodes), len(supplemental_nodes), len(all_edges),
    )

    return resources, all_ref_nodes, all_edges


# ==================================================================
# TypeDefinition node builder
# ==================================================================


def _schema_to_type_definition_node(type_name: str, schema: dict) -> AssetNode:
    """Convert a CloudFormation type schema into a TypeDefinition reference node."""
    properties = _get_schema_properties(schema)
    required = _get_required_properties(schema)
    read_only = _get_read_only_properties(schema)
    create_only = _get_create_only_properties(schema)
    primary_id = _get_primary_identifier(schema)
    additional_ids = _get_additional_identifiers(schema)
    meta = schema.get("_bona_meta", {})

    return AssetNode(
        id=f"type-definition:{type_name}",
        node_type="AWS::CloudFormation::TypeDefinition",
        name=type_name,
        provider="aws",
        account_id="global",
        region="global",
        resource_type="AWS::CloudFormation::TypeDefinition",
        state="ACTIVE",
        properties={
            "type_name": type_name,
            "description": schema.get("description", meta.get("description", "")),
            "provisioning_type": meta.get("provisioning_type", ""),
            "deprecated_status": meta.get("deprecated_status", ""),
            "source_url": meta.get("source_url", ""),
            "property_names": sorted(properties.keys()),
            "property_count": len(properties),
            "required_properties": required,
            "read_only_properties": read_only,
            "create_only_properties": create_only,
            "primary_identifier": primary_id,
            "additional_identifiers": additional_ids,
            "handlers": list(schema.get("handlers", {}).keys()),
        },
    )
