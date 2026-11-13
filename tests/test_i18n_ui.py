"""The real windows in both languages: every visible text comes from the active catalog.

The widget walk collects what a user can read: label and button texts, the full sub-lines of setting rows, the
options of every select (they are drawn into an image, so `cget("text")` doesn't see them), keycaps and window
titles.
"""

import contextlib
import tkinter as tk
from types import SimpleNamespace

import pytest
from test_i18n import looks_german

from hams import i18n
from hams import models as whisper_models
from hams import settings_models as models_mod
from hams import settings_status as status_mod
from hams import ui_kit as uk
from hams.config import CFG

#: Shown in their own language on purpose (the language picker).
ENDONYMS = {"Deutsch", "English"}


class InlineThread:
    def __init__(self, target, args=(), **_options):
        self.target, self.args = target, args

    def start(self) -> None:
        self.target(*self.args)


@pytest.fixture
def open_settings(live_app, monkeypatch):
    monkeypatch.setattr(whisper_models, "installed_whisper_models", lambda: {"turbo"})
    monkeypatch.setattr(whisper_models, "cached_whisper_models", lambda: ())
    inline = SimpleNamespace(Thread=InlineThread)
    monkeypatch.setattr(status_mod, "threading", inline)
    monkeypatch.setattr(models_mod, "threading", inline)
    CFG.model_size = "turbo"
    CFG.mic_device_raw = "Headset"

    def _open():
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win

    return _open


def _own_texts(widget) -> list:
    texts = []
    with contextlib.suppress(tk.TclError, AttributeError):
        texts.append(str(widget.cget("text")))
    if isinstance(widget, uk.SettingRow):
        texts.append(widget.sub_text)
    if isinstance(widget, uk.Select):
        texts.append(widget._placeholder)
        for option in widget.options:
            texts += [option.label, option.meta, option.trailing, option.group]
    if isinstance(widget, uk.KeyCombo):
        texts += widget.labels if widget.labels else [widget.EMPTY]
    return texts


def visible_texts(window: tk.Misc) -> list:
    """All readable texts of a window, in widget order, without empties and duplicates."""
    texts = [window.wm_title()] if isinstance(window, (tk.Tk, tk.Toplevel)) else []
    stack = [window]
    while stack:
        widget = stack.pop(0)
        texts += _own_texts(widget)
        stack += widget.winfo_children()
    seen, result = set(), []
    for text in texts:
        text = (text or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def german_texts(texts) -> list:
    return [text for text in texts if text not in ENDONYMS and looks_german(text)]


def tray_texts(app) -> list:
    menu = app._build_tray_menu()
    texts = [app._tray_tooltip()]
    for item in menu.items:
        texts.append(str(item.text))
        if item.submenu:
            texts += [str(sub.text) for sub in item.submenu.items]
    return [text for text in texts if text]


class TestEnglish:
    def test_settings_window_has_no_german(self, english, open_settings):
        win = open_settings()
        texts = visible_texts(win.win)
        assert "Settings" in texts and "Language" in texts
        assert not german_texts(texts)

    def test_main_window_has_no_german(self, english, live_app):
        live_app.apply_state("ready")
        texts = visible_texts(live_app.root)
        assert "Start recording" in texts and "Dictate" in texts
        assert {"Ctrl", "Shift", "Space"} <= set(texts)
        assert not german_texts(texts)

    @pytest.mark.parametrize(
        "state", ["model_loading", "recording", "recording_prompt", "transcribing", "done", "error"]
    )
    def test_every_state_is_english(self, english, live_app, state):
        live_app.apply_state(state)
        assert not german_texts([live_app.status_label.cget("text"), live_app.main_button.cget("text")])

    def test_tray_menu_and_tooltip(self, english, live_app):
        texts = tray_texts(live_app)
        assert "Quit" in texts and "No transcripts yet" in texts
        assert not german_texts(texts)


class TestGerman:
    def test_settings_window_speaks_german(self, open_settings):
        texts = visible_texts(open_settings().win)
        assert {"Einstellungen", "Sprache", "Systemsprache", "Strg"} <= set(texts)

    def test_tray_menu(self, live_app):
        assert "Beenden" in tray_texts(live_app)


class TestLanguageRow:
    def test_offers_the_three_settings(self, open_settings):
        win = open_settings()
        assert [option.value for option in win.select_language.options] == ["auto", "de", "en"]
        assert win.select_language.value == CFG.ui_language

    def test_a_new_language_is_saved_and_asks_for_a_restart(self, open_settings, hams, monkeypatch):
        monkeypatch.setattr(CFG, "ui_language", "de")
        win = open_settings()
        assert win.row_language.sub_text == "Sprache der Oberfläche"
        win._select_language("en")
        assert ("ui", "language", "en") in hams.saved
        assert CFG.ui_language == "en"
        assert win.row_language.sub_text == "Gilt nach einem Neustart"
        assert i18n.language() == "de", "switches only after a restart"

    def test_back_to_the_running_language_drops_the_hint(self, open_settings, monkeypatch):
        monkeypatch.setattr(CFG, "ui_language", "de")
        win = open_settings()
        win._select_language("en")
        win._select_language("de")
        assert win.row_language.sub_text == "Sprache der Oberfläche"

    def test_the_same_choice_writes_nothing(self, open_settings, hams, monkeypatch):
        monkeypatch.setattr(CFG, "ui_language", "de")
        win = open_settings()
        win._select_language("de")
        assert not [s for s in hams.saved if s[1] == "language"]

    def test_auto_counts_as_the_system_language(self, monkeypatch):
        from hams.settings_window import SettingsWindow

        monkeypatch.setattr(i18n, "detect_system_language", lambda *a: "de")
        assert SettingsWindow.language_hint("auto") == "Sprache der Oberfläche"
        monkeypatch.setattr(i18n, "detect_system_language", lambda *a: "en")
        assert SettingsWindow.language_hint("auto") == "Gilt nach einem Neustart"


class TestDictationRow:
    def test_offers_auto_and_every_supported_language(self, open_settings):
        from hams import dictation_language

        win = open_settings()
        assert [option.value for option in win.select_dictation.options] == list(dictation_language.SETTINGS)
        assert win.select_dictation.value == CFG.transcription_language == "de"
        assert win.row_dictation.sub_text == "Die Sprache, die du sprichst"

    def test_german_labels(self, open_settings):
        texts = visible_texts(open_settings().win)
        assert {"Diktiersprache", "Automatisch", "Deutsch", "English", "Français"} <= set(texts)

    def test_english_labels(self, english, open_settings):
        texts = visible_texts(open_settings().win)
        assert {"Dictation language", "Automatic", "The language you speak", "Português"} <= set(texts)

    def test_a_new_language_is_saved_and_applies_without_restart(self, open_settings, hams):
        win = open_settings()
        win._select_dictation_language("en")
        assert ("transcription", "language", "en") in hams.saved
        assert CFG.transcription_language == "en"
        assert win.row_dictation.sub_text == "Die Sprache, die du sprichst", "no restart hint"

    def test_auto_says_it_detects(self, open_settings, hams):
        win = open_settings()
        win._select_dictation_language("auto")
        assert ("transcription", "language", "auto") in hams.saved
        assert win.row_dictation.sub_text == "Wird bei jeder Aufnahme erkannt"

    def test_the_same_or_an_unknown_choice_writes_nothing(self, open_settings, hams):
        win = open_settings()
        win._select_dictation_language("de")
        win._select_dictation_language("klingonisch")
        assert not [s for s in hams.saved if s[0] == "transcription"]
        assert CFG.transcription_language == "de"
