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
