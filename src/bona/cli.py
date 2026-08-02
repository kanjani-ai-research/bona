"""Bona CLI — standalone asset discovery tool.

Usage:
    bona discover --account 123456789012 --region us-east-1
    bona services --region us-east-1
    bona compliance --account 123456789012
"""

import argparse
import json
import sys

from .providers.aws.aws_provider import AWSProvider
from .pipeline.discovery_pipeline import DiscoveryPipeline


def main():
    parser = argparse.ArgumentParser(description="Bona — Infrastructure asset graph")
    sub = parser.add_subparsers(dest="command")

    # discover
    disc = sub.add_parser("discover", help="Discover all resources + relationships")
    disc.add_argument("--account", default="", help="AWS account ID")
    disc.add_argument("--region", default="us-east-1", help="AWS region")
    disc.add_argument("--types", nargs="*", help="Resource types to discover")
    disc.add_argument("--output", choices=["json", "summary"], default="summary")

    # services
    svc = sub.add_parser("services", help="Enumerate AWS services + operations")
    svc.add_argument("--region", default="us-east-1")

    # compliance
    comp = sub.add_parser("compliance", help="Check compliance state")
    comp.add_argument("--account", default="")
    comp.add_argument("--region", default="us-east-1")

    args = parser.parse_args()

    if args.command == "discover":
        provider = AWSProvider(account_id=args.account, region=args.region)
        pipeline = DiscoveryPipeline(provider)
        result = pipeline.run()
        if args.output == "json":
            print(json.dumps({
                "resources": [{"id": r.id, "type": r.resource_type, "name": r.name} for r in result.resources],
                "relationships": [{"source": e.source_id, "target": e.target_id, "type": e.edge_type} for e in result.relationships],
                "findings": [{"id": f.id, "type": f.resource_type, "state": f.state} for f in result.findings],
            }, indent=2))
        else:
            print(result.summary())
            print(f"\nResource types:")
            types = {}
            for r in result.resources:
                types[r.resource_type] = types.get(r.resource_type, 0) + 1
            for t, c in sorted(types.items(), key=lambda x: -x[1]):
                print(f"  {t}: {c}")

    elif args.command == "services":
        provider = AWSProvider(region=args.region)
        services = provider.discover_services()
        print(f"AWS Services: {len(services)}")
        for s in sorted(services, key=lambda x: x["service_name"]):
            print(f"  {s['service_name']}: {s['operation_count']} operations")

    elif args.command == "compliance":
        provider = AWSProvider(account_id=args.account, region=args.region)
        result = provider.discover_compliance()
        print(f"Non-compliant resources: {len(result.findings)}")
        for f in result.findings:
            print(f"  ❌ {f.resource_type}: {f.properties.get('resource_id', '')}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
