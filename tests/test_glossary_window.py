"""The glossary in the app: `glossary.GlossaryFile` (editing the TOML file) and the window built on it."""

from pathlib import Path

import kit
import pytest
from test_settings_window_live import disk, open_settings  # noqa: F401 - Fixtures

from hams import glossary
from hams.glossary import GlossaryFile, GlossaryUnreadable

TEMPLATE = glossary.DEFAULT_PATH


@pytest.fixture
def path(tmp_path, monkeypatch) -> Path:
    """A copy of the German template as the user's glossary."""
    file = tmp_path / "glossary.toml"
    file.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(glossary, "glossary_path", lambda: file)
    return file


class TestGlossaryFile:
    def test_reads_both_lists_in_file_order(self, path):
        entries = GlossaryFile(path)
        assert entries.words[:3] == ["Hams", "Whisper", "faster-whisper"]
        assert ("Olama", "Ollama") in entries.replacements

    def test_changes_keep_comments_order_and_any_letters(self, path):
        entries = GlossaryFile(path)
        entries.add_word("Hüseyin")
        entries.add_word("عبد الله")
        entries.change_word("Python", "Python 3")
        entries.remove_word("API")
        entries.set_replacement("Ner  way", "Nerway")
        entries.set_replacement("Kuda", "CUDA 12", old="Kuda")
        entries.set_replacement("Gid Hub", "GitHub", old="Git Hub")
        entries.remove_replacement("Olama")
        entries.save()
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# Glossar für Hams")
        assert "# Werkzeuge und Begriffe rund um Hams" in text
        again = GlossaryFile(path)
        assert again.words[-2:] == ["Hüseyin", "عبد الله"]
        assert again.words.index("Python 3") == entries.words.index("Python 3"), "an edit keeps the place"
        assert "API" not in again.words
        pairs = dict(again.replacements)
        assert pairs["Ner way"] == "Nerway" and pairs["Kuda"] == "CUDA 12" and pairs["Gid Hub"] == "GitHub"
        assert "Olama" not in pairs and "Git Hub" not in pairs
        assert glossary._parse(glossary.load_toml_file(path))[0] == tuple(again.words), "the dictation reads it"

    def test_duplicates_ignore_case(self, path):
        entries = GlossaryFile(path)
        assert entries.has_word("hams") and not entries.has_word("Hams", besides="Hams")
        assert entries.has_replacement("olama") and not entries.has_replacement("Olama", besides="Olama")

    def test_an_empty_file_gets_both_lists(self, tmp_path):
        file = tmp_path / "glossary.toml"
        file.write_text("", encoding="utf-8")
        entries = GlossaryFile(file)
        assert entries.words == [] and entries.replacements == []
        entries.add_word("Nerway")
        entries.set_replacement("Ner way", "Nerway")
        entries.change_word("missing", "Hams")
        entries.remove_word("missing")
        entries.remove_replacement("missing")
        entries.save()
        again = GlossaryFile(file)
        assert again.words == ["Nerway", "Hams"]
        assert again.replacements == [("Ner way", "Nerway")]

    def test_english_keys_count_too(self, tmp_path):
        file = tmp_path / "glossary.toml"
        file.write_text('words = ["A"]\nwoerter = ["B"]\n\n[replace]\n"x" = "y"\n', encoding="utf-8")
        entries = GlossaryFile(file)
        assert entries.words == ["B", "A"]
        entries.set_replacement("x", "z")
        assert entries.replacements == [("x", "z")]

    @pytest.mark.parametrize("content", [b"woerter = [", "words = ['x']".encode("utf-16")])
    def test_a_broken_file_is_refused(self, tmp_path, content):
        file = tmp_path / "glossary.toml"
        file.write_bytes(content)
        with pytest.raises(GlossaryUnreadable):
            GlossaryFile(file)

    def test_the_default_path_creates_the_file(self, tmp_path, monkeypatch):
        file = tmp_path / "glossary.toml"
        monkeypatch.setattr(glossary, "glossary_path", lambda: file)
        assert GlossaryFile().words == GlossaryFile(TEMPLATE).words


@pytest.fixture
def window(live_app, path, monkeypatch):
    opened = []
    monkeypatch.setattr(glossary, "open_in_editor", lambda: opened.append(True))
    live_app.open_glossary()
    win = live_app.glossary_win
    win.opened = opened
    live_app.root.update()
    yield win
    win.close()


def rows(frame) -> list:
    return [child for child in frame.winfo_children() if isinstance(child, kit.SettingRow)]


def titles(frame) -> list[str]:
    return [row._title_text for row in rows(frame)]


class TestWindow:
    def test_opens_from_the_settings_once(self, open_settings, live_app, path):  # noqa: F811
        general = open_settings().general
        general.glossary_button._invoke()
        first = live_app.glossary_win
        general.edit_glossary()
        assert live_app.glossary_win is first
        assert first.win.title() == "Hams — Glossar"
        assert titles(first.word_list)[:2] == ["Hams", "Whisper"]
        assert "Olama → Ollama" in titles(first.replace_list)
        first.close()
        assert not first.alive()

    def test_add_a_word_in_any_script(self, window, path):
        for word in ("Hüseyin", "عبد الله"):
            window.word_field.set(word)
            window.submit_word()
        assert titles(window.word_list)[-2:] == ["Hüseyin", "عبد الله"]
        assert window.status.cget("text") == "Gespeichert · gilt ab dem nächsten Diktat"
        assert "عبد الله" in glossary.words(), "the next dictation uses it"
        assert window.word_field.value == ""

    def test_a_duplicate_is_named(self, window):
        window.word_field.set("hams")
        window.submit_word()
        assert window.status.cget("text") == "„hams“ steht schon im Glossar"
        window.word_field.set("   ")
        window.submit_word()  # empty: nothing happens

    def test_edit_and_delete_a_word(self, window):
        row = rows(window.word_list)[1]
        row._on_click()
        assert window.editing_word == "Whisper"
        assert window.word_button.cget("text") == "Speichern"
        window.word_field.set("Whisper Turbo")
        window.submit_word()
        assert titles(window.word_list)[1] == "Whisper Turbo"
        assert window.word_button.cget("text") == "Hinzufügen"
        window._edit_word("Hams")
        window.cancel_word()
        assert window.editing_word is None
        window._edit_word("Hams")
        window._delete_word("Hams")
        assert "Hams" not in titles(window.word_list) and window.editing_word is None

    def test_corrections(self, window):
        window.wrong_field.set("Ner way")
        window.right_field.set("Nerway")
        window.submit_replacement()
        assert "Ner way → Nerway" in titles(window.replace_list)
        window._edit_replacement("Ner way")
        assert window.right_field.value == "Nerway"
        window.right_field.set("Nerway!")
        window.submit_replacement()
        assert "Ner way → Nerway!" in titles(window.replace_list)
        window.wrong_field.set("olama")
        window.right_field.set("x")
        window.submit_replacement()
        assert window.status.cget("text") == "„olama“ steht schon im Glossar"
        window.cancel_replacement()
        window._edit_replacement("Kuda")
        window._delete_replacement("Kuda")
        assert not any(title.startswith("Kuda") for title in titles(window.replace_list))
        assert window.editing_replacement is None

    def test_search_narrows_both_lists(self, window):
        assert window.search_box.winfo_manager() == "pack", "the template has more than a dozen entries"
        window.search_field.set("ollama")
        window._render()
        assert titles(window.word_list) == ["Ollama"]
        assert titles(window.replace_list) == ["Olama → Ollama", "Ola Ma → Ollama"]
        window.search_field.set("zzz")
        window._render()
        assert titles(window.word_list) == ["Nichts gefunden"]

    def test_short_lists_have_no_search(self, live_app, tmp_path, monkeypatch):
        file = tmp_path / "glossary.toml"
        file.write_text("", encoding="utf-8")
        monkeypatch.setattr(glossary, "glossary_path", lambda: file)
        live_app.open_glossary()
        win = live_app.glossary_win
        assert not win.search_box.winfo_manager()
        assert titles(win.word_list) == ["Noch keine Wörter"]
        assert titles(win.replace_list) == ["Noch keine Korrekturen"]
        win.close()

    def test_a_broken_file_is_shown_not_written(self, live_app, tmp_path, monkeypatch):
        file = tmp_path / "glossary.toml"
        file.write_text("woerter = [", encoding="utf-8")
        monkeypatch.setattr(glossary, "glossary_path", lambda: file)
        live_app.open_glossary()
        win = live_app.glossary_win
        assert win.status.cget("text").startswith("Die Datei ist fehlerhaft")
        win.submit_word()
        win.submit_replacement()
        win._delete_word("x")
        win._delete_replacement("x")
        assert file.read_text(encoding="utf-8") == "woerter = ["
        win.close()

    def test_a_failed_save_is_said(self, window, monkeypatch):
        def broken():
            raise OSError("read-only")

        monkeypatch.setattr(window.file, "save", broken)
        window.word_field.set("Nerway")
        window.submit_word()
        assert window.status.cget("text").startswith("Ließ sich nicht speichern")

    def test_changes_from_an_editor_show_up(self, window, path, monkeypatch):
        window.open_as_file()
        assert window.opened == [True]
        assert window.status.cget("text").startswith("Im Editor geöffnet")
        path.write_text('woerter = ["Neu"]\n', encoding="utf-8")
        monkeypatch.setattr(window, "_file_stamp", lambda: "changed")
        window._on_focus(type("Event", (), {"widget": window.win})())
        assert titles(window.word_list) == ["Neu"]

    def test_no_editor(self, window, monkeypatch, path):
        def fail():
            raise OSError("kein Programm")

        window._open_file = fail
        window.open_as_file()
        assert str(path) in window.status.cget("text")

    def test_text_field_placeholder(self, window):
        field = window.word_field
        # The window puts the focus into this field when it opens, which clears the placeholder wherever a window
        # manager delivers that FocusIn (xvfb does, the hidden test desktop on Windows does not). Leaving the field
        # brings it back; start from there so the test does not depend on the desktop.
        field._show_placeholder()
        assert field.value == "" and field.entry.get() == "Neues Wort"
        field._focus_in()
        assert field.entry.get() == ""
        field.set("x")
        field._on_theme_change()
        assert field.value == "x"
        field.set("")
        field._show_placeholder()
        field._on_theme_change()
        assert field.value == ""
