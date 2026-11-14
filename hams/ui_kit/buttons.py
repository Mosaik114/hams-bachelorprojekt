"""Knoepfe: Grundlage aller Bedienelemente, Primaer-, Sekundaer- und Icon-Knopf."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable

from PIL import ImageTk

from hams.ui_kit.caches import _img_cache
from hams.ui_kit.icons import ICON_BOX, render_icon
from hams.ui_kit.scale import Scale, font, px
from hams.ui_kit.states import ControlStyle, FocusMixin, Pressable, State, StateMixin
from hams.ui_kit.surfaces import paint
from hams.ui_kit.theme import Depth, Height, Radius, Space, Theme
from hams.ui_kit.tooltip import Tooltip

# ---------------------------------------------------------------- Basis-Controls


def on_hidden_page(widget: tk.Misc) -> bool:
    """Ob das Widget auf einer gerade verborgenen Seite liegt (ein Vorfahr trägt `hidden_page = True`)."""
    node = getattr(widget, "master", None)
    while node is not None:
        if getattr(node, "hidden_page", False):
            return True
        node = getattr(node, "master", None)
    return False


class BaseControl(tk.Canvas, StateMixin, FocusMixin, Pressable):
    """Gemeinsame Grundlage aller gemalten Controls — ein „Stein“ auf einer Canvas.

    Bündelt, was sonst jede Klasse einzeln erfände: Zustände, Tastaturfokus, Drücken, das Neuzeichnen bei
    DPI-Wechsel und das saubere Abmelden beim Zerstören. Unterklassen liefern nur `_paint()` (die Fläche als
    Bild); die Beschriftung setzt die Basis als Tk-Text **auf die Mitte der Fläche** (nicht des Bildes, unter
    dem die Seite liegt) und lässt sie beim Drücken mit einsinken.

    Die Breite fragt das Control selbst an: Text plus Innenabstand (`natural_width`), mindestens eine gesetzte
    Breite (`configure(width=...)`, `keep_logical_width`). Früher war es ein Label, das immer genau so breit
    sein wollte wie sein letztes Bild plus Rand — Beschriftungen liefen über feste Breiten hinaus, und in
    unbegrenzten Behältern wuchs die Breite mit jedem Neuzeichnen.

    `text`, `fg` und `font` gehen wie bei einem Label über `configure`/`cget`.
    """

    #: Innenabstand links und rechts der Beschriftung (logisch).
    TEXT_PADDING = Space.XL

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
        self._text = str(label_kwargs.pop("text", ""))
        self._font_role = font_role
        self._font = font(font_role)
        self._fg = Theme.TEXT_PRIMARY
        self._min_width = 0
        self._logical_height = height
        super().__init__(
            master,
            bg=bg_under,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            height=px(height) + px(Depth.CONTROL),
            width=1,
        )
        self.init_states(role)
        self._bg_under = bg_under
        self._logical_radius = radius
        self._squircle = squircle
        self._command = command
        self._last_width = 0
        self._image: ImageTk.PhotoImage | None = None

        self.init_focus(self._invoke)
        self.init_press(self._command_if_any)
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)
        if label_kwargs:
            self.configure(**label_kwargs)
        self._update_request()

    # ------------------------------------------------------------ wie ein Label

    def configure(self, cnf=None, **kw):
        """Wie bei einem Label: `text`, `fg`, `font` gehören dem Control, `width` ist eine Mindestbreite."""
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
            cnf = None
        if cnf is not None or not kw:
            return super().configure(cnf)
        redraw = False
        if "text" in kw:
            self._text = str(kw.pop("text"))
            redraw = True
        if "fg" in kw:
            self._fg = kw.pop("fg")
        if "font" in kw:
            self._font = kw.pop("font")
            redraw = True
        for label_only in ("image", "compound", "anchor", "justify", "padx", "pady"):
            kw.pop(label_only, None)
        if "width" in kw:
            self._min_width = int(kw.pop("width"))
            redraw = True
        if kw:
            super().configure(**kw)
        if redraw:
            self._update_request()
            self._draw_text()
        return None

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "fg":
            return self._fg
        if key == "font":
            return str(self._font)
        if key == "image":
            return str(self._image) if self._image is not None else ""
        return super().cget(key)

    def natural_width(self) -> int:
        """So breit will das Control sein: Beschriftung plus Innenabstand (0 ohne Text)."""
        if not self._text:
            return 0
        return self._measure(self._text) + 2 * px(self.TEXT_PADDING)

    def _measure(self, text: str) -> int:
        try:
            return int(self.tk.call("font", "measure", self._font, text))
        except tk.TclError:  # pragma: no cover - font not yet created
            return 0

    def _update_request(self) -> None:
        """Mindestens die gesetzte Breite, nie schmaler als die Beschriftung braucht."""
        with contextlib.suppress(tk.TclError):  # Widget bereits zerstört
            super().configure(
                width=max(1, self._min_width, self.natural_width()),
                height=px(self._logical_height) + px(Depth.CONTROL),
            )

    # ------------------------------------------------------------ Verhalten

    def _command_if_any(self) -> None:
        if self._command is not None:
            self._command()

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
        self.press_enter()

    def _on_leave(self, _event=None) -> None:
        self.press_leave()

    def _on_press(self, _event=None) -> None:
        self.press_down()

    def _on_release(self, event=None) -> None:
        self.press_up(event)

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
            self._update_request()
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
        """The font role given at creation; a DPI change takes the font of this role again (not always "button")."""
        return self._font_role

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
        self._fg = self.colours()[1]
        self.delete("surface")
        self.create_image(0, 0, anchor="nw", image=image, tags="surface")
        self.tag_lower("surface")
        self._draw_text()

    def body_centre(self) -> tuple[int, int]:
        """Mitte der Fläche (ohne die Seite darunter); gedrückt um die Tiefe tiefer."""
        depth = px(Depth.CONTROL)
        sunk = depth if self.has_state(State.PRESSED) else 0
        return self._last_width // 2, px(self._logical_height) // 2 + sunk

    def text_offset(self) -> int:
        """Waagerechte Verschiebung der Beschriftung aus der Mitte (für ein Icon links davon)."""
        return 0

    def _draw_text(self) -> None:
        try:
            self.delete("label")
            if not self._text or self._last_width <= 1:
                return
            x, y = self.body_centre()
            self.create_text(
                x + self.text_offset(),
                y,
                text=self._text,
                font=self._font,
                fill=self._fg,
                anchor="center",
                tags="label",
            )
        except tk.TclError:  # pragma: no cover - Widget bereits zerstört
            pass

    def _paint(self, width: int, height: int) -> ImageTk.PhotoImage:  # pragma: no cover
        raise NotImplementedError


class _SurfaceControl(BaseControl):
    """Control mit gemalter Fläche (Tuscherand, Seite) und Fokusrand."""

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
        self._update_request()
        self._render()

    def natural_width(self) -> int:
        width = super().natural_width()
        return width + px(ICON_BOX) + px(Space.MD) if self._icon_name else width

    def text_offset(self) -> int:
        return (px(ICON_BOX) + px(Space.MD)) // 2 if self._icon_name else 0

    def _on_theme_change(self) -> None:
        if self._icon_colour is not None:
            self._icon_colour = Theme.remap(self._icon_colour)
        super()._on_theme_change()

    def _paint(self, width: int, height: int) -> ImageTk.PhotoImage:
        base = super()._paint(width, height)
        if self._icon_name is None:
            return base

        icon_size = px(ICON_BOX)
        gap = px(Space.MD)
        text_width = self._measure(self._text)
        # Icon und Text stehen als Gruppe mittig: das Icon links, der Text um seine halbe Breite nach rechts.
        group_left = (base.width() - (icon_size + gap + text_width)) // 2
        _x, centre = self.body_centre()
        top = centre - icon_size // 2
        colour = self._icon_colour
        if colour is None or not State.is_interactive(self._states):
            _fill, colour = self.colours()
        # One picture per look, from the shared store: hover and press used to build a new image every time.
        key = ("icon-button", str(base), self._icon_name, colour, group_left, top)
        cached = _img_cache.fetch(key)
        if cached is not None:
            return cached
        pil = ImageTk.getimage(base).copy()
        glyph = render_icon(self._icon_name, icon_size, colour)
        pil.paste(glyph, (max(0, group_left), max(0, top)), glyph)
        return _img_cache.store(key, ImageTk.PhotoImage(pil))


class IconButton(BaseControl):
    """Runder Stein mit Icon — Zahnrad und Kreuz in der Kopfzeile.

    Im Ruhezustand ohne Fläche; erst der Zeiger legt eine dezente Fläche unter.
    """

    stretch_stacked = False
    #: Durchmesser in logischen Pixeln.
    SIZE = 34

    def __init__(
        self,
        master: tk.Misc,
        icon_name: str,
        command: Callable[[], None],
        *,
        bg_under: str,
        size: int = SIZE,
        icon_size: int = ICON_BOX,
        tooltip: str = "",
    ) -> None:
        self._icon_name = icon_name
        self._icon_size = icon_size
        self._hover_colour: str | None = None
        super().__init__(master, bg_under=bg_under, role="plain", height=size, radius=Radius.CONTROL, command=command)
        self._side = size
        self.configure(width=px(size))
        self._last_width = px(size)
        self._render()
        #: The icon has no word of its own; the tip names it (pointer rest or keyboard focus).
        self.tooltip = Tooltip(self, tooltip) if tooltip else None

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
            self.configure(width=side)
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
        glyph_size = px(self._icon_size)
        top = (side - glyph_size) // 2 + (depth if pressed else 0)
        key = ("icon-stone", spec, self._icon_name, glyph_size, foreground, top)
        cached = _img_cache.fetch(key)
        if cached is not None:
            return cached
        plate = ImageTk.getimage(paint(spec)).copy()
        glyph = render_icon(self._icon_name, glyph_size, foreground)
        plate.paste(glyph, ((side - glyph_size) // 2, top), glyph)
        return _img_cache.store(key, ImageTk.PhotoImage(plate))
