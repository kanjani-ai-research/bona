"""Bona Logging — configurable logging with local file and S3 support.

Reads settings from BonaConfig.logging:
  - level: DEBUG, INFO, WARNING, ERROR (default: INFO)
  - path: local file path or S3 URI (optional, logs to stderr by default)

Config example (bona.yaml):

    logging:
      level: DEBUG
      path: ./bona.log           # local file
      # path: s3://bucket/logs/  # S3 (uploaded at end of run)

Usage:
    from bona.logging import setup_logging
    setup_logging(config)       # call once at CLI startup
"""

import io
import json
import logging
import sys
from pathlib import Path
from typing import Optional

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_LOG_DATEFMT = "%H:%M:%S"

# Buffer for S3 upload at end of run
_s3_buffer: Optional[io.StringIO] = None
_s3_uri: Optional[str] = None
_s3_profile: Optional[str] = None
_s3_region: Optional[str] = None


def setup_logging(
    config: Optional["BonaConfig"] = None,
    cli_verbose: bool = False,
    profile: str = "",
    region: str = "us-east-1",
) -> None:
    """Configure logging based on BonaConfig.

    Priority: CLI --verbose (forces DEBUG) > config.logging.level > default (INFO)

    Args:
        config: BonaConfig instance (may be None for defaults)
        cli_verbose: if True, override level to DEBUG
        profile: AWS profile for S3 log upload
        region: AWS region for S3 log upload
    """
    global _s3_buffer, _s3_uri, _s3_profile, _s3_region

    # Determine level
    if cli_verbose:
        level = logging.DEBUG
    elif config and config.logging.level:
        level = getattr(logging, config.logging.level.upper(), logging.INFO)
    else:
        level = logging.INFO

    # Determine output path
    log_path = config.logging.path if config else ""

    # Build handlers
    handlers: list[logging.Handler] = []

    # Always log to stderr
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    handlers.append(stderr_handler)

    # Optional file handler
    if log_path and not log_path.startswith("s3://"):
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(path), mode="a", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
        handlers.append(file_handler)

    # Optional S3 buffer (uploaded at end via flush_to_s3)
    if log_path and log_path.startswith("s3://"):
        _s3_buffer = io.StringIO()
        _s3_uri = log_path
        _s3_profile = profile
        _s3_region = region
        buffer_handler = logging.StreamHandler(_s3_buffer)
        buffer_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
        handlers.append(buffer_handler)

    # Apply to root logger
    root = logging.getLogger()
    root.setLevel(level)
    # Remove existing handlers (avoid duplicates on re-init)
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)

    logging.getLogger(__name__).debug(
        "Logging configured: level=%s path=%s", logging.getLevelName(level), log_path or "(stderr)"
    )


def flush_to_s3() -> None:
    """Upload buffered logs to S3 (call at end of run)."""
    global _s3_buffer, _s3_uri

    if not _s3_buffer or not _s3_uri:
        return

    import time
    import boto3

    content = _s3_buffer.getvalue()
    if not content:
        return

    logger = logging.getLogger(__name__)

    try:
        # Parse s3://bucket/prefix/
        parts = _s3_uri[5:].split("/", 1)
        bucket = parts[0]
        prefix = parts[1].rstrip("/") if len(parts) > 1 else ""

        # Generate timestamped key
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        key = f"{prefix}/bona-{timestamp}.log" if prefix else f"bona-{timestamp}.log"

        session_kwargs = {"region_name": _s3_region or "us-east-1"}
        if _s3_profile:
            session_kwargs["profile_name"] = _s3_profile
        session = boto3.Session(**session_kwargs)
        s3 = session.client("s3")

        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="text/plain",
        )
        logger.debug("Logs uploaded to s3://%s/%s", bucket, key)
    except Exception as e:
        logger.warning("Failed to upload logs to S3: %s", e)
    finally:
        _s3_buffer.close()
        _s3_buffer = None
