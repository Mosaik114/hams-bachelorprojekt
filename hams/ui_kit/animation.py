"""Animationen: Ueberblenden, Takt und Ruecksicht auf die Systemeinstellung."""

from __future__ import annotations

import contextlib
import ctypes
import time
import tkinter as tk
from collections.abc import Callable

from hams.ui_kit.surfaces import _hex_to_rgb

# ---------------------------------------------------------------- Animation


SPI_GETCLIENTAREAANIMATION = 0x1042
"""Windows-Systemeinstellung „Animationen in Windows anzeigen“."""

#: Ein Bild alle 16 ms — etwa 60 pro Sekunde. Feiner lohnt sich in Tk nicht.
FRAME_MS = 16


def system_animations_enabled() -> bool:
    """Fragt die Windows-Einstellung ab. Im Zweifel: an.

    Wer Animationen systemweit abschaltet, will sie auch hier nicht. Eine
    eigene Einstellung dafür wäre eine zweite Wahrheit über dieselbe Frage.
    """
    try:
        value = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0)
        return bool(ok) and bool(value.value)
    except Exception:  # pragma: no cover - kein Windows oder API fehlt
        return True


def ease_out(fraction: float) -> float:
    """Kubisch auslaufend: schneller Beginn, ruhiges Ende."""
    clamped = 0.0 if fraction < 0.0 else (1.0 if fraction > 1.0 else fraction)
    return 1.0 - (1.0 - clamped) ** 3


def blend(start: str, end: str, fraction: float) -> str:
    """Mischt zwei Hex-Farben. 0 ergibt `start`, 1 ergibt `end`.

    Grossbuchstaben wie in den Tokens: so bleibt eine Mischung mit dem Faktor 0
    oder 1 buchstabengleich mit der Farbe, aus der sie stammt.
    """
    clamped = 0.0 if fraction < 0.0 else (1.0 if fraction > 1.0 else fraction)
    a, b = _hex_to_rgb(start), _hex_to_rgb(end)
    mixed = tuple(int(round(x + (y - x) * clamped)) for x, y in zip(a, b, strict=False))
    return "#" + "".join(f"{v:02X}" for v in mixed)


class Animator:
    """Buchführung über laufende Tk-Animationen.

    Tk kennt nur `after()`. Ohne diese Verwaltung bleiben beim Zerstören eines
    Fensters Zeitgeber zurück (und melden sich als TclError zurück), und zwei
    schnell aufeinanderfolgende Zustandswechsel animieren gegeneinander statt
    nacheinander. Ein Widget hat je Schlüssel höchstens eine Animation; eine
    neue löst die alte an deren aktueller Stelle ab.

    Bewusst klein: kein Zeitstrahl, keine Verkettung, keine Kurvenbibliothek.
    """

    #: Wird beim Start aus der Systemeinstellung gesetzt.
    enabled: bool = True

    _jobs: dict = {}

    @classmethod
    def run(
        cls,
        widget: tk.Misc,
        key: str,
        duration_ms: int,
        step: Callable[[float], None],
        done: Callable[[], None] | None = None,
    ) -> bool:
        """Ruft `step(t)` mit t von 0 bis 1 (bereits geglättet).

        Gibt True zurück, wenn tatsächlich animiert wird. Sind Animationen
        abgeschaltet, wird `step(1.0)` sofort ausgeführt — der Zielzustand
        stellt sich also in jedem Fall ein.
        """
        cls.cancel(widget, key)
        if not cls.enabled or duration_ms <= 0:
            step(1.0)
            if done is not None:
                done()
            return False

        cls._watch(widget)
        token = (id(widget), key)
        start = time.monotonic()

        def tick() -> None:
            elapsed = (time.monotonic() - start) * 1000.0
            fraction = elapsed / duration_ms
            last = fraction >= 1.0
            try:
                step(ease_out(fraction))
            except tk.TclError:  # Widget verschwand mitten im Lauf
                cls._jobs.pop(token, None)
                return
            if last:
                cls._jobs.pop(token, None)
                if done is not None:
                    done()
                return
            try:
                cls._jobs[token] = widget.after(FRAME_MS, tick)
            except tk.TclError:  # pragma: no cover - Fenster schon weg
                cls._jobs.pop(token, None)

        tick()
        return token in cls._jobs

    @classmethod
    def cancel(cls, widget: tk.Misc, key: str) -> None:
        job = cls._jobs.pop((id(widget), key), None)
        if job is None:
            return
        with contextlib.suppress(Exception):  # Widget bereits zerstört
            widget.after_cancel(job)

    @classmethod
    def cancel_all(cls, widget: tk.Misc) -> None:
        """Beim Zerstören: alles zu diesem Widget abräumen."""
        target = id(widget)
        for token in [t for t in cls._jobs if t[0] == target]:
            job = cls._jobs.pop(token, None)
            with contextlib.suppress(Exception):
                widget.after_cancel(job)

    @classmethod
    def pending(cls, widget: tk.Misc | None = None) -> int:
        """Offene Animationsjobs — für Tests und die Ruhelastprüfung."""
        if widget is None:
            return len(cls._jobs)
        target = id(widget)
        return sum(1 for token in cls._jobs if token[0] == target)

    @classmethod
    def _watch(cls, widget: tk.Misc) -> None:
        """Sorgt dafür, dass ein zerstörtes Widget seine Zeitgeber mitnimmt."""
        if getattr(widget, "_anim_watched", False):
            return

        def on_destroy(event, target=widget) -> None:
            if event.widget is target:
                cls.cancel_all(target)

        try:
            widget.bind("<Destroy>", on_destroy, add="+")
        except tk.TclError:  # pragma: no cover
            return
        widget._anim_watched = True


def init_animations() -> bool:
    """Übernimmt die Systemeinstellung. Gibt zurück, ob animiert wird."""
    Animator.enabled = system_animations_enabled()
    return Animator.enabled
