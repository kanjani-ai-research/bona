# Bona

[![PyPI version](https://img.shields.io/pypi/v/bona.svg)](https://pypi.org/project/bona/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Infrastructure asset graph — the Joern for cloud.**

Discovers, maps, and graphs CSP resources via AWS Config.
Produces nodes + edges that feed into Neptune for compliance analysis, impact assessment, and risk scoring.

> **Standalone package. No dependency on graphrag-toolkit.**

## Installation

```bash
pip install bona
```

## Dependencies

Bona has minimal dependencies:
- `boto3>=1.35.0` — AWS SDK for resource discovery
- `pydantic>=2.0` — Data validation and schema models

Bona is a standalone package. It does not depend on `graphrag-document-graph`, `graphrag-codeproperty-graph`, or `graphrag-toolkit-lexical-graph`.

## Quick Start

```bash
# Discover all resources in an account
bona discover --account 123456789012 --region us-east-1

# Enumerate AWS services (for recipe generation)
bona services

# Check compliance state
bona compliance --account 123456789012
```

## Python API

```python
from bona import AWSProvider, DiscoveryPipeline

provider = AWSProvider(account_id="123456789012", region="us-east-1")
result = provider.discover()

print(result.summary())
# → Discovered 142 resources, 287 relationships, 3 findings in 4521ms

for resource in result.resources:
    print(f"{resource.resource_type}: {resource.name}")

for edge in result.relationships:
    print(f"{edge.source_id} --{edge.edge_type}--> {edge.target_id}")
```

## Architecture

```
AWS Config (already running) → Bona reads → normalizes → outputs graph schema
                                                              ↓
                                                    Neptune (via AI-LENS)
```

Bona is thin. AWS Config does the heavy lifting. Bona just transforms the output
into a graph schema compatible with the GraphRAG Toolkit ecosystem.

## Package Structure

```
bona/
  ├── providers/        AWS, Azure, GCP, Application adapters
  │   └── aws/          AWS Config + Resource Explorer + Inspector
  ├── pipeline/         discover → transform → load → enrich
  ├── schema/           AssetNode, AssetEdge, DiscoveryResult
  ├── query/            graph traversal, compliance, impact analysis
  └── cli.py            standalone CLI tool
```

## Dependency Chain

```
bona (standalone)
├── boto3>=1.35.0
└── pydantic>=2.0
```

Bona is independent. It produces graph-compatible output but does not import or require any graphrag packages.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and PR guidelines.

## License

MIT — see [LICENSE](LICENSE) for details.
