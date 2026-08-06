# Configuration

## Prerequisites

Bona reads from AWS Config. You need:

1. **AWS Config recorder** enabled in your target region(s)
2. **IAM permissions** for the Bona user/role
3. **Python 3.11+** with `boto3`

---

## AWS Config Setup

### Enable the Config Recorder

If AWS Config isn't already enabled, set it up via the console or CLI:

```bash
# Create a Config recorder (records all resource types)
aws configservice put-configuration-recorder \
  --configuration-recorder name=default,roleARN=arn:aws:iam::ACCOUNT:role/ConfigRole \
  --recording-group allSupported=true,includeGlobalResourceTypes=true

# Create a delivery channel (required for recorder to work)
aws configservice put-delivery-channel \
  --delivery-channel name=default,s3BucketName=my-config-bucket

# Start recording
aws configservice start-configuration-recorder --configuration-recorder-name default
```

### Verify Config is Recording

```bash
aws configservice describe-configuration-recorder-status
```

Expected output shows `recording: true` and `lastStatus: SUCCESS`.

### Resource Types

By default, AWS Config records all supported resource types. To limit recording:

```bash
aws configservice put-configuration-recorder \
  --configuration-recorder name=default,roleARN=arn:aws:iam::ACCOUNT:role/ConfigRole \
  --recording-group '{
    "allSupported": false,
    "resourceTypes": [
      "AWS::EC2::Instance",
      "AWS::EC2::SecurityGroup",
      "AWS::S3::Bucket",
      "AWS::IAM::Role"
    ]
  }'
```

> **Note**: Bona can only discover resource types that Config is recording. If you filter Config's recording, Bona's output will be limited to those types.

---

## IAM Permissions

### Minimum Policy for `bona export`

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "config:SelectResourceConfig",
        "config:ListDiscoveredResources",
        "config:BatchGetResourceConfig",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
```

### Additional: Multi-account (aggregator)

```json
{
  "Effect": "Allow",
  "Action": "config:SelectAggregateResourceConfig",
  "Resource": "arn:aws:config:*:*:config-aggregator/*"
}
```

### Additional: History command

```json
{
  "Effect": "Allow",
  "Action": "config:GetResourceConfigHistory",
  "Resource": "*"
}
```

### Additional: Compliance findings

```json
{
  "Effect": "Allow",
  "Action": [
    "config:DescribeComplianceByResource",
    "config:GetComplianceDetailsByResource",
    "config:DescribeConfigRules"
  ],
  "Resource": "*"
}
```

### Additional: S3 upload

```json
{
  "Effect": "Allow",
  "Action": "s3:PutObject",
  "Resource": "arn:aws:s3:::my-bucket/bona/*"
}
```

---

## AWS Credentials

Bona uses the standard boto3 credential chain:

1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`)
2. `--profile` flag (maps to `~/.aws/credentials`)
3. AWS config file (`~/.aws/config`)
4. EC2 instance metadata / ECS task role / Lambda execution role

### Using Named Profiles

```ini
# ~/.aws/credentials
[prod]
aws_access_key_id = AKIA...
aws_secret_access_key = ...

[management]
aws_access_key_id = AKIA...
aws_secret_access_key = ...
```

```bash
bona export --profile prod --region us-east-1
```

### Using SSO

```ini
# ~/.aws/config
[profile prod]
sso_start_url = https://myorg.awsapps.com/start
sso_region = us-east-1
sso_account_id = 123456789012
sso_role_name = ReadOnlyAccess
region = us-east-1
```

```bash
aws sso login --profile prod
bona export --profile prod --region us-east-1
```

---

## Config Advanced Query

Bona primarily uses AWS Config's **Advanced Query** feature (`select_resource_config`). This requires:

- AWS Config recorder enabled
- At least one successful delivery (resources must be recorded)
- The account must support Config SQL (all commercial regions do)

If Advanced Query is unavailable (permissions, region), Bona falls back to `list_discovered_resources` + `batch_get_resource_config`, which is slower but always works.

---

## Troubleshooting

### "No resources found"

1. Verify Config is recording: `aws configservice describe-configuration-recorder-status`
2. Check that resources exist: `aws configservice list-discovered-resources --resource-type AWS::EC2::Instance`
3. Ensure your profile/region are correct

### "AccessDenied"

Add the required IAM permissions above.

### "InvalidExpression"

Advanced Query might not support a specific SQL syntax in your region. Bona will automatically fall back to the list+batch method. Use `--verbose` to see the fallback in action.

---

## Bona Configuration File

Bona supports a project-level or user-level configuration file (`bona.yaml` or `bona.json`) that controls enrichment behavior, output destinations, logging, and schema caching.

### File Locations (Search Order)

Bona searches for a configuration file in this order:

1. **Explicit path** — `--config path/to/bona.yaml` (CLI flag, highest priority)
2. **Project-local** — `./bona.yaml`, `./bona.yml`, or `./bona.json` in the current working directory
3. **User-level** — `~/.bona/config.yaml`, `~/.bona/config.yml`, or `~/.bona/config.json`

The first file found is used. If no configuration file is found, Bona uses built-in defaults (no enrichment, output to `./bona-output`, INFO-level logging to stderr).

### Precedence

**CLI flags always override config file values.** For example:

- `--verbose` forces `DEBUG` logging regardless of `logging.level` in the config file
- `--output-dir` overrides `output.dir`
- `--profile` and `--region` override any implicit AWS settings

---

### Full YAML Example

```yaml
# Bona Configuration
# Place as ./bona.yaml (project-local) or ~/.bona/config.yaml (user-level)
# Or specify explicitly: bona --config path/to/bona.yaml export ...

logging:
  level: INFO                    # DEBUG | INFO | WARNING | ERROR
  path: ./bona.log              # local file log (optional)
  # path: s3://my-bucket/logs/  # S3 log upload (flushed at end of run)

enrichment:
  enabled: true
  mode: soft                     # soft (use cache) | hard (re-fetch from AWS)
  include:                       # glob patterns — empty means all types
    - "AWS::EC2::*"
    - "AWS::S3::*"
    - "AWS::Lambda::*"
    - "AWS::IAM::*"
    - "AWS::RDS::*"
  exclude:                       # glob patterns — takes precedence over include
    - "AWS::CloudFormation::Stack"
  supplemental:
    ec2_instance_specs: true      # fetch vCPU/memory/GPU specs for EC2 types

output:
  dir: ./bona-output
  s3_uri: s3://my-bucket/apg/   # auto-upload graph output to S3

schema_cache:
  s3_uri: s3://my-bucket/schemas/  # auto-upload type schemas to S3
```

---

### Full JSON Example

```json
{
  "logging": {
    "level": "INFO",
    "path": "./bona.log"
  },
  "enrichment": {
    "enabled": true,
    "mode": "soft",
    "include": [
      "AWS::EC2::*",
      "AWS::S3::*",
      "AWS::Lambda::*",
      "AWS::IAM::*",
      "AWS::RDS::*"
    ],
    "exclude": [
      "AWS::CloudFormation::Stack"
    ],
    "supplemental": {
      "ec2_instance_specs": true
    }
  },
  "output": {
    "dir": "./bona-output",
    "s3_uri": "s3://my-bucket/apg/"
  },
  "schema_cache": {
    "s3_uri": "s3://my-bucket/schemas/"
  }
}
```

---

### Section Reference

#### `logging`

Controls log verbosity and output destination.

| Key     | Type   | Default  | Description |
|---------|--------|----------|-------------|
| `level` | string | `INFO`   | Log level: `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `path`  | string | *(empty — stderr only)* | Path for log output. Supports local file paths (e.g., `./bona.log`) or S3 URIs (e.g., `s3://bucket/logs/`). S3 logs are buffered and uploaded at the end of the run. |

> **Note**: The `--verbose` CLI flag forces `DEBUG` level regardless of the config file setting.

#### `enrichment`

Controls which resource types are enriched with additional metadata from AWS CloudFormation schemas.

| Key            | Type    | Default | Description |
|----------------|---------|---------|-------------|
| `enabled`      | bool    | `false` | Master switch — set to `true` to enable enrichment |
| `mode`         | string  | `soft`  | `soft` uses cached schemas when available; `hard` re-fetches from AWS every time |
| `include`      | list    | `[]` *(all types)* | Glob patterns for types to enrich. An empty list means all discovered types are eligible. |
| `exclude`      | list    | `[]`    | Glob patterns for types to skip. **Exclude takes precedence over include.** |
| `supplemental` | object  | `{"ec2_instance_specs": true}` | Feature flags for supplemental enrichment data |

**Glob matching**: Patterns use `fnmatch`-style matching. For example:

- `"AWS::EC2::*"` matches all EC2 resource types
- `"AWS::S3::Bucket"` matches only S3 Buckets
- `"AWS::*::*"` matches everything (same as empty include list)

**Evaluation logic**:

1. If `include` is non-empty, the type must match at least one include pattern
2. If the type matches any `exclude` pattern, it is skipped
3. Otherwise the type is enriched

#### `output`

Controls where Bona writes its output files (APG graph, schemas, etc.).

| Key      | Type   | Default          | Description |
|----------|--------|------------------|-------------|
| `dir`    | string | `./bona-output`  | Local directory for output files |
| `s3_uri` | string | *(empty)*        | S3 URI prefix for auto-uploading output (e.g., `s3://my-bucket/apg/`) |

When `s3_uri` is set, Bona uploads the output to S3 in addition to writing locally.

#### `schema_cache`

Controls schema cache upload to S3 for sharing across team members or CI/CD.

| Key      | Type   | Default   | Description |
|----------|--------|-----------|-------------|
| `s3_uri` | string | *(empty)* | S3 URI prefix for uploading cached type schemas (e.g., `s3://my-bucket/schemas/`) |

When set, Bona uploads fetched CloudFormation type schemas to this S3 location, enabling other runs to use them as a shared cache.
