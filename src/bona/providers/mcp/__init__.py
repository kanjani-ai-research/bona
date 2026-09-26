"""MCP Discovery Provider — generic asset discovery via MCP server.

Instead of writing a plugin per service, configure MCP discovery in bona.yaml:

    mcp:
      enabled: true
      servers:
        - name: aws
          server_url: "http://localhost:3000"

      discovery:
        - service: guardduty
          operation: ListDetectors
          node_type: "AWS::GuardDuty::Detector"
          id_field: DetectorIds[]       # jmespath-like path to extract IDs
          detail_operation: GetDetector  # optional: fetch full details per item
          detail_params:
            DetectorId: "$id"           # $id is replaced with each extracted ID

        - service: inspector2
          operation: ListFindings
          node_type: "AWS::Inspector2::Finding"
          items_key: findings           # key in response containing the list
          id_field: findingArn
          name_field: title

        - service: securityhub
          operation: GetFindings
          node_type: "AWS::SecurityHub::Finding"
          items_key: Findings
          id_field: Id
          name_field: Title
          params:
            Filters:
              RecordState: [{Value: "ACTIVE", Comparison: "EQUALS"}]

Each entry produces AssetNode objects. The provider handles:
  - Calling the MCP server with the specified operation
  - Extracting items from the response
  - Mapping fields to node id/name/properties
  - Optional detail calls for richer data
  - Pagination (if response includes NextToken/nextToken)
"""

import logging
import time
from typing import Any, Optional

from ..provider_base import AssetProvider
from ...schema.model import AssetNode, AssetEdge, DiscoveryResult
from ...mcp_client import MCPClient, MCPPool

logger = logging.getLogger(__name__)


def _extract_field(data: dict, field_path: str) -> Any:
    """Extract a value from nested dict using dot/bracket notation.

    Supports:
      - "key" — simple key lookup
      - "key.subkey" — nested lookup
      - "key[]" — return list value directly
      - "key[].subfield" — pluck subfield from each list item
    """
    if not field_path or not data:
        return None

    parts = field_path.replace("[]", ".[].").split(".")
    current = data

    for i, part in enumerate(parts):
        if part == "" or part == "[]":
            # Current value should be a list, remaining parts apply to each item
            if isinstance(current, list):
                remaining = ".".join(p for p in parts[i + 1:] if p)
                if remaining:
                    return [_extract_field(item, remaining) for item in current if isinstance(item, dict)]
                return current
            continue
        elif isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            # Try to pluck field from list items
            return [item.get(part) for item in current if isinstance(item, dict)]
        else:
            return None

        if current is None:
            return None

    return current


class MCPDiscoveryEntry:
    """A single MCP discovery configuration entry."""

    def __init__(self, config: dict):
        self.service = config.get("service", "")
        self.operation = config.get("operation", "")
        self.node_type = config.get("node_type", "")
        self.items_key = config.get("items_key", "")
        self.id_field = config.get("id_field", "")
        self.name_field = config.get("name_field", "")
        self.params = config.get("params", {})
        self.detail_operation = config.get("detail_operation", "")
        self.detail_params = config.get("detail_params", {})
        self.max_results = config.get("max_results", 100)

    @property
    def inferred_node_type(self) -> str:
        """Infer node_type from service/operation if not set."""
        if self.node_type:
            return self.node_type
        # e.g. guardduty + ListDetectors → AWS::GuardDuty::Detector
        svc = self.service.capitalize()
        # Strip "List" / "Get" / "Describe" prefix, singularize
        resource = self.operation
        for prefix in ("List", "Get", "Describe", "Search"):
            if resource.startswith(prefix):
                resource = resource[len(prefix):]
                break
        # Simple singularize
        if resource.endswith("s") and not resource.endswith("ss"):
            resource = resource[:-1]
        return f"AWS::{svc}::{resource}"


class MCPProvider(AssetProvider):
    """Generic asset discovery provider using MCP servers.

    Discovers resources from any AWS service by calling MCP tools
    with configured operations. No per-service code needed.
    """

    name = "mcp"
    description = "Generic MCP-powered asset discovery"

    def __init__(self, mcp_config, region: str = "", account_id: str = "", **kwargs):
        super().__init__(region=region, account_id=account_id)
        self._mcp_config = mcp_config
        self._pool: Optional[MCPPool] = None
        self._entries: list[MCPDiscoveryEntry] = []

        # Parse discovery entries from config
        discovery_list = getattr(mcp_config, "discovery", [])
        if isinstance(discovery_list, list):
            self._entries = [MCPDiscoveryEntry(d) for d in discovery_list]

    def _get_pool(self) -> MCPPool:
        """Lazy-init MCP pool."""
        if self._pool is None:
            self._pool = MCPPool.from_config(self._mcp_config)
        return self._pool

    def discover(self) -> DiscoveryResult:
        """Discover all resources from configured MCP operations."""
        start = time.time()
        all_nodes: list[AssetNode] = []
        all_edges: list[AssetEdge] = []
        pool = self._get_pool()

        for entry in self._entries:
            try:
                nodes = self._discover_entry(pool, entry)
                all_nodes.extend(nodes)
                logger.info("MCP discovery: %s.%s → %d nodes",
                            entry.service, entry.operation, len(nodes))
            except Exception as e:
                logger.warning("MCP discovery failed for %s.%s: %s",
                               entry.service, entry.operation, e)

        duration_ms = int((time.time() - start) * 1000)
        logger.info("MCP provider complete: %d entries, %d nodes, %dms",
                    len(self._entries), len(all_nodes), duration_ms)

        return DiscoveryResult(
            resources=all_nodes,
            relationships=all_edges,
            duration_ms=duration_ms,
        )

    def _discover_entry(self, pool: MCPPool, entry: MCPDiscoveryEntry) -> list[AssetNode]:
        """Discover nodes for a single config entry."""
        nodes = []

        # Call the MCP server
        response = pool.call_tool("aws_api_call", {
            "service": entry.service,
            "operation": entry.operation,
            "parameters": entry.params,
        })

        if not response:
            return nodes

        # Extract items from response
        items = self._extract_items(response, entry)

        # If we got bare IDs (e.g. ListDetectors → DetectorIds[]), fetch details
        if entry.detail_operation and items:
            items = self._fetch_details(pool, entry, items)

        # Convert items to nodes
        for item in items:
            node = self._item_to_node(item, entry)
            if node:
                nodes.append(node)

        # Handle pagination
        next_token = response.get("NextToken") or response.get("nextToken")
        page = 1
        while next_token and len(nodes) < entry.max_results:
            page += 1
            params = {**entry.params, "NextToken": next_token}
            response = pool.call_tool("aws_api_call", {
                "service": entry.service,
                "operation": entry.operation,
                "parameters": params,
            })
            if not response:
                break
            page_items = self._extract_items(response, entry)
            if entry.detail_operation:
                page_items = self._fetch_details(pool, entry, page_items)
            for item in page_items:
                node = self._item_to_node(item, entry)
                if node:
                    nodes.append(node)
            next_token = response.get("NextToken") or response.get("nextToken")

        return nodes

    def _extract_items(self, response: dict, entry: MCPDiscoveryEntry) -> list:
        """Extract the list of items from an API response."""
        if entry.items_key:
            items = response.get(entry.items_key, [])
        elif entry.id_field and "[]" in entry.id_field:
            # e.g. "DetectorIds[]" — the response has a list field
            field_name = entry.id_field.replace("[]", "")
            raw = response.get(field_name, [])
            # Bare IDs → wrap in dicts
            if raw and isinstance(raw[0], str):
                items = [{entry.id_field.replace("[]", ""): v} for v in raw]
            else:
                items = raw
        else:
            # Try to find the first list value in response
            items = []
            for k, v in response.items():
                if isinstance(v, list) and v and k not in ("NextToken", "ResponseMetadata"):
                    items = v
                    break

        if not isinstance(items, list):
            return []
        return items

    def _fetch_details(self, pool: MCPPool, entry: MCPDiscoveryEntry, items: list) -> list:
        """Fetch detailed info for each item using detail_operation."""
        detailed = []
        for item in items:
            # Resolve params — replace $id with the item's ID
            item_id = self._get_item_id(item, entry)
            if not item_id:
                continue

            params = {}
            for k, v in entry.detail_params.items():
                if v == "$id":
                    params[k] = item_id
                else:
                    params[k] = v

            result = pool.call_tool("aws_api_call", {
                "service": entry.service,
                "operation": entry.detail_operation,
                "parameters": params,
            })
            if result:
                # Merge the ID back in
                if isinstance(result, dict):
                    result["_discovered_id"] = item_id
                detailed.append(result)

        return detailed

    def _get_item_id(self, item: Any, entry: MCPDiscoveryEntry) -> Optional[str]:
        """Extract the ID from an item."""
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            # Try id_field (without [] suffix)
            field = entry.id_field.replace("[]", "")
            if field in item:
                return str(item[field])
            # Try common ID patterns
            for key in ("Arn", "arn", "Id", "id", "ARN", "Identifier"):
                if key in item:
                    return str(item[key])
        return None

    def _item_to_node(self, item: Any, entry: MCPDiscoveryEntry) -> Optional[AssetNode]:
        """Convert an API response item to an AssetNode."""
        if not isinstance(item, dict):
            return None

        node_type = entry.inferred_node_type

        # Extract ID
        item_id = None
        if entry.id_field:
            item_id = _extract_field(item, entry.id_field.replace("[]", ""))
        if not item_id:
            item_id = item.get("_discovered_id")
        if not item_id:
            # Try common patterns
            for key in ("Arn", "arn", "ARN", "Id", "id", "FindingArn", "findingArn",
                        "DetectorId", "ResourceId"):
                if key in item:
                    item_id = str(item[key])
                    break
        if not item_id:
            return None

        # Extract name
        name = ""
        if entry.name_field:
            name = str(_extract_field(item, entry.name_field) or "")
        if not name:
            for key in ("Name", "name", "Title", "title", "DisplayName"):
                if key in item:
                    name = str(item[key])
                    break
        if not name:
            # Use last part of ID/ARN
            name = item_id.split("/")[-1].split(":")[-1]

        # Build properties (flatten top-level scalars)
        properties = {}
        for k, v in item.items():
            if k.startswith("_"):
                continue
            if isinstance(v, (str, int, float, bool)):
                properties[k] = v
            elif isinstance(v, list) and v and isinstance(v[0], str):
                properties[k] = ", ".join(v[:10])

        return AssetNode(
            id=item_id,
            node_type=node_type,
            name=name,
            provider="mcp",
            account_id=self.account_id,
            region=self.region,
            resource_type=node_type,
            properties=properties,
        )

    def get_resource_types(self) -> list[str]:
        """List resource types this provider discovers."""
        return [e.inferred_node_type for e in self._entries]

    def validate(self) -> list[str]:
        """Validate MCP connectivity."""
        errors = []
        if not self._entries:
            errors.append("No MCP discovery entries configured")
        try:
            pool = self._get_pool()
            tools = pool.list_tools()
            if not tools:
                errors.append("MCP server returned no tools")
        except Exception as e:
            errors.append(f"MCP server connection failed: {e}")
        return errors
