"""Zustaende der Bedienelemente und die Tastaturfuehrung (Fokus)."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from wisper.ui_kit.theme import Theme

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
            State.DISABLED: ("CONTROL_SURFACE", "TEXT_TERTIARY"),
            State.ERROR: ("ERROR_SURFACE", "ON_FILL"),
        },
        "secondary": {
            State.DEFAULT: ("CONTROL_SURFACE", "TEXT_PRIMARY"),
            State.HOVER: ("CONTROL_HOVER", "TEXT_PRIMARY"),
            State.PRESSED: ("GROUP_SURFACE", "TEXT_PRIMARY"),
            State.SELECTED: ("CONTROL_HOVER", "TEXT_PRIMARY"),
            State.LOADING: ("CONTROL_SURFACE", "TEXT_SECONDARY"),
            State.DISABLED: ("CONTROL_SURFACE", "TEXT_TERTIARY"),
            # Gefuellt statt nur rot beschriftet: `ERROR` auf `CONTROL_SURFACE`
            # ergibt dunkel 3,97:1 und hell 3,94:1 und faellt damit als Text
            # durch. Dieselbe Loesung wie bei den anderen Rollen — die Flaeche
            # traegt den Zustand, `ON_FILL` den Text (5,28:1 / 6,31:1).
            State.ERROR: ("ERROR_SURFACE", "ON_FILL"),
        },
        "plain": {  # Icon-Buttons und Zeilen: ohne Flaeche im Ruhezustand
            State.DEFAULT: (None, "TEXT_SECONDARY"),
            State.HOVER: ("CONTROL_SURFACE", "TEXT_PRIMARY"),
            State.PRESSED: ("GROUP_SURFACE", "TEXT_PRIMARY"),
            State.SELECTED: ("CONTROL_SURFACE", "TEXT_PRIMARY"),
            State.LOADING: (None, "TEXT_TERTIARY"),
            State.DISABLED: (None, "TEXT_TERTIARY"),
            State.ERROR: (None, "ERROR"),
        },
        "recording": {
            State.DEFAULT: ("RECORDING_SURFACE", "ON_FILL"),
            State.HOVER: ("RECORDING_HOVER", "ON_FILL"),
            State.PRESSED: ("RECORDING_PRESSED", "ON_FILL"),
            State.SELECTED: ("RECORDING_SURFACE", "ON_FILL"),
            State.LOADING: ("CONTROL_SURFACE", "TEXT_SECONDARY"),
            State.DISABLED: ("CONTROL_SURFACE", "TEXT_TERTIARY"),
            State.ERROR: ("ERROR_SURFACE", "ON_FILL"),
        },
        "ai": {
            State.DEFAULT: ("AI_SURFACE", "ON_FILL"),
            State.HOVER: ("AI_HOVER", "ON_FILL"),
            State.PRESSED: ("AI_PRESSED", "ON_FILL"),
            State.SELECTED: ("AI_SURFACE", "ON_FILL"),
            State.LOADING: ("CONTROL_SURFACE", "TEXT_SECONDARY"),
            State.DISABLED: ("CONTROL_SURFACE", "TEXT_TERTIARY"),
            State.ERROR: ("ERROR_SURFACE", "ON_FILL"),
        },
    }

    #: Der Fokusring ist immer der Akzentton des aktiven Modus.
    FOCUS_RING_TOKEN = "ACCENT_TINT"
    RING_WIDTH = 2  # logisch
    RING_GAP = 2  # logisch

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
    def focus_ring(cls) -> str:
        """Ringfarbe des aktiven Modus."""
        return getattr(Theme, cls.FOCUS_RING_TOKEN)

    @classmethod
    def ring_colour(cls, active) -> str | None:
        return cls.focus_ring() if State.shows_focus_ring(active) else None


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

    def ring(self) -> str | None:
        return ControlStyle.ring_colour(self._states)

    def on_state_change(self) -> None:  # pragma: no cover - überschrieben
        """Wird bei jeder Zustandsänderung gerufen; Controls zeichnen hier neu."""


class FocusMixin:
    """Tastaturfokus für ein Control.

    Bindet ausschliesslich am Widget selbst — keine globale Tastaturbehandlung,
    die anderen Controls oder den globalen Hotkeys in die Quere käme.
    """

    def init_focus(self, activate: Callable[[], None] | None = None) -> None:
        self._activate = activate
        #: Kam der Fokus gerade vom Zeiger? Dann bleibt der Ring aus.
        self._focus_from_pointer = False
        self.configure(takefocus=1)
        # Muss *vor* dem Fokuswechsel greifen: die Controls rufen in ihrem
        # eigenen Press-Handler `focus_set()`, und `add="+"` haengt hier an
        # erster Stelle, weil `init_focus()` vor jenen Bindungen laeuft.
        self.bind("<ButtonPress-1>", self._note_pointer_focus, add="+")
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<Return>", self._on_activate_key, add="+")
        self.bind("<space>", self._on_activate_key, add="+")

    def _note_pointer_focus(self, _event=None) -> None:
        self._focus_from_pointer = True

    def _on_focus_in(self, _event=None) -> None:
        """Der Ring erscheint nur, wenn der Fokus von der Tastatur kommt.

        Wer klickt, weiss bereits, wo er ist — dort wirkt der Ring wie eine
        dicke Markierung um das eben Angefasste. Wer sich mit Tabulator
        bewegt, braucht ihn dagegen unbedingt: er ist die einzige Anzeige, wo
        man gerade steht. Dieselbe Unterscheidung, die Browser als
        `:focus-visible` treffen.

        Der Fokus selbst wird in beiden Faellen gesetzt; nur die *Anzeige*
        unterscheidet sich. Leertaste und Eingabetaste wirken also auch nach
        einem Klick.
        """
        vom_zeiger = self._focus_from_pointer
        self._focus_from_pointer = False
        if not vom_zeiger:
            self.set_state(State.FOCUSED, True)

    def _on_focus_out(self, _event=None) -> None:
        self.set_state(State.FOCUSED, False)

    def _on_activate_key(self, _event=None) -> str:
        if State.is_interactive(self._states) and self._activate is not None:
            self._activate()
        return "break"  # verhindert, dass Tk die Taste weiterreicht


def focus_chain(window: tk.Misc, limit: int = 60) -> list:
    """Die Tabulatorkette eines Fensters, in ihrer tatsächlichen Reihenfolge.

    Folgt `tk_focusNext` — also genau dem, was Tab beim Nutzer tut. Nicht
    fokussierbare, deaktivierte und nicht eingeblendete Widgets fallen dabei von
    selbst heraus, weil Tk sie überspringt.

    Die Reihenfolge ergibt sich aus der Stapelreihenfolge der Kinder, nicht aus
    der Packung. Wer sie ändern will, erzeugt die Widgets in der gewünschten
    Reihenfolge oder verschiebt sie mit `lift()`.
    """
    chain: list = []
    current = window
    for _ in range(limit):
        try:
            name = window.tk.call("tk_focusNext", current)
        except tk.TclError:  # pragma: no cover - Fenster verschwindet gerade
            break
        if not name:
            break
        widget = window.nametowidget(name)
        if widget in chain:
            break  # einmal herum
        chain.append(widget)
        current = widget
    return chain


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
