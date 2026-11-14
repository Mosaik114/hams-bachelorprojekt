"""Auswahlfeld (Select): zeigt den gewählten Wert und öffnet das Popover."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from PIL import ImageTk

from hams.ui_kit.buttons import BaseControl
from hams.ui_kit.caches import _img_cache
from hams.ui_kit.icons import ICON_BOX, render_icon
from hams.ui_kit.popover import Option, Popover
from hams.ui_kit.scale import Scale, font, px
from hams.ui_kit.states import State
from hams.ui_kit.surfaces import paint
from hams.ui_kit.theme import Height, Radius, Space


class Select(BaseControl):
    """Auswahlfeld mit eigenem Popover — Ersatz für das alte tk.Menu.

    Immer einzeilig und 32 px hoch. Zusatzangaben wie „Schnell · sehr gut“
    stehen im Popover und in der Beschreibung der Zeile, nicht im Feld: eine
    zweite Zeile in 44 px war nur in 10 pt lesbar und wurde trotzdem gekürzt.
    Der Chevron ist ein echtes Icon und sitzt immer rechtsbündig.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        bg_under: str,
        options: list | None = None,
        value=None,
        on_change: Callable[[object], None] | None = None,
        placeholder: str = "—",
    ) -> None:
        # Nicht `_options` nennen: das ist eine Methode von tkinter.Misc und
        # wuerde vor super().__init__() ueberschrieben.
        self._option_list: list = options or []
        self._value = value
        self._on_change = on_change
        self._placeholder = placeholder
        super().__init__(
            master,
            bg_under=bg_under,
            role="secondary",
            height=Height.STANDARD,
            radius=Radius.CONTROL,
            command=self.toggle_popover,
            font_role="row_title",
        )

    # ------------------------------------------------------------ Daten

    def set_options(self, options: list) -> None:
        shown = self._shown_label()
        self._option_list = list(options)
        if self._shown_label() != shown:
            self._render()

    def set_value(self, value, notify: bool = False) -> None:
        shown = self._shown_label()
        self._value = value
        if self._shown_label() != shown:
            self._render()
        if notify and self._on_change is not None:
            self._on_change(value)

    def _shown_label(self) -> str:
        """The text in the field. The image only depends on it (plus state, size and colours, which redraw
        themselves); a settings window that syncs its rows on every show must not redraw unchanged fields."""
        selected = self._selected()
        return selected.label if selected is not None else self._placeholder

    @property
    def value(self):
        return self._value

    @property
    def options(self) -> list:
        return list(self._option_list)

    def _selected(self) -> Option | None:
        for option in self._option_list:
            if option.value == self._value:
                return option
        return None

    # ------------------------------------------------------------ Popover

    @property
    def is_open(self) -> bool:
        return Popover.current is not None and Popover.current.trigger is self

    def toggle_popover(self) -> None:
        if self.is_open:
            Popover.dismiss_current()
        else:
            self.open_popover()

    def open_popover(self) -> None:
        if not State.is_interactive(self._states) or not self._option_list:
            return
        Popover.dismiss_current()  # nie zwei gleichzeitig
        Popover(self, self._option_list, self._value, on_choose=self._on_choose)
        self.set_state(State.SELECTED, True)

    def _on_choose(self, option: Option) -> None:
        self.set_state(State.SELECTED, False)
        if option.value != self._value:
            self.set_value(option.value, notify=True)
        else:
            self._render()

    # ------------------------------------------------------------ Zeichnen

    def _paint(self, width: int, height: int) -> ImageTk.PhotoImage:
        """Fläche und Chevron als ein Bild (zwischengespeichert); der Wert steht als Tk-Text darauf."""
        _fill, foreground = self.colours()
        spec = self.surface_spec(width, height, px(self._logical_radius), under=self._bg_under)
        key = ("select", spec, foreground, Scale.factor)
        cached = _img_cache.fetch(key)
        if cached is not None:
            return cached
        plate = ImageTk.getimage(paint(spec)).copy()
        _x, centre = self.body_centre()
        chevron = px(ICON_BOX)
        glyph = render_icon("chevron", chevron, foreground)
        plate.paste(glyph, (plate.width - chevron - px(Space.MD), centre - chevron // 2), glyph)
        return _img_cache.store(key, ImageTk.PhotoImage(plate))

    def natural_width(self) -> int:
        return 0  # ein Auswahlfeld nimmt die Breite, die ihm gegeben wird, und kürzt den Wert

    def _draw_text(self) -> None:
        """Der Wert links, in der Schrift der Zeilentitel, gekürzt vor dem Chevron — als Tk-Text, nie als Bild."""
        try:
            self.delete("label")
            if self._last_width <= 1:
                return
            selected = self._selected()
            label = selected.label if selected is not None else self._placeholder
            left = px(Space.LG)
            available = self._last_width - px(ICON_BOX) - px(Space.MD) - left - px(Space.MD)
            title_font = font("row_title")
            text = _fit(title_font, label, available)
            _x, centre = self.body_centre()
            self.create_text(left, centre, text=text, font=title_font, fill=self._fg, anchor="w", tags="label")
        except tk.TclError:  # pragma: no cover - Widget bereits zerstört
            pass

    @property
    def shown_text(self) -> str:
        """Der Wert, wie er im Feld steht (womöglich gekürzt)."""
        items = self.find_withtag("label")
        return str(self.itemcget(items[0], "text")) if items else ""


def _fit(tk_font, text: str, available: int) -> str:
    """`text`, mit … gekürzt, bis es in `available` Pixel passt."""
    if available <= 0 or tk_font.measure(text) <= available:
        return text
    while text and tk_font.measure(text + "…") > available:
        text = text[:-1]
    return text.rstrip() + "…"
