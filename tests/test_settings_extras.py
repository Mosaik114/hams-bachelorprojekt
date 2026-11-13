"""Einstellungen: fehlendes Ollama-Modell per Knopf laden, Glossar im Editor öffnen."""

from types import SimpleNamespace

import pytest

from hams import glossary
from hams import ollama as ollama_api
from hams import settings_extras as extras
from hams import setup_wizard as sw
from hams.config import CFG
from hams.setup_wizard import Task


class InlineThread:
    def __init__(self, target, args=(), **_options):
        self.target, self.args = target, args

    def start(self) -> None:
        self.target(*self.args)


def online(*names):
    return ollama_api.OllamaState(True, models=[ollama_api.OllamaModel(name) for name in names])


class TestLogic:
    def test_the_row_appears_only_for_a_missing_model_on_a_running_service(self):
        task = Task()
        assert extras.pull_row_needed(online("gemma3:4b"), "qwen3:8b", task)
        assert not extras.pull_row_needed(online("qwen3:8b"), "qwen3:8b", task)
        assert not extras.pull_row_needed(ollama_api.OllamaState(False), "qwen3:8b", task)
        assert not extras.pull_row_needed(online(), "", task)

    @pytest.mark.parametrize("state", [sw.RUNNING, sw.FAILED, sw.CANCELLED])
    def test_a_started_download_stays_visible(self, state):
        task = Task()
        task.state = state
        assert extras.pull_row_needed(ollama_api.OllamaState(False), "qwen3:8b", task)

    def test_the_view_names_the_model(self):
        view = extras.pull_view(Task(), "qwen3:8b")
        assert view.action == "start"
        assert ollama_api.display_name("qwen3:8b") in view.text


@pytest.fixture
def settings(live_app, hams, monkeypatch):
    inline = SimpleNamespace(Thread=InlineThread)
    monkeypatch.setattr(extras, "threading", inline)
    monkeypatch.setattr(sw, "TaskThread", InlineThread)
    CFG.ollama_model_cleanup = "qwen3:8b"

    def _open():
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win

    return _open


def shown(win) -> bool:
    return win.ai.pull_row.row.winfo_manager() == "pack"


class TestPullRow:
    def test_hidden_while_ollama_is_off(self, settings):
        win = settings()
        assert not shown(win)

    def test_hidden_when_the_model_is_installed(self, settings, hams):
        hams.probe = online("qwen3:8b")
        win = settings()
        assert not shown(win)

    def test_a_missing_model_is_downloaded_with_progress(self, settings, hams, live_app, monkeypatch):
        hams.probe = online("gemma3:4b")
        calls = []

        def pull(client, name, progress, cancelled):
            calls.append(name)
            progress(50, 100)
            hams.probe = online("gemma3:4b", name)

        monkeypatch.setattr(ollama_api.OllamaClient, "pull", pull)
        win = settings()
        assert shown(win)
        assert win.ai.pull_row.row._control is win.ai.pull_row.button
        win.ai.pull_row.button._invoke()
        live_app.root.update()
        assert calls == ["qwen3:8b"]
        assert win.ai.pull_task.state == sw.DONE
        assert not shown(win), "nach der neuen Abfrage ist das Modell da, die Zeile geht"

    def test_a_failed_download_offers_retry(self, settings, hams, live_app, monkeypatch):
        hams.probe = online()

        def pull(client, name, progress, cancelled):
            raise ollama_api.PullError("kaputt")

        monkeypatch.setattr(ollama_api.OllamaClient, "pull", pull)
        win = settings()
        win.ai.start_model_pull()
        live_app.root.update()
        assert win.ai.pull_task.state == sw.FAILED
        assert shown(win)
        assert win.ai.pull_row.row._sub_text == "kaputt"
        assert win.ai.pull_row.button.cget("text") == "Erneut versuchen"

    def test_closing_the_window_cancels_a_running_download(self, settings, hams, live_app):
        hams.probe = online()
        win = settings()
        ai = win.ai
        ai.pull_task.begin()
        ai.start_model_pull()  # läuft schon: nichts Neues
        win.close()
        assert ai.pull_task.cancel_event.is_set()
        # Versteckt, nicht zerstört: dasselbe Fenster, keine Ollama-Abfrage mehr geplant.
        assert not win.alive() and win.reusable()
        assert live_app.settings_win is win
        assert not ai.pending("ollama")
        ai.refresh_pull_row()  # versteckt: nichts passiert
        ai._post_pull(lambda: None)
        live_app.root.update()  # das Nachspiel des Laufs prallt am versteckten Fenster ab


class TestGlossaryButton:
    def test_opens_the_file(self, settings, monkeypatch):
        opened = []
        monkeypatch.setattr(glossary, "open_in_editor", lambda: opened.append(True))
        win = settings()
        win.general.glossary_button._invoke()
        assert opened == [True]
        assert "nächste Diktat" in win.general.row_glossary._sub_text

    def test_names_the_file_when_no_editor_opens(self, settings, monkeypatch, tmp_path):
        def fail():
            raise OSError("kein Programm")

        monkeypatch.setattr(glossary, "open_in_editor", fail)
        monkeypatch.setattr(glossary, "glossary_path", lambda: tmp_path / "glossary.toml")
        win = settings()
        win.general.edit_glossary()
        assert str(tmp_path / "glossary.toml") in win.general.row_glossary._sub_text
