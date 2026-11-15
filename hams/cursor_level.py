"""The level at the mouse pointer: a small level animation beside the pointer while Hams records.

A paper pill with an ink edge and seven rounded bars, turquoise for dictation and in the AI colour for *Ask AI*,
like the level meter in the settings. It sits a little *above and to the left* of the pointer, so the pointer
and whatever it points at stay visible; at a screen edge it moves to the other side.

The window (Windows only):

- a borderless, always-on-top Tk toplevel with the extended styles `WS_EX_LAYERED | WS_EX_TRANSPARENT |
  WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`: clicks go through it, it never takes the focus (the text field the user
  dictates into keeps it) and it has no taskbar button;
- real per-pixel alpha: each frame is composed as an RGBA image (pill, then bars) and handed to Windows with
  `UpdateLayeredWindow` as a premultiplied 32-bit bitmap, so the anti-aliased rim blends with whatever lies
  underneath, light or dark. A colour key (`-transparentcolor`) left a light, jagged seam on dark backgrounds:
  the smoothed rim pixels were mixed with the key colour instead of the screen;
- built once on the first show, afterwards only shown, moved and hidden (`ShowWindow`/`SetWindowPos` with
  `SWP_NOACTIVATE`): no window per dictation, no leak.

While shown, a timer of `FRAME_MS` (about 60 Hz) reads `GetCursorPos`; the window only moves when the pointer
did, and the bars are only redrawn when one of them changed by at least a pixel. Nothing runs while hidden.

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
import time
import tkinter as tk
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

#: Frame interval of the follow timer in ms (about 60 Hz).
FRAME_MS = 16
#: Share of the remaining way a bar moves per frame: calm, but quick enough to follow speech.
EASE = 0.35
#: Each bar's share of the level: the middle one is tallest, like the meter's swell.
BAR_WEIGHTS = (0.45, 0.65, 0.85, 1.0, 0.85, 0.65, 0.45)


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
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _Rect), ("rcWork", _Rect), ("dwFlags", ctypes.c_ulong)]


GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
OVERLAY_STYLES = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
MONITOR_DEFAULTTONEAREST = 2
ULW_ALPHA = 2
AC_SRC_ALPHA = 1


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


class Win32Desktop:
    """The few user32 calls the overlay needs; tests replace it with a fake."""

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32
        self.shcore = None
        with contextlib.suppress(OSError):  # before Windows 8.1 there is no per-monitor DPI
            self.shcore = ctypes.windll.shcore
        self.user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        self.user32.MonitorFromPoint.argtypes = [_Point, ctypes.c_ulong]
        self.user32.MonitorFromPoint.restype = ctypes.c_void_p
        self.gdi32 = ctypes.windll.gdi32
        self.user32.GetDC.argtypes = [ctypes.c_void_p]
        self.user32.GetDC.restype = ctypes.c_void_p
        self.user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.user32.UpdateLayeredWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(_Size),
            ctypes.c_void_p,
            ctypes.POINTER(_Point),
            ctypes.c_uint32,
            ctypes.POINTER(_Blend),
            ctypes.c_uint32,
        ]
        self.gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self.gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        self.gdi32.CreateDIBSection.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_BitmapInfoHeader),
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self.gdi32.CreateDIBSection.restype = ctypes.c_void_p
        self.gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi32.SelectObject.restype = ctypes.c_void_p
        self.gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        self.gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

    def cursor(self) -> tuple[int, int] | None:
        point = _Point()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            return None  # e.g. on the secure desktop (lock screen, UAC prompt)
        return point.x, point.y

    def monitor(self, cursor: tuple[int, int]) -> tuple[int, tuple[int, int, int, int], float]:
        """Handle, work area and scale factor of the monitor nearest to `cursor`."""
        handle = self.user32.MonitorFromPoint(_Point(*cursor), MONITOR_DEFAULTTONEAREST) or 0
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        area = (cursor[0] - 4000, cursor[1] - 4000, cursor[0] + 4000, cursor[1] + 4000)
        if handle and self.user32.GetMonitorInfoW(ctypes.c_void_p(handle), ctypes.byref(info)):
            work = info.rcWork
            area = (work.left, work.top, work.right, work.bottom)
        factor = Scale.factor
        if handle and self.shcore is not None:
            dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
            if self.shcore.GetDpiForMonitor(ctypes.c_void_p(handle), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                factor = dpi_x.value / 96
        return handle, area, factor

    def prepare(self, hwnd: int) -> None:
        """Sets the overlay styles. `WS_EX_LAYERED` is taken off once first: Tk may have used
        `SetLayeredWindowAttributes` on the window, and then `UpdateLayeredWindow` fails (error 87)."""
        style = int(self.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & ~WS_EX_APPWINDOW
        self.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_LAYERED)
        self.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | OVERLAY_STYLES)

    def styles(self, hwnd: int) -> int:
        return int(self.user32.GetWindowLongW(hwnd, GWL_EXSTYLE))

    def show(self, hwnd: int, x: int, y: int) -> None:
        self.user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def move(self, hwnd: int, x: int, y: int) -> None:
        self.user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)

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


# --------------------------------------------------------------------------- overlay


class CursorLevel:
    """Shows the level beside the pointer while recording. Fed by the app: `follow_state` and `set_level`."""

    def __init__(self, root: tk.Misc, desktop=None, *, supported: bool | None = None) -> None:
        self.root = root
        self.supported = sys.platform == "win32" if supported is None else supported
        self._desktop = desktop
        self.win: tk.Toplevel | None = None
        #: The last frame handed to Windows (RGBA), for the tests and the pictures.
        self.frame_image = None
        self.hwnd = 0
        self.state = "ready"
        self.level = 0.0
        self.shown = False
        self._job: str | None = None
        self._cursor: tuple[int, int] | None = None
        self._position: tuple[int, int] | None = None
        self._monitor = 0
        self._area = (0, 0, 0, 0)
        self._geometry: Geometry | None = None
        self._look: tuple | None = None
        self._shown_heights: list[int] = []
        self._heights: list[float] = []
        self._images: dict = {}
        self._background = None
        self._bar_colour = ""
        #: Counters for the log and the measurements: frames, moves, redraws since the last show.
        self.frames = self.moves = self.redraws = 0
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
        if self.shown and self.hwnd:
            try:
                self.desktop.hide(self.hwnd)
            except OSError:  # pragma: no cover - the window is going anyway
                logger.exception("Level at the pointer: hiding failed")
        if self.shown:
            logger.debug("Level at the pointer hidden: %d frames, %d moves, %d redraws", *self.counters())
        self.shown = False

    def close(self) -> None:
        """At quit: hide and release the window; afterwards nothing is shown any more."""
        self.hide()
        self._closed = True
        if self.win is not None:
            with contextlib.suppress(tk.TclError):  # the root went first
                self.win.destroy()
        self.win = self.frame_image = None
        self._images.clear()
        self._background = None

    def counters(self) -> tuple[int, int, int]:
        return self.frames, self.moves, self.redraws

    # ------------------------------------------------------------------ inside

    @property
    def desktop(self):
        if self._desktop is None:
            self._desktop = Win32Desktop()
        return self._desktop

    def _show(self) -> None:
        cursor = self.desktop.cursor()
        if cursor is None:
            cursor = self._cursor or (0, 0)
        self._use_monitor(*self.desktop.monitor(cursor))
        self._ensure_window()
        self._paint_look()
        self._cursor = cursor
        self._position = self._target(cursor)
        self._draw_bars(time.monotonic())
        if not self.shown:
            self.frames = self.moves = self.redraws = 0
            self.desktop.show(self.hwnd, *self._position)
            self.shown = True
        if self._job is None:
            self._job = self.root.after(FRAME_MS, self._frame)

    def _use_monitor(self, handle: int, area: tuple[int, int, int, int], factor: float) -> None:
        self._monitor, self._area = handle, area
        if self._geometry is None or abs(self._geometry.factor - factor) > 1e-3:
            self._geometry = Geometry.for_factor(factor)
            self._look = None  # sizes changed: render again

    def _target(self, cursor: tuple[int, int]) -> tuple[int, int]:
        geometry = self._geometry
        return place(cursor, (geometry.width, geometry.height), geometry.gap, geometry.pointer_height, self._area)

    def _ensure_window(self) -> None:
        if self.win is not None:
            return
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        self.win = win
        # Mapped once far off screen so Tk creates the native window; from then on only user32 shows and hides it.
        win.geometry("+-32000+-32000")
        win.deiconify()
        win.update_idletasks()
        self.hwnd = int(win.tk.call("wm", "frame", str(win)), 16) if sys.platform == "win32" else win.winfo_id()
        self.desktop.prepare(self.hwnd)
        self.desktop.hide(self.hwnd)
        logger.info("Level at the pointer: window built")

    def _paint_look(self) -> None:
        """Pill and bar images for the current appearance, scale and mode; only when one of them changed."""
        geometry = self._geometry
        colour = getattr(Theme, BAR_COLOURS.get(self.state, "ACCENT_TINT"))
        look = (Theme.mode, geometry, colour)
        if look == self._look:
            return
        self._look = look
        # Tk's own idea of the size matches the bitmap, so it never sizes the window back.
        self.win.geometry(f"{geometry.width}x{geometry.height}")
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
        self.desktop.prepare(self.hwnd)

    def _bar_image(self, height: int):
        """A rounded bar of `height` pixels; kept per height (at most `bar_max` of them per look)."""
        image = self._images.get(height)
        if image is None:
            width = self._geometry.bar_width
            bar = stadium(width, height, self._bar_colour)
            image = self._images[height] = bar
        return image

    def _draw_bars(self, now: float) -> None:
        """Eases the bars toward the level; composes and paints a frame only when a bar changed by a pixel."""
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
        self.desktop.paint(self.hwnd, frame)
        self.redraws += 1

    def _frame(self) -> None:
        self._job = None
        if not self.wanted():
            self.hide()
            return
        self.frames += 1
        cursor = self.desktop.cursor()
        if cursor is not None and cursor != self._cursor:
            self._cursor = cursor
            monitor = self.desktop.monitor(cursor)
            if monitor[:2] != (self._monitor, self._area):
                self._use_monitor(*monitor)
                self._paint_look()
            position = self._target(cursor)
            if position != self._position:
                self._position = position
                self.desktop.move(self.hwnd, *position)
                self.moves += 1
        self._draw_bars(time.monotonic())
        self._job = self.root.after(FRAME_MS, self._frame)

    def _cancel(self) -> None:
        if self._job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.root.after_cancel(self._job)
            self._job = None
