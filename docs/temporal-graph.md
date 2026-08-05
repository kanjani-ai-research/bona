# Temporal Graph

The `bona history` command builds a temporal graph — configuration snapshots over time for a single resource, connected by `CHANGED_TO` edges.

## Use Cases

- **Drift detection** — compare `semantic_hash` across snapshots to find when config changed
- **Incident investigation** — correlate config changes with outage timelines
- **Compliance auditing** — prove resource was compliant at a point in time
- **Change attribution** — see which properties changed between snapshots

---

## Usage

```bash
bona history --profile <name> --region <region> \
  --resource-type <type> --resource-id <id> \
  [--limit N] [--output-dir ./path]
```

### Examples

```bash
# Instance configuration history
bona history --profile prod --region us-east-1 \
  --resource-type AWS::EC2::Instance \
  --resource-id i-0abc123def456

# Security group rule changes
bona history --profile prod --region us-east-1 \
  --resource-type AWS::EC2::SecurityGroup \
  --resource-id sg-abc123 \
  --limit 50

# S3 bucket policy changes
bona history --profile prod --region us-east-1 \
  --resource-type AWS::S3::Bucket \
  --resource-id my-bucket
```

---

## How It Works

1. Calls `get_resource_config_history` for the specified resource
2. Each configuration item becomes a **temporal node** with an `@timestamp` ID suffix
3. Successive nodes are linked with **`CHANGED_TO`** edges
4. Per-snapshot relationships (e.g., which SG was attached at that time) are preserved as regular edges

```
   [v3 @ 2026-08-05]        [v2 @ 2026-08-04]        [v1 @ 2026-08-01]
   ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
   │ state: ACTIVE   │─────▶│ state: ACTIVE   │─────▶│ state: ACTIVE   │
   │ hash: a1b2c3d4  │      │ hash: e5f6g7h8  │      │ hash: a1b2c3d4  │
   │ instanceType:   │      │ instanceType:   │      │ instanceType:   │
   │   t3.large      │      │   t3.medium     │      │   t3.large      │
   └─────────────────┘      └─────────────────┘      └─────────────────┘
          │ CHANGED_TO              │ CHANGED_TO
          ▼                         ▼
```

---

## Output Schema

### Temporal Nodes

Each node represents one configuration snapshot:

```json
{
  "id": "arn:aws:ec2:us-east-1:123456789012:instance/i-abc@2026-08-05T10:30:00",
  "node_type": "AWS::EC2::Instance",
  "name": "web-server",
  "stable_id": "aws|123456789012|us-east-1|AWS::EC2::Instance|i-abc@2026-08-05T10:30:00",
  "semantic_hash": "a1b2c3d4",
  "provider": "aws",
  "account_id": "123456789012",
  "region": "us-east-1",
  "resource_type": "AWS::EC2::Instance",
  "state": "ACTIVE",
  "configuration_capture_time": "2026-08-05 10:30:00+00:00",
  "configuration_state_id": "1722856200123",
  "version": "v3",
  "instanceType": "t3.large",
  "vpcId": "vpc-abc123"
}
```

Key temporal fields:
- **`version`** — sequential version label (v1 = oldest)
- **`configuration_capture_time`** — when Config recorded this snapshot
- **`configuration_state_id`** — Config's internal state identifier
- **`semantic_hash`** — changes when configuration changes

### CHANGED_TO Edges

Link successive snapshots:

```json
{
  "source_id": "arn:aws:.../i-abc@2026-08-05T10:30:00",
  "target_id": "arn:aws:.../i-abc@2026-08-04T15:22:00",
  "edge_type": "CHANGED_TO",
  "stable_id": "aws|123456789012|EDGE|CHANGED_TO|i-abc@2026-08-05T10:30:00→prev"
}
```

### Snapshot Relationship Edges

Each snapshot's relationships at that point in time:

```json
{
  "source_id": "arn:aws:.../i-abc@2026-08-05T10:30:00",
  "target_id": "sg-xyz789",
  "edge_type": "IS_ASSOCIATED_WITH",
  "stable_id": "aws|123456789012|EDGE|IS_ASSOCIATED_WITH|i-abc→sg-xyz789@2026-08-05T10:30:00",
  "snapshot_time": "2026-08-05 10:30:00+00:00"
}
```

---

## Console Output

```
  ✅ 8 snapshots, 15 edges (temporal + relationship)
  📁 ./bona-output/nodes.json
  📁 ./bona-output/edges.json

  Timeline:
    v8  2026-08-05T10:30:00  [ACTIVE]  hash=a1b2c3d4
    v7  2026-08-04T15:22:00  [ACTIVE]  hash=e5f6g7h8
    v6  2026-08-03T09:10:00  [ACTIVE]  hash=a1b2c3d4
    v5  2026-08-02T14:00:00  [ACTIVE]  hash=9i0j1k2l
    v4  2026-07-30T11:15:00  [ACTIVE]  hash=a1b2c3d4
    v3  2026-07-28T08:45:00  [ACTIVE]  hash=3m4n5o6p
    v2  2026-07-25T16:30:00  [ACTIVE]  hash=a1b2c3d4
    v1  2026-07-20T09:00:00  [DISCOVERED]  hash=7q8r9s0t
```

---

## Detecting Drift

Compare `semantic_hash` values across snapshots:

```bash
# Find snapshots where config changed (hash differs from previous)
jq '[.[] | {version, hash: .semantic_hash, time: .configuration_capture_time}]
    | [range(1; length)] as $i
    | select(.[$i].hash != .[$i-1].hash)
    | .[$i]' nodes.json
```

Same hash = same config. Different hash = something changed. Use `diff` on the full node objects to see exactly what changed.

---

## Combining with Export

Run `export` for the current state and `history` for change tracking:

```bash
# Current full graph
bona export --profile prod --region us-east-1 --output-dir ./current/

# History for a suspicious resource
bona history --profile prod --region us-east-1 \
  --resource-type AWS::EC2::SecurityGroup \
  --resource-id sg-abc123 \
  --output-dir ./history-sg/
```
