"""APG Exporter — writes Joern-compatible nodes.json + edges.json from discovery results.

Output goes to local directory by default. S3 upload only if --s3-uri is passed.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from .schema.model import DiscoveryResult

logger = logging.getLogger(__name__)


def export_to_dir(result: DiscoveryResult, output_dir: str) -> dict:
    """Write nodes.json + edges.json to a local directory.

    Returns metadata dict with file paths and counts.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Serialize nodes
    nodes = [r.to_dict() for r in result.resources]
    # Include findings as nodes too
    nodes.extend([f.to_dict() for f in result.findings])

    # Serialize edges
    edges = [e.to_dict() for e in result.relationships]

    nodes_path = out / "nodes.json"
    edges_path = out / "edges.json"

    with open(nodes_path, "w") as f:
        json.dump(nodes, f, indent=2, default=str)

    with open(edges_path, "w") as f:
        json.dump(edges, f, indent=2, default=str)

    # Write manifest
    manifest = {
        "provider": result.provider,
        "account_id": result.account_id,
        "region": result.region,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "finding_count": len(result.findings),
        "duration_ms": result.duration_ms,
        "nodes_file": str(nodes_path),
        "edges_file": str(edges_path),
    }

    manifest_path = out / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Exported: %d nodes, %d edges → %s", len(nodes), len(edges), output_dir)
    return manifest


def export_to_s3(result: DiscoveryResult, s3_uri: str, profile: str = "") -> dict:
    """Write nodes.json + edges.json to S3.

    Args:
        result: Discovery result to export
        s3_uri: S3 URI like s3://bucket/prefix/
        profile: AWS profile for S3 upload
    """
    import boto3

    # Parse S3 URI
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    parts = s3_uri[5:].split("/", 1)
    bucket = parts[0]
    prefix = parts[1].rstrip("/") if len(parts) > 1 else ""

    # Create session
    session_kwargs = {}
    if profile:
        session_kwargs["profile_name"] = profile
    session = boto3.Session(**session_kwargs)
    s3 = session.client("s3")

    # Serialize
    nodes = [r.to_dict() for r in result.resources]
    nodes.extend([f.to_dict() for f in result.findings])
    edges = [e.to_dict() for e in result.relationships]

    nodes_key = f"{prefix}/nodes.json" if prefix else "nodes.json"
    edges_key = f"{prefix}/edges.json" if prefix else "edges.json"
    manifest_key = f"{prefix}/manifest.json" if prefix else "manifest.json"

    s3.put_object(Bucket=bucket, Key=nodes_key, Body=json.dumps(nodes, indent=2, default=str), ContentType="application/json")
    s3.put_object(Bucket=bucket, Key=edges_key, Body=json.dumps(edges, indent=2, default=str), ContentType="application/json")

    manifest = {
        "provider": result.provider,
        "account_id": result.account_id,
        "region": result.region,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "finding_count": len(result.findings),
        "duration_ms": result.duration_ms,
        "nodes_s3": f"s3://{bucket}/{nodes_key}",
        "edges_s3": f"s3://{bucket}/{edges_key}",
    }

    s3.put_object(Bucket=bucket, Key=manifest_key, Body=json.dumps(manifest, indent=2), ContentType="application/json")

    logger.info("Exported to S3: %d nodes, %d edges → s3://%s/%s", len(nodes), len(edges), bucket, prefix)
    return manifest
