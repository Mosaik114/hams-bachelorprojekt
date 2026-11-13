"""Help and support from inside the app: documentation links, the log folder, a diagnostic report.

Used by *Settings → About & help* and the tray menu. The diagnostic report is
the self-test of ``Hams.exe --self-test`` (see `self_test`) plus the end of the
log, written to ``hams-report.txt`` in the data folder, so a bug report needs no
terminal. Neither contains dictated text: the self-test lists versions and
paths, and the log records text lengths only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from hams import app_paths, self_test, updates
from hams.config import LOG_DIR, logger

DOCS_URL = "https://github.com/Mosaik114/hams-bachelorprojekt/blob/main/docs/"
USER_GUIDE_URL = DOCS_URL + "user-guide.md"
TROUBLESHOOTING_URL = DOCS_URL + "troubleshooting.md"
REPORT_NAME = "hams-report.txt"
LOG_NAME = "hams.log"
#: How much of the log goes into the report: enough for the last start and a few dictations.
LOG_TAIL_LINES = 150
#: The Tk check creates a second Tk root; inside the running app, and off the Tk thread, it must not.
IN_APP_SKIPPED_CHECKS = frozenset({"tk"})


def open_url(url: str, *, opener: Callable[[str], object] | None = None) -> bool:
    """Opens a link in the browser. False if that failed (logged, never raised)."""
    try:
        (opener or webbrowser.open)(url)
    except Exception:  # noqa: BLE001 - a missing browser must not break the menu
        logger.exception("Could not open %s", url)
        return False
    return True


def open_user_guide(*_args) -> bool:
    """Tray and settings: the user guide on GitHub. Accepts pystray's (icon, item) arguments."""
    return open_url(USER_GUIDE_URL)


def open_troubleshooting(*_args) -> bool:
    return open_url(TROUBLESHOOTING_URL)


def _xdg_open(path: str) -> None:
    subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603, S607


def open_path(path: Path, *, platform: str | None = None, launch: Callable[[str], object] | None = None) -> None:
    """Opens a file or folder with its default program (Explorer, Notepad, xdg-open).

    Raises:
        OSError: no program could be started.
    """
    platform = platform or sys.platform
    if launch is None:
        launch = os.startfile if platform == "win32" else _xdg_open  # type: ignore[attr-defined]
    launch(str(path))


def log_dir() -> Path:
    return LOG_DIR


def open_log_folder(*, launch: Callable[[str], object] | None = None) -> Path:
    """Opens the log folder; creates it first so there is something to open.

    Raises:
        OSError: the folder could not be created or opened.
    """
    folder = log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    open_path(folder, launch=launch)
    return folder


def report_path() -> Path:
    """``%LOCALAPPDATA%\\Hams\\hams-report.txt`` (Linux: ``~/.local/share/hams/hams-report.txt``)."""
    return app_paths.local_data_dir() / REPORT_NAME


def in_app_checks() -> tuple:
    return tuple((name, check) for name, check in self_test.CHECKS if name not in IN_APP_SKIPPED_CHECKS)


def log_tail(path: Path, lines: int = LOG_TAIL_LINES) -> list[str]:
    """The last lines of the log, or an empty list if there is none yet."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def write_report(path: Path | None = None, *, checks: tuple | None = None, now: datetime | None = None) -> tuple:
    """Runs the self-test into the report file and appends the end of the log.

    Returns ``(path, failures)``. Meant for a worker thread: it imports the
    speech backend and can take a few seconds.

    Raises:
        OSError: the file could not be written.
    """
    path = path or report_path()
    checks = in_app_checks() if checks is None else checks
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        print(f"Hams {updates.app_version()} diagnostic report, {stamp}", file=stream)
        print("Contains versions, paths and device names; never dictated text. Check it before posting.", file=stream)
        print("", file=stream)
        failures = self_test.run(stream, checks)
        tail = log_tail(log_dir() / LOG_NAME)
        print("", file=stream)
        print(f"--- last {len(tail)} lines of {LOG_NAME} ---", file=stream)
        for line in tail:
            print(line, file=stream)
    logger.info("Diagnostic report written: %s (%d failed checks)", path, failures)
    return path, failures
