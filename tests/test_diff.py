"""Tests for bona.diff — drift detection between exports."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bona.diff import diff_exports, DiffResult, _compute_property_diff


@pytest.fixture
def baseline_export(tmp_path):
    """Create a baseline export directory."""
    nodes = [
        {"id": "arn:aws:s3:::bucket-a", "node_type": "AWS::S3::Bucket", "name": "bucket-a",
         "stable_id": "aws|123|us-east-1|AWS::S3::Bucket|bucket-a", "semantic_hash": "abc123",
         "properties": {"BucketName": "bucket-a", "Versioning": "Enabled"}},
        {"id": "arn:aws:s3:::bucket-b", "node_type": "AWS::S3::Bucket", "name": "bucket-b",
         "stable_id": "aws|123|us-east-1|AWS::S3::Bucket|bucket-b", "semantic_hash": "def456",
         "properties": {"BucketName": "bucket-b", "Versioning": "Suspended"}},
        {"id": "arn:aws:ec2:us-east-1:123:vpc/vpc-abc", "node_type": "AWS::EC2::VPC", "name": "vpc-abc",
         "stable_id": "aws|123|us-east-1|AWS::EC2::VPC|vpc-abc", "semantic_hash": "ghi789",
         "properties": {"CidrBlock": "10.0.0.0/16"}},
    ]
    edges = [
        {"source_id": "arn:aws:s3:::bucket-a", "target_id": "arn:aws:ec2:us-east-1:123:vpc/vpc-abc",
         "edge_type": "IN_VPC", "stable_id": "edge|bucket-a|IN_VPC|vpc-abc"},
    ]
    (tmp_path / "nodes.json").write_text(json.dumps(nodes))
    (tmp_path / "edges.json").write_text(json.dumps(edges))
    return tmp_path


class TestDiffNoChanges:
    """Test diff when exports are identical."""

    def test_no_drift(self, baseline_export):
        result = diff_exports(str(baseline_export), str(baseline_export))
        assert not result.has_changes
        assert result.unchanged_count == 3
        assert result.unchanged_edges_count == 1
        assert result.total_changes == 0

    def test_summary_no_drift(self, baseline_export):
        result = diff_exports(str(baseline_export), str(baseline_export))
        assert "No drift detected" in result.summary()


class TestDiffAdded:
    """Test detecting added resources."""

    def test_added_node(self, baseline_export, tmp_path):
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        nodes = json.loads((baseline_export / "nodes.json").read_text())
        nodes.append({"id": "arn:aws:s3:::bucket-c", "node_type": "AWS::S3::Bucket",
                      "name": "bucket-c", "stable_id": "aws|123|us-east-1|AWS::S3::Bucket|bucket-c",
                      "semantic_hash": "new111"})
        (new_dir / "nodes.json").write_text(json.dumps(nodes))
        (new_dir / "edges.json").write_text((baseline_export / "edges.json").read_text())

        result = diff_exports(str(baseline_export), str(new_dir))
        assert len(result.added_nodes) == 1
        assert result.added_nodes[0].name == "bucket-c"
        assert result.added_nodes[0].change == "added"

    def test_added_edge(self, baseline_export, tmp_path):
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        (new_dir / "nodes.json").write_text((baseline_export / "nodes.json").read_text())
        edges = json.loads((baseline_export / "edges.json").read_text())
        edges.append({"source_id": "arn:aws:s3:::bucket-b", "target_id": "arn:aws:ec2:us-east-1:123:vpc/vpc-abc",
                      "edge_type": "IN_VPC", "stable_id": "edge|bucket-b|IN_VPC|vpc-abc"})
        (new_dir / "edges.json").write_text(json.dumps(edges))

        result = diff_exports(str(baseline_export), str(new_dir))
        assert len(result.added_edges) == 1
        assert result.added_edges[0].edge_type == "IN_VPC"


class TestDiffRemoved:
    """Test detecting removed resources."""

    def test_removed_node(self, baseline_export, tmp_path):
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        nodes = json.loads((baseline_export / "nodes.json").read_text())
        nodes = [n for n in nodes if n["name"] != "bucket-b"]
        (new_dir / "nodes.json").write_text(json.dumps(nodes))
        (new_dir / "edges.json").write_text((baseline_export / "edges.json").read_text())

        result = diff_exports(str(baseline_export), str(new_dir))
        assert len(result.removed_nodes) == 1
        assert result.removed_nodes[0].name == "bucket-b"
        assert result.removed_nodes[0].change == "removed"

    def test_removed_edge(self, baseline_export, tmp_path):
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        (new_dir / "nodes.json").write_text((baseline_export / "nodes.json").read_text())
        (new_dir / "edges.json").write_text(json.dumps([]))

        result = diff_exports(str(baseline_export), str(new_dir))
        assert len(result.removed_edges) == 1


class TestDiffModified:
    """Test detecting modified resources (same stable_id, different hash)."""

    def test_modified_node(self, baseline_export, tmp_path):
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        nodes = json.loads((baseline_export / "nodes.json").read_text())
        # Change bucket-a's hash and a property
        for n in nodes:
            if n["name"] == "bucket-a":
                n["semantic_hash"] = "changed_hash"
                n["properties"]["Versioning"] = "Suspended"
        (new_dir / "nodes.json").write_text(json.dumps(nodes))
        (new_dir / "edges.json").write_text((baseline_export / "edges.json").read_text())

        result = diff_exports(str(baseline_export), str(new_dir))
        assert len(result.modified_nodes) == 1
        assert result.modified_nodes[0].name == "bucket-a"
        assert result.modified_nodes[0].old_hash == "abc123"
        assert result.modified_nodes[0].new_hash == "changed_hash"

    def test_modified_node_properties(self, baseline_export, tmp_path):
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        nodes = json.loads((baseline_export / "nodes.json").read_text())
        for n in nodes:
            if n["name"] == "bucket-a":
                n["semantic_hash"] = "different"
                n["properties"]["Versioning"] = "Suspended"
                n["properties"]["NewProp"] = "added"
        (new_dir / "nodes.json").write_text(json.dumps(nodes))
        (new_dir / "edges.json").write_text((baseline_export / "edges.json").read_text())

        result = diff_exports(str(baseline_export), str(new_dir))
        mod = result.modified_nodes[0]
        assert "properties" in mod.changed_properties

    def test_no_properties_flag(self, baseline_export, tmp_path):
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        nodes = json.loads((baseline_export / "nodes.json").read_text())
        for n in nodes:
            if n["name"] == "bucket-a":
                n["semantic_hash"] = "changed"
        (new_dir / "nodes.json").write_text(json.dumps(nodes))
        (new_dir / "edges.json").write_text((baseline_export / "edges.json").read_text())

        result = diff_exports(str(baseline_export), str(new_dir), include_properties=False)
        assert result.modified_nodes[0].changed_properties == {}


class TestDiffMixed:
    """Test mixed scenarios."""

    def test_combined_changes(self, baseline_export, tmp_path):
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        nodes = json.loads((baseline_export / "nodes.json").read_text())
        # Remove bucket-b, modify vpc, add new resource
        nodes = [n for n in nodes if n["name"] != "bucket-b"]
        for n in nodes:
            if n["name"] == "vpc-abc":
                n["semantic_hash"] = "modified_vpc"
                n["properties"]["CidrBlock"] = "10.0.0.0/8"
        nodes.append({"id": "arn:new", "node_type": "AWS::Lambda::Function",
                      "name": "new-func", "stable_id": "aws|123|us-east-1|AWS::Lambda::Function|new-func",
                      "semantic_hash": "brand_new"})
        (new_dir / "nodes.json").write_text(json.dumps(nodes))
        (new_dir / "edges.json").write_text((baseline_export / "edges.json").read_text())

        result = diff_exports(str(baseline_export), str(new_dir))
        assert len(result.added_nodes) == 1
        assert len(result.removed_nodes) == 1
        assert len(result.modified_nodes) == 1
        assert result.unchanged_count == 1  # bucket-a unchanged
        assert result.has_changes
        assert result.total_changes == 3


class TestDiffOutput:
    """Test output formats."""

    def test_to_dict(self, baseline_export, tmp_path):
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        nodes = json.loads((baseline_export / "nodes.json").read_text())
        nodes.append({"id": "new", "node_type": "T", "name": "n",
                      "stable_id": "new_sid", "semantic_hash": "h"})
        (new_dir / "nodes.json").write_text(json.dumps(nodes))
        (new_dir / "edges.json").write_text((baseline_export / "edges.json").read_text())

        result = diff_exports(str(baseline_export), str(new_dir))
        d = result.to_dict()
        assert d["has_changes"] is True
        assert len(d["nodes"]["added"]) == 1
        assert d["nodes"]["added"][0]["change"] == "added"

    def test_missing_file(self, tmp_path):
        """Gracefully handle missing files."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        (old_dir / "nodes.json").write_text("[]")
        (old_dir / "edges.json").write_text("[]")

        new_dir = tmp_path / "new"
        new_dir.mkdir()
        # No files in new_dir

        result = diff_exports(str(old_dir), str(new_dir))
        assert not result.has_changes  # both empty
