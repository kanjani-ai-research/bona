"""Tests for bona.graph_tree — CSP hierarchy node builder."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bona.graph_tree import build_hierarchy, _interpolate, _load_tree_config
from bona.schema.model import AssetNode


@pytest.fixture
def sample_nodes():
    return [
        AssetNode(id="arn:aws:s3:::bucket-a", node_type="AWS::S3::Bucket", name="bucket-a",
                  provider="aws", region="us-east-1", properties={}),
        AssetNode(id="arn:aws:ec2:us-east-1:123:vpc/vpc-1", node_type="AWS::EC2::VPC", name="vpc-1",
                  provider="aws", region="us-east-1", properties={}),
        AssetNode(id="arn:aws:iam::123:role/admin", node_type="AWS::IAM::Role", name="admin",
                  provider="aws", region="global", properties={}),
    ]


class TestInterpolate:
    def test_simple(self):
        assert _interpolate("cloud::aws::{account_id}", {"account_id": "123"}) == "cloud::aws::123"

    def test_multiple(self):
        result = _interpolate("{a}::{b}", {"a": "x", "b": "y"})
        assert result == "x::y"

    def test_missing_key_unchanged(self):
        assert _interpolate("{missing}", {}) == "{missing}"


class TestBuildHierarchy:
    def test_aws_hierarchy(self, sample_nodes):
        nodes, edges = build_hierarchy(
            csp="aws",
            context={"account_id": "123456789012", "region": "us-east-1"},
            resource_nodes=sample_nodes,
        )

        # Should have Root + Provider + Account + Region = 4 hierarchy nodes
        hierarchy_types = [n.node_type for n in nodes]
        assert "Cloud::Root" in hierarchy_types
        assert "Cloud::Provider" in hierarchy_types
        assert "Cloud::Account" in hierarchy_types
        assert "Cloud::Region" in hierarchy_types

    def test_hierarchy_edges(self, sample_nodes):
        nodes, edges = build_hierarchy(
            csp="aws",
            context={"account_id": "123", "region": "us-east-1"},
            resource_nodes=sample_nodes,
        )

        edge_types = [e.edge_type for e in edges]
        assert "HAS_PROVIDER" in edge_types
        assert "HAS_ACCOUNT" in edge_types
        assert "HAS_REGION" in edge_types
        assert "BELONGS_TO" in edge_types

    def test_resource_attachment(self, sample_nodes):
        nodes, edges = build_hierarchy(
            csp="aws",
            context={"account_id": "123", "region": "us-east-1"},
            resource_nodes=sample_nodes,
        )

        belongs_to = [e for e in edges if e.edge_type == "BELONGS_TO"]
        # All 3 resources should have BELONGS_TO edges
        assert len(belongs_to) == 3

    def test_global_resources_attach_to_account(self, sample_nodes):
        nodes, edges = build_hierarchy(
            csp="aws",
            context={"account_id": "123", "region": "us-east-1"},
            resource_nodes=sample_nodes,
        )

        # IAM role (global) should attach to Account, not Region
        iam_edge = [e for e in edges if e.source_id == "arn:aws:iam::123:role/admin" and e.edge_type == "BELONGS_TO"]
        assert len(iam_edge) == 1
        assert "account" in iam_edge[0].target_id  # attaches to account node

        # S3 bucket (global) should also attach to Account
        s3_edge = [e for e in edges if e.source_id == "arn:aws:s3:::bucket-a" and e.edge_type == "BELONGS_TO"]
        assert len(s3_edge) == 1
        assert "account" in s3_edge[0].target_id

    def test_gcp_hierarchy(self):
        gcp_nodes = [
            AssetNode(id="//storage.googleapis.com/my-bucket", node_type="GCP::Storage::Bucket",
                      name="my-bucket", provider="gcp", region="us-central1", properties={}),
        ]
        nodes, edges = build_hierarchy(
            csp="gcp",
            context={"project_id": "my-project", "region": "us-central1"},
            resource_nodes=gcp_nodes,
        )

        hierarchy_types = [n.node_type for n in nodes]
        assert "Cloud::Provider" in hierarchy_types
        assert "Cloud::Project" in hierarchy_types
        assert "Cloud::Region" in hierarchy_types

        edge_types = [e.edge_type for e in edges]
        assert "HAS_PROJECT" in edge_types
        assert "HAS_REGION" in edge_types
        assert "BELONGS_TO" in edge_types

    def test_unknown_csp_returns_empty(self, sample_nodes):
        nodes, edges = build_hierarchy(csp="unknown", context={}, resource_nodes=sample_nodes)
        assert nodes == []
        assert edges == []

    def test_node_ids_are_deterministic(self, sample_nodes):
        nodes1, _ = build_hierarchy(csp="aws", context={"account_id": "123", "region": "us-east-1"}, resource_nodes=[])
        nodes2, _ = build_hierarchy(csp="aws", context={"account_id": "123", "region": "us-east-1"}, resource_nodes=[])
        ids1 = [n.id for n in nodes1]
        ids2 = [n.id for n in nodes2]
        assert ids1 == ids2

    def test_empty_resources(self):
        nodes, edges = build_hierarchy(csp="aws", context={"account_id": "123", "region": "us-east-1"}, resource_nodes=[])
        # Still emits hierarchy nodes, no BELONGS_TO edges
        assert len(nodes) == 4  # Root + Provider + Account + Region
        belongs_to = [e for e in edges if e.edge_type == "BELONGS_TO"]
        assert len(belongs_to) == 0
