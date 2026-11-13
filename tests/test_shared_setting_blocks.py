"""The setup assistant uses the settings' building blocks instead of its own copies (audit finding H14)."""

from setup_fakes import InlineThread, fake_services

from hams import ollama as ollama_api
from hams import settings_sections, setup_window
from hams import setup_wizard as wizard_mod
from hams.config import CFG
from hams.settings_models import catalog_options
from hams.settings_status import LevelMonitor


def test_both_windows_share_the_microphone_rows(live_app, hams, monkeypatch, tmp_path):
    """Settings and setup assistant show the microphone with the same `LevelMonitor`, not with two copies."""
    monkeypatch.setattr(wizard_mod, "TaskThread", InlineThread)
    monkeypatch.setattr(setup_window, "CONFIG_PATH", tmp_path / "config.toml")
    live_app._open_settings()
    live_app.root.update()
    settings = live_app.settings_win.recording.monitor

    live_app.setup_win = setup = setup_window.SetupWindow(live_app, fake_services())
    try:
        while setup.controller.page != "microphone":
            setup.controller.next()
        setup.show_page()
        live_app.root.update()
        assert type(setup.monitor) is type(settings) is LevelMonitor
        assert setup.monitor.row_mic._title_text == settings.row_mic._title_text
        assert setup.monitor.row_level._title_text == settings.row_level._title_text
    finally:
        setup.close(confirmed=True)
        live_app.root.update()


def test_catalog_options_show_the_download_size_only_when_missing():
    options = {option.value: option for option in catalog_options({"turbo"})}
    assert options["turbo"].trailing == ""
    assert options["small"].trailing.startswith("↓ ")
    marked = catalog_options(set(), meta=lambda info: f"* {info.meta}", group=lambda info: "G")
    assert all(option.meta.startswith("* ") and option.group == "G" for option in marked)


def test_a_failing_probe_reads_as_offline_with_a_sentence():
    def broken():
        raise RuntimeError("kaputt")

    state = ollama_api.probe_or_offline(broken)
    assert state.online is False
    assert state.error == "Ollama antwortet unverständlich"


def test_a_working_probe_is_passed_through():
    state = ollama_api.OllamaState(True)
    assert ollama_api.probe_or_offline(lambda: state) is state


class TestLanguageChoices:
    def test_an_unchanged_language_is_not_saved(self, hams):
        assert settings_sections.choose_ui_language(CFG.ui_language) is None
        assert settings_sections.choose_dictation_language(CFG.transcription_language) is None
        assert hams.saved == []

    def test_a_new_language_is_saved(self, hams, monkeypatch):
        monkeypatch.setattr(CFG, "ui_language", "auto")
        assert settings_sections.choose_ui_language("en") == "en"
        assert settings_sections.choose_dictation_language("fr") == "fr"
        assert ("ui", "language", "en") in hams.saved
        assert ("transcription", "language", "fr") in hams.saved

    def test_an_unknown_dictation_language_is_ignored(self, hams):
        assert settings_sections.choose_dictation_language("klingonisch") is None
