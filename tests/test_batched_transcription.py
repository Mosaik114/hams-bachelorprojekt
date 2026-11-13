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

import numpy as np
import pytest
from live_app import make_pipeline

from hams import transcription as transcription_mod
from hams.recording import SAMPLE_RATE


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
def pipeline():
    return make_pipeline()


@pytest.fixture
def modell(monkeypatch):
    protokoll = Aufzeichnung()
    monkeypatch.setattr(transcription_mod, "BatchedInferencePipeline", lambda model: GebatchteAufzeichnung(protokoll))
    return protokoll


def _audio(sekunden: float) -> np.ndarray:
    rng = np.random.default_rng(7)
    return (0.2 * rng.standard_normal(int(sekunden * SAMPLE_RATE))).astype(np.float32)


class TestWelcherWegGenommenWird:
    @pytest.mark.parametrize("sekunden", [1, 5, 12, 24, 29.9])
    def test_short_recordings_stay_sequential(self, pipeline, modell, sekunden):
        pipeline.run_whisper(modell, _audio(sekunden))
        assert [weg for weg, _ in modell.aufrufe] == ["sequentiell"]

    @pytest.mark.parametrize("sekunden", [30, 45, 90, 300])
    def test_long_recordings_are_batched(self, pipeline, modell, sekunden):
        pipeline.run_whisper(modell, _audio(sekunden))
        assert [weg for weg, _ in modell.aufrufe] == ["gebatcht"]

    def test_the_threshold_is_where_the_measurement_turns(self):
        """Unter 24 s war der Gewinn null, ab 48 s deutlich."""
        assert 24 <= transcription_mod.BATCHED_MIN_SECONDS <= 48


class TestBeideWegeBekommenDasselbe:
    """Ein anderer Weg darf keine anderen Einstellungen bedeuten."""

    def test_the_options_are_identical(self, pipeline, modell):
        pipeline.run_whisper(modell, _audio(5))
        pipeline.run_whisper(modell, _audio(60))
        (_, kurz), (_, lang) = modell.aufrufe
        lang = dict(lang)
        assert lang.pop("batch_size") == transcription_mod.BATCH_SIZE
        assert kurz == lang

    @pytest.mark.parametrize(
        "schluessel",
        [
            "language",
            "vad_filter",
            "word_timestamps",
            "beam_size",
            "condition_on_previous_text",
            "without_timestamps",
            "hotwords",
        ],
    )
    def test_every_configured_option_reaches_both(self, pipeline, modell, schluessel):
        pipeline.run_whisper(modell, _audio(5))
        pipeline.run_whisper(modell, _audio(60))
        for _, kwargs in modell.aufrufe:
            assert schluessel in kwargs


class TestDieVoraussetzungFuersBatchen:
    def test_context_between_segments_stays_off(self, pipeline, modell):
        """Both paths treat every chunk on its own.

        The batched pipeline transcribes the chunks the VAD found independently (faster-whisper ignores
        `condition_on_previous_text` there). The sequential path must not carry text over either, or the two
        would not give the same result, and the comparison behind the threshold above would not hold.
        """
        pipeline.run_whisper(modell, _audio(5))
        assert modell.aufrufe[0][1]["condition_on_previous_text"] is False
