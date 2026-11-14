"""Screenshots of real Tk windows (Windows only), for the snapshot run and visual checks.

`PrintWindow` with `PW_RENDERFULLCONTENT` renders a window even on a desktop nobody looks at, so the snapshot
run works on the invisible test desktop.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import Image

PW_RENDERFULLCONTENT = 2


class _BitmapInfo(ctypes.Structure):
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


def capture(window, path: Path) -> Image.Image:
    """Saves what `window` (a Tk toplevel) shows to `path` and returns the image."""
    if sys.platform != "win32":  # pragma: no cover - the snapshot run is Windows only
        raise RuntimeError("capture needs Windows")
    from hams.ui_kit.window import _window_handle

    for _ in range(4):
        window.update()
    hwnd = _window_handle(window)
    rect = wintypes.RECT()
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)
    try:
        user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)
        info = _BitmapInfo(ctypes.sizeof(_BitmapInfo), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return image
