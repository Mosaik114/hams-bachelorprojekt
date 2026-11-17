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
auf Änderungen geprüft und dann neu gelesen – ohne Neustart. Bearbeitet wird sie im Glossar-Fenster
(`glossary_window.py`) über `GlossaryFile`, das Kommentare und Reihenfolge erhält; `open_in_editor()` öffnet sie für
alle, die lieber den Text bearbeiten, im Standard-Editor.
Eine kaputte Datei legt nichts lahm: dann gilt ein leeres Glossar, und das Log sagt, warum.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

from hams import dictation_language
from hams.config import APP_DIR, CFG, CONFIG_PATH, load_toml_file, logger
from hams.config_toml import read_toml_text

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


# ---------------------------------------------------------------- editing (the glossary window)


class GlossaryUnreadable(ValueError):
    """The file is broken (no valid TOML, not UTF-8); the window leaves it alone and offers it as a file."""


class GlossaryFile:
    """The glossary file opened for editing, comments and order kept (tomlkit).

    The file stays the one place the glossary lives: the window (`glossary_window.py`) changes it through this
    class and saves at once, and the next dictation reads it like any edit by hand. Both spellings of the keys
    count (`woerter`/`words`, `ersetzen`/`replace`); a new entry goes where the file already keeps its entries.

    Raises `GlossaryUnreadable` on opening a broken file, which is then never written.
    """

    WORD_KEYS = ("woerter", "words")
    REPLACE_KEYS = ("ersetzen", "replace")

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or glossary_path()
        if path is None:
            _ensure_file(self.path)
        try:
            text = read_toml_text(self.path) if self.path.exists() else ""
            self.doc = tomlkit.parse(text)
        except (OSError, ValueError, TOMLKitError) as exc:
            raise GlossaryUnreadable(str(exc)) from exc

    # ---------------------------------------------------------- reading

    def _arrays(self) -> list:
        return [self.doc[key] for key in self.WORD_KEYS if isinstance(self.doc.get(key), list)]

    def _tables(self) -> list:
        return [self.doc[key] for key in self.REPLACE_KEYS if isinstance(self.doc.get(key), dict)]

    @property
    def words(self) -> list[str]:
        """The words in file order (texts only; anything else in the list is left as it is)."""
        return [str(word) for array in self._arrays() for word in array if isinstance(word, str) and word.strip()]

    @property
    def replacements(self) -> list[tuple[str, str]]:
        """The fixed corrections "wrong = right" in file order."""
        return [
            (str(wrong), str(right))
            for table in self._tables()
            for wrong, right in table.items()
            if isinstance(right, str)
        ]

    def has_word(self, word: str, *, besides: str | None = None) -> bool:
        """Whether `word` is in the list already (ignoring case), not counting `besides` (the entry being edited)."""
        key = word.strip().casefold()
        return any(w.casefold() == key and w != besides for w in self.words)

    def has_replacement(self, wrong: str, *, besides: str | None = None) -> bool:
        key = " ".join(wrong.split()).casefold()
        return any(" ".join(w.split()).casefold() == key and w != besides for w, _ in self.replacements)

    # ---------------------------------------------------------- changing

    def _word_array(self):
        arrays = self._arrays()
        if arrays:
            return arrays[0]
        array = tomlkit.array()
        array.multiline(True)
        self.doc["words"] = array
        return array

    def _replace_table(self):
        tables = self._tables()
        if tables:
            return tables[0]
        table = tomlkit.table()
        self.doc["replace"] = table
        return table

    def add_word(self, word: str) -> None:
        self._word_array().append(word.strip())

    def change_word(self, old: str, new: str) -> None:
        """Replaces `old` in place, so its position (and with it its priority for Whisper) stays."""
        for array in self._arrays():
            for index, item in enumerate(array):
                if item == old:
                    array[index] = new.strip()
                    return
        self.add_word(new)

    def remove_word(self, word: str) -> None:
        for array in self._arrays():
            for index, item in enumerate(array):
                if item == word:
                    del array[index]
                    return

    def set_replacement(self, wrong: str, right: str, *, old: str | None = None) -> None:
        """Adds "wrong = right", or changes the entry `old` (then the new one keeps its place where possible)."""
        wrong, right = " ".join(wrong.split()), right.strip()
        for table in self._tables():
            if old is not None and old in table:
                if old == wrong:
                    table[wrong] = right
                    return
                del table[old]
                break
        for table in self._tables():
            if wrong in table:
                table[wrong] = right
                return
        self._replace_table()[wrong] = right

    def remove_replacement(self, wrong: str) -> None:
        for table in self._tables():
            if wrong in table:
                del table[wrong]
                return

    def save(self) -> None:
        """Writes the file atomically (UTF-8). Raises OSError."""
        text = tomlkit.dumps(self.doc)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, self.path)
        logger.info("Glossary saved: %d words, %d replacements", len(self.words), len(self.replacements))
