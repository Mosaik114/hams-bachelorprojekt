"""Weckt die laufende Windows-Instanz: Einstellungen oeffnen oder das Hauptfenster zeigen."""

from __future__ import annotations

import contextlib
import ctypes
import time
from ctypes import wintypes

SETTINGS_EVENT = "Local\\Hams.OpenSettings-7a3f2c1b"
SHOW_EVENT = "Local\\Hams.ShowWindow-7a3f2c1b"
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
POLL_MS = 150


def _kernel32():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateEventW.restype = wintypes.HANDLE
    api.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    api.OpenEventW.restype = wintypes.HANDLE
    api.SetEvent.argtypes = [wintypes.HANDLE]
    api.SetEvent.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def request_settings(*, timeout: float = 3.0, name: str = SETTINGS_EVENT) -> bool:
    """Sendet die Anfrage; wartet kurz, falls die erste Instanz gerade startet."""
    api = _kernel32()
    deadline = time.monotonic() + timeout
    while True:
        handle = api.OpenEventW(EVENT_MODIFY_STATE, False, name)
        if handle:
            try:
                return bool(api.SetEvent(handle))
            finally:
                api.CloseHandle(handle)
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def request_show(*, timeout: float = 3.0) -> bool:
    """Bittet die laufende Instanz, ihr Hauptfenster nach vorn zu holen."""
    return request_settings(timeout=timeout, name=SHOW_EVENT)


class SettingsRequests:
    """Ein Auto-Reset-Ereignis; der Rueckruf bleibt im Tk-Hauptthread."""

    def __init__(self, root, callback, *, name: str = SETTINGS_EVENT):
        self.root = root
        self.callback = callback
        self.api = _kernel32()
        self.handle = self.api.CreateEventW(None, False, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.job = root.after(POLL_MS, self._poll)

    def _poll(self):
        self.job = None
        if not self.handle:
            return
        try:
            status = self.api.WaitForSingleObject(self.handle, 0)
            if status == WAIT_OBJECT_0:
                self.callback()
            elif status != WAIT_TIMEOUT:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if self.handle:
                self.job = self.root.after(POLL_MS, self._poll)

    def close(self):
        if self.job is not None:
            with contextlib.suppress(Exception):
                self.root.after_cancel(self.job)
            self.job = None
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
