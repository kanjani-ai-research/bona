# Graph Schema

## Overview

The **graph schema** is a living DDL that evolves as you discover infrastructure. It records all known node types, edge types, and their observed relationships — building a structural definition of your Asset Property Graph over time.

Unlike static schemas that must be manually maintained, Bona's graph schema grows automatically through **learn mode**. Each export run can teach the schema about new resource types, edge relationships, and property shapes it hasn't seen before. The result is a complete, versioned description of your infrastructure graph topology.

## Schema File Location

The schema is persisted as a JSON file:

| Setting | Value |
|---------|-------|
| Default path | `~/.bona/graph-schema.json` |
| Config override | `graph_schema.path` in `bona.yaml` |
| S3 upload | `graph_schema.s3_uri` in `bona.yaml` |

```yaml
# bona.yaml
graph_schema:
  path: /path/to/custom/graph-schema.json     # local path override
  s3_uri: s3://my-bucket/bona/graph-schema.json  # upload to S3 on every --learn run
```

If the file doesn't exist, Bona starts with an empty schema (`v0.1.0`).

### S3 Storage

When `graph_schema.s3_uri` is configured, the schema is uploaded to S3 after every learn run. This enables:

- **Shared team schema** — everyone reads from the same S3 location
- **Cross-account visibility** — aggregate learned schemas from multiple accounts
- **Audit trail** — enable S3 versioning to track schema evolution over time
- **Downstream consumption** — other tools (Neptune, GraphRAG, CI/CD) can read the schema

The S3 URI can be an exact key or a prefix:

```yaml
# Exact key
graph_schema:
  s3_uri: s3://my-bucket/bona/graph-schema.json

# Prefix (appends graph-schema.json)
graph_schema:
  s3_uri: s3://my-bucket/bona/schemas/
```

The local file is always written regardless of S3 configuration.

## Schema Structure

The schema file has four top-level fields:

```json
{
  "version": "0.3.0",
  "last_updated": "2026-08-06T16:00:00Z",
  "node_types": { ... },
  "edge_types": { ... }
}
```

### Full Annotated Example

```json
{
  "version": "0.3.0",
  "last_updated": "2026-08-06T16:00:00Z",

  "node_types": {
    "AWS::EC2::Instance": {
      "description": "",
      "observed_properties": ["instanceType", "platform", "state", "subnetId", "vpcId"],
      "observed_count": 42,
      "first_seen": "2026-07-20T10:00:00Z",
      "last_seen": "2026-08-06T16:00:00Z"
    },
    "AWS::EC2::Subnet": {
      "description": "",
      "observed_properties": ["availabilityZone", "cidrBlock", "mapPublicIpOnLaunch", "vpcId"],
      "observed_count": 12,
      "first_seen": "2026-07-20T10:00:00Z",
      "last_seen": "2026-08-06T16:00:00Z"
    },
    "AWS::EC2::VPC": {
      "description": "",
      "observed_properties": ["cidrBlock", "enableDnsHostnames", "enableDnsSupport", "isDefault"],
      "observed_count": 3,
      "first_seen": "2026-07-20T10:00:00Z",
      "last_seen": "2026-08-06T16:00:00Z"
    }
  },

  "edge_types": {
    "IS_CONTAINED_IN": {
      "description": "Hierarchy — child contained in parent",
      "observed_pairs": [
        {"source_type": "AWS::EC2::Subnet", "target_type": "AWS::EC2::VPC"},
        {"source_type": "AWS::EC2::Instance", "target_type": "AWS::EC2::Subnet"}
      ],
      "observed_count": 54,
      "first_seen": "2026-07-20T10:00:00Z",
      "last_seen": "2026-08-06T16:00:00Z"
    },
    "IN_VPC": {
      "description": "",
      "observed_pairs": [
        {"source_type": "AWS::EC2::SecurityGroup", "target_type": "AWS::EC2::VPC"}
      ],
      "observed_count": 8,
      "first_seen": "2026-07-25T14:30:00Z",
      "last_seen": "2026-08-06T16:00:00Z"
    }
  }
}
```

### Field Reference

#### Top-Level

| Field | Type | Description |
|-------|------|-------------|
| `version` | string | Semver version, auto-bumped by learn mode |
| `last_updated` | string | ISO 8601 timestamp of last write |
| `node_types` | object | Map of node type name → node type schema |
| `edge_types` | object | Map of edge type name → edge type schema |

#### Node Type Entry

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | Optional human-readable description |
| `observed_properties` | list[string] | Property keys seen across all instances (sorted, deduplicated) |
| `observed_count` | int | Total number of instances observed |
| `first_seen` | string | ISO 8601 timestamp when this type was first discovered |
| `last_seen` | string | ISO 8601 timestamp of the most recent observation |

#### Edge Type Entry

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | Optional human-readable description |
| `observed_pairs` | list[object] | Source/target type pairs observed for this edge type |
| `observed_count` | int | Total number of edges observed |
| `first_seen` | string | ISO 8601 timestamp when this edge type was first discovered |
| `last_seen` | string | ISO 8601 timestamp of the most recent observation |

#### Observed Pair Entry

| Field | Type | Description |
|-------|------|-------------|
| `source_type` | string | Node type of the edge source |
| `target_type` | string | Node type of the edge target |

## Learn Mode

Learn mode automatically updates the graph schema during export by examining discovered nodes and edges. Enable it with the `--learn` flag.

### How It Works

1. Bona loads the existing schema from `~/.bona/graph-schema.json` (or creates an empty one).
2. For each **discovered node**, it:
   - Registers the node type if not already known.
   - Appends any new property keys to `observed_properties`.
   - Increments `observed_count` and updates `last_seen`.
3. For each **discovered edge**, it:
   - Registers the edge type if not already known.
   - Resolves source/target node types and records the pair in `observed_pairs` if new.
   - Increments `observed_count` and updates `last_seen`.
4. Bumps the schema version according to what changed (see Semver Versioning below).
5. Writes the updated schema back to disk.

### What Triggers Updates

| Discovery | Effect on Schema | Version Bump |
|-----------|-----------------|--------------|
| New node type never seen before | Added to `node_types` | MINOR |
| New edge type never seen before | Added to `edge_types` | MINOR |
| New source→target pair for existing edge type | Added to `observed_pairs` | MINOR |
| New property key on existing node type | Added to `observed_properties` | PATCH |
| Existing node type re-observed | Count/timestamp updated | PATCH |
| Existing edge type re-observed | Count/timestamp updated | PATCH |

## Semver Versioning

The schema version follows [semver](https://semver.org/) (`MAJOR.MINOR.PATCH`):

| Level | Trigger | Example |
|-------|---------|---------|
| **MAJOR** | Manual bump only — reserved for breaking schema structure changes | `1.0.0` → `2.0.0` |
| **MINOR** | New node types, edge types, or relationship pairs discovered | `0.2.5` → `0.3.0` |
| **PATCH** | Only observation updates (counts, timestamps, new properties) | `0.3.0` → `0.3.1` |

MINOR bumps reset the PATCH counter to zero. MAJOR bumps reset both MINOR and PATCH.

Version progression example across multiple exports:

```
v0.1.0  →  Initial empty schema
v0.2.0  →  First export discovers EC2 instances and VPCs (new node types → MINOR)
v0.2.1  →  Re-export updates counts only (PATCH)
v0.3.0  →  Export discovers S3 buckets (new node type → MINOR)
v0.4.0  →  Export finds new IS_CONTAINED_IN pair: Lambda→VPC (new pair → MINOR)
v0.4.1  →  Re-export, no structural changes (PATCH)
```

## CLI Usage

### Export with Learn Mode

```bash
# Run export and update the graph schema with discovered types/edges
bona export --region us-east-1 --learn

# Combine learn mode with enrichment
bona export --region us-east-1 --learn --enrich soft

# Learn from a filtered export (only EC2 types)
bona export --region us-east-1 --learn --types "AWS::EC2::*"
```

### Combining --learn with --enrich

The `--learn` and `--enrich` flags complement each other:

| Flag | Purpose |
|------|---------|
| `--learn` | Updates the graph schema with discovered node/edge types and relationships |
| `--enrich` | Adds TypeDefinition reference nodes and supplemental data to the graph |

When combined, Bona first enriches the graph (adding TypeDefinition nodes, INSTANCE_OF edges, etc.) and then learns from the **complete** enriched graph — meaning the schema will also capture enrichment-generated types like `AWS::CloudFormation::TypeDefinition` and edge types like `INSTANCE_OF`.

```bash
# Full discovery: enrich the graph, then learn from it
bona export --region us-east-1 --learn --enrich hard
```

## Use Cases

### Schema Validation

Use the schema to validate graph exports — reject nodes or edges with types not present in the schema:

```python
from bona.graph_schema import load_graph_schema

schema = load_graph_schema()

# Check if a node type is known
assert "AWS::EC2::Instance" in schema.node_types

# Check if an edge pair is valid
vpc_edges = schema.edge_types["IS_CONTAINED_IN"]
valid_pairs = [(p.source_type, p.target_type) for p in vpc_edges.observed_pairs]
assert ("AWS::EC2::Subnet", "AWS::EC2::VPC") in valid_pairs
```

### Graph DDL for Downstream Tools

Export the schema as a starting point for graph database DDL (Neo4j, Neptune, etc.):

```cypher
// Generated from graph-schema.json node_types
CREATE CONSTRAINT FOR (n:AWS__EC2__Instance) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT FOR (n:AWS__EC2__Subnet) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT FOR (n:AWS__EC2__VPC) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT FOR (n:AWS__EC2__SecurityGroup) REQUIRE n.id IS UNIQUE;
```

### Tracking Infrastructure Evolution

Compare schema versions over time to understand how your infrastructure topology changes:

- New node types appearing → new services being adopted
- New edge type pairs → new relationships being formed between services
- Growing `observed_count` → expanding resource footprint
- `first_seen` / `last_seen` gaps → resources that may have been decommissioned

## Example: Evolved Schema

Below is a real-world schema after several export runs across a multi-VPC environment with EC2, Subnet, and Security Group resources:

```json
{
  "version": "0.5.0",
  "last_updated": "2026-08-06T16:51:00Z",
  "node_types": {
    "AWS::EC2::Instance": {
      "description": "",
      "observed_properties": [
        "architecture",
        "imageId",
        "instanceType",
        "keyName",
        "launchTime",
        "platform",
        "privateDnsName",
        "privateIpAddress",
        "publicIpAddress",
        "state",
        "subnetId",
        "vpcId"
      ],
      "observed_count": 87,
      "first_seen": "2026-07-15T09:00:00Z",
      "last_seen": "2026-08-06T16:51:00Z"
    },
    "AWS::EC2::SecurityGroup": {
      "description": "",
      "observed_properties": [
        "description",
        "groupId",
        "groupName",
        "ipPermissions",
        "ipPermissionsEgress",
        "vpcId"
      ],
      "observed_count": 34,
      "first_seen": "2026-07-15T09:00:00Z",
      "last_seen": "2026-08-06T16:51:00Z"
    },
    "AWS::EC2::Subnet": {
      "description": "",
      "observed_properties": [
        "availabilityZone",
        "availableIpAddressCount",
        "cidrBlock",
        "defaultForAz",
        "mapPublicIpOnLaunch",
        "state",
        "subnetId",
        "vpcId"
      ],
      "observed_count": 18,
      "first_seen": "2026-07-15T09:00:00Z",
      "last_seen": "2026-08-06T16:51:00Z"
    },
    "AWS::EC2::VPC": {
      "description": "",
      "observed_properties": [
        "cidrBlock",
        "dhcpOptionsId",
        "enableDnsHostnames",
        "enableDnsSupport",
        "instanceTenancy",
        "isDefault",
        "state",
        "vpcId"
      ],
      "observed_count": 4,
      "first_seen": "2026-07-15T09:00:00Z",
      "last_seen": "2026-08-06T16:51:00Z"
    }
  },
  "edge_types": {
    "IS_CONTAINED_IN": {
      "description": "Hierarchy — child contained in parent",
      "observed_pairs": [
        {"source_type": "AWS::EC2::Instance", "target_type": "AWS::EC2::Subnet"},
        {"source_type": "AWS::EC2::Subnet", "target_type": "AWS::EC2::VPC"}
      ],
      "observed_count": 105,
      "first_seen": "2026-07-15T09:00:00Z",
      "last_seen": "2026-08-06T16:51:00Z"
    },
    "IN_VPC": {
      "description": "Resource belongs to a VPC",
      "observed_pairs": [
        {"source_type": "AWS::EC2::Instance", "target_type": "AWS::EC2::VPC"},
        {"source_type": "AWS::EC2::SecurityGroup", "target_type": "AWS::EC2::VPC"},
        {"source_type": "AWS::EC2::Subnet", "target_type": "AWS::EC2::VPC"}
      ],
      "observed_count": 139,
      "first_seen": "2026-07-15T09:00:00Z",
      "last_seen": "2026-08-06T16:51:00Z"
    },
    "PROTECTED_BY": {
      "description": "Resource is protected by a security group",
      "observed_pairs": [
        {"source_type": "AWS::EC2::Instance", "target_type": "AWS::EC2::SecurityGroup"}
      ],
      "observed_count": 87,
      "first_seen": "2026-07-20T14:30:00Z",
      "last_seen": "2026-08-06T16:51:00Z"
    }
  }
}
```

This schema captures the topology: instances sit in subnets, subnets sit in VPCs, security groups protect instances and belong to VPCs. Each relationship was discovered automatically through learn mode — no manual definition required.
