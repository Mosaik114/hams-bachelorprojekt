"""Glossar: Wörter, die Hams immer richtig schreiben soll (Namen, islamische Begriffe, Anime).

Zwei Wege, die sich ergänzen:
- `hotwords()` – die Wörter als Hinweis an Whisper; es erkennt sie dadurch öfter richtig.
- `apply_replacements()` – feste Korrekturen „falsch = richtig“ für Verhörer, die immer gleich kommen.
Dazu `cleanup_hint()`: ein Satz für die Ollama-Bereinigung, damit sie die Schreibweisen nicht „verbessert“.

Die Datei liegt neben der Konfiguration (`glossary.toml` im Ordner `Hams` unter %APPDATA% bzw. in
~/.config/hams; aeltere Installationen behalten ihr `glossar.toml`, das weiter gilt) und wird beim ersten Zugriff
aus einer Vorlage angelegt: `glossar.default.toml` (deutsch kommentiert)
fuer deutsches Diktat, sonst `glossar.default.en.toml` (englisch, ohne deutsche Schreibweisen wie „Pull-Request“).
Beide Schluesselpaare gelten in jeder Datei: `woerter`/`ersetzen` und `words`/`replace`. Sie wird bei jedem Diktat
auf Änderungen geprüft und dann neu gelesen – ohne Neustart. `open_in_editor()` öffnet sie im Standard-Editor.
Eine kaputte Datei legt nichts lahm: dann gilt ein leeres Glossar, und das Log sagt, warum.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from hams import dictation_language
from hams.config import APP_DIR, CFG, CONFIG_PATH, load_toml_file, logger

DEFAULT_PATH = APP_DIR / "glossar.default.toml"
#: Vorlage fuer jedes andere Diktat als Deutsch.
DEFAULT_PATH_EN = APP_DIR / "glossar.default.en.toml"
_cache: dict = {"path": None, "mtime": None, "words": (), "replacements": ()}


#: Der Dateiname neuer Installationen.
FILE_NAME = "glossary.toml"
#: Der deutsche Name aus Versionen bis 1.0; eine vorhandene Datei dieses Namens gilt weiter.
LEGACY_FILE_NAME = "glossar.toml"


def glossary_path() -> Path:
    legacy = CONFIG_PATH.parent / LEGACY_FILE_NAME
    return legacy if legacy.exists() else CONFIG_PATH.parent / FILE_NAME


def default_path() -> Path:
    """Die Vorlage fuer einen neuen Nutzer, passend zu seiner Diktiersprache."""
    german = dictation_language.glossary_default_language(CFG.transcription_language) == "de"
    return DEFAULT_PATH if german else DEFAULT_PATH_EN


def _ensure_file(path: Path) -> None:
    source = default_path()
    if path.exists() or not source.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
        logger.info("Glossary created: %s", path)
    except OSError:
        logger.warning("Glossary could not be created: %s", path)


def open_in_editor(*, platform: str | None = None, launch=None) -> Path:
    """Öffnet das Glossar im Standard-Editor; legt es vorher aus der Vorlage an, falls es fehlt.

    Raises:
        OSError: die Datei ließ sich nicht anlegen oder kein Programm öffnen.
    """
    path = glossary_path()
    _ensure_file(path)
    if not path.exists():
        # Ohne Vorlage (kaputte Installation) wenigstens eine leere Datei, damit der Editor etwas öffnet.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("words = []\n\n[replace]\n", encoding="utf-8")
    platform = platform or sys.platform
    if launch is None:
        launch = os.startfile if platform == "win32" else _xdg_open  # type: ignore[attr-defined]
    launch(str(path))
    logger.info("Glossary opened in the editor")
    return path


def _xdg_open(path: str) -> None:
    subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603, S607


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _table(value) -> dict:
    return value if isinstance(value, dict) else {}


def _parse(data: dict) -> tuple[tuple[str, ...], tuple[tuple[re.Pattern, str], ...]]:
    listed = _list(data.get("woerter")) + _list(data.get("words"))
    words = tuple(w.strip() for w in listed if isinstance(w, str) and w.strip())
    replacements = []
    table = {**_table(data.get("ersetzen")), **_table(data.get("replace"))}
    # längste zuerst, damit „Insha Allah“ vor „Allah“ greift
    pairs = [(k, v) for k, v in table.items() if isinstance(v, str) and k.strip()]
    for wrong, right in sorted(pairs, key=lambda p: -len(p[0])):
        pattern = re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, wrong.split())) + r"(?!\w)", re.IGNORECASE)
        replacements.append((pattern, right))
    return words, tuple(replacements)


def _load() -> dict:
    path = glossary_path()
    _ensure_file(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache
    words, replacements = (), ()
    if mtime is not None:
        try:
            words, replacements = _parse(load_toml_file(path))
        except (OSError, ValueError, AttributeError) as exc:
            # ValueError: kaputtes TOML oder keine UTF-8-Datei (`TomlEncodingError`). Das leere Ergebnis
            # gilt bis zur naechsten Aenderung der Datei, die Warnung steht deshalb nur einmal im Log.
            logger.warning("Glossary %s unreadable, ignored: %s", path, exc)
    _cache.update(path=path, mtime=mtime, words=words, replacements=replacements)
    logger.info("Glossary loaded: %d words, %d replacements", len(words), len(replacements))
    return _cache


def words() -> tuple[str, ...]:
    return _load()["words"]


#: Der deutsche Rahmensatz, wie er vor der Sprachwahl hiess; alle Sprachen in `dictation_language.HOTWORD_FRAMES`.
HOTWORD_FRAME = dictation_language.HOTWORD_FRAMES["de"]


def hotwords(language: str | None) -> str | None:
    """Hinweis für Whisper (faster-whisper `hotwords`) in der diktierten Sprache.

    None, wenn das Glossar leer ist oder es für die Sprache keinen Rahmensatz gibt (siehe
    `dictation_language.hotword_frame`).
    """
    word_list = words()
    frame = dictation_language.hotword_frame(language)
    return frame.format(", ".join(word_list)) if word_list and frame else None


def apply_replacements(text: str) -> str:
    r"""Applies the fixed corrections "wrong = right".

    `right` is inserted literally: a value like `C:\Temp` or `\1` is text, not a regex template. And a
    match that already sits inside the correct spelling stays as it is, so applying the corrections to a
    text that has them (`hams` = `Hams App` on "Hams App") changes nothing.
    """
    for pattern, right in _load()["replacements"]:
        text = _replace(pattern, right, text)
    return text


def _replace(pattern: re.Pattern, right: str, text: str) -> str:
    done = [(m.start(), m.end()) for m in re.finditer(re.escape(right), text)] if right else []

    def substitute(match: re.Match) -> str:
        if any(start <= match.start() and match.end() <= end for start, end in done):
            return match.group(0)
        return right

    return pattern.sub(substitute, text)


def cleanup_hint(language: str | None) -> str:
    """Satz für die Bereinigung: diese Schreibweisen gelten als richtig und bleiben, wie sie sind."""
    return dictation_language.glossary_hint(language, words())
