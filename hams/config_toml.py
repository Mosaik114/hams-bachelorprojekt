"""Einstellungsdatei als Text bearbeiten: einen Wert in einen Abschnitt setzen, ohne Kommentare, Reihenfolge und
Zeilenenden zu verändern (geschrieben wird in config.py, atomar)."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("hams")


def _toml_scalar(value: Any) -> str:
    """Formatiert einen Python-Wert als TOML-Skalar."""
    if isinstance(value, bool):  # vor int prüfen — bool ist eine int-Unterklasse
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, (int, float)):
        return repr(value)
    return str(value)


def _trailing_comment(rest: str) -> str:
    """Gibt einen nachgestellten Kommentar samt führendem Weißraum zurück.

    Ein `#` innerhalb eines Strings zählt nicht als Kommentarbeginn.
    """
    in_string = False
    escaped = False
    for idx, char in enumerate(rest):
        if escaped:
            escaped = False
            continue
        if in_string and char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if char == "#" and not in_string:
            begin = idx
            while begin > 0 and rest[begin - 1] in " \t":
                begin -= 1
            return rest[begin:].rstrip("\r\n")
    return ""


def _toml_setzen(text: str, section: str, key: str, value: Any) -> str:
    """`key = value` in `[section]` setzen – reine Textänderung (Kommentare, Reihenfolge, Zeilenenden bleiben)."""
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in text else "\n"
    key_re = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)(.*)$")
    section_start, section_end, target = _abschnitt_finden(lines, section, key_re)
    rhs = _toml_scalar(value)
    if target is not None:
        original = lines[target]
        bare = original.rstrip("\r\n")
        line_eol = original[len(bare) :] or eol
        match = key_re.match(bare)
        if match is None:  # oben bereits geprüft – kann nicht eintreten
            raise ValueError(f"Zeile {target} passt nicht mehr zum Schlüssel")
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
        logger.info("Config-Schlüssel '%s' in [%s] ergänzt", key, section)
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += eol
        if lines and lines[-1].strip():
            lines.append(eol)
        lines.append(f"[{section}]{eol}")
        lines.append(f"{key} = {rhs}{eol}")
        logger.info("Config-Abschnitt [%s] mit '%s' angelegt", section, key)
    return "".join(lines)


def _abschnitt_finden(lines: list[str], section: str, key_re: re.Pattern) -> tuple[int | None, int | None, int | None]:
    """(Beginn, Ende, Zeile des Schlüssels) des Abschnitts `[section]`; None, wo es nichts gibt."""
    header_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    current: str | None = None
    section_start: int | None = None
    section_end: int | None = None
    target: int | None = None
    for idx, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        header = header_re.match(bare)
        if header is not None:
            if current == section and section_end is None:
                section_end = idx
            current = header.group(1).strip()
            if current == section and section_start is None:
                section_start = idx
            continue
        if current == section and target is None and key_re.match(bare) is not None:
            target = idx
    if section_start is not None and section_end is None:
        section_end = len(lines)
    return section_start, section_end, target
