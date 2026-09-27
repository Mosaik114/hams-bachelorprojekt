"""Was unter Linux (KDE Plasma, Wayland) an die Stelle der Windows-Aufrufe tritt.

Hams selbst laeuft ueber XWayland (Tk kennt kein Wayland). Deshalb kommen
Bildschirme ueber XRandR, Kurzbefehle aber ueber das Portal (portal_hotkeys.py)
und das Einfuegen ueber eine virtuelle Tastatur: unter Wayland darf ein
Programm weder die Tastatur abhoeren noch Tasten in fremde Fenster schicken.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from functools import cache
from pathlib import Path

logger = logging.getLogger("wisper")

IS_LINUX = sys.platform.startswith("linux")

#: Kennung fuer Portal, Startmenue und Autostart; der Dateiname der
#: .desktop-Datei muss genau so lauten, sonst ordnet KDE die Kurzbefehle
#: keinem Programm zu.
APP_ID = "de.mosaik.Hams"


def _runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/hams-{os.getuid()}")


def autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "autostart"


# ---------------------------------------------------------------- Dienst

#: Name des systemd-Benutzerdienstes. Das Präfix „app-“ plus APP_ID lässt das
#: Portal das Programm am Dienst erkennen.
SERVICE = f"app-{APP_ID}.service"

#: Mit diesem Code beendet sich Hams, wenn es neu gestartet werden will; der
#: Dienst startet es dann sofort wieder (RestartForceExitStatus).
RESTART_EXIT_CODE = 75

#: Vom Neustart-Knopf gesetzt; main() beendet sich dann mit RESTART_EXIT_CODE.
restart_requested = False


def under_systemd() -> bool:
    """Läuft Hams in seinem eigenen Dienst? Steht in der cgroup des Prozesses.

    INVOCATION_ID allein reicht nicht: die erbt jeder Prozess, der irgendwo
    unter einem Dienst gestartet wurde, etwa ein Terminal im Editor.
    """
    try:
        return f"/{SERVICE}" in Path("/proc/self/cgroup").read_text()
    except OSError:
        return False


def service_unit(python: str, main_py: str) -> str:
    return "\n".join([
        "[Unit]",
        "Description=Hams (Sprache zu Text)",
        "PartOf=graphical-session.target",
        "After=graphical-session.target",
        "",
        "[Service]",
        f'ExecStart="{python}" "{main_py}"',
        # Absturz: sofort wieder da. Beenden über das Tray-Menü: bleibt aus.
        "Restart=on-failure",
        f"RestartForceExitStatus={RESTART_EXIT_CODE}",
        "RestartSec=2",
        "",
        "[Install]",
        "WantedBy=graphical-session.target",
    ]) + "\n"


# ---------------------------------------------------------------- Mosaiks-Suche

#: Hier lauscht die Mosaiks-Suche (Mosaiks-Suche/linux/mosaiks_suche/nachrichten.py).
SEARCH_SOCKET = _runtime_dir() / "mosaiks-suche.sock"

_search_socket = None


def notify_search(mode: int, level: float | None) -> None:
    """Zustand und Pegel an die Suche (Soundbar am Mauszeiger).

    Ersetzt PostMessage an „Mosaiks-Suche“: wartet nicht, und läuft die Suche
    nicht, passiert einfach nichts. Format: „hams <modus> <pegel> <pid>“.
    """
    global _search_socket
    if _search_socket is None:
        import socket
        _search_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        _search_socket.setblocking(False)
    pegel = -1 if level is None else round(max(0.0, min(1.0, level)), 3)
    with contextlib.suppress(OSError):
        _search_socket.sendto(f"hams {mode} {pegel} {os.getpid()}".encode(), str(SEARCH_SOCKET))


# ---------------------------------------------------------------- Einzelinstanz

_LOCK_FILE = None


def acquire_lock() -> bool:
    """Sperrdatei statt benanntem Mutex; der Kernel gibt sie beim Prozessende frei."""
    global _LOCK_FILE
    path = _runtime_dir() / "hams.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _LOCK_FILE = handle
    return True


def release_lock() -> bool:
    global _LOCK_FILE
    if _LOCK_FILE is not None:
        try:
            fcntl.flock(_LOCK_FILE, fcntl.LOCK_UN)
            _LOCK_FILE.close()
        except OSError:
            logger.exception("Sperrdatei liess sich nicht freigeben")
            return False
        _LOCK_FILE = None
    return True


def notify(title: str, message: str) -> None:
    """Kurze Systemmeldung; fehlt notify-send, passiert nichts."""
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", title, "-i", APP_ID, title, message],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- Ton

def beep(freq: int, duration_ms: int) -> None:
    """Ersatz fuer winsound.Beep: ein kurzer Sinuston mit weichen Kanten."""
    try:
        import numpy as np
        import sounddevice as sd
        rate = 44_100
        t = np.arange(int(rate * duration_ms / 1000)) / rate
        tone = 0.25 * np.sin(2 * np.pi * freq * t)
        ramp = min(len(t) // 2, int(rate * 0.005))     # 5 ms ein- und ausblenden, sonst knackt es
        if ramp:
            fade = np.linspace(0.0, 1.0, ramp)
            tone[:ramp] *= fade
            tone[-ramp:] *= fade[::-1]
        sd.play(tone.astype(np.float32), rate, blocking=True)
    except Exception:
        logger.debug("Ton liess sich nicht abspielen", exc_info=True)


# ---------------------------------------------------------------- Bildschirme

def monitor_areas() -> list:
    """Alle Monitore als (links, oben, rechts, unten), der primaere zuerst.

    Die Koordinaten sind die von XWayland — dieselben, in denen Tk rechnet.
    """
    try:
        from Xlib import display
        from Xlib.ext import randr
        disp = display.Display()
        try:
            reply = randr.get_monitors(disp.screen().root, True)
        finally:
            disp.close()
        areas = [(m.x, m.y, m.x + m.width_in_pixels, m.y + m.height_in_pixels, m.primary)
                 for m in reply.monitors]
        areas.sort(key=lambda a: not a[4])
        return [a[:4] for a in areas]
    except Exception:
        logger.debug("XRandR nicht verfuegbar", exc_info=True)
        return []


# ---------------------------------------------------------------- Schriften

#: Windows-Familie -> Linux-Ersatz, in dieser Reihenfolge gesucht. Inter und
#: Source Serif 4 kommen dem Vorbild des Mosaik-Designs am nächsten (Claude:
#: Styrene und Tiempos) und sind frei; die übrigen sind nur Rückfall.
FONT_SUBSTITUTES = {
    "text": ("Inter", "IBM Plex Sans", "Noto Sans", "DejaVu Sans"),
    "semibold": ("Inter SemiBold", "IBM Plex Sans SmBld", "IBM Plex Sans", "Noto Sans"),
    "serif": ("Source Serif 4", "Noto Serif", "DejaVu Serif"),
    "mono": ("IBM Plex Mono", "DejaVu Sans Mono"),
}

#: Schnitte, die fontconfig über den Namen findet, Tk aber nicht als eigene
#: Familie aufzählt: gilt als vorhanden, wenn die Grundfamilie da ist.
WEIGHT_STYLES = ("SemiBold", "Medium")


def font_available(family: str, families) -> bool:
    if family in families:
        return True
    base, _, style = family.rpartition(" ")
    return style in WEIGHT_STYLES and base in families


@cache
def font_file(family: str) -> str | None:
    """Pfad zur Schriftdatei einer Familie — PIL kennt unter Linux keine Namen."""
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", family],
                             capture_output=True, text=True, timeout=2)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------- Einfuegen

# Aus linux/uinput.h und linux/input-event-codes.h
_UI_SET_EVBIT = 0x40045564
_UI_SET_KEYBIT = 0x40045565
_UI_DEV_SETUP = 0x405C5503
_UI_DEV_CREATE = 0x5501
_UI_DEV_DESTROY = 0x5502
_EV_SYN, _EV_KEY = 0x00, 0x01
_BUS_VIRTUAL = 0x06
KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V = 29, 42, 47


class VirtualKeyboard:
    """Eine eigene Tastatur ueber /dev/uinput, die nur Strg+V drueckt.

    Unter Wayland gibt es keinen erlaubten Weg, einem fremden Fenster Tasten zu
    schicken; ein Eingabegeraet auf Kernel-Ebene sieht der Compositor dagegen
    wie eine echte Tastatur. Das Recht auf /dev/uinput kommt aus der udev-Regel
    in linux/system-einrichten.sh.

    Das Geraet wird einmal beim Start angelegt und bleibt bestehen: KWin braucht
    einen Moment, bis es ein neues Geraet kennt, und ein pro Einfuegen neu
    angelegtes wuerde die ersten Tasten verschlucken.
    """

    def __init__(self) -> None:
        self._fd: int | None = None
        self._lock = threading.Lock()
        self.error: str | None = None

    def open(self) -> bool:
        try:
            fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            self.error = f"/dev/uinput: {exc.strerror}"
            logger.error("Virtuelle Tastatur nicht moeglich (%s) — "
                         "linux/system-einrichten.sh ausgefuehrt?", self.error)
            return False
        try:
            fcntl.ioctl(fd, _UI_SET_EVBIT, _EV_KEY)
            fcntl.ioctl(fd, _UI_SET_EVBIT, _EV_SYN)
            for key in (KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V):
                fcntl.ioctl(fd, _UI_SET_KEYBIT, key)
            name = b"Hams virtuelle Tastatur"
            setup = struct.pack("<HHHH80sI", _BUS_VIRTUAL, 0x1209, 0x4841, 1, name, 0)
            fcntl.ioctl(fd, _UI_DEV_SETUP, setup)
            fcntl.ioctl(fd, _UI_DEV_CREATE)
        except OSError as exc:
            os.close(fd)
            self.error = f"uinput: {exc.strerror}"
            logger.exception("Virtuelle Tastatur liess sich nicht anlegen")
            return False
        self._fd = fd
        self.error = None
        logger.info("Virtuelle Tastatur angelegt")
        return True

    def _emit(self, type_: int, code: int, value: int) -> None:
        now = time.time()
        sec = int(now)
        os.write(self._fd, struct.pack("llHHi", sec, int((now - sec) * 1e6), type_, code, value))

    def _key(self, code: int, down: bool) -> None:
        self._emit(_EV_KEY, code, 1 if down else 0)
        self._emit(_EV_SYN, 0, 0)

    def press_combo(self, *keys: int) -> bool:
        with self._lock:
            if self._fd is None and not self.open():
                return False
            try:
                for key in keys:
                    self._key(key, True)
                    time.sleep(0.008)
                for key in reversed(keys):
                    self._key(key, False)
                    time.sleep(0.008)
                return True
            except OSError:
                logger.exception("Tastendruck fehlgeschlagen")
                return False

    def paste(self) -> bool:
        return self.press_combo(KEY_LEFTCTRL, KEY_V)

    def close(self) -> None:
        with self._lock:
            if self._fd is not None:
                try:
                    fcntl.ioctl(self._fd, _UI_DEV_DESTROY)
                finally:
                    os.close(self._fd)
                    self._fd = None


KEYBOARD = VirtualKeyboard()


class _KeyboardShim:
    """Das Stueck der `keyboard`-Bibliothek, das Hams zum Einfuegen braucht.

    Gleicher Name und gleiche Aufrufform wie unter Windows, damit der Code in
    transcription.py fuer beide Systeme derselbe bleibt.
    """

    _COMBOS = {"ctrl+v": (KEY_LEFTCTRL, KEY_V)}

    def send(self, combo: str) -> None:
        keys = self._COMBOS.get(combo.replace(" ", "").lower())
        if keys is None:
            raise ValueError(f"Unbekannte Tastenkombination: {combo}")
        if not KEYBOARD.press_combo(*keys):
            logger.warning("Einfuegen nicht moeglich (%s) — Text liegt in der "
                           "Zwischenablage", KEYBOARD.error)

    def add_hotkey(self, combo: str, _callback) -> None:
        # Nur der Rueckfall, wenn das Portal eine Kombination ablehnt; unter
        # Wayland gibt es keinen zweiten Weg.
        raise ValueError(f"„{combo}“: unter Wayland nur ueber das Portal moeglich")

    def unhook_all_hotkeys(self) -> None:
        pass


keyboard = _KeyboardShim()


class _WinsoundShim:
    Beep = staticmethod(beep)


winsound = _WinsoundShim()


# ---------------------------------------------------------------- Autostart

def desktop_entry(exec_line: str, icon: str) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Hams",
        "Comment=Sprache zu Text: diktieren und an der Cursorposition einfügen",
        f"Exec={exec_line}",
        f"Icon={icon}",
        "Terminal=false",
        "Categories=Utility;Accessibility;",
        "StartupNotify=false",
    ]) + "\n"
