"""Text messen und kuerzen."""

from __future__ import annotations

import functools
import sys
import tkinter as tk

from PIL import ImageFont

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


def _pil_font(tk_font):
    """Tk-Schriftrolle als PIL-Schrift — für Text, der ins Bild gezeichnet wird."""
    try:
        family = tk_font.cget("family")
        size = abs(int(tk_font.cget("size")))
    except AttributeError:
        family, size = "Segoe UI", px(12)
    return _pil_font_for(family, size)


@functools.lru_cache(maxsize=64)
def _pil_font_for(family: str, size: int):
    """The PIL font for one family and size, looked up once.

    `ImageFont.truetype` with a name instead of a path walks the whole Windows font folder for every name it
    cannot find; uncached that cost about 90 ms per opening of the settings window and 300 ms per switch of the
    colour scheme. The result for the same family and size never changes while the app runs.
    """
    candidates = [family, "Segoe UI Variable Text", "Segoe UI", "arial.ttf"]
    if sys.platform != "win32":
        from hams.linux import font_file

        candidates = [font_file(family), font_file("sans-serif")]
    for candidate in filter(None, candidates):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()  # pragma: no cover


def _ellipsize(draw, text: str, pil_font, available: int) -> str:
    """Kürzt mit Auslassungspunkten, statt den Text hart abzuschneiden."""
    if draw.textlength(text, font=pil_font) <= available:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=pil_font) > available:
        text = text[:-1]
    return text + ellipsis
