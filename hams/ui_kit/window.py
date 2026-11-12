"""Echte runde Fensterecken unter Windows 11 und der Fensterrahmen je System."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk

from hams.ui_kit.scale import px
from hams.ui_kit.theme import Radius

# --- Fensterecken -----------------------------------------------------------
#
# Gemessen an einem randlosen Tk-Fenster (WS_POPUP ohne Rahmen) unter
# Windows 11 Build 26200:
#
#   DWMWA_WINDOW_CORNER_PREFERENCE   wirkt      -> Ecken werden vom Compositor
#                                                 gerundet und kantengeglaettet
#   DWMWA_BORDER_COLOR               wirkungslos -> HRESULT 0, aber kein Pixel
#                                                 aendert sich; ohne Fensterrahmen
#                                                 zeichnet DWM keinen Rand
#   DwmExtendFrameIntoClientArea     wirkungslos -> kein Schatten
#   CS_DROPSHADOW am Klassenstil     wirkungslos -> kein Schatten
#
# Deshalb wird hier ausschliesslich die Eckenrundung nativ gesetzt. Ein Rand
# muesste als eigene Hairline im Fensterinhalt gezeichnet werden; ein Schatten
# braeuchte zusaetzliche Ebenenfenster, was der Plan ausdruecklich ausschliesst.

_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2
_WIN11_BUILD = 22000


def _windows_build() -> int:
    """Build-Nummer über RtlGetVersion — GetVersionEx lügt ohne Manifest."""

    class _Info(ctypes.Structure):
        _fields_ = [
            ("dwOSVersionInfoSize", ctypes.c_ulong),
            ("dwMajorVersion", ctypes.c_ulong),
            ("dwMinorVersion", ctypes.c_ulong),
            ("dwBuildNumber", ctypes.c_ulong),
            ("dwPlatformId", ctypes.c_ulong),
            ("szCSDVersion", ctypes.c_wchar * 128),
        ]

    try:
        info = _Info()
        info.dwOSVersionInfoSize = ctypes.sizeof(info)
        ctypes.windll.ntdll.RtlGetVersion(ctypes.byref(info))
        return int(info.dwBuildNumber)
    except (AttributeError, OSError):
        return 0


def _window_handle(root: tk.Misc) -> int:
    return ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()


def round_window_corners(root: tk.Misc, radius: int = Radius.WINDOW) -> str:
    """Rundet die Fensterecken. Gibt den benutzten Weg zurück (für das Log).

    Auf Windows 11 übernimmt das der Compositor: die Ecke wird kantengeglättet
    und passt sich der Systemdarstellung an. Darunter bleibt der bisherige Weg
    über `SetWindowRgn`, der mit einer 1-Bit-Maske schneidet und deshalb harte
    Treppen erzeugt.

    Beide Verfahren werden nie gleichzeitig angewandt — die Region wird vor dem
    DWM-Weg ausdrücklich entfernt.
    """
    if sys.platform != "win32":
        return "keine"  # XWayland-Fenster formt KWin selbst; Rundung ist kosmetisch
    try:
        root.update_idletasks()
        hwnd = _window_handle(root)
        if not hwnd:
            return "keine"
    except tk.TclError:  # pragma: no cover - Fenster bereits zerstört
        return "keine"

    if _windows_build() >= _WIN11_BUILD:
        try:
            ctypes.windll.user32.SetWindowRgn(hwnd, None, True)  # Maske weg
            preference = ctypes.c_int(_DWMWCP_ROUND)
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(_DWMWA_WINDOW_CORNER_PREFERENCE),
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
            if result == 0:
                return "dwm"
        except (AttributeError, OSError):
            pass  # fällt unten auf die Region zurück

    try:
        width, height = root.winfo_width(), root.winfo_height()
        if width <= 1 or height <= 1:
            return "keine"
        scaled = px(radius)
        region = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, scaled * 2, scaled * 2)
        ctypes.windll.user32.SetWindowRgn(hwnd, region, True)
        return "region"
    except (AttributeError, OSError, tk.TclError):
        return "keine"  # Rundung ist kosmetisch — nie den Start blockieren


# --- Fensterrahmen -----------------------------------------------------------
#
# Windows: randlos und immer oben, der Rahmen ist Teil des eigenen Designs.
# Linux: ein normales, von KWin verwaltetes Fenster. Randlose Fenster verwaltet
# KWin gar nicht – sie hatten keine Titelleiste (Schließen, Minimieren) und lagen
# über allem, sogar über dem Bildschirmfoto-Werkzeug und dem Mauszeiger.


def frame_window(win: tk.Misc) -> None:
    """Rahmen je System: randlos und oben (Windows) oder mit Titelleiste von KDE (Linux)."""
    if not system_frame():
        win.overrideredirect(True)
        win.attributes("-topmost", True)


def bring_to_front(win: tk.Misc) -> None:
    """Fenster zeigen und nach vorn holen; nur unter Windows zusätzlich „immer oben“."""
    win.deiconify()
    win.lift()
    if system_frame():
        win.focus_force()
    else:
        win.attributes("-topmost", True)


def system_frame() -> bool:
    """True, wenn das System Titelleiste und Schließen-Knopf zeichnet (Linux)."""
    return sys.platform != "win32"
