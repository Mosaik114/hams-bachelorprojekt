"""The level at the mouse pointer: a small level animation beside the pointer while Hams records.

A paper pill with an ink edge and seven rounded bars, turquoise for dictation and in the AI colour for *Ask AI*,
like the level meter in the settings. It sits a little *above and to the left* of the pointer, so the pointer
and whatever it points at stay visible; at a screen edge it moves to the other side.

The window (Windows only):

- a borderless popup with the extended styles `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE |
  WS_EX_TOOLWINDOW | WS_EX_TOPMOST`: clicks go through it, it never takes the focus (the text field the user
  dictates into keeps it) and it has no taskbar button;
- real per-pixel alpha: each frame is composed as an RGBA image (pill, then bars) and handed to Windows with
  `UpdateLayeredWindow` as a premultiplied 32-bit bitmap, so the anti-aliased rim blends with whatever lies
  underneath, light or dark.

Two clocks:

- **Following** runs in a thread of its own (`Follower`) at the refresh rate of the monitor under the pointer
  (`EnumDisplaySettings`, 240 Hz on a 240 Hz monitor; 60 Hz when a monitor does not say). The thread owns the
  window - it creates, moves, paints, hides and destroys it - so a move is a direct `SetWindowPos` without Tk and
  without a message to another thread. It waits for the next composition with `DwmFlush`, so a move lands on a
  frame of the screen; where `DwmFlush` does not block, `time.sleep` takes over, which uses a high-resolution
  waitable timer since Python 3.11 (no `timeBeginPeriod`). A Tk `after` timer could not do this: Tk's timers
  follow the 15.6 ms system tick, so the pill moved only 30 to 60 times a second and trailed the pointer.
- **Drawing** the bars stays on the Tk thread at `FRAME_MS`; a finished frame is handed to the follower, which
  paints it at its next tick. The bars are only redrawn when one of them changed by at least a pixel.

Over a full-screen application (a game, a video) the pill stays hidden: a topmost window on top of it would
force the desktop compositor back into the picture and cost the game its independent flip. The thread starts
on show and ends on hide; nothing runs while hidden. No mouse hook, no `timeBeginPeriod`.

Linux: not shown. X11 has no colour key for windows and no click-through without the Shape extension, and under
Wayland a client may neither place a window at the pointer nor read the pointer position outside its own
windows. The switch stays, but has no effect there.

Switched with `[ui] cursor_level` (*Settings → General → Level at the pointer*), on by default.
"""

from __future__ import annotations

import contextlib
import ctypes
import math
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass

from hams.config import CFG, logger
from hams.ui_kit.scale import Scale
from hams.ui_kit.theme import Depth, Theme

#: The states in which the animation is shown, and the colour token of its bars.
BAR_COLOURS = {"recording": "ACCENT_TINT", "recording_prompt": "AI"}

# Sizes in logical pixels (96 dpi); `Geometry.for_factor` scales them for the monitor under the pointer.
PILL_WIDTH = 56
PILL_HEIGHT = 20
BAR_COUNT = 7
BAR_WIDTH = 3
BAR_GAP = 3
BAR_MIN = 3  # at silence: a row of dots
BAR_MAX = 12
#: Distance between the pointer's hot spot and the pill (logical).
GAP = 14
#: Height of the standard arrow below its hot spot (logical); a pill moved below the pointer keeps clear of it.
POINTER_HEIGHT = 26

#: Frame interval of the bar animation in ms (about 60 Hz, on the Tk thread).
FRAME_MS = 16
#: Share of the remaining way a bar moves per frame: calm, but quick enough to follow speech.
EASE = 0.35
#: Each bar's share of the level: the middle one is tallest, like the meter's swell.
BAR_WEIGHTS = (0.45, 0.65, 0.85, 1.0, 0.85, 0.65, 0.45)

#: Follow rate when a monitor does not report its own (0 or 1 mean "hardware default").
FALLBACK_HZ = 60
#: Bounds for a reported refresh rate.
MIN_HZ = 24
MAX_HZ = 1000
#: How often the follower asks again for the refresh rate (it can change without a new monitor handle), in s.
RATE_CHECK_S = 2.0
#: How often the follower asks whether a full-screen application is in front, in s.
FULLSCREEN_CHECK_S = 0.5
#: Wait between ticks while there is no pointer position (lock screen, UAC prompt), in s.
NO_POINTER_S = 0.1
#: How long `hide` waits for the follower thread to end, in s.
STOP_TIMEOUT_S = 1.0


def frame_rate(reported: int | None) -> int:
    """The follow rate for a monitor that reports `reported` Hz: that rate, or `FALLBACK_HZ` when it reports none."""
    if not reported or reported <= 1:
        return FALLBACK_HZ
    return max(MIN_HZ, min(MAX_HZ, int(reported)))


@dataclass(frozen=True)
class Geometry:
    """All sizes in physical pixels for one scale factor."""

    factor: float
    width: int
    height: int
    bar_width: int
    bar_gap: int
    bar_min: int
    bar_max: int
    gap: int
    pointer_height: int

    @classmethod
    def for_factor(cls, factor: float) -> Geometry:
        def scaled(value: float) -> int:
            return max(1, int(round(value * factor)))

        return cls(
            factor,
            scaled(PILL_WIDTH),
            scaled(PILL_HEIGHT),
            scaled(BAR_WIDTH),
            scaled(BAR_GAP),
            scaled(BAR_MIN),
            scaled(BAR_MAX),
            scaled(GAP),
            scaled(POINTER_HEIGHT),
        )

    def bar_left(self, index: int) -> int:
        span = BAR_COUNT * self.bar_width + (BAR_COUNT - 1) * self.bar_gap
        return (self.width - span) // 2 + index * (self.bar_width + self.bar_gap)


def place(
    cursor: tuple[int, int], size: tuple[int, int], gap: int, pointer_height: int, area: tuple[int, int, int, int]
) -> tuple[int, int]:
    """Top-left corner of the pill for a pointer at `cursor` on a monitor with the work area `area`.

    Preferred: above and to the left of the hot spot, so it never covers the arrow (which points up-left and
    extends down-right). At the left edge it moves to the right of the pointer, at the top edge below the arrow;
    in the end it is clamped into the area, so it is never cut off by a monitor edge.
    """
    x_cursor, y_cursor = cursor
    width, height = size
    left, top, right, bottom = area
    x = x_cursor - gap - width
    y = y_cursor - gap - height
    if x < left:
        x = x_cursor + gap
    if y < top:
        y = y_cursor + pointer_height + gap // 2
    x = max(left, min(x, right - width))
    y = max(top, min(y, bottom - height))
    return x, y


#: Supersampling of the stadium shapes: drawn this many times larger, then reduced with a box filter.
SUPERSAMPLE = 4


def _hex_rgb(colour: str) -> tuple[int, int, int]:
    value = colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _stadium(draw, left: float, top: float, right: float, bottom: float) -> None:
    """An exact stadium: two half circles of the full height joined by a rectangle, so no corner is left over."""
    diameter = bottom - top
    if right - left <= diameter:
        draw.ellipse((left, top, right, bottom), fill=255)
        return
    draw.ellipse((left, top, left + diameter, bottom), fill=255)
    draw.ellipse((right - diameter, top, right, bottom), fill=255)
    draw.rectangle((left + diameter / 2, top, right - diameter / 2, bottom), fill=255)


def stadium(width: int, height: int, fill: str, border: str | None = None, border_w: float = 0.0):
    """A smooth stadium with real alpha (RGBA), drawn `SUPERSAMPLE` times larger; an ink rim of `border_w` px.

    Colour and coverage are kept apart: the colour layer reaches out to the edge, the shape only gives the alpha.
    Reducing a shape drawn on transparent black would darken the rim.
    """
    from PIL import Image, ImageDraw

    scale = SUPERSAMPLE
    big = (width * scale, height * scale)
    colour = Image.new("RGB", big, _hex_rgb(border or fill))
    mask = Image.new("L", big, 0)
    _stadium(ImageDraw.Draw(mask), 0, 0, big[0] - 1, big[1] - 1)  # PIL boxes include both ends
    if border and border_w > 0:
        inset = border_w * scale
        inner = Image.new("L", big, 0)
        _stadium(ImageDraw.Draw(inner), inset, inset, big[0] - 1 - inset, big[1] - 1 - inset)
        colour.paste(_hex_rgb(fill), (0, 0, *big), inner)
    reduced = colour.resize((width, height), Image.BOX)
    reduced.putalpha(mask.resize((width, height), Image.BOX))
    return reduced


def bar_targets(level: float, now: float, geometry: Geometry) -> list[float]:
    """Target height of each bar for `level` (0..1): a gentle swell around the middle, alive but calm."""
    level = max(0.0, min(1.0, level))
    span = geometry.bar_max - geometry.bar_min
    heights = []
    for index, weight in enumerate(BAR_WEIGHTS):
        wobble = 0.78 + 0.22 * math.sin(now * 9.0 + index * 1.7)
        heights.append(geometry.bar_min + span * level * weight * wobble)
    return heights


# --------------------------------------------------------------------------- Win32


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", ctypes.c_ulong),
        ("szDevice", ctypes.c_wchar * 32),  # MONITORINFOEXW: the device name for EnumDisplaySettings
    ]


class _DevMode(ctypes.Structure):
    """DEVMODEW (display variant); only `dmDisplayFrequency` is read."""

    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_uint16),
        ("dmDriverVersion", ctypes.c_uint16),
        ("dmSize", ctypes.c_uint16),
        ("dmDriverExtra", ctypes.c_uint16),
        ("dmFields", ctypes.c_uint32),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", ctypes.c_uint32),
        ("dmDisplayFixedOutput", ctypes.c_uint32),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_uint16),
        ("dmBitsPerPel", ctypes.c_uint32),
        ("dmPelsWidth", ctypes.c_uint32),
        ("dmPelsHeight", ctypes.c_uint32),
        ("dmDisplayFlags", ctypes.c_uint32),
        ("dmDisplayFrequency", ctypes.c_uint32),
        ("dmICMMethod", ctypes.c_uint32),
        ("dmICMIntent", ctypes.c_uint32),
        ("dmMediaType", ctypes.c_uint32),
        ("dmDitherType", ctypes.c_uint32),
        ("dmReserved1", ctypes.c_uint32),
        ("dmReserved2", ctypes.c_uint32),
        ("dmPanningWidth", ctypes.c_uint32),
        ("dmPanningHeight", ctypes.c_uint32),
    ]


class _WndClassEx(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", ctypes.c_void_p),
    ]


WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_APPWINDOW = 0x00040000
OVERLAY_STYLES = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
WS_POPUP = 0x80000000
GWL_EXSTYLE = -20
SW_HIDE = 0
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
#: A move: position only - no size, no change of the z-order, no activation.
MOVE_FLAGS = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
MONITOR_DEFAULTTONEAREST = 2
ENUM_CURRENT_SETTINGS = -1
PM_REMOVE = 1
ULW_ALPHA = 2
AC_SRC_ALPHA = 1
ERROR_CLASS_ALREADY_EXISTS = 1410
#: SHQueryUserNotificationState: a full-screen application, a full-screen Direct3D application.
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
CLASS_NAME = "HamsCursorLevel"


class _Size(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _Blend(ctypes.Structure):
    _fields_ = [
        ("op", ctypes.c_ubyte),
        ("flags", ctypes.c_ubyte),
        ("alpha", ctypes.c_ubyte),
        ("format", ctypes.c_ubyte),
    ]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def premultiplied_bgra(image) -> bytes:
    """An RGBA image as the bytes of a top-down 32-bit DIB: B, G, R premultiplied with A, then A."""
    from PIL import Image, ImageChops

    red, green, blue, alpha = image.convert("RGBA").split()
    channels = [ImageChops.multiply(channel, alpha) for channel in (blue, green, red)]
    return Image.merge("RGBA", (*channels, alpha)).tobytes()


def _signature(function, argtypes: list, restype=ctypes.c_int) -> None:
    function.argtypes = argtypes
    function.restype = restype


class Win32Desktop:
    """The few Win32 calls the overlay needs; tests replace it with a fake.

    Own `WinDLL` instances, so the signatures set here never change those other modules see through `windll`.
    `create`, `move`, `show`, `hide`, `paint`, `pump` and `destroy` run on the follower thread that owns the window.
    """

    _class_lock = threading.Lock()
    _class_registered = False

    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32")
        self.kernel32 = ctypes.WinDLL("kernel32")
        self.shell32 = ctypes.WinDLL("shell32")
        self.shcore = self.dwmapi = None
        with contextlib.suppress(OSError):  # before Windows 8.1 there is no per-monitor DPI
            self.shcore = ctypes.WinDLL("shcore")
        with contextlib.suppress(OSError):
            self.dwmapi = ctypes.WinDLL("dwmapi")
        void, uint, intp = ctypes.c_void_p, ctypes.c_uint, ctypes.c_int
        user32, gdi32 = self.user32, self.gdi32
        _signature(user32.SetWindowPos, [void, void, intp, intp, intp, intp, uint])
        _signature(user32.ShowWindow, [void, intp])
        _signature(user32.MonitorFromPoint, [_Point, ctypes.c_ulong], void)
        _signature(user32.GetMonitorInfoW, [void, ctypes.POINTER(_MonitorInfo)])
        _signature(user32.EnumDisplaySettingsW, [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(_DevMode)])
        _signature(user32.GetCursorPos, [ctypes.POINTER(_Point)])
        _signature(user32.GetWindowLongW, [void, intp], ctypes.c_long)
        _signature(user32.GetDC, [void], void)
        _signature(user32.ReleaseDC, [void, void])
        _signature(user32.RegisterClassExW, [ctypes.POINTER(_WndClassEx)], ctypes.c_uint16)
        _signature(user32.CreateWindowExW, [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
                   + [intp] * 4 + [void] * 4, void)  # fmt: skip
        _signature(user32.DestroyWindow, [void])
        _signature(user32.PeekMessageW, [ctypes.POINTER(wintypes.MSG), void, uint, uint, uint])
        _signature(user32.TranslateMessage, [ctypes.POINTER(wintypes.MSG)])
        _signature(user32.DispatchMessageW, [ctypes.POINTER(wintypes.MSG)], ctypes.c_ssize_t)
        _signature(user32.UpdateLayeredWindow, [void, void, void, ctypes.POINTER(_Size), void,
                   ctypes.POINTER(_Point), ctypes.c_uint32, ctypes.POINTER(_Blend), ctypes.c_uint32])  # fmt: skip
        _signature(self.kernel32.GetModuleHandleW, [ctypes.c_wchar_p], void)
        _signature(self.shell32.SHQueryUserNotificationState, [ctypes.POINTER(ctypes.c_int)], ctypes.c_long)
        _signature(gdi32.CreateCompatibleDC, [void], void)
        _signature(gdi32.CreateDIBSection, [void, ctypes.POINTER(_BitmapInfoHeader), uint,
                   ctypes.POINTER(ctypes.c_void_p), void, ctypes.c_uint32], void)  # fmt: skip
        _signature(gdi32.SelectObject, [void, void], void)
        _signature(gdi32.DeleteObject, [void])
        _signature(gdi32.DeleteDC, [void])
        if self.dwmapi is not None:
            _signature(self.dwmapi.DwmFlush, [], ctypes.c_long)

    # ------------------------------------------------------------------ queries (any thread)

    def cursor(self) -> tuple[int, int] | None:
        point = _Point()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            return None  # e.g. on the secure desktop (lock screen, UAC prompt)
        return point.x, point.y

    def _monitor_info(self, handle: int) -> _MonitorInfo | None:
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if handle and self.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return info
        return None

    def monitor(self, cursor: tuple[int, int]) -> tuple[int, tuple[int, int, int, int], float]:
        """Handle, work area and scale factor of the monitor nearest to `cursor`."""
        handle = self.user32.MonitorFromPoint(_Point(*cursor), MONITOR_DEFAULTTONEAREST) or 0
        area = (cursor[0] - 4000, cursor[1] - 4000, cursor[0] + 4000, cursor[1] + 4000)
        info = self._monitor_info(handle)
        if info is not None:
            work = info.rcWork
            area = (work.left, work.top, work.right, work.bottom)
        factor = Scale.factor
        if handle and self.shcore is not None:
            dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
            if self.shcore.GetDpiForMonitor(ctypes.c_void_p(handle), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                factor = dpi_x.value / 96
        return handle, area, factor

    def refresh_rate(self, handle: int) -> int:
        """The current refresh rate of the monitor `handle` in Hz; 0 if Windows does not tell."""
        info = self._monitor_info(handle)
        if info is None:
            return 0
        mode = _DevMode()
        mode.dmSize = ctypes.sizeof(_DevMode)
        if not self.user32.EnumDisplaySettingsW(info.szDevice, ENUM_CURRENT_SETTINGS, ctypes.byref(mode)):
            return 0
        return int(mode.dmDisplayFrequency)

    def fullscreen(self) -> bool:
        """Whether a full-screen application (a game, a video, a presentation) is in front."""
        state = ctypes.c_int()
        if self.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
        return state.value in (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN)

    def styles(self, hwnd: int) -> int:
        return int(self.user32.GetWindowLongW(hwnd, GWL_EXSTYLE))

    # ------------------------------------------------------------------ the window (follower thread)

    def _register_class(self, instance: int) -> None:
        with Win32Desktop._class_lock:
            if Win32Desktop._class_registered:
                return
            window_class = _WndClassEx()
            window_class.cbSize = ctypes.sizeof(_WndClassEx)
            # No Python window procedure: DefWindowProcW itself, so no message ever waits for the GIL.
            window_class.lpfnWndProc = ctypes.cast(self.user32.DefWindowProcW, ctypes.c_void_p).value
            window_class.hInstance = instance
            window_class.lpszClassName = CLASS_NAME
            if not self.user32.RegisterClassExW(ctypes.byref(window_class)):
                error = ctypes.get_last_error()
                if error != ERROR_CLASS_ALREADY_EXISTS:  # pragma: no cover - out of atoms
                    raise ctypes.WinError(error)
            Win32Desktop._class_registered = True

    def create(self) -> int:
        """A hidden overlay window owned by the calling thread (one class for the whole process)."""
        instance = self.kernel32.GetModuleHandleW(None)
        self._register_class(instance)
        hwnd = self.user32.CreateWindowExW(
            OVERLAY_STYLES | WS_EX_TOPMOST,
            CLASS_NAME,
            "Hams",
            WS_POPUP,
            -32000,
            -32000,
            1,
            1,
            None,
            None,
            instance,
            None,
        )
        if not hwnd:  # pragma: no cover - out of window handles
            raise ctypes.WinError(ctypes.get_last_error())
        return int(hwnd)

    def destroy(self, hwnd: int) -> None:
        self.user32.DestroyWindow(hwnd)

    def pump(self) -> None:
        """Handles the messages of the follower thread (broadcasts such as WM_SETTINGCHANGE wait for them)."""
        message = wintypes.MSG()
        while self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE):
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))

    def show(self, hwnd: int, x: int, y: int) -> None:
        self.user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def move(self, hwnd: int, x: int, y: int) -> None:
        """Moves the window without touching its picture: `UpdateLayeredWindow` with only a position, which costs
        about two thirds of a `SetWindowPos` (measured: 47 against 75 µs); `SetWindowPos` if it is refused."""
        if not self.user32.UpdateLayeredWindow(hwnd, None, ctypes.byref(_Point(x, y)), None, None, None, 0, None, 0):
            self.user32.SetWindowPos(hwnd, None, x, y, 0, 0, MOVE_FLAGS)

    def hide(self, hwnd: int) -> None:
        self.user32.ShowWindow(hwnd, SW_HIDE)

    def paint(self, hwnd: int, image) -> bool:
        """Puts the RGBA `image` into the layered window with per-pixel alpha (the position stays)."""
        width, height = image.size
        data = premultiplied_bgra(image)
        screen = self.user32.GetDC(None)
        memory = self.gdi32.CreateCompatibleDC(screen)
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bitmap = self.gdi32.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        try:
            if not bitmap or not bits.value:  # pragma: no cover - out of GDI resources
                return False
            ctypes.memmove(bits, data, len(data))
            old = self.gdi32.SelectObject(memory, bitmap)
            ok = self.user32.UpdateLayeredWindow(
                hwnd,
                screen,
                None,
                ctypes.byref(_Size(width, height)),
                memory,
                ctypes.byref(_Point(0, 0)),
                0,
                ctypes.byref(_Blend(0, 0, 255, AC_SRC_ALPHA)),
                ULW_ALPHA,
            )
            self.gdi32.SelectObject(memory, old)
            return bool(ok)
        finally:
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            self.gdi32.DeleteDC(memory)
            self.user32.ReleaseDC(None, screen)

    def wait_frame(self, previous: float, period: float, stop: threading.Event) -> None:
        """Waits for the next frame after `previous` (a `perf_counter` time), `period` seconds apart.

        `DwmFlush` returns when the compositor has put out its next frame, so the move after it lands on a frame
        of the screen. A frame counts once three quarters of `period` have passed: a compositor faster than the
        monitor under the pointer is waited out, a normal frame jitter is not. Where `DwmFlush` does not block
        (no composition pending, an error), `time.sleep` waits instead - a high-resolution timer since 3.11.
        """
        due = previous + period * 0.75
        while not stop.is_set():
            start = time.perf_counter()
            if start >= due:
                return
            if self.dwmapi is None or self.dwmapi.DwmFlush() != 0 or time.perf_counter() - start < 0.0005:
                break
        if not stop.is_set():
            time.sleep(max(0.0, previous + period - time.perf_counter()))


# --------------------------------------------------------------------------- follower


class Follower:
    """Keeps the pill beside the pointer at the refresh rate of the monitor under it, in a thread of its own.

    The thread owns the window: it creates it, paints the frames the Tk side hands over (`submit`), moves it,
    hides it over full-screen applications and destroys it when stopped. Moving a window from the thread that
    owns it is a direct call; from another thread Windows would hand the move to the Tk thread and wait for it
    (or, with `SWP_ASYNCWINDOWPOS`, queue it behind Tk's own work).
    """

    def __init__(self, desktop, cursor: tuple[int, int], monitor: tuple, geometry: Geometry) -> None:
        self.desktop = desktop
        #: Handle, work area and scale factor of the monitor under the pointer, read by the Tk side.
        self.monitor = monitor
        self.hz = FALLBACK_HZ
        self.hwnd = 0
        #: Ticks of the thread and moves of the window since the start.
        self.ticks = self.moves = 0
        self.visible = False
        self.covered = False
        self._cursor = cursor
        self._pointer = True
        self._geometry = geometry
        self._frame = None
        self._painted: Geometry | None = None
        self._position: tuple[int, int] | None = None
        self._next_rate = self._next_cover = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="cursor-level", daemon=True)

    # ------------------------------------------------------------------ Tk side

    def start(self) -> None:
        self._thread.start()

    def submit(self, image, geometry: Geometry) -> None:
        """A new frame for the next tick; an older one not yet painted is dropped."""
        with self._lock:
            self._frame, self._geometry = image, geometry

    def stop(self) -> bool:
        """Ends the thread (it hides and destroys the window); True once it has ended."""
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(STOP_TIMEOUT_S)
        return not self._thread.is_alive()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    # ------------------------------------------------------------------ thread

    def _run(self) -> None:
        try:
            self.hwnd = self.desktop.create()
        except OSError:
            logger.exception("Level at the pointer: window not created")
            return
        try:
            previous = time.perf_counter()
            while not self._stop.is_set():
                self.desktop.pump()
                self.tick(previous)
                period = 1.0 / self.hz if self._pointer else NO_POINTER_S
                self.desktop.wait_frame(previous, period, self._stop)
                previous = time.perf_counter()
        except Exception:
            logger.exception("Level at the pointer: following stopped")
        finally:
            self._close_window()

    def _close_window(self) -> None:
        try:
            if self.visible:
                self.desktop.hide(self.hwnd)
            self.desktop.destroy(self.hwnd)
        except OSError:  # pragma: no cover - the window is going anyway
            logger.exception("Level at the pointer: closing the window failed")
        self.visible = False

    def tick(self, now: float) -> None:
        """One frame: follow the pointer, paint a waiting frame, keep away from full-screen applications."""
        self.ticks += 1
        self._follow_pointer()
        self._check_screen(now)
        with self._lock:
            frame, self._frame = self._frame, None
            geometry = self._geometry
        if frame is not None:
            self.desktop.paint(self.hwnd, frame)
            self._painted = geometry
        if self._painted is None:
            return  # nothing to show yet
        self._place()

    def _follow_pointer(self) -> None:
        cursor = self.desktop.cursor()
        self._pointer = cursor is not None
        if cursor is None or cursor == self._cursor:
            return
        self._cursor = cursor
        monitor = self.desktop.monitor(cursor)
        if monitor[0] != self.monitor[0]:
            self._next_rate = 0.0  # another monitor: ask for its rate at once
        self.monitor = monitor

    def _check_screen(self, now: float) -> None:
        if now >= self._next_rate:
            self._next_rate = now + RATE_CHECK_S
            hz = frame_rate(self.desktop.refresh_rate(self.monitor[0]))
            if hz != self.hz:
                logger.debug("Level at the pointer follows at %d Hz", hz)
                self.hz = hz
        if now >= self._next_cover:
            self._next_cover = now + FULLSCREEN_CHECK_S
            covered = bool(self.desktop.fullscreen())
            if covered != self.covered:
                logger.info("Level at the pointer %s", "paused: full-screen application" if covered else "resumed")
                self.covered = covered

    def _place(self) -> None:
        if self.covered:
            if self.visible:
                self.desktop.hide(self.hwnd)
                self.visible = False
            return
        size = self._painted
        position = place(self._cursor, (size.width, size.height), size.gap, size.pointer_height, self.monitor[1])
        if not self.visible:
            self.desktop.show(self.hwnd, *position)
            self.visible = True
        elif position != self._position:
            self.desktop.move(self.hwnd, *position)
            self.moves += 1
        self._position = position


# --------------------------------------------------------------------------- overlay


class CursorLevel:
    """Shows the level beside the pointer while recording. Fed by the app: `follow_state` and `set_level`."""

    def __init__(self, root: tk.Misc, desktop=None, *, supported: bool | None = None) -> None:
        self.root = root
        self.supported = sys.platform == "win32" if supported is None else supported
        self._desktop = desktop
        #: The last frame composed (RGBA), for the tests and the pictures.
        self.frame_image = None
        self.state = "ready"
        self.level = 0.0
        self.shown = False
        self.follower: Follower | None = None
        self._job: str | None = None
        self._cursor: tuple[int, int] | None = None
        self._geometry: Geometry | None = None
        self._look: tuple | None = None
        self._shown_heights: list[int] = []
        self._heights: list[float] = []
        self._images: dict = {}
        self._background = None
        self._bar_colour = ""
        #: Counters for the log and the measurements: Tk frames and redraws since the last show; the follower's
        #: ticks and moves are kept from the last one when it ends.
        self.frames = self.redraws = 0
        self._moves = self._ticks = 0
        self._closed = False

    # ------------------------------------------------------------------ app interface

    def wanted(self) -> bool:
        return self.supported and not self._closed and CFG.cursor_level and self.state in BAR_COLOURS

    def follow_state(self, state: str) -> None:
        """Every state change of the main window: shows the overlay while recording, hides it otherwise."""
        self.state = state
        if state not in BAR_COLOURS:
            self.level = 0.0
        self.refresh()

    def set_level(self, level: float) -> None:
        """The display level (0..1) of the current recording, about 20 times a second."""
        self.level = max(0.0, min(1.0, float(level)))

    def refresh(self) -> None:
        """Shows or hides according to the state and the switch (the settings call this when it changes)."""
        if self.wanted():
            self._show()
        else:
            self.hide()

    def hide(self) -> None:
        self._cancel()
        follower, self.follower = self.follower, None
        if follower is not None:
            if not follower.stop():  # pragma: no cover - a DwmFlush that does not come back
                logger.warning("Level at the pointer: the follower did not end in time")
            self._moves, self._ticks = follower.moves, follower.ticks
            logger.debug(
                "Level at the pointer hidden: %d frames, %d moves, %d redraws, %d ticks at %d Hz",
                *self.counters(),
                follower.ticks,
                follower.hz,
            )
        self.shown = False

    def close(self) -> None:
        """At quit: hide and end the follower; afterwards nothing is shown any more."""
        self.hide()
        self._closed = True
        self.frame_image = None
        self._images.clear()
        self._background = None

    @property
    def moves(self) -> int:
        return self.follower.moves if self.follower is not None else self._moves

    def counters(self) -> tuple[int, int, int]:
        return self.frames, self.moves, self.redraws

    # ------------------------------------------------------------------ inside

    @property
    def desktop(self):
        if self._desktop is None:
            self._desktop = Win32Desktop()
        return self._desktop

    def _show(self) -> None:
        if self.follower is None:
            cursor = self.desktop.cursor() or self._cursor or (0, 0)
            monitor = self.desktop.monitor(cursor)
            self._cursor = cursor
            self._use_factor(monitor[2])
            self._paint_look()
            self.frames = self.redraws = 0
            self.follower = Follower(self.desktop, cursor, monitor, self._geometry)
            self._shown_heights = [-1] * BAR_COUNT  # the new window starts empty: paint a frame at once
            self._draw_bars(time.monotonic())
            self.follower.start()
            self.shown = True
        else:
            self._paint_look()  # dictation and Ask AI differ in colour
            self._draw_bars(time.monotonic())
        if self._job is None:
            self._job = self.root.after(FRAME_MS, self._frame)

    def _use_factor(self, factor: float) -> None:
        if self._geometry is None or abs(self._geometry.factor - factor) > 1e-3:
            self._geometry = Geometry.for_factor(factor)
            self._look = None  # sizes changed: render again

    def _paint_look(self) -> None:
        """Pill and bar images for the current appearance, scale and mode; only when one of them changed."""
        geometry = self._geometry
        colour = getattr(Theme, BAR_COLOURS.get(self.state, "ACCENT_TINT"))
        look = (Theme.mode, geometry, colour)
        if look == self._look:
            return
        self._look = look
        self._images.clear()
        self._background = stadium(
            geometry.width,
            geometry.height,
            Theme.GROUP_SURFACE,
            border=Theme.INK,
            border_w=max(1.0, Depth.INK * geometry.factor),
        )
        self._shown_heights = [-1] * BAR_COUNT
        self._heights = [float(geometry.bar_min)] * BAR_COUNT
        self._bar_colour = colour

    def _bar_image(self, height: int):
        """A rounded bar of `height` pixels; kept per height (at most `bar_max` of them per look)."""
        image = self._images.get(height)
        if image is None:
            width = self._geometry.bar_width
            bar = stadium(width, height, self._bar_colour)
            image = self._images[height] = bar
        return image

    def _draw_bars(self, now: float) -> None:
        """Eases the bars toward the level; composes a frame for the follower only when a bar changed by a pixel."""
        geometry = self._geometry
        changed = False
        for index, target in enumerate(bar_targets(self.level, now, geometry)):
            current = self._heights[index] + (target - self._heights[index]) * EASE
            self._heights[index] = current
            height = max(geometry.bar_min, min(geometry.bar_max, int(round(current))))
            if height != self._shown_heights[index]:
                self._shown_heights[index] = height
                changed = True
        if not changed:
            return
        frame = self._background.copy()
        for index, height in enumerate(self._shown_heights):
            frame.alpha_composite(self._bar_image(height), (geometry.bar_left(index), (geometry.height - height) // 2))
        self.frame_image = frame
        self.follower.submit(frame, geometry)
        self.redraws += 1

    def _frame(self) -> None:
        self._job = None
        if not self.wanted():
            self.hide()
            return
        self.frames += 1
        factor = self.follower.monitor[2]
        if abs(factor - self._geometry.factor) > 1e-3:  # the pointer went to a monitor with another scale
            self._use_factor(factor)
            self._paint_look()
        self._draw_bars(time.monotonic())
        self._job = self.root.after(FRAME_MS, self._frame)

    def _cancel(self) -> None:
        if self._job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.root.after_cancel(self._job)
            self._job = None
