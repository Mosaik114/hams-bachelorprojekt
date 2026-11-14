"""Auswahlfeld (Select): zeigt den gewählten Wert und öffnet das Popover."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from PIL import ImageDraw, ImageTk

from hams.ui_kit.buttons import BaseControl
from hams.ui_kit.caches import _img_cache
from hams.ui_kit.icons import ICON_BOX, render_icon
from hams.ui_kit.popover import Option, Popover
from hams.ui_kit.scale import Scale, font, px
from hams.ui_kit.states import State
from hams.ui_kit.surfaces import paint
from hams.ui_kit.text import _ellipsize, _pil_font
from hams.ui_kit.theme import Depth, Height, Radius, Space


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
        _fill, foreground = self.colours()
        depth = px(Depth.CONTROL)
        pressed = self.has_state(State.PRESSED)
        spec = self.surface_spec(width, height, px(self._logical_radius), under=self._bg_under)
        selected = self._selected()
        label = selected.label if selected is not None else self._placeholder
        self.configure(text="")
        key = ("select", spec, label, foreground, str(font("row_title").cget("family")), Scale.factor)
        cached = _img_cache.fetch(key)
        if cached is not None:
            self._composed = cached
            return cached
        plate = ImageTk.getimage(paint(spec)).copy()
        # Mitte der Fläche, nicht des Bildes: darunter liegt die Seite, beim Drücken sinkt die Fläche ein.
        self._body_centre = (plate.height - depth) // 2 + (depth if pressed else 0)

        chevron_size = px(ICON_BOX)
        glyph = render_icon("chevron", chevron_size, foreground)
        plate.paste(glyph, (plate.width - chevron_size - px(Space.MD), self._body_centre - chevron_size // 2), glyph)

        self._draw_text(plate, label, foreground, chevron_size)
        composed = _img_cache.store(key, ImageTk.PhotoImage(plate))
        self._composed = composed
        return composed

    def _draw_text(self, plate, label: str, colour: str, chevron: int) -> None:
        """Beschriftung ins Bild setzen — so bleibt der Chevron rechtsbündig."""
        x = px(Space.LG)
        available = plate.width - chevron - px(Space.MD) - x - px(Space.MD)
        draw = ImageDraw.Draw(plate)
        title_font = _pil_font(font("row_title"))
        text = _ellipsize(draw, label, title_font, available)
        # Nach den Oberlaengen der Schrift zentriert, nicht nach dem Text:
        # sonst saesse „Hell“ hoeher als „gut“, je nach Unterlaengen.
        ascent, descent = title_font.getmetrics()
        centre = getattr(self, "_body_centre", plate.height // 2)
        draw.text((x, centre - (ascent + descent) // 2), text, font=title_font, fill=colour)
