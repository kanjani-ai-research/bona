# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
