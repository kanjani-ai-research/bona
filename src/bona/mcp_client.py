"""MCP Client — use AWS MCP Server for dynamic API discovery and LLM tool use.

Instead of hardcoding boto3 calls per service, the MCP client allows:
1. LLM enrichment to call AWS APIs dynamically during reasoning
2. Generic discovery of any AWS service without writing a plugin
3. Integration with the AWS MCP Server (GA)

Configuration (bona.yaml):
    mcp:
      enabled: true
      server_url: "http://localhost:3000"  # local AWS MCP server
      # Or use stdio transport:
      # command: "npx @aws/mcp-server"

Usage:
    from bona.mcp_client import MCPClient
    client = MCPClient(server_url="http://localhost:3000")
    result = client.call_tool("aws_api_call", {
        "service": "guardduty",
        "operation": "list_findings",
        "parameters": {"DetectorId": "abc123", "MaxResults": 10}
    })
"""

import json
import logging
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MCPClient:
    """Client for AWS MCP Server — enables dynamic AWS API calls.

    Supports two transport modes:
    - HTTP: connect to a running MCP server
    - Stdio: spawn the MCP server as a subprocess
    """

    def __init__(self, server_url: str = "", command: str = ""):
        self.server_url = server_url
        self.command = command
        self._process = None

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Call an MCP tool and return the result.

        For AWS MCP Server, the main tool is 'aws_api_call':
            call_tool("aws_api_call", {
                "service": "guardduty",
                "operation": "ListFindings",
                "parameters": {"DetectorId": "...", "MaxResults": 10}
            })
        """
        if self.server_url:
            return self._call_http(tool_name, arguments)
        elif self.command:
            return self._call_stdio(tool_name, arguments)
        else:
            raise RuntimeError("MCP client not configured — set server_url or command")

    def _call_http(self, tool_name: str, arguments: dict) -> Any:
        """Call MCP tool via HTTP transport."""
        import urllib.request

        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
            "id": 1,
        }).encode()

        req = urllib.request.Request(
            self.server_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                if "result" in data:
                    content = data["result"].get("content", [])
                    if content and content[0].get("type") == "text":
                        return json.loads(content[0]["text"])
                    return content
                elif "error" in data:
                    logger.warning("MCP error: %s", data["error"])
                    return None
        except Exception as e:
            logger.warning("MCP HTTP call failed: %s", e)
            return None

    def _call_stdio(self, tool_name: str, arguments: dict) -> Any:
        """Call MCP tool via stdio transport (spawn server process)."""
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
            "id": 1,
        })

        try:
            result = subprocess.run(
                self.command.split(),
                input=payload,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout:
                data = json.loads(result.stdout)
                if "result" in data:
                    content = data["result"].get("content", [])
                    if content and content[0].get("type") == "text":
                        return json.loads(content[0]["text"])
                return data
        except Exception as e:
            logger.warning("MCP stdio call failed: %s", e)
        return None

    def discover_service(self, service: str, operation: str, **params) -> Any:
        """Convenience: call an AWS API via MCP."""
        return self.call_tool("aws_api_call", {
            "service": service,
            "operation": operation,
            "parameters": params,
        })

    def list_tools(self) -> list[dict]:
        """List available MCP tools."""
        if self.server_url:
            import urllib.request
            payload = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}).encode()
            req = urllib.request.Request(self.server_url, data=payload, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                    return data.get("result", {}).get("tools", [])
            except Exception:
                pass
        return []


class MCPPool:
    """Manages multiple MCP servers and aggregates their tools.

    Routes tool calls to the correct server based on tool name.
    Supports tool_filter per server to limit exposed tools.

    Usage:
        from bona.config import MCPConfig
        pool = MCPPool.from_config(mcp_config)
        tools = pool.list_tools()  # aggregated from all servers
        result = pool.call_tool("my_custom_tool", {...})  # routed to correct server
    """

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}  # name -> client
        self._tool_map: dict[str, str] = {}  # tool_name -> server_name
        self._tool_filters: dict[str, list[str]] = {}  # server_name -> allowed tools
        self._tools_cache: list[dict] = []

    @classmethod
    def from_config(cls, mcp_config) -> "MCPPool":
        """Build pool from MCPConfig (supports legacy single-server and multi-server)."""
        pool = cls()

        # Legacy single-server support
        if mcp_config.server_url or mcp_config.command:
            client = MCPClient(server_url=mcp_config.server_url, command=mcp_config.command)
            pool._clients["default"] = client
            pool._tool_filters["default"] = []

        # Multi-server entries
        for entry in getattr(mcp_config, "servers", []):
            if not entry.enabled:
                continue
            client = MCPClient(server_url=entry.server_url, command=entry.command)
            pool._clients[entry.name] = client
            pool._tool_filters[entry.name] = entry.tool_filter

        return pool

    def list_tools(self) -> list[dict]:
        """Aggregate tools from all connected servers."""
        if self._tools_cache:
            return self._tools_cache

        all_tools = []
        for name, client in self._clients.items():
            try:
                tools = client.list_tools()
                allowed = self._tool_filters.get(name, [])
                for tool in tools:
                    tool_name = tool.get("name", "")
                    if allowed and tool_name not in allowed:
                        continue
                    self._tool_map[tool_name] = name
                    all_tools.append(tool)
            except Exception as e:
                logger.warning("Failed to list tools from MCP server '%s': %s", name, e)

        self._tools_cache = all_tools
        logger.info("MCPPool: %d tools from %d servers", len(all_tools), len(self._clients))
        return all_tools

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Route a tool call to the correct server."""
        server_name = self._tool_map.get(tool_name)
        if not server_name:
            # Try all servers as fallback
            for name, client in self._clients.items():
                try:
                    result = client.call_tool(tool_name, arguments)
                    if result is not None:
                        self._tool_map[tool_name] = name
                        return result
                except Exception:
                    continue
            logger.warning("No MCP server handles tool '%s'", tool_name)
            return None

        client = self._clients[server_name]
        return client.call_tool(tool_name, arguments)
