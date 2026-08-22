"""Tests for domain classification and Metis source tree writing."""

import json
import tempfile
from pathlib import Path

import pytest

from bona.classify import DomainClassifier, DomainConfig, DomainRule
from bona.classify.assignments import DomainAssignments
from bona.schema.model import AssetEdge, AssetNode, DiscoveryResult
from bona.writers.metis_source import MetisSourceWriter


# -- Fixtures --

def make_node(id, resource_type="AWS::S3::Bucket", name="", tags=None, account="111122223333"):
    return AssetNode(
        id=id,
        node_type="CSPResource",
        provider="aws",
        name=name or id.split("/")[-1],
        region="us-east-1",
        account_id=account,
        resource_type=resource_type,
        state="active",
        tags=tags or {},
    )


def make_edge(source, target, edge_type="RELATED_TO"):
    return AssetEdge(source_id=source, target_id=target, edge_type=edge_type)


SAMPLE_CONFIG = {
    "domains": {
        "identity": {
            "tags": {"domain": ["identity", "scim"]},
            "name_patterns": ["*cognito*", "*sso*"],
            "resource_types": ["AWS::Cognito::*", "AWS::SSO::*"],
        },
        "security": {
            "name_patterns": ["*vault*", "*kms*"],
            "resource_types": ["AWS::KMS::*", "AWS::SecretsManager::*"],
        },
        "shared": {
            "resource_types": ["AWS::EC2::VPC", "AWS::EC2::Subnet"],
        },
    },
    "defaults": {
        "unclassified_domain": "shared",
        "include_shared_in_all": True,
    },
}


@pytest.fixture
def classifier():
    return DomainClassifier.from_dict(SAMPLE_CONFIG)


@pytest.fixture
def sample_result():
    nodes = [
        make_node("arn:aws:cognito:us-east-1:111:userpool/pool1", "AWS::Cognito::UserPool", "auth-pool"),
        make_node("arn:aws:sso:us-east-1:111:instance/sso1", "AWS::SSO::Instance", "sso-main"),
        make_node("arn:aws:kms:us-east-1:111:key/key1", "AWS::KMS::Key", "vault-key"),
        make_node("arn:aws:s3:::my-bucket", "AWS::S3::Bucket", "my-bucket"),
        make_node("vpc-0abc", "AWS::EC2::VPC", "main-vpc"),
    ]
    edges = [
        make_edge("arn:aws:cognito:us-east-1:111:userpool/pool1", "vpc-0abc", "IN_VPC"),
        make_edge("arn:aws:kms:us-east-1:111:key/key1", "vpc-0abc", "IN_VPC"),
        make_edge("arn:aws:s3:::my-bucket", "arn:aws:kms:us-east-1:111:key/key1", "ENCRYPTED_BY"),
    ]
    return DiscoveryResult(
        provider="aws",
        account_id="111122223333",
        region="us-east-1",
        resources=nodes,
        relationships=edges,
    )


# -- DomainRule tests --

class TestDomainRule:
    def test_matches_resource_type(self):
        rule = DomainRule(name="identity", resource_types=["AWS::Cognito::*"])
        node = make_node("x", "AWS::Cognito::UserPool")
        assert rule.matches(node)

    def test_no_match(self):
        rule = DomainRule(name="identity", resource_types=["AWS::Cognito::*"])
        node = make_node("x", "AWS::S3::Bucket")
        assert not rule.matches(node)

    def test_matches_name_pattern(self):
        rule = DomainRule(name="security", name_patterns=["*vault*"])
        node = make_node("arn:aws:kms:us-east-1:111:key/vault-key", "AWS::KMS::Key", "vault-key")
        assert rule.matches(node)

    def test_matches_tags(self):
        rule = DomainRule(name="identity", tags={"domain": ["identity", "scim"]})
        node = make_node("x", tags={"domain": "identity"})
        assert rule.matches(node)

    def test_tag_case_insensitive(self):
        rule = DomainRule(name="identity", tags={"domain": ["Identity"]})
        node = make_node("x", tags={"domain": "identity"})
        assert rule.matches(node)


# -- DomainClassifier tests --

class TestDomainClassifier:
    def test_classify_cognito_to_identity(self, classifier):
        node = make_node("pool1", "AWS::Cognito::UserPool")
        assert "identity" in classifier.classify_node(node)

    def test_classify_kms_to_security(self, classifier):
        node = make_node("key1", "AWS::KMS::Key", "vault-key")
        assert "security" in classifier.classify_node(node)

    def test_unclassified_goes_to_shared(self, classifier):
        node = make_node("x", "AWS::Lambda::Function", "my-lambda")
        assert "shared" in classifier.classify_node(node)

    def test_vpc_goes_to_shared(self, classifier):
        node = make_node("vpc-0abc", "AWS::EC2::VPC")
        assert "shared" in classifier.classify_node(node)

    def test_full_classify(self, classifier, sample_result):
        assignments = classifier.classify(sample_result)
        assert len(assignments.get_nodes("identity")) == 2
        assert len(assignments.get_nodes("security")) == 1
        # VPC + unclassified S3 bucket
        assert len(assignments.get_nodes("shared")) >= 2

    def test_edge_routing(self, classifier, sample_result):
        assignments = classifier.classify(sample_result)
        # identity nodes reference vpc (shared) via IN_VPC
        identity_edges = assignments.get_edges("identity")
        assert any(e.edge_type == "IN_VPC" for e in identity_edges)

    def test_no_dangling_edges(self, classifier, sample_result):
        assignments = classifier.classify(sample_result)
        for domain in assignments.domains_with_nodes:
            dangling = assignments.dangling_edges(domain)
            assert dangling == [], f"Dangling edges in {domain}: {dangling}"


# -- DomainConfig tests --

class TestDomainConfig:
    def test_from_dict(self):
        config = DomainConfig.from_dict(SAMPLE_CONFIG)
        assert len(config.domains) == 3
        assert config.unclassified_domain == "shared"

    def test_from_file(self, tmp_path):
        import yaml
        cfg_path = tmp_path / "domains.yaml"
        cfg_path.write_text(yaml.dump(SAMPLE_CONFIG))
        config = DomainConfig.from_file(cfg_path)
        assert len(config.domains) == 3


# -- MetisSourceWriter tests --

class TestMetisSourceWriter:
    def test_writes_source_tree(self, classifier, sample_result, tmp_path):
        assignments = classifier.classify(sample_result)
        writer = MetisSourceWriter(tmp_path / "output")
        summary = writer.write(assignments, account_id="111122223333", region="us-east-1")

        # Check structure
        output = tmp_path / "output"
        assert (output / "L2" / "L2-all-nodes.json").exists()
        assert (output / "L2" / "L2-all-edges.json").exists()

        # Check per-domain directories
        for domain in summary:
            app_dir = output / "L1" / "nodes" / domain
            assert (app_dir / "nodes.json").exists()
            assert (app_dir / "edges.json").exists()
            assert (app_dir / "manifest.json").exists()

    def test_nodes_json_has_required_fields(self, classifier, sample_result, tmp_path):
        assignments = classifier.classify(sample_result)
        writer = MetisSourceWriter(tmp_path / "output")
        writer.write(assignments, account_id="111122223333")

        # Read identity nodes
        nodes_path = tmp_path / "output" / "L1" / "nodes" / "identity" / "nodes.json"
        nodes = json.loads(nodes_path.read_text())
        assert len(nodes) > 0
        for node in nodes:
            assert "id" in node
            assert "node_type" in node

    def test_l2_type_definitions(self, classifier, sample_result, tmp_path):
        assignments = classifier.classify(sample_result)
        writer = MetisSourceWriter(tmp_path / "output")
        writer.write(assignments)

        l2_nodes = json.loads((tmp_path / "output" / "L2" / "L2-all-nodes.json").read_text())
        assert len(l2_nodes) > 0
        for node in l2_nodes:
            assert node["id"].startswith("L2:")
            assert node["node_type"] == "TypeDefinition"

    def test_l2_instance_of_edges(self, classifier, sample_result, tmp_path):
        assignments = classifier.classify(sample_result)
        writer = MetisSourceWriter(tmp_path / "output")
        writer.write(assignments)

        l2_edges = json.loads((tmp_path / "output" / "L2" / "L2-all-edges.json").read_text())
        assert len(l2_edges) > 0
        assert all(e["edge_type"] == "INSTANCE_OF" for e in l2_edges)
