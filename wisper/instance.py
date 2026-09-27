"""Nur eine Instanz gleichzeitig, und der Befehl fuer den Selbstneustart."""

import ctypes
import sys

from wisper.config import APP_DIR, logger

MUTEX_NAME = "Global\\Wisper-SingleInstance-7a3f2c1b"
ERROR_ALREADY_EXISTS = 183

#: Das Schloss, an dem die zweite Instanz scheitert. Modulweit, weil der
#: Neustart es freigeben muss, *bevor* der Nachfolger startet — sonst haelt
#: der scheidende Prozess es noch und der neue meldet „laeuft bereits".
_MUTEX_HANDLE: int | None = None


def _acquire_single_instance_mutex() -> int | None:
    """Return mutex handle on success, None if another instance is running.
    The returned handle must be kept alive for the lifetime of the process."""
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        from wisper import linux

        if not linux.acquire_lock():
            return None
        _MUTEX_HANDLE = 1
        return _MUTEX_HANDLE
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    _MUTEX_HANDLE = handle
    return handle


def _release_single_instance_mutex() -> bool:
    """Gibt das Schloss frei. True, wenn danach keines mehr gehalten wird.

    Ein benanntes Mutex verschwindet, sobald der letzte Griff darauf zu ist —
    danach kann der Nachfolger es sofort selbst anlegen.
    """
    global _MUTEX_HANDLE
    if _MUTEX_HANDLE is None:
        return True
    if sys.platform != "win32":
        from wisper import linux

        if not linux.release_lock():
            return False
        _MUTEX_HANDLE = None
        return True
    try:
        ctypes.windll.kernel32.CloseHandle(_MUTEX_HANDLE)
    except Exception:
        logger.exception("Einzelinstanz-Schloss liess sich nicht freigeben")
        return False
    _MUTEX_HANDLE = None
    return True


def restart_command() -> list:
    """Der Befehl, mit dem sich Hams selbst noch einmal startet.

    `sys.executable` statt eines festen Pfades: gestartet ueber das VBS ist das
    `pythonw.exe` (ohne Konsolenfenster), aus der Konsole `python.exe`. Der
    Nachfolger soll genau so laufen wie der Vorgaenger. Das Skript wird
    ausdruecklich aufgeloest angegeben und nicht ueber `sys.argv` uebernommen —
    `sys.argv[0]` ist je nach Startweg relativ und zeigt nach einem
    Verzeichniswechsel ins Leere.
    """
    return [sys.executable, str(APP_DIR / "main.py")]
