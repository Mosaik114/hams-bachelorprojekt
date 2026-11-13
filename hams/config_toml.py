"""Einstellungsdatei lesen und einen Wert setzen, ohne Kommentare, Reihenfolge und Zeilenenden zu verändern.

Gesetzt wird mit tomlkit (comment-preserving), geschrieben in config.py, atomar. `verify_toml_value` liest das
Ergebnis vorher mit tomllib: lieber nicht speichern als eine Datei hinterlassen, die beim nächsten Start alle
Einstellungen verwirft.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

import tomlkit

logger = logging.getLogger("hams")


class TomlEncodingError(ValueError):
    """The file is not UTF-8 text, which TOML requires (e.g. saved as ANSI/cp1252 or UTF-16)."""


def read_toml_text(path: Path) -> str:
    """The text of a TOML file the user may have edited: UTF-8, with or without a byte order mark.

    Line endings stay as they are (the config is written back with them). Notepad and other editors
    save UTF-8 with a BOM, which tomllib rejects; it is dropped here.

    Raises:
        OSError: the file cannot be read.
        TomlEncodingError: the bytes are not UTF-8.
    """
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TomlEncodingError(
            f"{Path(path).name} is not saved as UTF-8 (byte {exc.start}); save it again as UTF-8"
        ) from exc


def load_toml_file(path: Path) -> dict:
    """`read_toml_text` parsed. Raises OSError or ValueError (TOMLDecodeError, TomlEncodingError)."""
    return tomllib.loads(read_toml_text(path))


def _set_toml_value(text: str, section: str, key: str, value: Any) -> str:
    r"""`key = value` in `[section]` (dotted: `model.compute_priority`), everything else as it was.

    tomlkit keeps comments, blank lines, key order and quoting. A missing key goes to the end of its table, a
    missing table to the end of the file. New lines get the file's own line ending (tomlkit writes `\n`).

    Raises:
        ValueError, TypeError, KeyError or tomlkit's TOMLKitError: the text is no TOML, or `section` names
        something that is not a table.
    """
    doc = tomlkit.parse(text)
    table: Any = doc
    for part in section.split("."):
        if part not in table:
            table[part] = tomlkit.table()
            logger.info("Config section [%s] created", section)
        table = table[part]
        if not isinstance(table, dict):
            raise TypeError(f"[{section}] is not a table")
    table[key] = value
    out = tomlkit.dumps(doc)
    if "\r\n" in text:
        out = out.replace("\r\n", "\n").replace("\n", "\r\n")
    return out


def verify_toml_value(text: str, section: str, key: str, value: Any) -> str:
    """Warum `text` nicht geschrieben werden darf – oder "".

    Der Text muss gültiges TOML sein und unter `[section] key` genau `value`
    tragen. Sonst hat die Textänderung etwas missverstanden (siehe Modulkopf).
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return f"the edited file would not be valid TOML ({exc})"
    table: Any = data
    for part in section.split("."):
        table = table.get(part) if isinstance(table, dict) else None
    stored = table.get(key) if isinstance(table, dict) else None
    if stored != value or type(stored) is not type(value):
        return f"the edited file would not hold {section}.{key} as intended"
    return ""
