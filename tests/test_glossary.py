"""Glossar: Hinweiswörter für Whisper, feste Korrekturen, neu lesen ohne Neustart, kaputte Datei schadet nicht."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import tomllib

from hams import glossary


@pytest.fixture
def datei(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ziel = tmp_path / "glossar.toml"
    monkeypatch.setattr(glossary, "glossary_path", lambda: ziel)
    monkeypatch.setitem(glossary._cache, "path", None)
    return ziel


def _schreiben(datei: Path, text: str, schritt: int = 0) -> None:
    datei.write_text(text, encoding="utf-8")
    # mtime sicher verschieden, auch auf Dateisystemen mit grobem Zeitstempel
    stempel = 1_700_000_000 + schritt
    os.utime(datei, (stempel, stempel))


def test_the_default_glossary_is_created_and_knows_the_names(datei: Path) -> None:
    assert not datei.exists()
    hinweis = glossary.hotwords("de")
    assert datei.exists()
    for wort in ("Hams", "Whisper", "faster-whisper", "Ollama", "CUDA", "GitHub", "TypeScript", "PyTorch"):
        assert wort in hinweis


def test_known_mishearings_are_corrected_as_whole_words(datei: Path) -> None:
    assert glossary.apply_replacements("Läuft olama schon auf Kuda?") == "Läuft Ollama schon auf CUDA?"
    assert glossary.apply_replacements("git   hub und Type Script") == "GitHub und TypeScript"
    assert glossary.apply_replacements("Pull Request für Faster Whisper") == "Pull-Request für faster-whisper"
    assert glossary.apply_replacements("Kudamm bleibt") == "Kudamm bleibt"  # „Kuda“ steckt darin, kein ganzes Wort


def test_the_longest_wrong_phrase_wins(datei: Path) -> None:
    _schreiben(datei, '[ersetzen]\n"Allah" = "X"\n"Insha Allah" = "InshaaAllah"\n')
    assert glossary.apply_replacements("Insha Allah") == "InshaaAllah"


def test_an_edited_file_counts_from_the_next_recording(datei: Path) -> None:
    _schreiben(datei, 'woerter = ["Eins"]\n', 1)
    assert glossary.hotwords("de") == "Ein deutscher Text. Namen und Begriffe darin: Eins."
    _schreiben(datei, 'woerter = ["Eins", "Zwei"]\n[ersetzen]\n"drei" = "Drei"\n', 2)
    assert glossary.hotwords("de") == "Ein deutscher Text. Namen und Begriffe darin: Eins, Zwei."
    assert glossary.apply_replacements("eins drei") == "eins Drei"


def test_a_broken_file_never_crashes_and_means_an_empty_glossary(datei: Path) -> None:
    _schreiben(datei, "woerter = [kaputt", 3)
    assert glossary.hotwords("de") is None
    assert glossary.cleanup_hint("de") == ""
    assert glossary.apply_replacements("Mätzgin") == "Mätzgin"


def test_odd_entries_are_skipped(datei: Path) -> None:
    _schreiben(datei, 'woerter = ["Gut", 3, "  "]\n[ersetzen]\n"x" = 5\n" " = "leer"\n', 4)
    assert glossary.words() == ("Gut",)
    assert glossary.apply_replacements("x") == "x"


def test_the_cleanup_is_told_to_keep_the_spellings(datei: Path) -> None:
    _schreiben(datei, 'woerter = ["Mazgin", "Khalas"]\n', 5)
    assert glossary.cleanup_hint("de") == "Diese Wörter sind richtig geschrieben und bleiben genau so: Mazgin, Khalas."


def test_a_missing_default_creates_nothing(datei: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(glossary, "DEFAULT_PATH", datei.parent / "fehlt.toml")
    assert glossary.hotwords("de") is None
    assert not datei.exists()


def test_an_unwritable_place_is_only_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "datei").write_text("")
    monkeypatch.setattr(glossary, "glossary_path", lambda: tmp_path / "datei" / "glossar.toml")
    monkeypatch.setitem(glossary._cache, "path", None)
    assert glossary.hotwords("de") is None


def test_the_shipped_glossary_is_valid_toml() -> None:
    """Ein doppelter Eintrag macht die ganze Datei ungültig – dann wäre das Glossar leer."""
    daten = tomllib.loads(glossary.DEFAULT_PATH.read_text(encoding="utf-8"))
    assert daten["woerter"] and daten["ersetzen"]
