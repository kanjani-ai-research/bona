"""Tests for bona.logging — setup_logging, level override, file output."""

import logging
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bona.config import BonaConfig
from bona.logging import setup_logging, flush_to_s3


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset root logger between tests."""
    yield
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


class TestSetupLogging:
    """Test logging configuration from config."""

    def test_default_level_is_info(self):
        cfg = BonaConfig.from_dict({})
        setup_logging(config=cfg)
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_config_level_debug(self):
        cfg = BonaConfig.from_dict({"logging": {"level": "DEBUG"}})
        setup_logging(config=cfg)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_config_level_warning(self):
        cfg = BonaConfig.from_dict({"logging": {"level": "WARNING"}})
        setup_logging(config=cfg)
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_config_level_error(self):
        cfg = BonaConfig.from_dict({"logging": {"level": "ERROR"}})
        setup_logging(config=cfg)
        root = logging.getLogger()
        assert root.level == logging.ERROR

    def test_verbose_overrides_config(self):
        cfg = BonaConfig.from_dict({"logging": {"level": "WARNING"}})
        setup_logging(config=cfg, cli_verbose=True)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_verbose_without_config(self):
        setup_logging(config=None, cli_verbose=True)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_file_handler_created(self, tmp_path):
        log_file = tmp_path / "test.log"
        cfg = BonaConfig.from_dict({"logging": {"level": "INFO", "path": str(log_file)}})
        setup_logging(config=cfg)

        logger = logging.getLogger("test_file")
        logger.info("hello from test")

        assert log_file.exists()
        content = log_file.read_text()
        assert "hello from test" in content

    def test_file_handler_debug_messages(self, tmp_path):
        log_file = tmp_path / "debug.log"
        cfg = BonaConfig.from_dict({"logging": {"level": "DEBUG", "path": str(log_file)}})
        setup_logging(config=cfg)

        logger = logging.getLogger("test_debug")
        logger.debug("debug detail")
        logger.info("info message")
        logger.warning("warning message")

        content = log_file.read_text()
        assert "debug detail" in content
        assert "info message" in content
        assert "warning message" in content

    def test_file_handler_info_excludes_debug(self, tmp_path):
        log_file = tmp_path / "info.log"
        cfg = BonaConfig.from_dict({"logging": {"level": "INFO", "path": str(log_file)}})
        setup_logging(config=cfg)

        logger = logging.getLogger("test_info_only")
        logger.debug("should not appear")
        logger.info("should appear")

        content = log_file.read_text()
        assert "should not appear" not in content
        assert "should appear" in content

    def test_stderr_handler_always_present(self):
        cfg = BonaConfig.from_dict({})
        setup_logging(config=cfg)
        root = logging.getLogger()
        # Should have at least stderr handler
        assert len(root.handlers) >= 1
        assert any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            for h in root.handlers
        )

    def test_no_duplicate_handlers_on_reinit(self):
        cfg = BonaConfig.from_dict({})
        setup_logging(config=cfg)
        setup_logging(config=cfg)
        setup_logging(config=cfg)
        root = logging.getLogger()
        # Should clear handlers each time
        assert len(root.handlers) == 1  # just stderr

    def test_s3_path_creates_buffer_handler(self):
        cfg = BonaConfig.from_dict({"logging": {"path": "s3://bucket/logs/"}})
        setup_logging(config=cfg)
        root = logging.getLogger()
        # Should have stderr + s3 buffer = 2 handlers
        assert len(root.handlers) == 2

    def test_parent_dirs_created_for_file(self, tmp_path):
        log_file = tmp_path / "deep" / "nested" / "dir" / "bona.log"
        cfg = BonaConfig.from_dict({"logging": {"path": str(log_file)}})
        setup_logging(config=cfg)

        logger = logging.getLogger("test_nested")
        logger.info("nested test")

        assert log_file.exists()
