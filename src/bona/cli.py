"""Bona CLI — standalone asset discovery tool.

Usage:
    bona discover --account 123456789012 --region us-east-1
    bona discover --account 123456789012 --domains bona-domains.yaml --output-tree ./source
    bona classify --input discovery.json --domains bona-domains.yaml --output-tree ./source
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
    disc.add_argument("--domains", default="", help="Domain config YAML for classification")
    disc.add_argument("--output-tree", default="", help="Write Metis source tree to this directory")

    # classify (offline — from existing JSON)
    classify_cmd = sub.add_parser("classify", help="Classify an existing discovery result into domains")
    classify_cmd.add_argument("--input", required=True, help="Discovery result JSON file")
    classify_cmd.add_argument("--domains", required=True, help="Domain config YAML")
    classify_cmd.add_argument("--output-tree", required=True, help="Output Metis source tree directory")

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

        # If domain classification requested, classify and write tree
        if args.domains and args.output_tree:
            from .classify import DomainClassifier
            from .writers import MetisSourceWriter

            classifier = DomainClassifier.from_file(args.domains)
            assignments = classifier.classify(result)
            writer = MetisSourceWriter(args.output_tree)
            summary = writer.write(assignments, account_id=args.account, region=args.region)

            print(f"Source tree written to {args.output_tree}")
            print(f"  Domains: {len(summary)}")
            for domain, count in sorted(summary.items(), key=lambda x: -x[1]):
                print(f"    {domain}: {count} nodes")
        elif args.output == "json":
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

    elif args.command == "classify":
        from .classify import DomainClassifier
        from .writers import MetisSourceWriter
        from .schema.model import AssetNode, AssetEdge, DiscoveryResult

        # Load discovery result from JSON
        with open(args.input) as f:
            data = json.load(f)

        resources = [
            AssetNode(
                id=r["id"],
                node_type=r.get("node_type", "CSPResource"),
                provider=r.get("provider", "aws"),
                name=r.get("name", ""),
                region=r.get("region", ""),
                account_id=r.get("account_id", ""),
                resource_type=r.get("resource_type", r.get("type", "")),
                state=r.get("state", "active"),
                properties=r.get("properties", {}),
                tags=r.get("tags", {}),
            )
            for r in data.get("resources", [])
        ]
        relationships = [
            AssetEdge(
                source_id=e["source_id"] if "source_id" in e else e["source"],
                target_id=e["target_id"] if "target_id" in e else e["target"],
                edge_type=e["edge_type"] if "edge_type" in e else e["type"],
                properties=e.get("properties", {}),
            )
            for e in data.get("relationships", [])
        ]

        result = DiscoveryResult(
            provider=data.get("provider", "aws"),
            account_id=data.get("account_id", ""),
            region=data.get("region", ""),
            resources=resources,
            relationships=relationships,
        )

        classifier = DomainClassifier.from_file(args.domains)
        assignments = classifier.classify(result)
        writer = MetisSourceWriter(args.output_tree)
        summary = writer.write(
            assignments,
            account_id=result.account_id,
            region=result.region,
        )

        print(f"Source tree written to {args.output_tree}")
        print(f"  Domains: {len(summary)}")
        for domain, count in sorted(summary.items(), key=lambda x: -x[1]):
            print(f"    {domain}: {count} nodes")

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
