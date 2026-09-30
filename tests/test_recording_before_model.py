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

import ast
import threading
import time

import pytest

from wisper import main as wm


@pytest.fixture
def app():
    """Eine Instanz ohne Fenster, Tray und Modell-Ladethread."""
    a = wm.FloatingTranscriberApp.__new__(wm.FloatingTranscriberApp)
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
        wm.FloatingTranscriberApp.toggle_recording(app)
        assert app._gestartet == [True], "Die Aufnahme haette starten muessen"
        assert app._fehler_gezeigt == []

    def test_the_prompt_hotkey_does_the_same(self, app):
        wm.FloatingTranscriberApp.toggle_recording_generate(app)
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


class TestDieAnnahmeDahinter:
    """Die ganze Aenderung steht und faellt mit einer Eigenschaft."""

    @staticmethod
    def _rumpf(name: str) -> str:
        quelle = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        for knoten in ast.walk(ast.parse(quelle)):
            if isinstance(knoten, ast.FunctionDef) and knoten.name == name:
                return ast.get_source_segment(quelle, knoten)
        raise AssertionError(f"{name} nicht gefunden")

    def test_start_recording_never_touches_the_model(self):
        """Faellt das, ist die Aufnahme vor dem Modell nicht mehr sicher."""
        rumpf = self._rumpf("start_recording")
        for verboten in ("self.model", "model_ready", "WhisperModel",
                         "current_model", "_transcribe_lock"):
            assert verboten not in rumpf, (
                f"start_recording benutzt {verboten!r} — die Aufnahme haengt "
                f"damit doch wieder am Modell"
            )

    def test_the_transcription_waits_instead(self):
        rumpf = self._rumpf("_transcribe_audio")
        assert "_await_model" in rumpf, (
            "Wenn die Aufnahme nicht mehr wartet, muss die Auswertung es tun"
        )
        assert rumpf.index("_await_model") < rumpf.index("self._transcribe_lock")
