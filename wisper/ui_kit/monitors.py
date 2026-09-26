"""Bildschirme und ihre Arbeitsflaechen."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk




class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _Rect),
                ("rcWork", _Rect), ("dwFlags", ctypes.c_ulong)]


MONITORINFOF_PRIMARY = 0x1


def work_area(widget: tk.Misc) -> tuple[int, int, int, int]:
    """Arbeitsfläche des Monitors, auf dem das Fenster liegt.

    `winfo_screenwidth()` kennt weder mehrere Monitore noch die Taskleiste;
    beides zusammen liesse ein Popover am Rand halb im Nichts aufgehen.

    Die Koordinaten sind physische Bildschirmpixel — dieselbe Einheit, in der
    Tk `geometry()` entgegennimmt und `winfo_x()` antwortet.
    """
    if sys.platform != "win32":
        return _linux_area(widget)
    try:
        hwnd = ctypes.windll.user32.GetParent(widget.winfo_id()) or widget.winfo_id()
        monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)   # NEAREST
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            area = info.rcWork
            return area.left, area.top, area.right, area.bottom
    except (AttributeError, OSError, tk.TclError):
        pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def work_areas() -> list:
    """Arbeitsflächen **aller** Monitore, der primäre zuerst.

    Dieselben Strukturen wie `work_area()`, nur über alle Bildschirme statt
    über den einen unter einem Fenster. Gebraucht wird das beim Start: eine
    gemerkte Position kann auf einem Monitor liegen, den es nicht mehr gibt.

    Die Liste ist nie leer — schlägt die Abfrage fehl, steht wenigstens der
    Hauptbildschirm darin.
    """
    if sys.platform != "win32":
        from wisper import linux
        return linux.monitor_areas() or [(0, 0, 1920, 1080)]
    found: list = []
    primary: list = []

    def _collect(monitor, _dc, _rect, _data) -> int:
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            area = info.rcWork
            entry = (area.left, area.top, area.right, area.bottom)
            if info.dwFlags & MONITORINFOF_PRIMARY:
                primary.append(entry)
            else:
                found.append(entry)
        return 1

    try:
        callback = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(_Rect), ctypes.c_double)(_collect)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, callback, 0)
    except (AttributeError, OSError):      # pragma: no cover - kein Windows
        pass

    areas = primary + found
    if areas:
        return areas
    try:      # pragma: no cover - nur wenn die Aufzaehlung versagt
        width = ctypes.windll.user32.GetSystemMetrics(0)
        height = ctypes.windll.user32.GetSystemMetrics(1)
        return [(0, 0, width, height)]
    except (AttributeError, OSError):
        return [(0, 0, 1920, 1080)]


def _linux_area(widget: tk.Misc) -> tuple[int, int, int, int]:
    """Der Monitor unter der Fenstermitte; KDE meldet keine Arbeitsflaeche je Monitor."""
    from wisper import linux
    try:
        cx = widget.winfo_rootx() + widget.winfo_width() // 2
        cy = widget.winfo_rooty() + widget.winfo_height() // 2
    except tk.TclError:      # pragma: no cover
        cx = cy = 0
    areas = linux.monitor_areas()
    for area in areas:
        if area[0] <= cx < area[2] and area[1] <= cy < area[3]:
            return area
    if areas:
        return areas[0]
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()
