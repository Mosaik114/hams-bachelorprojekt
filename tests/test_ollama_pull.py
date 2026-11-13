"""`OllamaClient.pull`: der einzige Weg, auf dem Hams ein Ollama-Modell lädt (aus dem Einrichtungsassistenten).

Geprüft an einer Ersatzantwort im Format von `/api/pull`; es wird nie eine Verbindung aufgebaut.
"""

import json
import threading
import urllib.error
from types import SimpleNamespace

import pytest
from setup_fakes import InlineThread

from hams import ollama
from hams import settings_extras as extras


class Response:
    def __init__(self, lines):
        self.lines = [line if isinstance(line, bytes) else json.dumps(line).encode() for line in lines]
        self.closed = False

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


def opener_for(lines, seen=None):
    def opener(request, timeout=None):
        if seen is not None:
            seen.append((request.full_url, json.loads(request.data), timeout))
        return Response(lines)

    return opener


STREAM = [
    {"status": "pulling manifest"},
    {"status": "pulling a1", "digest": "a1", "total": 600, "completed": 0},
    {"status": "pulling a1", "digest": "a1", "total": 600, "completed": 300},
    {"status": "pulling b2", "digest": "b2", "total": 400, "completed": 400},
    {"status": "pulling a1", "digest": "a1", "total": 600, "completed": 600},
    {"status": "verifying sha256 digest"},
    {"status": "success"},
]


class TestPull:
    def test_progress_adds_up_all_layers(self):
        seen, progress = [], []
        ollama.OllamaClient("http://127.0.0.1:11434", opener=opener_for(STREAM, seen)).pull(
            "qwen3.5:9b", lambda d, t: progress.append((d, t)), lambda: False
        )
        url, body, timeout = seen[0]
        assert url == "http://127.0.0.1:11434/api/pull"
        assert body == {"model": "qwen3.5:9b", "stream": True}
        assert timeout == ollama.PULL_TIMEOUT
        assert progress == [(0, 600), (300, 600), (700, 1000), (1000, 1000)]

    def test_blank_and_broken_lines_are_skipped(self):
        lines = [b"", b"kein json", b"[1, 2]", {"status": "success"}]
        ollama.OllamaClient("http://x", opener=opener_for(lines)).pull("m", lambda d, t: None, lambda: False)

    def test_error_line_becomes_a_sentence(self, english):
        lines = [{"error": "pull model manifest: file does not exist"}]
        with pytest.raises(ollama.PullError, match="couldn't download the model: pull model manifest"):
            ollama.OllamaClient("http://x", opener=opener_for(lines)).pull("m", lambda d, t: None, lambda: False)

    def test_stream_without_success_is_incomplete(self, english):
        with pytest.raises(ollama.PullError, match="stopped before"):
            ollama.OllamaClient("http://x", opener=opener_for(STREAM[:3])).pull("m", lambda d, t: None, lambda: False)

    def test_cancel_stops_before_the_next_line(self):
        asked = []

        def cancel():
            asked.append(1)
            return len(asked) > 2

        progress = []
        with pytest.raises(ollama.PullCancelled):
            ollama.OllamaClient("http://x", opener=opener_for(STREAM)).pull(
                "m", lambda d, t: progress.append(d), cancel
            )
        assert progress == [0]

    def test_unreachable_service(self, english):
        def opener(request, timeout=None):
            raise urllib.error.URLError("refused")

        with pytest.raises(ollama.PullError, match="Ollama isn't reachable"):
            ollama.OllamaClient("http://x", opener=opener).pull("m", lambda d, t: None, lambda: False)

    def test_cancelled_is_a_pull_error(self):
        assert issubclass(ollama.PullCancelled, ollama.PullError)


class TestTheOllamaPullReportsThroughTheDispatcher:
    def test_post_pull_never_calls_tk_from_the_worker(self, live_app, monkeypatch):
        # Die Ollama-Abfrage beim Öffnen läuft inline: ihr Ergebnis darf nicht später in `queued` landen.
        monkeypatch.setattr(extras, "threading", SimpleNamespace(Thread=InlineThread))
        live_app._open_settings()
        live_app.root.update()
        win = live_app.settings_win
        ai = win.ai
        queued = []
        monkeypatch.setattr(live_app, "ui_call", queued.append)
        touched = []
        win.win.after = lambda *args: touched.append(args)
        try:
            worker = threading.Thread(target=ai._post_pull, args=(lambda: None,), name="pull")
            worker.start()
            worker.join(2)
        finally:
            del win.win.after
        assert touched == [], "Tk called from the pull thread"
        assert len(queued) == 1
        queued[0]()  # on the Tk thread: runs the callback and refreshes the row
