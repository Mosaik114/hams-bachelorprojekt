"""Nur eine Instanz gleichzeitig, und der Befehl fuer den Selbstneustart."""

import ctypes
import sys

from hams.config import APP_DIR, logger

#: Bewusst ``Global\``: eine Instanz je *Rechner*, nicht je Anmeldesitzung. Das
#: Whisper-Modell liegt im Grafikspeicher der einen GPU; zwei Sitzungen mit je
#: einem Hams (schneller Benutzerwechsel) kaemen sich dort in die Quere. Die
#: Namen bleiben ausserdem, wie sie in v1.0 und der Wisper-Zeit hiessen, sonst
#: erkennten sich alte und neue Fassung nicht. Das Einstellungs-Ereignis in
#: `activation` ist dagegen ``Local\``: es spricht die Instanz der eigenen
#: Sitzung an. Laeuft Hams in einer anderen Sitzung, findet es keines und die
#: zweite Instanz meldet „laeuft bereits“.
MUTEX_NAME = "Global\\Hams-SingleInstance-7a3f2c1b"
#: Der Name aus der Wisper-Zeit. Hams haelt ihn *zusaetzlich*: eine noch
#: installierte aeltere Fassung prueft nur diesen Namen. So laufen alt und neu
#: nie gleichzeitig (beide belegten dieselben Hotkeys) — wer zuerst startet,
#: gewinnt, der andere meldet „laeuft bereits".
LEGACY_MUTEX_NAME = "Global\\Wisper-SingleInstance-7a3f2c1b"
MUTEX_NAMES = (MUTEX_NAME, LEGACY_MUTEX_NAME)
ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183

#: Die Schloesser, an denen die zweite Instanz scheitert. Modulweit, weil der
#: Neustart sie freigeben muss, *bevor* der Nachfolger startet — sonst haelt
#: der scheidende Prozess sie noch und der neue meldet „laeuft bereits".
_MUTEX_HANDLES: tuple | None = None


class _MutexApi:
    """CreateMutexW/CloseHandle mit eigenen Prototypen.

    Ein eigenes `WinDLL` statt `ctypes.windll.kernel32`: dessen Funktionszeiger
    teilt sich der ganze Prozess, und gesetzte `argtypes` gaelten dort fuer jede
    andere Bibliothek mit. `use_last_error` liefert den Fehlercode des Aufrufs
    selbst, nicht den eines spaeteren.
    """

    def __init__(self) -> None:
        from ctypes import wintypes

        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    def create(self, name: str) -> tuple[int, int]:
        """(Handle oder 0, Fehlercode des Aufrufs)."""
        ctypes.set_last_error(0)
        handle = self.kernel32.CreateMutexW(None, False, name)
        return handle or 0, ctypes.get_last_error()

    def close(self, handle: int) -> bool:
        return bool(self.kernel32.CloseHandle(handle))


def _kernel32() -> _MutexApi:
    return _MutexApi()


#: `_create_named_mutex` fand das Mutex schon vor: eine andere Instanz laeuft.
HELD_ELSEWHERE = None
#: Das Mutex liess sich weder anlegen noch als belegt erkennen.
UNAVAILABLE = 0


def _create_named_mutex(api, name: str) -> int | None:
    """Legt ein benanntes Mutex an.

    Returns:
        Das Handle; `HELD_ELSEWHERE` (None), wenn es schon jemand haelt - auch in
        einer anderen Sitzung, deren Mutex uns den Zugriff verweigert; `UNAVAILABLE`
        (0), wenn Windows es aus einem anderen Grund nicht anlegt.
    """
    handle, error = api.create(name)
    if handle and error == ERROR_ALREADY_EXISTS:
        api.close(handle)
        return HELD_ELSEWHERE
    if handle:
        return handle
    if error == ERROR_ACCESS_DENIED:
        return HELD_ELSEWHERE
    logger.warning("Single-instance lock %s unavailable (Windows error %d)", name, error)
    return UNAVAILABLE


def _acquire_windows() -> tuple | None:
    """Alle Namen aus `MUTEX_NAMES` oder keinen: haelt schon jemand einen, wird alles zurueckgegeben.

    Laesst sich ein Name gar nicht anlegen, startet Hams trotzdem - ohne dieses
    eine Schloss. Ein Fehler der Schutzvorrichtung soll das Programm nicht
    verhindern; das Log nennt ihn.
    """
    api = _kernel32()
    handles = []
    for name in MUTEX_NAMES:
        handle = _create_named_mutex(api, name)
        if handle is HELD_ELSEWHERE:
            for held in handles:
                api.close(held)
            return None
        if handle != UNAVAILABLE:
            handles.append(handle)
    if not handles:
        logger.warning("Starting without a single-instance lock")
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
    # Wahr auch ohne Schloss: der Aufrufer prueft nur, ob eine andere Instanz laeuft.
    return handles[0] if handles else -1


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
        api = _kernel32()
        for handle in _MUTEX_HANDLES:
            api.close(handle)
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
