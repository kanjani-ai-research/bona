# CLI Reference

## Synopsis

```
bona [--verbose] <command> [options]
```

## Global Options

| Flag | Short | Description |
|------|-------|-------------|
| `--verbose` | `-v` | Enable debug logging (shows API calls, pagination, timing) |

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

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | No command specified |

## Environment Variables

Bona uses the standard AWS SDK credential chain. The `--profile` flag takes precedence over environment variables like `AWS_PROFILE`.
