"""Aufnehmen darf beginnen, bevor das Modell steht.

Die Sperre hing frueher am geladenen Modell — in beiden Umschaltern:

    if not self.model_ready.is_set():
        self._apply_state("model_loading")
        return

Das war eine Sperre zu viel. `start_recording` oeffnet das Mikrofon und
sammelt Puffer; es fasst das Modell nirgends an. Gebraucht wird es erst in
`_transcribe_audio`, und bis dahin vergehen bei einer mittleren Aufnahmedauer
von 11,95 s reichlich Sekunden — das Modell steht warm nach 2,2 s.

Gemessen war die wahrgenommene Startzeit dadurch 3,0 s statt 0,8 s: der Nutzer
wartete auf etwas, das er zum Sprechen gar nicht braucht.
"""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from hams.app import FloatingTranscriberApp
from hams.app import FloatingTranscriberApp as App
from hams.config import CFG


@pytest.fixture
def app():
    """Eine Instanz ohne Fenster, Tray und Modell-Ladethread."""
    a = FloatingTranscriberApp.__new__(FloatingTranscriberApp)
    a.model_ready = threading.Event()
    a.model_error = None
    a._switching_to = None
    a.is_recording = False
    a._prompt_mode = False
    a._gestartet = []
    a._fehler_gezeigt = []
    a.start_recording = lambda: a._gestartet.append(True)
    a.stop_recording_and_transcribe = lambda: None
    a._on_model_error = lambda: a._fehler_gezeigt.append(True)
    return a


class TestDieSperreFaelltWeg:
    def test_recording_may_start_while_the_model_loads(self, app):
        assert not app.model_ready.is_set()
        assert app._may_record() is True

    def test_recording_may_start_during_a_model_switch(self, app):
        """Ein Wechsel setzt model_ready kurz zurueck — kein Grund zu sperren.

        Die Auswertung wartet ohnehin auf `_transcribe_lock`.
        """
        app._switching_to = "small"
        assert app.is_switching_model
        assert app._may_record() is True

    def test_the_hotkey_records_instead_of_refusing(self, app):
        FloatingTranscriberApp.toggle_recording(app)
        assert app._gestartet == [True], "Die Aufnahme haette starten muessen"
        assert app._fehler_gezeigt == []

    def test_the_prompt_hotkey_does_the_same(self, app):
        FloatingTranscriberApp.toggle_recording_generate(app)
        assert app._gestartet == [True]
        assert app._prompt_mode is True


class TestWasWeiterhinAbgewiesenWird:
    """Nur, was auch spaeter nicht gutgehen kann."""

    def test_a_model_that_never_loaded_still_refuses(self, app):
        """Sonst waeren die gesprochenen Worte verloren."""
        app.model_error = "cuda/float16: kaputt | cpu/int8: kaputt"
        assert app._may_record() is False
        assert app._fehler_gezeigt == [True]

    def test_an_old_error_does_not_block_a_working_model(self, app):
        """Nach einem geglueckten Rueckfall zaehlt der Fehler nicht mehr."""
        app.model_error = "alter Fehlschlag"
        app.model_ready.set()
        assert app._may_record() is True


class TestDasWartenAufDasModell:
    def test_it_returns_at_once_when_the_model_is_there(self, app):
        app.model_ready.set()
        t0 = time.monotonic()
        assert app._await_model(t0 + 5) is True
        assert time.monotonic() - t0 < 0.2

    def test_it_waits_for_a_model_that_arrives_late(self, app):
        threading.Timer(0.3, app.model_ready.set).start()
        assert app._await_model(time.monotonic() + 5) is True
        assert app.model_ready.is_set()

    def test_it_gives_up_on_a_model_that_failed(self, app):
        app.model_error = "kaputt"
        t0 = time.monotonic()
        assert app._await_model(t0 + 5) is False
        assert time.monotonic() - t0 < 1.0, "sollte nicht bis zur Frist warten"

    def test_a_failure_during_a_switch_is_not_the_end(self, app):
        """Waehrend eines Wechsels darf ein alter Fehler nicht abbrechen."""
        app.model_error = "alter Fehlschlag"
        app._switching_to = "small"
        threading.Timer(0.3, app.model_ready.set).start()
        assert app._await_model(time.monotonic() + 5) is True

    def test_it_honours_the_deadline(self, app):
        t0 = time.monotonic()
        assert app._await_model(t0 + 0.5) is False
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

        live_app.model = SimpleNamespace(transcribe=lambda audio, **options: ([SimpleNamespace(text=" Gesagt ")], None))
        live_app.model_ready.set()
        deadline = time.monotonic() + 5
        while not hams.clipboard.copies:
            assert time.monotonic() < deadline, "the waiting dictation was never pasted"
            time.sleep(0.02)
        assert hams.clipboard.copies == ["Gesagt"]


class TestStoppingAlwaysWorks:
    def _recording_app(self):
        app = App.__new__(App)
        app.model_ready = threading.Event()
        app._switching_to = None
        app._prompt_mode = False
        app.is_recording = True
        app.model_error = "download failed"
        app._state = "error"
        app.stopped = []
        app.stop_recording_and_transcribe = lambda: app.stopped.append(True)
        app._on_model_error = lambda: pytest.fail("the error must not refuse the stop")
        return app

    @pytest.mark.parametrize("toggle", ["toggle_recording", "toggle_recording_generate"])
    def test_a_failed_model_never_refuses_the_stop(self, toggle):
        app = self._recording_app()
        getattr(App, toggle)(app)
        assert app.stopped == [True]

    def test_a_model_error_during_the_recording_waits_for_the_stop(self, live_app, hams):
        live_app.toggle_recording()
        assert live_app._state == "recording"
        live_app._after_switch("turbo", False, persist=False, requested="turbo")
        assert live_app._state == "recording", "the window keeps saying Recording"
        live_app.toggle_recording()  # nothing recorded
        assert live_app.is_recording is False
        assert live_app._state == "error"
        assert live_app.status_label.cget("text") == "Modell konnte nicht geladen werden"

    def test_a_failed_download_during_the_recording_waits_for_the_stop(self, live_app, hams):
        live_app.toggle_recording()
        live_app._on_model_download_failed("small", "Server nicht erreichbar")
        assert live_app._state == "recording"
        assert live_app.model_error == "Server nicht erreichbar"
        live_app.toggle_recording()
        assert live_app.footer_label.cget("text") == "Server nicht erreichbar"

    def test_a_fallback_during_the_recording_follows_the_pasted_dictation(self, live_app, hams, monkeypatch):
        monkeypatch.setattr(live_app, "_enqueue_transcription", lambda *job: None)
        live_app.model_ready.set()
        live_app.toggle_recording()
        live_app._after_switch("turbo", True, persist=True, requested="small")
        assert live_app._state == "recording"
        hams.streams[-1].feed(np.full(800, 0.2))
        live_app.toggle_recording()
        assert live_app._state == "transcribing"
        live_app._back_to_ready()  # the pasted dictation's "done" runs out
        assert live_app.status_label.cget("text") == "Small ließ sich nicht laden"

    def test_a_missing_model_is_left_to_the_queued_dictation(self, live_app, hams, monkeypatch):
        monkeypatch.setattr(live_app, "_enqueue_transcription", lambda *job: None)
        live_app.toggle_recording()
        live_app.model_error = "CUDA out of memory"  # what the failed load leaves behind
        live_app._after_switch("turbo", False, persist=False, requested="turbo")
        hams.streams[-1].feed(np.full(800, 0.2))
        live_app.toggle_recording()
        assert live_app._notice_after_pipeline is None, "the dictation reports the missing model itself"

    def test_a_model_ready_during_the_recording_leaves_the_window_alone(self, live_app):
        live_app.toggle_recording()
        live_app._after_switch("turbo", True, persist=False, requested="turbo")
        assert live_app._state == "recording"
