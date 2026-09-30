"""Tests for config loading."""

import pytest
from pathlib import Path
from wisper.main import _load_config, Config


class TestLoadConfig:
    """Tests for config loading functionality."""

    def test_defaults_when_no_config(self, tmp_path, monkeypatch):
        """Should return defaults when config file doesn't exist."""
        monkeypatch.setattr("wisper.main.CONFIG_PATH", tmp_path / "nonexistent.toml")
        cfg = _load_config()

        assert isinstance(cfg, Config)
        assert cfg.sample_rate == 16000
        assert cfg.channels == 1
        assert cfg.model_size == "turbo"

    def test_loads_existing_config(self, tmp_path, monkeypatch):
        """Should load values from existing config file."""
        config_content = """
[audio]
sample_rate = 44100
channels = 2

[ollama]
model_transcript_cleanup = "ministral-3:8b"
"""
        config_file = tmp_path / "config.toml"
        config_file.write_text(config_content)

        monkeypatch.setattr("wisper.main.CONFIG_PATH", config_file)
        cfg = _load_config()

        assert cfg.sample_rate == 44100
        assert cfg.channels == 2
        assert cfg.ollama_model_cleanup == "ministral-3:8b"

    def test_invalid_toml_returns_defaults(self, tmp_path, monkeypatch):
        """Should return defaults when TOML is invalid."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("this is not valid toml = [")

        monkeypatch.setattr("wisper.main.CONFIG_PATH", config_file)
        cfg = _load_config()

        assert isinstance(cfg, Config)
        assert cfg.sample_rate == 16000
