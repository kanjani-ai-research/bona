# Bona

**The Joern for cloud** — builds an Asset Property Graph from AWS Config and exports Joern-compatible `nodes.json` + `edges.json`.

Bona discovers your cloud infrastructure via AWS Config's advanced SQL queries and produces a typed property graph that can be loaded into [Joern](https://joern.io), [Amazon Neptune](https://aws.amazon.com/neptune/), the [GraphRAG Toolkit](https://github.com/awslabs/graphrag-toolkit), or any tool that consumes the document-graph format.

## Installation

```bash
pip install bona
```

Requires Python 3.11+ and valid AWS credentials with `config:SelectResourceConfig` permissions.

## Quick Start

### Export — full infrastructure graph

```bash
# Export all resources in a region
bona export --profile myprofile --region us-east-1

# Filter to specific resource types (supports globs)
bona export --profile myprofile --region us-east-1 --types "AWS::EC2::*,AWS::S3::*"

# Multi-region export
bona export --profile myprofile --region us-east-1,us-west-2,eu-west-1

# Multi-account via aggregator
bona export --profile myprofile --region us-east-1 --aggregator MyOrgAggregator

# Include compliance findings as graph nodes
bona export --profile myprofile --region us-east-1 --include-compliance

# Upload to S3
bona export --profile myprofile --region us-east-1 --s3-uri s3://my-bucket/apg/
```

### History — temporal graph (config changes over time)

```bash
# Get configuration history for a specific resource
bona history --profile myprofile --region us-east-1 \
  --resource-type AWS::EC2::Instance \
  --resource-id i-0abc123def456

# Limit to last 5 snapshots
bona history --profile myprofile --region us-east-1 \
  --resource-type AWS::S3::Bucket \
  --resource-id my-bucket-name \
  --limit 5
```

### Types — list tracked resource types

```bash
# Show all resource types tracked by AWS Config in a region
bona types --profile myprofile --region us-east-1
```

### Schema — download type definitions

```bash
# Download CloudFormation type schemas to local cache
bona schema --profile myprofile --region us-east-1

# Specific types
bona schema --profile myprofile --region us-east-1 --types "AWS::S3::Bucket,AWS::EC2::Instance"

# All types tracked by Config
bona schema --profile myprofile --region us-east-1 --types all

# Upload schema cache to S3
bona schema --profile myprofile --region us-east-1 --s3-uri s3://my-bucket/schemas/
```

### Enrichment — type-aware graph

```bash
# Export with type schema enrichment (soft = uses cache, no extra AWS calls)
bona export --profile myprofile --region us-east-1 --enrich

# Hard enrichment (re-downloads schemas from CloudFormation)
bona export --profile myprofile --region us-east-1 --enrich hard
```

Enrichment adds **TypeDefinition reference nodes** and **INSTANCE_OF edges** to the graph:

```
[IAM Role "MyRole"] ──INSTANCE_OF──▶ [TypeDefinition "AWS::IAM::Role"]
[IAM Role "OtherRole"] ──INSTANCE_OF──▶ [TypeDefinition "AWS::IAM::Role"]
[S3 Bucket "data"] ──INSTANCE_OF──▶ [TypeDefinition "AWS::S3::Bucket"]
```

One TypeDefinition node per resource type carries the full schema contract (properties, required fields, read-only, identifiers).

### Configuration File

Control enrichment, output, and logging via `bona.yaml`:

```yaml
logging:
  level: INFO                    # DEBUG | INFO | WARNING | ERROR
  path: ./bona.log               # local file or s3://bucket/logs/

enrichment:
  enabled: true
  mode: soft                     # soft | hard
  include:
    - "AWS::EC2::*"
    - "AWS::S3::*"
  exclude:
    - "AWS::CloudFormation::Stack"

output:
  dir: ./bona-output
  s3_uri: s3://my-bucket/apg/

schema_cache:
  s3_uri: s3://my-bucket/schemas/
```

Place as `./bona.yaml` (project-local), `~/.bona/config.yaml` (user-level), or pass `--config path/to/bona.yaml`.

## CLI Reference

```
bona [--verbose] [--config <path>] <command> [options]

Global:
  --verbose, -v         Enable debug logging (overrides config level to DEBUG)
  --config <path>       Path to bona config file (JSON or YAML)

Commands:
  export                Export Asset Property Graph (nodes.json + edges.json)
  schema                Download CloudFormation type schemas to local cache
  history               Export temporal graph — resource config over time
  types                 List resource types tracked by AWS Config
  cache                 Pre-populate local cache (instance types, etc.)
```

### `bona export`

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | (default) | AWS profile name from ~/.aws/credentials |
| `--region` | us-east-1 | AWS region (comma-separated for multi-region) |
| `--output-dir` | ./bona-output | Local output directory |
| `--s3-uri` | | S3 URI for upload (e.g. `s3://bucket/prefix/`) |
| `--types` | (all) | Resource type filter (comma-separated, globs supported) |
| `--max-resources` | 10000 | Safety cap on total resources |
| `--include-compliance` | false | Include Config rule compliance findings as nodes |
| `--aggregator` | | Config aggregator name for multi-account queries |
| `--enrich` | disabled | Enable type schema enrichment (`soft` or `hard`). If flag given without value: `soft` |
| `--refresh-cache` | false | Force refresh of all cached data from AWS |

### `bona schema`

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | (default) | AWS profile name |
| `--region` | us-east-1 | AWS region |
| `--type` | | Single resource type (e.g. `AWS::S3::Bucket`) |
| `--types` | (common) | Comma-separated types, or `all` for Config-tracked types |
| `--s3-uri` | | Upload schemas to S3 (e.g. `s3://bucket/schemas/`) |
| `--validate` | | Validate a `nodes.json` file against cached schemas |

### `bona history`

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | (default) | AWS profile name |
| `--region` | us-east-1 | AWS region |
| `--resource-type` | (required) | Resource type (e.g. `AWS::EC2::Instance`) |
| `--resource-id` | (required) | Resource ID |
| `--limit` | 20 | Max history items |
| `--output-dir` | ./bona-output | Local output directory |

### `bona types`

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | (default) | AWS profile name |
| `--region` | us-east-1 | AWS region |

## Output Format

Bona produces two files: `nodes.json` and `edges.json` (plus a `manifest.json` summary).

### nodes.json

Each node is a flat JSON object representing an AWS resource:

```json
[
  {
    "id": "arn:aws:ec2:us-east-1:123456789012:instance/i-0abc123def456",
    "node_type": "AWS::EC2::Instance",
    "name": "web-server-1",
    "stable_id": "aws|123456789012|us-east-1|AWS::EC2::Instance|i-0abc123def456",
    "semantic_hash": "a1b2c3d4e5f6g7h8",
    "provider": "aws",
    "account_id": "123456789012",
    "region": "us-east-1",
    "resource_type": "AWS::EC2::Instance",
    "state": "ACTIVE",
    "tags": {"Name": "web-server-1", "Environment": "production"},
    "configuration_capture_time": "2026-08-05T12:00:00Z",
    "instanceType": "t3.medium",
    "vpcId": "vpc-abc123",
    "subnetId": "subnet-def456",
    "imageId": "ami-0123456789abcdef0"
  }
]
```

Key fields:
- **id** — ARN (globally unique identifier)
- **node_type** — AWS resource type (e.g. `AWS::EC2::Instance`)
- **stable_id** — deterministic identity for delta reconciliation across runs
- **semantic_hash** — SHA-256 of configuration; detects config drift
- **state** — `ACTIVE`, `DELETED`, or `DISCOVERED`
- All configuration properties are flattened to top-level keys (Joern style)

### edges.json

Each edge represents a relationship between resources:

```json
[
  {
    "source_id": "arn:aws:ec2:us-east-1:123456789012:instance/i-0abc123",
    "target_id": "arn:aws:ec2:us-east-1:123456789012:security-group/sg-xyz789",
    "edge_type": "IS_ASSOCIATED_WITH",
    "stable_id": "aws|123456789012|EDGE|IS_ASSOCIATED_WITH|i-0abc123→sg-xyz789"
  }
]
```

Edge types include:
- `IS_CONTAINED_IN`, `CONTAINS` — hierarchy (subnet in VPC)
- `IS_ATTACHED_TO` — resource attachments (volume to instance)
- `IS_ASSOCIATED_WITH` — associations (security group to instance)
- `IN_VPC`, `IN_SUBNET` — inferred from configuration properties
- `USES_ROLE`, `ENCRYPTED_BY` — inferred from IAM/KMS references
- `ROUTES_TO`, `ATTACHED_TO`, `IN_CLUSTER` — inferred from ARN references
- `INSTANCE_OF` — resource instance to its TypeDefinition (enrichment)
- `RUNS_ON` — EC2 instance to its InstanceSpec (supplemental enrichment)
- `CHANGED_TO` — temporal edges (history command)

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                            bona CLI                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────┐     ┌──────────────────┐     ┌───────────────────┐   │
│  │  CLI    │────▶│   AWSProvider    │────▶│    Exporter       │   │
│  │ (args)  │     │                  │     │                   │   │
│  └─────────┘     │  1. Enumerate    │     │  • nodes.json     │   │
│       │          │     types        │     │  • edges.json     │   │
│       ▼          │  2. SQL query    │     │  • manifest.json   │   │
│  ┌─────────┐    │     each type    │     │  • (S3 upload)    │   │
│  │ Config  │    │  3. Extract      │     └───────────────────┘   │
│  │(yaml/json)   │     relationships│                              │
│  └─────────┘    │  4. Enrich       │◀──── CloudFormation          │
│                  │     (type schemas)│      describe_type           │
│                  │  5. Compliance   │                              │
│                  └──────────────────┘                              │
│                           │                                        │
│                           ▼                                        │
│                  ┌──────────────────┐                              │
│                  │  Schema/Model    │                              │
│                  │                  │                              │
│                  │  • AssetNode     │                              │
│                  │  • AssetEdge     │                              │
│                  │  • DiscoveryResult│                             │
│                  └──────────────────┘                              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      AWS APIs                                        │
│                                                                     │
│  AWS Config:                                                        │
│  • select_resource_config (SQL)     — primary discovery method     │
│  • select_aggregate_resource_config — multi-account via aggregator │
│  • list_discovered_resources        — fallback enumeration         │
│  • batch_get_resource_config        — fallback detail fetch        │
│  • get_resource_config_history      — temporal graph (history cmd) │
│  • describe_compliance_by_resource  — compliance findings          │
│  • describe_config_rules            — rule metadata enrichment     │
│                                                                     │
│  CloudFormation:                                                    │
│  • describe_type                    — resource type schemas        │
│                                                                     │
│  EC2 (supplemental):                                                │
│  • describe_instance_types          — hardware specs               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   Downstream Consumers                               │
│                                                                     │
│  • Joern / joern-cpg           (nodes.json + edges.json)           │
│  • Amazon Neptune              (bulk load / document-graph)         │
│  • GraphRAG Toolkit            (graph-augmented retrieval)          │
│  • Neo4j / any property graph  (via JSON import)                   │
│  • Custom analytics            (jq, pandas, etc.)                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Multi-Account via Aggregator

Use the `--aggregator` flag to query across multiple AWS accounts and regions using an [AWS Config Aggregator](https://docs.aws.amazon.com/config/latest/developerguide/aggregate-data.html):

```bash
bona export --profile management --region us-east-1 --aggregator MyOrgAggregator
```

This uses `select_aggregate_resource_config` instead of `select_resource_config`, returning resources from all accounts enrolled in the aggregator. Each node retains its original `account_id` and `region`.

See [docs/multi-account.md](docs/multi-account.md) for aggregator setup instructions.

## Compatibility

| Consumer | How to use |
|----------|-----------|
| **Joern** | Load `nodes.json` + `edges.json` directly — schema matches `cpg_exporter.py` format |
| **Amazon Neptune** | Bulk load via `neptune-export` or convert to CSV with provided scripts |
| **GraphRAG Toolkit** | Feed `nodes.json` as document-graph source for graph-augmented RAG |
| **Neo4j** | Import JSON via `apoc.load.json` or convert to Cypher |
| **document-graph** | Native format — flat JSON nodes with typed edges |
| **jq / pandas** | Standard JSON — pipe through `jq` or load with `pd.read_json()` |

## Requirements

- Python 3.11+
- AWS credentials with permissions:
  - `config:SelectResourceConfig`
  - `config:SelectAggregateResourceConfig` (if using `--aggregator`)
  - `config:GetResourceConfigHistory` (for `history` command)
  - `config:DescribeComplianceByResource` (if using `--include-compliance`)
  - `config:DescribeConfigRules` (if using `--include-compliance`)
  - `cloudformation:DescribeType` (if using `--enrich` or `bona schema`)
  - `ec2:DescribeInstanceTypes` (if using EC2 supplemental enrichment)
  - `sts:GetCallerIdentity`
  - `s3:PutObject` (if using `--s3-uri`)
- AWS Config recorder must be enabled in target region(s)

## License

MIT
