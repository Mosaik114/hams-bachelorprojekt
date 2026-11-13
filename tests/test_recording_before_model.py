"""Aufnehmen darf beginnen, bevor das Modell steht.

Die Sperre hing frueher am geladenen Modell — in beiden Umschaltern:

    if not self.model_ready.is_set():
        self.apply_state("model_loading")
        return

Das war eine Sperre zu viel. `start_recording` oeffnet das Mikrofon und
sammelt Puffer; es fasst das Modell nirgends an. Gebraucht wird es erst in
der Pipeline, und bis dahin vergehen bei einer mittleren Aufnahmedauer
von 11,95 s reichlich Sekunden — das Modell steht warm nach 2,2 s.

Gemessen war die wahrgenommene Startzeit dadurch 3,0 s statt 0,8 s: der Nutzer
wartete auf etwas, das er zum Sprechen gar nicht braucht.
"""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from live_app import ready_models

from hams.config import CFG


@pytest.fixture
def models():
    """A real model manager with nothing loaded yet."""
    return ready_models()


class TestTheLockIsGone:
    def test_recording_may_start_while_the_model_loads(self, live_app):
        assert not live_app.models.model_ready.is_set()
        live_app.toggle_recording()
        assert live_app.is_recording and live_app._state == "recording"

    def test_recording_may_start_during_a_model_switch(self, live_app):
        """A switch clears `model_ready` for a moment - no reason to refuse; the pipeline waits for it anyway."""
        live_app.models._switching_to = "small"
        assert live_app.models.is_switching
        live_app.toggle_recording()
        assert live_app.is_recording

    def test_the_prompt_hotkey_does_the_same(self, live_app):
        live_app.toggle_recording_generate()
        assert live_app.is_recording and live_app._prompt_mode is True
        assert live_app._state == "recording_prompt"


class TestWhatIsStillRefused:
    """Only what cannot go well later either."""

    def test_a_model_that_never_loaded_still_refuses(self, live_app, hams):
        """Otherwise the spoken words would be lost."""
        live_app.models.model_error = "cuda/float16: kaputt | cpu/int8: kaputt"
        live_app.toggle_recording()
        assert not live_app.is_recording and hams.streams == []
        assert live_app.status_label.cget("text") == "Modell konnte nicht geladen werden"

    def test_an_old_error_does_not_block_a_working_model(self, live_app):
        """After a successful fallback the error no longer counts."""
        live_app.models.model_error = "alter Fehlschlag"
        live_app.models.model_ready.set()
        live_app.toggle_recording()
        assert live_app.is_recording


class TestWaitingForTheModel:
    def test_it_returns_at_once_when_the_model_is_there(self, models):
        models.model_ready.set()
        t0 = time.monotonic()
        assert models.wait_until_ready(t0 + 5) is True
        assert time.monotonic() - t0 < 0.2

    def test_it_waits_for_a_model_that_arrives_late(self, models):
        threading.Timer(0.3, models.model_ready.set).start()
        assert models.wait_until_ready(time.monotonic() + 5) is True
        assert models.model_ready.is_set()

    def test_it_gives_up_on_a_model_that_failed(self, models):
        models.model_error = "kaputt"
        t0 = time.monotonic()
        assert models.wait_until_ready(t0 + 5) is False
        assert time.monotonic() - t0 < 1.0, "it must not wait until the deadline"

    def test_a_failure_during_a_switch_is_not_the_end(self, models):
        """During a switch an old error must not give up."""
        models.model_error = "alter Fehlschlag"
        models._switching_to = "small"
        threading.Timer(0.3, models.model_ready.set).start()
        assert models.wait_until_ready(time.monotonic() + 5) is True

    def test_it_honours_the_deadline(self, models):
        t0 = time.monotonic()
        assert models.wait_until_ready(t0 + 0.5) is False
        assert 0.4 < time.monotonic() - t0 < 2.0


class TestEndToEnd:
    def test_words_spoken_while_the_model_loads_are_pasted_once_it_is_there(self, live_app, hams):
        """The recording never needs the model; the transcription waits for it and then pastes."""
        CFG.ollama_cleanup_enabled = False
        live_app.toggle_recording()
        assert live_app.is_recording
        hams.streams[-1].feed(np.full(1600, 0.2))
        live_app.toggle_recording()
        assert not live_app.is_recording
        assert hams.clipboard.copies == []  # nothing to transcribe with yet

        said = [SimpleNamespace(text=" Gesagt ")]
        live_app.models.model = SimpleNamespace(transcribe=lambda audio, **options: (said, None))
        live_app.models.model_ready.set()
        deadline = time.monotonic() + 5
        while not hams.clipboard.copies:
            assert time.monotonic() < deadline, "the waiting dictation was never pasted"
            time.sleep(0.02)
        assert hams.clipboard.copies == ["Gesagt"]


class TestStoppingAlwaysWorks:
    @pytest.mark.parametrize("toggle", ["toggle_recording", "toggle_recording_generate"])
    def test_a_failed_model_never_refuses_the_stop(self, live_app, toggle):
        getattr(live_app, toggle)()
        live_app.models.model_error = "download failed"  # the load failed during the recording
        live_app.apply_state("error")
        getattr(live_app, toggle)()
        assert not live_app.is_recording

    def test_a_model_error_during_the_recording_waits_for_the_stop(self, live_app, hams):
        live_app.toggle_recording()
        assert live_app._state == "recording"
        live_app.models._finish_load("turbo", False, persist=False, requested="turbo")
        assert live_app._state == "recording", "the window keeps saying Recording"
        live_app.toggle_recording()  # nothing recorded
        assert live_app.is_recording is False
        assert live_app._state == "error"
        assert live_app.status_label.cget("text") == "Modell konnte nicht geladen werden"

    def test_a_failed_download_during_the_recording_waits_for_the_stop(self, live_app, hams):
        live_app.toggle_recording()
        live_app._on_model_download_failed("small", "Server nicht erreichbar")
        assert live_app._state == "recording"
        assert live_app.models.model_error == "Server nicht erreichbar"
        live_app.toggle_recording()
        assert live_app.footer_label.cget("text") == "Server nicht erreichbar"

    def test_a_fallback_during_the_recording_follows_the_pasted_dictation(self, live_app, hams, monkeypatch):
        monkeypatch.setattr(live_app.pipeline, "enqueue", lambda *job: None)
        live_app.models.model_ready.set()
        live_app.toggle_recording()
        live_app.models._finish_load("turbo", True, persist=True, requested="small")
        assert live_app._state == "recording"
        hams.streams[-1].feed(np.full(800, 0.2))
        live_app.toggle_recording()
        assert live_app._state == "transcribing"
        live_app._back_to_ready()  # the pasted dictation's "done" runs out
        assert live_app.status_label.cget("text") == "Small ließ sich nicht laden"

    def test_a_missing_model_is_left_to_the_queued_dictation(self, live_app, hams, monkeypatch):
        monkeypatch.setattr(live_app.pipeline, "enqueue", lambda *job: None)
        live_app.toggle_recording()
        live_app.models.model_error = "CUDA out of memory"  # what the failed load leaves behind
        live_app.models._finish_load("turbo", False, persist=False, requested="turbo")
        hams.streams[-1].feed(np.full(800, 0.2))
        live_app.toggle_recording()
        assert live_app._notice_after_pipeline is None, "the dictation reports the missing model itself"

    def test_a_model_ready_during_the_recording_leaves_the_window_alone(self, live_app):
        live_app.toggle_recording()
        live_app.models._finish_load("turbo", True, persist=False, requested="turbo")
        assert live_app._state == "recording"
