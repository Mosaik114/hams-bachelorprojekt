"""Lange Aufnahmen werden gebatcht ausgewertet, kurze nicht.

Gemessen (turbo/float16, RTX 5070, gegen bekannten deutschen Referenztext,
80 Proben aus einem synthetischen Korpus):

      6 s   −1,6 % Zeit    WER ±0,00 pp
     12 s   ±0,0 % Zeit    WER ±0,00 pp
     24 s   +0,5 % Zeit    WER ±0,00 pp
     48 s  +25,6 % Zeit    WER −2,38 pp
     84 s  +34,5 % Zeit    WER −1,32 pp

Unterhalb von rund 30 s bringt der gebatchte Weg nichts. Oberhalb wird er
schneller *und* genauer: der sequentielle Lauf verschluckt am Ende langer
Aufnahmen Woerter und haengt Floskeln an ("vielen Dank"), die nie gesprochen
wurden. In keinem Laengenbereich wurde die WER schlechter.

Die Schwelle ist deshalb kein Geschmack, sondern die Stelle, an der die
Messung kippt.
"""

import ast

import numpy as np
import pytest

from wisper import main as wm


class Aufzeichnung:
    """Merkt sich, welcher Weg genommen wurde."""

    def __init__(self):
        self.aufrufe = []

    def transcribe(self, audio, **kwargs):
        self.aufrufe.append(("sequentiell", kwargs))
        return iter([type("S", (), {"text": " sequentiell"})()]), None


class GebatchteAufzeichnung:
    def __init__(self, protokoll):
        self.protokoll = protokoll

    def transcribe(self, audio, **kwargs):
        self.protokoll.aufrufe.append(("gebatcht", kwargs))
        return iter([type("S", (), {"text": " gebatcht"})()]), None


@pytest.fixture
def app(monkeypatch):
    a = wm.FloatingTranscriberApp.__new__(wm.FloatingTranscriberApp)
    a.root = type("R", (), {"after": staticmethod(lambda *args, **kw: None)})()
    a._prompt_mode = False
    return a


@pytest.fixture
def modell(monkeypatch):
    protokoll = Aufzeichnung()
    monkeypatch.setattr(wm, "BatchedInferencePipeline",
                        lambda model: GebatchteAufzeichnung(protokoll))
    return protokoll


def _audio(sekunden: float) -> np.ndarray:
    rng = np.random.default_rng(7)
    return (0.2 * rng.standard_normal(int(sekunden * wm.CFG.sample_rate))).astype(np.float32)


class TestWelcherWegGenommenWird:

    @pytest.mark.parametrize("sekunden", [1, 5, 12, 24, 29.9])
    def test_short_recordings_stay_sequential(self, app, modell, sekunden):
        app._run_transcription(modell, _audio(sekunden))
        assert [weg for weg, _ in modell.aufrufe] == ["sequentiell"]

    @pytest.mark.parametrize("sekunden", [30, 45, 90, 300])
    def test_long_recordings_are_batched(self, app, modell, sekunden):
        app._run_transcription(modell, _audio(sekunden))
        assert [weg for weg, _ in modell.aufrufe] == ["gebatcht"]

    def test_the_threshold_is_where_the_measurement_turns(self):
        """Unter 24 s war der Gewinn null, ab 48 s deutlich."""
        assert 24 <= wm.BATCHED_MIN_SECONDS <= 48


class TestBeideWegeBekommenDasselbe:
    """Ein anderer Weg darf keine anderen Einstellungen bedeuten."""

    def test_the_options_are_identical(self, app, modell):
        app._run_transcription(modell, _audio(5))
        app._run_transcription(modell, _audio(60))
        (_, kurz), (_, lang) = modell.aufrufe
        lang = dict(lang)
        assert lang.pop("batch_size") == wm.BATCH_SIZE
        assert kurz == lang

    @pytest.mark.parametrize("schluessel", [
        "language", "vad_filter", "word_timestamps", "beam_size",
        "condition_on_previous_text", "without_timestamps",
    ])
    def test_every_configured_option_reaches_both(self, app, modell, schluessel):
        app._run_transcription(modell, _audio(5))
        app._run_transcription(modell, _audio(60))
        for _, kwargs in modell.aufrufe:
            assert schluessel in kwargs


class TestDieVoraussetzungFuersBatchen:

    def test_context_between_segments_stays_off(self):
        """Ohne das waere die parallele Auswertung nicht dasselbe Ergebnis.

        Gebatcht werden die vom VAD gefundenen Abschnitte unabhaengig
        voneinander. Floesse Kontext von einem in den naechsten, waere das
        Ergebnis von der Reihenfolge abhaengig — und der Vergleich, auf dem
        die Schwelle oben beruht, hinfaellig.
        """
        assert wm.Config().transcription_condition_on_previous_text is False
        quelle = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        for knoten in ast.walk(ast.parse(quelle)):
            if isinstance(knoten, ast.FunctionDef) and knoten.name == "_run_transcription":
                rumpf = ast.get_source_segment(quelle, knoten)
                break
        else:
            raise AssertionError("_run_transcription nicht gefunden")
        assert "condition_on_previous_text" in rumpf
