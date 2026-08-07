"""Bona CLI — Asset Property Graph from AWS Config.

Usage:
    bona export --profile nw --region us-east-1
    bona export --profile nw --region us-east-1 --output-dir ./my-apg/
    bona export --profile nw --region us-east-1 --s3-uri s3://bucket/prefix/
    bona export --profile nw --types "AWS::EC2::*,AWS::S3::*"
"""

import argparse
import json
import logging
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="bona",
        description="Asset Property Graph — the Joern for cloud infrastructure",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    parser.add_argument("--config", default="", help="Path to bona config file (JSON or YAML). Searches ./bona.yaml, ~/.bona/config.yaml if not set")

    subparsers = parser.add_subparsers(dest="command")

    # export command
    export_parser = subparsers.add_parser("export", help="Export Asset Property Graph (nodes.json + edges.json)")
    export_parser.add_argument("--profile", default="", help="AWS profile name (from ~/.aws/credentials)")
    export_parser.add_argument("--region", default="us-east-1", help="AWS region (comma-separated for multi-region)")
    export_parser.add_argument("--output-dir", default="./bona-output", help="Local output directory (default: ./bona-output)")
    export_parser.add_argument("--s3-uri", default="", help="S3 URI for upload (e.g. s3://bucket/prefix/). Requires explicit opt-in.")
    export_parser.add_argument("--types", default="", help="Resource type filter (comma-separated, supports globs like AWS::EC2::*)")
    export_parser.add_argument("--max-resources", type=int, default=10000, help="Safety cap on resources (default: 10000)")
    export_parser.add_argument("--include-compliance", action="store_true", help="Include Config rule compliance findings as nodes")
    export_parser.add_argument("--aggregator", default="", help="Config aggregator name for multi-account queries")
    export_parser.add_argument("--refresh-cache", action="store_true", help="Force refresh of cached data (instance types, etc.) from AWS")
    export_parser.add_argument("--enrich", nargs="?", const="soft", choices=["soft", "hard"], default=None, help="Enrich nodes with type schema validation (soft=cached, hard=re-fetch). Default: disabled. If flag given without value: soft")
    export_parser.add_argument("--learn", action="store_true", help="Learn mode: update graph schema (node/edge types) from discoveries")
    export_parser.add_argument("--llm-enrich", action="store_true", help="Use Bedrock LLM to classify properties, validate relationships, generate descriptions")
    export_parser.add_argument("--format", choices=["json", "neo4jcsv", "graphml", "dot"], default="json", help="Output format (default: json)")

    # cache command
    cache_parser = subparsers.add_parser("cache", help="Pre-populate local cache (instance types, etc.)")
    cache_parser.add_argument("--profile", default="", help="AWS profile name")
    cache_parser.add_argument("--region", default="us-east-1", help="AWS region")

    # history command
    history_parser = subparsers.add_parser("history", help="Export temporal graph — resource config over time")
    history_parser.add_argument("--profile", default="", help="AWS profile name")
    history_parser.add_argument("--region", default="us-east-1", help="AWS region")
    history_parser.add_argument("--resource-type", required=True, help="Resource type (e.g. AWS::EC2::Instance)")
    history_parser.add_argument("--resource-id", required=True, help="Resource ID")
    history_parser.add_argument("--limit", type=int, default=20, help="Max history items (default: 20)")
    history_parser.add_argument("--output-dir", default="./bona-output", help="Local output directory")

    # services command (list discoverable resource types)
    svc_parser = subparsers.add_parser("types", help="List resource types tracked by AWS Config")
    svc_parser.add_argument("--profile", default="", help="AWS profile name")
    svc_parser.add_argument("--region", default="us-east-1", help="AWS region")

    # schema command (download CloudFormation type schemas to cache, optionally upload to S3)
    schema_parser = subparsers.add_parser("schema", help="Download CloudFormation type schemas to local cache (and optionally S3)")
    schema_parser.add_argument("--profile", default="", help="AWS profile name")
    schema_parser.add_argument("--region", default="us-east-1", help="AWS region")
    schema_parser.add_argument("--type", dest="type_name", default="", help="Specific resource type (e.g. AWS::S3::Bucket)")
    schema_parser.add_argument("--types", default="", help="Comma-separated resource types (or 'all' for all Config-tracked types)")
    schema_parser.add_argument("--s3-uri", default="", help="S3 URI to upload cached schemas (e.g. s3://bucket/schemas/)")
    schema_parser.add_argument("--validate", default="", help="Validate a nodes.json file against cached type schemas")

    # scan command (run security/compliance queries against the graph)
    scan_parser = subparsers.add_parser("scan", help="Run security and compliance queries against infrastructure")
    scan_parser.add_argument("--profile", default="", help="AWS profile name")
    scan_parser.add_argument("--region", default="us-east-1", help="AWS region (comma-separated for multi-region)")
    scan_parser.add_argument("--types", default="", help="Resource type filter (comma-separated, globs supported)")
    scan_parser.add_argument("--max-resources", type=int, default=10000, help="Safety cap on resources")
    scan_parser.add_argument("--tags", default="", help="Filter queries by tag (comma-separated: security,compliance,bedrock)")
    scan_parser.add_argument("--names", default="", help="Filter queries by name (comma-separated)")
    scan_parser.add_argument("--min-score", type=float, default=0, help="Minimum score threshold for findings")
    scan_parser.add_argument("--enrich", nargs="?", const="soft", choices=["soft", "hard"], default=None, help="Enable enrichment before scanning")
    scan_parser.add_argument("--format", choices=["text", "json", "sarif"], default="text", help="Output format (default: text)")
    scan_parser.add_argument("--list-queries", action="store_true", help="List all available queries and exit")

    # slice command (subgraph extraction)
    slice_parser = subparsers.add_parser("slice", help="Extract subgraph around a resource")
    slice_parser.add_argument("--profile", default="", help="AWS profile name")
    slice_parser.add_argument("--region", default="us-east-1", help="AWS region")
    slice_parser.add_argument("--types", default="", help="Resource type filter for discovery")
    slice_parser.add_argument("--max-resources", type=int, default=10000, help="Safety cap on resources")
    slice_parser.add_argument("--enrich", nargs="?", const="soft", choices=["soft", "hard"], default=None, help="Enable enrichment")
    slice_parser.add_argument("--resource", default="", help="Resource ID/ARN/name to slice around (neighborhood mode)")
    slice_parser.add_argument("--depth", type=int, default=2, help="Hop depth for neighborhood/impact slice (default: 2)")
    slice_parser.add_argument("--path", nargs=2, metavar=("SOURCE", "TARGET"), help="Find path between two resources")
    slice_parser.add_argument("--type", dest="slice_type", default="", help="Extract all resources of a type (type mode)")
    slice_parser.add_argument("--impact", default="", help="Resource to trace impact from")
    slice_parser.add_argument("--direction", choices=["upstream", "downstream"], default="downstream", help="Impact direction (default: downstream)")
    slice_parser.add_argument("--output-dir", default="./bona-slice", help="Output directory for slice")

    # query command (interactive APGQL traversal)
    query_parser = subparsers.add_parser("query", help="Run APGQL traversals against the graph")
    query_parser.add_argument("--profile", default="", help="AWS profile name")
    query_parser.add_argument("--region", default="us-east-1", help="AWS region")
    query_parser.add_argument("--types", default="", help="Resource type filter")
    query_parser.add_argument("--max-resources", type=int, default=10000, help="Safety cap")
    query_parser.add_argument("--enrich", nargs="?", const="soft", choices=["soft", "hard"], default=None, help="Enable enrichment")
    query_parser.add_argument("--expr", default="", help="APGQL expression to evaluate (e.g. 'apg.nodes(\"AWS::EC2::*\").count')")
    query_parser.add_argument("--repl", action="store_true", help="Start interactive REPL")

    args = parser.parse_args()

    # Load config and configure logging
    from .config import load_config
    from .logging import setup_logging, flush_to_s3

    cfg = load_config(args.config or None)
    setup_logging(config=cfg, cli_verbose=args.verbose, profile="", region="us-east-1")

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "export":
        _do_export(args)
    elif args.command == "types":
        _do_types(args)
    elif args.command == "history":
        _do_history(args)
    elif args.command == "cache":
        _do_cache(args)
    elif args.command == "schema":
        _do_schema(args)
    elif args.command == "scan":
        _do_scan(args)
    elif args.command == "slice":
        _do_slice(args)
    elif args.command == "query":
        _do_query(args)

    # Flush logs to S3 if configured
    flush_to_s3()


def _do_query(args):
    """Run APGQL queries against the graph."""
    import json as json_mod
    from .providers.aws.aws_provider import AWSProvider
    from .config import load_config, EnrichmentConfig
    from .query import APG

    logger = logging.getLogger(__name__)
    cfg = load_config(args.config if hasattr(args, 'config') else None)
    type_filter = [t.strip() for t in args.types.split(",") if t.strip()] or None

    # Discover
    provider = AWSProvider(
        profile=args.profile,
        region=args.region,
        max_resources=args.max_resources,
        resource_types=type_filter,
    )
    if args.enrich:
        provider.validate_schemas = True
        provider.enrichment_config = EnrichmentConfig(enabled=True, mode=args.enrich)

    result = provider.discover()
    apg = APG(result.resources, result.relationships)
    logger.info("Graph loaded: %s", apg.summary)

    if args.expr:
        # Evaluate expression
        try:
            output = eval(args.expr, {"apg": apg, "APG": APG})
            if isinstance(output, (list, dict)):
                print(json_mod.dumps(output, indent=2, default=str))
            else:
                print(output)
        except Exception as e:
            print(f"Error: {e}")
    elif args.repl:
        # Interactive REPL
        print(f"\n  Bona APGQL REPL")
        print(f"  {apg.summary}")
        print(f"  Type traversals using 'apg' as root. Ctrl+D to exit.\n")
        print(f"  Examples:")
        print(f"    apg.nodes('AWS::EC2::*').count")
        print(f"    apg.nodes('AWS::Bedrock::Agent').out('USES_MODEL').name.l")
        print(f"    apg.node('vpc-abc').out().name.l")
        print()
        while True:
            try:
                expr = input("apgql> ")
                if not expr.strip():
                    continue
                output = eval(expr, {"apg": apg, "APG": APG})
                if isinstance(output, (list, dict)):
                    print(json_mod.dumps(output, indent=2, default=str))
                else:
                    print(output)
            except EOFError:
                print("\nBye.")
                break
            except KeyboardInterrupt:
                print()
                continue
            except Exception as e:
                print(f"Error: {e}")
    else:
        print(f"  {apg.summary}")
        print(f"  Use --expr 'apg.nodes(...)...' or --repl for interactive mode")


def _do_slice(args):
    """Extract a subgraph slice around a resource."""
    import json as json_mod
    from pathlib import Path
    from .providers.aws.aws_provider import AWSProvider
    from .config import load_config, EnrichmentConfig
    from .slice import neighborhood_slice, path_slice, type_slice, impact_slice

    logger = logging.getLogger(__name__)
    cfg = load_config(args.config if hasattr(args, 'config') else None)

    type_filter = [t.strip() for t in args.types.split(",") if t.strip()] or None

    # Discover resources
    provider = AWSProvider(
        profile=args.profile,
        region=args.region,
        max_resources=args.max_resources,
        resource_types=type_filter,
    )
    if args.enrich:
        provider.validate_schemas = True
        provider.enrichment_config = EnrichmentConfig(enabled=True, mode=args.enrich)

    result = provider.discover()

    # Determine slice mode
    if args.path:
        slice_result = path_slice(result.resources, result.relationships, args.path[0], args.path[1])
    elif args.slice_type:
        slice_result = type_slice(result.resources, result.relationships, args.slice_type)
    elif args.impact:
        slice_result = impact_slice(result.resources, result.relationships, args.impact, args.direction, args.depth)
    elif args.resource:
        slice_result = neighborhood_slice(result.resources, result.relationships, args.resource, args.depth)
    else:
        logger.error("Specify one of: --resource, --path, --type, --impact")
        return

    logger.info("Slice: %s", slice_result.summary())

    # Write output
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes_out = [n.to_dict() for n in slice_result.nodes]
    edges_out = [e.to_dict() for e in slice_result.edges]

    (out_dir / "nodes.json").write_text(json_mod.dumps(nodes_out, indent=2, default=str))
    (out_dir / "edges.json").write_text(json_mod.dumps(edges_out, indent=2, default=str))
    (out_dir / "slice.json").write_text(json_mod.dumps({
        "mode": slice_result.mode,
        "root": slice_result.root,
        "depth": slice_result.depth,
        "node_count": slice_result.node_count,
        "edge_count": slice_result.edge_count,
    }, indent=2))

    print(f"\n  {slice_result.summary()}")
    print(f"  📁 {out_dir}/nodes.json ({slice_result.node_count} nodes)")
    print(f"  📁 {out_dir}/edges.json ({slice_result.edge_count} edges)")


def _do_scan(args):
    """Run security/compliance queries against infrastructure."""
    import json as json_mod
    from .providers.aws.aws_provider import AWSProvider
    from .scan.engine import run_scan
    from .scan.queries import BUILTIN_QUERIES
    from .config import load_config, EnrichmentConfig

    logger = logging.getLogger(__name__)

    # List queries mode
    if args.list_queries:
        print(f"\nAvailable queries ({len(BUILTIN_QUERIES)}):\n")
        print(f"{'Name':<30} {'Score':<6} {'Tags':<30} Title")
        print(f"{'-'*30} {'-'*6} {'-'*30} {'-'*40}")
        for q in sorted(BUILTIN_QUERIES, key=lambda x: x.name):
            tags = ", ".join(q.tags)
            print(f"{q.name:<30} {q.score:<6} {tags:<30} {q.title}")
        return

    cfg = load_config(args.config if hasattr(args, 'config') else None)

    # Parse regions
    regions = [r.strip() for r in args.region.split(",") if r.strip()]
    type_filter = [t.strip() for t in args.types.split(",") if t.strip()] or None
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] or None
    names = [n.strip() for n in args.names.split(",") if n.strip()] or None

    all_findings = []

    for region in regions:
        # Discover resources
        provider = AWSProvider(
            profile=args.profile,
            region=region,
            max_resources=args.max_resources,
            resource_types=type_filter,
        )

        # Optional enrichment before scanning
        if args.enrich:
            provider.validate_schemas = True
            provider.enrichment_config = EnrichmentConfig(enabled=True, mode=args.enrich)
            if args.enrich == "hard":
                provider.refresh_cache = True

        result = provider.discover()

        # Run scan
        scan_result = run_scan(
            nodes=result.resources,
            edges=result.relationships,
            queries=BUILTIN_QUERIES,
            tags=tags,
            names=names,
            min_score=args.min_score,
        )

        all_findings.extend(scan_result.findings)

        logger.info("Scan %s: %s", region, scan_result.summary())

    # Output
    if args.format == "json":
        print(json_mod.dumps([f.to_dict() for f in all_findings], indent=2))
    elif args.format == "sarif":
        _print_sarif(all_findings)
    else:
        _print_text_findings(all_findings)


def _print_text_findings(findings):
    """Print findings in human-readable text format."""
    if not findings:
        print("\n  ✅ No findings. All clear.")
        return

    print(f"\n  ⚠️  {len(findings)} finding(s):\n")
    for f in findings:
        severity = "🔴" if f.score >= 9 else "🟠" if f.score >= 7 else "🟡" if f.score >= 4 else "⚪"
        print(f"  {severity} [{f.score}] {f.title}")
        print(f"      Resource: {f.resource_name} ({f.resource_type})")
        if f.detail:
            print(f"      Detail: {f.detail}")
        if f.remediation:
            print(f"      Fix: {f.remediation}")
        print()


def _print_sarif(findings):
    """Print findings in SARIF format (for CI integration)."""
    import json as json_mod
    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "bona-scan", "version": "0.3.4"}},
            "results": [
                {
                    "ruleId": f.query_name,
                    "level": "error" if f.score >= 9 else "warning" if f.score >= 5 else "note",
                    "message": {"text": f"{f.title}: {f.detail}"},
                    "locations": [{"physicalLocation": {"artifactLocation": {"uri": f.resource_id}}}],
                }
                for f in findings
            ],
        }],
    }
    print(json_mod.dumps(sarif, indent=2))


def _do_export(args):
    """Run export: discover → write nodes.json + edges.json."""
    from .providers.aws.aws_provider import AWSProvider
    from .export import export_to_dir, export_to_s3
    from .config import load_config

    # Load configuration
    cfg = load_config(args.config or None)

    # Parse resource type filter
    type_filter = [t.strip() for t in args.types.split(",") if t.strip()] or None

    # Parse regions
    regions = [r.strip() for r in args.region.split(",") if r.strip()]

    all_results = []
    for region in regions:
        print(f"\n{'='*60}")
        print(f"  Bona — Asset Property Graph Export")
        print(f"  Profile: {args.profile or '(default)'}")
        print(f"  Region:  {region}")
        print(f"  Cap:     {args.max_resources} resources")
        if type_filter:
            print(f"  Filter:  {', '.join(type_filter)}")
        print(f"{'='*60}\n")

        provider = AWSProvider(
            profile=args.profile,
            region=region,
            max_resources=args.max_resources,
            resource_types=type_filter,
            include_compliance=args.include_compliance,
            aggregator=args.aggregator,
        )
        provider.refresh_cache = args.refresh_cache

        # Enrichment: CLI flag overrides config file
        if args.enrich:
            provider.validate_schemas = True
            provider.enrichment_config = cfg.enrichment
            provider.enrichment_config.enabled = True
            if args.enrich == "hard":
                provider.refresh_cache = True
                provider.enrichment_config.mode = "hard"
        elif cfg.enrichment.enabled:
            provider.validate_schemas = True
            provider.enrichment_config = cfg.enrichment
            if cfg.enrichment.mode == "hard":
                provider.refresh_cache = True

        # Learn mode
        provider.learn_mode = args.learn
        if args.learn:
            provider._graph_schema_path = cfg.graph_schema.path or cfg.learning.graph_schema_path or ""
            provider._graph_schema_s3_uri = cfg.graph_schema.s3_uri or ""
            provider._learning_s3_uri = cfg.learning.s3_uri or ""
            provider._learning_rules_path = cfg.learning.relationship_rules_path or ""
            provider._learning_descriptions_path = cfg.learning.descriptions_path or ""

        # LLM enrichment
        provider.llm_enrich = args.llm_enrich
        if args.llm_enrich:
            provider._llm_config = cfg.llm
            provider._learning_s3_uri = cfg.learning.s3_uri or ""
            provider._learning_rules_path = cfg.learning.relationship_rules_path or ""
            provider._learning_descriptions_path = cfg.learning.descriptions_path or ""

        result = provider.discover()
        all_results.append(result)

        # Write to local dir (CLI flag > config > default)
        output_dir = args.output_dir if args.output_dir != "./bona-output" else cfg.output.dir
        if len(regions) > 1:
            output_dir = f"{output_dir}/{region}"

        manifest = export_to_dir(result, output_dir)

        print(f"\n  ✅ {result.summary()}")
        print(f"  📁 {output_dir}/nodes.json ({manifest['node_count']} nodes)")
        print(f"  📁 {output_dir}/edges.json ({manifest['edge_count']} edges)")

        # Additional format exports
        if args.format != "json":
            from .formats import export_neo4jcsv, export_graphml, export_dot
            fmt_dir = f"{output_dir}/{args.format}"
            if args.format == "neo4jcsv":
                export_neo4jcsv(result.resources, result.relationships, fmt_dir)
                print(f"  📁 {fmt_dir}/nodes.csv + relationships.csv")
            elif args.format == "graphml":
                export_graphml(result.resources, result.relationships, fmt_dir)
                print(f"  📁 {fmt_dir}/graph.graphml")
            elif args.format == "dot":
                export_dot(result.resources, result.relationships, fmt_dir)
                print(f"  📁 {fmt_dir}/graph.dot")

        # Optional S3 upload (CLI flag > config)
        s3_uri = args.s3_uri or cfg.output.s3_uri
        if s3_uri:
            s3_prefix = s3_uri.rstrip("/")
            if len(regions) > 1:
                s3_prefix = f"{s3_prefix}/{region}"
            s3_manifest = export_to_s3(result, s3_prefix, profile=args.profile)
            print(f"  ☁️  Uploaded to {s3_manifest.get('nodes_s3', s3_uri)}")

    # Summary
    if len(regions) > 1:
        total_nodes = sum(r.resource_count for r in all_results)
        total_edges = sum(r.relationship_count for r in all_results)
        print(f"\n  📊 Total across {len(regions)} regions: {total_nodes} nodes, {total_edges} edges")


def _do_types(args):
    """List resource types tracked by Config in this region."""
    from .providers.aws.aws_provider import AWSProvider

    provider = AWSProvider(profile=args.profile, region=args.region)
    types = provider._get_resource_types()

    print(f"\nResource types tracked by AWS Config ({args.region}):")
    print(f"{'='*50}")
    for t in sorted(types):
        print(f"  {t}")
    print(f"\nTotal: {len(types)} types")


def _do_cache(args):
    """Pre-populate local caches (instance types, etc.)."""
    import boto3
    from .enrichment.instance_types import get_instance_types
    from .cache import CACHE_DIR

    session_kwargs = {"region_name": args.region}
    if args.profile:
        session_kwargs["profile_name"] = args.profile
    session = boto3.Session(**session_kwargs)

    print(f"\n  Bona — Cache Population")
    print(f"  Profile: {args.profile or '(default)'}")
    print(f"  Region:  {args.region}")
    print(f"  Cache:   {CACHE_DIR}")
    print()

    # Instance types
    types = get_instance_types(session, args.region, refresh=True)
    print(f"  ✅ Instance types: {len(types)} definitions cached")
    print(f"  📁 {CACHE_DIR}/instance-types-{args.region}.json")


def _do_schema(args):
    """Download CloudFormation type schemas to local cache (and optionally S3)."""
    import boto3
    from .enrichment.type_schemas import fetch_all_type_schemas, get_type_schema
    from .cache import CACHE_DIR
    from .config import load_config

    cfg = load_config(args.config if hasattr(args, 'config') else None)

    session_kwargs = {"region_name": args.region}
    if args.profile:
        session_kwargs["profile_name"] = args.profile
    session = boto3.Session(**session_kwargs)

    # Determine which types to download
    if args.validate:
        # Validate mode: load nodes.json and check against cached schemas
        _do_schema_validate(args, session)
        return

    type_names = []
    if args.type_name:
        type_names = [args.type_name]
    elif args.types:
        if args.types == "all":
            # Enumerate from Config
            from .providers.aws.aws_provider import AWSProvider
            provider = AWSProvider(profile=args.profile, region=args.region)
            type_names = provider._get_resource_types()
        else:
            type_names = [t.strip() for t in args.types.split(",") if t.strip()]
    else:
        # Default: download common types
        from .providers.aws.aws_provider import AWSProvider
        type_names = AWSProvider._COMMON_TYPES

    logger = logging.getLogger(__name__)
    logger.info("Type Schema Download (hard inquiry) — profile=%s region=%s types=%d cache=%s",
                args.profile or "(default)", args.region, len(type_names), CACHE_DIR)

    # Always hard inquiry — this command is specifically for populating cache
    schemas = fetch_all_type_schemas(session, args.region, type_names, refresh=True)

    logger.info("Downloaded %d / %d type schemas", len(schemas), len(type_names))
    for name in sorted(schemas.keys()):
        prop_count = len(schemas[name].get("properties", {}))
        logger.info("  %s (%d properties)", name, prop_count)

    failed = set(type_names) - set(schemas.keys())
    if failed:
        logger.warning("%d types not found in CloudFormation registry", len(failed))
        for name in sorted(failed):
            logger.warning("  %s", name)

    # Optional S3 upload (CLI flag > config)
    s3_uri = args.s3_uri or cfg.schema_cache.s3_uri
    if s3_uri:
        _upload_schemas_to_s3(schemas, s3_uri, args.region, args.profile)


def _do_schema_validate(args, session):
    """Validate a nodes.json file against cached type schemas."""
    import json
    from pathlib import Path
    from .enrichment.type_schemas import get_type_schema, soft_check
    from .schema.model import AssetNode

    logger = logging.getLogger(__name__)

    nodes_path = Path(args.validate)
    if not nodes_path.exists():
        logger.error("File not found: %s", nodes_path)
        return

    with open(nodes_path) as f:
        nodes_data = json.load(f)

    logger.info("Schema Validation (soft check) — file=%s nodes=%d", nodes_path, len(nodes_data))

    valid_count = 0
    invalid_count = 0
    no_schema_count = 0

    for node_dict in nodes_data:
        resource_type = node_dict.get("resource_type", node_dict.get("node_type", ""))
        if not resource_type.startswith("AWS::"):
            continue

        schema = get_type_schema(session, args.region, resource_type, refresh=False)
        if not schema:
            no_schema_count += 1
            continue

        # Build a minimal AssetNode for validation
        node = AssetNode(
            id=node_dict.get("id", ""),
            node_type=node_dict.get("node_type", ""),
            name=node_dict.get("name", ""),
            resource_type=resource_type,
            properties={k: v for k, v in node_dict.items() if k not in {
                "id", "node_type", "name", "stable_id", "semantic_hash",
                "provider", "account_id", "region", "resource_type", "state", "tags",
                "configuration_capture_time",
            }},
        )

        result = soft_check(node, schema)
        if result["valid"]:
            valid_count += 1
        else:
            invalid_count += 1
            logger.warning("%s (%s) — INVALID", node_dict.get('name', node_dict.get('id', '?')), resource_type)
            if result["missing_required"]:
                logger.warning("  Missing required: %s", ', '.join(result['missing_required']))
            if result["unknown_properties"]:
                logger.warning("  Unknown props: %s", ', '.join(result['unknown_properties'][:5]))

    logger.info("Validation results: valid=%d invalid=%d no_schema=%d", valid_count, invalid_count, no_schema_count)


def _upload_schemas_to_s3(schemas: dict, s3_uri: str, region: str, profile: str = ""):
    """Upload cached schemas to S3."""
    import json
    import boto3

    logger = logging.getLogger(__name__)

    # Parse s3://bucket/prefix/
    if not s3_uri.startswith("s3://"):
        logger.error("Invalid S3 URI: %s", s3_uri)
        return

    parts = s3_uri[5:].split("/", 1)
    bucket = parts[0]
    prefix = parts[1].rstrip("/") if len(parts) > 1 else ""

    session_kwargs = {"region_name": region}
    if profile:
        session_kwargs["profile_name"] = profile
    session = boto3.Session(**session_kwargs)
    s3 = session.client("s3")

    uploaded = 0
    for type_name, schema in schemas.items():
        safe_name = type_name.replace("::", "--")
        key = f"{prefix}/{safe_name}.json" if prefix else f"{safe_name}.json"

        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(schema, indent=2, default=str),
            ContentType="application/json",
        )
        uploaded += 1

    logger.info("Uploaded %d schemas to s3://%s/%s/", uploaded, bucket, prefix)


def _do_history(args):
    """Export temporal graph — configuration history for a single resource."""
    import json
    from pathlib import Path

    import boto3
    from .schema.model import AssetNode, AssetEdge

    session_kwargs = {"region_name": args.region}
    if args.profile:
        session_kwargs["profile_name"] = args.profile
    session = boto3.Session(**session_kwargs)
    config_client = session.client("config", region_name=args.region)
    sts = session.client("sts")
    account_id = sts.get_caller_identity()["Account"]

    print(f"\n{'='*60}")
    print(f"  Bona — Resource Config History")
    print(f"  Resource: {args.resource_type} / {args.resource_id}")
    print(f"  Limit:    {args.limit} items")
    print(f"{'='*60}\n")

    # Fetch history
    try:
        resp = config_client.get_resource_config_history(
            resourceType=args.resource_type,
            resourceId=args.resource_id,
            limit=args.limit,
        )
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return

    items = resp.get("configurationItems", [])
    if not items:
        print("  No history found for this resource.")
        return

    # Build temporal nodes — one node per configuration snapshot
    nodes = []
    edges = []
    prev_node_id = None

    for i, item in enumerate(items):
        capture_time = item.get("configurationItemCaptureTime", "")
        # Format timestamp for ID
        ts_str = str(capture_time).replace(" ", "T")[:19] if capture_time else f"t{i}"

        arn = item.get("arn", f"{args.resource_type}/{args.resource_id}")
        node_id = f"{arn}@{ts_str}"

        # Parse configuration
        config = item.get("configuration", "")
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except (json.JSONDecodeError, TypeError):
                config = {}

        status = item.get("configurationItemStatus", "OK")
        state_map = {"OK": "ACTIVE", "ResourceDiscovered": "DISCOVERED", "ResourceDeleted": "DELETED"}
        state = state_map.get(status, status)

        node = {
            "id": node_id,
            "node_type": args.resource_type,
            "name": item.get("resourceName", args.resource_id),
            "stable_id": f"aws|{account_id}|{args.region}|{args.resource_type}|{args.resource_id}@{ts_str}",
            "semantic_hash": item.get("configurationItemMD5Hash", ""),
            "provider": "aws",
            "account_id": account_id,
            "region": item.get("awsRegion", args.region),
            "resource_type": args.resource_type,
            "state": state,
            "configuration_capture_time": str(capture_time),
            "configuration_state_id": item.get("configurationStateId", ""),
            "version": f"v{len(items) - i}",
        }

        # Add configuration properties
        if isinstance(config, dict):
            for k, v in config.items():
                if isinstance(v, (str, int, float, bool)) and k not in node:
                    node[k] = v

        # Extract relationships from this snapshot
        for rel in item.get("relationships", []):
            if isinstance(rel, dict) and rel.get("resourceId"):
                edges.append({
                    "source_id": node_id,
                    "target_id": rel.get("resourceId", ""),
                    "edge_type": rel.get("relationshipName", "RELATED_TO").upper().replace(" ", "_"),
                    "stable_id": f"aws|{account_id}|EDGE|{rel.get('relationshipName', '')}|{args.resource_id}→{rel.get('resourceId', '')}@{ts_str}",
                    "snapshot_time": str(capture_time),
                })

        nodes.append(node)

        # Add temporal edge: this_version → previous_version
        if prev_node_id:
            edges.append({
                "source_id": node_id,
                "target_id": prev_node_id,
                "edge_type": "CHANGED_TO",
                "stable_id": f"aws|{account_id}|EDGE|CHANGED_TO|{args.resource_id}@{ts_str}→prev",
            })
        prev_node_id = node_id

    # Write output
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(out / "nodes.json", "w") as f:
        json.dump(nodes, f, indent=2, default=str)
    with open(out / "edges.json", "w") as f:
        json.dump(edges, f, indent=2, default=str)

    print(f"  ✅ {len(nodes)} snapshots, {len(edges)} edges (temporal + relationship)")
    print(f"  📁 {out}/nodes.json")
    print(f"  📁 {out}/edges.json")
    print(f"\n  Timeline:")
    for n in nodes:
        print(f"    {n['version']}  {n['configuration_capture_time'][:19]}  [{n['state']}]  hash={n['semantic_hash'][:8]}")


if __name__ == "__main__":
    main()
