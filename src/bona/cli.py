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


if __name__ == "__main__":
    main()
