# bona-provider-example

Example plugin for [bona](https://github.com/evanerwee/bona) — demonstrates how to add custom providers to the Asset Property Graph.

## What this does

- Adds a `cost_tags` reference provider that discovers CostCenter tags and creates graph nodes
- Adds a URL relationship classifier that detects HTTPS connections as edges

## Install

```bash
cd examples/bona-provider-example
pip install -e .
```

## Usage

Once installed, the plugin is auto-discovered by bona:

```bash
bona export --profile myprofile --region us-east-1 --enrich
```

Your custom provider runs alongside bona's built-in providers. No configuration needed.

## How it works

1. The `pyproject.toml` declares an entry point:
   ```toml
   [project.entry-points."bona"]
   example = "bona_provider_example.plugin"
   ```

2. Bona's plugin manager discovers this at startup via `pluggy.load_setuptools_entrypoints("bona")`

3. The plugin module exports a class with `@hookimpl` decorated methods:
   ```python
   from bona.plugins import hookimpl

   class BonaProviderExample:
       @hookimpl
       def bona_reference_providers(self) -> dict:
           return {"cost_tags": my_provider_function}
   ```

## Writing your own provider

A provider function has this signature:

```python
def my_provider(
    session: boto3.Session,
    region: str,
    resources: list[AssetNode],
    refresh: bool = False,
) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Discover resources and return (nodes, edges)."""
    ...
```

It receives:
- `session` — boto3 session (use for AWS API calls)
- `region` — AWS region being exported
- `resources` — already-discovered asset nodes (from Config)
- `refresh` — True if user passed `--refresh-cache`

It returns:
- A list of new nodes to add to the graph
- A list of new edges to add to the graph

## Available hooks

| Hook | Purpose | Returns |
|------|---------|---------|
| `bona_reference_providers` | Register reference providers (service catalog data) | `dict[name, fn]` |
| `bona_supplemental_providers` | Register per-resource-type enrichers | `dict[resource_type, fn]` |
| `bona_relationship_classifiers` | Register property → edge classifiers | `list[fn]` |
