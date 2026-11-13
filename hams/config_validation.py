"""Typed reading of the config file and the check of the Ollama address."""

import ipaddress
import math
import urllib.parse
from collections.abc import Callable
from typing import Any

#: Ranges for the typed readers below.
MAX_RECORDING_SECONDS = 3600
#: Upper bound for the Ollama timeouts (seconds); a larger value is a typo, not a wish.
MAX_OLLAMA_TIMEOUT = 3600
#: Upper bound for `ollama.cleanup_temperature`; Ollama's useful range ends at 2.
MAX_CLEANUP_TEMPERATURE = 2.0
#: Upper bound for `clipboard.paste_timeout` (seconds).
MAX_PASTE_TIMEOUT = 10.0
#: Upper bound for `output.keep_days`: ten years.
MAX_KEEP_DAYS = 3650


def ollama_url_problem(url: str) -> str:
    """Warum diese Ollama-Adresse nicht benutzt werden darf - oder "".

    Hams schickt jedes Diktat an diese Adresse. Unverschluesselt darf sie
    deshalb nur auf den eigenen Rechner zeigen; ein fremder Rechner muss
    https sprechen, sonst liest jeder im Netz mit. Andere Schemata wie
    `file:` wuerden `urlopen` lokale Dateien lesen lassen.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return f"ollama.url is not a valid URL, got {url}"
    if parts.scheme not in ("http", "https") or not host:
        return f"ollama.url should start with http:// or https:// and name a host, got {url}"
    if parts.scheme == "http" and not _is_loopback(host):
        return f"ollama.url must use https for another computer, got {url}"
    return ""


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class SectionReader:
    """Typed, forgiving access to one table of a parsed TOML file.

    TOML has types, but a hand-edited file can still carry the wrong one:
    `timeout_cleanup = "30s"`, `hotkeys = "x"` or `check = "false"`. The old
    loader called `int()`/`bool()` on such values. That either raised during
    the import of `hams.config` (the windowed app then never started, without
    a single log line) or, worse, turned the string "false" into True.

    Every getter here returns the value only if it has the right type (a bool
    must be a real TOML boolean) and lies in its range. Anything else is
    reported through `warn` and the previous value stays: the one from the
    template, or the code default. Nothing in here raises.
    """

    def __init__(self, data: dict, name: str, warn: Callable[[str], None]) -> None:
        self.name = name
        self._warn = warn
        values = data.get(name, {})
        if not isinstance(values, dict):
            warn(f"[{name}] should be a table, got {_shown(values)} - section ignored")
            values = {}
        self.values: dict = values

    def __contains__(self, key: str) -> bool:
        return key in self.values

    def raw(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def reject(self, key: str, expected: str, value: Any, fallback: Any) -> Any:
        """Report a wrong value and return the fallback."""
        self._warn(f"{self.name}.{key} should be {expected}, got {_shown(value)} - using {fallback!r}")
        return fallback

    def boolean(self, key: str, current: bool, invalid: bool | None = None) -> bool:
        """A real TOML boolean.

        The quoted words "true" and "false" are a common slip and mean exactly
        one thing, so they count, with a warning: `check = "false"` must switch
        the update check off, not on as `bool("false")` did. Anything else falls
        back to `current`, or to `invalid` where an unclear value must lean one
        way (no network access when in doubt).
        """
        if key not in self.values:
            return current
        value = self.values[key]
        if isinstance(value, bool):
            return value
        word = value.strip().lower() if isinstance(value, str) else None
        if word in ("true", "false"):
            return self.reject(key, "true or false without quotes", value, word == "true")
        return self.reject(key, "true or false", value, current if invalid is None else invalid)

    def integer(self, key: str, current: int, low: int, high: int) -> int:
        if key not in self.values:
            return current
        value = self.values[key]
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            return self.reject(key, f"a whole number from {low} to {high}", value, current)
        return value

    def number(self, key: str, current: float, low: float, high: float) -> float:
        if key not in self.values:
            return current
        value = self.values[key]
        valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if not valid or not low <= value <= high:
            return self.reject(key, f"a number from {low} to {high}", value, current)
        return float(value)

    def text(self, key: str, current: str) -> str:
        if key not in self.values:
            return current
        value = self.values[key]
        if isinstance(value, str):
            return value
        return self.reject(key, "a text in quotes", value, current)


def _shown(value: Any) -> str:
    """A short description of a config value for a warning; long values are cut."""
    text = repr(value)
    if len(text) > 60:
        text = text[:57] + "..."
    return f"{type(value).__name__} {text}"
