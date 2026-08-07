"""Tests for MCPProvider — generic MCP-powered discovery."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bona.config import MCPConfig, MCPServerEntry, BonaConfig
from bona.providers.mcp import MCPProvider, MCPDiscoveryEntry, _extract_field
from bona.schema.model import DiscoveryResult


class TestExtractField:
    """Test the field extraction helper."""

    def test_simple_key(self):
        assert _extract_field({"name": "foo"}, "name") == "foo"

    def test_nested_key(self):
        assert _extract_field({"a": {"b": "c"}}, "a.b") == "c"

    def test_list_field(self):
        assert _extract_field({"ids": [1, 2, 3]}, "ids[]") == [1, 2, 3]

    def test_pluck_from_list(self):
        data = {"items": [{"id": "a"}, {"id": "b"}]}
        assert _extract_field(data, "items[].id") == ["a", "b"]

    def test_missing_field(self):
        assert _extract_field({"x": 1}, "y") is None

    def test_empty_data(self):
        assert _extract_field({}, "x") is None
        assert _extract_field(None, "x") is None


class TestMCPDiscoveryEntry:
    """Test discovery entry config parsing."""

    def test_explicit_node_type(self):
        entry = MCPDiscoveryEntry({
            "service": "guardduty",
            "operation": "ListDetectors",
            "node_type": "AWS::GuardDuty::Detector",
        })
        assert entry.inferred_node_type == "AWS::GuardDuty::Detector"

    def test_inferred_node_type(self):
        entry = MCPDiscoveryEntry({
            "service": "guardduty",
            "operation": "ListDetectors",
        })
        assert entry.inferred_node_type == "AWS::Guardduty::Detector"

    def test_inferred_describe(self):
        entry = MCPDiscoveryEntry({
            "service": "ec2",
            "operation": "DescribeInstances",
        })
        assert entry.inferred_node_type == "AWS::Ec2::Instance"


class TestMCPProviderDiscover:
    """Test MCPProvider.discover() with mocked MCP calls."""

    def _make_provider(self, discovery_config, mock_pool):
        cfg = MCPConfig(
            enabled=True,
            servers=[MCPServerEntry(name="aws", server_url="http://localhost:3000")],
            discovery=discovery_config,
        )
        provider = MCPProvider(cfg, region="us-east-1", account_id="123456789012")
        provider._pool = mock_pool
        return provider

    def test_simple_list_operation(self):
        """Test discovering from a simple List operation."""
        mock_pool = MagicMock()
        mock_pool.call_tool.return_value = {
            "Findings": [
                {"FindingArn": "arn:aws:inspector2:us-east-1:123:finding/abc", "Title": "CVE-2024-001", "Severity": "HIGH"},
                {"FindingArn": "arn:aws:inspector2:us-east-1:123:finding/def", "Title": "CVE-2024-002", "Severity": "MEDIUM"},
            ]
        }

        provider = self._make_provider([{
            "service": "inspector2",
            "operation": "ListFindings",
            "node_type": "AWS::Inspector2::Finding",
            "items_key": "Findings",
            "id_field": "FindingArn",
            "name_field": "Title",
        }], mock_pool)

        result = provider.discover()
        assert len(result.resources) == 2
        assert result.resources[0].id == "arn:aws:inspector2:us-east-1:123:finding/abc"
        assert result.resources[0].name == "CVE-2024-001"
        assert result.resources[0].node_type == "AWS::Inspector2::Finding"
        assert result.resources[0].properties["Severity"] == "HIGH"

    def test_list_with_detail_fetch(self):
        """Test ListDetectors → GetDetector pattern."""
        mock_pool = MagicMock()
        # First call: list returns IDs
        # Second call: detail returns full object
        mock_pool.call_tool.side_effect = [
            {"DetectorIds": ["det-abc123"]},
            {"CreatedAt": "2024-01-01", "Status": "ENABLED", "ServiceRole": "arn:aws:iam::123:role/GuardDuty"},
        ]

        provider = self._make_provider([{
            "service": "guardduty",
            "operation": "ListDetectors",
            "node_type": "AWS::GuardDuty::Detector",
            "id_field": "DetectorIds[]",
            "detail_operation": "GetDetector",
            "detail_params": {"DetectorId": "$id"},
        }], mock_pool)

        result = provider.discover()
        assert len(result.resources) == 1
        assert result.resources[0].id == "det-abc123"
        assert result.resources[0].properties["Status"] == "ENABLED"

    def test_pagination(self):
        """Test automatic pagination via NextToken."""
        mock_pool = MagicMock()
        mock_pool.call_tool.side_effect = [
            {"findings": [{"Id": "f1", "Title": "Finding 1"}], "NextToken": "page2"},
            {"findings": [{"Id": "f2", "Title": "Finding 2"}]},
        ]

        provider = self._make_provider([{
            "service": "securityhub",
            "operation": "GetFindings",
            "node_type": "AWS::SecurityHub::Finding",
            "items_key": "findings",
            "id_field": "Id",
            "name_field": "Title",
        }], mock_pool)

        result = provider.discover()
        assert len(result.resources) == 2
        assert result.resources[1].name == "Finding 2"
        # Should have made 2 calls
        assert mock_pool.call_tool.call_count == 2

    def test_auto_detect_items_key(self):
        """When items_key not set, auto-detect first list in response."""
        mock_pool = MagicMock()
        mock_pool.call_tool.return_value = {
            "ResponseMetadata": {},
            "Buckets": [
                {"Name": "my-bucket", "CreationDate": "2024-01-01"},
            ],
        }

        provider = self._make_provider([{
            "service": "s3",
            "operation": "ListBuckets",
            "node_type": "AWS::S3::Bucket",
            "id_field": "Name",
            "name_field": "Name",
        }], mock_pool)

        result = provider.discover()
        assert len(result.resources) == 1
        assert result.resources[0].name == "my-bucket"

    def test_empty_response(self):
        """Test graceful handling of empty responses."""
        mock_pool = MagicMock()
        mock_pool.call_tool.return_value = {"Findings": []}

        provider = self._make_provider([{
            "service": "inspector2",
            "operation": "ListFindings",
            "items_key": "Findings",
        }], mock_pool)

        result = provider.discover()
        assert len(result.resources) == 0

    def test_mcp_failure_handled(self):
        """Test graceful handling when MCP call fails."""
        mock_pool = MagicMock()
        mock_pool.call_tool.return_value = None

        provider = self._make_provider([{
            "service": "badservice",
            "operation": "BadOp",
        }], mock_pool)

        result = provider.discover()
        assert len(result.resources) == 0

    def test_multiple_entries(self):
        """Test discovering from multiple service entries."""
        mock_pool = MagicMock()
        mock_pool.call_tool.side_effect = [
            {"Findings": [{"Id": "f1", "Title": "SH Finding"}]},
            {"findings": [{"findingArn": "arn:insp:f2", "title": "Inspector Finding"}]},
        ]

        provider = self._make_provider([
            {"service": "securityhub", "operation": "GetFindings", "items_key": "Findings",
             "node_type": "AWS::SecurityHub::Finding", "id_field": "Id", "name_field": "Title"},
            {"service": "inspector2", "operation": "ListFindings", "items_key": "findings",
             "node_type": "AWS::Inspector2::Finding", "id_field": "findingArn", "name_field": "title"},
        ], mock_pool)

        result = provider.discover()
        assert len(result.resources) == 2
        types = {n.node_type for n in result.resources}
        assert "AWS::SecurityHub::Finding" in types
        assert "AWS::Inspector2::Finding" in types


class TestMCPConfigDiscoveryParsing:
    """Test config parsing of discovery entries."""

    def test_discovery_entries_parsed(self):
        cfg = BonaConfig.from_dict({
            "mcp": {
                "enabled": True,
                "servers": [{"name": "aws", "server_url": "http://localhost:3000"}],
                "discovery": [
                    {"service": "guardduty", "operation": "ListDetectors", "node_type": "AWS::GuardDuty::Detector"},
                    {"service": "inspector2", "operation": "ListFindings"},
                ],
            }
        })
        assert len(cfg.mcp.discovery) == 2
        assert cfg.mcp.discovery[0]["service"] == "guardduty"

    def test_get_resource_types(self):
        cfg = MCPConfig(
            enabled=True,
            servers=[MCPServerEntry(name="aws", server_url="http://localhost:3000")],
            discovery=[
                {"service": "guardduty", "operation": "ListDetectors", "node_type": "AWS::GuardDuty::Detector"},
                {"service": "inspector2", "operation": "ListFindings", "node_type": "AWS::Inspector2::Finding"},
            ],
        )
        provider = MCPProvider(cfg, region="us-east-1")
        types = provider.get_resource_types()
        assert "AWS::GuardDuty::Detector" in types
        assert "AWS::Inspector2::Finding" in types
