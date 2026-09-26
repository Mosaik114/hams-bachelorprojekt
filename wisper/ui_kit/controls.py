"""Anzeigen und Schalter: Statuspunkt, Switch, Pegel, Tastenkuerzel."""

from __future__ import annotations

import tkinter as tk
from typing import Callable


from wisper.ui_kit.animation import Animator, blend
from wisper.ui_kit.scale import Scale, font, px
from wisper.ui_kit.states import ControlStyle, FocusMixin, State, StateMixin
from wisper.ui_kit.surfaces import surface
from wisper.ui_kit.text import _text_width
from wisper.ui_kit.theme import Height, Radius, Space, Theme


# ---------------------------------------------------------------- Statuspunkt


class StatusDot(tk.Canvas):
    """Farbiger Punkt mit fester Zustandssemantik.

    Dasselbe Bauteil trägt später die Statuszeile im Hauptfenster und die
    Unterzeilen in den Einstellungen — nur in zwei Grössen.
    """

    #: Status -> Tokenname. Wie bei `ControlStyle` bewusst Namen statt Werte,
    #: damit ein Moduswechsel nicht an einer beim Import eingefrorenen Tabelle
    #: vorbeilaeuft.
    COLOURS = {
        "idle": "TEXT_TERTIARY",
        "ready": "SUCCESS",
        "recording": "RECORDING",
        "processing": "ACCENT_TINT",
        "ai": "AI",
        "warning": "WARNING",
        "error": "ERROR",
    }

    SIZE_MAIN = 7       # logisch, Statuszeile im Hauptfenster
    SIZE_ROW = 6        # logisch, Unterzeile einer Einstellungszeile

    #: Kurzer Farbwechsel. Der Text daneben springt sofort — nur der Punkt
    #: blendet, sonst wirkte die ganze Statuszeile weich und ungenau.
    CROSSFADE_MS = 130

    def __init__(self, master: tk.Misc, *, bg_under: str,
                 size: int = SIZE_MAIN, status: str = "idle") -> None:
        self._logical = size
        self._bg_under = bg_under
        self._status = status
        self._colour = self.colour(status)
        side = px(size)
        super().__init__(master, width=side, height=side, bg=bg_under,
                         bd=0, highlightthickness=0)
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)
        self._draw()

    @classmethod
    def colour(cls, status: str) -> str:
        """Farbe eines Status im aktiven Darstellungsmodus."""
        return getattr(Theme, cls.COLOURS[status])

    def set_status(self, status: str) -> None:
        if status not in self.COLOURS:
            raise KeyError(f"Unbekannter Status: {status!r}")
        if status == self._status:
            return
        previous = self._colour      # die *gezeichnete* Farbe, nicht die Zielfarbe:
        self._status = status        # ein Wechsel mitten im Lauf setzt hier an
        target = self.colour(status)

        def step(fraction: float) -> None:
            self._colour = blend(previous, target, fraction)
            self._draw()

        Animator.run(self, "colour", self.CROSSFADE_MS, step)

    @property
    def status(self) -> str:
        return self._status

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)

    def _on_scale_change(self, _factor: float) -> None:
        try:
            side = px(self._logical)
            self.configure(width=side, height=side)
            self._draw()
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            pass

    def _on_theme_change(self) -> None:
        """Punktfarbe und Untergrund auf die neue Palette bringen.

        Eine laufende Überblendung muss vorher enden: sie trägt Anfangs- und
        Zielfarbe der *alten* Palette in sich und schriebe sie im nächsten Bild
        wieder über den neuen Wert.
        """
        Animator.cancel(self, "colour")
        self._bg_under = Theme.remap(self._bg_under)
        self._colour = self.colour(self._status)
        try:
            self.configure(bg=self._bg_under)
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            return
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        side = px(self._logical)
        image = surface(side, side, Radius.pill(side), self._colour)
        self._image = image      # Referenz halten, sonst räumt Tk sie ab
        self.create_image(0, 0, anchor="nw", image=image)


class Switch(tk.Canvas, StateMixin, FocusMixin):
    """Schalter mit gleitendem Knopf.

    Der Zustand wird doppelt kodiert — Spurfarbe *und* Knopfposition. Farbe
    allein reicht nicht; in Graustufen bliebe sonst nur ein Grauwertunterschied.

    Der Knopf gleitet; der logische Zustand springt. Beides gehört getrennt:
    gespeichert wird sofort, die Bewegung ist nur Rückmeldung.
    """

    WIDTH = 40
    HEIGHT = 24
    INSET = 2

    #: Gleitdauer des Knopfes. Darüber wirkt der Schalter träge.
    ANIM_MS = 170

    def __init__(self, master: tk.Misc, *, bg_under: str, value: bool,
                 command: Callable[[bool], None]) -> None:
        self._bg_under = bg_under
        self._value = value
        self._command = command
        #: Sichtbare Stellung zwischen 0 (aus) und 1 (an). Weicht während der
        #: Bewegung bewusst vom logischen Wert ab.
        self._progress = 1.0 if value else 0.0
        side = (px(self.WIDTH), px(self.HEIGHT))
        ring = 2 * (px(ControlStyle.RING_WIDTH) + px(ControlStyle.RING_GAP))
        super().__init__(master, width=side[0] + ring, height=side[1] + ring,
                         bg=bg_under, bd=0, highlightthickness=0, cursor="hand2")
        self.init_states("secondary")
        self.init_focus(self.toggle)
        self.bind("<Enter>", lambda _e: self.enabled and self.set_state(State.HOVER, True), add="+")
        self.bind("<Leave>", lambda _e: self.set_state(State.HOVER, False), add="+")
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)
        self._draw()

    # ------------------------------------------------------------ Zustand

    @property
    def value(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        if value != self._value:
            self._value = value
            self._glide()

    def toggle(self) -> None:
        if not State.is_interactive(self._states):
            return
        self._value = not self._value
        # Erst melden, dann bewegen: das Speichern darf nicht auf Pixel warten.
        self._command(self._value)
        self._glide()

    def _glide(self) -> None:
        """Führt den Knopf an seine neue Stelle — von dort, wo er gerade steht.

        Bei schnellem Mehrfachklick löst der neue Lauf den alten an dessen
        aktueller Position ab. So entsteht keine Warteschlange, und der Knopf
        kehrt einfach um.
        """
        target = 1.0 if self._value else 0.0
        start = self._progress
        if start == target:
            self._draw()
            return

        def step(fraction: float) -> None:
            self._progress = start + (target - start) * fraction
            self._draw()

        Animator.run(self, "knob", self.ANIM_MS, step)

    def set_enabled(self, enabled: bool) -> None:
        self.set_state(State.DISABLED, not enabled)
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=1 if enabled else 0)

    def _on_press(self, _event=None) -> None:
        if State.is_interactive(self._states):
            self.set_state(State.PRESSED, True)
            self.focus_set()

    def _on_release(self, _event=None) -> None:
        fired = self.has_state(State.PRESSED)
        self.set_state(State.PRESSED, False)
        if fired:
            self.toggle()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)

    def _on_scale_change(self, _factor: float) -> None:
        try:
            ring = 2 * (px(ControlStyle.RING_WIDTH) + px(ControlStyle.RING_GAP))
            self.configure(width=px(self.WIDTH) + ring, height=px(self.HEIGHT) + ring)
            self._draw()
        except tk.TclError:      # pragma: no cover
            pass

    def on_state_change(self) -> None:
        self._draw()

    def _on_theme_change(self) -> None:
        """Untergrund umschlüsseln und neu zeichnen.

        Eine laufende Knopfbewegung darf weiterlaufen: sie verschiebt nur
        `_progress`, und Spur- wie Knopffarbe holt `_draw()` bei jedem Bild
        frisch aus `Theme`.
        """
        self._bg_under = Theme.remap(self._bg_under)
        try:
            self.configure(bg=self._bg_under)
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            return
        self._draw()

    # ------------------------------------------------------------ Zeichnen

    def _track_colour(self) -> str:
        """Spurfarbe zur aktuellen Stellung — nicht zum logischen Wert.

        Farbe und Knopf laufen dadurch gemeinsam; ein sofortiger Farbsprung
        unter einem noch gleitenden Knopf sähe zerrissen aus.
        """
        if not self.enabled:
            return Theme.CONTROL_SURFACE
        off = Theme.CONTROL_HOVER if self.has_state(State.HOVER) else Theme.CONTROL_SURFACE
        return blend(off, Theme.ACCENT_TINT, self._progress)

    def _draw(self) -> None:
        self.delete("all")
        width, height = px(self.WIDTH), px(self.HEIGHT)
        ring_w, ring_gap = px(ControlStyle.RING_WIDTH), px(ControlStyle.RING_GAP)
        pad = ring_w + ring_gap

        # Die ausgeschaltete Spur hebt sich mit 1,16:1 kaum vom Zeilengrund ab —
        # ohne Umriss wäre nicht zu sehen, dass dort überhaupt ein Bedienelement
        # sitzt. Eingeschaltet trägt die Akzentfläche selbst mindestens 3:1,
        # dann verschwindet der Umriss in ihr.
        track = surface(
            width + 2 * pad, height + 2 * pad, Radius.pill(height),
            self._track_colour(), squircle=False,
            border=blend(Theme.CONTROL_OUTLINE, Theme.ACCENT_TINT, self._progress),
            border_w=max(1, px(1)),
            ring=self.ring(), ring_width=ring_w, ring_gap=ring_gap,
        )
        self._track_image = track
        self.create_image(0, 0, anchor="nw", image=track)

        inset = px(self.INSET)
        knob_size = height - 2 * inset
        knob = surface(
            knob_size, knob_size, Radius.pill(knob_size),
            Theme.KNOB if self.enabled else Theme.TEXT_TERTIARY,
            squircle=False, shadow=(1.2, 0.6, 0.35),
        )
        self._knob_image = knob
        left = pad + inset
        right = pad + width - knob_size - inset
        x = int(round(left + (right - left) * self._progress))
        self.create_image(x, pad + inset, anchor="nw", image=knob)


class SignalMeter(tk.Canvas):
    """Pegel als durchgehender Balken — dauerhaft sichtbar, im Ruhezustand leer.

    Früher 22 einzelne Segmente; im Ruhezustand sah das aus wie eine
    gestrichelte Platzhalterlinie. Jetzt ist es eine ruhige Spur, in die der
    Pegel von links hineinläuft. Die Farbe folgt der Aussteuerung: bis 60 %
    Bereit-Grün, darüber Warnung, ab 82 % Fehler.
    """

    HEIGHT = 6             # logisch

    def __init__(self, master: tk.Misc, *, bg_under: str,
                 height: int = HEIGHT) -> None:
        self._bg_under = bg_under
        self._logical_height = height
        self._level = 0.0
        self._last_width = 0
        self._images: list = []
        super().__init__(master, height=px(height), bg=bg_under, bd=0,
                         highlightthickness=0)
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, level))
        if abs(level - self._level) > 0.004:
            self._level = level
            self._draw()

    @property
    def level(self) -> float:
        return self._level

    @property
    def filled_width(self) -> int:
        """Breite des gefüllten Teils in Pixeln — 0 im Ruhezustand."""
        if self._last_width <= 1 or self._level <= 0.0:
            return 0
        height = px(self._logical_height)
        return max(height, int(round(self._level * self._last_width)))

    def _on_configure(self, event: tk.Event) -> None:
        if event.width != self._last_width:
            self._last_width = event.width
            self._draw()

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self.configure(height=px(self._logical_height))
            self._draw()
        except tk.TclError:      # pragma: no cover
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)

    def _on_theme_change(self) -> None:
        self._bg_under = Theme.remap(self._bg_under)
        try:
            self.configure(bg=self._bg_under)
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            return
        self._draw()

    @staticmethod
    def level_colour(level: float) -> str:
        if level > 0.82:
            return Theme.ERROR
        if level > 0.60:
            return Theme.WARNING
        return Theme.SUCCESS

    def _draw(self) -> None:
        self.delete("all")
        self._images = []
        if self._last_width <= 1:
            return
        height = px(self._logical_height)
        radius = Radius.pill(height)
        track = surface(self._last_width, height, radius, Theme.CONTROL_SURFACE,
                        squircle=False)
        self._images.append(track)
        self.create_image(0, 0, anchor="nw", image=track)
        filled = self.filled_width
        if filled:
            # Zwei runde Kappen aus dem Bildspeicher plus ein Rechteck. Ein
            # eigenes Bild je Pegelbreite legte bei 20 Messungen pro Sekunde
            # laufend neue Eintraege in den Speicher und verdraengte die
            # wiederverwendbaren Flaechen aller anderen Controls.
            colour = self.level_colour(self._level)
            cap = surface(height, height, radius, colour, squircle=False)
            self._images.append(cap)
            half = height / 2
            if filled > height:
                self.create_rectangle(half, 0, filled - half, height,
                                      fill=colour, outline="")
            self.create_image(0, 0, anchor="nw", image=cap)
            self.create_image(filled - height, 0, anchor="nw", image=cap)


# ---------------------------------------------------------------- Tasten-Chips

#: Tastennamen, wie sie auf einer deutschen Tastatur stehen.
KEY_NAMES = {
    "ctrl": "Strg", "control": "Strg", "strg": "Strg",
    "shift": "Umschalt", "alt": "Alt", "altgr": "AltGr",
    "win": "Win", "windows": "Win", "cmd": "Win", "super": "Win",
    "space": "Leertaste", "enter": "Eingabe", "return": "Eingabe",
    "esc": "Esc", "escape": "Esc", "tab": "Tab",
    "backspace": "Rücktaste", "delete": "Entf", "del": "Entf",
    "insert": "Einfg", "home": "Pos1", "end": "Ende",
    "page up": "Bild auf", "page down": "Bild ab",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
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
        super().__init__(master, height=px(Height.KEYCAP), width=1, bg=bg_under,
                         bd=0, highlightthickness=0)
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
        try:
            self._draw()
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)

    def _on_theme_change(self) -> None:
        self._bg_under = Theme.remap(self._bg_under)
        try:
            self.configure(bg=self._bg_under)
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            return
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        self._images = []
        tk_font = font("shortcut")
        height = px(Height.KEYCAP)
        self.configure(width=max(1, self.natural_width()), height=height)
        if not self._labels:
            self.create_text(0, height // 2, anchor="w", text=self.EMPTY,
                             font=tk_font, fill=Theme.TEXT_TERTIARY)
            return
        x = 0
        for label in self._labels:
            width = self._chip_width(label, tk_font)
            chip = surface(width, height, px(Radius.KEYCAP), Theme.CONTROL_SURFACE)
            self._images.append(chip)
            self.create_image(x, 0, anchor="nw", image=chip)
            self.create_text(x + width // 2, height // 2, anchor="center",
                             text=label, font=tk_font, fill=Theme.TEXT_PRIMARY)
            x += width + px(Space.KEY_GAP)
