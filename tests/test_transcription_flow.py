"""Der Weg von der fertigen Tonspur bis zum eingefuegten Text.

Geprueft an der echten Anwendung (siehe tests/live_app.py): Whisper ist ein
kleines Ersatzmodell, Ollama eine Ersatzantwort auf `urlopen`. Die Pipeline
selbst - Zustaende, Bereinigung, Prompt-Modus, Einfuegen, Verlauf, Datei -
laeuft unveraendert.
"""

import io
import json
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import numpy as np
import pytest

from hams import glossary
from hams import transcription as transcription_mod
from hams.config import CFG
from hams.transcription import PROMPT_PLAIN_SYSTEM

AUDIO = np.full(16000, 0.2, dtype=np.float32)


class FakeModel:
    def __init__(self, *texts, error=None):
        self.texts = texts
        self.error = error
        self.calls = 0

    def transcribe(self, audio, **options):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [SimpleNamespace(text=f" {t} ") for t in self.texts], None


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def ollama(monkeypatch):
    """Ersetzt `urlopen`: merkt sich jede Anfrage, antwortet wie Ollama."""
    state = SimpleNamespace(requests=[], answer="", lines=None, error=None)

    def urlopen(request, timeout=None):
        state.requests.append((json.loads(request.data), timeout))
        if state.error is not None:
            raise state.error
        if state.lines is not None:
            return FakeResponse(b"\n".join(state.lines))
        return FakeResponse(json.dumps({"response": state.answer}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(glossary, "cleanup_hint", lambda _language: "")  # Prompts hier ohne Glossar-Satz vergleichen
    return state


@pytest.fixture
def ready(live_app):
    """Die Anwendung mit geladenem Modell und abgeschalteter Bereinigung."""
    live_app.model = FakeModel("Hallo", "Welt")
    live_app.model_ready.set()
    CFG.ollama_cleanup_enabled = False
    return live_app


def run(app, audio=AUDIO, prompt_mode=False) -> None:
    """Die Pipeline auf dem Testthread - sie laeuft sonst im Hintergrund."""
    app._transcribe_audio(audio, prompt_mode=prompt_mode)
    ende = time.monotonic() + 2
    while any(t.name == "save-transcript" and t.is_alive() for t in threading.enumerate()):
        assert time.monotonic() < ende, "Transkript wurde nicht gespeichert"
        time.sleep(0.01)
    app.root.update()


def status(app) -> str:
    return app.status_label.cget("text")


class TestPipeline:
    def test_the_raw_transcript_is_pasted_saved_and_remembered(self, ready, hams):
        run(ready)
        assert hams.clipboard.copies == ["Hallo Welt"]
        assert hams.keyboard.sent == ["ctrl+v"]
        assert ready._history[0][2] == "Hallo Welt"
        files = list(transcription_mod.OUTPUT_DIR.glob("*.txt"))
        assert [f.read_text(encoding="utf-8") for f in files] == ["Hallo Welt"]
        assert ready._state == "done"

    def test_cleanup_goes_through_ollama_and_loses_its_markdown(self, ready, hams, ollama):
        CFG.ollama_cleanup_enabled = True
        CFG.cleanup_prompt_template = "Korrigiere: {text}"
        ollama.answer = "**Hallo** Welt"
        run(ready)
        body, timeout = ollama.requests[0]
        assert body["prompt"] == "Korrigiere: Hallo Welt"
        assert body["model"] == CFG.ollama_model_cleanup and body["think"] is False
        assert "system" not in body
        assert timeout == CFG.ollama_timeout_cleanup
        assert hams.clipboard.copies == ["Hallo Welt"]

    def test_a_template_without_placeholder_gets_the_text_appended(self, ready, ollama):
        CFG.ollama_cleanup_enabled = True
        CFG.cleanup_prompt_template = "Korrigiere den Text."
        ollama.answer = "ok"
        run(ready)
        assert ollama.requests[0][0]["prompt"] == "Korrigiere den Text.\n\nText: Hallo Welt"

    def test_the_glossary_goes_before_the_cleanup_prompt(self, ready, ollama, monkeypatch):
        monkeypatch.setattr(glossary, "cleanup_hint", lambda _language: "Bleibt so: Mazgin.")
        CFG.ollama_cleanup_enabled = True
        CFG.cleanup_prompt_template = "Korrigiere: {text}"
        ollama.answer = "Hallo Mätzgin"
        run(ready)
        assert ollama.requests[0][0]["prompt"] == "Bleibt so: Mazgin.\n\nKorrigiere: Hallo Welt"

    def test_without_ollama_the_raw_text_is_still_pasted(self, ready, hams, ollama):
        CFG.ollama_cleanup_enabled = True
        ollama.error = urllib.error.URLError("verbindung abgelehnt")
        ready._apply_state = record_states(ready)
        run(ready)
        assert hams.clipboard.copies == ["Hallo Welt"]
        assert ("done", "Eingefügt · ohne Bereinigung") in ready.seen

    def test_without_ollama_one_calm_hint_then_no_more_waiting(self, ready, hams, ollama):
        from hams.ui_kit import Theme

        CFG.ollama_cleanup_enabled = True
        ollama.error = urllib.error.URLError("verbindung abgelehnt")
        run(ready)
        assert status(ready) == "Eingefügt · ohne Bereinigung", (
            "der Hinweis bleibt stehen, statt ueberschrieben zu werden"
        )
        assert ready.footer_label.cget("text").startswith("Ollama läuft nicht")
        assert ready.footer_label.cget("fg") == Theme.TEXT_SECONDARY, "eine Auskunft, kein Fehlerrot"
        assert ready._state == "done"
        run(ready)
        assert len(ollama.requests) == 1, "kurz nach dem Ausfall wird nicht erneut gewartet"
        assert hams.clipboard.copies == ["Hallo Welt", "Hallo Welt"]
        assert status(ready) == "Eingefügt · ohne Bereinigung"
        assert ready.footer_label.cget("text") == "", "der Hinweis kommt nur einmal"
        ready._cleanup_retry_at = 0.0  # eine Minute spaeter
        run(ready)
        assert len(ollama.requests) == 2

    def test_prompt_mode_pastes_the_answer_as_plain_text(self, ready, hams, ollama):
        CFG.ollama_stream_prompt = False
        ollama.answer = "Die Hauptstadt ist **Oslo**."
        run(ready, prompt_mode=True)
        body = ollama.requests[0][0]
        assert body["prompt"] == "Hallo Welt", "der diktierte Prompt bleibt Wort fuer Wort"
        assert body["system"] == PROMPT_PLAIN_SYSTEM
        assert "options" not in body, "KI fragen sampelt mit den Vorgaben des Modells"
        assert hams.clipboard.copies == ["Die Hauptstadt ist Oslo."]

    def test_the_cleanup_samples_deterministically(self, ready, ollama):
        CFG.ollama_cleanup_enabled = True
        CFG.cleanup_prompt_template = "Korrigiere: {text}"
        ollama.answer = "Hallo Welt."
        run(ready)
        assert ollama.requests[0][0]["options"] == {"temperature": 0.0, "seed": 0}

    def test_the_cleanup_temperature_comes_from_the_settings(self, ready, ollama):
        CFG.ollama_cleanup_enabled = True
        CFG.ollama_cleanup_temperature = 0.7
        ollama.answer = "Hallo Welt."
        run(ready)
        assert ollama.requests[0][0]["options"] == {"temperature": 0.7, "seed": 0}

    def test_a_rejected_cleanup_pastes_the_raw_text(self, ready, hams, ollama):
        """Eine erfundene Ziffer: lieber das, was Whisper gehoert hat."""
        CFG.ollama_cleanup_enabled = True
        ollama.answer = "Hallo Welt 42."
        run(ready)
        assert hams.clipboard.copies == ["Hallo Welt"]
        assert ready._state == "done"

    def test_a_failed_prompt_pastes_nothing(self, ready, hams, ollama):
        ollama.error = TimeoutError()
        run(ready, prompt_mode=True)
        assert hams.clipboard.copies == []
        assert status(ready) == "KI-Antwort fehlgeschlagen"

    def test_silence_in_the_model_is_reported(self, live_app, hams):
        live_app.model = FakeModel()
        live_app.model_ready.set()
        run(live_app)
        assert status(live_app) == "Keine Sprache erkannt"
        assert hams.clipboard.copies == []

    def test_a_crashing_model_is_reported(self, live_app, hams):
        live_app.model = FakeModel(error=RuntimeError("CUDA out of memory"))
        live_app.model_ready.set()
        run(live_app, prompt_mode=True)
        assert status(live_app) == "Transkription fehlgeschlagen"

    def test_a_failure_while_pasting_is_reported(self, ready, hams, monkeypatch):
        def broken(text):
            raise RuntimeError("Zwischenablage gesperrt")

        monkeypatch.setattr(ready, "_copy_and_paste", broken)
        run(ready)
        assert status(ready) == "Transkription fehlgeschlagen"


class TestModelNotThereYet:
    def test_it_waits_and_says_so(self, live_app, hams):
        live_app.model = FakeModel("Spaet", "aber", "da")
        live_app._apply_state = record_states(live_app)
        threading.Timer(0.2, live_app.model_ready.set).start()
        CFG.ollama_cleanup_enabled = False
        run(live_app)
        assert live_app.seen[0] == ("transcribing", "Modell wird noch geladen …")
        assert hams.clipboard.copies == ["Spaet aber da"]

    def test_a_model_that_never_comes_ends_in_an_error(self, live_app, hams):
        live_app.model_error = "CUDA fehlt"
        run(live_app, prompt_mode=True)
        assert status(live_app) == "Modell nicht verfügbar"
        assert hams.clipboard.copies == []

    def test_a_lost_model_ends_in_an_error(self, live_app, hams):
        live_app.model_ready.set()  # bereit gemeldet, aber schon wieder weg
        run(live_app)
        assert status(live_app) == "Kein Modell geladen"


def record_states(app):
    """Haelt jeden Zustandswechsel fest und fuehrt ihn trotzdem aus."""
    original = app._apply_state
    app.seen = []

    def apply(state, detail=None, hint=None, **options):
        app.seen.append((state, detail))
        original(state, detail, hint, **options)

    return apply


class TestStreaming:
    def test_the_answer_is_assembled_and_counted_live(self, live_app, ollama):
        ollama.lines = [
            json.dumps({"response": "Hallo "}).encode(),
            b"",
            b"kein json",
            json.dumps({"response": "Welt"}).encode(),
            json.dumps({"response": "", "done": True}).encode(),
            json.dumps({"response": "zu spaet"}).encode(),
        ]
        answer = live_app._ollama_generate("Frage", "llama", 30, stream=True, progress_label="Generiere")
        live_app.root.update()
        assert answer == "Hallo Welt"
        assert live_app.elapsed_label.cget("text") == "6 Zeichen"

    def test_without_a_label_nothing_is_shown(self, live_app, ollama):
        ollama.lines = [json.dumps({"response": "x", "done": True}).encode()]
        assert live_app._ollama_generate("Frage", "llama", 30, stream=True) == "x"
        live_app.root.update()
        assert live_app.elapsed_label.cget("text") == ""


class TestPaste:
    def test_it_waits_for_the_clipboard_despite_line_endings(self, live_app, hams):
        live_app._copy_and_paste("eins\r\nzwei")
        assert hams.clipboard.copies == ["eins\r\nzwei"]
        assert hams.keyboard.sent == ["ctrl+v"]

    def test_an_unreadable_clipboard_still_pastes(self, live_app, hams):
        hams.clipboard.broken = True
        live_app._copy_and_paste("Text")
        assert hams.keyboard.sent == ["ctrl+v"]

    def test_a_slow_clipboard_is_given_the_configured_time_only(self, live_app, hams, monkeypatch):
        monkeypatch.setattr(hams.clipboard, "paste", lambda: "noch alt")
        CFG.clipboard_paste_timeout = 0.05
        start = time.monotonic()
        live_app._copy_and_paste("neu")
        assert time.monotonic() - start < 1
        assert hams.keyboard.sent == ["ctrl+v"]


class TestSaveAndWarmup:
    def test_a_save_that_fails_does_not_raise(self, live_app, monkeypatch, tmp_path):
        blocker = tmp_path / "datei"
        blocker.write_text("x")
        monkeypatch.setattr(transcription_mod, "OUTPUT_DIR", blocker / "unter")
        live_app._save_transcript_file("Text")

    def test_warmup_loads_the_cleanup_model_without_a_prompt(self, live_app, ollama):
        CFG.ollama_cleanup_enabled = True
        live_app._warmup_ollama()
        body, timeout = ollama.requests[0]
        assert body["model"] == CFG.ollama_model_cleanup and body["prompt"] == ""
        assert timeout == 60

    def test_warmup_is_skipped_without_cleanup(self, live_app, ollama):
        CFG.ollama_cleanup_enabled = False
        live_app._warmup_ollama()
        assert ollama.requests == []

    def test_an_unreachable_ollama_is_only_logged(self, live_app, ollama):
        CFG.ollama_cleanup_enabled = True
        ollama.error = urllib.error.URLError("aus")
        live_app._warmup_ollama()

    def test_the_warmup_thread_honours_the_setting(self, live_app, monkeypatch):
        from hams.transcription import TranscriptionMixin

        started = []
        monkeypatch.setattr(live_app, "_warmup_ollama", lambda: started.append(1))
        CFG.ollama_warmup_on_start = False
        TranscriptionMixin._warmup_ollama_async(live_app)
        CFG.ollama_warmup_on_start = True
        TranscriptionMixin._warmup_ollama_async(live_app)
        ende = time.monotonic() + 2
        while not started and time.monotonic() < ende:
            time.sleep(0.01)
        assert started == [1]
