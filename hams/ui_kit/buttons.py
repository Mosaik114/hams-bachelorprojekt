"""Knoepfe: Grundlage aller Bedienelemente, Primaer-, Sekundaer- und Icon-Knopf."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from PIL import Image, ImageTk

from hams.ui_kit.icons import ICON_BOX, render_icon
from hams.ui_kit.scale import Scale, font, px
from hams.ui_kit.states import ControlStyle, FocusMixin, State, StateMixin
from hams.ui_kit.surfaces import _hex_to_rgb, surface
from hams.ui_kit.theme import Height, Radius, Space, Theme

# ---------------------------------------------------------------- Basis-Controls


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
        if event.width != self._last_width:
            self._last_width = event.width
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
        fill, _foreground = self.colours()
        ring_w, ring_gap = px(ControlStyle.RING_WIDTH), px(ControlStyle.RING_GAP)
        return surface(
            width,
            height + 2 * (ring_w + ring_gap),
            px(self._logical_radius),
            fill if fill is not None else self._bg_under,
            squircle=self._squircle,
            ring=self.ring(),
            ring_width=ring_w,
            ring_gap=ring_gap,
        )


class PrimaryButton(_SurfaceControl):
    """Die eine dominante Aktion eines Fensters."""

    def __init__(
        self, master: tk.Misc, text: str, command: Callable[[], None], *, bg_under: str, role: str = "primary"
    ) -> None:
        super().__init__(
            master,
            bg_under=bg_under,
            role=role,
            height=Height.PRIMARY,
            radius=Radius.BUTTON,
            command=command,
            text=text,
        )


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
        self.configure(width=px(size), height=px(size))
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
            self.configure(width=side, height=side)
            self._last_width = side
            self._render()
        except tk.TclError:  # pragma: no cover
            pass

    def _paint(self, _width: int, _height: int) -> ImageTk.PhotoImage:
        side = px(self._side)
        fill, foreground = self.colours()
        if self._hover_colour and self.has_state(State.HOVER) and self.enabled:
            foreground = self._hover_colour
        ring = self.ring()
        if fill is None and ring is None:
            # Im Ruhezustand gibt es nichts zu zeichnen: die Flaeche *ist* der
            # Untergrund. Eine gerundete Platte in genau dieser Farbe waere
            # trotzdem nicht neutral — beim Verkleinern ueberschwingt Lanczos
            # an der Kante. Auf dunklem Grund fiel das nicht auf (4 Bildpunkte,
            # 7 Stufen daneben), auf hellem stand dort ein sichtbarer heller
            # Ring um jede Icon-Schaltflaeche (35 Bildpunkte, bis zu 35 Stufen).
            # Eine glatte Flaeche hat keine Kante und damit kein Ueberschwingen.
            plate = Image.new("RGBA", (side, side), _hex_to_rgb(self._bg_under) + (255,))
        else:
            ring_w, ring_gap = px(ControlStyle.RING_WIDTH), px(ControlStyle.RING_GAP)
            plate = ImageTk.getimage(
                surface(
                    side,
                    side,
                    px(Radius.CONTROL),
                    fill if fill is not None else self._bg_under,
                    squircle=True,
                    ring=ring,
                    ring_width=ring_w,
                    ring_gap=ring_gap,
                )
            )
        glyph_size = px(self._icon_size)
        glyph = render_icon(self._icon_name, glyph_size, foreground)
        offset = ((plate.width - glyph_size) // 2, (plate.height - glyph_size) // 2)
        plate.paste(glyph, offset, glyph)
        return ImageTk.PhotoImage(plate)
