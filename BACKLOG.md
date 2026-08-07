# Bona Backlog

## Next Up

- [ ] Wire PromptsConfig into LLM enrichment pipeline (use Bedrock managed prompts when configured)
- [ ] MCP integration in LLM enrichment (Claude calls AWS APIs via MCP tools during reasoning)
- [ ] Custom MCP server support for enrichment (user's own tools)
- [ ] Enable + test Audit Manager in lab account
- [ ] Parallel provider execution using workers.py ThreadPoolExecutor
- [ ] `bona export --format neo4jcsv` tested with actual Neptune import

## Future Plugins

- [ ] `bona-provider-control-tower` — landing zones, enabled controls, baselines
- [ ] `bona-provider-organizations` — OUs, accounts, SCPs
- [ ] `bona-provider-azure` — Azure Resource Graph provider
- [ ] `bona-provider-gcp` — GCP Asset Inventory provider
- [ ] `bona-provider-k8s` — Kubernetes API provider

## Enhancements

- [ ] `bona diff` — compare two exports, detect drift (stable_id + semantic_hash)
- [ ] `bona watch` — scheduled runs, detect changes over time
- [ ] Typer CLI refactor (from argparse)
- [ ] Docker Hub published image
- [ ] Graph schema strict mode (reject unknown node/edge types)
- [ ] Multi-account schema merge (combine learned schemas across accounts)
- [ ] OSCAL profile support (FedRAMP baselines as OSCAL profiles)
- [ ] Compliance score trending (store scores over time, detect regression)

## Documentation

- [ ] `docs/plugins.md` — full plugin development guide
- [ ] `docs/mcp-integration.md` — MCP server setup and usage
- [ ] `docs/compliance-pipeline.md` — full compliance flow (framework → scan → OSCAL → KB → LLM)
- [ ] Architecture Decision Records (ADRs)
