"""Integration test — LLM enrichment with live AWS MCP server.

Requires:
  - AWS API MCP server running on http://localhost:3000
    Install: pip install awslabs.aws-api-mcp-server
    Run:     python -m awslabs.aws_api_mcp_server --transport http --port 3000
  - Valid AWS credentials (profile nw or default)
  - Bedrock access in us-east-1

Run with:
  pytest tests/test_mcp_integration.py -v

Skipped automatically if MCP server is not reachable on localhost:3000.
"""

import json
import sys
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bona.config import MCPConfig, MCPServerEntry
from bona.mcp_client import MCPClient, MCPPool
from bona.enrichment.llm_enrichment import (
    classify_properties,
    validate_candidates,
    generate_descriptions,
    llm_enrich,
)
from bona.schema.model import AssetNode
from bona.relationship_learner import RelationshipRules


def mcp_server_available(url: str = "http://localhost:3000") -> bool:
    """Check if MCP server is responding."""
    payload = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return "result" in data
    except Exception:
        return False


# Skip all tests if MCP server not available or --run-integration not set
pytestmark = pytest.mark.skipif(
    not mcp_server_available(),
    reason="AWS MCP server not running on localhost:3000 (start with: npx @aws/mcp-server)"
)


class TestMCPServerConnection:
    """Test basic connectivity to live MCP server."""

    def test_list_tools(self):
        client = MCPClient(server_url="http://localhost:3000")
        tools = client.list_tools()
        assert len(tools) > 0
        # AWS MCP server should expose at least one tool
        tool_names = [t.get("name", "") for t in tools]
        assert any("aws" in name.lower() for name in tool_names), f"Expected AWS tools, got: {tool_names}"

    def test_pool_from_config(self):
        cfg = MCPConfig(
            enabled=True,
            servers=[MCPServerEntry(name="aws", server_url="http://localhost:3000")],
        )
        pool = MCPPool.from_config(cfg)
        tools = pool.list_tools()
        assert len(tools) > 0


class TestMCPToolCall:
    """Test actual tool invocations against live MCP server."""

    def test_describe_vpcs(self):
        client = MCPClient(server_url="http://localhost:3000")
        result = client.call_tool("aws_api_call", {
            "service": "ec2",
            "operation": "DescribeVpcs",
            "parameters": {"MaxResults": 5},
        })
        # Should return something (even if empty VPC list)
        assert result is not None

    def test_list_buckets(self):
        client = MCPClient(server_url="http://localhost:3000")
        result = client.call_tool("aws_api_call", {
            "service": "s3",
            "operation": "ListBuckets",
            "parameters": {},
        })
        assert result is not None


class TestMCPEnrichmentIntegration:
    """Test full enrichment pipeline with live MCP server + Bedrock."""

    @pytest.fixture
    def aws_session(self):
        import boto3
        return boto3.Session(profile_name="nw")

    @pytest.fixture
    def mcp_pool(self):
        cfg = MCPConfig(
            enabled=True,
            servers=[MCPServerEntry(name="aws", server_url="http://localhost:3000")],
        )
        pool = MCPPool.from_config(cfg)
        tools = pool.list_tools()
        return pool, [{"toolSpec": t} for t in tools]

    @pytest.fixture
    def sample_nodes(self):
        return [
            AssetNode(
                id="arn:aws:ec2:us-east-1:123456789012:vpc/vpc-abc123",
                node_type="AWS::EC2::VPC",
                name="vpc-abc123",
                properties={"CidrBlock": "10.0.0.0/16", "VpcId": "vpc-abc123", "IsDefault": "false"},
            ),
            AssetNode(
                id="arn:aws:ec2:us-east-1:123456789012:subnet/subnet-def456",
                node_type="AWS::EC2::Subnet",
                name="subnet-def456",
                properties={"VpcId": "vpc-abc123", "CidrBlock": "10.0.1.0/24", "AvailabilityZone": "us-east-1a"},
            ),
            AssetNode(
                id="arn:aws:s3:::my-bucket",
                node_type="AWS::S3::Bucket",
                name="my-bucket",
                properties={"BucketName": "my-bucket", "CreationDate": "2024-01-01"},
            ),
        ]

    def test_classify_with_mcp(self, aws_session, mcp_pool, sample_nodes):
        """Classify properties with MCP tools available for Claude to use."""
        pool, tools = mcp_pool
        result = classify_properties(
            aws_session, "us-east-1", sample_nodes,
            model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            mcp_client=pool,
            mcp_tools=tools,
        )
        # Should return classifications (may or may not use tools)
        assert isinstance(result, list)
        # VpcId on subnet should be classified as a relationship
        vpc_refs = [c for c in result if c.get("property_name") == "VpcId"]
        if vpc_refs:
            assert vpc_refs[0].get("is_relationship") is True

    def test_describe_with_mcp(self, aws_session, mcp_pool, sample_nodes):
        """Generate descriptions with MCP tools available."""
        pool, tools = mcp_pool
        result = generate_descriptions(
            aws_session, "us-east-1", sample_nodes,
            model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            mcp_client=pool,
            mcp_tools=tools,
        )
        assert isinstance(result, dict)
        # Should generate at least one description
        assert len(result) > 0

    def test_full_enrich_with_mcp(self, aws_session, sample_nodes):
        """Full llm_enrich pipeline with MCP enabled."""
        rules = RelationshipRules()
        mcp_cfg = MCPConfig(
            enabled=True,
            servers=[MCPServerEntry(name="aws", server_url="http://localhost:3000")],
        )
        updated_rules, edges, descriptions = llm_enrich(
            aws_session, "us-east-1", sample_nodes, rules,
            mcp_config=mcp_cfg,
        )
        # Should complete without error and return results
        assert isinstance(updated_rules, RelationshipRules)
        assert isinstance(descriptions, dict)
