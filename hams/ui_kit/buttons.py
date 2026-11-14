"""Knoepfe: Grundlage aller Bedienelemente, Primaer-, Sekundaer- und Icon-Knopf."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from PIL import ImageTk

from hams.ui_kit.icons import ICON_BOX, render_icon
from hams.ui_kit.scale import Scale, font, px
from hams.ui_kit.states import ControlStyle, FocusMixin, State, StateMixin
from hams.ui_kit.surfaces import paint
from hams.ui_kit.theme import Depth, Height, Radius, Space, Theme

# ---------------------------------------------------------------- Basis-Controls


def on_hidden_page(widget: tk.Misc) -> bool:
    """Ob das Widget auf einer gerade verborgenen Seite liegt (ein Vorfahr trägt `hidden_page = True`)."""
    node = getattr(widget, "master", None)
    while node is not None:
        if getattr(node, "hidden_page", False):
            return True
        node = getattr(node, "master", None)
    return False


class BaseControl(tk.Label, StateMixin, FocusMixin):
    """Gemeinsame Grundlage aller neuen Controls.

    Bündelt, was sonst jede Klasse einzeln erfände: Zustände, Tastaturfokus,
    Maus-Rückmeldung, das Neuzeichnen bei DPI-Wechsel und das saubere Abmelden
    beim Zerstören. Unterklassen liefern nur noch `_paint()`.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        bg_under: str,
        role: str = "secondary",
        height: int = Height.STANDARD,
        radius: int = Radius.CONTROL,
        command: Callable[[], None] | None = None,
        squircle: bool = True,
        font_role: str = "button",
        **label_kwargs,
    ) -> None:
        super().__init__(
            master,
            bg=bg_under,
            compound="center",
            bd=0,
            highlightthickness=0,
            font=font(font_role),
            cursor="hand2",
            **label_kwargs,
        )
        self.init_states(role)
        self._bg_under = bg_under
        self._logical_height = height
        self._logical_radius = radius
        self._squircle = squircle
        self._command = command
        self._last_width = 0
        self._image: ImageTk.PhotoImage | None = None

        self.init_focus(self._invoke)
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    # ------------------------------------------------------------ Verhalten

    def _invoke(self) -> None:
        if State.is_interactive(self._states) and self._command is not None:
            self._command()

    def _on_enter(self, _event=None) -> None:
        # Hover bleibt bewusst ohne Uebergang. Ein Flaechenwechsel ist keine
        # Farbe an einem Widget, sondern ein neu gerendertes PIL-Bild: gemessen
        # 0,74 ms je Bild gegen 0,001 ms aus dem Zwischenspeicher. Ein
        # Uebergang legte je Bewegung sechs Einmalfarben in den Bildspeicher
        # (Platz fuer 256) — nach gut 40 Hovern waere nichts Wiederverwendbares
        # mehr darin und *jedes* Neuzeichnen teuer. Der Gewinn waere ein
        # 60-ms-Effekt, den kaum jemand bemerkt.
        if self.enabled:
            self.set_state(State.HOVER, True)

    def _on_leave(self, _event=None) -> None:
        self.set_state(State.HOVER, False)
        self.set_state(State.PRESSED, False)

    def _on_press(self, _event=None) -> None:
        if State.is_interactive(self._states):
            self.set_state(State.PRESSED, True)
            self.focus_set()

    def _on_release(self, _event=None) -> None:
        fired = self.has_state(State.PRESSED)
        self.set_state(State.PRESSED, False)
        if fired:
            self._invoke()

    def set_enabled(self, enabled: bool) -> None:
        self.set_state(State.DISABLED, not enabled)
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=1 if enabled else 0)

    def set_role(self, role: str) -> None:
        """Wechselt die Farbrolle, etwa von `primary` auf `recording`."""
        if role != self._role:
            self._role = role
            self._render()

    # ------------------------------------------------------------ Zeichnen

    def _on_configure(self, event: tk.Event) -> None:
        if event.width == self._last_width:
            return
        self._last_width = event.width
        if not on_hidden_page(self):
            self._render()
        else:
            # Auf einer verborgenen Seite (Reiter der Einstellungen) hat das Feld keinen Platz, der es begrenzt:
            # jedes neue Bild verlangt wieder eine andere Breite, und Tk legt endlos neu aus. Gezeichnet wird,
            # sobald die Seite gezeigt wird (`render_if_stale`).
            self._stale = True

    def render_if_stale(self) -> None:
        """Holt ein Neuzeichnen nach, das ausfiel, solange das Control verborgen war."""
        if getattr(self, "_stale", False):
            self._stale = False
            self._render()

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self.configure(font=font(self._font_role_name()))
            self._render()
        except tk.TclError:  # pragma: no cover - Widget bereits zerstört
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)

    def _on_theme_change(self) -> None:
        """Untergrund umschlüsseln und neu zeichnen.

        Füllung und Textfarbe holt `_render()` ohnehin frisch aus
        `ControlStyle`; gemerkt ist an einem Control nur der Untergrund, auf
        dem es sitzt.
        """
        self._bg_under = Theme.remap(self._bg_under)
        try:
            self.configure(bg=self._bg_under)
        except tk.TclError:  # pragma: no cover - Widget bereits zerstört
            return
        self._render()

    def _font_role_name(self) -> str:
        return "button"

    def on_state_change(self) -> None:
        self._render()

    def _render(self) -> None:
        if self._last_width <= 1:
            return
        try:
            image = self._paint(self._last_width, px(self._logical_height))
        except tk.TclError:  # pragma: no cover
            return
        self._image = image
        _fill, foreground = self.colours()
        self.configure(image=image, fg=foreground)

    def _paint(self, width: int, height: int) -> ImageTk.PhotoImage:  # pragma: no cover
        raise NotImplementedError


class _SurfaceControl(BaseControl):
    """Control mit flacher, gefüllter Fläche und Fokusring."""

    #: In einer gestapelten Zeile nicht auf volle Breite ziehen.
    stretch_stacked = False

    def _paint(self, width: int, height: int) -> ImageTk.PhotoImage:
        spec = self.surface_spec(width, height, px(self._logical_radius), under=self._bg_under, squircle=self._squircle)
        return paint(spec)


class SecondaryButton(_SurfaceControl):
    """Neutrale, materialartige Aktion — optional mit Icon links vom Text.

    Das Icon wird in die Fläche einkomponiert und aus der gemessenen Textbreite
    positioniert, damit Icon und Text zusammen optisch mittig stehen. Tk kann
    einen Text mit `compound="center"` nicht versetzen.
    """

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        bg_under: str,
        icon_name: str | None = None,
        icon_colour: str | None = None,
        height: int = Height.PRIMARY,
        role: str = "secondary",
    ) -> None:
        self._icon_name = icon_name
        self._icon_colour = icon_colour
        super().__init__(
            master, bg_under=bg_under, role=role, height=height, radius=Radius.BUTTON, command=command, text=text
        )

    def set_icon(self, icon_name: str | None, colour: str | None = None) -> None:
        self._icon_name, self._icon_colour = icon_name, colour
        self._render()

    def _on_theme_change(self) -> None:
        if self._icon_colour is not None:
            self._icon_colour = Theme.remap(self._icon_colour)
        super()._on_theme_change()

    def _paint(self, width: int, height: int) -> ImageTk.PhotoImage:
        base = super()._paint(width, height)
        if self._icon_name is None:
            return base

        pil = ImageTk.getimage(base)
        icon_size = px(ICON_BOX)
        gap = px(Space.MD)
        # Ueber Tk messen statt ueber das Font-Objekt: font() liefert vor
        # init_fonts() ein Tupel, und cget("font") stimmt immer.
        text_width = int(self.tk.call("font", "measure", self.cget("font"), self.cget("text")))
        # Der Text steht durch compound="center" mittig; das Icon muss deshalb
        # vom Textanfang aus nach links gesetzt werden, nicht von der Gruppe aus
        # - sonst ueberlappt es den Text.
        text_left = (pil.width - text_width) // 2
        group_left = text_left - gap - icon_size
        top = (pil.height - icon_size) // 2

        colour = self._icon_colour
        if colour is None or not State.is_interactive(self._states):
            _fill, colour = self.colours()
        glyph = render_icon(self._icon_name, icon_size, colour)
        pil.paste(glyph, (max(0, group_left), max(0, top)), glyph)
        composed = ImageTk.PhotoImage(pil)
        self._composed = composed  # Referenz halten
        return composed


class IconButton(BaseControl):
    """Quadratische Schaltfläche mit Icon — Zahnrad und Kreuz in der Kopfzeile.

    Im Ruhezustand ohne Fläche; erst der Zeiger legt eine dezente Fläche unter.
    """

    stretch_stacked = False

    def __init__(
        self,
        master: tk.Misc,
        icon_name: str,
        command: Callable[[], None],
        *,
        bg_under: str,
        size: int = Height.STANDARD,
        icon_size: int = ICON_BOX,
        tooltip: str = "",
    ) -> None:
        self._icon_name = icon_name
        self._icon_size = icon_size
        self._hover_colour: str | None = None
        super().__init__(master, bg_under=bg_under, role="plain", height=size, radius=Radius.CONTROL, command=command)
        self._side = size
        self.configure(width=px(size), height=px(size) + px(Depth.CONTROL))
        self._last_width = px(size)
        self._render()

    def set_hover_colour(self, colour: str | None) -> None:
        """Eigene Icon-Farbe im Hover — etwa Rot für das Schliessen-Kreuz."""
        self._hover_colour = colour
        self._render()

    def _on_theme_change(self) -> None:
        if self._hover_colour is not None:
            self._hover_colour = Theme.remap(self._hover_colour)
        super()._on_theme_change()

    def _font_role_name(self) -> str:
        return "icon"

    def _on_configure(self, _event: tk.Event) -> None:
        pass  # feste Grösse, die Breite kommt nicht vom Layout

    def _on_scale_change(self, _factor: float) -> None:
        try:
            side = px(self._side)
            self.configure(width=side, height=side + px(Depth.CONTROL))
            self._last_width = side
            self._render()
        except tk.TclError:  # pragma: no cover
            pass

    def _paint(self, _width: int, _height: int) -> ImageTk.PhotoImage:
        """Ein gemalter Kreisstein (Loratu): Tuscherand, Seite darunter, sinkt beim Drücken ein."""
        side = px(self._side)
        depth = px(Depth.CONTROL)
        _fill, foreground = self.colours()
        hovered = self.enabled and (self.has_state(State.HOVER) or self.has_state(State.PRESSED))
        if self._hover_colour and hovered:
            foreground = self._hover_colour
        pressed = self.has_state(State.PRESSED)
        stone = {State.HOVER} if hovered else set()
        stone |= {state for state in self._states if state in (State.PRESSED, State.FOCUSED)}
        spec = ControlStyle.surface_spec(
            "secondary", stone, side, side, side // 2, under=self._bg_under, squircle=False
        )
        plate = ImageTk.getimage(paint(spec)).copy()
        glyph_size = px(self._icon_size)
        glyph = render_icon(self._icon_name, glyph_size, foreground)
        top = (side - glyph_size) // 2 + (depth if pressed else 0)
        plate.paste(glyph, ((side - glyph_size) // 2, top), glyph)
        return ImageTk.PhotoImage(plate)
