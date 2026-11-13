"""Nur eine Instanz gleichzeitig, und der Befehl fuer den Selbstneustart."""

import ctypes
import sys

from hams.config import APP_DIR, logger

MUTEX_NAME = "Global\\Hams-SingleInstance-7a3f2c1b"
#: Der Name aus der Wisper-Zeit. Hams haelt ihn *zusaetzlich*: eine noch
#: installierte aeltere Fassung prueft nur diesen Namen. So laufen alt und neu
#: nie gleichzeitig (beide belegten dieselben Hotkeys) — wer zuerst startet,
#: gewinnt, der andere meldet „laeuft bereits".
LEGACY_MUTEX_NAME = "Global\\Wisper-SingleInstance-7a3f2c1b"
MUTEX_NAMES = (MUTEX_NAME, LEGACY_MUTEX_NAME)
ERROR_ALREADY_EXISTS = 183

#: Die Schloesser, an denen die zweite Instanz scheitert. Modulweit, weil der
#: Neustart sie freigeben muss, *bevor* der Nachfolger startet — sonst haelt
#: der scheidende Prozess sie noch und der neue meldet „laeuft bereits".
_MUTEX_HANDLES: tuple | None = None


def _kernel32():
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    return kernel32


def _create_named_mutex(kernel32, name: str) -> int | None:
    """Legt ein benanntes Mutex an. None, wenn es schon jemand haelt."""
    handle = kernel32.CreateMutexW(None, False, name)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return handle


def _acquire_windows() -> tuple | None:
    """Alle Namen aus `MUTEX_NAMES` oder keinen: haelt schon jemand einen, wird alles zurueckgegeben."""
    kernel32 = _kernel32()
    handles = []
    for name in MUTEX_NAMES:
        handle = _create_named_mutex(kernel32, name)
        if handle is None:
            for held in handles:
                kernel32.CloseHandle(held)
            return None
        handles.append(handle)
    return tuple(handles)


def _acquire_single_instance_mutex() -> int | None:
    """Return a handle on success, None if another instance is running.
    The handles stay referenced in the module for the lifetime of the process."""
    global _MUTEX_HANDLES
    if sys.platform != "win32":
        from hams import linux

        if not linux.acquire_lock():
            return None
        _MUTEX_HANDLES = (1,)
        return 1
    handles = _acquire_windows()
    if handles is None:
        return None
    _MUTEX_HANDLES = handles
    return handles[0]


def _release_single_instance_mutex() -> bool:
    """Gibt die Schloesser frei. True, wenn danach keines mehr gehalten wird.

    Ein benanntes Mutex verschwindet, sobald der letzte Griff darauf zu ist —
    danach kann der Nachfolger es sofort selbst anlegen.
    """
    global _MUTEX_HANDLES
    if _MUTEX_HANDLES is None:
        return True
    if sys.platform != "win32":
        from hams import linux

        if not linux.release_lock():
            return False
        _MUTEX_HANDLES = None
        return True
    try:
        for handle in _MUTEX_HANDLES:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        logger.exception("Einzelinstanz-Schloss liess sich nicht freigeben")
        return False
    _MUTEX_HANDLES = None
    return True


def restart_command() -> list:
    """Der Befehl, mit dem sich Hams selbst noch einmal startet.

    `sys.executable` statt eines festen Pfades: gestartet ueber das VBS ist das
    `pythonw.exe` (ohne Konsolenfenster), aus der Konsole `python.exe`. Der
    Nachfolger soll genau so laufen wie der Vorgaenger. Das Skript wird
    ausdruecklich aufgeloest angegeben und nicht ueber `sys.argv` uebernommen —
    `sys.argv[0]` ist je nach Startweg relativ und zeigt nach einem
    Verzeichniswechsel ins Leere.

    Als installiertes Programm (PyInstaller) ist `sys.executable` die Hams.exe
    selbst; ein Skript gibt es dann nicht.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(APP_DIR / "main.py")]
