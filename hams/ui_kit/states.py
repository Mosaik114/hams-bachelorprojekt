"""Zustaende der Bedienelemente und die Tastaturfuehrung (Fokus)."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from hams.ui_kit.theme import Theme

# ---------------------------------------------------------------- Zustände


class State:
    """Zustände, die jedes Control kennt — und ihre Rangfolge.

    Zwei Achsen, bewusst getrennt:

    * **Fläche** — genau ein Zustand bestimmt Füllung und Textfarbe. Die
      Rangfolge in `SURFACE_ORDER` entscheidet, welcher das ist.
    * **Fokus** — liegt als Ring *über* der Fläche und übersteuert nichts.
      Ein fokussierter Schalter, über dem die Maus steht, zeigt beides.

    Damit gilt: `disabled` sieht nie aus wie `hover`, `pressed` übersteuert
    `hover`, und der Fokus bleibt in jedem Flächenzustand sichtbar.
    """

    DEFAULT = "default"
    HOVER = "hover"
    PRESSED = "pressed"
    FOCUSED = "focused"
    SELECTED = "selected"
    DISABLED = "disabled"
    LOADING = "loading"
    ERROR = "error"

    ALL = (DEFAULT, HOVER, PRESSED, FOCUSED, SELECTED, DISABLED, LOADING, ERROR)

    SURFACE_ORDER = (DISABLED, ERROR, LOADING, PRESSED, HOVER, SELECTED, DEFAULT)
    """Höchster Rang zuerst. `FOCUSED` steht bewusst nicht darin."""

    @classmethod
    def surface_state(cls, active) -> str:
        """Der eine Zustand, der die Fläche bestimmt."""
        for state in cls.SURFACE_ORDER:
            if state in active:
                return state
        return cls.DEFAULT

    @classmethod
    def shows_focus_ring(cls, active) -> bool:
        """Fokus wird gezeigt — ausser das Control ist gar nicht bedienbar."""
        return cls.FOCUSED in active and cls.DISABLED not in active

    @classmethod
    def is_interactive(cls, active) -> bool:
        return cls.DISABLED not in active and cls.LOADING not in active


class ControlStyle:
    """Farbauflösung für alle Controls an genau einer Stelle.

    Kein Control erfindet eigene Zustandsfarben; es nennt seine Rolle und seine
    aktiven Zustände und bekommt Füllung und Textfarbe zurück.
    """

    #: Rolle -> Zustand -> (Fuellungs-Token, Text-Token). Bewusst *Namen*, keine
    #: Farbwerte: eine Tabelle aus Werten waere beim Import eingefroren und
    #: zeigte nach einem Moduswechsel weiter die alte Palette. Aufgeloest wird
    #: erst in `resolve()`.
    _ROLES: dict = {
        "primary": {
            State.DEFAULT: ("ACCENT_SURFACE", "ON_ACCENT"),
            State.HOVER: ("ACCENT_HOVER", "ON_ACCENT"),
            State.PRESSED: ("ACCENT_PRESSED", "ON_ACCENT"),
            State.SELECTED: ("ACCENT_SURFACE", "ON_ACCENT"),
            State.LOADING: ("CONTROL_SURFACE", "TEXT_SECONDARY"),
            State.DISABLED: ("CONTROL_SURFACE", "TEXT_DISABLED"),
            State.ERROR: ("ERROR_SURFACE", "ON_FILL"),
        },
        "secondary": {
            State.DEFAULT: ("CONTROL_SURFACE", "TEXT_PRIMARY"),
            State.HOVER: ("CONTROL_HOVER", "TEXT_PRIMARY"),
            State.PRESSED: ("CONTROL_PRESSED", "TEXT_PRIMARY"),
            State.SELECTED: ("CONTROL_HOVER", "TEXT_PRIMARY"),
            State.LOADING: ("CONTROL_SURFACE", "TEXT_SECONDARY"),
            State.DISABLED: ("CONTROL_SURFACE", "TEXT_DISABLED"),
            # Gefuellt statt nur rot beschriftet: `ERROR` auf `CONTROL_SURFACE`
            # ergibt dunkel 3,97:1 und hell 3,94:1 und faellt damit als Text
            # durch. Dieselbe Loesung wie bei den anderen Rollen — die Flaeche
            # traegt den Zustand, `ON_FILL` den Text (5,28:1 / 6,31:1).
            State.ERROR: ("ERROR_SURFACE", "ON_FILL"),
        },
        "plain": {  # Icon-Buttons und Zeilen: ohne Flaeche im Ruhezustand
            State.DEFAULT: (None, "TEXT_SECONDARY"),
            State.HOVER: ("CONTROL_SURFACE", "TEXT_PRIMARY"),
            State.PRESSED: ("CONTROL_PRESSED", "TEXT_PRIMARY"),
            State.SELECTED: ("CONTROL_SURFACE", "TEXT_PRIMARY"),
            State.LOADING: (None, "TEXT_TERTIARY"),
            State.DISABLED: (None, "TEXT_DISABLED"),
            State.ERROR: (None, "ERROR"),
        },
        "recording": {
            State.DEFAULT: ("RECORDING_SURFACE", "ON_FILL"),
            State.HOVER: ("RECORDING_HOVER", "ON_FILL"),
            State.PRESSED: ("RECORDING_PRESSED", "ON_FILL"),
            State.SELECTED: ("RECORDING_SURFACE", "ON_FILL"),
            State.LOADING: ("CONTROL_SURFACE", "TEXT_SECONDARY"),
            State.DISABLED: ("CONTROL_SURFACE", "TEXT_DISABLED"),
            State.ERROR: ("ERROR_SURFACE", "ON_FILL"),
        },
        "ai": {
            State.DEFAULT: ("AI_SURFACE", "ON_FILL"),
            State.HOVER: ("AI_HOVER", "ON_FILL"),
            State.PRESSED: ("AI_PRESSED", "ON_FILL"),
            State.SELECTED: ("AI_SURFACE", "ON_FILL"),
            State.LOADING: ("CONTROL_SURFACE", "TEXT_SECONDARY"),
            State.DISABLED: ("CONTROL_SURFACE", "TEXT_DISABLED"),
            State.ERROR: ("ERROR_SURFACE", "ON_FILL"),
        },
    }

    #: Die Seite unter der Fläche (Tusche und Aquarell): je Rolle der dunklere Ton ihrer Füllung.
    _SIDES: dict = {
        "primary": "ACCENT_SIDE",
        "secondary": "CONTROL_SIDE",
        "recording": "RECORDING_SIDE",
        "ai": "AI_SIDE",
        "plain": "CONTROL_SIDE",
    }

    #: Der Tastaturfokus färbt den Tuscherand der Fläche (Token `FOCUS`); ein Ring drumherum entfällt.
    FOCUS_TOKEN = "FOCUS"  # noqa: S105 – Name einer Farbe, kein Passwort

    @classmethod
    def roles(cls) -> list[str]:
        return list(cls._ROLES)

    @classmethod
    def resolve(cls, role: str, active) -> tuple[str | None, str]:
        """(Füllung, Textfarbe) für eine Rolle im aktuellen Zustand.

        Füllung `None` heisst: keine eigene Fläche, der Untergrund bleibt stehen.
        """
        table = cls._ROLES.get(role)
        if table is None:
            raise KeyError(f"Unbekannte Control-Rolle: {role!r}")
        fill, text = table[State.surface_state(active)]
        return (None if fill is None else getattr(Theme, fill)), getattr(Theme, text)

    @classmethod
    def side(cls, role: str, active) -> str:
        """Farbe der Seite unter der Fläche; folgt dem Zustand wie die Füllung."""
        state = State.surface_state(active)
        if state == State.ERROR:
            return Theme.ERROR_SIDE
        if state in (State.LOADING, State.DISABLED):
            return Theme.CONTROL_SIDE
        return getattr(Theme, cls._SIDES.get(role, "CONTROL_SIDE"))

    @classmethod
    def focus_colour(cls) -> str:
        """Farbe des Tuscherands bei sichtbarem Tastaturfokus."""
        return getattr(Theme, cls.FOCUS_TOKEN)

    @classmethod
    def focus_for(cls, active) -> str | None:
        return cls.focus_colour() if State.shows_focus_ring(active) else None

    @classmethod
    def surface_spec(
        cls,
        role: str,
        active,
        width: int,
        height: int,
        radius: int,
        *,
        under: str,
        squircle: bool = True,
        depth: int | None = None,
    ):
        """Die Fläche eines Controls in Rolle und Zustand — die *eine* Stelle, die das entscheidet.

        `height` ist die Höhe der Fläche selbst; das Bild ist um die Seite (`depth`) höher. Ohne eigene
        Füllung (Rolle `plain` in Ruhe) bleibt nur der Untergrund, ohne Tusche und Seite.
        """
        from hams.ui_kit.scale import px
        from hams.ui_kit.surfaces import SurfaceSpec
        from hams.ui_kit.theme import Depth, ink_width

        fill, _text = cls.resolve(role, active)
        lift = px(Depth.CONTROL) if depth is None else depth
        if fill is None:
            return SurfaceSpec(width, height + lift, radius, under, squircle=squircle, focus=cls.focus_for(active))
        return SurfaceSpec(
            width,
            height + lift,
            radius,
            fill,
            ink=Theme.INK,
            ink_width=ink_width(),
            side=cls.side(role, active),
            depth=lift,
            pressed=State.PRESSED in set(active),
            focus=cls.focus_for(active),
            squircle=squircle,
        )


class StateMixin:
    """Zustandsverwaltung für ein Control.

    Das Widget meldet Zustände an und ab; bei jeder Änderung wird einmal neu
    gezeichnet. Die konkrete Darstellung liefert `ControlStyle`.
    """

    def init_states(self, role: str = "secondary") -> None:
        self._role = role
        self._states: set[str] = set()

    @property
    def states(self) -> set[str]:
        return set(self._states)

    def set_state(self, state: str, on: bool = True) -> None:
        if state not in State.ALL:
            raise KeyError(f"Unbekannter Zustand: {state!r}")
        before = set(self._states)
        self._states.add(state) if on else self._states.discard(state)
        if state == State.DISABLED and on:
            # Ein deaktiviertes Control darf keinen Hover- oder Druckzustand
            # behalten — sonst sieht es bedienbar aus.
            self._states -= {State.HOVER, State.PRESSED, State.FOCUSED}
        if self._states != before:
            self.on_state_change()

    def has_state(self, state: str) -> bool:
        return state in self._states

    @property
    def enabled(self) -> bool:
        return State.DISABLED not in self._states

    def colours(self) -> tuple[str | None, str]:
        return ControlStyle.resolve(self._role, self._states)

    def focus_colour(self) -> str | None:
        """Farbe des Tuscherands, solange der Fokus sichtbar ist, sonst None."""
        return ControlStyle.focus_for(self._states)

    def surface_spec(self, width: int, height: int, radius: int, **options):
        return ControlStyle.surface_spec(self._role, self._states, width, height, radius, **options)

    def on_state_change(self) -> None:  # pragma: no cover - überschrieben
        """Wird bei jeder Zustandsänderung gerufen; Controls zeichnen hier neu."""


class Pressable:
    """Druecken wie bei einer Windows-Schaltflaeche — fuer jedes Control dasselbe Verhalten.

    Der Druck gilt bis zum Loslassen. Rutscht der Zeiger dabei hinaus, hebt sich die Flaeche; kommt er zurueck,
    sinkt sie wieder ein. Ausgeloest wird beim Loslassen ueber dem Control. Frueher vergass ein Control den
    Druck beim Hinausrutschen, und ein Zurueckgleiten loeste nicht mehr aus.

    Erwartet `StateMixin` und `focus_set`; `on_activate` ist, was ein vollstaendiger Klick tut.
    """

    def init_press(self, on_activate: Callable[[], None] | None) -> None:
        self._press_action = on_activate
        self._armed = False

    def press_enter(self, _event=None) -> None:
        if self.enabled:
            self.set_state(State.HOVER, True)
            if self._armed:
                self.set_state(State.PRESSED, True)

    def press_leave(self, _event=None) -> None:
        self.set_state(State.HOVER, False)
        self.set_state(State.PRESSED, False)

    def press_down(self, _event=None) -> None:
        if State.is_interactive(self._states):
            self._armed = True
            self.set_state(State.PRESSED, True)
            self.focus_set()

    def press_up(self, event=None) -> None:
        armed, self._armed = self._armed, False
        self.set_state(State.PRESSED, False)
        complete = armed and self._pointer_inside(event) and State.is_interactive(self._states)
        if complete and self._press_action is not None:
            self._press_action()

    def _pointer_inside(self, event) -> bool:
        if event is None or not hasattr(event, "x"):
            return True
        try:
            return 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        except tk.TclError:  # pragma: no cover - widget going away
            return False


class InputModality:
    """Womit zuletzt bedient wurde: Tastatur oder Zeiger — fuer alle Fenster der Anwendung gemeinsam.

    Der Fokusrand ist nur fuer Tastaturbedienung da (wie `:focus-visible` im Browser). Frueher entschied jedes
    Control an *seinem* FocusIn, ob ein Klick vorausging. Tk schickt FocusIn aber auch ohne Klick: wenn das
    Fenster wieder aktiv wird, und wenn ein Popover den Fokus zurueckgibt. Dann stand der Rand nach einem
    Mausklick da. Jetzt zaehlt die letzte Eingabe: jede Taste schaltet auf Tastatur, jeder Mausklick auf Zeiger.
    """

    keyboard = True
    _installed: set = set()

    @classmethod
    def install(cls, widget: tk.Misc) -> None:
        """Bindet einmal je Tcl-Interpreter an alle Widgets (idempotent)."""
        interp = str(widget.tk)
        if interp in cls._installed:
            return
        cls._installed.add(interp)
        # Tab hat in Tk eigene, genauere Bindungen am Tag "all" (Fokus weiterreichen); eine allgemeine
        # <KeyPress>-Bindung liefe dort nicht. Deshalb auch Tab und Umschalt+Tab ausdruecklich, angehaengt.
        for sequence in ("<KeyPress>", "<Key-Tab>", "<Shift-Key-Tab>", "<<NextWindow>>", "<<PrevWindow>>"):
            widget.bind_all(sequence, cls._on_key, add="+")
        widget.bind_all("<ButtonPress>", cls._on_pointer, add="+")

    @classmethod
    def _on_key(cls, _event=None) -> None:
        cls.keyboard = True

    @classmethod
    def _on_pointer(cls, event=None) -> None:
        cls.keyboard = False
        # Ein Klick neben das fokussierte Control (auf freie Flaeche) nimmt ihm den sichtbaren Rand.
        try:
            focused = event.widget.focus_get() if event is not None else None
        except (tk.TclError, KeyError, AttributeError):
            focused = None
        if isinstance(focused, FocusMixin) and focused.has_state(State.FOCUSED):
            focused.set_state(State.FOCUSED, False)


class FocusMixin:
    """Tastaturfokus für ein Control.

    Bindet ausschliesslich am Widget selbst — keine globale Tastaturbehandlung,
    die anderen Controls oder den globalen Hotkeys in die Quere käme.
    """

    def init_focus(self, activate: Callable[[], None] | None = None) -> None:
        self._activate = activate
        InputModality.install(self)
        self.configure(takefocus=1)
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<Return>", self._on_activate_key, add="+")
        self.bind("<space>", self._on_activate_key, add="+")

    def _on_focus_in(self, _event=None) -> None:
        """Der Ring erscheint nur, wenn der Fokus von der Tastatur kommt.

        Wer klickt, weiss bereits, wo er ist — dort wirkt der Ring wie eine
        dicke Markierung um das eben Angefasste. Wer sich mit Tabulator
        bewegt, braucht ihn dagegen unbedingt: er ist die einzige Anzeige, wo
        man gerade steht. Dieselbe Unterscheidung, die Browser als
        `:focus-visible` treffen.

        Der Fokus selbst wird in beiden Faellen gesetzt; nur die *Anzeige*
        unterscheidet sich. Leertaste und Eingabetaste wirken also auch nach
        einem Klick. Ob zuletzt Tastatur oder Zeiger bedient hat, weiss
        `InputModality` fuer alle Fenster — ein FocusIn beim Wiederaktivieren
        des Fensters zeigt den Rand deshalb nur nach Tastaturbedienung.
        """
        if InputModality.keyboard:
            self.set_state(State.FOCUSED, True)

    def _on_focus_out(self, _event=None) -> None:
        self.set_state(State.FOCUSED, False)

    def _on_activate_key(self, _event=None) -> str:
        if State.is_interactive(self._states) and self._activate is not None:
            self._activate()
        return "break"  # verhindert, dass Tk die Taste weiterreicht


def return_focus_to(widget: tk.Misc | None) -> None:
    """Gibt den Fokus an das Element zurück, das ein Popup geöffnet hat.

    Regel für alle künftigen Popover: wer öffnet, bekommt den Fokus zurück.
    """
    if widget is None:
        return
    try:
        if widget.winfo_exists():
            widget.focus_set()
    except tk.TclError:  # pragma: no cover - Widget bereits zerstört
        pass
