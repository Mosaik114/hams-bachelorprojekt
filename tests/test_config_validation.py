"""Tests for configuration validation."""

import pytest
from wisper.config_validation import validate_config, ConfigValidationError
from wisper.main import Config


class TestValidateConfig:
    """Tests for config validation."""

    def test_valid_config(self):
        """Valid config should return no errors."""
        cfg = Config()
        errors = validate_config(cfg)
        assert errors == []

    def test_invalid_sample_rate(self):
        """Invalid sample rates should be flagged."""
        cfg = Config()
        cfg.sample_rate = 12345
        errors = validate_config(cfg)
        assert any("sample_rate" in e for e in errors)

    def test_invalid_channels(self):
        """Invalid channel counts should be flagged."""
        cfg = Config()
        cfg.channels = 5
        errors = validate_config(cfg)
        assert any("channels" in e for e in errors)

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

    def test_valid_sample_rates(self):
        """Valid sample rates should pass."""
        for rate in (16000, 22050, 44100, 48000):
            cfg = Config()
            cfg.sample_rate = rate
            errors = validate_config(cfg)
            assert not any("sample_rate" in e for e in errors), f"Failed for rate {rate}"

    def test_valid_channels(self):
        """Valid channel counts should pass."""
        for channels in (1, 2):
            cfg = Config()
            cfg.channels = channels
            errors = validate_config(cfg)
            assert not any("channels" in e for e in errors), f"Failed for channels {channels}"
