"""Tests for configuration validation."""

from hams.config import Config
from hams.config_validation import validate_config


class TestValidateConfig:
    """Tests for config validation."""

    def test_valid_config(self):
        """Valid config should return no errors."""
        cfg = Config()
        errors = validate_config(cfg)
        assert errors == []

    def test_invalid_max_recording_seconds(self):
        """Invalid max recording duration should be flagged."""
        cfg = Config()
        cfg.max_recording_seconds = 0
        errors = validate_config(cfg)
        assert any("max_recording_seconds" in e for e in errors)

        cfg.max_recording_seconds = -1
        errors = validate_config(cfg)
        assert any("max_recording_seconds" in e for e in errors)

        cfg.max_recording_seconds = 5000
        errors = validate_config(cfg)
        assert any("max_recording_seconds" in e for e in errors)

    def test_invalid_beam_size(self):
        """Invalid beam sizes should be flagged."""
        cfg = Config()
        cfg.transcription_beam_size = 0
        errors = validate_config(cfg)
        assert any("beam_size" in e for e in errors)

        cfg.transcription_beam_size = 15
        errors = validate_config(cfg)
        assert any("beam_size" in e for e in errors)

    def test_invalid_timeout(self):
        """Invalid timeouts should be flagged."""
        cfg = Config()
        cfg.ollama_timeout_cleanup = 0
        errors = validate_config(cfg)
        assert any("timeout_cleanup" in e for e in errors)

    def test_invalid_ollama_url(self):
        """Invalid Ollama URL should be flagged."""
        cfg = Config()
        cfg.ollama_url = "not-a-url"
        errors = validate_config(cfg)
        assert any("ollama.url" in e for e in errors)
