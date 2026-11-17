"""User interface language: message catalogs, language choice and lookup.

Deliberately small and dependency-free. Every user-visible string lives in the catalogs `messages_<language>.py`,
keyed by a stable English identifier; German is the source, the others carry exactly its keys and placeholders
(tests/test_i18n.py). `t()` looks a key up in the active language and fills its placeholders with `str.format`.

Arabic reads right to left. Tk has no bidirectional text of its own, but on Windows it draws each run of text
through the system, which shapes the letters and orders them, in a left-to-right paragraph. `t()` therefore wraps
Arabic messages in right-to-left embeddings (`right_to_left`), and the kit mirrors the layout (`ui_kit.Writing`). No
extra library is needed for it; on Linux, Tk draws Arabic unshaped, so there "auto" never picks it.

The language is chosen once at startup (`set_language`, called from `main`). Switching it at runtime would mean
rebuilding every open window, so the settings window asks for a restart instead.

Plurals: a key with variants stores them as ``<key>.one`` and ``<key>.other``; `tn()` picks one by count. Languages
with more forms (Russian, Arabic) word ``.other`` so that it fits any number ("Моделей: 3").
"""

from __future__ import annotations

import os
import re
import sys

from hams.messages_ar import MESSAGES as _AR
from hams.messages_de import MESSAGES as _DE
from hams.messages_en import MESSAGES as _EN
from hams.messages_es import MESSAGES as _ES
from hams.messages_fr import MESSAGES as _FR
from hams.messages_ru import MESSAGES as _RU
from hams.messages_tr import MESSAGES as _TR

#: The interface languages, in the order of the language choice (each named in itself there).
SUPPORTED = ("de", "en", "ru", "es", "fr", "ar", "tr")
#: Accepted values of `ui.language`. "auto" follows the operating system.
SETTINGS = ("auto", *SUPPORTED)
DEFAULT_SETTING = "auto"
#: Fallback for any system language Hams does not speak.
FALLBACK = "en"
#: Languages written right to left.
RTL = ("ar",)

CATALOGS: dict[str, dict[str, str]] = {"de": _DE, "en": _EN, "ru": _RU, "es": _ES, "fr": _FR, "ar": _AR, "tr": _TR}

_active: str | None = None

#: Primary language ids of a Windows LANGID (its low ten bits).
_WINDOWS_LANGUAGES = {0x07: "de", 0x09: "en", 0x19: "ru", 0x0A: "es", 0x0C: "fr", 0x01: "ar", 0x1F: "tr"}
_LOCALE_VARIABLES = ("LC_ALL", "LC_MESSAGES", "LANG")


def normalise_setting(value: object) -> str:
    """A valid `ui.language` value; anything unknown becomes "auto"."""
    text = str(value or "").strip().lower()
    return text if text in SETTINGS else DEFAULT_SETTING


def _windows_ui_language() -> str:
    """The Windows display language (GetUserDefaultUILanguage) if Hams speaks it, else English; "" if unreadable."""
    try:
        import ctypes

        langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except (AttributeError, OSError):
        return ""
    return _WINDOWS_LANGUAGES.get(langid & 0x3FF, FALLBACK)


def _posix_ui_language(environ=None) -> str:
    """The language of the first set locale variable if Hams speaks it, else English; "" when none is set.

    Arabic counts as English here: Tk on Linux draws it neither shaped nor right to left.
    """
    environ = os.environ if environ is None else environ
    for name in _LOCALE_VARIABLES:
        value = (environ.get(name) or "").strip()
        if value:
            language = value.lower()[:2]
            return language if language in SUPPORTED and language not in RTL else FALLBACK
    return ""


def detect_system_language(platform: str | None = None, environ=None) -> str:
    """The operating system's UI language, reduced to the supported ones."""
    platform = sys.platform if platform is None else platform
    found = _windows_ui_language() if platform == "win32" else _posix_ui_language(environ)
    return found or FALLBACK


def resolve_language(setting: object, platform: str | None = None, environ=None) -> str:
    """The concrete language for a `ui.language` value ("auto" asks the system)."""
    setting = normalise_setting(setting)
    if setting == "auto":
        return detect_system_language(platform, environ)
    return setting


def set_language(setting: object) -> str:
    """Activates a language for all following lookups. Returns the concrete language."""
    global _active
    _active = resolve_language(setting)
    return _active


def language() -> str:
    """The active language; resolved from the system on first use if nobody set it."""
    global _active
    if _active is None:
        _active = resolve_language(DEFAULT_SETTING)
    return _active


def is_rtl(lang: str | None = None) -> bool:
    """Whether a language (by default the active one) is written right to left."""
    return (language() if lang is None else lang) in RTL


#: Right-to-left embedding (U+202B). Windows' text drawing honours it; Tk passes it through unchanged.
RLE = "\u202b"
#: Letters of the right-to-left scripts (Arabic and Hebrew blocks, with their presentation forms).
_RTL_LETTER = re.compile("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefc]")
_WORDS = re.compile(r"(\s+)")


def right_to_left(text: str) -> str:
    """`text` laid out as a right-to-left paragraph by Windows' text drawing (Tk itself has no bidi).

    Tk draws every line (and every line it wraps itself) as a separate left-to-right paragraph. An embedding at
    the start of a line makes it read right to left: the full stop goes to the left end, a product name after
    an Arabic word stands to its left. Because Tk may wrap before any word, an embedding also opens before every
    word that begins or ends an Arabic stretch. Never between two Latin words: "Hams 1.3.4" must stay one run, or
    the two would swap places.
    """
    lines = []
    for line in text.split("\n"):
        parts, previous = [RLE], None
        for part in _WORDS.split(line):
            if not part or part.isspace():
                parts.append(part)
                continue
            arabic = bool(_RTL_LETTER.search(part))
            if previous is not None and (arabic or previous):
                parts.append(RLE)
            parts.append(part)
            previous = arabic
        lines.append("".join(parts))
    return "\n".join(lines)


def t_for(lang: str, key: str, **values) -> str:
    """Looks a key up in a given language; falls back to English, then to the key itself.

    Right-to-left text comes out embedded for Windows (`right_to_left`); elsewhere Tk cannot lay it out anyway.
    """
    text = CATALOGS.get(lang, _EN).get(key)
    if text is None:
        text = _EN.get(key, key)
    text = text.format(**values) if values else text
    return right_to_left(text) if lang in RTL and sys.platform == "win32" else text


def t(key: str, **values) -> str:
    """The message for `key` in the active language, with placeholders filled in."""
    return t_for(language(), key, **values)


def tn(key: str, count: int, **values) -> str:
    """Plural-aware lookup: `<key>.one` for exactly one, `<key>.other` otherwise. `{count}` is filled in."""
    form = "one" if count == 1 else "other"
    return t(f"{key}.{form}", count=count, **values)


def decimal(value: float, digits: int = 1) -> str:
    """A number with the decimal separator of the active language ("1,6" or "1.6")."""
    return f"{value:.{digits}f}".replace(".", t("number.decimal_separator"))


class Text:
    """Class attribute that is translated on every access.

    Class constants such as a hint text are evaluated at import time, before the language is known. As a
    descriptor, `TranscriptionSection.MODEL_HINT` stays a plain attribute for callers but always returns the text in the
    active language. A callable instead of a key builds a derived value (e.g. a table) the same way.
    """

    def __init__(self, source) -> None:
        self._source = source

    def __get__(self, _instance, _owner=None):
        return self._source() if callable(self._source) else t(self._source)
