"""Echte runde Fensterecken unter Windows 11 und der Fensterrahmen je System."""

from __future__ import annotations

import contextlib
import ctypes
import sys
import tkinter as tk
from collections.abc import Callable

from hams.ui_kit.scale import px
from hams.ui_kit.states import FocusMixin
from hams.ui_kit.theme import Radius, Theme

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
    und passt sich der Systemdarstellung an. Darunter bleibt der Weg
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


def frame_window(
    win: tk.Misc,
    *,
    min_size: tuple[int, int] | None = None,
    on_moved: Callable[[int, int], object] | None = None,
    on_resized: Callable[[int, int], object] | None = None,
) -> WindowChrome | None:
    """Rahmen je System: randlos und oben (Windows) oder mit Titelleiste von KDE (Linux).

    Randlos übernimmt `WindowChrome`, was sonst der Rahmen täte: Verschieben an jeder freien Fläche und, mit
    `min_size` (logische Pixel), Größe ändern an Rändern und Ecken. Unter Linux macht das KWin.
    """
    if system_frame():
        return None
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    return WindowChrome(win, min_size=min_size, on_moved=on_moved, on_resized=on_resized)


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


# --- Verschieben und Größe ändern ------------------------------------------
#
# Ein randloses Fenster hat keinen Rahmen, an dem Windows es verschieben oder in der Größe ändern könnte. Früher
# hatte jedes Fenster dafür eigenen Code, und gezogen werden konnte nur an der Überschrift. Jetzt gilt für alle
# Fenster eine Regel: freie Fläche (Fensterhintergrund, kein Bedienelement) verschiebt das Fenster; ein Rand von
# `WindowChrome.EDGE` Pixeln ändert die Größe, wenn das Fenster das erlaubt.
#
# Bewusst *nicht* über die Verschiebeschleife von Windows (WM_NCLBUTTONDOWN mit HTCAPTION): aus einem
# Tk-Ereignis heraus gestartet, liess sie Hams mitten im Ziehen abstuerzen (0xC0000409 in ucrtbase, 14.11.2026).
# Tk bewegt das Fenster selbst, eine Lage je Leerlauf.

#: Tk-Cursor je Rand (die Eck-Cursor gibt es nur unter Windows, und nur dort ist das Fenster randlos).
_EDGE_CURSORS = {
    "n": "sb_v_double_arrow",
    "s": "sb_v_double_arrow",
    "e": "sb_h_double_arrow",
    "w": "sb_h_double_arrow",
    "nw": "size_nw_se",
    "se": "size_nw_se",
    "ne": "size_ne_sw",
    "sw": "size_ne_sw",
}
_TEXT_INPUTS = frozenset({"Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "Listbox", "TCombobox"})


def is_free_area(widget: tk.Misc) -> bool:
    """Fensterhintergrund ohne eigene Bedienung: Überschrift, Ränder, Text auf dem Fenstergrund."""
    try:
        if isinstance(widget, FocusMixin) or widget.winfo_class() in _TEXT_INPUTS:
            return False
        if str(widget.cget("cursor")) not in ("", "arrow"):
            return False  # alles Anklickbare zeigt die Hand
        return str(widget.cget("bg")).lower() == Theme.WINDOW_BG.lower()
    except (tk.TclError, AttributeError):
        return False


class WindowChrome:
    """Verschieben und Größe ändern für ein randloses Fenster.

    Gebunden wird am Fenster selbst: dessen Name steht in den Bind-Tags jedes Widgets darin, so gilt die Regel
    auch für alles, was später dazukommt (eine neu aufgebaute Seite). Bedienelemente bekommen den Klick zuerst.
    `on_moved(x, y)` und `on_resized(width, height)` melden das Ende einer Bewegung, in Bildschirmpixeln.
    """

    #: Breite des Greifrands in logischen Pixeln; die Ecken greifen doppelt so weit.
    EDGE = 6

    def __init__(
        self,
        win: tk.Misc,
        *,
        min_size: tuple[int, int] | None = None,
        on_moved: Callable[[int, int], object] | None = None,
        on_resized: Callable[[int, int], object] | None = None,
    ) -> None:
        self.win = win
        self.min_size = min_size
        self.on_moved = on_moved
        self.on_resized = on_resized
        #: Laufende Geste: ("move", dx, dy) oder ("resize", rand, x0, y0, (x, y, w, h)); None ohne Geste.
        self._gesture: tuple | None = None
        self._pending: tuple | None = None
        self._apply_job: str | None = None
        self._cursor: tuple[tk.Misc, str] | None = None
        #: Where this chrome last put the window; Tk reports a new position only after its next event round.
        self._placed: tuple[int, int] | None = None
        win.bind("<ButtonPress-1>", self._on_press, add="+")
        win.bind("<B1-Motion>", self._on_drag, add="+")
        win.bind("<ButtonRelease-1>", self._on_release, add="+")
        if min_size is not None:
            win.bind("<Motion>", self._on_motion, add="+")

    @property
    def resizable(self) -> bool:
        return self.min_size is not None

    # ------------------------------------------------------------ Ränder

    def edge_at(self, x_root: int, y_root: int) -> str:
        """Der Rand unter dem Zeiger ("n", "se", ...) oder "", wenn keiner (oder das Fenster fest ist)."""
        if not self.resizable:
            return ""
        win = self.win
        try:
            dx, dy = x_root - win.winfo_rootx(), y_root - win.winfo_rooty()
            width, height = win.winfo_width(), win.winfo_height()
        except tk.TclError:
            return ""
        if not (0 <= dx < width and 0 <= dy < height):
            return ""
        edge, corner = px(self.EDGE), 2 * px(self.EDGE)
        near_v = "n" if dy < edge else "s" if dy >= height - edge else ""
        near_h = "w" if dx < edge else "e" if dx >= width - edge else ""
        if near_v and not near_h:
            near_h = "w" if dx < corner else "e" if dx >= width - corner else ""
        elif near_h and not near_v:
            near_v = "n" if dy < corner else "s" if dy >= height - corner else ""
        return near_v + near_h

    def _on_motion(self, event: tk.Event) -> None:
        if self._gesture is not None:
            return
        edge = self.edge_at(event.x_root, event.y_root)
        widget = event.widget
        if self._cursor is not None and (not edge or self._cursor[0] is not widget):
            self._restore_cursor()
        if edge and self._cursor is None:
            with contextlib.suppress(tk.TclError):
                self._cursor = (widget, str(widget.cget("cursor")))
                widget.configure(cursor=_EDGE_CURSORS[edge])
        elif edge:
            with contextlib.suppress(tk.TclError):
                widget.configure(cursor=_EDGE_CURSORS[edge])

    def _restore_cursor(self) -> None:
        widget, cursor = self._cursor
        self._cursor = None
        with contextlib.suppress(tk.TclError):
            widget.configure(cursor=cursor)

    # ------------------------------------------------------------ Gesten

    def _on_press(self, event: tk.Event) -> None:
        win = self.win
        edge = self.edge_at(event.x_root, event.y_root)
        try:
            frame = (win.winfo_x(), win.winfo_y(), win.winfo_width(), win.winfo_height())
        except tk.TclError:
            return
        if edge:
            self._gesture = ("resize", edge, event.x_root, event.y_root, frame)
            return
        if not is_free_area(event.widget):
            return
        self._gesture = ("move", event.x_root - frame[0], event.y_root - frame[1])

    def _on_drag(self, event: tk.Event) -> None:
        gesture = self._gesture
        if gesture is None:
            return
        if gesture[0] == "move":
            self._schedule(None, None, event.x_root - gesture[1], event.y_root - gesture[2])
            return
        _kind, edge, x0, y0, (x, y, width, height) = gesture
        dx, dy = event.x_root - x0, event.y_root - y0
        min_w, min_h = (px(value) for value in self.min_size)
        if "e" in edge:
            width = max(min_w, width + dx)
        if "s" in edge:
            height = max(min_h, height + dy)
        if "w" in edge:
            new = max(min_w, width - dx)
            x, width = x + width - new, new
        if "n" in edge:
            new = max(min_h, height - dy)
            y, height = y + height - new, new
        self._schedule(width, height, x, y)

    def _schedule(self, width: int | None, height: int | None, x: int, y: int) -> None:
        """Eine Lage je Leerlauf: beim Ziehen kommen mehr Ereignisse, als Tk neu auslegen kann."""
        self._pending = (width, height, x, y)
        self._placed = None
        if self._apply_job is None:
            try:
                self._apply_job = self.win.after_idle(self._apply)
            except tk.TclError:  # pragma: no cover - Fenster verschwindet gerade
                self._apply_job = None

    def _apply(self) -> None:
        self._apply_job = None
        pending, self._pending = self._pending, None
        if pending is None:
            return
        width, height, x, y = pending
        size = f"{width}x{height}" if width is not None else ""
        self._placed = (x, y)
        with contextlib.suppress(tk.TclError):
            self.win.geometry(f"{size}+{x}+{y}")

    def _on_release(self, _event=None) -> None:
        gesture, self._gesture = self._gesture, None
        if gesture is None:
            return
        if self._apply_job is not None:
            with contextlib.suppress(tk.TclError, ValueError):
                self.win.after_cancel(self._apply_job)
        self._apply()
        if gesture[0] == "move":
            if self._placed is not None and self.on_moved is not None:
                self.on_moved(*self._placed)
            return
        with contextlib.suppress(tk.TclError):
            self.win.update_idletasks()
            round_window_corners(self.win)
            if self.on_resized is not None:
                self.on_resized(self.win.winfo_width(), self.win.winfo_height())
