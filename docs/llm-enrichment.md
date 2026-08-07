# LLM Enrichment

## Overview

LLM enrichment uses **Amazon Bedrock** (Claude via the Converse API) to enhance the Asset Property Graph with intelligence that pattern-matching alone cannot provide. It performs three tasks:

1. **Classify** — determines which node properties are relationships to other resources and suggests edge types.
2. **Validate** — reviews pattern-learned relationship candidates and corrects or promotes them.
3. **Describe** — generates concise, human-readable descriptions for nodes that lack one.

LLM enrichment runs as a post-processing step after standard export and relationship learning. It augments — never replaces — the pattern-learned rules.

## Three Tasks

### Classify (Property → Edge)

The LLM examines sampled node properties and classifies each as either a **relationship** (references another resource) or an **attribute** (describes the resource itself). For relationships, it suggests:

- `target_type` — the AWS resource type being referenced
- `edge_type` — an UPPER_SNAKE_CASE label (e.g., `IN_VPC`, `ASSUMES_ROLE`)
- `confidence` — 0.0–1.0 score

Only classifications with confidence ≥ 0.7 are added as candidates. Those with confidence ≥ 0.9 are auto-promoted (edges created immediately).

Nodes are sampled one per type (up to `max_nodes_per_batch`) to maximize type diversity per API call.

### Validate (Correct Candidates)

The LLM reviews unpromoted relationship candidates from the pattern learner and determines:

- Whether the candidate is a real relationship
- Whether the edge type and target type are correct (suggests corrections if not)
- Whether the candidate should be promoted to create edges automatically

Invalid candidates (e.g., self-references, false positives) are removed from the rule set.

### Describe (Generate Summaries)

The LLM generates one-sentence descriptions for nodes that lack a `description` property. Descriptions focus on the resource's purpose or role rather than restating the type name.

Descriptions are cached locally and persist across runs (see [Descriptions Cache](#descriptions-cache) below).

## Configuration

Add an `llm` section to `bona.yaml`:

```yaml
llm:
  enabled: true
  model_id: us.anthropic.claude-sonnet-4-5-20250929-v1:0
  region: us-east-1                    # empty = use export region
  max_nodes_per_batch: 20
  max_candidates_per_call: 10
  temperature: 0.0
  budget_max_calls: 10
  system_prompt: "You are an AWS infrastructure analyst specializing in resource relationships and graph topology."
  tasks:
    classify: us.anthropic.claude-sonnet-4-5-20250929-v1:0
    validate: us.anthropic.claude-sonnet-4-5-20250929-v1:0
    describe: us.anthropic.claude-haiku-3-5-20250929-v1:0
```

### Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `llm.enabled` | bool | `true` | Master switch for LLM enrichment |
| `llm.model_id` | string | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Default Bedrock model ID (used when per-task model is empty) |
| `llm.region` | string | `""` | Bedrock region; empty means use the export region |
| `llm.max_nodes_per_batch` | int | `20` | Max nodes sampled per classify/describe call |
| `llm.max_candidates_per_call` | int | `10` | Max relationship candidates sent to validate |
| `llm.temperature` | float | `0.0` | LLM temperature (0.0 = deterministic) |
| `llm.budget_max_calls` | int | `10` | Maximum total Bedrock API calls per run |
| `llm.system_prompt` | string | *(see above)* | System prompt prepended to every LLM call |
| `llm.tasks.classify` | string | `""` | Model override for classify task |
| `llm.tasks.validate` | string | `""` | Model override for validate task |
| `llm.tasks.describe` | string | `""` | Model override for describe task |

## Per-Task Model Selection

Different tasks have different cost/quality tradeoffs:

| Task | Recommended Model | Rationale |
|------|-------------------|-----------|
| **classify** | Sonnet (expensive) | Relationship classification requires strong reasoning about AWS resource semantics |
| **validate** | Sonnet (expensive) | Correcting edge types needs deep AWS knowledge |
| **describe** | Haiku (cheap) | Generating short descriptions is a simpler task that a smaller model handles well |

Set per-task models in the `tasks` block. An empty string falls back to `model_id`.

```yaml
llm:
  model_id: us.anthropic.claude-sonnet-4-5-20250929-v1:0   # default / classify / validate
  tasks:
    classify: ""                                            # uses model_id
    validate: ""                                            # uses model_id
    describe: us.anthropic.claude-haiku-3-5-20250929-v1:0   # cheaper model for descriptions
```

## Budget Control

The `budget_max_calls` field caps the total number of Bedrock API calls per export run. The three tasks execute in order — classify, validate, describe — and each consumes one call. If the budget is exhausted, remaining tasks are skipped.

**Cost considerations:**

- Each call sends a batch (up to `max_nodes_per_batch` or `max_candidates_per_call` items), so a single call covers many resources.
- With defaults (`budget_max_calls: 10`), a typical run uses 3 calls (one per task).
- Set `budget_max_calls: 0` to effectively disable LLM enrichment without removing config.
- Monitor costs via CloudWatch Bedrock metrics or AWS Cost Explorer.

## Relationship Learning Integration

LLM-classified rules join the same rule store as pattern-learned rules:

1. **Pattern learner** observes property values that look like ARNs, IDs, or names of other resources and creates candidates with `pattern: "arn"`, `"id-suffix"`, etc.
2. **LLM classify** adds new candidates with `pattern: "llm-classified"` for properties the pattern learner missed.
3. **LLM validate** reviews unpromoted candidates (from either source) and may promote, correct, or remove them.

After both passes, the merged rule set is saved to `~/.bona/relationship-rules.json` (or the configured path/S3 location). On subsequent runs, promoted rules create edges automatically without calling the LLM again.

```
Pattern Learner         LLM Classify
     │                       │
     ▼                       ▼
┌─────────────────────────────────┐
│   Relationship Rules Store      │
│   (~/.bona/relationship-rules.json) │
└─────────────────────────────────┘
              │
              ▼
         LLM Validate
              │
              ▼
     Promoted rules → edges
```

## Descriptions Cache

Generated descriptions are persisted at:

```
~/.bona/descriptions.json
```

### Cache format

```json
{
  "last_updated": "2026-08-07T12:00:00Z",
  "count": 47,
  "descriptions": {
    "arn:aws:ec2:us-east-1:123456789012:vpc/vpc-abc123": "Production VPC hosting the main application workloads",
    "arn:aws:s3:::my-data-lake": "Central data lake bucket for analytics pipelines"
  }
}
```

### Behavior

- On each run, existing descriptions are loaded first. The LLM is only called for nodes **not already cached**.
- New descriptions are merged (without overwriting) and saved back.
- If `learning.s3_uri` is configured, the cache is also uploaded to S3 for team sharing.
- Override the local path with `learning.descriptions_path` in `bona.yaml`.

## CLI Usage

### Basic LLM Enrichment

```bash
bona export --region us-east-1 --llm-enrich
```

### Combining with Learn and Enrich

```bash
# Full pipeline: type schemas + relationship learning + LLM enrichment
bona export --region us-east-1 --enrich soft --learn --llm-enrich
```

| Flag | Effect |
|------|--------|
| `--enrich soft` | Attach TypeDefinition nodes and INSTANCE_OF edges (from cache) |
| `--learn` | Run pattern-based relationship learning |
| `--llm-enrich` | Run LLM classify → validate → describe |

The flags are independent and combinable. `--llm-enrich` loads the current relationship rules, enriches them via LLM, and saves the updated rules back — so it benefits from (but does not require) `--learn` having run previously.

## Example

Given a Subnet node with property `vpcId: "vpc-0abc123def456"`, the LLM enrichment discovers:

### Classify Output

The LLM identifies `vpcId` as a relationship:

```json
{
  "source_type": "AWS::EC2::Subnet",
  "property_name": "vpcId",
  "is_relationship": true,
  "target_type": "AWS::EC2::VPC",
  "edge_type": "IN_VPC",
  "confidence": 0.95
}
```

This creates a promoted rule (`confidence ≥ 0.9`), which immediately produces `IN_VPC` edges from every Subnet to its VPC.

### Describe Output

```json
{
  "arn:aws:ec2:us-east-1:123456789012:subnet/subnet-abc123": "Private application subnet in us-east-1a for backend services",
  "arn:aws:ec2:us-east-1:123456789012:vpc/vpc-0abc123def456": "Production VPC with 10.0.0.0/16 CIDR hosting all application workloads"
}
```

### Resulting Graph Edges

```
┌─────────────────────┐       IN_VPC       ┌─────────────────────┐
│  AWS::EC2::Subnet   │───────────────────▶│  AWS::EC2::VPC      │
│  subnet-abc123      │                     │  vpc-0abc123def456  │
│  desc: "Private     │                     │  desc: "Production  │
│   application subnet│                     │   VPC with 10.0..."│
│   in us-east-1a..." │                     └─────────────────────┘
└─────────────────────┘
```
