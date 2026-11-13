"""Regressions from the second review: dictation pipeline, Ollama failures, glossary, transcript files.

Each class pins one finding: the fix must hold, or a dictation gets lost, mangled or overwritten.
"""

import logging
import socket
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from live_app import FakeOllama, make_pipeline

from hams import glossary
from hams import transcription as transcription_mod
from hams.config import CFG
from hams.ollama import OllamaClient

AUDIO = np.full(1600, 0.2, dtype=np.float32)


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """A real pipeline with a loaded fake model; states and pasted texts are recorded (tests/live_app.py)."""
    instance = make_pipeline(ollama=OllamaClient.for_url(lambda: CFG.ollama_url))
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
    return [state for state, *_ in states]


class TestAnOlderRunNeverClobbersANewerOne:
    """Finding 1: the old worker reset `_prompt_mode` and showed "done" over a recording started meanwhile."""

    def test_a_prompt_recording_started_meanwhile_keeps_its_mode_and_state(self, pipeline):
        gate = threading.Event()
        pipeline.run_whisper = lambda model, audio: ("hallo welt", "de")

        def slow_cleanup(text, prompt_mode, language=""):
            gate.wait(5)  # Ollama takes a while
            return text

        pipeline.post_process = slow_cleanup
        run_a = pipeline.next_run()
        worker = threading.Thread(
            target=pipeline.transcribe, args=(AUDIO,), kwargs={"prompt_mode": False, "run_id": run_a}
        )
        worker.start()
        time.sleep(0.05)
        pipeline.sink.is_recording = True  # the user presses the prompt hotkey
        gate.set()
        worker.join(5)
        assert "done" not in names(pipeline.states), "the old run drew over the running recording"
        assert pipeline.pasted == ["hallo welt"], "the old dictation is still delivered"

    def test_a_newer_run_owns_the_window_even_after_its_recording(self, pipeline):
        pipeline.run_whisper = lambda model, audio: ("alt", "de")
        pipeline.post_process = lambda text, prompt_mode, language="": text
        run_a = pipeline.next_run()
        pipeline.next_run()  # the next recording was stopped and waits in the queue
        pipeline.transcribe(AUDIO, prompt_mode=False, run_id=run_a)
        assert pipeline.pasted == ["alt"]
        assert pipeline.states == []

    def test_the_current_run_shows_its_result(self, pipeline):
        pipeline.run_whisper = lambda model, audio: ("neu", "de")
        pipeline.post_process = lambda text, prompt_mode, language="": text
        pipeline.transcribe(AUDIO, prompt_mode=False, run_id=pipeline.next_run())
        assert names(pipeline.states) == ["done"]


class TestQueuedRecordings:
    """A recording finished while the previous one is still in the pipeline waits its turn; none is lost."""

    def test_both_results_arrive_in_order_with_their_own_mode(self, pipeline):
        gate = threading.Event()
        modes = []
        pipeline.run_whisper = lambda model, audio: (f"text {int(audio[0])}", "de")

        def post(text, prompt_mode, language=""):
            modes.append(prompt_mode)
            if text == "text 1":
                gate.wait(5)
            return text

        pipeline.post_process = post
        pipeline.enqueue(np.full(10, 1.0), False, pipeline.next_run())
        time.sleep(0.05)
        pipeline.enqueue(np.full(10, 2.0), True, pipeline.next_run())
        gate.set()
        wait_for_pipeline()
        assert pipeline.pasted == ["text 1", "text 2"]
        assert modes == [False, True]
        assert names(pipeline.states) == ["done"], "only the newest run reports"

    def test_a_crashing_job_does_not_stop_the_next(self, pipeline):
        calls = []

        def transcribe(audio, prompt_mode=False, run_id=None):
            calls.append(int(audio[0]))
            if len(calls) == 1:
                raise RuntimeError("kaputt")

        pipeline.transcribe = transcribe
        pipeline._busy = True  # hold the worker back until both jobs are queued
        pipeline.enqueue(np.full(10, 1.0), False, 1)
        pipeline.enqueue(np.full(10, 2.0), False, 2)
        pipeline._drain()
        assert calls == [1, 2]
        assert pipeline._busy is False

    def test_stopping_hands_mode_and_run_to_the_queue(self, live_app, hams, monkeypatch):
        received = []
        monkeypatch.setattr(live_app.pipeline, "transcribe", lambda audio, **job: received.append(job))
        live_app.toggle_recording_generate()
        hams.streams[-1].feed(np.full(800, 0.2))
        live_app.toggle_recording()
        wait_for_pipeline()
        assert received == [{"prompt_mode": True, "run_id": live_app.pipeline.latest_run}]
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
    yield server
    server.close()


class TestOllamaFailsInAnyWay:
    """Finding 2: a connection dropped mid-request escaped the fallback, and the dictation was lost."""

    def test_the_raw_text_is_pasted_with_the_offline_note(self, pipeline, broken_ollama):
        pipeline.run_whisper = lambda model, audio: ("das ist mein diktat", "de")
        pipeline.transcribe(AUDIO)
        assert pipeline.pasted == ["das ist mein diktat"]
        assert pipeline.states[-1][:2] == ("done", "Eingefügt · ohne Bereinigung")

    def test_the_prompt_mode_says_the_ai_failed(self, pipeline, broken_ollama):
        pipeline.run_whisper = lambda model, audio: ("was ist die hauptstadt", "de")
        pipeline.transcribe(AUDIO, prompt_mode=True)
        assert pipeline.pasted == []
        assert pipeline.states[-1][:2] == ("error", "KI-Antwort fehlgeschlagen")


class TestGlossaryAppliesOnce:
    """Finding 3: the replacements ran on the raw text and again on the cleanup's answer."""

    @pytest.fixture
    def glossary_pipeline(self, pipeline, tmp_path, monkeypatch):
        (tmp_path / "glossary.toml").write_text('[replace]\n"hams" = "Hams App"\n', encoding="utf-8")
        monkeypatch.setitem(glossary._cache, "path", None)
        monkeypatch.setattr(glossary, "cleanup_hint", lambda _language: "")
        monkeypatch.setattr(CFG, "ollama_cleanup_enabled", True)
        return pipeline

    def test_whisper_hands_on_the_raw_text(self, glossary_pipeline, monkeypatch):
        model = SimpleNamespace(transcribe=lambda audio, **_: ([SimpleNamespace(text=" ich nutze hams ")], None))
        monkeypatch.setattr(CFG, "transcription_language", "de")
        assert glossary_pipeline.run_whisper(model, AUDIO) == ("ich nutze hams", "de")

    @pytest.mark.parametrize(
        ("answer", "pasted"),
        [("ich nutze hams", "ich nutze Hams App"), ("Ich nutze Hams App.", "Ich nutze Hams App.")],
    )
    def test_the_cleaned_text_gets_them_once(self, glossary_pipeline, monkeypatch, answer, pasted):
        glossary_pipeline.ollama = FakeOllama(answer)
        sent = glossary_pipeline.ollama.requests
        glossary_pipeline.run_whisper = lambda model, audio: ("ich nutze hams", "de")
        glossary_pipeline.transcribe(AUDIO)
        assert "ich nutze hams" in sent[0]["prompt"], "the cleanup gets the raw text"
        assert glossary_pipeline.pasted == [pasted]

    def test_without_cleanup_the_raw_text_gets_them(self, glossary_pipeline, monkeypatch):
        monkeypatch.setattr(CFG, "ollama_cleanup_enabled", False)
        glossary_pipeline.run_whisper = lambda model, audio: ("ich nutze hams", "de")
        glossary_pipeline.transcribe(AUDIO)
        assert glossary_pipeline.pasted == ["ich nutze Hams App"]

    def test_the_prompt_gets_them_before_it_goes_to_the_ai(self, glossary_pipeline, monkeypatch):
        glossary_pipeline.ollama = FakeOllama("Antwort")
        sent = glossary_pipeline.ollama.requests
        glossary_pipeline.run_whisper = lambda model, audio: ("frag hams", "de")
        glossary_pipeline.transcribe(AUDIO, prompt_mode=True)
        assert sent[0]["prompt"] == "frag Hams App"
        assert glossary_pipeline.pasted == ["Antwort"]


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
        for text in ("eins", "zwei", "drei"):
            transcription_mod.save_transcript_file(text)
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
            transcription_mod.create_transcript_file("stamp", "neu", attempts=1)
        assert Path(tmp_path / "stamp.txt").read_text(encoding="utf-8") == "alt"


@pytest.fixture
def plain_pipeline(monkeypatch):
    """A real pipeline without the cleanup, talking to whatever `urlopen` answers."""
    monkeypatch.setattr(CFG, "ollama_cleanup_enabled", False)
    return make_pipeline(ollama=OllamaClient.for_url(lambda: CFG.ollama_url))


GLOSSARY = 'words = ["Müller"]\n[replace]\n"mueller" = "Müller"\n'


class TestGlossaryEncodings:
    @pytest.fixture
    def path(self, tmp_path, monkeypatch):
        target = tmp_path / "glossary.toml"
        monkeypatch.setattr(glossary, "glossary_path", lambda: target)
        monkeypatch.setitem(glossary._cache, "path", None)
        return target

    @pytest.mark.parametrize("encoding", ["cp1252", "utf-16"])
    def test_a_file_in_another_encoding_never_breaks_a_dictation(self, plain_pipeline, path, encoding, caplog):
        path.write_bytes(GLOSSARY.encode(encoding))
        plain_pipeline.run_whisper = lambda model, audio: ("frau mueller", "de")
        with caplog.at_level(logging.WARNING, logger="hams"):
            plain_pipeline.transcribe(AUDIO)
            plain_pipeline.transcribe(AUDIO)
        assert plain_pipeline.pasted == ["frau mueller", "frau mueller"], "an empty glossary, the text still comes"
        warnings = [r for r in caplog.records if "Glossary" in r.getMessage()]
        assert len(warnings) == 1, "cached for this file version: one warning, not one per dictation"
        assert glossary.hotwords("de") is None

    def test_a_utf8_file_with_byte_order_mark_counts(self, plain_pipeline, path):
        path.write_bytes(GLOSSARY.encode("utf-8-sig"))
        plain_pipeline.run_whisper = lambda model, audio: ("frau mueller", "de")
        plain_pipeline.transcribe(AUDIO)
        assert plain_pipeline.pasted == ["frau Müller"]
        assert "Müller" in glossary.hotwords("de")


class _Response:
    def __init__(self, body: bytes, lines=None):
        self.body = body
        self.lines = lines or []

    def read(self):
        return self.body

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestAnUnusableAiAnswerIsAFailure:
    @pytest.fixture
    def answer(self, monkeypatch, plain_pipeline):
        def install(response):
            monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: response)
            plain_pipeline.run_whisper = lambda model, audio: ("was ist die hauptstadt", "de")
            plain_pipeline.transcribe(AUDIO, prompt_mode=True)
            return plain_pipeline

        return install

    @pytest.mark.parametrize(
        "response",
        [
            _Response(b"", [b'{"response": "", "done": true}\n']),
            _Response(b"", [b'{"error": "model not found"}\n']),
            _Response(b"", [b'{"response": "Oslo"}\n', b'{"error": "out of memory"}\n']),
            _Response(b"", [b'{"response": "Os"}\n', b'{"response": "lo"}\n']),  # cut off, no "done"
            _Response(b"", [b'{"response": "   ", "done": true}\n']),
        ],
        ids=["empty", "error-field", "error-in-stream", "no-done", "blank-stream"],
    )
    def test_nothing_is_pasted_and_the_error_says_so(self, answer, response):
        pipeline = answer(response)
        assert pipeline.pasted == [], "the clipboard stays untouched"
        state, detail, hint = pipeline.states[-1]
        assert (state, detail) == ("error", "KI-Antwort fehlgeschlagen")
        assert hint.startswith("Die KI hat keine brauchbare Antwort geliefert")

    def test_a_complete_stream_is_pasted(self, answer):
        pipeline = answer(_Response(b"", [b'{"response": "Os"}\n', b'{"response": "lo", "done": true}\n']))
        assert pipeline.pasted == ["Oslo"]
