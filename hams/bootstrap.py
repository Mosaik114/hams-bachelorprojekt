"""What Hams does once at start, before any window: the log, the settings and the colour scheme.

`hams.config` only defines things; this module does them, in this order. The log comes first: loading the settings
reports invalid values through it, and from the autostart there is no console to see them otherwise.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from hams import audio_devices, config
from hams.config import CFG
from hams.ui_kit import Theme


def setup_logging() -> logging.Logger:
    """The application log: a rotating file and the console from WARNING up. Called again, it changes nothing.

    Without a usable log folder (read-only, drive missing) it goes on with the console alone: a start that failed
    here would mean no window and no message.
    """
    log = logging.getLogger("hams")
    log.setLevel(logging.DEBUG)
    if log.handlers:
        return log
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.addHandler(console_handler)
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            config.LOG_DIR / "hams.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("No log file in %s (%s) - logging to the console only", config.LOG_DIR, exc)
        return log
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    log.addHandler(file_handler)
    return log


def load() -> None:
    """Log, settings and colour scheme, without creating any file (the self-test and the GPU installer use this)."""
    setup_logging()
    config.reload()
    # Resolved now and again before every recording: device indexes move when a microphone is plugged in or out.
    CFG.mic_device = audio_devices.resolve_mic_device(CFG.mic_device_raw)
    # Before the first widget: every drawing routine reads the palette through `Theme`.
    Theme.apply(CFG.appearance)


def start() -> str:
    """The application start: creates or migrates the user file once, then `load()`.

    Returns what `config.ensure_user_config` did (`CONFIG_EXISTING`, `CONFIG_CREATED`, ...).
    """
    setup_logging()
    state = config.ensure_user_config()
    load()
    return state
