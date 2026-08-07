"""Tests for OSCAL catalog, prompt manager, and compliance discovery."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bona.oscal import load_catalog, get_control, search_controls, map_rule_to_controls
from bona.prompts import list_prompts, load_prompt, render_prompt
from bona.scan.engine import Query, Finding, run_scan
from bona.schema.model import AssetNode, AssetEdge


class TestOSCAL:
    """Test OSCAL catalog loading and control lookups."""

    @pytest.fixture
    def catalog(self):
        return load_catalog()

    def test_catalog_loads(self, catalog):
        assert len(catalog) > 1000  # 1196 controls

    def test_get_control_by_id(self, catalog):
        ctrl = get_control(catalog, "AC-2")
        assert ctrl is not None
        assert ctrl["title"] == "Account Management"
        assert ctrl["group_id"] == "ac"

    def test_get_control_case_insensitive(self, catalog):
        ctrl = get_control(catalog, "ac-2")
        assert ctrl is not None
        assert ctrl["id"] == "AC-2"

    def test_get_control_enhancement(self, catalog):
        ctrl = get_control(catalog, "AC-2(1)")
        assert ctrl is not None

    def test_get_control_not_found(self, catalog):
        ctrl = get_control(catalog, "ZZ-99")
        assert ctrl is None

    def test_search_controls(self, catalog):
        results = search_controls(catalog, "encryption")
        assert len(results) > 0
        assert any("encrypt" in r["title"].lower() or "encrypt" in r["description"].lower() for r in results)

    def test_map_rule_access_keys(self, catalog):
        matches = map_rule_to_controls(catalog, "ACCESS_KEYS_ROTATED")
        assert len(matches) > 0
        control_ids = [m["id"] for m in matches]
        assert "AC-2" in control_ids or "IA-5" in control_ids

    def test_map_rule_encrypted_volumes(self, catalog):
        matches = map_rule_to_controls(catalog, "ENCRYPTED_VOLUMES", "EBS volumes encrypted")
        assert len(matches) > 0
        control_ids = [m["id"] for m in matches]
        assert "SC-28" in control_ids or "SC-13" in control_ids

    def test_map_rule_cloudtrail(self, catalog):
        matches = map_rule_to_controls(catalog, "CLOUD_TRAIL_ENABLED")
        assert len(matches) > 0
        control_ids = [m["id"] for m in matches]
        assert "AU-2" in control_ids or "AU-3" in control_ids

    def test_map_rule_no_match(self, catalog):
        matches = map_rule_to_controls(catalog, "TOTALLY_MADE_UP_RULE_XYZ")
        assert len(matches) == 0


class TestPrompts:
    """Test prompt template loading and rendering."""

    def test_list_prompts(self):
        prompts = list_prompts()
        assert len(prompts) >= 5
        names = [p["name"] for p in prompts]
        assert "bona-system" in names
        assert "compliance-mapping" in names
        assert "remediation-guidance" in names

    def test_load_prompt(self):
        prompt = load_prompt("compliance-mapping")
        assert prompt is not None
        assert "system" in prompt or "user_template" in prompt
        assert prompt.get("version") == "1.0"

    def test_load_prompt_not_found(self):
        prompt = load_prompt("nonexistent-prompt")
        assert prompt is None

    def test_render_simple_variables(self):
        template = "Rule: {{rule_name}}, Type: {{resource_type}}"
        result = render_prompt(template, {"rule_name": "S3_BUCKET_SSL", "resource_type": "AWS::S3::Bucket"})
        assert result == "Rule: S3_BUCKET_SSL, Type: AWS::S3::Bucket"

    def test_render_if_block_true(self):
        template = "Start{{#if context}} Context: {{context}}{{/if}} End"
        result = render_prompt(template, {"context": "some data"})
        assert "Context: some data" in result

    def test_render_if_block_false(self):
        template = "Start{{#if context}} Context: {{context}}{{/if}} End"
        result = render_prompt(template, {"context": ""})
        assert "Context:" not in result

    def test_render_each_block(self):
        template = "Controls:{{#each controls}} - {{id}}: {{title}}{{/each}}"
        result = render_prompt(template, {"controls": [
            {"id": "AC-2", "title": "Account Mgmt"},
            {"id": "SC-8", "title": "Transmission"},
        ]})
        assert "AC-2: Account Mgmt" in result
        assert "SC-8: Transmission" in result

    def test_render_json_variable(self):
        template = "Data: {{data}}"
        result = render_prompt(template, {"data": {"key": "value"}})
        assert '"key"' in result
        assert '"value"' in result


class TestComplianceIntegration:
    """Test compliance → OSCAL → prompt pipeline."""

    def test_failing_rule_maps_to_control(self):
        catalog = load_catalog()
        # Simulate: a resource fails ECS_CONTAINERS_READONLY_ACCESS
        matches = map_rule_to_controls(catalog, "ECS_CONTAINERS_READONLY_ACCESS", "read-only access to filesystem")
        # Should map to access control family
        assert len(matches) > 0

    def test_prompt_renders_with_oscal_data(self):
        catalog = load_catalog()
        ctrl = get_control(catalog, "SC-28")

        prompt = load_prompt("remediation-guidance")
        assert prompt is not None

        rendered = render_prompt(prompt["user_template"], {
            "resource_id": "arn:aws:ec2:us-east-1:123:volume/vol-abc",
            "resource_type": "AWS::EC2::Volume",
            "rule_name": "ENCRYPTED_VOLUMES",
            "rule_description": "Checks whether EBS volumes are encrypted",
            "nist_control_id": ctrl["id"],
            "nist_control_title": ctrl["title"],
            "annotation": "Volume is not encrypted",
            "kb_context": "",
            "resource_properties": "",
        })

        assert "ENCRYPTED_VOLUMES" in rendered
        assert "SC-28" in rendered
        assert "Protection of Information at Rest" in rendered

    def test_scan_finding_has_needed_fields(self):
        """Verify scan findings have the fields needed for compliance enrichment."""
        nodes = [AssetNode(
            id="arn:aws:ec2:us-east-1:123:volume/vol-abc",
            node_type="AWS::EC2::Volume",
            name="vol-abc",
            resource_type="AWS::EC2::Volume",
            properties={"encrypted": False},
        )]
        edges = []

        from bona.scan.queries import BUILTIN_QUERIES
        result = run_scan(nodes, edges, BUILTIN_QUERIES, names=["ec2-unencrypted-volume"])
        assert result.finding_count == 1
        finding = result.findings[0]
        assert finding.resource_id == "arn:aws:ec2:us-east-1:123:volume/vol-abc"
        assert finding.query_name == "ec2-unencrypted-volume"
        assert finding.remediation != ""
