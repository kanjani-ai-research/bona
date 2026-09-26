"""Tests for bona.config — configuration loading, include/exclude logic."""

import json
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bona.config import (
    BonaConfig,
    EnrichmentConfig,
    LoggingConfig,
    OutputConfig,
    SchemaCacheConfig,
    load_config,
)


class TestEnrichmentConfig:
    """Test include/exclude glob filtering."""

    def test_disabled_rejects_all(self):
        cfg = EnrichmentConfig(enabled=False)
        assert cfg.should_enrich("AWS::EC2::Instance") is False
        assert cfg.should_enrich("AWS::S3::Bucket") is False

    def test_enabled_no_filters_accepts_all(self):
        cfg = EnrichmentConfig(enabled=True, include=[], exclude=[])
        assert cfg.should_enrich("AWS::EC2::Instance") is True
        assert cfg.should_enrich("AWS::S3::Bucket") is True
        assert cfg.should_enrich("AWS::Lambda::Function") is True

    def test_include_filters(self):
        cfg = EnrichmentConfig(enabled=True, include=["AWS::EC2::*", "AWS::S3::*"])
        assert cfg.should_enrich("AWS::EC2::Instance") is True
        assert cfg.should_enrich("AWS::EC2::VPC") is True
        assert cfg.should_enrich("AWS::S3::Bucket") is True
        assert cfg.should_enrich("AWS::Lambda::Function") is False
        assert cfg.should_enrich("AWS::RDS::DBInstance") is False

    def test_exclude_filters(self):
        cfg = EnrichmentConfig(enabled=True, include=[], exclude=["AWS::CloudFormation::*"])
        assert cfg.should_enrich("AWS::EC2::Instance") is True
        assert cfg.should_enrich("AWS::CloudFormation::Stack") is False
        assert cfg.should_enrich("AWS::CloudFormation::StackSet") is False

    def test_include_and_exclude_combined(self):
        cfg = EnrichmentConfig(
            enabled=True,
            include=["AWS::EC2::*"],
            exclude=["AWS::EC2::FlowLog"],
        )
        assert cfg.should_enrich("AWS::EC2::Instance") is True
        assert cfg.should_enrich("AWS::EC2::VPC") is True
        assert cfg.should_enrich("AWS::EC2::FlowLog") is False  # excluded
        assert cfg.should_enrich("AWS::S3::Bucket") is False  # not included

    def test_exact_type_in_include(self):
        cfg = EnrichmentConfig(enabled=True, include=["AWS::S3::Bucket"])
        assert cfg.should_enrich("AWS::S3::Bucket") is True
        assert cfg.should_enrich("AWS::S3::AccessPoint") is False


class TestBonaConfig:
    """Test config parsing from dict."""

    def test_from_dict_full(self):
        cfg = BonaConfig.from_dict({
            "logging": {"level": "DEBUG", "path": "/tmp/test.log"},
            "enrichment": {
                "enabled": True,
                "mode": "hard",
                "include": ["AWS::EC2::*"],
                "exclude": ["AWS::EC2::FlowLog"],
                "supplemental": {"ec2_instance_specs": False},
            },
            "output": {"dir": "./my-output", "s3_uri": "s3://bucket/prefix/"},
            "schema_cache": {"s3_uri": "s3://bucket/schemas/"},
        })

        assert cfg.logging.level == "DEBUG"
        assert cfg.logging.path == "/tmp/test.log"
        assert cfg.enrichment.enabled is True
        assert cfg.enrichment.mode == "hard"
        assert cfg.enrichment.include == ["AWS::EC2::*"]
        assert cfg.enrichment.exclude == ["AWS::EC2::FlowLog"]
        assert cfg.enrichment.supplemental["ec2_instance_specs"] is False
        assert cfg.output.dir == "./my-output"
        assert cfg.output.s3_uri == "s3://bucket/prefix/"
        assert cfg.schema_cache.s3_uri == "s3://bucket/schemas/"

    def test_from_dict_empty(self):
        cfg = BonaConfig.from_dict({})
        assert cfg.logging.level == "INFO"
        assert cfg.logging.path == ""
        assert cfg.enrichment.enabled is False
        assert cfg.enrichment.mode == "soft"
        assert cfg.output.dir == "./bona-output"
        assert cfg.output.s3_uri == ""

    def test_default(self):
        cfg = BonaConfig.default()
        assert cfg.enrichment.enabled is False
        assert cfg.logging.level == "INFO"


class TestLoadConfig:
    """Test config file loading."""

    def test_load_json_file(self, tmp_path):
        config_file = tmp_path / "bona.json"
        config_file.write_text(json.dumps({
            "logging": {"level": "WARNING"},
            "enrichment": {"enabled": True, "include": ["AWS::S3::*"]},
        }))

        cfg = load_config(str(config_file))
        assert cfg.logging.level == "WARNING"
        assert cfg.enrichment.enabled is True
        assert cfg.enrichment.include == ["AWS::S3::*"]

    def test_load_yaml_file(self, tmp_path):
        config_file = tmp_path / "bona.yaml"
        # Simple YAML that our basic parser can handle
        config_file.write_text(
            "logging:\n"
            "  level: DEBUG\n"
            "enrichment:\n"
            "  enabled: true\n"
            "  mode: hard\n"
        )

        cfg = load_config(str(config_file))
        assert cfg.logging.level == "DEBUG"
        assert cfg.enrichment.enabled is True
        assert cfg.enrichment.mode == "hard"

    def test_load_missing_file_returns_default(self):
        cfg = load_config("/nonexistent/path/bona.json")
        assert cfg.enrichment.enabled is False
        assert cfg.logging.level == "INFO"

    def test_load_none_returns_default(self, tmp_path, monkeypatch):
        # Ensure no config file in cwd or home
        monkeypatch.chdir(tmp_path)
        cfg = load_config(None)
        assert cfg.enrichment.enabled is False
