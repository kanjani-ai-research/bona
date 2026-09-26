"""Tests for LLM enrichment — PromptsConfig wiring and MCP integration."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bona.config import BonaConfig, LLMConfig, MCPConfig, PromptsConfig
from bona.enrichment.llm_enrichment import (
    _resolve_prompt,
    _call_bedrock,
    _call_bedrock_with_tools,
    classify_properties,
    generate_descriptions,
    llm_enrich,
)
from bona.schema.model import AssetNode
from bona.relationship_learner import RelationshipRules


class TestResolvePrompt:
    """Test _resolve_prompt loads managed prompts or falls back."""

    def test_fallback_when_no_prompt_found(self):
        prompt, system, arn = _resolve_prompt(
            "nonexistent-prompt",
            {"foo": "bar"},
            "fallback text",
            "fallback system",
        )
        assert prompt == "fallback text"
        assert system == "fallback system"
        assert arn == ""

    @patch("bona.enrichment.llm_enrichment.load_prompt")
    def test_uses_managed_prompt(self, mock_load):
        mock_load.return_value = {
            "system": "managed system",
            "user_template": "Classify: {{nodes_json}}",
            "bedrock_prompt_id": "ABC123",
        }
        prompt, system, arn = _resolve_prompt(
            "classify-relationships",
            {"nodes_json": "[{...}]"},
            "fallback",
            "fallback sys",
        )
        assert "Classify: [{...}]" == prompt
        assert system == "managed system"
        assert arn == "ABC123"


class TestConfigParsing:
    """Test that from_dict parses prompts and mcp sections."""

    def test_prompts_parsed(self):
        cfg = BonaConfig.from_dict({
            "prompts": {
                "classify": {"source": "local", "local_name": "my-classify"},
                "system": {"source": "local", "local_name": "my-system"},
            }
        })
        assert cfg.prompts.classify["local_name"] == "my-classify"
        assert cfg.prompts.system["local_name"] == "my-system"
        # Defaults preserved for unspecified
        assert cfg.prompts.remediation["local_name"] == "remediation-guidance"

    def test_mcp_parsed(self):
        cfg = BonaConfig.from_dict({
            "mcp": {
                "enabled": True,
                "server_url": "http://localhost:3000",
            }
        })
        assert cfg.mcp.enabled is True
        assert cfg.mcp.server_url == "http://localhost:3000"

    def test_defaults_when_missing(self):
        cfg = BonaConfig.from_dict({})
        assert cfg.mcp.enabled is False
        assert cfg.prompts.classify["source"] == "local"


class TestMCPToolLoop:
    """Test _call_bedrock_with_tools agentic loop."""

    @patch("bona.enrichment.llm_enrichment.boto3.Session")
    def test_direct_response_no_tools(self, _):
        session = MagicMock()
        client = MagicMock()
        session.client.return_value = client
        client.converse.return_value = {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": '["result"]'}]}},
        }
        mcp = MagicMock()
        result = _call_bedrock_with_tools(
            session, "us-east-1", "model", "prompt",
            [{"toolSpec": {"name": "t1"}}], mcp,
        )
        assert result == '["result"]'
        mcp.call_tool.assert_not_called()

    @patch("bona.enrichment.llm_enrichment.boto3.Session")
    def test_tool_use_loop(self, _):
        session = MagicMock()
        client = MagicMock()
        session.client.return_value = client

        # First call: model wants a tool
        tool_response = {
            "stopReason": "tool_use",
            "output": {"message": {"content": [
                {"toolUse": {"toolUseId": "t1", "name": "aws_api_call", "input": {"service": "ec2"}}}
            ]}},
        }
        # Second call: model returns final answer
        final_response = {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": "done"}]}},
        }
        client.converse.side_effect = [tool_response, final_response]

        mcp = MagicMock()
        mcp.call_tool.return_value = {"Instances": []}

        result = _call_bedrock_with_tools(
            session, "us-east-1", "model", "prompt",
            [{"toolSpec": {"name": "aws_api_call"}}], mcp,
        )
        assert result == "done"
        mcp.call_tool.assert_called_once_with("aws_api_call", {"service": "ec2"})


class TestClassifyWithPrompts:
    """Test classify_properties uses PromptsConfig."""

    @patch("bona.enrichment.llm_enrichment._call_bedrock")
    def test_uses_prompts_config(self, mock_bedrock):
        mock_bedrock.return_value = '[]'
        session = MagicMock()
        nodes = [AssetNode(id="arn:test", node_type="AWS::EC2::Instance", name="i-123",
                           properties={"VpcId": "vpc-abc", "Name": "test"})]
        prompts_cfg = PromptsConfig(
            classify={"source": "local", "local_name": "classify-relationships"}
        )
        result = classify_properties(session, "us-east-1", nodes,
                                     prompts_config=prompts_cfg)
        assert mock_bedrock.called


class TestLLMEnrichMCP:
    """Test llm_enrich passes MCP config through."""

    @patch("bona.enrichment.llm_enrichment._call_bedrock")
    @patch("bona.enrichment.llm_enrichment.MCPClient")
    def test_mcp_disabled_no_client(self, mock_mcp_cls, mock_bedrock):
        mock_bedrock.return_value = '[]'
        session = MagicMock()
        rules = RelationshipRules()
        nodes = []
        mcp_cfg = MCPConfig(enabled=False)
        llm_enrich(session, "us-east-1", nodes, rules, mcp_config=mcp_cfg)
        mock_mcp_cls.assert_not_called()
