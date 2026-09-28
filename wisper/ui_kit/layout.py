"""Aufbau der Einstellungen: Scrollbereich, Ueberschrift, Gruppe, Zeile, Trennlinie."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable

from wisper.ui_kit.scale import Scale, font, px
from wisper.ui_kit.surfaces import surface
from wisper.ui_kit.theme import Radius, Space, Theme

# ---------------------------------------------------------------- Einstellungs-Bausteine


class ScrollArea(tk.Frame):
    """Scrollbarer Bereich mit schlanker eigener Leiste.

    Tk bringt keinen scrollbaren Container mit; der übliche Weg ist ein Canvas
    mit einem Fenster darin. Die Leiste ist ein selbst gezeichneter Balken statt
    einer klassischen Tk-Scrollbar, und sie erscheint nur, wenn der Inhalt
    tatsächlich höher ist als der Ausschnitt.
    """

    BAR_WIDTH = 4  # logisch
    BAR_INSET = 3  # logisch
    STEP = 40  # logisch, eine Tastenstufe

    def __init__(self, master: tk.Misc, *, bg_under: str, on_scroll: Callable[[float], None] | None = None) -> None:
        super().__init__(master, bg=bg_under)
        self._bg_under = bg_under
        self._on_scroll = on_scroll
        self._offset = 0

        self.canvas = tk.Canvas(self, bg=bg_under, bd=0, highlightthickness=0, takefocus=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=bg_under)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.bar = tk.Canvas(
            self, width=px(self.BAR_WIDTH + 2 * self.BAR_INSET), bg=bg_under, bd=0, highlightthickness=0, takefocus=0
        )
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
            return 0  # noch nicht gelayoutet — sonst erschiene die Leiste grundlos
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
        with contextlib.suppress(tk.TclError):
            self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event: tk.Event) -> None:
        self.scroll_by(-int(event.delta / 120) * px(self.STEP))

    # ------------------------------------------------------------ Layout

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=(0, 0, 0, self.body.winfo_reqheight()))
        self._draw_bar()

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self.scroll_to(self._offset)  # Position halten, Grenzen neu prüfen
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
        image = surface(
            px(self.BAR_WIDTH), length, Radius.pill(px(self.BAR_WIDTH)), Theme.CONTROL_HOVER, squircle=False
        )
        self._bar_image = image
        self.bar.create_image(px(self.BAR_INSET), top, anchor="nw", image=image)

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self.bar.configure(width=px(self.gutter_width()))
            self._gutter.configure(width=px(self.gutter_width()))
            self._draw_bar()
        except tk.TclError:  # pragma: no cover
            pass

    def _on_theme_change(self) -> None:
        """Nur der gemerkte Untergrund und der Balken.

        Rahmen, Canvas, Körper und Leiste sind gewöhnliche Tk-Widgets im selben
        Baum — deren `bg` hat `retint()` bereits umgeschlüsselt. Die
        Rollposition bleibt unangetastet.
        """
        self._bg_under = Theme.remap(self._bg_under)
        with contextlib.suppress(tk.TclError):  # Widget bereits zerstört
            self._draw_bar()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)
            self._release_wheel()


class SectionHeader(tk.Label):
    """Abschnittsüberschrift über einer Gruppe."""

    def __init__(self, master: tk.Misc, text: str, *, bg_under: str) -> None:
        super().__init__(
            master,
            text=text,
            bg=bg_under,
            fg=Theme.TEXT_PRIMARY,
            font=font("section"),
            anchor="w",
            bd=0,
            padx=0,
            pady=0,
        )
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    def _on_scale_change(self, _factor: float) -> None:
        with contextlib.suppress(tk.TclError):
            self.configure(font=font("section"))

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
        super().__init__(master, bg=bg_under, bd=0, highlightthickness=0, takefocus=0, height=px(Radius.GROUP * 2))
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
        except tk.TclError:  # pragma: no cover
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
        image = surface(
            width,
            height,
            px(Radius.GROUP),
            Theme.GROUP_SURFACE,
            border=Theme.HAIRLINE,
            border_w=max(1, px(1)),
            squircle=True,
        )
        self._plate = image
        self.create_image(0, 0, anchor="nw", image=image, tags="plate")
        self.tag_lower("plate")

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self._last_size = (0, 0)
            self._sync_height()
            self.event_generate("<Configure>")
        except tk.TclError:  # pragma: no cover
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
        except tk.TclError:  # pragma: no cover - Widget bereits zerstört
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)


def separator(master: tk.Misc, bg_under: str) -> tk.Frame:
    line = tk.Frame(master, bg=Theme.HAIRLINE, height=px(1))
    return line
