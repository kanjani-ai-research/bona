"""Descriptions Cache — persists LLM-generated node descriptions.

Stores at ~/.bona/descriptions.json so descriptions aren't regenerated
every run. Only calls the LLM for nodes not already described.

Supports S3 upload for shared team access.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DESCRIPTIONS_PATH = Path.home() / ".bona" / "descriptions.json"


def load_descriptions(path: Optional[str] = None) -> dict[str, str]:
    """Load cached descriptions. Returns dict mapping node_id → description."""
    desc_path = Path(path) if path else DEFAULT_DESCRIPTIONS_PATH
    if not desc_path.exists():
        return {}
    try:
        data = json.loads(desc_path.read_text())
        descriptions = data.get("descriptions", {})
        logger.info("Loaded %d cached descriptions", len(descriptions))
        return descriptions
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load descriptions cache: %s", e)
        return {}


def save_descriptions(descriptions: dict[str, str], path: Optional[str] = None, s3_uri: str = "", profile: str = "", region: str = "us-east-1") -> None:
    """Save descriptions to local file and optionally S3."""
    desc_path = Path(path) if path else DEFAULT_DESCRIPTIONS_PATH
    desc_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(descriptions),
        "descriptions": descriptions,
    }
    content = json.dumps(data, indent=2)
    desc_path.write_text(content)
    logger.info("Saved %d descriptions to %s", len(descriptions), desc_path)

    if s3_uri:
        _upload_to_s3(content, s3_uri, "descriptions.json", profile, region)


def merge_descriptions(existing: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    """Merge new descriptions into existing, without overwriting."""
    merged = dict(existing)
    added = 0
    for node_id, desc in new.items():
        if node_id not in merged and desc:
            merged[node_id] = desc
            added += 1
    if added:
        logger.info("Merged %d new descriptions (total: %d)", added, len(merged))
    return merged


def _upload_to_s3(content: str, s3_uri: str, filename: str, profile: str = "", region: str = "us-east-1") -> None:
    """Upload content to S3."""
    import boto3

    if not s3_uri.startswith("s3://"):
        return

    parts = s3_uri[5:].split("/", 1)
    bucket = parts[0]
    prefix = parts[1].rstrip("/") if len(parts) > 1 else ""
    key = f"{prefix}/{filename}" if prefix else filename

    try:
        session_kwargs = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile
        session = boto3.Session(**session_kwargs)
        s3 = session.client("s3")
        s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"), ContentType="application/json")
        logger.info("Uploaded %s to s3://%s/%s", filename, bucket, key)
    except Exception as e:
        logger.warning("Failed to upload %s to S3: %s", filename, e)
