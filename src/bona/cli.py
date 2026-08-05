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

    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "export":
        _do_export(args)
    elif args.command == "types":
        _do_types(args)
    elif args.command == "history":
        _do_history(args)


def _do_export(args):
    """Run export: discover → write nodes.json + edges.json."""
    from .providers.aws.aws_provider import AWSProvider
    from .export import export_to_dir, export_to_s3

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

        result = provider.discover()
        all_results.append(result)

        # Write to local dir (always — default behavior)
        output_dir = args.output_dir
        if len(regions) > 1:
            output_dir = f"{args.output_dir}/{region}"

        manifest = export_to_dir(result, output_dir)

        print(f"\n  ✅ {result.summary()}")
        print(f"  📁 {output_dir}/nodes.json ({manifest['node_count']} nodes)")
        print(f"  📁 {output_dir}/edges.json ({manifest['edge_count']} edges)")

        # Optional S3 upload (only if explicitly configured)
        if args.s3_uri:
            s3_prefix = args.s3_uri.rstrip("/")
            if len(regions) > 1:
                s3_prefix = f"{s3_prefix}/{region}"
            s3_manifest = export_to_s3(result, s3_prefix, profile=args.profile)
            print(f"  ☁️  Uploaded to {s3_manifest.get('nodes_s3', args.s3_uri)}")

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
