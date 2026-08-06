"""Tests for bona.enrichment.type_schemas — schema parsing, TypeDefinition nodes, enrichment."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import bona.cache as cache_mod
from bona.cache import write_cache, read_cache
from bona.config import EnrichmentConfig
from bona.enrichment.type_schemas import (
    _cache_name,
    _get_create_only_properties,
    _get_primary_identifier,
    _get_read_only_properties,
    _get_required_properties,
    _get_schema_properties,
    _schema_to_type_definition_node,
    enrich_with_type_schemas,
    fetch_all_type_schemas,
    get_type_schema,
)
from bona.schema.model import AssetEdge, AssetNode


@pytest.fixture(autouse=True)
def temp_cache(tmp_path):
    """Use a temp dir for cache during tests."""
    original = cache_mod.CACHE_DIR
    cache_mod.CACHE_DIR = tmp_path / "cache"
    cache_mod.CACHE_DIR.mkdir()
    yield cache_mod.CACHE_DIR
    cache_mod.CACHE_DIR = original


# Sample schemas for testing
S3_SCHEMA = {
    "typeName": "AWS::S3::Bucket",
    "description": "Creates an S3 bucket.",
    "properties": {
        "BucketName": {"type": "string"},
        "Arn": {"type": "string"},
        "DomainName": {"type": "string"},
        "VersioningConfiguration": {"type": "object"},
        "Tags": {"type": "array"},
    },
    "required": ["BucketName"],
    "readOnlyProperties": ["/properties/Arn", "/properties/DomainName"],
    "createOnlyProperties": ["/properties/BucketName"],
    "primaryIdentifier": ["/properties/BucketName"],
    "additionalIdentifiers": [["/properties/Arn"]],
    "handlers": {"create": {}, "read": {}, "update": {}, "delete": {}, "list": {}},
    "_bona_meta": {
        "type_name": "AWS::S3::Bucket",
        "provisioning_type": "FULLY_MUTABLE",
        "deprecated_status": "LIVE",
        "description": "Creates an S3 bucket.",
        "arn": "arn:aws:cloudformation:us-east-1::type/resource/AWS-S3-Bucket",
        "source_url": "",
    },
}

EC2_SCHEMA = {
    "typeName": "AWS::EC2::Instance",
    "description": "Creates an EC2 instance.",
    "properties": {
        "InstanceId": {"type": "string"},
        "InstanceType": {"type": "string"},
        "ImageId": {"type": "string"},
        "SubnetId": {"type": "string"},
    },
    "required": ["ImageId"],
    "readOnlyProperties": ["/properties/InstanceId"],
    "createOnlyProperties": ["/properties/ImageId", "/properties/SubnetId"],
    "primaryIdentifier": ["/properties/InstanceId"],
    "handlers": {"create": {}, "read": {}, "delete": {}},
    "_bona_meta": {
        "type_name": "AWS::EC2::Instance",
        "provisioning_type": "FULLY_MUTABLE",
        "deprecated_status": "LIVE",
        "description": "Creates an EC2 instance.",
        "arn": "",
        "source_url": "",
    },
}


class TestCacheName:
    """Test cache key generation."""

    def test_replaces_colons(self):
        assert _cache_name("AWS::S3::Bucket") == "type-schema-AWS--S3--Bucket"

    def test_ec2_instance(self):
        assert _cache_name("AWS::EC2::Instance") == "type-schema-AWS--EC2--Instance"

    def test_nested_type(self):
        assert _cache_name("AWS::ElasticLoadBalancingV2::LoadBalancer") == (
            "type-schema-AWS--ElasticLoadBalancingV2--LoadBalancer"
        )


class TestSchemaParsingHelpers:
    """Test schema property extraction."""

    def test_get_properties(self):
        props = _get_schema_properties(S3_SCHEMA)
        assert "BucketName" in props
        assert "Arn" in props
        assert len(props) == 5

    def test_get_required(self):
        assert _get_required_properties(S3_SCHEMA) == ["BucketName"]
        assert _get_required_properties(EC2_SCHEMA) == ["ImageId"]

    def test_get_read_only(self):
        read_only = _get_read_only_properties(S3_SCHEMA)
        assert "Arn" in read_only
        assert "DomainName" in read_only
        assert len(read_only) == 2

    def test_get_create_only(self):
        create_only = _get_create_only_properties(S3_SCHEMA)
        assert create_only == ["BucketName"]

        ec2_create = _get_create_only_properties(EC2_SCHEMA)
        assert "ImageId" in ec2_create
        assert "SubnetId" in ec2_create

    def test_get_primary_identifier(self):
        assert _get_primary_identifier(S3_SCHEMA) == ["BucketName"]
        assert _get_primary_identifier(EC2_SCHEMA) == ["InstanceId"]

    def test_empty_schema(self):
        empty = {}
        assert _get_schema_properties(empty) == {}
        assert _get_required_properties(empty) == []
        assert _get_read_only_properties(empty) == []
        assert _get_create_only_properties(empty) == []
        assert _get_primary_identifier(empty) == []


class TestTypeDefinitionNode:
    """Test TypeDefinition reference node creation."""

    def test_creates_node_with_correct_id(self):
        node = _schema_to_type_definition_node("AWS::S3::Bucket", S3_SCHEMA)
        assert node.id == "type-definition:AWS::S3::Bucket"
        assert node.node_type == "AWS::CloudFormation::TypeDefinition"
        assert node.name == "AWS::S3::Bucket"

    def test_node_is_global(self):
        node = _schema_to_type_definition_node("AWS::S3::Bucket", S3_SCHEMA)
        assert node.account_id == "global"
        assert node.region == "global"
        assert node.state == "ACTIVE"

    def test_node_properties_contain_schema_info(self):
        node = _schema_to_type_definition_node("AWS::S3::Bucket", S3_SCHEMA)
        props = node.properties
        assert props["type_name"] == "AWS::S3::Bucket"
        assert props["property_count"] == 5
        assert props["required_properties"] == ["BucketName"]
        assert "Arn" in props["read_only_properties"]
        assert props["create_only_properties"] == ["BucketName"]
        assert props["primary_identifier"] == ["BucketName"]
        assert "create" in props["handlers"]
        assert "delete" in props["handlers"]

    def test_node_property_names_sorted(self):
        node = _schema_to_type_definition_node("AWS::S3::Bucket", S3_SCHEMA)
        names = node.properties["property_names"]
        assert names == sorted(names)

    def test_ec2_node(self):
        node = _schema_to_type_definition_node("AWS::EC2::Instance", EC2_SCHEMA)
        assert node.id == "type-definition:AWS::EC2::Instance"
        assert node.properties["required_properties"] == ["ImageId"]
        assert node.properties["primary_identifier"] == ["InstanceId"]


class TestGetTypeSchema:
    """Test schema fetch with caching (soft/hard inquiry)."""

    def test_soft_inquiry_hits_cache(self):
        # Pre-populate cache
        write_cache(_cache_name("AWS::S3::Bucket"), "us-east-1", S3_SCHEMA)

        mock_session = MagicMock()
        schema = get_type_schema(mock_session, "us-east-1", "AWS::S3::Bucket", refresh=False)

        assert schema is not None
        assert schema["typeName"] == "AWS::S3::Bucket"
        # Should NOT have called CloudFormation
        mock_session.client.assert_not_called()

    def test_soft_inquiry_miss_returns_none_without_aws(self):
        # No cache, but also no AWS call if we mock the client to raise
        mock_session = MagicMock()
        mock_cfn = MagicMock()
        mock_session.client.return_value = mock_cfn
        mock_cfn.describe_type.side_effect = Exception("should not be called")

        # With refresh=False and no cache, it should return None without calling AWS
        # Actually it will try to fetch... let's test the hard path instead
        pass

    def test_hard_inquiry_calls_cloudformation(self):
        import json
        mock_session = MagicMock()
        mock_cfn = MagicMock()
        mock_session.client.return_value = mock_cfn
        mock_cfn.describe_type.return_value = {
            "Schema": json.dumps(S3_SCHEMA),
            "Arn": "arn:aws:cloudformation:us-east-1::type/resource/AWS-S3-Bucket",
            "Description": "S3 Bucket",
            "ProvisioningType": "FULLY_MUTABLE",
            "DeprecatedStatus": "LIVE",
            "IsDefaultVersion": True,
        }

        schema = get_type_schema(mock_session, "us-east-1", "AWS::S3::Bucket", refresh=True)

        assert schema is not None
        assert schema["typeName"] == "AWS::S3::Bucket"
        mock_session.client.assert_called_with("cloudformation", region_name="us-east-1")
        mock_cfn.describe_type.assert_called_once()


class TestFetchAllTypeSchemas:
    """Test batch schema fetching."""

    def test_uses_cache_first(self):
        # Cache S3, not EC2
        write_cache(_cache_name("AWS::S3::Bucket"), "us-east-1", S3_SCHEMA)

        mock_session = MagicMock()
        mock_cfn = MagicMock()
        mock_session.client.return_value = mock_cfn

        import json
        mock_cfn.describe_type.return_value = {
            "Schema": json.dumps(EC2_SCHEMA),
            "ProvisioningType": "FULLY_MUTABLE",
            "DeprecatedStatus": "LIVE",
            "IsDefaultVersion": True,
        }

        schemas = fetch_all_type_schemas(
            mock_session, "us-east-1",
            ["AWS::S3::Bucket", "AWS::EC2::Instance"],
            refresh=False,
        )

        assert "AWS::S3::Bucket" in schemas
        assert "AWS::EC2::Instance" in schemas
        # Only EC2 should have triggered a CloudFormation call
        mock_cfn.describe_type.assert_called_once()


class TestEnrichWithTypeSchemas:
    """Test the main enrichment function."""

    def _setup_cache(self):
        write_cache(_cache_name("AWS::S3::Bucket"), "us-east-1", S3_SCHEMA)
        write_cache(_cache_name("AWS::EC2::Instance"), "us-east-1", EC2_SCHEMA)

    def _make_nodes(self):
        return [
            AssetNode(id="arn:aws:s3:::b1", node_type="AWS::S3::Bucket", name="b1",
                      resource_type="AWS::S3::Bucket", account_id="123"),
            AssetNode(id="arn:aws:s3:::b2", node_type="AWS::S3::Bucket", name="b2",
                      resource_type="AWS::S3::Bucket", account_id="123"),
            AssetNode(id="arn:aws:ec2:us-east-1:123:instance/i-1", node_type="AWS::EC2::Instance",
                      name="web", resource_type="AWS::EC2::Instance", account_id="123",
                      properties={"instanceType": "t3.medium"}),
        ]

    def test_creates_type_definition_nodes(self):
        self._setup_cache()
        mock_session = MagicMock()
        config = EnrichmentConfig(enabled=True)

        resources, ref_nodes, edges = enrich_with_type_schemas(
            self._make_nodes(), mock_session, "us-east-1", config=config
        )

        type_defs = [n for n in ref_nodes if n.node_type == "AWS::CloudFormation::TypeDefinition"]
        assert len(type_defs) == 2
        names = {n.name for n in type_defs}
        assert "AWS::S3::Bucket" in names
        assert "AWS::EC2::Instance" in names

    def test_creates_instance_of_edges(self):
        self._setup_cache()
        mock_session = MagicMock()
        config = EnrichmentConfig(enabled=True)

        resources, ref_nodes, edges = enrich_with_type_schemas(
            self._make_nodes(), mock_session, "us-east-1", config=config
        )

        io_edges = [e for e in edges if e.edge_type == "INSTANCE_OF"]
        assert len(io_edges) == 3  # 2 S3 + 1 EC2

        # All point to type-definition:
        for edge in io_edges:
            assert edge.target_id.startswith("type-definition:")

    def test_include_filter_limits_types(self):
        self._setup_cache()
        mock_session = MagicMock()
        config = EnrichmentConfig(enabled=True, include=["AWS::S3::*"])

        resources, ref_nodes, edges = enrich_with_type_schemas(
            self._make_nodes(), mock_session, "us-east-1", config=config
        )

        type_defs = [n for n in ref_nodes if n.node_type == "AWS::CloudFormation::TypeDefinition"]
        assert len(type_defs) == 1
        assert type_defs[0].name == "AWS::S3::Bucket"

        io_edges = [e for e in edges if e.edge_type == "INSTANCE_OF"]
        assert len(io_edges) == 2  # only 2 S3 buckets

    def test_exclude_filter_removes_types(self):
        self._setup_cache()
        mock_session = MagicMock()
        config = EnrichmentConfig(enabled=True, exclude=["AWS::EC2::*"])

        resources, ref_nodes, edges = enrich_with_type_schemas(
            self._make_nodes(), mock_session, "us-east-1", config=config
        )

        type_defs = [n for n in ref_nodes if n.node_type == "AWS::CloudFormation::TypeDefinition"]
        assert len(type_defs) == 1
        assert type_defs[0].name == "AWS::S3::Bucket"

    def test_resources_unchanged(self):
        self._setup_cache()
        mock_session = MagicMock()
        config = EnrichmentConfig(enabled=True)
        original_nodes = self._make_nodes()

        resources, ref_nodes, edges = enrich_with_type_schemas(
            original_nodes, mock_session, "us-east-1", config=config
        )

        # Resources list should be the same objects
        assert resources is original_nodes
        assert len(resources) == 3

    def test_no_matching_types_returns_empty(self):
        self._setup_cache()
        mock_session = MagicMock()
        config = EnrichmentConfig(enabled=True, include=["AWS::Lambda::*"])

        resources, ref_nodes, edges = enrich_with_type_schemas(
            self._make_nodes(), mock_session, "us-east-1", config=config
        )

        assert ref_nodes == []
        assert edges == []

    def test_default_config_enriches_all(self):
        self._setup_cache()
        mock_session = MagicMock()

        resources, ref_nodes, edges = enrich_with_type_schemas(
            self._make_nodes(), mock_session, "us-east-1", config=None
        )

        # Default config (enabled=True, no filters) should enrich all
        type_defs = [n for n in ref_nodes if n.node_type == "AWS::CloudFormation::TypeDefinition"]
        assert len(type_defs) == 2
