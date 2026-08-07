"""GCP Provider — discovers infrastructure via Cloud Asset Inventory.

Uses the Cloud Asset API (cloudasset.googleapis.com) to enumerate all
resources in a GCP project, folder, or organization.

Configuration (bona.yaml):
    gcp:
      project_id: "majestic-hybrid-292718"
      # Or scope to org/folder:
      # organization_id: "123456789"
      # folder_id: "folders/123456789"
      asset_types: []  # empty = all types, or filter e.g. ["compute.googleapis.com/*"]
      content_type: "resource"  # resource | iam_policy | org_policy

Usage:
    from bona.providers.gcp import GCPProvider
    provider = GCPProvider(project_id="majestic-hybrid-292718")
    result = provider.discover()
"""

import hashlib
import json
import logging
import time
from typing import Any, Optional

from ..provider_base import AssetProvider
from ...schema.model import AssetNode, AssetEdge, DiscoveryResult

logger = logging.getLogger(__name__)

# GCP asset type → bona node_type mapping
# Format: "service.googleapis.com/Resource" → "GCP::Service::Resource"
def _to_node_type(asset_type: str) -> str:
    """Convert GCP asset type to bona node_type.

    Example: compute.googleapis.com/Instance → GCP::Compute::Instance
    """
    if "/" in asset_type:
        service_part, resource = asset_type.rsplit("/", 1)
        service = service_part.split(".")[0].capitalize()
        return f"GCP::{service}::{resource}"
    return f"GCP::{asset_type}"


def _extract_id(asset: dict) -> str:
    """Extract a usable ID from a GCP asset.

    Uses the `name` field which is the full resource path:
    //storage.googleapis.com/my-bucket
    //compute.googleapis.com/projects/proj/zones/zone/instances/name
    """
    return asset.get("name", "")


def _extract_name(asset: dict) -> str:
    """Extract a short human-readable name from the asset."""
    name = asset.get("name", "")
    # Resource data may have a 'name' or 'displayName'
    resource_data = asset.get("resource", {}).get("data", {})
    if resource_data.get("displayName"):
        return resource_data["displayName"]
    if resource_data.get("name"):
        return resource_data["name"]
    # Fall back to last path segment
    return name.rsplit("/", 1)[-1] if "/" in name else name


def _flatten_properties(resource_data: dict, max_depth: int = 2) -> dict:
    """Flatten resource data to scalar properties for the node."""
    props = {}
    for k, v in resource_data.items():
        if isinstance(v, (str, int, float, bool)):
            props[k] = v
        elif isinstance(v, list) and v:
            if isinstance(v[0], str):
                props[k] = ", ".join(v[:10])
            else:
                props[f"{k}_count"] = len(v)
        elif isinstance(v, dict) and max_depth > 0:
            # Flatten one level with prefix
            for sk, sv in v.items():
                if isinstance(sv, (str, int, float, bool)):
                    props[f"{k}.{sk}"] = sv
    return props


class GCPProvider(AssetProvider):
    """GCP Cloud Asset Inventory provider.

    Discovers all resources in a GCP project/folder/org using the
    Cloud Asset API and converts them to AssetNodes.
    """

    name = "gcp"
    description = "GCP Cloud Asset Inventory provider"

    def __init__(self, project_id: str = "", organization_id: str = "",
                 folder_id: str = "", asset_types: Optional[list[str]] = None,
                 content_type: str = "resource", **kwargs):
        super().__init__(region="global", account_id=project_id, **kwargs)
        self.project_id = project_id
        self.organization_id = organization_id
        self.folder_id = folder_id
        self.asset_types = asset_types or []
        self.content_type = content_type

    @property
    def _scope(self) -> str:
        """Build the parent scope for the API call."""
        if self.organization_id:
            return f"organizations/{self.organization_id}"
        elif self.folder_id:
            return f"folders/{self.folder_id}"
        elif self.project_id:
            return f"projects/{self.project_id}"
        raise ValueError("GCPProvider requires project_id, organization_id, or folder_id")

    def discover(self) -> DiscoveryResult:
        """Discover all GCP resources via Cloud Asset Inventory.

        Tries the Python client library first (requires ADC).
        Falls back to gcloud CLI if ADC not configured.
        """
        start = time.time()
        nodes: list[AssetNode] = []
        edges: list[AssetEdge] = []

        try:
            nodes = self._discover_via_client()
        except Exception as e:
            logger.info("Python client failed (%s), falling back to gcloud CLI", e)
            try:
                nodes = self._discover_via_cli()
            except Exception as e2:
                logger.error("GCP discovery failed: %s", e2)

        # Infer edges from resource references
        edges = self._infer_edges(nodes)

        duration_ms = int((time.time() - start) * 1000)
        logger.info("GCP discovery complete: %d nodes, %d edges, %dms (scope=%s)",
                    len(nodes), len(edges), duration_ms, self._scope)

        return DiscoveryResult(resources=nodes, relationships=edges, duration_ms=duration_ms)

    def _discover_via_client(self) -> list[AssetNode]:
        """Discover using google-cloud-asset Python library."""
        from google.cloud import asset_v1

        client = asset_v1.AssetServiceClient()
        content_type_map = {
            "resource": asset_v1.ContentType.RESOURCE,
            "iam_policy": asset_v1.ContentType.IAM_POLICY,
            "org_policy": asset_v1.ContentType.ORG_POLICY,
        }
        ct = content_type_map.get(self.content_type, asset_v1.ContentType.RESOURCE)

        request = asset_v1.ListAssetsRequest(
            parent=self._scope,
            content_type=ct,
            asset_types=self.asset_types or [],
        )

        nodes = []
        for asset in client.list_assets(request=request):
            node = self._asset_to_node(asset)
            if node:
                nodes.append(node)
        return nodes

    def _discover_via_cli(self) -> list[AssetNode]:
        """Discover using gcloud CLI (fallback when ADC not set)."""
        import subprocess

        cmd = ["gcloud", "asset", "list",
               f"--project={self.project_id}" if self.project_id else f"--organization={self.organization_id}",
               "--content-type=resource", "--format=json"]

        if self.asset_types:
            cmd.append(f"--asset-types={','.join(self.asset_types)}")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"gcloud asset list failed: {result.stderr[:200]}")

        assets = json.loads(result.stdout) if result.stdout else []
        nodes = []
        for asset_dict in assets:
            node = self._dict_to_node(asset_dict)
            if node:
                nodes.append(node)
        return nodes

    def _dict_to_node(self, asset: dict) -> Optional[AssetNode]:
        """Convert a gcloud JSON asset dict to an AssetNode."""
        asset_type = asset.get("assetType", "")
        name = asset.get("name", "")

        if not name:
            return None

        node_type = _to_node_type(asset_type)
        resource = asset.get("resource", {})
        resource_data = resource.get("data", {})
        location = resource.get("location", "")

        short_name = resource_data.get("displayName") or resource_data.get("name") or name.rsplit("/", 1)[-1]
        properties = _flatten_properties(resource_data)

        properties["assetType"] = asset_type
        if location:
            properties["location"] = location
        if asset.get("updateTime"):
            properties["updateTime"] = asset["updateTime"]
        if asset.get("ancestors"):
            properties["ancestors"] = ", ".join(asset["ancestors"])

        return AssetNode(
            id=name,
            node_type=node_type,
            name=short_name,
            provider="gcp",
            account_id=self.project_id,
            region=location or "global",
            resource_type=asset_type,
            properties=properties,
        )

    def _asset_to_node(self, asset) -> Optional[AssetNode]:
        """Convert a GCP asset proto to an AssetNode."""
        asset_type = asset.asset_type or ""
        name = asset.name or ""

        if not name:
            return None

        node_type = _to_node_type(asset_type)
        short_name = _extract_name({"name": name, "resource": {"data": {}}})

        # Extract resource data if available
        properties = {}
        location = ""
        if asset.resource:
            location = asset.resource.location or ""
            if asset.resource.data:
                # Convert proto MapComposite to dict
                resource_data = dict(asset.resource.data)
                properties = _flatten_properties(resource_data)
                # Override short_name if resource has better name
                if resource_data.get("displayName"):
                    short_name = resource_data["displayName"]
                elif resource_data.get("name"):
                    short_name = resource_data["name"]

        # Add metadata properties
        properties["assetType"] = asset_type
        if location:
            properties["location"] = location
        if asset.update_time:
            properties["updateTime"] = asset.update_time.isoformat()
        if asset.ancestors:
            properties["ancestors"] = ", ".join(asset.ancestors)

        return AssetNode(
            id=name,
            node_type=node_type,
            name=short_name,
            provider="gcp",
            account_id=self.project_id,
            region=location or "global",
            resource_type=asset_type,
            properties=properties,
        )

    def _infer_edges(self, nodes: list[AssetNode]) -> list[AssetEdge]:
        """Infer relationships from resource properties.

        Common GCP patterns:
        - network/subnetwork references
        - serviceAccount references
        - project parent references
        """
        edges = []
        node_ids = {n.id for n in nodes}

        for node in nodes:
            props = node.properties

            # Network references
            for key in ("network", "subnetwork", "selfLink"):
                val = props.get(key, "")
                if isinstance(val, str) and val.startswith("//") and val in node_ids:
                    edge_type = "IN_NETWORK" if "network" in key else "REFERENCES"
                    edges.append(AssetEdge(
                        source_id=node.id,
                        target_id=val,
                        edge_type=edge_type,
                    ))

            # Service account references
            sa = props.get("serviceAccount", "") or props.get("serviceAccountEmail", "")
            if sa:
                # Try to match a service account node
                sa_id = f"//iam.googleapis.com/projects/{self.project_id}/serviceAccounts/{sa}"
                if sa_id in node_ids:
                    edges.append(AssetEdge(
                        source_id=node.id,
                        target_id=sa_id,
                        edge_type="USES_SERVICE_ACCOUNT",
                    ))

        return edges

    def get_resource_types(self) -> list[str]:
        """List available GCP resource types."""
        return [_to_node_type(t) for t in self.asset_types] if self.asset_types else []

    def validate(self) -> list[str]:
        """Validate GCP credentials and project access."""
        errors = []
        if not self.project_id and not self.organization_id and not self.folder_id:
            errors.append("No GCP scope configured (set project_id, organization_id, or folder_id)")
        try:
            from google.cloud import asset_v1
            client = asset_v1.AssetServiceClient()
            # Quick test — list 1 asset
            request = asset_v1.ListAssetsRequest(parent=self._scope, page_size=1)
            next(iter(client.list_assets(request=request)), None)
        except Exception as e:
            errors.append(f"GCP access failed: {e}")
        return errors
