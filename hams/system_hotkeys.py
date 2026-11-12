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
    for teil in (t.strip().lower() for t in combo.split("+")):
        if not teil:
            return None
        if teil in _MODS:
            mods |= _MODS[teil]
            continue
        if vk is not None:
            return None
        if teil in _KEYS:
            vk = _KEYS[teil]
        elif len(teil) == 1 and teil.isascii() and teil.isalnum():
            vk = ord(teil.upper())
        elif teil[0] == "f" and teil[1:].isdigit() and 1 <= int(teil[1:]) <= 24:
            vk = 0x70 + int(teil[1:]) - 1
        else:
            return None
    if vk is None:
        return None
    return mods, vk


class SystemHotkeys:
    """Eigener Thread mit Nachrichtenschleife; ruft die Handler bei `WM_HOTKEY`.

    `RegisterHotKey(NULL, …)` bindet den Befehl an den aufrufenden Thread,
    deshalb geschieht Anmelden, Warten und Abmelden alles in diesem Thread.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._handlers: dict[int, Callable[[], None]] = {}

    def register(self, entries: list[tuple[str, Callable[[], None]]]) -> list[str]:
        """Meldet alle Kombinationen an und gibt die zurueck, die scheiterten."""
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
        result: dict[str, list[str]] = {"failed": []}
        self._thread = threading.Thread(
            target=self._schleife, args=(wanted, ready, result), name="system-hotkeys", daemon=True
        )
        self._thread.start()
        ready.wait(5)
        return failed + result["failed"]

    def _schleife(self, wanted: list, ready: threading.Event, result: dict[str, list[str]]) -> None:
        """Im eigenen Thread: anmelden, auf WM_HOTKEY warten, beim Ende wieder abmelden."""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        registered = []
        for hid, combo, mods, vk, handler in wanted:
            if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                self._handlers[hid] = handler
                registered.append(hid)
            else:
                result["failed"].append(combo)
        ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self._ausloesen(int(msg.wParam))
        finally:
            for hid in registered:
                user32.UnregisterHotKey(None, hid)

    def _ausloesen(self, hid: int) -> None:
        handler = self._handlers.get(hid)
        if handler is not None:
            try:
                handler()
            except Exception:
                logger.exception("Kurzbefehl-Handler fehlgeschlagen")

    def stop(self) -> None:
        if self._thread is not None and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = None
        self._thread_id = 0
        self._handlers.clear()
