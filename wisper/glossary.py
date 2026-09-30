"""Glossar: Wörter, die Hams immer richtig schreiben soll (Namen, islamische Begriffe, Anime).

Zwei Wege, die sich ergänzen:
- `hotwords()` – die Wörter als Hinweis an Whisper; es erkennt sie dadurch öfter richtig.
- `apply_replacements()` – feste Korrekturen „falsch = richtig“ für Verhörer, die immer gleich kommen.
Dazu `cleanup_hint()`: ein Satz für die Ollama-Bereinigung, damit sie die Schreibweisen nicht „verbessert“.

Die Datei liegt neben der Konfiguration (~/.config/Wisper/glossar.toml) und wird beim ersten Zugriff aus
`glossar.default.toml` angelegt. Sie wird neu gelesen, sobald sie sich ändert – ohne Neustart. Eine kaputte Datei
legt nichts lahm: dann gilt ein leeres Glossar, und das Log sagt, warum.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import tomllib

from wisper.config import APP_DIR, CONFIG_PATH, logger

DEFAULT_PATH = APP_DIR / "glossar.default.toml"
_cache: dict = {"path": None, "mtime": None, "words": (), "replacements": ()}


def glossary_path() -> Path:
    return CONFIG_PATH.parent / "glossar.toml"


def _ensure_file(path: Path) -> None:
    if path.exists() or not DEFAULT_PATH.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_PATH, path)
        logger.info("Glossar angelegt: %s", path)
    except OSError:
        logger.warning("Glossar konnte nicht angelegt werden: %s", path)


def _parse(data: dict) -> tuple[tuple[str, ...], tuple[tuple[re.Pattern, str], ...]]:
    words = tuple(w.strip() for w in data.get("woerter", []) if isinstance(w, str) and w.strip())
    replacements = []
    # längste zuerst, damit „Insha Allah“ vor „Allah“ greift
    pairs = [(k, v) for k, v in data.get("ersetzen", {}).items() if isinstance(v, str) and k.strip()]
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


#: Whisper nimmt den Hinweis als „vorigen Text“. Eine bloße Liste arabischer und englischer Wörter zog die Ausgabe
#: ins Englische („The Prophet“ statt „der Prophet“) – ein deutscher Rahmensatz hält sie beim Deutschen.
HOTWORD_FRAME = "Ein deutscher Text. Namen und Begriffe darin: {}."


def hotwords() -> str | None:
    """Hinweis für Whisper (faster-whisper `hotwords`); None, wenn das Glossar leer ist."""
    liste = words()
    return HOTWORD_FRAME.format(", ".join(liste)) if liste else None


def apply_replacements(text: str) -> str:
    for pattern, right in _load()["replacements"]:
        text = pattern.sub(right, text)
    return text


def cleanup_hint() -> str:
    """Satz für die Bereinigung: diese Schreibweisen gelten als richtig und bleiben, wie sie sind."""
    liste = words()
    if not liste:
        return ""
    return "Diese Wörter sind richtig geschrieben und bleiben genau so: " + ", ".join(liste) + "."
