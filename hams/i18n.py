"""User interface language: message catalogs, language choice and lookup.

Deliberately small and dependency-free. Every user-visible string lives in one of the two catalogs
(`messages_de.py`, `messages_en.py`), keyed by a stable English identifier. `t()` looks a key up in the active
language and fills its placeholders with `str.format`.

The language is chosen once at startup (`set_language`, called from `main`). Switching it at runtime would mean
rebuilding every open window, so the settings window asks for a restart instead.

Plurals: a key with variants stores them as ``<key>.one`` and ``<key>.other``; `tn()` picks one by count. German
and English share that rule, so two forms are enough.
"""

from __future__ import annotations

import os
import sys

from hams.messages_de import MESSAGES as _DE
from hams.messages_en import MESSAGES as _EN

#: Accepted values of `ui.language`. "auto" follows the operating system.
SETTINGS = ("auto", "de", "en")
SUPPORTED = ("de", "en")
DEFAULT_SETTING = "auto"
#: Fallback for any system language other than German.
FALLBACK = "en"

CATALOGS: dict[str, dict[str, str]] = {"de": _DE, "en": _EN}

_active: str | None = None

#: Primary language id of German in a Windows LANGID (the low ten bits).
_LANG_GERMAN = 0x07
_LOCALE_VARIABLES = ("LC_ALL", "LC_MESSAGES", "LANG")


def normalise_setting(value: object) -> str:
    """A valid `ui.language` value; anything unknown becomes "auto"."""
    text = str(value or "").strip().lower()
    return text if text in SETTINGS else DEFAULT_SETTING


def _windows_ui_language() -> str:
    """ "de" or "en" from GetUserDefaultUILanguage; "" when it can't be read."""
    try:
        import ctypes

        langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except (AttributeError, OSError):
        return ""
    return "de" if langid & 0x3FF == _LANG_GERMAN else FALLBACK


def _posix_ui_language(environ=None) -> str:
    """ "de" or "en" from the first set locale variable; "" when none is set."""
    environ = os.environ if environ is None else environ
    for name in _LOCALE_VARIABLES:
        value = (environ.get(name) or "").strip()
        if value:
            return "de" if value.lower().startswith("de") else FALLBACK
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


def t_for(lang: str, key: str, **values) -> str:
    """Looks a key up in a given language; falls back to English, then to the key itself."""
    text = CATALOGS.get(lang, _EN).get(key)
    if text is None:
        text = _EN.get(key, key)
    return text.format(**values) if values else text


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
