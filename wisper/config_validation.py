"""Configuration validation for Wisper."""

from typing import Any


class ConfigValidationError(ValueError):
    """Raised when configuration values are invalid."""
    pass


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

    if not cfg.ollama_url.startswith("http://") and not cfg.ollama_url.startswith("https://"):
        errors.append(f"ollama.url should start with http:// or https://, got {cfg.ollama_url}")

    return errors
