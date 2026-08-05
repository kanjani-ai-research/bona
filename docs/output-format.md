# Output Format

Bona exports a property graph as three JSON files compatible with Joern's `cpg_exporter.py` format.

## Files

| File | Description |
|------|-------------|
| `nodes.json` | Array of resource nodes (flat JSON objects) |
| `edges.json` | Array of relationship edges |
| `manifest.json` | Export metadata (counts, timing, account info) |

---

## nodes.json

### Schema

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | ARN (globally unique identifier) |
| `node_type` | string | AWS resource type (e.g. `AWS::EC2::Instance`) |
| `name` | string | Resource name |
| `stable_id` | string | Deterministic identity for delta reconciliation |
| `semantic_hash` | string | SHA-256 prefix (16 chars) of config — detects drift |
| `provider` | string | Always `"aws"` |
| `account_id` | string | AWS account ID |
| `region` | string | AWS region |
| `resource_type` | string | Same as `node_type` |
| `state` | string | `ACTIVE`, `DELETED`, or `DISCOVERED` |
| `tags` | object | Key-value tag map (if present) |
| `configuration_capture_time` | string | ISO timestamp of Config snapshot |
| *(properties)* | varies | Flattened configuration keys |

### stable_id Format

```
{provider}|{account_id}|{region}|{node_type}|{short_resource_id}
```

Example: `aws|123456789012|us-east-1|AWS::EC2::Instance|i-0abc123def456`

### semantic_hash

First 16 characters of SHA-256 over `{id, node_type, state, properties}`. If it changes between exports, the resource config has drifted.

### Example Node

```json
{
  "id": "arn:aws:ec2:us-east-1:123456789012:instance/i-0abc123def456",
  "node_type": "AWS::EC2::Instance",
  "name": "web-server-prod",
  "stable_id": "aws|123456789012|us-east-1|AWS::EC2::Instance|i-0abc123def456",
  "semantic_hash": "7f3a9c1b2e4d6f08",
  "provider": "aws",
  "account_id": "123456789012",
  "region": "us-east-1",
  "resource_type": "AWS::EC2::Instance",
  "state": "ACTIVE",
  "tags": {"Name": "web-server-prod", "Environment": "production"},
  "instanceType": "t3.medium",
  "vpcId": "vpc-abc123",
  "subnetId": "subnet-def456"
}
```

### Finding Nodes

With `--include-compliance`, findings appear as nodes with `node_type: "Finding"`:

```json
{
  "id": "compliance:AWS::S3::Bucket/my-bucket",
  "node_type": "Finding",
  "name": "NON_COMPLIANT: AWS::S3::Bucket/my-bucket",
  "state": "NON_COMPLIANT",
  "target_resource_id": "my-bucket",
  "failing_rules": ["s3-bucket-ssl-requests-only"],
  "failing_rule_count": 1
}
```

---

## edges.json

### Schema

| Field | Type | Description |
|-------|------|-------------|
| `source_id` | string | Source node ID (ARN) |
| `target_id` | string | Target node ID (ARN or resource ID) |
| `edge_type` | string | Relationship type (uppercase) |
| `stable_id` | string | Deterministic edge identity |

### Edge Types

**From Config relationships:**

| Edge Type | Meaning |
|-----------|---------|
| `IS_CONTAINED_IN` | Hierarchical containment (subnet in VPC) |
| `CONTAINS` | Reverse containment |
| `IS_ATTACHED_TO` | Attachment (volume to instance) |
| `IS_ASSOCIATED_WITH` | Association (SG to instance) |

**Inferred from properties:**

| Edge Type | Source Property |
|-----------|----------------|
| `IN_VPC` | `vpcId` |
| `IN_SUBNET` | `subnetId` |
| `USES_ROLE` | `roleArn`, `executionRoleArn`, `taskRoleArn`, `iamInstanceProfile` |
| `ENCRYPTED_BY` | `kmsKeyId`, `kmsMasterKeyId` |
| `ROUTES_TO` | `targetGroupArn` |
| `ATTACHED_TO` | `loadBalancerArn` |
| `IN_CLUSTER` | `clusterArn`, `dbClusterIdentifier` |

**Temporal:** `CHANGED_TO` — links successive config snapshots (history command)

### Example Edge

```json
{
  "source_id": "arn:aws:ec2:us-east-1:123456789012:instance/i-0abc123",
  "target_id": "arn:aws:ec2:us-east-1:123456789012:security-group/sg-xyz789",
  "edge_type": "IS_ASSOCIATED_WITH",
  "stable_id": "aws|123456789012|EDGE|IS_ASSOCIATED_WITH|i-0abc123→sg-xyz789"
}
```

---

## manifest.json

```json
{
  "provider": "aws",
  "account_id": "123456789012",
  "region": "us-east-1",
  "node_count": 847,
  "edge_count": 2341,
  "finding_count": 12,
  "duration_ms": 15420,
  "nodes_file": "./bona-output/nodes.json",
  "edges_file": "./bona-output/edges.json"
}
```

---

## Loading into Tools

### jq

```bash
# Count by type
jq 'group_by(.node_type) | map({type: .[0].node_type, count: length})' nodes.json

# Find edges from a resource
jq --arg arn "arn:aws:..." '.[] | select(.source_id == $arn)' edges.json
```

### Python

```python
import pandas as pd
nodes = pd.read_json("bona-output/nodes.json")
nodes.groupby("node_type").size().sort_values(ascending=False)
```
