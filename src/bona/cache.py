"""Cache manager — soft/hard inquiry pattern.

Soft: read from ~/.bona/cache/ (fast, no AWS calls)
Hard: call AWS, save to disk, then return
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".bona" / "cache"
DEFAULT_TTL_DAYS = 30


def _cache_path(name: str, region: str) -> Path:
    return CACHE_DIR / f"{name}-{region}.json"


def read_cache(name: str, region: str, ttl_days: int = DEFAULT_TTL_DAYS) -> Optional[dict]:
    """Soft inquiry: read from disk if cache exists and not expired."""
    path = _cache_path(name, region)
    if not path.exists():
        return None

    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Cache read failed for %s: %s", path, e)
        return None

    # Check TTL
    metadata = data.get("_metadata", {})
    cached_at = metadata.get("cached_at", 0)
    age_days = (time.time() - cached_at) / 86400

    if age_days > ttl_days:
        logger.info("Cache expired: %s (%.1f days old, ttl=%d)", path.name, age_days, ttl_days)
        return None

    logger.info("Cache hit: %s (%d entries, %.1f days old)", path.name, len(data) - 1, age_days)
    return data


def write_cache(name: str, region: str, data: dict) -> Path:
    """Write data to cache with metadata timestamp."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(name, region)

    data["_metadata"] = {
        "cached_at": time.time(),
        "region": region,
        "entry_count": len(data) - 1,
    }

    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info("Cache written: %s (%d entries)", path.name, len(data) - 1)
    return path


def invalidate_cache(name: str, region: str) -> bool:
    """Delete a cache file."""
    path = _cache_path(name, region)
    if path.exists():
        path.unlink()
        return True
    return False
