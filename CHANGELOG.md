# Changelog

## [Unreleased]

### Changed (CI only, no image change)
- GitHub Actions jobs in `.github/workflows/publish.yml` run on the CodeBuild-hosted runner `aigp-actions` in AWS instead of
  GitHub-hosted `ubuntu-latest` (ADR-0216 am. 1 A9; guard-infra `bootstrap/79-ci-actions-runner.sh`). Rollback:
  `runs-on: ubuntu-latest`.

## [0.2.0] - 2026-08-22

### Added
- Edge completeness: `_ensure_edge_targets()` creates stub nodes for
  relationship targets not in the discovered resources list
- Schema generation: `generate_schema_package()` produces a Metis-compatible
  schema_extension YAML declaring all discovered edge kinds
- ID pattern inference for stub nodes (vpc-*, subnet-*, sg-*, etc.)

### Fixed
- Relative import path in `providers/aws/aws_provider.py`

## [0.1.0] - 2026-08-03

### Added
- Initial public release
- AWS Config resource discovery and enumeration
- Infrastructure asset graph generation (nodes + edges)
- Compliance state checking via AWS Config
- CLI interface (`bona discover`, `bona services`, `bona compliance`)
- Pydantic-based schema: AssetNode, AssetEdge, DiscoveryResult
- Provider architecture supporting AWS, Azure, GCP adapters

### Dependencies
- boto3>=1.35.0
- pydantic>=2.0
