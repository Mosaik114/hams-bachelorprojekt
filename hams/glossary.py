"""Glossar: Wörter, die Hams immer richtig schreiben soll (Namen, islamische Begriffe, Anime).

Zwei Wege, die sich ergänzen:
- `hotwords()` – die Wörter als Hinweis an Whisper; es erkennt sie dadurch öfter richtig.
- `apply_replacements()` – feste Korrekturen „falsch = richtig“ für Verhörer, die immer gleich kommen.
Dazu `cleanup_hint()`: ein Satz für die Ollama-Bereinigung, damit sie die Schreibweisen nicht „verbessert“.

Die Datei liegt neben der Konfiguration (`glossar.toml` im Ordner `Hams` unter %APPDATA% bzw. in
~/.config/hams) und wird beim ersten Zugriff aus einer Vorlage angelegt: `glossar.default.toml` (deutsch kommentiert)
fuer deutsches Diktat, sonst `glossar.default.en.toml` (englisch, ohne deutsche Schreibweisen wie „Pull-Request“).
Beide Schluesselpaare gelten in jeder Datei: `woerter`/`ersetzen` und `words`/`replace`. Sie wird neu gelesen,
sobald sie sich ändert – ohne Neustart. Eine kaputte Datei legt nichts lahm: dann gilt ein leeres Glossar,
und das Log sagt, warum.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import tomllib

from hams import dictation_language
from hams.config import APP_DIR, CFG, CONFIG_PATH, logger

DEFAULT_PATH = APP_DIR / "glossar.default.toml"
#: Vorlage fuer jedes andere Diktat als Deutsch.
DEFAULT_PATH_EN = APP_DIR / "glossar.default.en.toml"
_cache: dict = {"path": None, "mtime": None, "words": (), "replacements": ()}


def glossary_path() -> Path:
    return CONFIG_PATH.parent / "glossar.toml"


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
        logger.info("Glossar angelegt: %s", path)
    except OSError:
        logger.warning("Glossar konnte nicht angelegt werden: %s", path)


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
            words, replacements = _parse(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, tomllib.TOMLDecodeError, AttributeError) as exc:
            logger.warning("Glossar %s nicht lesbar, wird ignoriert: %s", path, exc)
    _cache.update(path=path, mtime=mtime, words=words, replacements=replacements)
    logger.info("Glossar geladen: %d Wörter, %d Korrekturen", len(words), len(replacements))
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
    liste = words()
    frame = dictation_language.hotword_frame(language)
    return frame.format(", ".join(liste)) if liste and frame else None


def apply_replacements(text: str) -> str:
    for pattern, right in _load()["replacements"]:
        text = pattern.sub(right, text)
    return text


def cleanup_hint(language: str | None) -> str:
    """Satz für die Bereinigung: diese Schreibweisen gelten als richtig und bleiben, wie sie sind."""
    return dictation_language.glossary_hint(language, words())
