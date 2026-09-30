"""Der Signalton unter Linux: weicher Glockenklang statt Piepton."""

import numpy as np
import pytest

pytest.importorskip("fcntl", reason="Linux-Modul: der Signalton gehört zur Linux-Fassung")
from wisper import linux  # noqa: E402


def _dominant_hz(samples: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(samples))
    return float(np.fft.rfftfreq(len(samples), 1 / linux.CHIME_RATE)[spectrum.argmax()])


def test_the_chime_is_quiet_and_ready_to_play():
    tone = linux.chime(880, 80)
    assert tone.dtype == np.float32
    assert abs(float(np.abs(tone).max()) - linux.CHIME_VOLUME) < 1e-6


def test_the_chime_starts_and_ends_without_a_click():
    tone = linux.chime(220, 120)
    assert abs(tone[0]) < 1e-3 and abs(tone[-1]) < 1e-3


def test_start_rises_and_stop_falls():
    gap = int(linux.CHIME_RATE * linux.CHIME_GAP)
    for freq, rising in ((880, True), (220, False)):
        tone = linux.chime(freq, 80)
        first, second = _dominant_hz(tone[:gap]), _dominant_hz(tone[gap + gap // 2 :])
        assert (second > first) is rising


def test_an_unknown_frequency_rings_once():
    assert len(linux.chime(1000, 50)) == len(linux.bell(1000.0))
