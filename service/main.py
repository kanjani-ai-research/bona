"""Bona Service — FastAPI HTTP wrapper for pipeline integration.

Drop-in replacement for joern-cpg in the client CPG pipeline.
Same contract: POST /export → job_id, GET /status/{job_id} → poll until COMPLETE.

Output: nodes.json + edges.json + manifest.json to S3.
"""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown."""
    logger.info("Bona service starting — region=%s bucket=%s", settings.aws_region, settings.s3_bucket)
    yield
    logger.info("Bona service shutting down")


app = FastAPI(title="Bona — Asset Property Graph", version="0.2.0", lifespan=lifespan)

# Auth middleware
from .token_middleware import DelegationTokenMiddleware
app.add_middleware(DelegationTokenMiddleware, signing_key=settings.vouch_signing_key)

# In-memory job tracking
jobs: dict = {}


# ------------------------------------------------------------------
# Request/Response models
# ------------------------------------------------------------------


class ExportRequest(BaseModel):
    """Request to export an Asset Property Graph."""
    job_id: str = ""
    profile: str = ""  # AWS profile (empty = use instance role / IRSA)
    region: str = ""  # Override region (empty = use settings.aws_region)
    types: str = ""  # Comma-separated resource type filter
    max_resources: int = 0  # Override cap (0 = use settings)
    enrich: str = ""  # "soft", "hard", or "" (empty = use settings)
    learn: Optional[bool] = None  # Override learn mode
    include_compliance: bool = False
    s3_uri: str = ""  # Override output S3 URI


class JobStatus(BaseModel):
    """Job status response — same contract as joern-cpg."""
    job_id: str
    status: str  # QUEUED, EXPORTING, COMPLETE, FAILED
    started_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    output: Optional[dict] = None  # {nodes, edges, manifest, node_count, edge_count}


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@app.get("/health")
def health():
    """Health check — compatible with EKS readiness probes."""
    return {
        "status": "healthy",
        "service": "bona",
        "version": "0.2.0",
        "region": settings.aws_region,
        "active_jobs": len([j for j in jobs.values() if j["status"] == "EXPORTING"]),
    }


@app.get("/status/{job_id}")
def status(job_id: str):
    """Poll job status — same contract as joern-cpg GET /status/{job_id}."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return JobStatus(**job)


@app.post("/export", status_code=202)
async def export(req: ExportRequest):
    """Trigger Asset Property Graph export. Returns job_id immediately.

    Same contract as joern-cpg POST /export:
      - Accepts job config
      - Returns {job_id, status: "EXPORTING"}
      - Poll GET /status/{job_id} until COMPLETE or FAILED
    """
    job_id = req.job_id or str(uuid.uuid4())

    jobs[job_id] = {
        "job_id": job_id,
        "status": "QUEUED",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": "",
        "error": None,
        "output": None,
    }

    asyncio.create_task(_run_export(job_id, req))
    jobs[job_id]["status"] = "EXPORTING"

    return {"job_id": job_id, "status": "EXPORTING"}


@app.get("/jobs")
def list_jobs():
    """List all jobs (for debugging)."""
    return {"jobs": list(jobs.values())[-20:]}  # last 20


# ------------------------------------------------------------------
# Background job runner
# ------------------------------------------------------------------


async def _run_export(job_id: str, req: ExportRequest):
    """Background: run bona export and update job status."""
    try:
        result = await asyncio.to_thread(_do_bona_export, job_id, req)
        jobs[job_id]["status"] = "COMPLETE"
        jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        jobs[job_id]["output"] = result
        logger.info("Job %s COMPLETE: %d nodes, %d edges",
                    job_id, result.get("node_count", 0), result.get("edge_count", 0))
    except Exception as e:
        jobs[job_id]["status"] = "FAILED"
        jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        jobs[job_id]["error"] = str(e)[:500]
        logger.error("Job %s FAILED: %s", job_id, e)


def _do_bona_export(job_id: str, req: ExportRequest) -> dict:
    """Synchronous bona export — runs in thread pool.

    Calls the same provider/export logic as the CLI.
    """
    from bona.providers.aws.aws_provider import AWSProvider
    from bona.export import export_to_dir, export_to_s3
    from bona.config import load_config, EnrichmentConfig

    # Resolve settings (request overrides service defaults)
    profile = req.profile or settings.aws_profile
    region = req.region or settings.aws_region
    max_resources = req.max_resources or settings.max_resources
    types_filter = req.types or settings.resource_types
    type_list = [t.strip() for t in types_filter.split(",") if t.strip()] or None
    enrich_mode = req.enrich or settings.enrich_mode
    learn = req.learn if req.learn is not None else settings.learn_mode
    include_compliance = req.include_compliance or settings.include_compliance

    # S3 output path
    s3_uri = req.s3_uri
    if not s3_uri and settings.s3_bucket:
        s3_uri = f"s3://{settings.s3_bucket}/{settings.export_prefix}/{settings.tenant_id}/{region}/{job_id}/"

    logger.info("Export starting: job_id=%s region=%s types=%s enrich=%s learn=%s",
                job_id, region, type_list or "(all)", enrich_mode, learn)

    # Build provider
    provider = AWSProvider(
        profile=profile,
        region=region,
        max_resources=max_resources,
        resource_types=type_list,
        include_compliance=include_compliance,
    )

    # Enrichment
    if enrich_mode:
        provider.validate_schemas = True
        provider.enrichment_config = EnrichmentConfig(enabled=True, mode=enrich_mode)
        if enrich_mode == "hard":
            provider.refresh_cache = True

    # Learn mode
    provider.learn_mode = learn

    # Run discovery
    result = provider.discover()

    # Write to S3
    output_info = {}
    if s3_uri:
        manifest = export_to_s3(result, s3_uri.rstrip("/"), profile=profile)
        output_info = {
            "nodes": manifest.get("nodes_s3", ""),
            "edges": manifest.get("edges_s3", ""),
            "manifest": manifest.get("manifest_s3", ""),
            "node_count": result.resource_count,
            "edge_count": result.relationship_count,
            "region": region,
            "account_id": result.account_id,
            "duration_ms": result.duration_ms,
        }
    else:
        # No S3 — write locally (fallback for dev/testing)
        import tempfile
        output_dir = tempfile.mkdtemp(prefix=f"bona-{job_id}-")
        manifest = export_to_dir(result, output_dir)
        output_info = {
            "nodes": f"{output_dir}/nodes.json",
            "edges": f"{output_dir}/edges.json",
            "manifest": f"{output_dir}/manifest.json",
            "node_count": result.resource_count,
            "edge_count": result.relationship_count,
            "region": region,
            "account_id": result.account_id,
            "duration_ms": result.duration_ms,
        }

    return output_info


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
