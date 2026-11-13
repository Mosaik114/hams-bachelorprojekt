"""Configuration validation for Hams."""

import ipaddress
import math
import urllib.parse
from collections.abc import Callable
from typing import Any

from hams.dictation_language import SETTINGS as DICTATION_SETTINGS
from hams.i18n import SETTINGS as LANGUAGE_SETTINGS

#: Allowed values, shared by the typed readers below and by `validate_config`.
SAMPLE_RATES = (16000, 22050, 44100, 48000)
CHANNELS = (1, 2)
MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v3", "turbo")
MAX_RECORDING_SECONDS = 3600
MAX_BEAM_SIZE = 10
#: Upper bound for the Ollama timeouts (seconds); a larger value is a typo, not a wish.
MAX_OLLAMA_TIMEOUT = 3600
#: Upper bound for `ollama.cleanup_temperature`; Ollama's useful range ends at 2.
MAX_CLEANUP_TEMPERATURE = 2.0
#: Upper bound for `clipboard.paste_timeout` (seconds).
MAX_PASTE_TIMEOUT = 10.0


class ConfigValidationError(ValueError):
    """Raised when configuration values are invalid."""

    pass


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


def validate_config(cfg: Any) -> list[str]:
    """Validate configuration values.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []

    if cfg.sample_rate not in SAMPLE_RATES:
        errors.append(f"audio.sample_rate should be 16000, 22050, 44100 or 48000, got {cfg.sample_rate}")

    if cfg.channels not in CHANNELS:
        errors.append(f"audio.channels should be 1 or 2, got {cfg.channels}")

    if cfg.max_recording_seconds <= 0 or cfg.max_recording_seconds > MAX_RECORDING_SECONDS:
        errors.append(f"audio.max_recording_seconds should be between 1 and 3600, got {cfg.max_recording_seconds}")

    if cfg.model_size not in MODEL_SIZES:
        errors.append(f"model.size should be a valid Whisper model size, got {cfg.model_size}")

    if cfg.transcription_beam_size < 1 or cfg.transcription_beam_size > MAX_BEAM_SIZE:
        errors.append(f"transcription.beam_size should be between 1 and 10, got {cfg.transcription_beam_size}")

    if cfg.ollama_timeout_cleanup <= 0:
        errors.append(f"ollama.timeout_cleanup should be positive, got {cfg.ollama_timeout_cleanup}")

    if cfg.ollama_timeout_prompt <= 0:
        errors.append(f"ollama.timeout_prompt should be positive, got {cfg.ollama_timeout_prompt}")

    if cfg.clipboard_paste_timeout < 0:
        errors.append(f"clipboard.paste_timeout should be non-negative, got {cfg.clipboard_paste_timeout}")

    problem = ollama_url_problem(cfg.ollama_url)
    if problem:
        errors.append(problem)

    errors.extend(_dictation_problems(cfg))
    errors.extend(_ui_problems(cfg))
    return errors


def _dictation_problems(cfg: Any) -> list[str]:
    """Problems with `transcription.language`. Empty means "not set" and is resolved while loading."""
    language = getattr(cfg, "transcription_language", "")
    if language and language not in DICTATION_SETTINGS:
        return [f"transcription.language should be one of {', '.join(DICTATION_SETTINGS)}, got {language}"]
    return []


def _ui_problems(cfg: Any) -> list[str]:
    """Problems in the [ui] section."""
    language = getattr(cfg, "ui_language", "auto")
    if language not in LANGUAGE_SETTINGS:
        return [f"ui.language should be one of {', '.join(LANGUAGE_SETTINGS)}, got {language}"]
    return []


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

    def choice(self, key: str, current: Any, allowed: tuple) -> Any:
        """One of `allowed`, with the same type (16000.0 or "16000" is not 16000)."""
        if key not in self.values:
            return current
        value = self.values[key]
        if any(type(value) is type(option) and value == option for option in allowed):
            return value
        return self.reject(key, "one of " + ", ".join(map(str, allowed)), value, current)

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
