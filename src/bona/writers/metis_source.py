"""Metis source tree writer — writes per-domain L1/L2 output.

Produces a directory layout that Metis can validate and refine:

    <output>/
      L1/nodes/<domain>/nodes.json
                        edges.json
                        manifest.json
      L1/deep/*.json                    (optional, raw inventory)
      L2/L2-all-nodes.json
         L2-all-edges.json
         L2-manifest.json
      schemas/<pkg>/<pkg>.yaml          (optional)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..classify.assignments import DomainAssignments
from ..schema.model import AssetEdge, AssetNode

logger = logging.getLogger(__name__)


def _node_to_l1(node: AssetNode) -> dict:
    """Convert an AssetNode to an L1 node record."""
    record = {
        "id": node.id,
        "node_type": node.resource_type.replace("::", "_") if node.resource_type else node.node_type,
        "name": node.name or node.id,
        "provider": node.provider,
        "account_id": node.account_id,
        "region": node.region,
        "resource_type": node.resource_type,
        "state": node.state or "active",
    }
    # Add stable_id if we can construct one
    if node.provider and node.account_id and node.region and node.resource_type:
        native_id = node.properties.get("resource_id", node.name or node.id)
        record["stable_id"] = (
            f"{node.provider}|{node.account_id}|{node.region}"
            f"|{record['node_type']}|{native_id}"
        )
    # Carry extra properties
    for key, value in node.properties.items():
        if key not in record and key not in ("tags", "resource_id", "resource_name"):
            record[key] = value
    return record


def _edge_to_l1(edge: AssetEdge) -> dict:
    """Convert an AssetEdge to an L1 edge record."""
    record = {
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "edge_type": edge.edge_type,
    }
    for key, value in edge.properties.items():
        if key not in record:
            record[key] = value
    return record


def _node_to_l2(node: AssetNode) -> dict:
    """Convert an AssetNode to an L2 type definition record."""
    type_name = node.resource_type or node.node_type
    return {
        "id": f"L2:{type_name}",
        "node_type": "TypeDefinition",
        "type_name": type_name,
    }


class MetisSourceWriter:
    """Writes domain-classified discovery results as a Metis source tree.

    Usage:
        writer = MetisSourceWriter(output_dir="./source-tree")
        writer.write(assignments, account_id="123456789012", region="us-east-1")
    """

    def __init__(self, output_dir: str | Path):
        self._output = Path(output_dir)

    def write(
        self,
        assignments: DomainAssignments,
        account_id: str = "",
        region: str = "us-east-1",
        schema_packages: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """Write the full source tree.

        Returns a summary dict: {domain: node_count, ...}
        """
        self._output.mkdir(parents=True, exist_ok=True)

        summary = {}

        # Write per-domain L1 directories
        for domain in assignments.domains_with_nodes:
            nodes = assignments.get_nodes(domain)
            edges = assignments.get_edges(domain)
            self._write_l1_app(domain, nodes, edges, account_id, region, schema_packages)
            summary[domain] = len(nodes)

        # Write L2 type definitions (global, across all domains)
        self._write_l2(assignments.all_nodes)

        # Write L2 manifest
        self._write_l2_manifest(account_id, region)

        logger.info(
            "Source tree written to %s: %d domains, %d total nodes",
            self._output,
            len(summary),
            sum(summary.values()),
        )
        return summary

    def _write_l1_app(
        self,
        domain: str,
        nodes: list[AssetNode],
        edges: list[AssetEdge],
        account_id: str,
        region: str,
        schema_packages: Optional[list[str]],
    ) -> None:
        """Write one L1/nodes/<domain>/ directory."""
        app_dir = self._output / "L1" / "nodes" / domain
        app_dir.mkdir(parents=True, exist_ok=True)

        # nodes.json
        l1_nodes = [_node_to_l1(n) for n in nodes]
        self._write_json(app_dir / "nodes.json", l1_nodes)

        # edges.json
        l1_edges = [_edge_to_l1(e) for e in edges]
        self._write_json(app_dir / "edges.json", l1_edges)

        # manifest.json
        manifest = {
            "app": domain,
            "provider": "aws",
            "accounts": [account_id] if account_id else [],
            "regions": [region],
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "node_count": len(l1_nodes),
            "edge_count": len(l1_edges),
        }
        if schema_packages:
            manifest["schema_packages"] = schema_packages
        self._write_json(app_dir / "manifest.json", manifest)

    def _write_l2(self, all_nodes: list[AssetNode]) -> None:
        """Write L2/L2-all-nodes.json and L2-all-edges.json."""
        l2_dir = self._output / "L2"
        l2_dir.mkdir(parents=True, exist_ok=True)

        # Deduplicate type definitions by resource_type
        seen_types: dict[str, dict] = {}
        instance_of_edges: list[dict] = []

        for node in all_nodes:
            rt = node.resource_type
            if not rt:
                continue
            l2_id = f"L2:{rt}"

            # Type definition
            if rt not in seen_types:
                seen_types[rt] = {
                    "id": l2_id,
                    "node_type": "TypeDefinition",
                    "type_name": rt,
                }

            # INSTANCE_OF edge
            instance_of_edges.append({
                "source_id": node.id,
                "target_id": l2_id,
                "edge_type": "INSTANCE_OF",
            })

        self._write_json(l2_dir / "L2-all-nodes.json", list(seen_types.values()))
        self._write_json(l2_dir / "L2-all-edges.json", instance_of_edges)

    def _write_l2_manifest(self, account_id: str, region: str) -> None:
        """Write L2/L2-manifest.json."""
        l2_dir = self._output / "L2"
        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "bona",
            "account_id": account_id,
            "region": region,
        }
        self._write_json(l2_dir / "L2-manifest.json", manifest)

    @staticmethod
    def _write_json(path: Path, data) -> None:
        """Write data as formatted JSON."""
        with path.open("w") as f:
            json.dump(data, f, indent=2, default=str)
