"""Glossar: Hinweiswörter für Whisper, feste Korrekturen, neu lesen ohne Neustart, kaputte Datei schadet nicht."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from hams import glossary


@pytest.fixture
def file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "glossar.toml"
    monkeypatch.setattr(glossary, "glossary_path", lambda: target)
    monkeypatch.setitem(glossary._cache, "path", None)
    return target


def _schreiben(file: Path, text: str, step: int = 0) -> None:
    file.write_text(text, encoding="utf-8")
    # mtime sicher verschieden, auch auf Dateisystemen mit grobem Zeitstempel
    stamp = 1_700_000_000 + step
    os.utime(file, (stamp, stamp))


def test_the_default_glossary_is_created_and_knows_the_names(file: Path) -> None:
    assert not file.exists()
    hint = glossary.hotwords("de")
    assert file.exists()
    for wort in ("Hams", "Whisper", "faster-whisper", "Ollama", "CUDA", "GitHub", "TypeScript", "PyTorch"):
        assert wort in hint


def test_known_mishearings_are_corrected_as_whole_words(file: Path) -> None:
    assert glossary.apply_replacements("Läuft olama schon auf Kuda?") == "Läuft Ollama schon auf CUDA?"
    assert glossary.apply_replacements("git   hub und Type Script") == "GitHub und TypeScript"
    assert glossary.apply_replacements("Pull Request für Faster Whisper") == "Pull-Request für faster-whisper"
    assert glossary.apply_replacements("Kudamm bleibt") == "Kudamm bleibt"  # „Kuda“ steckt darin, kein ganzes Wort


def test_the_longest_wrong_phrase_wins(file: Path) -> None:
    _schreiben(file, '[ersetzen]\n"Allah" = "X"\n"Insha Allah" = "InshaaAllah"\n')
    assert glossary.apply_replacements("Insha Allah") == "InshaaAllah"


def test_an_edited_file_counts_from_the_next_recording(file: Path) -> None:
    _schreiben(file, 'woerter = ["Eins"]\n', 1)
    assert glossary.hotwords("de") == "Ein deutscher Text. Namen und Begriffe darin: Eins."
    _schreiben(file, 'woerter = ["Eins", "Zwei"]\n[ersetzen]\n"drei" = "Drei"\n', 2)
    assert glossary.hotwords("de") == "Ein deutscher Text. Namen und Begriffe darin: Eins, Zwei."
    assert glossary.apply_replacements("eins drei") == "eins Drei"


def test_a_broken_file_never_crashes_and_means_an_empty_glossary(file: Path) -> None:
    _schreiben(file, "woerter = [kaputt", 3)
    assert glossary.hotwords("de") is None
    assert glossary.cleanup_hint("de") == ""
    assert glossary.apply_replacements("Mätzgin") == "Mätzgin"


def test_odd_entries_are_skipped(file: Path) -> None:
    _schreiben(file, 'woerter = ["Gut", 3, "  "]\n[ersetzen]\n"x" = 5\n" " = "leer"\n', 4)
    assert glossary.words() == ("Gut",)
    assert glossary.apply_replacements("x") == "x"


def test_the_cleanup_is_told_to_keep_the_spellings(file: Path) -> None:
    _schreiben(file, 'woerter = ["Mazgin", "Khalas"]\n', 5)
    assert glossary.cleanup_hint("de") == "Diese Wörter sind richtig geschrieben und bleiben genau so: Mazgin, Khalas."


def test_a_missing_default_creates_nothing(file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(glossary, "DEFAULT_PATH", file.parent / "fehlt.toml")
    assert glossary.hotwords("de") is None
    assert not file.exists()


def test_an_unwritable_place_is_only_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "datei").write_text("")
    monkeypatch.setattr(glossary, "glossary_path", lambda: tmp_path / "datei" / "glossar.toml")
    monkeypatch.setitem(glossary._cache, "path", None)
    assert glossary.hotwords("de") is None


def test_the_shipped_glossary_is_valid_toml() -> None:
    """Ein doppelter Eintrag macht die ganze Datei ungültig – dann wäre das Glossar leer."""
    daten = tomllib.loads(glossary.DEFAULT_PATH.read_text(encoding="utf-8"))
    assert daten["woerter"] and daten["ersetzen"]


def test_new_installs_use_the_english_file_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(glossary, "CONFIG_PATH", tmp_path / "config.toml")
    assert glossary.glossary_path() == tmp_path / "glossary.toml"


def test_an_existing_german_file_name_keeps_working(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(glossary, "CONFIG_PATH", tmp_path / "config.toml")
    (tmp_path / "glossar.toml").write_text('words = ["Alt"]\n', encoding="utf-8")
    assert glossary.glossary_path() == tmp_path / "glossar.toml"


def test_open_in_editor_creates_the_file_first(file: Path) -> None:
    launched = []
    path = glossary.open_in_editor(launch=launched.append)
    assert path == file and file.exists()
    assert launched == [str(file)]


def test_open_in_editor_without_a_template_writes_an_empty_glossary(
    file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(glossary, "default_path", lambda: file.parent / "fehlt.toml")
    glossary.open_in_editor(launch=lambda _path: None)
    assert tomllib.loads(file.read_text(encoding="utf-8")) == {"words": [], "replace": {}}


def test_open_in_editor_picks_the_platform_opener(file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    popen = []
    monkeypatch.setattr(glossary.subprocess, "Popen", lambda args, **_kw: popen.append(args))
    glossary.open_in_editor(platform="linux")
    assert popen == [["xdg-open", str(file)]]
    if hasattr(glossary.os, "startfile"):
        started = []
        monkeypatch.setattr(glossary.os, "startfile", started.append)
        glossary.open_in_editor(platform="win32")
        assert started == [str(file)]


def test_a_value_is_inserted_literally_not_as_a_regex_template(file: Path) -> None:
    r"""Regression: 'C:\Temp' raised re.error on every dictation, '\1' was read as a group reference."""
    _schreiben(file, "[replace]\n\"temp ordner\" = 'C:\\Temp'\n\"gruppe\" = '\\1 und \\g<0>'\n", 4)
    assert glossary.apply_replacements("im temp ordner") == r"im C:\Temp"
    assert glossary.apply_replacements("eine gruppe") == r"eine \1 und \g<0>"


def test_applying_twice_changes_nothing(file: Path) -> None:
    """Regression: "hams" = "Hams App" turned an already corrected text into "Hams App App"."""
    _schreiben(file, '[replace]\n"hams" = "Hams App"\n"app" = "Hams App"\n', 5)
    once = glossary.apply_replacements("ich nutze hams")
    assert once == "ich nutze Hams App"
    assert glossary.apply_replacements(once) == once
    assert glossary.apply_replacements("die app und hams") == "die Hams App und Hams App"
    assert glossary.apply_replacements("Die Hams App") == "Die Hams App"
