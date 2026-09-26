# Type Schema Enrichment

## Overview

Enrichment augments the Asset Property Graph with **type-level metadata** from CloudFormation type schemas. For each resource type discovered during export, Bona can:

1. **Download the CloudFormation type schema** via `describe_type` — capturing property definitions, required fields, identifiers, and handler metadata.
2. **Create TypeDefinition reference nodes** — one per unique resource type, representing the "class" of a resource.
3. **Create INSTANCE_OF edges** — linking each resource instance to its TypeDefinition.
4. **Run supplemental providers** — attach additional reference data (e.g., EC2 instance specs) as extra nodes and edges.

This enables graph queries like "show all resources of types that have been deprecated" or "find EC2 instances running on burstable instance types with less than 4 vCPUs."

## Graph Structure

Enrichment adds two layers to the graph:

### TypeDefinition Nodes + INSTANCE_OF Edges

```
┌────────────────────────┐          INSTANCE_OF          ┌──────────────────────────────────┐
│  AssetNode             │─────────────────────────────▶│  TypeDefinition                   │
│  id: "s3-bucket-xyz"   │                               │  id: "type-definition:AWS::S3::  │
│  type: AWS::S3::Bucket │                               │       Bucket"                     │
│  name: "my-bucket"     │                               │  properties:                      │
└────────────────────────┘                               │    property_count: 42             │
                                                         │    required_properties: [...]      │
┌────────────────────────┐          INSTANCE_OF          │    read_only_properties: [...]     │
│  AssetNode             │─────────────────────────────▶│    primary_identifier: [...]       │
│  id: "s3-bucket-abc"   │                               └──────────────────────────────────┘
│  type: AWS::S3::Bucket │
│  name: "other-bucket"  │
└────────────────────────┘
```

Multiple resource instances share a single TypeDefinition node.

### Supplemental Nodes + Edges (EC2 Example)

```
┌────────────────────────┐          INSTANCE_OF          ┌──────────────────────────────────┐
│  AssetNode             │─────────────────────────────▶│  TypeDefinition                   │
│  id: "ec2-i-abc123"    │                               │  "AWS::EC2::Instance"             │
│  type: AWS::EC2::      │                               └──────────────────────────────────┘
│        Instance         │
│  instanceType: t3.med  │          RUNS_ON              ┌──────────────────────────────────┐
│                        │─────────────────────────────▶│  InstanceSpec                     │
└────────────────────────┘                               │  id: "instance-spec:t3.medium"    │
                                                         │  vcpus: 2                         │
                                                         │  memory_mib: 4096                 │
                                                         │  architecture: x86_64             │
                                                         └──────────────────────────────────┘
```

### Edge Summary

| Edge Type | Source | Target | Meaning |
|-----------|--------|--------|---------|
| `INSTANCE_OF` | Any resource node | TypeDefinition | "This resource is an instance of this type" |
| `RUNS_ON` | EC2 Instance | InstanceSpec | "This instance runs on this hardware spec" |

## Soft vs Hard Inquiry

Enrichment uses a two-tier caching strategy to avoid unnecessary API calls:

| Mode | Behavior | When to Use |
|------|----------|-------------|
| **Soft** (default) | Reads from `~/.bona/cache/`. If cache hit, returns immediately. No API calls. | Regular exports, CI/CD pipelines, offline analysis |
| **Hard** | Calls AWS APIs (`describe_type`, `describe_instance_types`), then writes results to cache. | First run, after schema updates, when cache is stale |

Cache files are stored at:

```
~/.bona/cache/{region}/type-schema-AWS--{Service}--{Resource}.json
~/.bona/cache/{region}/instance-specs.json
```

The naming convention replaces `::` with `--` for filesystem safety.

### Behavior During Export

- **`--enrich soft`** — attempts cache read for each type. Types without cached schemas are silently skipped (no API call).
- **`--enrich hard`** — forces fresh API calls for all types, updating the cache.
- **`bona schema` command** — pre-populates the cache without running a full export (see CLI Usage below).

## Configuration

Enrichment is controlled via `bona.yaml` (or `bona.json`):

```yaml
enrichment:
  enabled: true              # Master switch (default: false)
  mode: soft                 # soft | hard (default: soft)
  include:                   # Glob patterns — only enrich matching types
    - "AWS::EC2::*"
    - "AWS::S3::*"
    - "AWS::Lambda::*"
  exclude:                   # Glob patterns — skip these even if included
    - "AWS::CloudFormation::Stack"
  supplemental:
    ec2_instance_specs: true  # Enable EC2 instance spec enrichment
```

### Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enrichment.enabled` | bool | `false` | Master enable/disable for all enrichment |
| `enrichment.mode` | string | `"soft"` | Default inquiry mode (`soft` or `hard`) |
| `enrichment.include` | list[str] | `[]` (all) | Glob patterns; empty list means enrich all types |
| `enrichment.exclude` | list[str] | `[]` | Glob patterns to exclude from enrichment |
| `enrichment.supplemental.ec2_instance_specs` | bool | `true` | Enable EC2 instance spec nodes + RUNS_ON edges |

### Include/Exclude Logic

1. If `include` is non-empty, the type must match **at least one** include pattern.
2. If the type matches **any** exclude pattern, it is skipped regardless of include.
3. Empty `include` list means "all types" (only exclude filtering applies).

Patterns use `fnmatch` glob syntax: `*` matches any sequence, `?` matches a single character.

## CLI Usage

### Pre-populate Schema Cache

```bash
# Download type schemas for all discovered types (hard inquiry)
bona schema --region us-east-1

# Download schemas for specific types
bona schema --region us-east-1 --types "AWS::S3::Bucket,AWS::EC2::Instance"

# Upload cache to S3 for team sharing
bona schema --region us-east-1 --upload s3://my-bucket/schemas/
```

### Export with Enrichment

```bash
# Export with soft enrichment (cache only, no API calls)
bona export --region us-east-1 --enrich soft

# Export with hard enrichment (fetch fresh schemas)
bona export --region us-east-1 --enrich hard

# Combine with type filters
bona export --region us-east-1 --enrich soft --types "AWS::EC2::*"
```

The `--enrich` flag overrides `enrichment.mode` from config. If `enrichment.enabled` is `false` in config but `--enrich` is passed on the CLI, enrichment runs anyway.

## Supplemental Providers

Supplemental providers attach **additional reference data** beyond CloudFormation type schemas. They are registered in `_SUPPLEMENTAL_PROVIDERS` keyed by the resource type they enrich.

### EC2 Instance Specs

The built-in `ec2_instance_specs` provider:

1. Identifies all `AWS::EC2::Instance` nodes with an `instanceType` property.
2. Calls `describe_instance_types` (or reads from cache) for the needed instance types.
3. Creates an `InstanceSpec` reference node per unique instance type, with properties:

| Property | Type | Description |
|----------|------|-------------|
| `instance_type` | string | e.g., `t3.medium` |
| `vcpus` | int | Default vCPU count |
| `memory_mib` | int | Memory in MiB |
| `architecture` | string | `x86_64`, `arm64`, etc. |
| `hypervisor` | string | `nitro`, `xen`, etc. |
| `network_performance` | string | e.g., `Up to 5 Gigabit` |
| `max_network_interfaces` | int | Max ENIs |
| `ipv6_supported` | bool | IPv6 support |
| `ebs_optimized_support` | string | `default`, `supported`, `unsupported` |
| `burstable_performance` | bool | T-series burstable |
| `bare_metal` | bool | Bare metal instance |
| `current_generation` | bool | Current generation flag |
| `gpu_count` | int | Number of GPUs (0 if none) |
| `gpu_manufacturer` | string | e.g., `NVIDIA` |
| `gpu_name` | string | e.g., `T4` |
| `gpu_memory_mib` | int | GPU memory |
| `storage_supported` | bool | Local instance storage |
| `instance_storage_gb` | int | Local storage size |
| `storage_type` | string | `local` or `ebs-only` |

4. Creates a `RUNS_ON` edge from each EC2 instance to its spec node.

### Writing a Custom Provider

To add a new supplemental provider:

```python
# In src/bona/enrichment/type_schemas.py

def _enrich_my_resource(
    session: boto3.Session,
    region: str,
    resources: list[AssetNode],
    refresh: bool = False,
) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Custom enrichment for MyResource."""
    # Filter to relevant resources
    my_nodes = [r for r in resources if r.resource_type == "AWS::MyService::MyResource"]
    if not my_nodes:
        return [], []

    # Fetch supplemental data (with caching)
    # ... your logic here ...

    # Return extra reference nodes and edges
    return extra_nodes, extra_edges


# Register the provider
_SUPPLEMENTAL_PROVIDERS["AWS::MyService::MyResource"] = _enrich_my_resource
_SUPPLEMENTAL_PROVIDER_KEYS["AWS::MyService::MyResource"] = "my_resource_data"
```

Then add the config key to allow users to enable/disable it:

```yaml
enrichment:
  supplemental:
    my_resource_data: true
```

## Example Output

### TypeDefinition Node

```json
{
  "id": "type-definition:AWS::S3::Bucket",
  "node_type": "AWS::CloudFormation::TypeDefinition",
  "name": "AWS::S3::Bucket",
  "provider": "aws",
  "account_id": "global",
  "region": "global",
  "resource_type": "AWS::CloudFormation::TypeDefinition",
  "state": "ACTIVE",
  "properties": {
    "type_name": "AWS::S3::Bucket",
    "description": "The AWS::S3::Bucket resource creates an Amazon S3 bucket.",
    "provisioning_type": "FULLY_MUTABLE",
    "deprecated_status": "LIVE",
    "source_url": "https://github.com/aws-cloudformation/aws-cloudformation-resource-providers-s3.git",
    "property_names": [
      "AccelerateConfiguration",
      "AccessControl",
      "AnalyticsConfigurations",
      "Arn",
      "BucketEncryption",
      "BucketName",
      "CorsConfiguration",
      "DomainName",
      "DualStackDomainName",
      "IntelligentTieringConfigurations",
      "InventoryConfigurations",
      "LifecycleConfiguration",
      "LoggingConfiguration",
      "MetricsConfigurations",
      "NotificationConfiguration",
      "ObjectLockConfiguration",
      "ObjectLockEnabled",
      "OwnershipControls",
      "PublicAccessBlockConfiguration",
      "RegionalDomainName",
      "ReplicationConfiguration",
      "Tags",
      "VersioningConfiguration",
      "WebsiteConfiguration",
      "WebsiteURL"
    ],
    "property_count": 25,
    "required_properties": [],
    "read_only_properties": ["Arn", "DomainName", "DualStackDomainName", "RegionalDomainName", "WebsiteURL"],
    "create_only_properties": ["BucketName", "ObjectLockEnabled"],
    "primary_identifier": ["BucketName"],
    "additional_identifiers": [],
    "handlers": ["create", "read", "update", "delete", "list"]
  }
}
```

### INSTANCE_OF Edge

```json
{
  "source_id": "s3-bucket-my-app-assets-123456789012",
  "target_id": "type-definition:AWS::S3::Bucket",
  "edge_type": "INSTANCE_OF",
  "provider": "aws",
  "account_id": "123456789012"
}
```

### InstanceSpec Node (Supplemental)

```json
{
  "id": "instance-spec:t3.medium",
  "node_type": "AWS::EC2::InstanceSpec",
  "name": "t3.medium",
  "provider": "aws",
  "account_id": "global",
  "region": "global",
  "resource_type": "AWS::EC2::InstanceSpec",
  "state": "ACTIVE",
  "properties": {
    "instance_type": "t3.medium",
    "vcpus": 2,
    "memory_mib": 4096,
    "architecture": "x86_64",
    "hypervisor": "nitro",
    "network_performance": "Up to 5 Gigabit",
    "max_network_interfaces": 3,
    "ipv6_supported": true,
    "ebs_optimized_support": "default",
    "ebs_encryption_support": "supported",
    "burstable_performance": true,
    "bare_metal": false,
    "current_generation": true,
    "gpu_count": 0,
    "gpu_manufacturer": "",
    "gpu_name": "",
    "gpu_memory_mib": 0,
    "storage_supported": false,
    "instance_storage_gb": 0,
    "storage_type": "ebs-only"
  }
}
```

### RUNS_ON Edge (Supplemental)

```json
{
  "source_id": "ec2-instance-i-0abc123def456789a-123456789012",
  "target_id": "instance-spec:t3.medium",
  "edge_type": "RUNS_ON",
  "provider": "aws",
  "account_id": "123456789012"
}
```
