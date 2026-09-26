"""End-to-end tests — mock AWS, run full export with enrichment, verify output."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import bona.cache as cache_mod
from bona.config import BonaConfig, EnrichmentConfig
from bona.enrichment.type_schemas import _cache_name
from bona.cache import write_cache
from bona.schema.model import AssetNode, AssetEdge, DiscoveryResult
from bona.export import export_to_dir


@pytest.fixture(autouse=True)
def temp_cache(tmp_path):
    """Use a temp dir for cache during tests."""
    original = cache_mod.CACHE_DIR
    cache_mod.CACHE_DIR = tmp_path / "cache"
    cache_mod.CACHE_DIR.mkdir()
    yield cache_mod.CACHE_DIR
    cache_mod.CACHE_DIR = original


# Mock AWS Config responses
MOCK_CONFIG_RESULTS = [
    json.dumps({
        "arn": "arn:aws:s3:::my-bucket-1",
        "resourceType": "AWS::S3::Bucket",
        "resourceId": "my-bucket-1",
        "resourceName": "my-bucket-1",
        "accountId": "123456789012",
        "awsRegion": "us-east-1",
        "configurationItemCaptureTime": "2026-08-05T12:00:00Z",
        "configurationItemStatus": "OK",
        "configuration": json.dumps({
            "bucketName": "my-bucket-1",
            "creationDate": "2026-01-01T00:00:00Z",
        }),
        "tags": {"Name": "my-bucket-1", "Environment": "prod"},
        "relationships": [],
    }),
    json.dumps({
        "arn": "arn:aws:ec2:us-east-1:123456789012:instance/i-abc123",
        "resourceType": "AWS::EC2::Instance",
        "resourceId": "i-abc123",
        "resourceName": "web-server",
        "accountId": "123456789012",
        "awsRegion": "us-east-1",
        "configurationItemCaptureTime": "2026-08-05T12:00:00Z",
        "configurationItemStatus": "OK",
        "configuration": json.dumps({
            "instanceId": "i-abc123",
            "instanceType": "t3.medium",
            "imageId": "ami-0123456789abcdef0",
            "vpcId": "vpc-abc123",
            "subnetId": "subnet-def456",
        }),
        "tags": {"Name": "web-server"},
        "relationships": [
            {
                "relationshipName": "Is contained in",
                "resourceType": "AWS::EC2::VPC",
                "resourceId": "vpc-abc123",
            },
            {
                "relationshipName": "Is contained in",
                "resourceType": "AWS::EC2::Subnet",
                "resourceId": "subnet-def456",
            },
        ],
    }),
]

# Mock CloudFormation type schemas
S3_SCHEMA = {
    "typeName": "AWS::S3::Bucket",
    "description": "S3 Bucket resource",
    "properties": {
        "BucketName": {"type": "string"},
        "Arn": {"type": "string"},
    },
    "required": ["BucketName"],
    "readOnlyProperties": ["/properties/Arn"],
    "createOnlyProperties": ["/properties/BucketName"],
    "primaryIdentifier": ["/properties/BucketName"],
    "handlers": {"create": {}, "read": {}, "update": {}, "delete": {}, "list": {}},
    "_bona_meta": {"type_name": "AWS::S3::Bucket", "provisioning_type": "FULLY_MUTABLE",
                   "deprecated_status": "LIVE", "description": "", "arn": "", "source_url": ""},
}

EC2_SCHEMA = {
    "typeName": "AWS::EC2::Instance",
    "description": "EC2 Instance resource",
    "properties": {
        "InstanceId": {"type": "string"},
        "InstanceType": {"type": "string"},
        "ImageId": {"type": "string"},
    },
    "required": [],
    "readOnlyProperties": ["/properties/InstanceId"],
    "createOnlyProperties": ["/properties/ImageId"],
    "primaryIdentifier": ["/properties/InstanceId"],
    "handlers": {"create": {}, "read": {}, "delete": {}},
    "_bona_meta": {"type_name": "AWS::EC2::Instance", "provisioning_type": "FULLY_MUTABLE",
                   "deprecated_status": "LIVE", "description": "", "arn": "", "source_url": ""},
}


class TestEndToEndExport:
    """Full pipeline: discover → enrich → export."""

    def _mock_aws_provider(self, tmp_path):
        """Create a mocked AWSProvider that returns our test data."""
        from bona.providers.aws.aws_provider import AWSProvider

        with patch("boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session

            # Mock STS
            mock_sts = MagicMock()
            mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}

            # Mock Config
            mock_config = MagicMock()
            mock_config.select_resource_config.return_value = {
                "Results": MOCK_CONFIG_RESULTS,
                "NextToken": None,
            }

            # Route client calls
            def client_router(service, **kwargs):
                if service == "sts":
                    return mock_sts
                elif service == "config":
                    return mock_config
                elif service == "cloudformation":
                    mock_cfn = MagicMock()
                    mock_cfn.describe_type.return_value = {
                        "Schema": "{}",
                        "ProvisioningType": "FULLY_MUTABLE",
                    }
                    return mock_cfn
                elif service == "ec2":
                    mock_ec2 = MagicMock()
                    mock_paginator = MagicMock()
                    mock_paginator.paginate.return_value = []
                    mock_ec2.get_paginator.return_value = mock_paginator
                    return mock_ec2
                return MagicMock()

            mock_session.client.side_effect = client_router

            provider = AWSProvider.__new__(AWSProvider)
            provider.region = "us-east-1"
            provider.max_resources = 10000
            provider.resource_type_filter = ["AWS::S3::Bucket", "AWS::EC2::Instance"]
            provider.include_compliance = False
            provider.aggregator = ""
            provider.refresh_cache = False
            provider.validate_schemas = True
            provider.account_id = "123456789012"
            provider._session = mock_session
            provider._config = mock_config

            # Pre-populate schema cache
            write_cache(_cache_name("AWS::S3::Bucket"), "us-east-1", S3_SCHEMA)
            write_cache(_cache_name("AWS::EC2::Instance"), "us-east-1", EC2_SCHEMA)

            # Set enrichment config
            provider.enrichment_config = EnrichmentConfig(
                enabled=True, mode="soft", include=[], exclude=[]
            )

            return provider

    def test_full_discovery_produces_nodes_and_edges(self, tmp_path):
        provider = self._mock_aws_provider(tmp_path)
        result = provider.discover()

        # Should have discovered resources
        assert result.resource_count >= 2
        assert result.relationship_count >= 0

        # Check S3 bucket node
        s3_nodes = [r for r in result.resources if r.resource_type == "AWS::S3::Bucket"]
        assert len(s3_nodes) >= 1
        assert s3_nodes[0].name == "my-bucket-1"
        assert s3_nodes[0].tags.get("Environment") == "prod"

        # Check EC2 instance node
        ec2_nodes = [r for r in result.resources if r.resource_type == "AWS::EC2::Instance"]
        assert len(ec2_nodes) >= 1
        assert ec2_nodes[0].properties.get("instanceType") == "t3.medium"

    def test_enrichment_adds_type_definitions(self, tmp_path):
        provider = self._mock_aws_provider(tmp_path)
        result = provider.discover()

        # Should have TypeDefinition reference nodes
        type_defs = [r for r in result.resources
                     if r.node_type == "AWS::CloudFormation::TypeDefinition"]
        assert len(type_defs) >= 1

        # Should have INSTANCE_OF edges
        io_edges = [e for e in result.relationships if e.edge_type == "INSTANCE_OF"]
        assert len(io_edges) >= 1

    def test_export_writes_valid_json(self, tmp_path):
        provider = self._mock_aws_provider(tmp_path)
        result = provider.discover()

        output_dir = str(tmp_path / "output")
        manifest = export_to_dir(result, output_dir)

        # Check files exist
        nodes_path = Path(output_dir) / "nodes.json"
        edges_path = Path(output_dir) / "edges.json"
        manifest_path = Path(output_dir) / "manifest.json"

        assert nodes_path.exists()
        assert edges_path.exists()
        assert manifest_path.exists()

        # Verify JSON is valid
        nodes = json.loads(nodes_path.read_text())
        edges = json.loads(edges_path.read_text())
        manifest_data = json.loads(manifest_path.read_text())

        assert isinstance(nodes, list)
        assert isinstance(edges, list)
        assert len(nodes) > 0
        assert manifest_data["node_count"] == len(nodes)
        assert manifest_data["edge_count"] == len(edges)

    def test_exported_nodes_have_required_fields(self, tmp_path):
        provider = self._mock_aws_provider(tmp_path)
        result = provider.discover()

        output_dir = str(tmp_path / "output")
        export_to_dir(result, output_dir)

        nodes = json.loads((Path(output_dir) / "nodes.json").read_text())

        required_fields = {"id", "node_type", "name", "stable_id", "semantic_hash",
                           "provider", "account_id", "region", "resource_type", "state"}

        for node in nodes:
            missing = required_fields - set(node.keys())
            assert not missing, f"Node {node.get('id', '?')} missing fields: {missing}"

    def test_exported_edges_have_required_fields(self, tmp_path):
        provider = self._mock_aws_provider(tmp_path)
        result = provider.discover()

        output_dir = str(tmp_path / "output")
        export_to_dir(result, output_dir)

        edges = json.loads((Path(output_dir) / "edges.json").read_text())

        required_fields = {"source_id", "target_id", "edge_type", "stable_id"}

        for edge in edges:
            missing = required_fields - set(edge.keys())
            assert not missing, f"Edge missing fields: {missing}"

    def test_type_definition_nodes_in_output(self, tmp_path):
        provider = self._mock_aws_provider(tmp_path)
        result = provider.discover()

        output_dir = str(tmp_path / "output")
        export_to_dir(result, output_dir)

        nodes = json.loads((Path(output_dir) / "nodes.json").read_text())

        type_defs = [n for n in nodes if n["node_type"] == "AWS::CloudFormation::TypeDefinition"]
        assert len(type_defs) >= 1

        # Check TypeDefinition has schema info
        for td in type_defs:
            assert "property_count" in td
            assert "primary_identifier" in td
            assert td["account_id"] == "global"
            assert td["region"] == "global"

    def test_instance_of_edges_in_output(self, tmp_path):
        provider = self._mock_aws_provider(tmp_path)
        result = provider.discover()

        output_dir = str(tmp_path / "output")
        export_to_dir(result, output_dir)

        edges = json.loads((Path(output_dir) / "edges.json").read_text())

        io_edges = [e for e in edges if e["edge_type"] == "INSTANCE_OF"]
        assert len(io_edges) >= 1

        # All INSTANCE_OF edges should point to type-definition: targets
        for edge in io_edges:
            assert edge["target_id"].startswith("type-definition:")


class TestEndToEndWithConfig:
    """Test that config file settings flow through to enrichment."""

    def test_enrichment_disabled_produces_no_type_defs(self, tmp_path):
        """When enrichment is disabled, no TypeDefinition nodes are created."""
        write_cache(_cache_name("AWS::S3::Bucket"), "us-east-1", S3_SCHEMA)

        nodes = [
            AssetNode(id="arn:aws:s3:::b1", node_type="AWS::S3::Bucket",
                      name="b1", resource_type="AWS::S3::Bucket", account_id="123"),
        ]

        from bona.enrichment.type_schemas import enrich_with_type_schemas
        config = EnrichmentConfig(enabled=False)

        # With enabled=False, should_enrich returns False for all
        resources, ref_nodes, edges = enrich_with_type_schemas(
            nodes, MagicMock(), "us-east-1", config=config
        )

        assert ref_nodes == []
        assert edges == []

    def test_config_driven_export(self, tmp_path):
        """Config include/exclude controls what gets enriched."""
        write_cache(_cache_name("AWS::S3::Bucket"), "us-east-1", S3_SCHEMA)
        write_cache(_cache_name("AWS::EC2::Instance"), "us-east-1", EC2_SCHEMA)

        nodes = [
            AssetNode(id="arn:aws:s3:::b1", node_type="AWS::S3::Bucket",
                      name="b1", resource_type="AWS::S3::Bucket", account_id="123"),
            AssetNode(id="arn:aws:ec2:us-east-1:123:instance/i-1",
                      node_type="AWS::EC2::Instance", name="web",
                      resource_type="AWS::EC2::Instance", account_id="123"),
        ]

        from bona.enrichment.type_schemas import enrich_with_type_schemas

        # Only enrich S3
        config = EnrichmentConfig(enabled=True, include=["AWS::S3::*"])
        resources, ref_nodes, edges = enrich_with_type_schemas(
            nodes, MagicMock(), "us-east-1", config=config
        )

        type_defs = [n for n in ref_nodes if n.node_type == "AWS::CloudFormation::TypeDefinition"]
        assert len(type_defs) == 1
        assert type_defs[0].name == "AWS::S3::Bucket"

        # Only S3 bucket gets INSTANCE_OF edge
        io_edges = [e for e in edges if e.edge_type == "INSTANCE_OF"]
        assert len(io_edges) == 1
        assert "s3" in io_edges[0].source_id
