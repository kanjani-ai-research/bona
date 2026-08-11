# Bona — Next Generation Spec (AI-Enabled Intelligence Graph)

## Current State (v0.7.0)

| Feature | Status |
|---------|--------|
| CLI tool (`bona export`) | ✅ |
| Docker service (FastAPI) | ✅ |
| AWS provider (Config SQL) | ✅ |
| GCP provider | ✅ |
| MCP provider | ✅ |
| Type schemas (CF Registry → ASSET_TYPE nodes) | ✅ |
| LLM enrichment (soft/hard modes) | ✅ Partial |
| Output: nodes.json + edges.json + manifest.json → S3 | ✅ |
| Multi-region + multi-account (aggregator) | ✅ |
| Compliance findings as nodes | ✅ |
| Temporal history (per-resource config changes) | ✅ |
| Neptune CSV export format | ✅ |
| Token auth (delegation tokens) | ✅ |
| Diff (compare two scans) | ✅ |

## What's Missing

### 1. UUIDv7 Sequence (RID) — CRITICAL

Every node and edge output must carry a UUIDv7 `rid` field.
No timestamps for ordering. UUIDs for sequence.

```
Every node:
{
  "id": "arn:aws:s3:::prod-data-lake",
  "rid": "01916f3a-7b2c-7000-8000-4a3b2c1d0e0f",  ← UUIDv7, monotonic
  "stable_id": "sha256:...",                         ← content hash (same config = same ID)
  "operation": "upsert",                             ← upsert | delete
  ...
}

Manifest:
{
  "scan_id": "scan-...",
  "rid_high": "01916f3a-...",   ← highest RID in this batch
  "rid_low": "01916f3a-...",    ← lowest RID in this batch
  "prev_rid_high": "...",       ← last RID from previous scan (for delta)
  "type": "delta"               ← or "full"
}
```

graphrag-helper contract: "Give me everything with rid > my_last_rid"

### 2. Delta Mode — CRITICAL

Only process what changed since last scan.

```
bona export --delta --prev-manifest s3://bucket/prev/manifest.json
```

- Compare `stable_id` (content hash) of each resource
- If same → skip (no RID assigned, not in output)
- If changed → upsert with new RID
- If gone → delete marker with new RID
- If new → upsert with new RID

Output includes: `nodes_added`, `nodes_modified`, `nodes_removed` counts.

### 3. Vector Embeddings — CRITICAL

Embed summaries during enrichment (not as separate service).

```
bona export --enrich hard --embed --embed-model amazon.titan-embed-text-v2:0 --embed-dimensions 1024
```

Output: separate `vectors.json` file alongside nodes/edges.

```json
{"node_id": "arn:aws:s3:::prod-data-lake", "rid": "...", "vector": [0.012, -0.034, ...], "source_text": "Production data lake..."}
```

### 4. Organizational Node Creation (Schema-Driven) — HIGH

When tags match schema rules, create new nodes + edges.

```yaml
# Schema rule
enrichment_rules:
  - name: team_nodes
    match: "tag.Team exists"
    create_node:
      type: TEAM
      id: "team:{tag.Team}"
      properties:
        name: "{tag.Team}"
    create_edge:
      type: BELONGS_TO
      from: "{asset.id}"
      to: "team:{tag.Team}"
```

Driven by a schema YAML file passed to Bona:
```
bona export --schema /path/to/org-schema.yaml
```

### 5. Strict Scaling Parameters — HIGH

Controllable resource limits per run.

```
bona export \
  --batch-size 500 \          ← process 500 resources per batch
  --max-concurrent 3 \        ← max parallel API calls
  --rate-limit 10 \           ← max requests per second
  --max-resources 5000 \      ← stop after N resources
  --timeout 3600              ← overall timeout in seconds
```

### 6. Config-Helper Integration — HIGH

Service mode fetches config from config-helper (not env vars).

```python
# On startup:
config = fetch_config("extract")  # from config-helper
# Gets: s3_bucket, regions, schedule, embed_model, schema_path, etc.
```

### 7. Multi-Cloud Schema Normalization — MEDIUM

Normalize asset types across clouds into a common taxonomy:

```
AWS::S3::Bucket     → storage.object_store
AWS::EC2::Instance  → compute.virtual_machine
GCP::Storage::Bucket → storage.object_store
Azure::Storage::BlobContainer → storage.object_store
```

Enables cross-cloud queries: "All object stores across all clouds"

### 8. Scan State Persistence — MEDIUM

Track scan state in DynamoDB (not just S3 manifests):

```
PK: SCAN#{org_id}#{provider_id}
SK: LATEST
Attrs: last_rid, last_scan_at, asset_count, status, manifest_uri
```

Enables: resume after failure, delta without reading full previous output.

### 9. Event-Driven Triggers — MEDIUM

SQS/EventBridge integration for scheduled + on-demand scans:

```
EventBridge rule (daily) → SQS → Bona service picks up → scans → S3
Onboard service "Scan Now" → SQS → Bona service picks up → scans → S3
```

### 10. Compliance Framework Enrichment — LOW (Bona already has this partially)

When `--include-compliance` is used, map findings to frameworks and create:
- COMPLIANCE_FINDING nodes
- VIOLATES / MEETS edges to framework controls
- Use Bona's existing `frameworks/` directory (NIST, CIS, HIPAA, PCI)

## Docker Service Changes

Current Dockerfile installs `bona` from PyPI. For next-gen:

```dockerfile
FROM public.ecr.aws/docker/library/python:3.13-slim

RUN groupadd -r app && useradd -r -g app app
WORKDIR /app

# Install bona library (includes all providers + enrichment)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Service wrapper
COPY service/ /app/service/

ENV PYTHONPATH=/app
USER app
EXPOSE 8080

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Requirements additions:
```
uuid7>=0.1.0            # UUIDv7 generation
boto3>=1.35.0           # AWS (Config, S3, Bedrock)
httpx>=0.27.0           # config-helper client
```

## Service API Additions

```
POST /export           ← existing (add: delta, embed, schema params)
GET  /status/{job_id}  ← existing
GET  /health           ← existing
POST /scan             ← NEW: trigger from SQS message format
GET  /scans            ← NEW: list recent scans for a provider
```

## Summary

Bona evolves from "cloud scanner" to "AI-enabled intelligence graph producer."
One tool: cloud account in → enriched, embedded, sequenced, delta-aware graph out.
graphrag-helper reads the output (by RID) and writes to Neptune + OpenSearch.
