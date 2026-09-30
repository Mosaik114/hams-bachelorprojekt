"""Tests des Konfigurationsortes: Vorlage, Nutzerdatei, Übernahme.

Keiner dieser Tests fasst die echte Konfiguration des Nutzers an: `CONFIG_PATH`
und die beiden Quellpfade werden je Test auf ein temporäres Verzeichnis
umgehängt. Die Suite als Ganzes läuft ausserdem mit `WISPER_CONFIG` auf einem
Wegwerfpfad (siehe conftest).
"""

import os
from pathlib import Path

import pytest

from wisper import main as wm

TEMPLATE = """[audio]
sample_rate = 16000
device = "default"

[model]
size = "turbo"

[ui]
sound_feedback = true
"""

LEGACY = """[audio]
sample_rate = 48000
device = "Shure MV7+"

[model]
size = "large-v3"

[ui]
sound_feedback = false
"""


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Vorlage, Altdatei und Nutzerdatei in einem Wegwerfverzeichnis."""
    template = tmp_path / "config.default.toml"
    legacy = tmp_path / "projekt" / "config.toml"
    user = tmp_path / "appdata" / "Wisper" / "config.toml"
    template.write_text(TEMPLATE, encoding="utf-8")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wm, "DEFAULT_CONFIG_PATH", template)
    monkeypatch.setattr(wm, "LEGACY_CONFIG_PATH", legacy)
    monkeypatch.setattr(wm, "CONFIG_PATH", user)
    return type("Paths", (), {"template": template, "legacy": legacy, "user": user})


# ------------------------------------------------------------------- Pfade


class TestUserConfigPath:
    def test_it_lands_under_appdata(self, monkeypatch):
        monkeypatch.delenv("WISPER_CONFIG", raising=False)
        monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
        path = wm._user_config_path()
        assert path == Path(r"C:\Users\test\AppData\Roaming\Wisper\config.toml")

    def test_the_environment_variable_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WISPER_CONFIG", str(tmp_path / "eigene.toml"))
        assert wm._user_config_path() == tmp_path / "eigene.toml"

    def test_without_appdata_it_still_answers(self, monkeypatch):
        monkeypatch.delenv("WISPER_CONFIG", raising=False)
        monkeypatch.delenv("APPDATA", raising=False)
        path = wm._user_config_path()
        assert path.name == "config.toml" and "Wisper" in str(path)

    def test_the_live_path_is_not_in_the_repository(self):
        """Der eigentliche Punkt dieser Phase."""
        assert wm.APP_DIR not in wm.CONFIG_PATH.parents

    def test_the_template_ships_with_the_package(self):
        assert wm.DEFAULT_CONFIG_PATH.parent == wm.APP_DIR
        assert wm.DEFAULT_CONFIG_PATH.exists()


# ----------------------------------------------------------------- Erststart


class TestBootstrap:
    def test_a_new_installation_gets_the_template(self, paths):
        assert wm.ensure_user_config() == "angelegt"
        assert paths.user.exists()
        assert paths.user.read_text(encoding="utf-8") == TEMPLATE

    def test_the_directory_is_created(self, paths):
        assert not paths.user.parent.exists()
        wm.ensure_user_config()
        assert paths.user.parent.is_dir()

    def test_an_existing_file_is_left_alone(self, paths):
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text("# meins\n", encoding="utf-8")
        assert wm.ensure_user_config() == "vorhanden"
        assert paths.user.read_text(encoding="utf-8") == "# meins\n"

    def test_a_blocked_place_is_reported(self, paths):
        paths.user.parent.parent.mkdir(parents=True, exist_ok=True)
        (paths.user.parent.parent / "Wisper").write_text("keine Ablage",
                                                         encoding="utf-8")
        assert wm.ensure_user_config() == "fehlgeschlagen"

    def test_nothing_falls_back_into_the_project(self, paths):
        """Persönliche Werte gehören nicht in den Programmordner."""
        paths.user.parent.parent.mkdir(parents=True, exist_ok=True)
        (paths.user.parent.parent / "Wisper").write_text("blockiert",
                                                         encoding="utf-8")
        before = paths.template.read_text(encoding="utf-8")
        wm.ensure_user_config()
        assert paths.template.read_text(encoding="utf-8") == before
        assert not paths.legacy.exists()


# ------------------------------------------------------------------ Migration


class TestMigration:
    def test_an_old_installation_keeps_its_values(self, paths):
        paths.legacy.write_text(LEGACY, encoding="utf-8")
        assert wm.ensure_user_config() == "migriert"
        assert paths.user.read_text(encoding="utf-8") == LEGACY

    def test_it_happens_only_once(self, paths):
        paths.legacy.write_text(LEGACY, encoding="utf-8")
        wm.ensure_user_config()
        paths.user.write_text("# inzwischen geaendert\n", encoding="utf-8")
        paths.legacy.write_text("[audio]\ndevice = \"anders\"\n", encoding="utf-8")
        assert wm.ensure_user_config() == "vorhanden"
        assert paths.user.read_text(encoding="utf-8") == "# inzwischen geaendert\n"

    def test_a_changed_template_never_overwrites_user_values(self, paths):
        wm.ensure_user_config()
        paths.user.write_text("[audio]\ndevice = \"meins\"\n", encoding="utf-8")
        paths.template.write_text("[audio]\ndevice = \"neu\"\n", encoding="utf-8")
        wm.ensure_user_config()
        assert 'device = "meins"' in paths.user.read_text(encoding="utf-8")

    def test_the_old_file_is_not_deleted(self, paths):
        """Wisper nimmt sich die Werte, raeumt aber nicht im Projektordner auf."""
        paths.legacy.write_text(LEGACY, encoding="utf-8")
        wm.ensure_user_config()
        assert paths.legacy.exists()


# --------------------------------------------------------------- Drei Ebenen


class TestLayering:
    def test_the_template_alone_is_enough(self, paths):
        config = wm._load_config()
        assert config.sample_rate == 16000 and config.model_size == "turbo"

    def test_the_user_file_wins(self, paths):
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text('[model]\nsize = "small"\n', encoding="utf-8")
        config = wm._load_config()
        assert config.model_size == "small"

    def test_missing_keys_come_from_the_template(self, paths):
        """Eine aeltere Nutzerdatei kennt neue Schluessel nicht — das ist ok."""
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text('[model]\nsize = "small"\n', encoding="utf-8")
        config = wm._load_config()
        assert config.sample_rate == 16000
        assert config.sound_feedback is True

    def test_missing_everywhere_falls_back_to_the_code(self, paths):
        paths.template.write_text("", encoding="utf-8")
        config = wm._load_config()
        assert config.sample_rate == wm.Config().sample_rate

    def test_a_broken_user_file_does_not_stop_the_program(self, paths):
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text("das ist [[kein toml", encoding="utf-8")
        config = wm._load_config()
        assert config.model_size == "turbo", "die Vorlage traegt weiter"

    def test_a_broken_template_does_not_stop_the_program(self, paths):
        paths.template.write_text("kaputt [[[", encoding="utf-8")
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text('[model]\nsize = "base"\n', encoding="utf-8")
        assert wm._load_config().model_size == "base"

    def test_the_window_position_survives_the_layers(self, paths):
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text("[ui]\nwindow_x = 812\nwindow_y = 377\n",
                              encoding="utf-8")
        config = wm._load_config()
        assert (config.window_x, config.window_y) == (812, 377)

    def test_a_position_only_in_the_template_is_ignored_by_a_user_file(self, paths):
        paths.template.write_text("[ui]\nwindow_x = 10\nwindow_y = 10\n",
                                  encoding="utf-8")
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text("[ui]\nwindow_x = 500\nwindow_y = 600\n",
                              encoding="utf-8")
        config = wm._load_config()
        assert (config.window_x, config.window_y) == (500, 600)


# ------------------------------------------------------------------ Schreiben


class TestWriting:
    @pytest.mark.parametrize("section,key,value", [
        ("audio", "device", "Yeti"),
        ("model", "size", "small"),
        ("ollama", "model_transcript_cleanup", "qwen2.5:3b"),
        ("ollama", "model_prompt_generate", "qwen2.5:7b"),
        ("ollama", "cleanup_enabled", False),
        ("ui", "sound_feedback", False),
        ("ui", "window_x", 812),
    ])
    def test_every_setting_lands_in_the_user_file(self, paths, section, key, value):
        wm.ensure_user_config()
        assert wm._persist_config_value(section, key, value) is True
        assert key in paths.user.read_text(encoding="utf-8")

    def test_the_template_stays_untouched(self, paths):
        before = paths.template.read_text(encoding="utf-8")
        wm.ensure_user_config()
        for section, key, value in (("audio", "device", "Yeti"),
                                    ("model", "size", "base"),
                                    ("ui", "sound_feedback", False)):
            wm._persist_config_value(section, key, value)
        assert paths.template.read_text(encoding="utf-8") == before

    def test_the_old_project_file_stays_untouched(self, paths):
        paths.legacy.write_text(LEGACY, encoding="utf-8")
        wm.ensure_user_config()
        wm._persist_config_value("audio", "device", "Yeti")
        assert paths.legacy.read_text(encoding="utf-8") == LEGACY

    def test_writing_creates_the_file_if_it_is_missing(self, paths):
        assert not paths.user.exists()
        assert wm._persist_config_value("ui", "sound_feedback", False) is True
        assert paths.user.exists()


# ------------------------------------------------------------ Produktdateien


class TestResourcePaths:
    def test_the_cleanup_prompt_stays_with_the_package(self):
        """Eine Produktdatei, keine Nutzerkonfiguration."""
        source = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        assert "(APP_DIR / prompt_file_rel)" in source
        assert "CONFIG_PATH / prompt_file_rel" not in source

    def test_the_shipped_prompt_exists(self):
        assert (wm.APP_DIR / "prompts" / "cleanup.txt").exists()

    def test_transcripts_and_logs_stay_where_they_were(self, monkeypatch):
        """Ausgeliefert liegt beides beim Paket — anders als die Konfiguration.

        Fuer das Log wird die Voreinstellung geprueft, nicht `wm.LOG_DIR`:
        die Testsuite leitet es ueber `WISPER_LOG_DIR` in ein Temporaer-
        verzeichnis um, damit ein Testlauf nicht in die Aufzeichnung des
        Nutzers schreibt. Ohne die Umleitung gilt weiter der Ort beim Paket.
        """
        assert wm.OUTPUT_DIR.parent == wm.APP_DIR
        monkeypatch.delenv("WISPER_LOG_DIR", raising=False)
        assert wm._log_dir() == wm.APP_DIR / "logs"

    def test_the_log_can_be_moved_out_of_the_way(self, monkeypatch, tmp_path):
        """Derselbe Ausweg wie bei WISPER_CONFIG, aus demselben Grund."""
        monkeypatch.setenv("WISPER_LOG_DIR", str(tmp_path / "woanders"))
        assert wm._log_dir() == tmp_path / "woanders"

    def test_autostart_does_not_care_about_the_config(self):
        source = (wm.APP_DIR / "setup_autostart.py").read_text(encoding="utf-8")
        assert "config" not in source.lower()


# ------------------------------------------------------------------- Vorlage


class TestTemplateContent:
    @pytest.fixture
    def template(self):
        import tomllib

        with wm.DEFAULT_CONFIG_PATH.open("rb") as handle:
            return tomllib.load(handle)

    def test_no_personal_microphone(self, template):
        assert template["audio"]["device"] == "default"

    def test_no_ollama_model_is_presumed(self, template):
        """Ein Modellname hier hiesse: es ist auf dem Rechner installiert."""
        assert "model_transcript_cleanup" not in template["ollama"]
        assert "model_prompt_generate" not in template["ollama"]

    def test_no_window_position(self, template):
        assert "window_x" not in template.get("ui", {})
        assert "window_y" not in template.get("ui", {})

    def test_it_is_still_a_complete_starting_point(self, template):
        for section in ("audio", "model", "transcription", "hotkeys", "ollama",
                        "clipboard", "ui"):
            assert section in template

    def test_the_repository_no_longer_holds_a_live_config(self):
        assert not (wm.APP_DIR / "config.toml").exists()


class TestSuiteIsolation:
    def test_the_tests_run_against_a_throwaway_file(self):
        override = os.environ.get("WISPER_CONFIG")
        assert override, "conftest muss WISPER_CONFIG setzen"
        assert "AppData\\Roaming\\Wisper" not in override

    def test_the_real_user_config_is_out_of_reach(self):
        real = Path(os.environ.get("APPDATA", "")) / "Wisper" / "config.toml"
        assert wm.CONFIG_PATH != real
