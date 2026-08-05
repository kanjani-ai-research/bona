# Architecture

## Overview

Bona builds an Asset Property Graph (APG) by querying AWS Config's advanced SQL interface, transforming the results into Joern-compatible typed nodes and edges, and exporting them as JSON files.

## Data Pipeline

```
┌─────────┐     ┌──────────────────────┐     ┌─────────────┐     ┌──────────────┐
│  CLI    │────▶│    AWSProvider       │────▶│   Schema    │────▶│   Exporter   │
│         │     │                      │     │   Model     │     │              │
│ parse   │     │ 1. enumerate types   │     │             │     │ nodes.json   │
│ args    │     │ 2. SQL query each    │     │ AssetNode   │     │ edges.json   │
│         │     │ 3. extract rels      │     │ AssetEdge   │     │ manifest.json│
│         │     │ 4. infer edges       │     │             │     │ (S3 upload)  │
│         │     │ 5. compliance        │     │             │     │              │
└─────────┘     └──────────────────────┘     └─────────────┘     └──────────────┘
```

## Components

### CLI (`src/bona/cli.py`)

Entry point. Parses arguments, dispatches to command handlers:
- `_do_export()` — full graph export
- `_do_history()` — temporal graph for one resource
- `_do_types()` — list tracked resource types

### AWSProvider (`src/bona/providers/aws/aws_provider.py`)

Core discovery engine. Uses AWS Config APIs:

| API | Purpose |
|-----|---------|
| `select_resource_config` | Primary SQL-based discovery (single account) |
| `select_aggregate_resource_config` | Multi-account via Config Aggregator |
| `list_discovered_resources` | Fallback: enumerate resource IDs |
| `batch_get_resource_config` | Fallback: fetch full config by ID |
| `get_resource_config_history` | Temporal snapshots (history command) |
| `describe_compliance_by_resource` | Compliance findings |
| `describe_config_rules` | Rule metadata enrichment |

### Schema Model (`src/bona/schema/model.py`)

Dataclasses that define the graph schema:

- **AssetNode** — resource node with `stable_id` and `semantic_hash` properties
- **AssetEdge** — typed directed edge with `stable_id`
- **DiscoveryResult** — container for a discovery run's output

### Exporter (`src/bona/export.py`)

Serializes `DiscoveryResult` to JSON files:
- `export_to_dir()` — write to local filesystem
- `export_to_s3()` — upload to S3 bucket

## Discovery Flow (export command)

```
1. Enumerate Resource Types
   ├─ SQL: SELECT resourceType, COUNT(*) ... GROUP BY resourceType
   └─ Fallback: hardcoded _COMMON_TYPES list (30 types)

2. Apply Type Filter
   ├─ Exact match: use types directly
   └─ Glob match: fnmatch against enumerated types

3. Query Each Type (paginated, 100/page)
   ├─ SQL: SELECT * WHERE resourceType = '{type}'
   ├─ Parse JSON results
   ├─ Convert to AssetNode (flatten config, extract tags)
   └─ Extract relationship edges from `relationships` field

4. Infer Additional Edges
   └─ Scan node properties for known keys (vpcId, roleArn, etc.)
       └─ Create typed edges (IN_VPC, USES_ROLE, ENCRYPTED_BY, etc.)

5. Compliance Findings (optional)
   ├─ Query AWS::Config::ResourceCompliance type
   ├─ Fallback: describe_compliance_by_resource
   └─ Enrich with rule metadata (describe_config_rules)

6. Export
   ├─ Serialize nodes via AssetNode.to_dict()
   ├─ Serialize edges via AssetEdge.to_dict()
   ├─ Write nodes.json, edges.json, manifest.json
   └─ Optional: upload to S3
```

## Temporal Flow (history command)

```
1. Call get_resource_config_history(type, id, limit)
2. For each configuration item:
   ├─ Create node with @timestamp suffix on ID
   ├─ Extract relationships from that snapshot
   └─ Create CHANGED_TO edge to previous snapshot
3. Write nodes.json + edges.json
```

## Edge Sources

Bona creates edges from three sources:

1. **Config relationships** — the `relationships` array in each Config item contains AWS-provided links (containment, attachment, association)
2. **Inferred edges** — configuration properties like `vpcId` imply structural relationships that Config doesn't always express explicitly
3. **Temporal edges** — `CHANGED_TO` edges link successive snapshots in history exports

## Throttling and Safety

- **Page size**: 100 results per API call (AWS Config limit)
- **Throttle delay**: 200ms between pages
- **Max resources cap**: configurable via `--max-resources` (default 10,000)
- **Fallback**: if `select_resource_config` fails (permissions, region support), falls back to `list_discovered_resources` + `batch_get_resource_config`

## Property Flattening

AWS Config returns deeply nested JSON. Bona flattens it:

- Scalars → top-level keys
- Short lists (≤20 scalars or ≤5 complex) → JSON arrays
- Long lists → `{key}_count` integer
- Nested objects → dot-notation keys (up to 2 levels deep)
- supplementaryConfiguration → `supp.` prefix

This matches Joern's flat-property style for code property graphs.
