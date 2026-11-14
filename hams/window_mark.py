"""The painted Hams mark in the head of every window (main window, settings, setup assistant)."""

from __future__ import annotations

import contextlib
import tkinter as tk

from PIL import ImageTk

from hams import branding
from hams import ui_kit as uk
from hams.ui_kit.caches import _img_cache

#: Size of the mark in logical pixels: as tall as the window title.
MARK_SIZE = 26


def mark_photo(size: int = MARK_SIZE) -> ImageTk.PhotoImage | None:
    """The mark at `size` logical pixels for the current DPI (cached), or None when the file is missing."""
    key = ("ink-mark", uk.px(size))
    cached = _img_cache.fetch(key)
    if cached is not None:
        return cached
    image = branding.ink_mark_image(uk.px(size))
    return _img_cache.store(key, ImageTk.PhotoImage(image)) if image is not None else None


def mark_label(master: tk.Misc, *, bg: str, size: int = MARK_SIZE) -> tk.Label:
    """A label with the mark; it follows DPI changes. Empty (no image) when the file is missing."""
    label = tk.Label(master, bg=bg, bd=0, highlightthickness=0, padx=0, pady=0)

    def paint(_factor: float | None = None) -> None:
        photo = mark_photo(size)
        with contextlib.suppress(tk.TclError):
            label.configure(image=photo if photo is not None else "")
            label.image = photo  # keep a reference, Tk does not

    def forget(event: tk.Event) -> None:
        if event.widget is label:
            uk.Scale.unregister(paint)

    paint()
    uk.Scale.register(paint)
    label.bind("<Destroy>", forget, add="+")
    return label
