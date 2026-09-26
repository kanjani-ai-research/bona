# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] — 2026-09-26

Merges the `kanjani-ai-research/bona` line (published as 0.2.0) back onto 0.7.0.

### Added

- Edge completeness (SPEC-004): `_ensure_edge_targets()` creates stub nodes for
  relationship targets not in the discovered resources list
- ID pattern inference for stub nodes (vpc-*, subnet-*, sg-*, etc.)
- Schema generation: `generate_schema_package()` produces a Metis-compatible
  schema_extension YAML declaring all discovered edge kinds
- `graph_writer` module — schema-validated Neptune writes

## [0.7.0] — 2026-08-07

### Added

- **`bona diff`** — drift detection between exports using stable_id + semantic_hash
  - Detects added/removed/modified nodes and edges
  - Per-property diff for modified resources
  - CLI: `bona diff <old> <new> [--format text|json] [--only added|removed|modified]`
- **GCP Provider** — Cloud Asset Inventory discovery
  - Discovers all resources in a GCP project/folder/org
  - Maps GCP asset types to `GCP::Service::Resource` node format
  - Supports Python client (ADC) or gcloud CLI fallback
  - `credentials` config field for service account key auto-loading
- **GCP Enrichment** — IAM + Recommender insights (no org required)
  - `discover_iam_edges()`: extracts IAM bindings as HAS_ROLE edges
  - `discover_recommender_insights()`: over-permissioned accounts, idle resources
  - `discover_with_enrichment()`: full pipeline (resources + IAM + SCC + Recommender)
  - `discover_scc_findings()`: SCC findings (gracefully skipped without org)
- **Graph Tree** — configurable CSP hierarchy nodes (`frameworks/graph_tree.yaml`)
  - Per-CSP layers: Cloud::Root → Provider → Account/Project → Region
  - Resources attach via BELONGS_TO edges
  - Global resources (IAM, S3) attach to Account instead of Region
  - User-overridable via `./graph_tree.yaml` or `~/.bona/graph_tree.yaml`
  - `custom_layers` extension point for Organization/BusinessUnit/Environment
  - Azure hierarchy pre-defined (Subscription → ResourceGroup)
- `GCPConfig` dataclass with `project_id`, `credentials`, `asset_types`, `content_type`

### Changed

- AWS and GCP providers now emit hierarchy nodes automatically
- `bona.yaml.example` updated with GCP config section

## [0.6.1] — 2026-08-07

(Patch release with all v0.6.0 features properly packaged)

## [0.6.0] — 2026-08-07

### Added

- **Custom MCP server support** — configure multiple MCP servers for LLM enrichment tool use (AWS, CMDB, custom APIs)
- `MCPPool` class — aggregates tools from multiple servers, routes calls by tool name, supports `tool_filter` per server
- `MCPServerEntry` config — per-server `name`, `server_url`, `command`, `enabled`, `tool_filter`
- **PromptsConfig wiring** — LLM enrichment loads prompts from YAML templates (with Bedrock managed prompt IDs) instead of hardcoded inline strings
- `_resolve_prompt()` helper — loads managed prompts or falls back to inline (backward compatible)
- **MCP agentic tool-use loop** — `_call_bedrock_with_tools()` runs Converse API with toolConfig, iterates up to 5 rounds
- **MCP tool-use on all enrichment tasks** — classify, validate, and describe all use MCP tools when available
- **MCP Discovery Provider** — generic config-driven provider discovers any AWS service via MCP (zero plugin code)
- `MCPProvider` with `mcp.discovery[]` config — supports items_key, detail fetches, pagination, auto-ID detection
- **Neptune CSV export** — `bona export --format neptunecsv` produces `~id`, `~label`, `~from`, `~to` headers for Neptune bulk loader
- Property type annotations in Neptune CSV (`:String`, `:Int`, `:Bool`, `:Double`)
- **Parallel provider execution** — `run_providers_parallel()` in `workers.py` runs multiple providers via ThreadPoolExecutor
- Integration test suite for live MCP server (`test_mcp_integration.py`)
- 55 new unit tests across 4 test files

### Changed

- `MCPConfig` extended with `servers: list[MCPServerEntry]` and `discovery: list[dict]`
- `llm_enrich()` accepts `prompts_config` and `mcp_config` parameters
- All three LLM tasks (`classify`, `validate`, `describe`) accept MCP client/tools params
- `from_dict()` now parses `mcp.servers`, `mcp.discovery`, and `prompts` config sections
- CLI format choices now include `neptunecsv`

### Removed

- Audit Manager backlog item (service deprecated by AWS, April 2026)

## [Unreleased]

### Added

- `bona schema` command — download CloudFormation type schemas to local cache
- `--enrich [soft|hard]` flag on `bona export` — adds TypeDefinition reference nodes + INSTANCE_OF edges
- Configuration file support (`bona.yaml` / `bona.json`) with include/exclude patterns for enrichment
- Generic logging module — configurable level and output path (local file or S3)
- `--config` global flag for explicit config file path
- Supplemental data provider registry (EC2 instance specs as first provider)
- S3 upload support for type schema cache (`bona schema --s3-uri`)
- Graph schema with learn mode (`--learn`) — auto-maintains node/edge type registry at `~/.bona/graph-schema.json`
- Semver versioning on graph schema (MINOR for new types/pairs, PATCH for observation updates)
- Config backfill — automatically uses `batch_get_resource_config` when SQL query returns empty configurations
- Relationship learner — auto-discovers edge inference rules from property values (`~/.bona/relationship-rules.json`)
- LLM enrichment via Bedrock (`--llm-enrich`) — classifies properties, validates rules, generates descriptions
- Configurable LLM settings in `bona.yaml` — per-task model selection, budget control, temperature, system prompt
- Bedrock reference providers — full model graph decomposition (119 models → Provider, Modality, Family nodes + edges)
- Bedrock Agents + Prompts discovery with USES_MODEL and HAS_KB edges
- Bedrock Platform resources (Guardrails, Prompt Routers, Batch Jobs, Automated Reasoning)
- Bedrock AgentCore discovery (Runtimes, Gateways, Registries, Memories, Workload Identities)
- Bedrock Mantle availability detection on ON_DEMAND models with multi-region support
- Plugin system via pluggy — external packages can register providers via entry_points
- Hook specs: `bona_reference_providers`, `bona_supplemental_providers`, `bona_relationship_classifiers`
- Example plugin at `examples/bona-provider-example/` with CostCenter provider and URL classifier
- Descriptions cache (`~/.bona/descriptions.json`) — persists LLM-generated descriptions across runs
- S3 upload for all learning artifacts via `learning.s3_uri` config
- `learning` config section for centralized control of artifact storage

### Changed

- Unified enrichment pipeline replaces per-service enrichment files
- Logging uses structured module (`bona.logging`) instead of print statements
- Relationship learning now has two layers: pattern matching + LLM classification
- Reference providers are a new category separate from supplemental providers
- Providers now loaded dynamically via pluggy plugin manager (backward compatible)

## [0.1.0] — 2026-08-05

### Added

- **`bona export`** — full Asset Property Graph export from AWS Config
  - SQL-based discovery via `select_resource_config`
  - Fallback to `list_discovered_resources` + `batch_get_resource_config`
  - Resource type filtering with glob support (`AWS::EC2::*`)
  - Multi-region export (comma-separated `--region`)
  - Multi-account via `--aggregator` (Config Aggregator)
  - Compliance findings as graph nodes (`--include-compliance`)
  - S3 upload (`--s3-uri`)
  - Safety cap (`--max-resources`)
- **`bona history`** — temporal graph export
  - Configuration snapshots over time for a single resource
  - `CHANGED_TO` edges linking successive snapshots
  - Per-snapshot relationship edges
  - Semantic hash for drift detection between versions
- **`bona types`** — list resource types tracked by AWS Config
- **Schema model** — Joern-compatible `AssetNode` / `AssetEdge` dataclasses
  - `stable_id` for deterministic delta reconciliation
  - `semantic_hash` (SHA-256) for config drift detection
  - Flat property serialization matching Joern's `cpg_exporter.py`
- **Edge inference** — automatic relationship creation from config properties
  - VPC/Subnet membership, IAM role usage, KMS encryption, load balancer routing
- **Output format** — `nodes.json`, `edges.json`, `manifest.json`
- **Compatibility** — works with Joern, Neptune, GraphRAG Toolkit, Neo4j, document-graph format

[0.1.0]: https://github.com/evanerwee/bona/releases/tag/v0.1.0
