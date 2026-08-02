# Bona

**Infrastructure asset graph — the Joern for cloud.**

Discovers, maps, and graphs CSP resources via AWS Config.
Produces nodes + edges that feed into Neptune for compliance analysis, impact assessment, and risk scoring.

## Quick Start

```bash
pip install bona

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
into a graph schema compatible with the GraphRAG Toolkit (lexical-graph, document-graph, codeproperty-graph).

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

## Integration

Part of the GraphRAG Toolkit ecosystem:
- **lexical-graph** — foundation (storage, vectors, retrieval)
- **document-graph** — documents (Confluence, PDFs)
- **codeproperty-graph** — code (Joern → vulnerabilities)
- **bona** — infrastructure (AWS Config → compliance)

All produce nodes + edges for the same Neptune instance. Unified queries across all domains.

## License

MIT
