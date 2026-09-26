# CLI Reference

## Synopsis

```
bona [--verbose] [--config <path>] <command> [options]
```

## Global Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--verbose` | `-v` | `false` | Enable debug logging (shows API calls, pagination, timing) |
| `--config` | | (auto) | Path to bona config file (JSON or YAML). Searches `./bona.yaml`, `~/.bona/config.yaml` if not set |

## Commands

---

## `bona export`

Export the Asset Property Graph — discovers all resources via AWS Config and writes `nodes.json` + `edges.json`.

### Usage

```bash
bona export --profile <name> --region <region> [options]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | (default) | AWS profile name from `~/.aws/credentials` |
| `--region` | `us-east-1` | AWS region (comma-separated for multi-region) |
| `--output-dir` | `./bona-output` | Local directory for output files |
| `--s3-uri` | | S3 URI for upload (e.g. `s3://bucket/prefix/`) |
| `--types` | (all) | Resource type filter (comma-separated, globs supported) |
| `--max-resources` | `10000` | Safety cap on total resources |
| `--include-compliance` | `false` | Include Config rule compliance findings as nodes |
| `--aggregator` | | Config Aggregator name for multi-account queries |
| `--refresh-cache` | `false` | Force refresh of cached data (instance types, etc.) from AWS |
| `--enrich` | disabled | Enrich nodes with type schema validation. Values: `soft` (cached schemas), `hard` (re-fetch schemas). If flag given without a value, defaults to `soft` |

### Examples

```bash
# Basic export
bona export --profile prod --region us-east-1

# Filter to EC2 and S3
bona export --profile prod --region us-east-1 --types "AWS::EC2::*,AWS::S3::*"

# Multi-region
bona export --profile prod --region us-east-1,us-west-2

# Multi-account via aggregator
bona export --profile management --region us-east-1 --aggregator OrgAggregator

# With compliance and S3 upload
bona export --profile prod --region us-east-1 \
  --include-compliance \
  --s3-uri s3://my-graph-bucket/bona/latest/

# Enrich with cached type schemas (soft)
bona export --profile prod --region us-east-1 --enrich

# Enrich with forced re-download of type schemas (hard)
bona export --profile prod --region us-east-1 --enrich hard
```

### Output Structure

```
./bona-output/
  nodes.json
  edges.json
  manifest.json
```

For multi-region, each region gets a subdirectory:

```
./bona-output/
  us-east-1/
    nodes.json, edges.json, manifest.json
  us-west-2/
    nodes.json, edges.json, manifest.json
```

---

## `bona schema`

Download CloudFormation type schemas to local cache, optionally upload to S3, or validate a `nodes.json` file against cached schemas.

### Usage

```bash
bona schema --profile <name> --region <region> [options]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | (default) | AWS profile name |
| `--region` | `us-east-1` | AWS region |
| `--type` | | Specific resource type to download (e.g. `AWS::S3::Bucket`) |
| `--types` | | Comma-separated resource types, or `all` for every Config-tracked type |
| `--s3-uri` | | S3 URI to upload cached schemas (e.g. `s3://bucket/schemas/`) |
| `--validate` | | Path to a `nodes.json` file to validate against cached type schemas |

If neither `--type` nor `--types` is specified, a default set of common types is downloaded.

### Examples

```bash
# Download schemas for common types
bona schema --profile prod --region us-east-1

# Download schema for a single type
bona schema --profile prod --region us-east-1 --type AWS::S3::Bucket

# Download schemas for all Config-tracked types
bona schema --profile prod --region us-east-1 --types all

# Download specific types and upload to S3
bona schema --profile prod --region us-east-1 \
  --types "AWS::EC2::Instance,AWS::S3::Bucket" \
  --s3-uri s3://my-bucket/schemas/

# Validate a nodes.json against cached schemas
bona schema --profile prod --region us-east-1 \
  --validate ./bona-output/nodes.json
```

---

## `bona history`

Export a temporal graph — configuration snapshots over time for a single resource, linked by `CHANGED_TO` edges.

### Usage

```bash
bona history --profile <name> --region <region> \
  --resource-type <type> --resource-id <id> [options]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | (default) | AWS profile name |
| `--region` | `us-east-1` | AWS region |
| `--resource-type` | (required) | AWS resource type (e.g. `AWS::EC2::Instance`) |
| `--resource-id` | (required) | Resource ID (e.g. `i-0abc123def456`) |
| `--limit` | `20` | Maximum history items to retrieve |
| `--output-dir` | `./bona-output` | Local output directory |

### Examples

```bash
# EC2 instance history
bona history --profile prod --region us-east-1 \
  --resource-type AWS::EC2::Instance \
  --resource-id i-0abc123def456

# S3 bucket changes (last 10)
bona history --profile prod --region us-east-1 \
  --resource-type AWS::S3::Bucket \
  --resource-id my-bucket \
  --limit 10
```

---

## `bona types`

List all resource types tracked by AWS Config in a region.

### Usage

```bash
bona types --profile <name> --region <region>
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | (default) | AWS profile name |
| `--region` | `us-east-1` | AWS region |

### Example Output

```
Resource types tracked by AWS Config (us-east-1):
==================================================
  AWS::CloudFormation::Stack
  AWS::EC2::Instance
  AWS::EC2::SecurityGroup
  AWS::S3::Bucket
  ...

Total: 42 types
```

---

## `bona cache`

Pre-populate local caches (instance types, pricing data, etc.) from AWS.

### Usage

```bash
bona cache --profile <name> --region <region>
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | (default) | AWS profile name |
| `--region` | `us-east-1` | AWS region |

### Example

```bash
bona cache --profile prod --region us-east-1
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | No command specified |

## Environment Variables

Bona uses the standard AWS SDK credential chain. The `--profile` flag takes precedence over environment variables like `AWS_PROFILE`.
