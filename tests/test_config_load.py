"""Tests for config loading."""

from hams.config import Config, _load_config


class TestLoadConfig:
    """Tests for config loading functionality."""

    def test_defaults_when_no_config(self, tmp_path, monkeypatch):
        """Should return defaults when config file doesn't exist."""
        monkeypatch.setattr("hams.config.CONFIG_PATH", tmp_path / "nonexistent.toml")
        cfg = _load_config()

        assert isinstance(cfg, Config)
        assert cfg.max_recording_seconds == 300
        assert cfg.model_size == "turbo"

    def test_loads_existing_config(self, tmp_path, monkeypatch):
        """Should load values from existing config file."""
        config_content = """
[audio]
max_recording_seconds = 90

[ollama]
model_transcript_cleanup = "ministral-3:8b"
"""
        config_file = tmp_path / "config.toml"
        config_file.write_text(config_content)

        monkeypatch.setattr("hams.config.CONFIG_PATH", config_file)
        cfg = _load_config()

        assert cfg.max_recording_seconds == 90
        assert cfg.ollama_model_cleanup == "ministral-3:8b"

    def test_invalid_toml_returns_defaults(self, tmp_path, monkeypatch):
        """Should return defaults when TOML is invalid."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("this is not valid toml = [")

        monkeypatch.setattr("hams.config.CONFIG_PATH", config_file)
        cfg = _load_config()

        assert isinstance(cfg, Config)
        assert cfg.max_recording_seconds == 300


def test_fensterposition_bleibt_wenn_eine_schicht_sie_nicht_nennt():
    """Q7: window_x wurde von jeder Schicht ohne den Schlüssel auf None gesetzt, window_y nicht."""
    import copy

    from hams import config

    cfg = copy.deepcopy(config.CFG)
    config._apply_toml(cfg, {"ui": {"window_x": 120, "window_y": 80}})
    config._apply_toml(cfg, {"ui": {"sound_feedback": True}})
    assert (cfg.window_x, cfg.window_y) == (120, 80)
