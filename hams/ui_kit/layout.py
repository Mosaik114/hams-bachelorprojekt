"""Aufbau der Einstellungen: Scrollbereich, Ueberschrift, Gruppe, Zeile, Trennlinie."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable

from hams.ui_kit.animation import Animator
from hams.ui_kit.resizing import Resizing
from hams.ui_kit.scale import Scale, font, px
from hams.ui_kit.surfaces import surface
from hams.ui_kit.theme import Depth, Radius, Space, Theme, ink_width

# ---------------------------------------------------------------- Einstellungs-Bausteine


class ScrollArea(tk.Frame):
    """Scrollbarer Bereich mit schlanker eigener Leiste und Kanten oben und unten.

    Tk bringt keinen scrollbaren Container mit; der übliche Weg ist ein Canvas
    mit einem Fenster darin. Die Leiste ist ein selbst gezeichneter Balken statt
    einer klassischen Tk-Scrollbar, und sie erscheint nur, wenn der Inhalt
    tatsächlich höher ist als der Ausschnitt.

    Die Leiste ist sichtbar (`SCROLL_THUMB`, mindestens 3:1 zum Papier) und bedienbar: der Daumen lässt sich
    ziehen, ein Klick in die Rinne blättert eine Seite. Unter dem Zeiger wird er breiter. Früher war er im
    Hellen mit 1,15:1 kaum zu sehen und reagierte auf keinen Klick.

    Mit `edges=True` zeigt der Bereich eine feine Linie oben, sobald Inhalt nach oben weggerollt ist, und unten,
    solange darunter noch Inhalt folgt: die Seite endet an einer gezeichneten Kante statt an einer mitten in der
    Zeile abgeschnittenen Schrift.
    """

    BAR_WIDTH = 4  # logisch
    BAR_HOVER_WIDTH = 6  # logisch, unter dem Zeiger
    BAR_INSET = 3  # logisch
    #: Distance of the bar from the window edge (logical): the edge grip for resizing lies there.
    EDGE_GAP = Space.SM
    STEP = 40  # logisch, eine Tastenstufe
    #: Wheel and keys glide to their target in this time instead of jumping (only with animations on). A new
    #: notch during the glide extends the target, so a fast spin stays one continuous movement.
    GLIDE_MS = 140

    def __init__(
        self,
        master: tk.Misc,
        *,
        bg_under: str,
        on_scroll: Callable[[float], None] | None = None,
        edges: bool = False,
    ) -> None:
        super().__init__(master, bg=bg_under)
        self._bg_under = bg_under
        self._on_scroll = on_scroll
        self._offset = 0
        self._bar_hover = False
        #: While the thumb is dragged: (pointer y at the press, offset at the press).
        self._bar_drag: tuple[int, int] | None = None
        self.top_edge: tk.Frame | None = None
        self.bottom_edge: tk.Frame | None = None
        if edges:
            self.top_edge = tk.Frame(self, bg=bg_under, height=max(1, px(1)))
            self.top_edge.pack(side="top", fill="x")
            self.bottom_edge = tk.Frame(self, bg=bg_under, height=max(1, px(1)))
            self.bottom_edge.pack(side="bottom", fill="x")
        #: Where the running glide ends, or None.
        self._target: int | None = None
        self._bar_item: int | None = None
        self._bar_key: tuple | None = None

        self.canvas = tk.Canvas(self, bg=bg_under, bd=0, highlightthickness=0, takefocus=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=bg_under)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.bar = tk.Canvas(
            self, width=px(self.BAR_WIDTH + 2 * self.BAR_INSET), bg=bg_under, bd=0, highlightthickness=0, takefocus=0
        )
        self.bar.drag_surface = False  # the bar scrolls; pressing it must not move the window
        self.bar.bind("<Enter>", lambda _e: self._set_bar_hover(True), add="+")
        self.bar.bind("<Leave>", lambda _e: self._set_bar_hover(self._bar_drag is not None), add="+")
        self.bar.bind("<ButtonPress-1>", self._on_bar_press, add="+")
        self.bar.bind("<B1-Motion>", self._on_bar_drag, add="+")
        self.bar.bind("<ButtonRelease-1>", self._on_bar_release, add="+")
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
            self._update_edges()
            if self._on_scroll is not None:
                self._on_scroll(offset)
        elif self._offset == 0:
            self.canvas.yview_moveto(0)

    # ------------------------------------------------------------ edges and bar

    def _update_edges(self) -> None:
        """A line above where content scrolled away, a line below where more content follows."""
        if self.top_edge is None or self.bottom_edge is None:
            return
        try:
            rest = self.scrollable
            self.top_edge.configure(bg=Theme.HAIRLINE if self._offset > 0 else self._bg_under)
            self.bottom_edge.configure(bg=Theme.HAIRLINE if self._offset < rest else self._bg_under)
        except tk.TclError:  # pragma: no cover - the window is going away
            pass

    @property
    def more_below(self) -> bool:
        return self._offset < self.scrollable

    def _thumb(self) -> tuple[int, int] | None:
        """(top, length) of the thumb in pixels, or None without a bar."""
        span = self.body.winfo_reqheight()
        view = self.canvas.winfo_height()
        if span <= view or view <= 1:
            return None
        length = max(px(20), int(view * view / span))
        top = int((view - length) * (self._offset / max(1, self.scrollable)))
        return top, length

    def _set_bar_hover(self, hover: bool) -> None:
        if hover != self._bar_hover:
            self._bar_hover = hover
            self._draw_bar()

    def _on_bar_press(self, event: tk.Event) -> str:
        thumb = self._thumb()
        if thumb is not None:
            top, length = thumb
            if top <= event.y < top + length:
                self._bar_drag = (event.y, self._offset)
            else:
                self.scroll_page(-1 if event.y < top else 1)
        return "break"

    def _on_bar_drag(self, event: tk.Event) -> str:
        thumb = self._thumb()
        if self._bar_drag is not None and thumb is not None:
            start_y, start_offset = self._bar_drag
            room = max(1, self.canvas.winfo_height() - thumb[1])
            self.scroll_to(start_offset + round((event.y - start_y) * self.scrollable / room))
        return "break"

    def _on_bar_release(self, event: tk.Event) -> str:
        self._bar_drag = None
        inside = 0 <= event.x < self.bar.winfo_width() and 0 <= event.y < self.bar.winfo_height()
        self._set_bar_hover(inside)
        return "break"

    def scroll_by(self, delta: int, *, glide: bool = False) -> None:
        """Moves by `delta` px; `glide` lets wheel and keys ease out instead of jumping."""
        if not (glide and Animator.enabled):
            self.scroll_to(self._offset + delta)
            return
        base = self._target if self._target is not None else self._offset
        self.glide_to(base + delta)

    def glide_to(self, offset: int) -> None:
        """Eases out to `offset`, starting from where the view is now."""
        target = max(0, min(int(offset), self.scrollable))
        start = self._offset
        if target == start:
            Animator.cancel(self.canvas, "scroll")
            self._target = None
            return
        self._target = target

        def step(fraction: float) -> None:
            self.scroll_to(round(start + (target - start) * fraction))

        def done() -> None:
            self._target = None

        Animator.run(self.canvas, "scroll", self.GLIDE_MS, step, done)

    def scroll_page(self, direction: int) -> None:
        self.scroll_by(direction * max(1, self.canvas.winfo_height() - px(self.STEP)), glide=True)

    def refresh(self) -> None:
        """Neu bewerten, ob die Leiste noch gebraucht wird.

        Nötig, wenn Inhalt entfernt wurde: Tk rechnet die geforderte Höhe eines
        Frames nicht neu, solange kein Configure-Ereignis anfällt.
        """
        self.update_idletasks()
        self.canvas.configure(scrollregion=(0, 0, 0, self.body.winfo_reqheight()))
        self.scroll_to(self._offset)
        self._draw_bar()
        self._update_edges()

    def bind_keys(self, widget: tk.Misc) -> None:
        """Tastaturscrollen an ein Fenster hängen (Pfeile, Bild auf/ab, Pos1/Ende)."""
        widget.bind("<Up>", lambda _e: self.scroll_by(-px(self.STEP), glide=True), add="+")
        widget.bind("<Down>", lambda _e: self.scroll_by(px(self.STEP), glide=True), add="+")
        widget.bind("<Prior>", lambda _e: self.scroll_page(-1), add="+")
        widget.bind("<Next>", lambda _e: self.scroll_page(1), add="+")
        widget.bind("<Home>", lambda _e: self._glide_or_jump(0), add="+")
        widget.bind("<End>", lambda _e: self._glide_or_jump(self.scrollable), add="+")

    def _glide_or_jump(self, offset: int) -> None:
        if Animator.enabled:
            self.glide_to(offset)
        else:
            self.scroll_to(offset)

    # Windows und macOS melden das Rad als <MouseWheel> mit `delta`; Tk 8.6 unter
    # Linux (X11/XWayland) dagegen als Maustaste 4 (hoch) und 5 (runter).
    WHEEL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")

    def _grab_wheel(self, _event=None) -> None:
        for sequence in self.WHEEL_EVENTS:
            self.canvas.bind_all(sequence, self._on_wheel)

    def _release_wheel(self, _event=None) -> None:
        with contextlib.suppress(tk.TclError):
            for sequence in self.WHEEL_EVENTS:
                self.canvas.unbind_all(sequence)

    def _on_wheel(self, event: tk.Event) -> None:
        num = getattr(event, "num", None)
        if num == 4:
            notches = 1.0
        elif num == 5:
            notches = -1.0
        else:
            # Fractions too: a precision touchpad reports small deltas (e.g. 30), which `int(delta / 120)` turned
            # into 0 - the touchpad did not scroll at all.
            notches = event.delta / 120
        delta = round(-notches * px(self.STEP))
        if delta:
            self.scroll_by(delta, glide=True)

    # ------------------------------------------------------------ Layout

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=(0, 0, 0, self.body.winfo_reqheight()))
        self._draw_bar()
        self._update_edges()

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self.scroll_to(self._offset)  # Position halten, Grenzen neu prüfen
        self._draw_bar()
        self._update_edges()

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
        """The thumb. While scrolling only its position changes, so the item is moved, not drawn anew."""
        thumb = self._thumb()
        if thumb is None:
            self.bar.delete("all")
            self._bar_item = self._bar_key = None
            self._show_bar(False)
            return
        self._show_bar(True)
        top, length = thumb
        wide = self._bar_hover or self._bar_drag is not None
        width = px(self.BAR_HOVER_WIDTH if wide else self.BAR_WIDTH)
        colour = Theme.TEXT_TERTIARY if wide else Theme.SCROLL_THUMB
        left = (px(self.gutter_width()) - width) // 2
        key = (length, colour, width, left)
        if self._bar_item is not None and key == self._bar_key:
            self.bar.coords(self._bar_item, left, top)
            return
        self.bar.delete("all")
        image = surface(width, length, Radius.pill(width), colour, squircle=False)
        self._bar_image = image
        self._bar_key = key
        self._bar_item = self.bar.create_image(left, top, anchor="nw", image=image)

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
            self._update_edges()

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

    Rand und Seite der Platte verschieben das Fenster nicht (`drag_surface`), obwohl die Canvas für die runden
    Ecken den Fenstergrund trägt.

    Die Zeilen liegen in einem Frame, das um `Space.GROUP_INSET` eingerückt im
    Canvas sitzt. Das genügt: bei 10 px Radius liegt die Ecke (4, 4) noch
    innerhalb der Rundung, der Rand bleibt also frei. Den sichtbaren Abstand
    zum Text liefert die Zeile selbst mit `Space.ROW_X`.
    """

    drag_surface = False

    def __init__(self, master: tk.Misc, *, bg_under: str) -> None:
        super().__init__(master, bg=bg_under, bd=0, highlightthickness=0, takefocus=0, height=px(Radius.GROUP * 2))
        self._bg_under = bg_under
        self._last_size = (0, 0)
        self._render_job: str | None = None
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
            wanted = self.body.winfo_reqheight() + 2 * self.inset + px(Depth.GROUP)
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
        self._schedule_render()

    def _schedule_render(self) -> None:
        """Draws the plate once the layout has settled, not for every size it passes through.

        While a window is being built, a group grows row by row and reports a new height each time (measured: 44
        renders for the six groups of the settings window). Only the last size is ever seen.
        """
        if Resizing.active():
            # While a window is resized the size *is* settled for this frame: an idle render would leave the old
            # picture on screen for a frame, too small or cut off.
            if self._render_job is not None:
                with contextlib.suppress(tk.TclError, ValueError):
                    self.after_cancel(self._render_job)
            self._render_settled()
            return
        if self._render_job is None:
            self._render_job = self.after_idle(self._render_settled)

    def _render_settled(self) -> None:
        self._render_job = None
        width, height = self._last_size
        if width > 1:
            self._render(width, height)

    def _render(self, width: int, height: int) -> None:
        self.delete("plate")
        # Eine Papierplatte (Loratu): Tuscherand und darunter ihre Seite.
        image = surface(
            width,
            height,
            px(Radius.GROUP),
            Theme.GROUP_SURFACE,
            border=Theme.INK,
            border_w=ink_width(),
            squircle=True,
            side=Theme.GROUP_SIDE,
            depth=px(Depth.GROUP),
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
            if self._render_job is not None:
                with contextlib.suppress(tk.TclError):
                    self.after_cancel(self._render_job)
                self._render_job = None
