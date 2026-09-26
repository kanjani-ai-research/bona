"""Instance Type Enrichment — adds hardware specs to EC2 nodes.

Soft inquiry: reads from ~/.bona/cache/instance-types-{region}.json
Hard inquiry: calls ec2.describe_instance_types, saves to cache

Also emits InstanceTypeDefinition reference nodes + INSTANCE_OF edges.
"""

import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from ..cache import read_cache, write_cache
from ..schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)

CACHE_NAME = "instance-types"


def fetch_instance_types(session: boto3.Session, region: str) -> dict:
    """Hard inquiry: call ec2.describe_instance_types, return type→specs dict."""
    ec2 = session.client("ec2", region_name=region)
    types = {}

    logger.info("Fetching instance type definitions from EC2 (hard inquiry)...")
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
            storage_info = it.get("InstanceStorageInfo", {})
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
                "instance_storage_gb": storage_info.get("TotalSizeInGB", 0),
                "storage_type": "local" if it.get("InstanceStorageSupported") else "ebs-only",
            }

    logger.info("Fetched %d instance type definitions", len(types))
    return types


def get_instance_types(session: boto3.Session, region: str, refresh: bool = False) -> dict:
    """Get instance types — soft inquiry (cache) or hard (AWS call).

    Args:
        session: boto3 session
        region: AWS region
        refresh: if True, bypass cache (hard inquiry)

    Returns:
        dict mapping instance_type name → specs
    """
    if not refresh:
        cached = read_cache(CACHE_NAME, region)
        if cached:
            # Remove metadata key before returning
            return {k: v for k, v in cached.items() if k != "_metadata"}

    # Hard inquiry
    types = fetch_instance_types(session, region)
    write_cache(CACHE_NAME, region, types)
    return types


def enrich_ec2_nodes(
    resources: list[AssetNode],
    session: boto3.Session,
    region: str,
    refresh_cache: bool = False,
) -> tuple[list[AssetNode], list[AssetNode], list[AssetEdge]]:
    """Enrich EC2 instance nodes with hardware specs.

    Args:
        resources: all discovered nodes
        session: boto3 session for hard inquiry
        region: AWS region
        refresh_cache: force cache refresh

    Returns:
        (enriched_resources, reference_nodes, instance_of_edges)
    """
    # Find EC2 instance nodes
    ec2_nodes = [r for r in resources if r.resource_type == "AWS::EC2::Instance"]
    if not ec2_nodes:
        return resources, [], []

    # Collect unique instance types needed
    needed_types = set()
    for node in ec2_nodes:
        it = node.properties.get("instanceType", "")
        if it:
            needed_types.add(it)

    if not needed_types:
        return resources, [], []

    # Get instance type definitions (soft or hard)
    all_types = get_instance_types(session, region, refresh=refresh_cache)

    # Enrich EC2 nodes
    for node in ec2_nodes:
        it = node.properties.get("instanceType", "")
        specs = all_types.get(it, {})
        if specs:
            node.properties["vcpus"] = specs["vcpus"]
            node.properties["memory_mib"] = specs["memory_mib"]
            node.properties["architecture"] = specs["architecture"]
            node.properties["hypervisor"] = specs["hypervisor"]
            node.properties["network_performance"] = specs["network_performance"]
            node.properties["ebs_optimized_support"] = specs["ebs_optimized_support"]
            node.properties["burstable"] = specs["burstable_performance"]
            node.properties["bare_metal"] = specs["bare_metal"]
            node.properties["gpu_count"] = specs["gpu_count"]
            node.properties["storage_type"] = specs["storage_type"]

    # Emit reference nodes for each used instance type
    reference_nodes = []
    instance_of_edges = []

    for type_name in needed_types:
        specs = all_types.get(type_name, {})
        if not specs:
            continue

        ref_node = AssetNode(
            id=f"instance-type:{type_name}",
            node_type="AWS::EC2::InstanceTypeDefinition",
            name=type_name,
            provider="aws",
            account_id="global",
            region="global",
            resource_type="AWS::EC2::InstanceTypeDefinition",
            state="ACTIVE",
            properties=specs,
        )
        reference_nodes.append(ref_node)

    # Create INSTANCE_OF edges
    for node in ec2_nodes:
        it = node.properties.get("instanceType", "")
        if it and it in all_types:
            instance_of_edges.append(AssetEdge(
                source_id=node.id,
                target_id=f"instance-type:{it}",
                edge_type="INSTANCE_OF",
                provider="aws",
                account_id=node.account_id,
            ))

    logger.info("Enriched %d EC2 nodes with %d instance type specs, %d reference nodes",
                len(ec2_nodes), len(needed_types), len(reference_nodes))

    return resources, reference_nodes, instance_of_edges
