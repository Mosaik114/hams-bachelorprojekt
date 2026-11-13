"""Tests des Konfigurationsortes: Vorlage, Nutzerdatei, Übernahme.

Keiner dieser Tests fasst die echte Konfiguration des Nutzers an: `CONFIG_PATH`
und die beiden Quellpfade werden je Test auf ein temporäres Verzeichnis
umgehängt. Die Suite als Ganzes läuft ausserdem mit `HAMS_CONFIG` auf einem
Wegwerfpfad (siehe conftest).
"""

import inspect
import os
import sys
from pathlib import Path

import pytest
from source_code import all_source

from hams import app_paths
from hams import config as config_mod

TEMPLATE = """[audio]
max_recording_seconds = 300
device = "default"

[model]
size = "turbo"

[ui]
sound_feedback = true
"""

LEGACY = """[audio]
max_recording_seconds = 120
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
    user = tmp_path / "appdata" / "Hams" / "config.toml"
    old_dir = tmp_path / "appdata" / "Wisper"
    template.write_text(TEMPLATE, encoding="utf-8")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", template)
    monkeypatch.setattr(config_mod, "LEGACY_CONFIG_PATH", legacy)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", user)
    monkeypatch.setattr(config_mod, "LEGACY_USER_DIR", old_dir)
    return type("Paths", (), {"template": template, "legacy": legacy, "user": user, "old_dir": old_dir})


# ------------------------------------------------------------------- Pfade


class TestUserConfigPath:
    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-Pfad")
    def test_it_lands_under_appdata(self, monkeypatch):
        monkeypatch.delenv("HAMS_CONFIG", raising=False)
        monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
        path = config_mod._user_config_path()
        assert path == Path(r"C:\Users\test\AppData\Roaming\Hams\config.toml")

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-Pfad")
    def test_the_old_folder_is_known_on_windows(self, monkeypatch):
        monkeypatch.delenv("HAMS_CONFIG", raising=False)
        monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
        assert config_mod._legacy_user_dir() == Path(r"C:\Users\test\AppData\Roaming\Wisper")

    @pytest.mark.skipif(sys.platform == "win32", reason="XDG-Pfad")
    def test_it_lands_under_xdg_config_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HAMS_CONFIG", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert config_mod._user_config_path() == tmp_path / "xdg" / "hams" / "config.toml"
        assert config_mod._legacy_user_dir() == tmp_path / "xdg" / "Wisper"

    @pytest.mark.skipif(sys.platform == "win32", reason="XDG-Pfad")
    def test_without_xdg_it_uses_dot_config(self, monkeypatch):
        monkeypatch.delenv("HAMS_CONFIG", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert config_mod._user_config_path() == Path.home() / ".config" / "hams" / "config.toml"

    def test_the_environment_variable_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HAMS_CONFIG", str(tmp_path / "eigene.toml"))
        assert config_mod._user_config_path() == tmp_path / "eigene.toml"

    def test_without_appdata_it_still_answers(self, monkeypatch):
        monkeypatch.delenv("HAMS_CONFIG", raising=False)
        monkeypatch.delenv("APPDATA", raising=False)
        path = config_mod._user_config_path()
        assert path.name == "config.toml" and path.parent.name == config_mod.USER_DIR_NAME

    def test_the_live_path_is_not_in_the_repository(self):
        """Der eigentliche Punkt dieser Phase."""
        assert config_mod.APP_DIR not in config_mod.CONFIG_PATH.parents

    def test_the_template_ships_with_the_package(self):
        assert config_mod.DEFAULT_CONFIG_PATH.parent == config_mod.APP_DIR
        assert config_mod.DEFAULT_CONFIG_PATH.exists()


# ----------------------------------------------------------------- Erststart


class TestBootstrap:
    def test_a_new_installation_gets_the_template(self, paths):
        assert config_mod.ensure_user_config() == config_mod.CONFIG_CREATED
        assert paths.user.exists()
        assert paths.user.read_text(encoding="utf-8") == TEMPLATE

    def test_the_directory_is_created(self, paths):
        assert not paths.user.parent.exists()
        config_mod.ensure_user_config()
        assert paths.user.parent.is_dir()

    def test_an_existing_file_is_left_alone(self, paths):
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text("# meins\n", encoding="utf-8")
        assert config_mod.ensure_user_config() == config_mod.CONFIG_EXISTING
        assert paths.user.read_text(encoding="utf-8") == "# meins\n"

    def test_a_blocked_place_is_reported(self, paths):
        paths.user.parent.parent.mkdir(parents=True, exist_ok=True)
        (paths.user.parent.parent / "Hams").write_text("keine Ablage", encoding="utf-8")
        assert config_mod.ensure_user_config() == config_mod.CONFIG_FAILED

    def test_nothing_falls_back_into_the_project(self, paths):
        """Persönliche Werte gehören nicht in den Programmordner."""
        paths.user.parent.parent.mkdir(parents=True, exist_ok=True)
        (paths.user.parent.parent / "Hams").write_text("blockiert", encoding="utf-8")
        before = paths.template.read_text(encoding="utf-8")
        config_mod.ensure_user_config()
        assert paths.template.read_text(encoding="utf-8") == before
        assert not paths.legacy.exists()


# ------------------------------------------------------------------ Migration


class TestMigration:
    def test_an_old_installation_keeps_its_values(self, paths):
        paths.legacy.write_text(LEGACY, encoding="utf-8")
        assert config_mod.ensure_user_config() == config_mod.CONFIG_MIGRATED
        assert paths.user.read_text(encoding="utf-8") == LEGACY

    def test_it_happens_only_once(self, paths):
        paths.legacy.write_text(LEGACY, encoding="utf-8")
        config_mod.ensure_user_config()
        paths.user.write_text("# inzwischen geaendert\n", encoding="utf-8")
        paths.legacy.write_text("[audio]\ndevice = \"anders\"\n", encoding="utf-8")
        assert config_mod.ensure_user_config() == config_mod.CONFIG_EXISTING
        assert paths.user.read_text(encoding="utf-8") == "# inzwischen geaendert\n"

    def test_a_changed_template_never_overwrites_user_values(self, paths):
        config_mod.ensure_user_config()
        paths.user.write_text("[audio]\ndevice = \"meins\"\n", encoding="utf-8")
        paths.template.write_text("[audio]\ndevice = \"neu\"\n", encoding="utf-8")
        config_mod.ensure_user_config()
        assert 'device = "meins"' in paths.user.read_text(encoding="utf-8")

    def test_the_old_file_is_not_deleted(self, paths):
        """Hams nimmt sich die Werte, raeumt aber nicht im Projektordner auf."""
        paths.legacy.write_text(LEGACY, encoding="utf-8")
        config_mod.ensure_user_config()
        assert paths.legacy.exists()


# ------------------------------------------------- Umzug aus dem Wisper-Ordner


def _fill_old_dir(old_dir: Path) -> None:
    (old_dir / "unterordner").mkdir(parents=True)
    (old_dir / "config.toml").write_text(LEGACY, encoding="utf-8")
    (old_dir / "glossar.toml").write_text('[begriffe]\n"hams" = "Hams"\n', encoding="utf-8")
    (old_dir / "unterordner" / "notiz.txt").write_text("bleibt", encoding="utf-8")


def _snapshot(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


class TestLegacyFolderMigration:
    def test_the_old_folder_is_copied_on_first_start(self, paths):
        _fill_old_dir(paths.old_dir)
        assert config_mod.ensure_user_config() == config_mod.CONFIG_MIGRATED
        assert paths.user.read_text(encoding="utf-8") == LEGACY
        assert _snapshot(paths.user.parent) == _snapshot(paths.old_dir)

    def test_the_old_folder_stays_untouched(self, paths):
        """Kopieren, nie verschieben: eine aeltere Fassung liest dort weiter."""
        _fill_old_dir(paths.old_dir)
        before = _snapshot(paths.old_dir)
        config_mod.ensure_user_config()
        config_mod._persist_config_value("audio", "device", "Yeti")
        assert _snapshot(paths.old_dir) == before

    def test_it_is_logged(self, paths, caplog):
        _fill_old_dir(paths.old_dir)
        with caplog.at_level("INFO", logger="hams"):
            config_mod.ensure_user_config()
        assert any("kopiert" in record.getMessage() for record in caplog.records)

    def test_an_existing_new_folder_wins(self, paths):
        _fill_old_dir(paths.old_dir)
        paths.user.parent.mkdir(parents=True)
        assert config_mod.migrate_legacy_user_dir(paths.user.parent, paths.old_dir) is False
        assert config_mod.ensure_user_config() == config_mod.CONFIG_CREATED
        assert paths.user.read_text(encoding="utf-8") == TEMPLATE

    def test_it_happens_only_once(self, paths):
        _fill_old_dir(paths.old_dir)
        config_mod.ensure_user_config()
        paths.user.write_text("# inzwischen geaendert\n", encoding="utf-8")
        assert config_mod.ensure_user_config() == config_mod.CONFIG_EXISTING
        assert paths.user.read_text(encoding="utf-8") == "# inzwischen geaendert\n"

    def test_without_an_old_folder_nothing_happens(self, paths):
        assert config_mod.migrate_legacy_user_dir(paths.user.parent, paths.old_dir) is False
        assert not paths.user.parent.exists()

    def test_without_a_known_old_folder_nothing_happens(self, paths):
        assert config_mod.migrate_legacy_user_dir(paths.user.parent, None) is False

    def test_an_old_file_instead_of_a_folder_is_ignored(self, paths):
        paths.old_dir.parent.mkdir(parents=True)
        paths.old_dir.write_text("kein Ordner", encoding="utf-8")
        assert config_mod.ensure_user_config() == config_mod.CONFIG_CREATED

    def test_an_old_folder_without_config_still_gets_the_template(self, paths):
        paths.old_dir.mkdir(parents=True)
        (paths.old_dir / "glossar.toml").write_text("[begriffe]\n", encoding="utf-8")
        assert config_mod.ensure_user_config() == config_mod.CONFIG_CREATED
        assert paths.user.read_text(encoding="utf-8") == TEMPLATE
        assert (paths.user.parent / "glossar.toml").exists()

    def test_a_failed_copy_leaves_no_half_folder(self, paths, monkeypatch):
        """Sonst saehe der naechste Start einen „vorhandenen" Ordner und uebernaehme nie."""
        _fill_old_dir(paths.old_dir)

        def _broken(_src, dst, **_kwargs):
            Path(dst).mkdir(parents=True)
            (Path(dst) / "config.toml").write_text("halb", encoding="utf-8")
            raise OSError("Platte voll")

        monkeypatch.setattr(config_mod.shutil, "copytree", _broken)
        assert config_mod.migrate_legacy_user_dir(paths.user.parent, paths.old_dir) is False
        assert not paths.user.parent.exists()
        assert list(paths.user.parent.parent.iterdir()) == [paths.old_dir]

    def test_a_leftover_staging_folder_is_replaced(self, paths):
        _fill_old_dir(paths.old_dir)
        staging = paths.user.parent.with_name(paths.user.parent.name + ".migrating")
        staging.mkdir(parents=True)
        (staging / "rest.txt").write_text("alt", encoding="utf-8")
        assert config_mod.migrate_legacy_user_dir(paths.user.parent, paths.old_dir) is True
        assert not (paths.user.parent / "rest.txt").exists()
        assert not staging.exists()


# --------------------------------------------------------------- Drei Ebenen


class TestLayering:
    def test_the_template_alone_is_enough(self, paths):
        config = config_mod._load_config()
        assert config.max_recording_seconds == 300 and config.model_size == "turbo"

    def test_the_user_file_wins(self, paths):
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text('[model]\nsize = "small"\n', encoding="utf-8")
        config = config_mod._load_config()
        assert config.model_size == "small"

    def test_missing_keys_come_from_the_template(self, paths):
        """Eine aeltere Nutzerdatei kennt neue Schluessel nicht — das ist ok."""
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text('[model]\nsize = "small"\n', encoding="utf-8")
        config = config_mod._load_config()
        assert config.max_recording_seconds == 300
        assert config.sound_feedback is True

    def test_missing_everywhere_falls_back_to_the_code(self, paths):
        paths.template.write_text("", encoding="utf-8")
        config = config_mod._load_config()
        assert config.max_recording_seconds == config_mod.Config().max_recording_seconds

    def test_a_broken_user_file_does_not_stop_the_program(self, paths):
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text("das ist [[kein toml", encoding="utf-8")
        config = config_mod._load_config()
        assert config.model_size == "turbo", "die Vorlage traegt weiter"

    def test_a_broken_template_does_not_stop_the_program(self, paths):
        paths.template.write_text("kaputt [[[", encoding="utf-8")
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text('[model]\nsize = "base"\n', encoding="utf-8")
        assert config_mod._load_config().model_size == "base"

    def test_the_window_position_survives_the_layers(self, paths):
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text("[ui]\nwindow_x = 812\nwindow_y = 377\n", encoding="utf-8")
        config = config_mod._load_config()
        assert (config.window_x, config.window_y) == (812, 377)

    def test_a_position_only_in_the_template_is_ignored_by_a_user_file(self, paths):
        paths.template.write_text("[ui]\nwindow_x = 10\nwindow_y = 10\n", encoding="utf-8")
        paths.user.parent.mkdir(parents=True)
        paths.user.write_text("[ui]\nwindow_x = 500\nwindow_y = 600\n", encoding="utf-8")
        config = config_mod._load_config()
        assert (config.window_x, config.window_y) == (500, 600)


# ------------------------------------------------------------------ Schreiben


class TestWriting:
    @pytest.mark.parametrize(
        "section,key,value",
        [
            ("audio", "device", "Yeti"),
            ("model", "size", "small"),
            ("ollama", "model_transcript_cleanup", "qwen2.5:3b"),
            ("ollama", "model_prompt_generate", "qwen2.5:7b"),
            ("ollama", "cleanup_enabled", False),
            ("ui", "sound_feedback", False),
            ("ui", "window_x", 812),
        ],
    )
    def test_every_setting_lands_in_the_user_file(self, paths, section, key, value):
        config_mod.ensure_user_config()
        assert config_mod._persist_config_value(section, key, value) is True
        assert key in paths.user.read_text(encoding="utf-8")

    def test_the_template_stays_untouched(self, paths):
        before = paths.template.read_text(encoding="utf-8")
        config_mod.ensure_user_config()
        for section, key, value in (
            ("audio", "device", "Yeti"),
            ("model", "size", "base"),
            ("ui", "sound_feedback", False),
        ):
            config_mod._persist_config_value(section, key, value)
        assert paths.template.read_text(encoding="utf-8") == before

    def test_the_old_project_file_stays_untouched(self, paths):
        paths.legacy.write_text(LEGACY, encoding="utf-8")
        config_mod.ensure_user_config()
        config_mod._persist_config_value("audio", "device", "Yeti")
        assert paths.legacy.read_text(encoding="utf-8") == LEGACY

    def test_writing_creates_the_file_if_it_is_missing(self, paths):
        assert not paths.user.exists()
        assert config_mod._persist_config_value("ui", "sound_feedback", False) is True
        assert paths.user.exists()


# ------------------------------------------------------------ Produktdateien


class TestResourcePaths:
    def test_the_cleanup_prompt_stays_with_the_package(self):
        """Eine Produktdatei, keine Nutzerkonfiguration."""
        source = inspect.getsource(config_mod._load_cleanup_prompt)  # aus _apply_ollama ausgelagert
        assert "(APP_DIR / prompt_file_rel)" in source
        assert "CONFIG_PATH / prompt_file_rel" not in all_source()

    def test_the_shipped_prompt_exists(self):
        assert (config_mod.APP_DIR / "prompts" / "cleanup.txt").exists()

    def test_transcripts_and_logs_live_outside_the_package(self, monkeypatch, tmp_path):
        """Installiert gehoert der Programmordner dem Installer: ein Update ersetzt ihn.

        Fuer das Log wird die Voreinstellung geprueft, nicht `config_mod.LOG_DIR`:
        die Testsuite leitet es ueber `HAMS_LOG_DIR` in ein Temporaer-
        verzeichnis um, damit ein Testlauf nicht in die Aufzeichnung des
        Nutzers schreibt. Ohne die Umleitung gilt der Datenordner.
        """
        assert config_mod.APP_DIR not in config_mod.OUTPUT_DIR.parents
        assert app_paths.transcripts_dir() == config_mod.OUTPUT_DIR
        monkeypatch.delenv("HAMS_LOG_DIR", raising=False)
        monkeypatch.setenv("HAMS_DATA_DIR", str(tmp_path / "daten"))
        assert config_mod._log_dir() == tmp_path / "daten" / "logs"
        assert config_mod.APP_DIR not in config_mod._log_dir().parents

    def test_the_log_can_be_moved_out_of_the_way(self, monkeypatch, tmp_path):
        """Derselbe Ausweg wie bei HAMS_CONFIG, aus demselben Grund."""
        monkeypatch.setenv("HAMS_LOG_DIR", str(tmp_path / "woanders"))
        assert config_mod._log_dir() == tmp_path / "woanders"

    def test_autostart_does_not_care_about_the_config(self):
        source = (config_mod.APP_DIR / "setup_autostart.py").read_text(encoding="utf-8")
        assert "config" not in source.lower()


# ------------------------------------------------------------------- Vorlage


class TestTemplateContent:
    @pytest.fixture
    def template(self):
        import tomllib

        with config_mod.DEFAULT_CONFIG_PATH.open("rb") as handle:
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
        for section in ("audio", "model", "transcription", "hotkeys", "ollama", "clipboard", "ui"):
            assert section in template

    def test_the_repository_no_longer_holds_a_live_config(self):
        assert not (config_mod.APP_DIR / "config.toml").exists()


class TestSuiteIsolation:
    def test_the_tests_run_against_a_throwaway_file(self):
        override = os.environ.get("HAMS_CONFIG")
        assert override, "conftest muss HAMS_CONFIG setzen"
        assert "AppData\\Roaming\\Wisper" not in override
        assert "AppData\\Roaming\\Hams" not in override

    def test_the_real_user_config_is_out_of_reach(self):
        for name in ("Hams", "Wisper"):
            real = Path(os.environ.get("APPDATA", "")) / name / "config.toml"
            assert real != config_mod.CONFIG_PATH

    def test_the_real_old_folder_is_out_of_reach(self):
        """Mit umgeleiteter Konfiguration gibt es nichts zu uebernehmen."""
        assert config_mod.LEGACY_USER_DIR is None
        assert config_mod._legacy_user_dir() is None


class TestDocumentedOllamaDefault:
    """Ein Standardmodell, ueberall dasselbe: Code, Vorlage, README und Auswertung."""

    ROOT = config_mod.APP_DIR.parent

    @pytest.fixture
    def default(self):
        model = config_mod.Config().ollama_model_cleanup
        assert model == config_mod.Config().ollama_model_prompt
        return model

    def test_the_template_names_it(self, default):
        text = config_mod.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        assert f"# model_transcript_cleanup = \"{default}\"" in text
        assert f"# model_prompt_generate = \"{default}\"" in text
        assert f"ollama pull {default}" in text

    def test_the_readme_installs_it(self, default):
        text = (self.ROOT / "README.md").read_text(encoding="utf-8")
        assert f"ollama pull {default}" in text

    def test_the_evaluation_separates_measured_and_default(self, default):
        text = (self.ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
        flat = " ".join(text.split())
        assert f"current default cleanup and prompt model is `{default}`" in flat
        assert "All thesis measurements in this document were taken with `qwen2.5:7b`" in flat
