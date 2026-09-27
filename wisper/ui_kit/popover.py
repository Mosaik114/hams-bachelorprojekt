"""Auswahllisten: Popover und Select."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from PIL import ImageDraw, ImageTk

from wisper.ui_kit.animation import Animator
from wisper.ui_kit.buttons import BaseControl
from wisper.ui_kit.icons import ICON_BOX, icon, render_icon
from wisper.ui_kit.layout import ScrollArea
from wisper.ui_kit.monitors import work_area
from wisper.ui_kit.scale import font, px
from wisper.ui_kit.states import ControlStyle, State, return_focus_to
from wisper.ui_kit.surfaces import surface
from wisper.ui_kit.text import _ellipsize, _fit_text, _pil_font, _text_width
from wisper.ui_kit.theme import Height, Radius, Space, Theme
from wisper.ui_kit.window import round_window_corners

# ---------------------------------------------------------------- Select und Popover


class Option:
    """Ein Eintrag einer Auswahlliste.

    `group` fasst Einträge unter einer Überschrift zusammen; `meta` ist die
    kleine zweite Zeile. Beides braucht das Mikrofon noch nicht, der
    Modell-Selector später schon.
    """

    __slots__ = ("value", "label", "meta", "icon", "enabled", "group", "trailing")

    def __init__(
        self,
        value,
        label: str,
        *,
        meta: str = "",
        icon: str | None = None,
        enabled: bool = True,
        group: str = "",
        trailing: str = "",
    ) -> None:
        self.value = value
        self.label = label
        self.meta = meta
        self.icon = icon
        self.enabled = enabled
        self.group = group
        self.trailing = trailing


class _PopoverItem(tk.Frame):
    """Eine Zeile im Popover: Haken, Titel, Meta, rechte Zusatzangabe."""

    def __init__(
        self, master: tk.Misc, option: Option, *, selected: bool, command: Callable[[Option], None], text_width: int = 0
    ) -> None:
        super().__init__(master, bg=Theme.POPOVER_SURFACE)
        self.option = option
        self._command = command
        self._selected = selected
        self._hovered = False

        # Immer ein Bild setzen — auch bei nicht gewaehlten Eintraegen, dort in
        # Hintergrundfarbe. `width` zaehlt bei einem Label *ohne* Bild in
        # Zeichen, nicht in Pixeln; die Spalte waere sonst um ein Vielfaches zu
        # breit und draengte den Titel aus der Zeile.
        self._check_image = icon("check", ICON_BOX, Theme.ACCENT_TINT if selected else Theme.POPOVER_SURFACE)
        self._check = tk.Label(self, bg=Theme.POPOVER_SURFACE, bd=0, image=self._check_image)
        self._check.pack(side="left", padx=(px(Space.MD), px(Space.ICON_GAP)))

        text_box = tk.Frame(self, bg=Theme.POPOVER_SURFACE)
        text_box.pack(side="left", fill="x", expand=True, pady=px(Space.SM))
        colour = Theme.TEXT_PRIMARY if option.enabled else Theme.TEXT_TERTIARY
        title = _fit_text(self, option.label, font("row_title"), text_width)
        self._title = tk.Label(
            text_box, text=title, bg=Theme.POPOVER_SURFACE, fg=colour, font=font("row_title"), anchor="w"
        )
        self._title.pack(anchor="w")
        self._meta = None
        if option.meta:
            meta = _fit_text(self, option.meta, font("meta"), text_width)
            self._meta = tk.Label(
                text_box, text=meta, bg=Theme.POPOVER_SURFACE, fg=Theme.TEXT_SECONDARY, font=font("meta"), anchor="w"
            )
            self._meta.pack(anchor="w")

        self._trailing = None
        if option.trailing:
            self._trailing = tk.Label(
                self, text=option.trailing, bg=Theme.POPOVER_SURFACE, fg=Theme.TEXT_SECONDARY, font=font("meta")
            )
            # `before`, weil der Textblock mit expand=True gepackt ist und der
            # Zusatzangabe sonst keinen Platz uebrig laesst.
            self._trailing.pack(side="right", before=text_box, padx=(0, px(Space.MD)))

        for widget in self._parts():
            widget.bind("<Enter>", lambda _e: self.set_hover(True), add="+")
            widget.bind("<Leave>", lambda _e: self.set_hover(False), add="+")
            widget.bind("<Button-1>", self._on_click, add="+")
            if option.enabled:
                widget.configure(cursor="hand2")

    def _parts(self) -> list:
        parts = [self, self._check, self._title]
        if self._meta is not None:
            parts.append(self._meta)
        if self._trailing is not None:
            parts.append(self._trailing)
        return parts

    def set_hover(self, hovered: bool) -> None:
        if not self.option.enabled:
            return
        self._hovered = hovered
        colour = Theme.CONTROL_HOVER if hovered else Theme.POPOVER_SURFACE
        for widget in self._parts() + [self._title.master]:
            widget.configure(bg=colour)

    @property
    def hovered(self) -> bool:
        return self._hovered

    def _on_click(self, _event=None) -> str:
        if self.option.enabled:
            self._command(self.option)
        return "break"


class Popover(tk.Toplevel):
    """Schwebende Auswahlliste.

    Tiefe entsteht ohne nativen Schatten — den gibt Windows für randlose
    Fenster nicht her (in Phase 9 gemessen). Stattdessen: eigene hellere
    Fläche, feine Umrandung, Rundung und Abstand.
    """

    MAX_VISIBLE = 8  # Einträge, danach wird gescrollt
    GAP = 6  # logisch, Abstand zum Auslöser
    ALPHA = 0.98  # sehr dezent; mehr kostet Textkontrast
    MAX_WIDTH = 340  # logisch; darüber wird gekürzt statt verbreitert
    MIN_WIDTH = 200  # logisch
    #: Einblenden über das Fenster-Alpha. Geschlossen wird ohne Übergang:
    #: ein Ausblenden müsste Grab und Fokus überdauern, und das ist es nicht
    #: wert — Funktion vor Symmetrie.
    FADE_MS = 110

    current: Popover | None = None

    def __init__(self, trigger: tk.Misc, options: list, value, *, on_choose: Callable[[Option], None]) -> None:
        super().__init__(trigger.winfo_toplevel())
        self.trigger = trigger
        self._on_choose = on_choose
        self._items: list = []
        self._index = -1

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.0)  # das Einblenden folgt unten
        except tk.TclError:  # pragma: no cover - Alpha nicht verfügbar
            pass
        self.configure(bg=Theme.HAIRLINE)  # 1 px Rand als äußerer Rahmen

        inner = tk.Frame(self, bg=Theme.POPOVER_SURFACE)
        inner.pack(fill="both", expand=True, padx=max(1, px(1)), pady=max(1, px(1)))

        self._body_holder = tk.Frame(inner, bg=Theme.POPOVER_SURFACE)
        self._body_holder.pack(fill="both", expand=True, pady=px(Space.SM))
        self._scroll: ScrollArea | None = None
        self._width = self._measure_width(options)
        self._build_items(options, value)

        self.bind("<Escape>", lambda _e: self.dismiss(), add="+")
        self.bind("<Return>", lambda _e: self._choose_current(), add="+")
        self.bind("<Down>", lambda _e: self._move(1), add="+")
        self.bind("<Up>", lambda _e: self._move(-1), add="+")
        self.bind("<Home>", lambda _e: self._move_to(0), add="+")
        self.bind("<End>", lambda _e: self._move_to(len(self._items) - 1), add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<Button-1>", self._on_click_outside, add="+")

        self._place()
        self.update_idletasks()
        round_window_corners(self, Radius.POPOVER)
        self.focus_force()
        self.grab_set()  # lokal, nie global — sonst friert der Desktop ein
        Popover.current = self
        Animator.run(self, "fade", self.FADE_MS, lambda fraction: self.attributes("-alpha", self.ALPHA * fraction))

    # ------------------------------------------------------------ Aufbau

    def _measure_width(self, options: list) -> int:
        """Breite aus dem Inhalt, begrenzt auf `MAX_WIDTH`.

        Ohne Deckel machte ein einziger langer Gerätename das Popover doppelt so
        breit wie nötig; ohne Untergrenze wirkte es bei kurzen Namen gequetscht.
        """
        self._trailing_width = 0
        widest = 0
        for option in options:
            widest = max(widest, _text_width(self, option.label, font("row_title")))
            if option.meta:
                widest = max(widest, _text_width(self, option.meta, font("meta")))
        self._trailing_width = max(
            (_text_width(self, o.trailing, font("meta")) for o in options if o.trailing), default=0
        )
        content = widest + self._chrome_width()
        floor = max(px(self.MIN_WIDTH), self.trigger.winfo_width())
        return max(floor, min(content, px(self.MAX_WIDTH)))

    def _chrome_width(self) -> int:
        """Alles ausser dem Text: Haken, Abstände und die rechte Zusatzangabe."""
        return px(ICON_BOX) + px(Space.ICON_GAP) + 3 * px(Space.MD) + getattr(self, "_trailing_width", 0)

    def _text_budget(self) -> int:
        """Platz, der einer Zeile für Text bleibt.

        Die Zusatzangabe rechts muss mitgerechnet werden — sonst schiebt sich
        die Meta-Zeile darunter und beide überlappen.
        """
        # Untergrenze zusaetzlich als Anteil der Breite: so bleibt der Titel
        # lesbar, auch wenn die Zusatzangabe ungewoehnlich breit ausfaellt.
        return max(px(60), int(self._width * 0.55), self._width - self._chrome_width())

    def _build_items(self, options: list, value) -> None:
        rows = sum(1 for option in options)
        groups = sorted({option.group for option in options if option.group})
        needs_scroll = rows > self.MAX_VISIBLE

        if needs_scroll:
            self._scroll = ScrollArea(self._body_holder, bg_under=Theme.POPOVER_SURFACE)
            self._scroll.pack(fill="both", expand=True)
            container = self._scroll.body
            container.configure(bg=Theme.POPOVER_SURFACE)
        else:
            container = self._body_holder

        def add_group(name: str) -> None:
            tk.Label(
                container,
                text=name.upper(),
                bg=Theme.POPOVER_SURFACE,
                fg=Theme.TEXT_SECONDARY,
                font=font("meta"),
                anchor="w",
            ).pack(fill="x", padx=(px(Space.MD), 0), pady=(px(Space.SM), px(Space.XS)))

        if groups:
            for name in groups:
                add_group(name)
                for option in [o for o in options if o.group == name]:
                    self._add_item(container, option, value)
            for option in [o for o in options if not o.group]:
                self._add_item(container, option, value)
        else:
            for option in options:
                self._add_item(container, option, value)

    def _add_item(self, container: tk.Misc, option: Option, value) -> None:
        item = _PopoverItem(
            container, option, selected=option.value == value, command=self._choose, text_width=self._text_budget()
        )
        item.pack(fill="x")
        self._items.append(item)
        if option.value == value:
            self._index = len(self._items) - 1

    # ------------------------------------------------------------ Platzierung

    def _place(self) -> None:
        self.update_idletasks()
        width = self._width
        height = self.winfo_reqheight()
        left, top, right, bottom = work_area(self.trigger)

        gap = px(self.GAP)
        x = self.trigger.winfo_rootx()
        y = self.trigger.winfo_rooty() + self.trigger.winfo_height() + gap

        if y + height > bottom:  # unten kein Platz -> nach oben
            above = self.trigger.winfo_rooty() - height - gap
            y = above if above >= top else max(top, bottom - height)
        if x + width > right:  # rechts kein Platz -> einrücken
            x = right - width
        x = max(left, x)
        # Zum Schluss beidseitig einfangen. Der Umschlag nach oben allein genügt
        # nicht: wird das Fenster so weit nach unten gezogen, dass der Auslöser
        # selbst unter der Arbeitsfläche liegt, landete das Popover sonst
        # ebenfalls dort (gemessen: 345 px unterhalb des Randes).
        y = max(top, min(y, bottom - height))
        self.geometry(f"{width}x{height}+{int(x)}+{int(y)}")

    # ------------------------------------------------------------ Auswahl

    def _move(self, delta: int) -> str:
        usable = [i for i, item in enumerate(self._items) if item.option.enabled]
        if not usable:
            return "break"
        if self._index in usable:
            position = usable.index(self._index)
            position = (position + delta) % len(usable)
        else:
            position = 0 if delta > 0 else len(usable) - 1
        return self._move_to(usable[position])

    def _move_to(self, index: int) -> str:
        if not self._items:
            return "break"
        index = max(0, min(index, len(self._items) - 1))
        for position, item in enumerate(self._items):
            item.set_hover(position == index)
        self._index = index
        return "break"

    def _choose_current(self) -> str:
        if 0 <= self._index < len(self._items):
            item = self._items[self._index]
            if item.option.enabled:
                self._choose(item.option)
        return "break"

    def _choose(self, option: Option) -> None:
        callback = self._on_choose
        self.dismiss()
        callback(option)

    def _on_focus_out(self, _event=None) -> None:
        # Klick in ein *fremdes* Fenster: Tk meldet Fokusverlust am Popover.
        if Popover.current is self:
            self.after(1, self._dismiss_if_unfocused)

    def _on_click_outside(self, event) -> str:
        """Ein Klick neben das Popover schliesst es.

        Der lokale Grab leitet jeden Klick der Anwendung hierher — auch den auf
        ein anderes Bedienelement oder auf freie Fläche im Einstellungsfenster.
        `FocusOut` greift dabei nicht, denn innerhalb derselben Anwendung
        wandert der Fokus gar nicht. Ohne diese Prüfung versackte so ein Klick
        wirkungslos, und das Popover liess sich nur über einen Eintrag oder
        Escape wieder loswerden.

        Der schliessende Klick wird verbraucht und nicht an das Bedienelement
        darunter weitergereicht — genauso verhalten sich Menüs unter Windows.
        """
        if Popover.current is not self:
            return ""
        left, top = self.winfo_rootx(), self.winfo_rooty()
        inside = left <= event.x_root < left + self.winfo_width() and top <= event.y_root < top + self.winfo_height()
        if not inside:
            self.dismiss()
            return "break"
        return ""

    def _dismiss_if_unfocused(self) -> None:
        try:
            if self.focus_displayof() is None:
                self.dismiss()
        except (tk.TclError, KeyError):  # pragma: no cover
            self.dismiss()

    def _on_theme_change(self) -> None:
        """Beim Moduswechsel schliessen statt live umfärben.

        Der robustere der beiden Wege: ein schwebendes Toplevel trägt Grab,
        Fokus, eigenes Alpha und abgerundete Fensterecken. Es mitten im
        Wechsel umzufärben hiesse, all das zu halten, während unter ihm die
        Palette wechselt. Es zu schliessen und den Fokus an den Auslöser
        zurückzugeben ist eindeutig — und der Wechsel wurde ohnehin gerade vom
        Nutzer im Einstellungsfenster ausgelöst.
        """
        self.dismiss()

    def dismiss(self) -> None:
        """Schliessen und den Fokus an den Auslöser zurückgeben."""
        if Popover.current is self:
            Popover.current = None
        try:
            self.grab_release()
        except tk.TclError:  # pragma: no cover
            pass
        trigger = self.trigger
        try:
            self.destroy()
        except tk.TclError:  # pragma: no cover
            pass
        return_focus_to(trigger)

    @classmethod
    def dismiss_current(cls) -> None:
        """Es darf immer nur ein Popover offen sein."""
        if cls.current is not None:
            cls.current.dismiss()


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
        self._option_list = list(options)
        self._render()

    def set_value(self, value, notify: bool = False) -> None:
        self._value = value
        self._render()
        if notify and self._on_change is not None:
            self._on_change(value)

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
        fill, foreground = self.colours()
        ring_w, ring_gap = px(ControlStyle.RING_WIDTH), px(ControlStyle.RING_GAP)
        plate = ImageTk.getimage(
            surface(
                width,
                height + 2 * (ring_w + ring_gap),
                px(self._logical_radius),
                fill if fill is not None else self._bg_under,
                squircle=True,
                ring=self.ring(),
                ring_width=ring_w,
                ring_gap=ring_gap,
            )
        )

        chevron_size = px(ICON_BOX)
        glyph = render_icon("chevron", chevron_size, foreground)
        plate.paste(glyph, (plate.width - chevron_size - px(Space.MD), (plate.height - chevron_size) // 2), glyph)

        selected = self._selected()
        label = selected.label if selected is not None else self._placeholder
        self.configure(text="")
        self._draw_text(plate, label, foreground, chevron_size)
        composed = ImageTk.PhotoImage(plate)
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
        draw.text((x, (plate.height - ascent - descent) // 2), text, font=title_font, fill=colour)
