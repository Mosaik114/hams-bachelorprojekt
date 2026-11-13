"""Optional integration: tell an external overlay that Hams is recording, and how loud.

Off unless `[integrations] cursor_dot = true`. The receiver is a separate desktop tool ("Mosaiks-Suche", not part
of Hams) that draws a small recording dot with a level bar at the mouse pointer. Nothing is sent while the option
is off, and with it on nothing leaves the computer:

- Windows: the registered window message `MosaiksSuche.HamsZustand`, posted to the top-level window titled
  `Mosaiks-Suche`. wParam carries the mode (0 off, 1 dictation, 2 prompt) and, from bit 8 on, the level 0..255 + 1;
  lParam the process id, so the overlay can drop the dot if Hams dies.
- Linux: the datagram `hams <mode> <level|-1> <pid>` to `$XDG_RUNTIME_DIR/mosaiks-suche.sock`.

Both are fire-and-forget: no receiver, no effect. The window handle is cached and looked up again at most every
`LOOKUP_SECONDS`, not on each of the 20 level updates per second.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import time
from pathlib import Path

from hams.config import CFG

WINDOW_TITLE = "Mosaiks-Suche"
MESSAGE_NAME = "MosaiksSuche.HamsZustand"
SOCKET_NAME = "mosaiks-suche.sock"
#: How often a missing receiver window is looked for again (seconds).
LOOKUP_SECONDS = 2.0

_MODES = {"recording": 1, "recording_prompt": 2}


def _mode(state: str) -> int:
    return _MODES.get(state, 0)


def notify(state: str, level: float | None = None) -> None:
    """Reports the main window's state and, while recording, the level (0..1). Does nothing unless enabled."""
    if not CFG.integrations_cursor_dot:
        return
    if sys.platform == "win32":
        _WINDOWS.post(_mode(state), level)
    else:
        _LINUX.send(_mode(state), level)


class _WindowsReceiver:
    """PostMessage to the overlay window; the handle is cached while the window exists."""

    def __init__(self) -> None:
        self.user32 = None
        self.message = 0
        self.hwnd = 0
        self.next_lookup = 0.0

    def _window(self) -> int:
        if self.hwnd and self.user32.IsWindow(self.hwnd):
            return self.hwnd
        now = time.monotonic()
        if now < self.next_lookup:
            return 0
        self.next_lookup = now + LOOKUP_SECONDS
        self.hwnd = self.user32.FindWindowW(None, WINDOW_TITLE) or 0
        return self.hwnd

    def post(self, mode: int, level: float | None) -> None:
        if self.user32 is None:
            import ctypes

            self.user32 = ctypes.windll.user32
        if not self.message:
            self.message = self.user32.RegisterWindowMessageW(MESSAGE_NAME)
        if level is not None:
            mode |= (int(max(0.0, min(1.0, level)) * 255) + 1) << 8
        with contextlib.suppress(OSError):
            hwnd = self._window()
            if hwnd and self.message:
                self.user32.PostMessageW(hwnd, self.message, mode, os.getpid())


class _LinuxReceiver:
    """A non-blocking datagram to the overlay's socket."""

    def __init__(self) -> None:
        self.sock = None

    @staticmethod
    def path() -> Path:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or Path(tempfile.gettempdir()) / f"hams-{os.getuid()}"
        return Path(runtime) / SOCKET_NAME

    def send(self, mode: int, level: float | None) -> None:
        if self.sock is None:
            import socket

            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
        shown = -1 if level is None else round(max(0.0, min(1.0, level)), 3)
        with contextlib.suppress(OSError):
            self.sock.sendto(f"hams {mode} {shown} {os.getpid()}".encode(), str(self.path()))


_WINDOWS = _WindowsReceiver()
_LINUX = _LinuxReceiver()
