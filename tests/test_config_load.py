"""Tests for config loading."""

import os
import subprocess
import sys

import pytest

from hams import config as config_mod
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


class TestImportAndBootstrap:
    """Importing the settings defines them; `bootstrap` does the work, explicitly."""

    @staticmethod
    def run_probe(code: str, tmp_path) -> tuple[list, object]:
        config_file = tmp_path / "config.toml"
        config_file.write_text('[ui]\nappearance = "dark"\n[model]\nsize = "small"\n', encoding="utf-8")
        log_dir = tmp_path / "logs"
        env = {**os.environ, "HAMS_CONFIG": str(config_file), "HAMS_LOG_DIR": str(log_dir)}
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            cwd=config_mod.APP_DIR.parent,
            env=env,
        )
        return result.stdout.split(), log_dir

    def test_importing_the_settings_reads_nothing_and_starts_nothing(self, tmp_path):
        code = (
            "import sys, logging, hams.config as c; "
            "print(c.CFG == c.Config(), 'sounddevice' in sys.modules, 'hams.ui_kit' in sys.modules, "
            "bool(logging.getLogger('hams').handlers))"
        )
        output, log_dir = self.run_probe(code, tmp_path)
        assert output == ["True", "False", "False", "False"]
        assert not log_dir.exists()

    def test_bootstrap_sets_up_the_log_reads_the_file_and_applies_the_scheme(self, tmp_path):
        code = (
            "from hams import bootstrap, config; from hams.ui_kit import Theme; bootstrap.load(); "
            "print(config.CFG.model_size, Theme.mode)"
        )
        output, log_dir = self.run_probe(code, tmp_path)
        assert output == ["small", "dark"]
        assert (log_dir / "hams.log").exists()

    def test_reload_updates_the_one_settings_object_in_place(self, monkeypatch, tmp_path):
        user_file = tmp_path / "config.toml"
        user_file.write_text("[ui]\nsound_feedback = false\n", encoding="utf-8")
        monkeypatch.setattr(config_mod, "CONFIG_PATH", user_file)
        before = dict(vars(config_mod.CFG))
        try:
            assert config_mod.reload() is config_mod.CFG
            assert config_mod.CFG.sound_feedback is False
        finally:
            vars(config_mod.CFG).update(before)

    @pytest.mark.parametrize("value", ["light", "dark", " Dark ", "regenbogen", None, 3])
    def test_the_settings_and_the_theme_agree_on_the_schemes(self, value):
        from hams.ui_kit import Theme

        assert config_mod.APPEARANCES == Theme.MODES
        assert config_mod.DEFAULT_APPEARANCE == Theme.DEFAULT_MODE
        assert config_mod.normalise_appearance(value) == Theme.normalise(value)
