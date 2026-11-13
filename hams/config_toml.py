"""Einstellungsdatei als Text bearbeiten: einen Wert in einen Abschnitt setzen, ohne Kommentare, Reihenfolge und
Zeilenenden zu verändern (geschrieben wird in config.py, atomar).

Die Änderung ist reine Textarbeit und kennt deshalb nur das Übliche: Abschnitte
als `[name]` (auch mit Kommentar dahinter), Schlüssel als `key`, `"key"` oder
`'key'`. Was sie nicht versteht – ein Schlüssel als Inline-Tabelle, ein
punktierter Schlüssel ausserhalb seines Abschnitts, ein mehrzeiliger String –
fängt `verify_toml_value` ab: der neue Text wird mit tomllib gelesen, bevor er
die Datei ersetzen darf. Lieber nicht speichern als eine Datei hinterlassen, die
beim nächsten Start alle Einstellungen verwirft.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[import-not-found]

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


#: `[name]` oder `[[name]]`, danach höchstens ein Kommentar.
_HEADER_RE = re.compile(r"^\s*\[(?P<array>\[)?(?P<name>[^\[\]]+)\](?(array)\])\s*(?:#.*)?$")

#: Kurzformen, die TOML in einem einfachen String vorsieht.
_SHORT_ESCAPES = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r", '"': '\\"', "\\": "\\\\"}


def _toml_string(value: str) -> str:
    """Ein TOML-Basic-String: Anführungszeichen, Backslash und Steuerzeichen maskiert."""
    out = []
    for char in value:
        if char in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _toml_scalar(value: Any) -> str:
    """Formatiert einen Python-Wert als TOML-Skalar."""
    if isinstance(value, bool):  # vor int prüfen — bool ist eine int-Unterklasse
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (int, float)):
        return repr(value)
    return str(value)


def _comment_start(rest: str) -> int:
    """Index des `#`, mit dem der Kommentar beginnt, oder -1. Ein `#` in einem String zählt nicht."""
    quote = ""
    escaped = False
    for idx, char in enumerate(rest):
        if escaped:
            escaped = False
        elif quote == '"' and char == "\\":
            escaped = True
        elif quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "#":
            return idx
    return -1


def _trailing_comment(rest: str) -> str:
    """Gibt einen nachgestellten Kommentar samt führendem Weißraum zurück."""
    begin = _comment_start(rest)
    if begin < 0:
        return ""
    while begin > 0 and rest[begin - 1] in " \t":
        begin -= 1
    return rest[begin:].rstrip("\r\n")


def _header_name(line: str) -> str | None:
    """Der Name eines Abschnittskopfs (`[ a . "b" ]` → `a.b`), `[[name]]` als `[[name]]`; sonst None."""
    match = _HEADER_RE.match(line)
    if match is None:
        return None
    name = ".".join(part.strip().strip("\"'") for part in match.group("name").split("."))
    return f"[[{name}]]" if match.group("array") else name


def _key_pattern(key: str) -> re.Pattern:
    """Die Zeile `key = …`, auch mit gequotetem Schlüssel."""
    escaped = re.escape(key)
    return re.compile(rf"""^(\s*(?:{escaped}|"{escaped}"|'{escaped}')\s*=\s*)(.*)$""")


def _set_toml_value(text: str, section: str, key: str, value: Any) -> str:
    """`key = value` in `[section]` setzen – reine Textänderung (Kommentare, Reihenfolge, Zeilenenden bleiben)."""
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in text else "\n"
    key_re = _key_pattern(key)
    section_start, section_end, target = _find_section(lines, section, key_re)
    rhs = _toml_scalar(value)
    if target is not None:
        original = lines[target]
        bare = original.rstrip("\r\n")
        line_eol = original[len(bare) :] or eol
        match = key_re.match(bare)
        if match is None:  # oben bereits geprüft – kann nicht eintreten
            raise ValueError(f"line {target} no longer matches the key")
        lines[target] = match.group(1) + rhs + _trailing_comment(match.group(2)) + line_eol
    elif section_start is not None:
        insert_at = section_end if section_end is not None else len(lines)
        # Hinter der letzten inhaltlichen Zeile des Abschnitts einfügen,
        # damit die Leerzeile vor dem nächsten Abschnitt erhalten bleibt.
        while insert_at > section_start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        if insert_at > 0 and not lines[insert_at - 1].endswith(("\n", "\r")):
            lines[insert_at - 1] += eol
        lines.insert(insert_at, f"{key} = {rhs}{eol}")
        logger.info("Config key '%s' added to [%s]", key, section)
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += eol
        if lines and lines[-1].strip():
            lines.append(eol)
        lines.append(f"[{section}]{eol}")
        lines.append(f"{key} = {rhs}{eol}")
        logger.info("Config section [%s] created with '%s'", section, key)
    return "".join(lines)


def _find_section(lines: list[str], section: str, key_re: re.Pattern) -> tuple[int | None, int | None, int | None]:
    """(Beginn, Ende, Zeile des Schlüssels) des Abschnitts `[section]`; None, wo es nichts gibt."""
    current: str | None = None
    section_start: int | None = None
    section_end: int | None = None
    target: int | None = None
    for idx, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        name = _header_name(bare)
        if name is not None:
            if current == section and section_end is None:
                section_end = idx
            current = name
            if current == section and section_start is None:
                section_start = idx
            continue
        if current == section and target is None and key_re.match(bare) is not None:
            target = idx
    if section_start is not None and section_end is None:
        section_end = len(lines)
    return section_start, section_end, target


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
