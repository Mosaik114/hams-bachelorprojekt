"""Tastenkürzel als Tastenkappen (KeyCombo) und ihre Beschriftung.

Ausgelagert aus controls.py (Q7)."""

from __future__ import annotations

import contextlib
import tkinter as tk

from hams.ui_kit.scale import Scale, font, px
from hams.ui_kit.surfaces import surface
from hams.ui_kit.text import _text_width
from hams.ui_kit.theme import Height, Radius, Space, Theme

#: Tastennamen, wie sie auf einer deutschen Tastatur stehen.
KEY_NAMES = {
    "ctrl": "Strg",
    "control": "Strg",
    "strg": "Strg",
    "shift": "Umschalt",
    "alt": "Alt",
    "altgr": "AltGr",
    "win": "Win",
    "windows": "Win",
    "cmd": "Win",
    "super": "Win",
    "space": "Leertaste",
    "enter": "Eingabe",
    "return": "Eingabe",
    "esc": "Esc",
    "escape": "Esc",
    "tab": "Tab",
    "backspace": "Rücktaste",
    "delete": "Entf",
    "del": "Entf",
    "insert": "Einfg",
    "home": "Pos1",
    "end": "Ende",
    "page up": "Bild auf",
    "page down": "Bild ab",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
}


def key_labels(combo: str) -> list[str]:
    """Zerlegt ein Tastenkürzel wie ``ctrl+shift+space`` in Tastenbeschriftungen.

    Unbekannte Tasten erscheinen, wie sie heißen — ein einzelner Buchstabe
    groß, wie auf der Taste.
    """
    labels = []
    for part in str(combo or "").split("+"):
        key = part.strip().lower()
        if not key:
            continue
        if key in KEY_NAMES:
            labels.append(KEY_NAMES[key])
        elif len(key) == 1 or (key[0] == "f" and key[1:].isdigit()):
            labels.append(key.upper())
        else:
            labels.append(part.strip().capitalize())
    return labels


class KeyCombo(tk.Canvas):
    """Ein Tastenkürzel als Reihe kleiner Tasten — Anzeige, kein Control.

    Vorher stand das Kürzel als roher Konfigurationswert in Monoschrift da
    (``ctrl+shift+space``). Tasten-Chips lesen sich wie die Tastatur selbst
    und brauchen keine Übersetzung im Kopf.
    """

    EMPTY = "Nicht belegt"

    def __init__(self, master: tk.Misc, *, bg_under: str, combo: str = "") -> None:
        self._bg_under = bg_under
        self._labels = key_labels(combo)
        self._images: list = []
        super().__init__(master, height=px(Height.KEYCAP), width=1, bg=bg_under, bd=0, highlightthickness=0)
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)
        self._draw()

    @property
    def labels(self) -> list[str]:
        return list(self._labels)

    def set_combo(self, combo: str) -> None:
        labels = key_labels(combo)
        if labels != self._labels:
            self._labels = labels
            self._draw()

    def _chip_width(self, text: str, tk_font) -> int:
        inner = _text_width(self, text, tk_font)
        return max(px(Height.KEYCAP), inner + 2 * px(Space.MD))

    def natural_width(self) -> int:
        """Breite in physischen Pixeln, die alle Tasten zusammen brauchen."""
        tk_font = font("shortcut")
        if not self._labels:
            return _text_width(self, self.EMPTY, tk_font)
        widths = [self._chip_width(label, tk_font) for label in self._labels]
        return sum(widths) + px(Space.KEY_GAP) * (len(widths) - 1)

    def _on_scale_change(self, _factor: float) -> None:
        with contextlib.suppress(tk.TclError):  # Widget bereits zerstört
            self._draw()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)

    def _on_theme_change(self) -> None:
        self._bg_under = Theme.remap(self._bg_under)
        try:
            self.configure(bg=self._bg_under)
        except tk.TclError:  # pragma: no cover - Widget bereits zerstört
            return
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        self._images = []
        tk_font = font("shortcut")
        height = px(Height.KEYCAP)
        self.configure(width=max(1, self.natural_width()), height=height)
        if not self._labels:
            self.create_text(0, height // 2, anchor="w", text=self.EMPTY, font=tk_font, fill=Theme.TEXT_TERTIARY)
            return
        x = 0
        for label in self._labels:
            width = self._chip_width(label, tk_font)
            chip = surface(width, height, px(Radius.KEYCAP), Theme.CONTROL_SURFACE)
            self._images.append(chip)
            self.create_image(x, 0, anchor="nw", image=chip)
            self.create_text(
                x + width // 2, height // 2, anchor="center", text=label, font=tk_font, fill=Theme.TEXT_PRIMARY
            )
            x += width + px(Space.KEY_GAP)
