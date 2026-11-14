"""Text messen und kuerzen."""

from __future__ import annotations

import tkinter as tk

from hams.ui_kit.scale import px


def _line_height(tk_font, widget: tk.Misc) -> int:
    """Zeilenhöhe einer Schrift in Pixeln."""
    try:
        return int(widget.tk.call("font", "metrics", tk_font, "-linespace"))
    except tk.TclError:  # pragma: no cover
        return px(16)


def _text_width(widget: tk.Misc, text: str, tk_font) -> int:
    """Textbreite in Pixeln, über Tk gemessen."""
    try:
        return int(widget.tk.call("font", "measure", tk_font, text))
    except tk.TclError:  # pragma: no cover
        return len(text) * px(7)


def _fit_text(widget: tk.Misc, text: str, tk_font, available: int) -> str:
    """Kürzt mit Auslassungspunkten, wenn der Text nicht passt."""
    if available <= 0 or _text_width(widget, text, tk_font) <= available:
        return text
    ellipsis = "…"
    while text and _text_width(widget, text + ellipsis, tk_font) > available:
        text = text[:-1]
    return text + ellipsis
