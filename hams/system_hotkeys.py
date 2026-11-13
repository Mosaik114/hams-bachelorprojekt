"""Kurzbefehle ueber `RegisterHotKey` statt ueber einen Tastaturhook.

Den Low-Level-Hook der `keyboard`-Bibliothek kann Windows jederzeit still
entfernen (siehe `restart_app` und tests/test_hotkey_recovery.py): nach
Standby, nach langer Abwesenheit oder wenn der Rechner kurz ausgelastet ist.
Danach kommen die Kurzbefehle nie wieder an.

Ein mit `RegisterHotKey` angemeldeter Kurzbefehl haengt dagegen nicht an einer
Rueckruf-Frist: Windows erkennt die Kombination selbst und legt `WM_HOTKEY` in
die Warteschlange des Threads. Der Befehl bleibt ueber Standby und
Sperrbildschirm hinweg bestehen, solange der Prozess lebt.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

logger = logging.getLogger("hams")

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000  # gehaltene Taste loest nicht staendig neu aus

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
#: Kennung fuer die kurze Probe in `available`; weit weg von den fortlaufenden Kennungen der Kurzbefehle.
PROBE_ID = 0xBFFF

_MODS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "strg": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "umschalt": MOD_SHIFT,
    "alt": MOD_ALT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "super": MOD_WIN,
}

_KEYS = {
    "space": 0x20,
    "leertaste": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "page up": 0x21,
    "pageup": 0x21,
    "page down": 0x22,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "pause": 0x13,
    "print screen": 0x2C,
    "scroll lock": 0x91,
}


def parse_combo(combo: str) -> tuple[int, int] | None:
    """„ctrl+shift+space“ -> (Modifier-Bits, virtueller Tastencode).

    None, wenn die Kombination nicht eindeutig in Modifier plus genau eine
    Taste zerfaellt; dann bleibt der Aufrufer bei der Bibliothek.
    """
    mods, vk = 0, None
    for part in (t.strip().lower() for t in combo.split("+")):
        if not part:
            return None
        if part in _MODS:
            mods |= _MODS[part]
            continue
        if vk is not None:
            return None
        if part in _KEYS:
            vk = _KEYS[part]
        elif len(part) == 1 and part.isascii() and part.isalnum():
            vk = ord(part.upper())
        elif part[0] == "f" and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        else:
            return None
    if vk is None:
        return None
    return mods, vk


class _WinApi:
    """The few Win32 calls of the hotkey thread, with their own prototypes (tests replace the whole object)."""

    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self.user32.GetMessageW.restype = wintypes.BOOL
        self.user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def current_thread_id(self) -> int:
        return int(self.kernel32.GetCurrentThreadId())

    def register(self, hid: int, mods: int, vk: int) -> bool:
        return bool(self.user32.RegisterHotKey(None, hid, mods, vk))

    def unregister(self, hid: int) -> bool:
        return bool(self.user32.UnregisterHotKey(None, hid))

    def next_hotkey(self) -> int | None:
        """Blocks until the next WM_HOTKEY of this thread; None on WM_QUIT or an error."""
        msg = wintypes.MSG()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                return int(msg.wParam)
        return None

    def post_quit(self, thread_id: int) -> bool:
        return bool(self.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0))


class SystemHotkeys:
    """Eigener Thread mit Nachrichtenschleife; ruft die Handler bei `WM_HOTKEY`.

    `RegisterHotKey(NULL, …)` bindet den Befehl an den aufrufenden Thread,
    deshalb geschieht Anmelden, Warten und Abmelden alles in diesem Thread -
    `UnregisterHotKey` aus einem anderen Thread waere wirkungslos.

    Die Handler laufen auf diesem Thread und duerfen nicht blockieren (die
    Anwendung reicht sie nur an `UiDispatcher` weiter). Sonst kaeme `stop`
    nicht durch, und der alte Thread behielte seine Anmeldungen.
    """

    #: So lange wartet `stop` auf das Abmelden.
    STOP_TIMEOUT = 2.0

    def __init__(self, api=None) -> None:
        self._api = api
        self._thread: threading.Thread | None = None
        self._thread_id = 0

    def _win(self):
        if self._api is None:
            self._api = _WinApi()
        return self._api

    def register(self, entries: list[tuple[str, Callable[[], None]]]) -> list[str]:
        """Meldet alle Kombinationen an und gibt die zurueck, die scheiterten."""
        self.stop()  # nie zwei Threads mit Anmeldungen nebeneinander
        wanted: list[tuple[int, str, int, int, Callable[[], None]]] = []
        failed: list[str] = []
        for i, (combo, handler) in enumerate(entries, start=1):
            parsed = parse_combo(combo)
            if parsed is None:
                failed.append(combo)
            else:
                wanted.append((i, combo, parsed[0], parsed[1], handler))
        if not wanted:
            return failed
        ready = threading.Event()
        result: dict = {"failed": [], "thread_id": 0}
        thread = threading.Thread(
            target=self._message_loop, args=(self._win(), wanted, ready, result), name="system-hotkeys", daemon=True
        )
        thread.start()
        if not ready.wait(5):
            logger.error("Hotkey thread did not start in time")
        self._thread = thread
        self._thread_id = result["thread_id"]
        return failed + result["failed"]

    @staticmethod
    def _message_loop(api, wanted: list, ready: threading.Event, result: dict) -> None:
        """Im eigenen Thread: anmelden, auf WM_HOTKEY warten, beim Ende wieder abmelden."""
        handlers: dict[int, Callable[[], None]] = {}
        try:
            result["thread_id"] = api.current_thread_id()
            for hid, combo, mods, vk, handler in wanted:
                if api.register(hid, mods | MOD_NOREPEAT, vk):
                    handlers[hid] = handler
                else:
                    result["failed"].append(combo)
        finally:
            ready.set()
        try:
            while (hid := api.next_hotkey()) is not None:
                SystemHotkeys._fire(handlers.get(hid))
        finally:
            for hid in handlers:
                api.unregister(hid)

    @staticmethod
    def _fire(handler) -> None:
        if handler is not None:
            try:
                handler()
            except Exception:
                logger.exception("Shortcut handler failed")

    @staticmethod
    def available(combo: str) -> bool:
        """Whether no other program holds `combo`: register it briefly and release it at once.

        Hams' own shortcuts must be stopped first, or they count as taken.
        """
        parsed = parse_combo(combo)
        if parsed is None:
            return False
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, PROBE_ID, parsed[0] | MOD_NOREPEAT, parsed[1]):
            return False
        user32.UnregisterHotKey(None, PROBE_ID)
        return True

    def stop(self) -> None:
        """Beendet den Thread; er meldet seine Kurzbefehle selbst ab. Wartet hoechstens `STOP_TIMEOUT`."""
        thread, thread_id = self._thread, self._thread_id
        self._thread = None
        self._thread_id = 0
        if thread is None or not thread.is_alive():
            return
        deadline = time.monotonic() + self.STOP_TIMEOUT
        # Erneut senden, bis er weg ist: die erste Nachricht kann vor seiner Warteschlange ankommen.
        while thread.is_alive() and time.monotonic() < deadline:
            if thread_id:
                self._win().post_quit(thread_id)
            thread.join(0.1)
        if thread.is_alive():
            logger.error("Hotkey thread did not stop; its shortcuts stay registered until Hams exits")
