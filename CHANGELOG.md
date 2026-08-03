# Changelog

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
