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


class Writing:
    """Which way the interface reads: left to right, or right to left (Arabic).

    Set once at the start, before the first window (`hams.bootstrap`), like the language itself. Right to left
    mirrors the layout where it carries meaning: text stands on the right and is right-aligned, controls stand on
    its left, rows of buttons and tabs run from the right. Tk draws the letters themselves through Windows, which
    shapes and orders them (see `hams.i18n.right_to_left`); the kit only decides where things stand.
    """

    rtl = False


def mirror_side(side: str) -> str:
    """`pack(side=...)`: "left" and "right" swap places right to left."""
    if Writing.rtl and side in ("left", "right"):
        return "right" if side == "left" else "left"
    return side


def mirror_anchor(anchor: str) -> str:
    """An anchor or `sticky` value with west and east swapped right to left ("w" -> "e", "nw" -> "ne")."""
    if not Writing.rtl:
        return anchor
    return anchor.translate(str.maketrans("we", "ew"))


def mirror_pad(pad: tuple[int, int]) -> tuple[int, int]:
    """`padx=(left, right)`, swapped right to left."""
    return (pad[1], pad[0]) if Writing.rtl else pad


def text_justify() -> str:
    """How lines of a wrapped text line up: on the left, or on the right."""
    return "right" if Writing.rtl else "left"
