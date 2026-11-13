"""Configuration validation for Hams."""

import ipaddress
import urllib.parse
from typing import Any

from hams.dictation_language import SETTINGS as DICTATION_SETTINGS
from hams.i18n import SETTINGS as LANGUAGE_SETTINGS


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

    if cfg.sample_rate not in (16000, 22050, 44100, 48000):
        errors.append(f"audio.sample_rate should be 16000, 22050, 44100 or 48000, got {cfg.sample_rate}")

    if cfg.channels not in (1, 2):
        errors.append(f"audio.channels should be 1 or 2, got {cfg.channels}")

    if cfg.max_recording_seconds <= 0 or cfg.max_recording_seconds > 3600:
        errors.append(f"audio.max_recording_seconds should be between 1 and 3600, got {cfg.max_recording_seconds}")

    if cfg.model_size not in ("tiny", "base", "small", "medium", "large-v3", "turbo"):
        errors.append(f"model.size should be a valid Whisper model size, got {cfg.model_size}")

    if cfg.transcription_beam_size < 1 or cfg.transcription_beam_size > 10:
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
