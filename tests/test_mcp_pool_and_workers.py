"""Tests for MCPPool (multi-server) and parallel provider execution."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bona.config import MCPConfig, MCPServerEntry, BonaConfig
from bona.mcp_client import MCPClient, MCPPool
from bona.workers import run_parallel, run_providers_parallel
from bona.schema.model import DiscoveryResult, AssetNode, AssetEdge


# ─── MCPPool Tests ────────────────────────────────────────────────────────────

class TestMCPPool:
    """Test MCPPool multi-server aggregation."""

    def test_from_config_legacy_single_server(self):
        cfg = MCPConfig(enabled=True, server_url="http://localhost:3000")
        pool = MCPPool.from_config(cfg)
        assert "default" in pool._clients
        assert len(pool._clients) == 1

    def test_from_config_multi_server(self):
        cfg = MCPConfig(
            enabled=True,
            servers=[
                MCPServerEntry(name="aws", server_url="http://localhost:3000"),
                MCPServerEntry(name="cmdb", command="npx @company/cmdb-mcp"),
                MCPServerEntry(name="disabled", server_url="http://x", enabled=False),
            ],
        )
        pool = MCPPool.from_config(cfg)
        assert "aws" in pool._clients
        assert "cmdb" in pool._clients
        assert "disabled" not in pool._clients

    def test_from_config_legacy_plus_servers(self):
        cfg = MCPConfig(
            enabled=True,
            server_url="http://localhost:3000",
            servers=[MCPServerEntry(name="custom", command="my-server")],
        )
        pool = MCPPool.from_config(cfg)
        assert "default" in pool._clients
        assert "custom" in pool._clients

    @patch.object(MCPClient, "list_tools")
    def test_list_tools_aggregates(self, mock_list):
        mock_list.return_value = [{"name": "tool_a"}, {"name": "tool_b"}]
        cfg = MCPConfig(
            enabled=True,
            servers=[
                MCPServerEntry(name="s1", server_url="http://s1"),
                MCPServerEntry(name="s2", server_url="http://s2"),
            ],
        )
        pool = MCPPool.from_config(cfg)
        tools = pool.list_tools()
        # Each server returns 2 tools, total 4
        assert len(tools) == 4

    @patch.object(MCPClient, "list_tools")
    def test_tool_filter(self, mock_list):
        mock_list.return_value = [{"name": "allowed"}, {"name": "blocked"}]
        cfg = MCPConfig(
            enabled=True,
            servers=[
                MCPServerEntry(name="s1", server_url="http://s1", tool_filter=["allowed"]),
            ],
        )
        pool = MCPPool.from_config(cfg)
        tools = pool.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "allowed"

    @patch.object(MCPClient, "list_tools")
    @patch.object(MCPClient, "call_tool")
    def test_call_tool_routes_correctly(self, mock_call, mock_list):
        mock_list.return_value = [{"name": "my_tool"}]
        mock_call.return_value = {"data": "result"}

        cfg = MCPConfig(enabled=True, servers=[
            MCPServerEntry(name="s1", server_url="http://s1"),
        ])
        pool = MCPPool.from_config(cfg)
        pool.list_tools()  # populate tool_map

        result = pool.call_tool("my_tool", {"arg": 1})
        assert result == {"data": "result"}
        mock_call.assert_called_once_with("my_tool", {"arg": 1})


# ─── Config Parsing Tests ─────────────────────────────────────────────────────

class TestMCPConfigParsing:
    """Test from_dict handles servers list."""

    def test_servers_list_parsed(self):
        cfg = BonaConfig.from_dict({
            "mcp": {
                "enabled": True,
                "servers": [
                    {"name": "aws", "server_url": "http://localhost:3000"},
                    {"name": "internal", "command": "my-mcp-server", "tool_filter": ["lookup"]},
                ],
            }
        })
        assert cfg.mcp.enabled is True
        assert len(cfg.mcp.servers) == 2
        assert cfg.mcp.servers[0].name == "aws"
        assert cfg.mcp.servers[1].command == "my-mcp-server"
        assert cfg.mcp.servers[1].tool_filter == ["lookup"]

    def test_empty_servers_defaults(self):
        cfg = BonaConfig.from_dict({"mcp": {"enabled": True}})
        assert cfg.mcp.servers == []


# ─── Parallel Provider Tests ──────────────────────────────────────────────────

class TestRunProvidersParallel:
    """Test parallel provider execution."""

    def test_empty_providers(self):
        result = run_providers_parallel([])
        assert result.resources == []
        assert result.relationships == []

    def test_single_provider_fast_path(self):
        provider = MagicMock()
        provider.name = "test"
        provider.discover.return_value = DiscoveryResult(
            resources=[AssetNode(id="n1", node_type="AWS::S3::Bucket", name="b1", properties={})],
            relationships=[],
        )
        result = run_providers_parallel([provider])
        provider.discover.assert_called_once()
        assert len(result.resources) == 1

    def test_multiple_providers_merged(self):
        p1 = MagicMock()
        p1.name = "aws"
        p1.discover.return_value = DiscoveryResult(
            resources=[AssetNode(id="n1", node_type="AWS::S3::Bucket", name="b1", properties={})],
            relationships=[],
            duration_ms=100,
        )
        p2 = MagicMock()
        p2.name = "gcp"
        p2.discover.return_value = DiscoveryResult(
            resources=[AssetNode(id="n2", node_type="GCP::Storage::Bucket", name="b2", properties={})],
            relationships=[AssetEdge(source_id="n2", target_id="n1", edge_type="MIRRORS")],
            duration_ms=50,
        )

        result = run_providers_parallel([p1, p2], max_workers=2)
        assert len(result.resources) == 2
        assert len(result.relationships) == 1
        assert result.duration_ms == 150

    def test_provider_failure_handled(self):
        p1 = MagicMock()
        p1.name = "good"
        p1.discover.return_value = DiscoveryResult(
            resources=[AssetNode(id="n1", node_type="AWS::EC2::VPC", name="vpc1", properties={})],
            relationships=[],
        )
        p2 = MagicMock()
        p2.name = "bad"
        p2.discover.side_effect = RuntimeError("connection failed")

        result = run_providers_parallel([p1, p2])
        # p1 results preserved, p2 failure handled gracefully
        assert len(result.resources) >= 1


class TestRunParallel:
    """Test the base run_parallel utility."""

    def test_basic_parallel(self):
        def add(a, b):
            return a + b

        tasks = [
            ("task1", add, [1, 2]),
            ("task2", add, [3, 4]),
        ]
        results = run_parallel(tasks, max_workers=2)
        assert results["task1"] == 3
        assert results["task2"] == 7

    def test_exception_captured(self):
        def fail():
            raise ValueError("oops")

        tasks = [("bad", fail, [])]
        results = run_parallel(tasks)
        # Failed tasks return ([], []) sentinel
        assert results["bad"] == ([], [])
