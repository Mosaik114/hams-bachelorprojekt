"""Regressions from the second review: dictation pipeline, Ollama failures, glossary, transcript files.

Each class pins one finding: the fix must hold, or a dictation gets lost, mangled or overwritten.
"""

import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from hams import glossary
from hams import transcription as transcription_mod
from hams.app import FloatingTranscriberApp as App
from hams.config import CFG

AUDIO = np.full(1600, 0.2, dtype=np.float32)


@pytest.fixture
def app(monkeypatch, tmp_path):
    """The app without window: every UI call runs at once and is recorded, pasting is recorded too."""
    instance = App.__new__(App)
    instance.model_ready = threading.Event()
    instance.model_ready.set()
    instance._transcribe_lock = threading.Lock()
    instance._model_lock = threading.Lock()
    instance.model = object()
    instance.is_recording = False
    instance._prompt_mode = False
    instance.states = []
    instance.pasted = []
    instance._ui_call = lambda fn: fn()
    instance._apply_state = lambda state, detail=None, hint=None, **_: instance.states.append((state, detail))
    instance._add_to_history = lambda text: None
    instance._copy_and_paste = instance.pasted.append
    instance._save_transcript_file = lambda text: None
    empty = tmp_path / "glossary.toml"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr(glossary, "glossary_path", lambda: empty)
    monkeypatch.setitem(glossary._cache, "path", None)
    return instance


def wait_for_pipeline(timeout=5.0) -> None:
    end = time.monotonic() + timeout
    while any(t.name == "transcribe" and t.is_alive() for t in threading.enumerate()):
        assert time.monotonic() < end, "the pipeline did not finish"
        time.sleep(0.01)


def names(states) -> list:
    return [state for state, _detail in states]


class TestAnOlderRunNeverClobbersANewerOne:
    """Finding 1: the old worker reset `_prompt_mode` and showed "done" over a recording started meanwhile."""

    def test_a_prompt_recording_started_meanwhile_keeps_its_mode_and_state(self, app):
        gate = threading.Event()
        app._run_transcription = lambda model, audio: ("hallo welt", "de")

        def slow_cleanup(text, prompt_mode, language=""):
            gate.wait(5)  # Ollama takes a while
            return text

        app._nachbearbeiten = slow_cleanup
        run_a = app._next_pipeline_run()
        worker = threading.Thread(
            target=app._transcribe_audio, args=(AUDIO,), kwargs={"prompt_mode": False, "run_id": run_a}
        )
        worker.start()
        time.sleep(0.05)
        app.is_recording, app._prompt_mode = True, True  # the user presses the prompt hotkey
        gate.set()
        worker.join(5)
        assert app._prompt_mode is True
        assert "done" not in names(app.states), "the old run drew over the running recording"
        assert app.pasted == ["hallo welt"], "the old dictation is still delivered"

    def test_a_newer_run_owns_the_window_even_after_its_recording(self, app):
        app._run_transcription = lambda model, audio: ("alt", "de")
        app._nachbearbeiten = lambda text, prompt_mode, language="": text
        run_a = app._next_pipeline_run()
        app._next_pipeline_run()  # the next recording was stopped and waits in the queue
        app._transcribe_audio(AUDIO, prompt_mode=False, run_id=run_a)
        assert app.pasted == ["alt"]
        assert app.states == []

    def test_the_current_run_shows_its_result(self, app):
        app._run_transcription = lambda model, audio: ("neu", "de")
        app._nachbearbeiten = lambda text, prompt_mode, language="": text
        app._transcribe_audio(AUDIO, prompt_mode=False, run_id=app._next_pipeline_run())
        assert names(app.states) == ["done"]


class TestQueuedRecordings:
    """A recording finished while the previous one is still in the pipeline waits its turn; none is lost."""

    def test_both_results_arrive_in_order_with_their_own_mode(self, app):
        gate = threading.Event()
        modes = []
        app._run_transcription = lambda model, audio: (f"text {int(audio[0])}", "de")

        def post(text, prompt_mode, language=""):
            modes.append(prompt_mode)
            if text == "text 1":
                gate.wait(5)
            return text

        app._nachbearbeiten = post
        app._enqueue_transcription(np.full(10, 1.0), False, app._next_pipeline_run())
        time.sleep(0.05)
        app._enqueue_transcription(np.full(10, 2.0), True, app._next_pipeline_run())
        gate.set()
        wait_for_pipeline()
        assert app.pasted == ["text 1", "text 2"]
        assert modes == [False, True]
        assert names(app.states) == ["done"], "only the newest run reports"

    def test_a_crashing_job_does_not_stop_the_next(self, app):
        calls = []

        def transcribe(audio, prompt_mode=False, run_id=None):
            calls.append(int(audio[0]))
            if len(calls) == 1:
                raise RuntimeError("kaputt")

        app._transcribe_audio = transcribe
        app._pipeline_busy = True  # hold the worker back until both jobs are queued
        app._enqueue_transcription(np.full(10, 1.0), False, 1)
        app._enqueue_transcription(np.full(10, 2.0), False, 2)
        app._drain_pipeline()
        assert calls == [1, 2]
        assert app._pipeline_busy is False

    def test_stopping_hands_mode_and_run_to_the_queue(self, live_app, hams, monkeypatch):
        received = []
        monkeypatch.setattr(live_app, "_transcribe_audio", lambda audio, **job: received.append(job))
        live_app.toggle_recording_generate()
        hams.streams[-1].feed(np.full(800, 0.2))
        live_app.toggle_recording()
        wait_for_pipeline()
        assert received == [{"prompt_mode": True, "run_id": live_app._pipeline_latest}]
        assert live_app._prompt_mode is False


class _DroppingOllama:
    """A socket server that reads the request and answers with `reply` (b"" = just hang up)."""

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        self.server.settimeout(5)
        threading.Thread(target=self._serve, daemon=True, name="fake-ollama").start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.getsockname()[1]}/api/generate"

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                if self.reply:
                    conn.sendall(self.reply)

    def close(self) -> None:
        self.server.close()


REPLIES = {
    "hang-up": b"",  # RemoteDisconnected
    "cut-off": b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{"resp',  # IncompleteRead
    "no-text": b'HTTP/1.1 200 OK\r\nContent-Length: 18\r\n\r\n{"response": null}',
    "a-list": b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n[1]",
    "server-error": b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n",
}


@pytest.fixture(params=sorted(REPLIES))
def broken_ollama(request, monkeypatch):
    server = _DroppingOllama(REPLIES[request.param])
    monkeypatch.setattr(CFG, "ollama_url", server.url)
    monkeypatch.setattr(CFG, "ollama_cleanup_enabled", True)
    monkeypatch.setattr(CFG, "ollama_timeout_cleanup", 5)
    monkeypatch.setattr(CFG, "ollama_timeout_prompt", 5)
    monkeypatch.setattr(CFG, "ollama_stream_prompt", False)
    yield server
    server.close()


class TestOllamaFailsInAnyWay:
    """Finding 2: a connection dropped mid-request escaped the fallback, and the dictation was lost."""

    def test_the_raw_text_is_pasted_with_the_offline_note(self, app, broken_ollama):
        app._run_transcription = lambda model, audio: ("das ist mein diktat", "de")
        app._transcribe_audio(AUDIO)
        assert app.pasted == ["das ist mein diktat"]
        assert app.states[-1] == ("done", "Eingefügt · ohne Bereinigung")

    def test_the_prompt_mode_says_the_ai_failed(self, app, broken_ollama):
        app._run_transcription = lambda model, audio: ("was ist die hauptstadt", "de")
        app._transcribe_audio(AUDIO, prompt_mode=True)
        assert app.pasted == []
        assert app.states[-1] == ("error", "KI-Antwort fehlgeschlagen")


class TestGlossaryAppliesOnce:
    """Finding 3: the replacements ran on the raw text and again on the cleanup's answer."""

    @pytest.fixture
    def hams_app(self, app, tmp_path, monkeypatch):
        (tmp_path / "glossary.toml").write_text('[replace]\n"hams" = "Hams App"\n', encoding="utf-8")
        monkeypatch.setitem(glossary._cache, "path", None)
        monkeypatch.setattr(glossary, "cleanup_hint", lambda _language: "")
        monkeypatch.setattr(CFG, "ollama_cleanup_enabled", True)
        return app

    def test_whisper_hands_on_the_raw_text(self, hams_app, monkeypatch):
        model = SimpleNamespace(transcribe=lambda audio, **_: ([SimpleNamespace(text=" ich nutze hams ")], None))
        monkeypatch.setattr(CFG, "transcription_language", "de")
        assert hams_app._run_transcription(model, AUDIO) == ("ich nutze hams", "de")

    @pytest.mark.parametrize(
        ("answer", "pasted"),
        [("ich nutze hams", "ich nutze Hams App"), ("Ich nutze Hams App.", "Ich nutze Hams App.")],
    )
    def test_the_cleaned_text_gets_them_once(self, hams_app, monkeypatch, answer, pasted):
        sent = []
        monkeypatch.setattr(hams_app, "_ollama_generate", lambda **request: sent.append(request) or answer)
        hams_app._run_transcription = lambda model, audio: ("ich nutze hams", "de")
        hams_app._transcribe_audio(AUDIO)
        assert "ich nutze hams" in sent[0]["prompt"], "the cleanup gets the raw text"
        assert hams_app.pasted == [pasted]

    def test_without_cleanup_the_raw_text_gets_them(self, hams_app, monkeypatch):
        monkeypatch.setattr(CFG, "ollama_cleanup_enabled", False)
        hams_app._run_transcription = lambda model, audio: ("ich nutze hams", "de")
        hams_app._transcribe_audio(AUDIO)
        assert hams_app.pasted == ["ich nutze Hams App"]

    def test_the_prompt_gets_them_before_it_goes_to_the_ai(self, hams_app, monkeypatch):
        sent = []
        monkeypatch.setattr(hams_app, "_ollama_generate", lambda **request: sent.append(request) or "Antwort")
        hams_app._run_transcription = lambda model, audio: ("frag hams", "de")
        hams_app._transcribe_audio(AUDIO, prompt_mode=True)
        assert sent[0]["prompt"] == "frag Hams App"
        assert hams_app.pasted == ["Antwort"]


class TestTranscriptFiles:
    """Finding 6: two dictations in the same second overwrote each other's file."""

    def test_the_same_second_gets_a_second_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(transcription_mod, "OUTPUT_DIR", tmp_path)

        class Frozen:
            @staticmethod
            def now():
                from datetime import datetime

                return datetime(2026, 11, 13, 9, 30, 0)

        monkeypatch.setattr(transcription_mod, "datetime", Frozen)
        app = App.__new__(App)
        for text in ("eins", "zwei", "drei"):
            app._save_transcript_file(text)
        files = sorted(tmp_path.glob("*.txt"), key=lambda p: p.stat().st_mtime_ns)
        assert [f.name for f in files] == [
            "2026-11-13_09-30-00.txt",
            "2026-11-13_09-30-00_2.txt",
            "2026-11-13_09-30-00_3.txt",
        ]
        assert [f.read_text(encoding="utf-8") for f in files] == ["eins", "zwei", "drei"]

    def test_no_free_name_is_reported_not_overwritten(self, tmp_path, monkeypatch):
        monkeypatch.setattr(transcription_mod, "OUTPUT_DIR", tmp_path)
        (tmp_path / "stamp.txt").write_text("alt", encoding="utf-8")
        with pytest.raises(FileExistsError):
            App._create_transcript_file("stamp", "neu", attempts=1)
        assert Path(tmp_path / "stamp.txt").read_text(encoding="utf-8") == "alt"

    def test_the_paste_function_has_one_docstring(self):
        import inspect

        source = inspect.getsource(App._copy_and_paste)
        assert source.count('"""') == 2
