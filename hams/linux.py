"""Was unter Linux (KDE Plasma, Wayland) an die Stelle der Windows-Aufrufe tritt.

Hams selbst laeuft ueber XWayland (Tk kennt kein Wayland). Deshalb kommen
Bildschirme ueber XRandR, Kurzbefehle aber ueber das Portal (portal_hotkeys.py)
und das Einfuegen ueber eine virtuelle Tastatur: unter Wayland darf ein
Programm weder die Tastatur abhoeren noch Tasten in fremde Fenster schicken.
"""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
from functools import cache
from pathlib import Path

from hams.i18n import t_for

logger = logging.getLogger("hams")

IS_LINUX = sys.platform.startswith("linux")

#: Kennung fuer Portal, Startmenue und Autostart; der Dateiname der
#: .desktop-Datei muss genau so lauten, sonst ordnet KDE die Kurzbefehle
#: keinem Programm zu.
APP_ID = "de.mosaik.Hams"


def _runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or Path(tempfile.gettempdir()) / f"hams-{os.getuid()}")


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
    return (
        "\n".join(
            [
                "[Unit]",
                "Description=Hams (speech to text)",
                "PartOf=graphical-session.target",
                "After=graphical-session.target",
                "",
                "[Service]",
                # --background: an der Sitzung startet Hams im Tray, wie unter Windows der Autostart.
                f'ExecStart="{python}" "{main_py}" --background',
                # Absturz: sofort wieder da. Beenden über das Tray-Menü: bleibt aus.
                "Restart=on-failure",
                f"RestartForceExitStatus={RESTART_EXIT_CODE}",
                "RestartSec=2",
                "",
                "[Install]",
                "WantedBy=graphical-session.target",
            ]
        )
        + "\n"
    )


# ---------------------------------------------------------------- Einzelinstanz

_LOCK_FILE = None


def acquire_lock() -> bool:
    """Sperrdatei statt benanntem Mutex; der Kernel gibt sie beim Prozessende frei.

    Darin steht die Prozessnummer, damit `linux/show-hams.sh` das Fenster per
    SIGUSR1 nach vorn holen kann. Geöffnet mit "a", nicht "w": eine zweite Instanz
    würde sonst die Nummer der ersten löschen, bevor sie an der Sperre scheitert.
    """
    global _LOCK_FILE
    path = _runtime_dir() / "hams.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a")  # noqa: SIM115 – bleibt offen, solange die Sperre gilt
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    handle.truncate(0)
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    _LOCK_FILE = handle
    return True


def on_show_request(callback) -> None:
    """SIGUSR1 (von `linux/show-hams.sh`, Knopf im Kontrollzentrum) ruft `callback` auf."""
    signal.signal(signal.SIGUSR1, lambda _signum, _frame: callback())


def request_show(lock_path: Path | None = None) -> bool:
    """Zweiter Start: die laufende Instanz per SIGUSR1 ihr Fenster zeigen lassen (wie `linux/show-hams.sh`).

    Nur an einen Hams-Prozess: eine fremde Nummer in einer alten Sperrdatei bekommt kein Signal.
    """
    path = lock_path or _runtime_dir() / "hams.lock"
    try:
        pid = int(path.read_text(encoding="utf-8").split()[0])
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError, IndexError):
        return False
    if b"hams" not in cmdline.lower() or pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGUSR1)
    except OSError:
        return False
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
        subprocess.Popen(
            ["notify-send", "-a", title, "-i", APP_ID, title, message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


# ---------------------------------------------------------------- Ton


#: Weicher Glockenklang statt Piepton. Die Aufrufer stammen aus der Windows-Zeit
#: und rufen `winsound.Beep(frequenz, dauer)`; die drei bekannten Frequenzen stehen
#: für Aufnahme an (steigend), Aufnahme aus (fallend) und Beenden (tief fallend).
CHIMES = {
    880: (659.25, 987.77),  # E5 → H5
    220: (987.77, 659.25),  # H5 → E5
    440: (659.25, 493.88),  # E5 → H4
}
CHIME_RATE = 44_100
CHIME_GAP = 0.07  # s zwischen den beiden Anschlägen
CHIME_RING = 0.45  # s Ausklang je Anschlag
CHIME_VOLUME = 0.16


def bell(freq: float, rate: int = CHIME_RATE, seconds: float = CHIME_RING):
    """Ein Anschlag wie eine kleine Glocke: Grundton, leise Obertöne, schneller Ausklang."""
    import numpy as np

    t = np.arange(int(rate * seconds)) / rate
    tone = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.13)
    tone += 0.25 * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-t / 0.06)
    tone += 0.06 * np.sin(2 * np.pi * 3.01 * freq * t) * np.exp(-t / 0.03)
    attack = int(rate * 0.004)  # 4 ms einblenden, sonst knackt es
    tone[:attack] *= np.linspace(0.0, 1.0, attack)
    tone[-attack:] *= np.linspace(1.0, 0.0, attack)
    return tone


def chime(freq: int, duration_ms: int, rate: int = CHIME_RATE):
    """Der ganze Klang als float32: zwei Anschläge für bekannte Frequenzen, sonst einer."""
    import numpy as np

    notes = CHIMES.get(freq, (float(freq),))
    gap = int(rate * CHIME_GAP)
    first = bell(notes[0], rate)
    out = np.zeros(len(first) + gap * (len(notes) - 1))
    for index, note in enumerate(notes):
        out[index * gap : index * gap + len(first)] += bell(note, rate)
    return (CHIME_VOLUME * out / max(1e-9, float(np.abs(out).max()))).astype(np.float32)


def beep(freq: int, duration_ms: int) -> None:
    """Ersatz fuer winsound.Beep: spielt den Glockenklang (`chime`)."""
    try:
        import sounddevice as sd

        sd.play(chime(freq, duration_ms), CHIME_RATE, blocking=True)
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
        areas = [(m.x, m.y, m.x + m.width_in_pixels, m.y + m.height_in_pixels, m.primary) for m in reply.monitors]
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
        out = subprocess.run(["fc-match", "-f", "%{file}", family], capture_output=True, text=True, timeout=2)
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
    in linux/setup-system.sh.

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
            logger.error("Virtuelle Tastatur nicht moeglich (%s) — linux/setup-system.sh ausgefuehrt?", self.error)
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
            raise ValueError(f"Unknown key combination: {combo}")
        if not KEYBOARD.press_combo(*keys):
            logger.warning("Einfuegen nicht moeglich (%s) — Text liegt in der Zwischenablage", KEYBOARD.error)

    def add_hotkey(self, combo: str, _callback) -> None:
        # Nur der Rueckfall, wenn das Portal eine Kombination ablehnt; unter
        # Wayland gibt es keinen zweiten Weg.
        raise ValueError(f"{combo!r}: under Wayland only the portal can register shortcuts")

    def unhook_all_hotkeys(self) -> None:
        pass


keyboard = _KeyboardShim()


class _WinsoundShim:
    Beep = staticmethod(beep)


winsound = _WinsoundShim()


# ---------------------------------------------------------------- Autostart


def desktop_entry(exec_line: str, icon: str) -> str:
    return (
        "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                "Name=Hams",
                f"Comment={t_for('en', 'linux.desktop_comment')}",
                f"Comment[de]={t_for('de', 'linux.desktop_comment')}",
                f"Exec={exec_line}",
                f"Icon={icon}",
                "Terminal=false",
                "Categories=Utility;Accessibility;",
                "StartupNotify=false",
            ]
        )
        + "\n"
    )
