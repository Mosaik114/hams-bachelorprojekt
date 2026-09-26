"""Tests der nativen Fensterkante.

Was DWM an einem randlosen Tk-Fenster tatsächlich bewirkt, wurde vorab
gemessen: die Eckenrundung greift, `DWMWA_BORDER_COLOR`,
`DwmExtendFrameIntoClientArea` und `CS_DROPSHADOW` melden zwar Erfolg,
verändern aber kein Pixel. Deshalb setzt der Code nur die Ecken — und diese
Tests sichern den Plattformpfad und den Rückfall ab.
"""

import ctypes
import inspect
import sys
import tkinter as tk


import pytest

from source_code import ui_kit_source
from wisper.ui_kit import window as window_mod
from wisper import ui_kit as uk


windows_only = pytest.mark.skipif(sys.platform != "win32",
                                  reason="DWM und Fensterregionen gibt es nur unter Windows")


class TestWindowsBuild:
    def test_returns_a_plausible_build(self):
        build = uk._windows_build()
        assert build == 0 or 7000 < build < 100000

    @windows_only
    def test_this_machine_is_windows_11(self):
        assert uk._windows_build() >= uk._WIN11_BUILD


@windows_only
class TestCornerPath:
    def test_uses_dwm_on_windows_11(self, root):
        window = tk.Toplevel(root)
        window.overrideredirect(True)
        window.geometry("200x120+400+400")
        window.update_idletasks()
        try:
            assert uk.round_window_corners(window) == "dwm"
        finally:
            window.destroy()

    def test_falls_back_to_region_on_older_windows(self, root, monkeypatch):
        monkeypatch.setattr(window_mod, "_windows_build", lambda: 19045)   # Windows 10
        window = tk.Toplevel(root)
        window.overrideredirect(True)
        window.geometry("200x120+400+400")
        window.update_idletasks()
        try:
            assert uk.round_window_corners(window) == "region"
        finally:
            window.destroy()

    def test_falls_back_when_dwm_reports_failure(self, root, monkeypatch):
        """Meldet DWM einen Fehler, greift der alte Weg — kein Absturz."""
        class _Failing:
            def DwmSetWindowAttribute(self, *_args):
                return -2147024891      # E_ACCESSDENIED

        monkeypatch.setattr(ctypes, "windll", type(
            "W", (), {"dwmapi": _Failing(), "user32": ctypes.windll.user32,
                      "gdi32": ctypes.windll.gdi32, "ntdll": ctypes.windll.ntdll})())
        window = tk.Toplevel(root)
        window.overrideredirect(True)
        window.geometry("200x120+400+400")
        window.update_idletasks()
        try:
            assert uk.round_window_corners(window) == "region"
        finally:
            window.destroy()

    def test_never_raises_on_a_destroyed_window(self, root):
        window = tk.Toplevel(root)
        window.destroy()
        assert uk.round_window_corners(window) in {"keine", "region", "dwm"}

    def test_unmapped_window_is_handled(self, root):
        window = tk.Toplevel(root)
        window.withdraw()
        try:
            assert uk.round_window_corners(window) in {"dwm", "region", "keine"}
        finally:
            window.destroy()


@windows_only
class TestNoDoubleMechanism:
    def test_region_is_cleared_before_dwm(self, root, monkeypatch):
        """Beide Verfahren gleichzeitig wären ein Fehler — die Maske muss weg."""
        calls = []
        real_user32 = ctypes.windll.user32

        class _Spy:
            def __getattr__(self, name):
                target = getattr(real_user32, name)
                if name != "SetWindowRgn":
                    return target

                def wrapper(hwnd, region, redraw):
                    calls.append(region)
                    return target(hwnd, region, redraw)

                return wrapper

        monkeypatch.setattr(ctypes, "windll", type(
            "W", (), {"user32": _Spy(), "dwmapi": ctypes.windll.dwmapi,
                      "gdi32": ctypes.windll.gdi32,
                      "ntdll": ctypes.windll.ntdll})())
        window = tk.Toplevel(root)
        window.overrideredirect(True)
        window.geometry("200x120+400+400")
        window.update_idletasks()
        try:
            result = uk.round_window_corners(window)
        finally:
            window.destroy()
        if result == "dwm":
            assert calls and calls[0] is None, "Region wurde nicht entfernt"


class TestDocumentedLimits:
    """Die gemessenen Grenzen dürfen nicht stillschweigend verschwinden."""

    @staticmethod
    def _code_lines() -> str:
        """Nur echte Codezeilen — im Kommentar stehen die Namen absichtlich."""
        lines = [
            line for line in ui_kit_source().splitlines()
            if not line.lstrip().startswith("#")
        ]
        return chr(10).join(lines)

    def test_no_border_colour_call(self):
        assert "DWMWA_BORDER_COLOR" not in self._code_lines()

    def test_no_shadow_hacks(self):
        code = self._code_lines()
        for forbidden in ("DwmExtendFrameIntoClientArea", "CS_DROPSHADOW",
                          "ACCENT_ENABLE_ACRYLIC", "transparentcolor"):
            assert forbidden not in code, f"{forbidden} sollte nicht verwendet werden"

    def test_limits_are_documented(self):
        code = inspect.getsource(window_mod)
        assert "wirkungslos" in code, "Die gemessenen Grenzen fehlen im Kommentar"
