"""Tests for GCPProvider — Cloud Asset Inventory discovery."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bona.providers.gcp import GCPProvider, _to_node_type, _extract_name, _flatten_properties


class TestNodeTypeMapping:
    """Test GCP asset type → bona node_type conversion."""

    def test_compute_instance(self):
        assert _to_node_type("compute.googleapis.com/Instance") == "GCP::Compute::Instance"

    def test_storage_bucket(self):
        assert _to_node_type("storage.googleapis.com/Bucket") == "GCP::Storage::Bucket"

    def test_iam_service_account(self):
        assert _to_node_type("iam.googleapis.com/ServiceAccount") == "GCP::Iam::ServiceAccount"

    def test_cloud_functions(self):
        assert _to_node_type("cloudfunctions.googleapis.com/Function") == "GCP::Cloudfunctions::Function"

    def test_pubsub(self):
        assert _to_node_type("pubsub.googleapis.com/Topic") == "GCP::Pubsub::Topic"


class TestExtractName:
    """Test name extraction from assets."""

    def test_name_from_path(self):
        asset = {"name": "//storage.googleapis.com/my-bucket", "resource": {"data": {}}}
        assert _extract_name(asset) == "my-bucket"

    def test_name_from_resource_data(self):
        asset = {"name": "//x/long/path", "resource": {"data": {"name": "short-name"}}}
        assert _extract_name(asset) == "short-name"

    def test_display_name_preferred(self):
        asset = {"name": "//x/y", "resource": {"data": {"displayName": "My Display", "name": "internal"}}}
        assert _extract_name(asset) == "My Display"


class TestFlattenProperties:
    """Test resource data flattening."""

    def test_scalars(self):
        data = {"name": "test", "count": 5, "enabled": True, "ratio": 0.5}
        props = _flatten_properties(data)
        assert props == {"name": "test", "count": 5, "enabled": True, "ratio": 0.5}

    def test_string_list(self):
        data = {"tags": ["a", "b", "c"]}
        props = _flatten_properties(data)
        assert props["tags"] == "a, b, c"

    def test_object_list_count(self):
        data = {"rules": [{"id": 1}, {"id": 2}]}
        props = _flatten_properties(data)
        assert props["rules_count"] == 2

    def test_nested_dict_flattened(self):
        data = {"config": {"enabled": True, "timeout": 30}}
        props = _flatten_properties(data)
        assert props["config.enabled"] is True
        assert props["config.timeout"] == 30

    def test_empty_dict(self):
        assert _flatten_properties({}) == {}


class TestGCPProviderInit:
    """Test GCPProvider initialization."""

    def test_project_scope(self):
        p = GCPProvider(project_id="my-project")
        assert p._scope == "projects/my-project"

    def test_org_scope(self):
        p = GCPProvider(organization_id="123456")
        assert p._scope == "organizations/123456"

    def test_folder_scope(self):
        p = GCPProvider(folder_id="folders/789")
        assert p._scope == "folders/folders/789"

    def test_no_scope_raises(self):
        p = GCPProvider()
        with pytest.raises(ValueError):
            _ = p._scope


class TestGCPProviderDiscover:
    """Test GCPProvider.discover() with mocked CLI output."""

    @patch("subprocess.run")
    def test_cli_fallback(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {
                    "name": "//storage.googleapis.com/test-bucket",
                    "assetType": "storage.googleapis.com/Bucket",
                    "ancestors": ["projects/123"],
                    "updateTime": "2026-01-01T00:00:00Z",
                    "resource": {
                        "data": {"name": "test-bucket", "location": "US", "storageClass": "STANDARD"},
                        "location": "us",
                    },
                }
            ]),
        )

        provider = GCPProvider(project_id="test-project")
        # Force CLI path by making client import fail
        with patch("bona.providers.gcp.GCPProvider._discover_via_client", side_effect=ImportError("no module")):
            result = provider.discover()

        assert len(result.resources) == 1
        node = result.resources[0]
        assert node.node_type == "GCP::Storage::Bucket"
        assert node.name == "test-bucket"
        assert node.provider == "gcp"
        assert node.properties["storageClass"] == "STANDARD"
        assert node.region == "us"

    @patch("subprocess.run")
    def test_multiple_types(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {"name": "//storage.googleapis.com/b1", "assetType": "storage.googleapis.com/Bucket",
                 "resource": {"data": {"name": "b1"}, "location": "us-east1"}},
                {"name": "//iam.googleapis.com/projects/p/serviceAccounts/sa@p.iam.gserviceaccount.com",
                 "assetType": "iam.googleapis.com/ServiceAccount",
                 "resource": {"data": {"displayName": "My SA", "email": "sa@p.iam"}, "location": ""}},
            ]),
        )

        provider = GCPProvider(project_id="test")
        with patch("bona.providers.gcp.GCPProvider._discover_via_client", side_effect=Exception("no adc")):
            result = provider.discover()

        assert len(result.resources) == 2
        types = {n.node_type for n in result.resources}
        assert "GCP::Storage::Bucket" in types
        assert "GCP::Iam::ServiceAccount" in types

    @patch("subprocess.run")
    def test_empty_project(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")

        provider = GCPProvider(project_id="empty")
        with patch("bona.providers.gcp.GCPProvider._discover_via_client", side_effect=Exception()):
            result = provider.discover()

        assert len(result.resources) == 0
