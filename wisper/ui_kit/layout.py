"""Aufbau der Einstellungen: Scrollbereich, Ueberschrift, Gruppe, Zeile, Trennlinie."""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional


from wisper.ui_kit.controls import StatusDot
from wisper.ui_kit.scale import Scale, font, px
from wisper.ui_kit.states import ControlStyle, FocusMixin
from wisper.ui_kit.surfaces import surface
from wisper.ui_kit.text import _fit_text, _line_height
from wisper.ui_kit.theme import Height, Radius, Space, Theme, set_surface_under


# ---------------------------------------------------------------- Einstellungs-Bausteine


class ScrollArea(tk.Frame):
    """Scrollbarer Bereich mit schlanker eigener Leiste.

    Tk bringt keinen scrollbaren Container mit; der übliche Weg ist ein Canvas
    mit einem Fenster darin. Die Leiste ist ein selbst gezeichneter Balken statt
    einer klassischen Tk-Scrollbar, und sie erscheint nur, wenn der Inhalt
    tatsächlich höher ist als der Ausschnitt.
    """

    BAR_WIDTH = 4        # logisch
    BAR_INSET = 3        # logisch
    STEP = 40            # logisch, eine Tastenstufe

    def __init__(self, master: tk.Misc, *, bg_under: str,
                 on_scroll: Optional[Callable[[float], None]] = None) -> None:
        super().__init__(master, bg=bg_under)
        self._bg_under = bg_under
        self._on_scroll = on_scroll
        self._offset = 0

        self.canvas = tk.Canvas(self, bg=bg_under, bd=0, highlightthickness=0,
                                takefocus=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=bg_under)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.bar = tk.Canvas(self, width=px(self.BAR_WIDTH + 2 * self.BAR_INSET),
                             bg=bg_under, bd=0, highlightthickness=0, takefocus=0)
        # Platzhalter in Leistenbreite: ohne ihn wuerde der Inhalt breiter,
        # sobald die Leiste verschwindet, und der rechte Rand waere ein anderer
        # als der linke.
        self._gutter = tk.Frame(self, width=px(self.gutter_width()), bg=bg_under)
        self._gutter.pack(side="right", fill="y", before=self.canvas)

        self.body.bind("<Configure>", self._on_body_configure, add="+")
        self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        # Mausrad nur, solange der Zeiger drin ist — sonst würde der Bereich
        # das Rad auch für andere Fenster abfangen.
        self.canvas.bind("<Enter>", self._grab_wheel, add="+")
        self.canvas.bind("<Leave>", self._release_wheel, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    @classmethod
    def gutter_width(cls) -> int:
        """Breite der Leistenrinne in logischen Pixeln."""
        return cls.BAR_WIDTH + 2 * cls.BAR_INSET

    # ------------------------------------------------------------ Scrollen

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def scrollable(self) -> int:
        view = self.canvas.winfo_height()
        if view <= 1:
            return 0      # noch nicht gelayoutet — sonst erschiene die Leiste grundlos
        return max(0, self.body.winfo_reqheight() - view)

    def scroll_to(self, offset: int) -> None:
        offset = max(0, min(int(offset), self.scrollable))
        if offset != self._offset:
            self._offset = offset
            self.canvas.yview_moveto(offset / max(1, self.body.winfo_reqheight()))
            self._draw_bar()
            if self._on_scroll is not None:
                self._on_scroll(offset)
        elif self._offset == 0:
            self.canvas.yview_moveto(0)

    def scroll_by(self, delta: int) -> None:
        self.scroll_to(self._offset + delta)

    def scroll_page(self, direction: int) -> None:
        self.scroll_by(direction * max(1, self.canvas.winfo_height() - px(self.STEP)))

    def refresh(self) -> None:
        """Neu bewerten, ob die Leiste noch gebraucht wird.

        Nötig, wenn Inhalt entfernt wurde: Tk rechnet die geforderte Höhe eines
        Frames nicht neu, solange kein Configure-Ereignis anfällt.
        """
        self.update_idletasks()
        self.canvas.configure(scrollregion=(0, 0, 0, self.body.winfo_reqheight()))
        self.scroll_to(self._offset)
        self._draw_bar()

    def bind_keys(self, widget: tk.Misc) -> None:
        """Tastaturscrollen an ein Fenster hängen (Pfeile, Bild auf/ab, Pos1/Ende)."""
        widget.bind("<Up>", lambda _e: self.scroll_by(-px(self.STEP)), add="+")
        widget.bind("<Down>", lambda _e: self.scroll_by(px(self.STEP)), add="+")
        widget.bind("<Prior>", lambda _e: self.scroll_page(-1), add="+")
        widget.bind("<Next>", lambda _e: self.scroll_page(1), add="+")
        widget.bind("<Home>", lambda _e: self.scroll_to(0), add="+")
        widget.bind("<End>", lambda _e: self.scroll_to(self.scrollable), add="+")

    def _grab_wheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _release_wheel(self, _event=None) -> None:
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:      # pragma: no cover
            pass

    def _on_wheel(self, event: tk.Event) -> None:
        self.scroll_by(-int(event.delta / 120) * px(self.STEP))

    # ------------------------------------------------------------ Layout

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=(0, 0, 0, self.body.winfo_reqheight()))
        self._draw_bar()

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self.scroll_to(self._offset)      # Position halten, Grenzen neu prüfen
        self._draw_bar()

    def _show_bar(self, visible: bool) -> None:
        """Sichtbarkeit an genau einer Stelle — konsistent mit dem Gezeichneten."""
        if visible and self.bar.winfo_manager() != "pack":
            # `before` ist noetig: der Canvas ist mit expand=True gepackt und
            # liesse der Leiste sonst keine Breite uebrig.
            self._gutter.pack_forget()
            self.bar.pack(side="right", fill="y", before=self.canvas)
        elif not visible and self.bar.winfo_manager() == "pack":
            self.bar.pack_forget()
            self._gutter.pack(side="right", fill="y", before=self.canvas)

    def _draw_bar(self) -> None:
        self.bar.delete("all")
        span = self.body.winfo_reqheight()
        view = self.canvas.winfo_height()
        if span <= view or view <= 1:
            self._show_bar(False)
            return
        self._show_bar(True)
        track = view
        length = max(px(20), int(track * view / span))
        top = int((track - length) * (self._offset / max(1, self.scrollable)))
        image = surface(px(self.BAR_WIDTH), length, Radius.pill(px(self.BAR_WIDTH)),
                        Theme.CONTROL_HOVER, squircle=False)
        self._bar_image = image
        self.bar.create_image(px(self.BAR_INSET), top, anchor="nw", image=image)

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self.bar.configure(width=px(self.gutter_width()))
            self._gutter.configure(width=px(self.gutter_width()))
            self._draw_bar()
        except tk.TclError:      # pragma: no cover
            pass

    def _on_theme_change(self) -> None:
        """Nur der gemerkte Untergrund und der Balken.

        Rahmen, Canvas, Körper und Leiste sind gewöhnliche Tk-Widgets im selben
        Baum — deren `bg` hat `retint()` bereits umgeschlüsselt. Die
        Rollposition bleibt unangetastet.
        """
        self._bg_under = Theme.remap(self._bg_under)
        try:
            self._draw_bar()
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)
            self._release_wheel()


class SectionHeader(tk.Label):
    """Abschnittsüberschrift über einer Gruppe."""

    def __init__(self, master: tk.Misc, text: str, *, bg_under: str) -> None:
        super().__init__(master, text=text, bg=bg_under, fg=Theme.TEXT_PRIMARY,
                         font=font("section"), anchor="w", bd=0, padx=0, pady=0)
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self.configure(font=font("section"))
        except tk.TclError:      # pragma: no cover
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)


class GroupSurface(tk.Canvas):
    """Eine abgerundete Fläche, die mehrere Zeilen zusammenfasst.

    Die Zeilen liegen in einem Frame, das um `Space.GROUP_INSET` eingerückt im
    Canvas sitzt. Das genügt: bei 10 px Radius liegt die Ecke (4, 4) noch
    innerhalb der Rundung, der Rand bleibt also frei. Den sichtbaren Abstand
    zum Text liefert die Zeile selbst mit `Space.ROW_X`.
    """

    def __init__(self, master: tk.Misc, *, bg_under: str) -> None:
        super().__init__(master, bg=bg_under, bd=0, highlightthickness=0,
                         takefocus=0, height=px(Radius.GROUP * 2))
        self._bg_under = bg_under
        self._last_size = (0, 0)
        self.body = tk.Frame(self, bg=Theme.GROUP_SURFACE)
        self._window = self.create_window((0, 0), window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_configure, add="+")
        self.body.bind("<Configure>", lambda _e: self._sync_height(), add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    @property
    def inset(self) -> int:
        return px(Space.GROUP_INSET)

    def _sync_height(self) -> None:
        try:
            wanted = self.body.winfo_reqheight() + 2 * self.inset
            if wanted != int(self.cget("height")):
                self.configure(height=wanted)
        except tk.TclError:      # pragma: no cover
            pass

    def _on_configure(self, event: tk.Event) -> None:
        if (event.width, event.height) == self._last_size or event.width <= 1:
            return
        self._last_size = (event.width, event.height)
        inset = self.inset
        self.coords(self._window, inset, inset)
        self.itemconfigure(self._window, width=max(1, event.width - 2 * inset))
        self._render(event.width, event.height)

    def _render(self, width: int, height: int) -> None:
        self.delete("plate")
        image = surface(width, height, px(Radius.GROUP), Theme.GROUP_SURFACE,
                        border=Theme.HAIRLINE, border_w=max(1, px(1)),
                        squircle=True)
        self._plate = image
        self.create_image(0, 0, anchor="nw", image=image, tags="plate")
        self.tag_lower("plate")

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self._last_size = (0, 0)
            self._sync_height()
            self.event_generate("<Configure>")
        except tk.TclError:      # pragma: no cover
            pass

    def _on_theme_change(self) -> None:
        """Die Gruppenfläche neu zeichnen — in derselben Grösse.

        Über `_last_size` statt `winfo_width()`: die Grösse ändert sich beim
        Moduswechsel nicht, und ein erzwungenes Configure würde nur ein
        weiteres Layout auslösen.
        """
        self._bg_under = Theme.remap(self._bg_under)
        width, height = self._last_size
        if width <= 1:
            return
        try:
            self.configure(bg=self._bg_under)
            self.body.configure(bg=Theme.GROUP_SURFACE)
            self._render(width, height)
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)


class SettingRow(tk.Frame):
    """Eine Einstellungszeile in vier Ausprägungen.

    * `simple`      Titel — Control
    * `description` Titel / Beschreibung — Control
    * `status`      Titel / Statuspunkt + Text — Control
    * `display`     Titel — Wert, kein Control

    Bei `stacked=True` steht das Control nicht rechts, sondern in voller Breite
    unter dem Text. Das ist die Form für Auswahlfelder mit langen Werten
    (Geräte, Modelle): nebeneinander blieben für Titel, Beschreibung und Wert
    je nur rund 140 px, und alle drei wurden abgeschnitten.

    Die Höhe ergibt sich aus dem Inhalt: oben und unten `Space.ROW_Y`, dazwischen
    Text oder Control, mindestens `Height.ROW_MIN`. Eine Beschreibung bricht um,
    statt gekürzt zu werden.

    Bei `interactive=True` ist die ganze Zeile Auslöser (für Schalter). Bei
    Auswahlzeilen bleibt das ausdrücklich aus: ein Klick auf die Beschreibung
    soll kein Popover öffnen.
    """

    def __init__(self, master: tk.Misc, title: str, *, variant: str = "simple",
                 description: str = "", value: str = "",
                 interactive: bool = False,
                 command: Optional[Callable[[], None]] = None,
                 hairline: bool = True, stacked: bool = False) -> None:
        super().__init__(master, bg=Theme.GROUP_SURFACE)
        self._variant = variant
        self._interactive = interactive
        self._command = command
        self._stacked = stacked
        self._enabled = True
        self._hovered = False
        self._control: Optional[tk.Misc] = None
        #: Die vollständigen Texte. Angezeigt wird, was in die Zeile passt.
        self._title_text = title
        self._sub_text = description

        tall = variant in ("description", "status")

        self._line = tk.Frame(self, bg=Theme.GROUP_SURFACE)
        self._line.pack(fill="x")
        self._line.grid_columnconfigure(0, weight=1)

        self._text_box = tk.Frame(self._line, bg=Theme.GROUP_SURFACE)
        self._text_box.grid(row=0, column=0, sticky="ew")

        self.title_label = tk.Label(self._text_box, text=title, bg=Theme.GROUP_SURFACE,
                                    fg=Theme.TEXT_PRIMARY, font=font("row_title"),
                                    anchor="w", bd=0, padx=0, pady=0)
        self.title_label.pack(anchor="w")

        self.sub_box: Optional[tk.Frame] = None
        self.status_dot: Optional[StatusDot] = None
        self.sub_label: Optional[tk.Label] = None
        if tall:
            self.sub_box = tk.Frame(self._text_box, bg=Theme.GROUP_SURFACE)
            self.sub_box.pack(anchor="w", fill="x", pady=(px(Space.TEXT_GAP), 0))
            if variant == "status":
                self.status_dot = StatusDot(self.sub_box, bg_under=Theme.GROUP_SURFACE,
                                            size=StatusDot.SIZE_ROW)
                self.status_dot.pack(side="left", anchor="n", padx=(0, px(Space.ICON_GAP)))
            self.sub_label = tk.Label(self.sub_box, text=description,
                                      bg=Theme.GROUP_SURFACE, fg=Theme.TEXT_TERTIARY,
                                      font=font("description"), anchor="w",
                                      justify="left", bd=0, padx=0, pady=0)
            self.sub_label.pack(side="left", fill="x")

        self.value_label: Optional[tk.Label] = None
        if variant == "display":
            self.value_label = tk.Label(self._line, text=value, bg=Theme.GROUP_SURFACE,
                                        fg=Theme.TEXT_SECONDARY, font=font("row_title"),
                                        anchor="e", justify="right", bd=0, padx=0, pady=0)
            self.value_label.grid(row=0, column=1, sticky="e")

        self.hairline: Optional[tk.Frame] = None
        if hairline:
            holder = tk.Frame(self, bg=Theme.GROUP_SURFACE)
            holder.pack(fill="x")
            self.hairline = tk.Frame(holder, bg=Theme.HAIRLINE, height=max(1, px(1)))
            self.hairline.pack(fill="x", padx=px(Space.ROW_X))

        self._apply_metrics()

        # Der Textblock bekommt erst nach dem Einhängen des Controls seine
        # endgültige Breite; vorher lässt sich weder kürzen noch umbrechen.
        self._text_box.bind("<Configure>", self._refit, add="+")

        if interactive:
            for widget in self._hover_widgets():
                widget.bind("<Enter>", self._on_enter, add="+")
                widget.bind("<Leave>", self._on_leave, add="+")
                widget.bind("<Button-1>", self._on_click, add="+")
                widget.configure(cursor="hand2")

        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    # ------------------------------------------------------------ Aufbau

    def set_control(self, control: tk.Misc) -> None:
        """Hängt das Control rechts in die Zeile — und löst ein früheres ab.

        Der Austausch ist gewollt: eine Zeile zeigt während eines Downloads
        eine Abbruch-Schaltfläche statt des Auswahlfeldes.
        """
        if self._control is not None and self._control is not control:
            try:
                self._control.grid_forget()
            except tk.TclError:      # pragma: no cover - bereits zerstoert
                pass
        self._control = control
        if control is None:
            return
        # Controls mit Fokusring halten dafür ringsum Platz frei; ihre sichtbare
        # Kante liegt um diesen Rand weiter innen. Die Zeile rückt sie deshalb
        # um genau so viel nach außen, damit Feld, Titel und Tasten-Chips auf
        # einer Linie stehen. Controls ohne Ring bekommen den Rand zurück.
        inset = self._ring_inset()
        ringed = isinstance(control, FocusMixin) or getattr(control, "focus_ring", False)
        outer = 0 if ringed else inset
        if self._stacked:
            # Auswahlfelder und Pegel nehmen die volle Breite; eine Schaltfläche,
            # die vorübergehend an ihre Stelle tritt, behält ihre eigene.
            sticky = "ew" if getattr(control, "stretch_stacked", True) else "w"
            control.grid(in_=self._line, row=1, column=0, columnspan=2, sticky=sticky,
                         padx=(outer, outer),
                         pady=(px(Space.STACK_GAP) - (inset if ringed else 0), 0))
        else:
            control.grid(in_=self._line, row=0, column=1, sticky="e",
                         padx=(px(Space.CONTROL_GAP) - (inset if ringed else 0), outer))
        # Tk stapelt nach Erzeugungsreihenfolge; ein spaeter erzeugtes Control
        # laege sonst hinter dem Zeilengrund.
        # `tk.Misc.lift`, nicht `control.lift`: an einer Canvas hebt das Tags.
        tk.Misc.lift(control, self._line)

    @staticmethod
    def _ring_inset() -> int:
        """Rand, den ein Control mit Fokusring um seine sichtbare Fläche lässt."""
        return px(ControlStyle.RING_WIDTH) + px(ControlStyle.RING_GAP)

    def _apply_metrics(self) -> None:
        """Innenabstände und Mindesthöhe — beim Aufbau und bei jedem DPI-Wechsel.

        Die Zeile selbst rückt um den Ringrand weniger ein; Text und Wert holen
        ihn als eigenen Abstand zurück. So stehen alle sichtbaren Kanten bei
        `Space.ROW_X`, auch die eines Controls mit Fokusring.
        """
        inset = self._ring_inset()
        self._line.configure(padx=px(Space.ROW_X) - inset, pady=px(Space.ROW_Y))
        # Gestapelt bestimmt der Text selbst die Höhe der ersten Reihe; eine
        # Mindesthöhe schöbe nur Luft zwischen Text und Feld.
        self._line.grid_rowconfigure(
            0, minsize=0 if self._stacked else px(Height.ROW_MIN - 2 * Space.ROW_Y))
        self._text_box.grid_configure(padx=(inset, inset if self._stacked else 0))
        if self._control is not None:
            self.set_control(self._control)
        if self.value_label is not None:
            self.value_label.grid_configure(padx=(px(Space.CONTROL_GAP), inset))
        if self.status_dot is not None:
            # Mittig zur ersten Textzeile, nicht an deren Oberkante.
            line = _line_height(font("description"), self)
            self.status_dot.pack_configure(
                pady=(max(0, (line - px(StatusDot.SIZE_ROW)) // 2), 0))

    def _hover_widgets(self) -> list:
        widgets = [self._line, self._text_box, self.title_label]
        if self.sub_box is not None:
            widgets += [self.sub_box]
        if self.sub_label is not None:
            widgets += [self.sub_label]
        return widgets

    # ------------------------------------------------------------ Zustand

    def _refit(self, _event=None) -> None:
        """Passt Titel und Unterzeile an den Platz an, der wirklich übrig ist.

        Die Unterzeile bricht um — früher wurde sie gekürzt, und gerade die
        Hinweise („Ollama wird gepr…“) waren dann nicht mehr lesbar. Nur der
        Titel wird im Notfall gekürzt; er ist kurz und passt praktisch immer.
        """
        try:
            width = self._text_box.winfo_width()
        except tk.TclError:      # pragma: no cover - Zeile wird gerade zerstört
            return
        if width <= 1:
            return
        self.title_label.configure(
            text=_fit_text(self, self._title_text, font("row_title"), width))
        if self.sub_label is None:
            return
        used = 0
        if self.status_dot is not None:
            used = px(StatusDot.SIZE_ROW) + px(Space.ICON_GAP)
        wrap = max(1, width - used)
        if int(str(self.sub_label.cget("wraplength")) or 0) != wrap:
            self.sub_label.configure(wraplength=wrap)
        if self.sub_label.cget("text") != self._sub_text:
            self.sub_label.configure(text=self._sub_text)

    @property
    def sub_text(self) -> str:
        """Die vollständige Unterzeile, unabhängig vom Umbruch."""
        return self._sub_text

    def set_status(self, status: str, text: str) -> None:
        if self.status_dot is not None:
            self.status_dot.set_status(status)
        self._sub_text = text
        self._refit()

    def set_description(self, text: str) -> None:
        self._sub_text = text
        self._refit()

    def set_value(self, text: str) -> None:
        if self.value_label is not None:
            self.value_label.configure(text=text)

    def set_enabled(self, enabled: bool) -> None:
        """Deaktiviert die Zeile — die Höhe bleibt, damit nichts springt."""
        self._enabled = enabled
        self.title_label.configure(
            fg=Theme.TEXT_PRIMARY if enabled else Theme.TEXT_TERTIARY)
        if self.sub_label is not None:
            self.sub_label.configure(
                fg=Theme.TEXT_TERTIARY if enabled else Theme.CONTROL_HOVER)
        if self.value_label is not None:
            self.value_label.configure(
                fg=Theme.TEXT_SECONDARY if enabled else Theme.TEXT_TERTIARY)
        if self._control is not None and hasattr(self._control, "set_enabled"):
            self._control.set_enabled(enabled)
        if not enabled:
            self._paint(Theme.GROUP_SURFACE)
        for widget in self._hover_widgets() if self._interactive else []:
            widget.configure(cursor="hand2" if enabled else "arrow")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------ Interaktion

    def _paint(self, colour: str) -> None:
        for widget in (self._line, self._text_box, self.title_label,
                       self.sub_box, self.sub_label, self.value_label):
            if widget is not None:
                # Tk-Kinder erben keinen Hintergrund; jedes muss einzeln gesetzt
                # werden, sonst bleiben Textkästchen in der alten Farbe stehen.
                widget.configure(bg=colour)
        if self.status_dot is not None:
            self.status_dot.configure(bg=colour)
        if self._control is not None:
            # Das Control gehoert zur Zeile. Ohne diesen Schritt blieb es beim
            # Hover als Rechteck der alten Farbe stehen — am deutlichsten am
            # Schalter, dessen runde Spur den Untergrund durchscheinen laesst.
            set_surface_under(self._control, colour)

    def _on_enter(self, _event=None) -> None:
        if self._enabled and self._interactive:
            self._hovered = True
            self._paint(Theme.CONTROL_SURFACE)

    def _on_leave(self, _event=None) -> None:
        self._hovered = False
        self._paint(Theme.GROUP_SURFACE)

    def _on_click(self, _event=None) -> str:
        if self._enabled and self._command is not None:
            self._command()
        return "break"      # verhindert, dass der Klick zusätzlich am Control landet

    # ------------------------------------------------------------ Darstellung

    def _on_theme_change(self) -> None:
        """Zeilengrund und Textfarben neu setzen.

        `set_enabled()` ist hier der ehrlichste Weg: dort steht bereits, welche
        Textfarbe zu welchem Zustand gehört, und die Zeile weiss selbst, ob sie
        gerade abgeblendet ist. Danach entscheidet der Hover über die Fläche —
        sonst verlöre eine Zeile unter dem Zeiger beim Wechsel ihre Hervorhebung.
        """
        self.set_enabled(self._enabled)
        hovered = self._hovered and self._enabled and self._interactive
        self._paint(Theme.CONTROL_SURFACE if hovered else Theme.GROUP_SURFACE)

    # ------------------------------------------------------------ DPI

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self._apply_metrics()
            self.title_label.configure(font=font("row_title"))
            if self.sub_label is not None:
                self.sub_label.configure(font=font("description"))
            if self.value_label is not None:
                self.value_label.configure(font=font("row_title"))
            self._refit()
            if self.hairline is not None:
                self.hairline.configure(height=max(1, px(1)))
                self.hairline.pack_configure(padx=px(Space.ROW_X))
        except tk.TclError:      # pragma: no cover
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)


def separator(master: tk.Misc, bg_under: str) -> tk.Frame:
    line = tk.Frame(master, bg=Theme.HAIRLINE, height=px(1))
    return line
