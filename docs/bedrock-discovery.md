# Bedrock Discovery

## Overview

Bedrock discovery emits **reference nodes** for the AI/ML capabilities available in your account and region — things that *exist in the service catalog* or have been provisioned as platform infrastructure. Your owned assets (knowledge bases, custom models) link to these via edges.

The following providers compose the full Bedrock discovery surface:

| Provider | What It Discovers |
|----------|-------------------|
| `bedrock_models` | Foundation models, providers, modalities, model families, inference profiles |
| `bedrock_agents` | Agents, managed prompts, USES_MODEL and HAS_KB relationships |
| `bedrock_profiles` | Inference profiles (cross-region model routing) |
| `bedrock_platform` | Guardrails, prompt routers, batch inference jobs, automated reasoning policies |
| `bedrock_agentcore` | Agent runtimes, gateways, registries, memories, workload identities |

All providers are registered in the `REFERENCE_PROVIDERS` dict and invoked during enrichment.

## Graph Structure

### Model Decomposition

The `bedrock_models` provider decomposes each foundation model into orthogonal dimensions:

```
                                    ┌──────────────────────────────┐
                                    │  BedrockProvider             │
                                    │  id: "bedrock-provider:      │
                                    │       Anthropic"             │
                     PROVIDED_BY    └──────────────────────────────┘
                    ┌──────────────▶
┌───────────────────┤               ┌──────────────────────────────┐
│  FoundationModel  │  SUPPORTS_    │  BedrockModality             │
│  id: "bedrock-    │  INPUT        │  id: "bedrock-modality:TEXT" │
│  model:anthropic. ├──────────────▶└──────────────────────────────┘
│  claude-3-sonnet" │
│                   │  PRODUCES_    ┌──────────────────────────────┐
│  mantle_available:│  OUTPUT       │  BedrockModality             │
│  true             ├──────────────▶│  id: "bedrock-modality:TEXT" │
│                   │               └──────────────────────────────┘
│                   │  MEMBER_OF    ┌──────────────────────────────┐
│                   ├──────────────▶│  BedrockModelFamily          │
└───────────────────┘               │  id: "bedrock-family:        │
                                    │       anthropic.claude"      │
                                    └──────────────────────────────┘
```

Multiple models share provider, modality, and family nodes — enabling queries like "which models from Anthropic support IMAGE input?"

### Node Types

| Node Type | ID Pattern | Description |
|-----------|------------|-------------|
| `AWS::Bedrock::FoundationModel` | `bedrock-model:{model_id}` | A foundation model available in the region |
| `AWS::Bedrock::Provider` | `bedrock-provider:{name}` | Model provider (Anthropic, Amazon, Meta, etc.) |
| `AWS::Bedrock::Modality` | `bedrock-modality:{modality}` | Input/output modality (TEXT, IMAGE, EMBEDDING) |
| `AWS::Bedrock::ModelFamily` | `bedrock-family:{family}` | Model family (anthropic.claude, amazon.nova) |
| `AWS::Bedrock::InferenceProfile` | `bedrock-profile:{id}` | Cross-region inference profile |
| `AWS::Bedrock::Agent` | ARN or `bedrock-agent:{id}` | A Bedrock Agent |
| `AWS::Bedrock::Prompt` | ARN or `bedrock-prompt:{id}` | A managed prompt with variants |
| `AWS::Bedrock::Guardrail` | ARN or `bedrock-guardrail:{id}` | Content filtering guardrail |
| `AWS::Bedrock::PromptRouter` | ARN or `bedrock-router:{name}` | Intelligent prompt router |
| `AWS::Bedrock::BatchInferenceJob` | ARN or `bedrock-batch:{name}` | Batch model invocation job |
| `AWS::Bedrock::AutomatedReasoningPolicy` | ARN or `bedrock-reasoning:{name}` | Automated reasoning policy |
| `AWS::Bedrock::AgentRuntime` | ARN or `agentcore-runtime:{id}` | AgentCore runtime |
| `AWS::Bedrock::Gateway` | ARN or `agentcore-gateway:{id}` | AgentCore gateway |
| `AWS::Bedrock::Registry` | ARN or `agentcore-registry:{id}` | AgentCore tool/agent registry |
| `AWS::Bedrock::Memory` | ARN or `agentcore-memory:{id}` | AgentCore memory store |
| `AWS::Bedrock::WorkloadIdentity` | ARN or `agentcore-identity:{name}` | AgentCore workload identity |

### Edge Types

| Edge Type | Source | Target | Meaning |
|-----------|--------|--------|---------|
| `PROVIDED_BY` | FoundationModel | Provider | "This model is provided by this vendor" |
| `SUPPORTS_INPUT` | FoundationModel | Modality | "This model accepts this input modality" |
| `PRODUCES_OUTPUT` | FoundationModel | Modality | "This model produces this output modality" |
| `MEMBER_OF` | FoundationModel | ModelFamily | "This model belongs to this family" |
| `USES_MODEL` | Agent / Prompt / KnowledgeBase / BatchJob | FoundationModel | "This resource uses this model" |
| `HAS_KB` | Agent | KnowledgeBase | "This agent has access to this knowledge base" |
| `ROUTES_TO` | PromptRouter | FoundationModel | "This router can route to this model" |
| `ROUTES_VIA` | AgentRuntime | Gateway | "This runtime routes traffic via this gateway" |
| `REGISTERED_IN` | AgentRuntime | Registry | "This runtime is registered in this registry" |
| `RUNS_AS` | AgentRuntime | WorkloadIdentity | "This runtime runs as this workload identity" |

## Bedrock Mantle

The `bedrock_models` provider detects **Mantle compatibility** — whether a model is accessible via the OpenAI-compatible Bedrock surface.

### Detection Logic

A model is Mantle-compatible when its `inferenceTypesSupported` list includes `ON_DEMAND`. The provider sets:

| Property | Type | Description |
|----------|------|-------------|
| `mantle_available` | bool | `true` if ON_DEMAND inference is supported |
| `mantle_qualified_id` | string | OpenAI-style ID (`openai.{model_id}`) for Mantle addressing |
| `mantle_regions` | list[str] | Regions where this model is available via Mantle |

### Multi-Region Detection

After listing models in the primary region, the provider probes key regions (`us-east-1`, `us-east-2`, `us-west-2`, `eu-west-1`) to build a full `mantle_regions` availability map. This enables queries like "which Claude models are available in eu-west-1 via Mantle?"

The multi-region check only runs for models already identified as Mantle-compatible — non-ON_DEMAND models are skipped.

## Agents & Prompts

The `bedrock_agents` provider queries the `bedrock-agent` API to discover:

### Agents

Each agent becomes an `AWS::Bedrock::Agent` node with properties including `agent_id`, `description`, `foundation_model`, and `instruction` (truncated to 200 chars).

Edges emitted:
- **USES_MODEL** — Agent → FoundationModel (the agent's configured foundation model)
- **HAS_KB** — Agent → KnowledgeBase (for each knowledge base associated with the agent's DRAFT version)

### Prompts

Each managed prompt becomes an `AWS::Bedrock::Prompt` node. Prompt variants are inspected to extract model references.

Edges emitted:
- **USES_MODEL** — Prompt → FoundationModel (one edge per unique model across variants)

### Model Usage from KnowledgeBases

The `bedrock_models` provider also discovers **USES_MODEL** edges from KnowledgeBases to their embedding model by inspecting `vectorKnowledgeBaseConfiguration.embeddingModelArn`.

## AgentCore

The `bedrock_agentcore` provider discovers resources from the `bedrock-agentcore-control` API:

| Resource | Node Type | Description |
|----------|-----------|-------------|
| Agent Runtimes | `AWS::Bedrock::AgentRuntime` | Managed compute environments for running agents |
| Gateways | `AWS::Bedrock::Gateway` | Network gateways routing traffic to runtimes |
| Registries | `AWS::Bedrock::Registry` | Tool and agent registries |
| Memories | `AWS::Bedrock::Memory` | Persistent memory stores for agent sessions |
| Workload Identities | `AWS::Bedrock::WorkloadIdentity` | IAM-like identities for agent workloads |

### AgentCore Edges

| Edge | Direction | Logic |
|------|-----------|-------|
| `ROUTES_VIA` | Runtime → Gateway | Each runtime routes through discovered gateways |
| `REGISTERED_IN` | Runtime → Registry | Each runtime is registered in discovered registries |
| `RUNS_AS` | Runtime → WorkloadIdentity | Matched by runtime ID appearing in the identity name |

## Platform

The `bedrock_platform` provider covers broader Bedrock infrastructure:

### Guardrails

Lists all guardrails via `list_guardrails`. Each becomes a node with `guardrail_id`, `description`, `version`, and `status`.

### Prompt Routers

Lists prompt routers via `list_prompt_routers`. Each router node includes `routing_criteria` and `model_count`. For each model in the router's configuration, a **ROUTES_TO** edge is emitted pointing to the corresponding FoundationModel node.

### Batch Inference Jobs

Lists batch invocation jobs via `list_model_invocation_jobs`. Each job becomes a node with `model_id`, `status`, `submitted_at`, and `end_time`. A **USES_MODEL** edge connects the job to its target model.

### Automated Reasoning

Lists automated reasoning policies via `list_automated_reasoning_policies`. Each policy becomes a node with `description` and `version`.

## Configuration

Each Bedrock reference provider can be individually enabled or disabled via `enrichment.supplemental` in `bona.yaml`:

```yaml
enrichment:
  enabled: true
  mode: soft
  supplemental:
    bedrock_models: true        # Foundation models + decomposition graph
    bedrock_agents: true        # Agents and managed prompts
    bedrock_profiles: true      # Inference profiles
    bedrock_platform: true      # Guardrails, routers, batch, reasoning
    bedrock_agentcore: true     # Runtimes, gateways, registries, memories, identities
```

Set any key to `false` to skip that provider during enrichment. By default all providers run when enrichment is enabled.

### Caching

Model and profile data is cached under:

```
~/.bona/cache/{region}/bedrock-models.json
~/.bona/cache/{region}/bedrock-profiles.json
```

Use `--enrich hard` to force fresh API calls and refresh the cache.

## Example Output

### FoundationModel Node

```json
{
  "id": "bedrock-model:anthropic.claude-3-sonnet-20240229-v1:0",
  "node_type": "AWS::Bedrock::FoundationModel",
  "name": "Claude 3 Sonnet",
  "provider": "aws",
  "account_id": "global",
  "region": "us-east-1",
  "resource_type": "AWS::Bedrock::FoundationModel",
  "state": "ACTIVE",
  "properties": {
    "model_id": "anthropic.claude-3-sonnet-20240229-v1:0",
    "model_arn": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-sonnet-20240229-v1:0",
    "model_name": "Claude 3 Sonnet",
    "provider_name": "Anthropic",
    "input_modalities": ["TEXT", "IMAGE"],
    "output_modalities": ["TEXT"],
    "streaming_supported": true,
    "customizations_supported": [],
    "inference_types": ["ON_DEMAND"],
    "lifecycle_status": "ACTIVE",
    "mantle_available": true,
    "mantle_qualified_id": "openai.anthropic.claude-3-sonnet-20240229-v1:0",
    "mantle_regions": ["us-east-1", "us-east-2", "us-west-2", "eu-west-1"]
  }
}
```

### Provider Node

```json
{
  "id": "bedrock-provider:Anthropic",
  "node_type": "AWS::Bedrock::Provider",
  "name": "Anthropic",
  "provider": "aws",
  "account_id": "global",
  "region": "global",
  "resource_type": "AWS::Bedrock::Provider",
  "state": "ACTIVE",
  "properties": {
    "provider_name": "Anthropic"
  }
}
```

### Modality Node

```json
{
  "id": "bedrock-modality:IMAGE",
  "node_type": "AWS::Bedrock::Modality",
  "name": "IMAGE",
  "provider": "aws",
  "account_id": "global",
  "region": "global",
  "resource_type": "AWS::Bedrock::Modality",
  "state": "ACTIVE",
  "properties": {
    "modality": "IMAGE",
    "direction": "input"
  }
}
```

### USES_MODEL Edge (Agent → Model)

```json
{
  "source_id": "arn:aws:bedrock:us-east-1:123456789012:agent/ABCDEFGHIJ",
  "target_id": "bedrock-model:anthropic.claude-3-sonnet-20240229-v1:0",
  "edge_type": "USES_MODEL",
  "provider": "aws",
  "account_id": "123456789012",
  "properties": {
    "relationship": "foundation_model"
  }
}
```

### PROVIDED_BY Edge (Model → Provider)

```json
{
  "source_id": "bedrock-model:anthropic.claude-3-sonnet-20240229-v1:0",
  "target_id": "bedrock-provider:Anthropic",
  "edge_type": "PROVIDED_BY",
  "provider": "aws"
}
```
