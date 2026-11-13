"""Helpers the UI tests share; not part of the app."""

import tkinter as tk


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
