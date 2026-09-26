"""DPI-Skalierung und Schriftrollen; beides haengt am selben Faktor."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable


from wisper.ui_kit.caches import _img_cache


# ---------------------------------------------------------------- DPI / Skalierung


def enable_dpi_awareness() -> str:
    """Meldet den Prozess als DPI-aware an. Muss VOR dem ersten `tk.Tk()` laufen.

    Ohne diesen Aufruf rendert die Anwendung in 96 dpi und Windows streckt das
    fertige Bild — bei 125–200 % sichtbar unscharf. Es werden drei Stufen
    versucht, damit auch ältere Windows-Versionen etwas bekommen.

    Returns:
        Kennung der erreichten Stufe für das Log.
    """
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError, ValueError):
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE == 2 (Windows 8.1+)
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "keine"


def window_dpi(widget: tk.Misc) -> int:
    """DPI des Monitors, auf dem das Fenster gerade liegt (Fallback: System)."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(widget.winfo_id()) or widget.winfo_id()
        dpi = int(user32.GetDpiForWindow(hwnd))
        if dpi > 0:
            return dpi
    except (AttributeError, OSError, tk.TclError):
        pass
    try:
        return int(ctypes.windll.user32.GetDpiForSystem())
    except (AttributeError, OSError):
        return Scale.BASE_DPI


class Scale:
    """Zentraler Skalierungsfaktor für alle Pixelmaße.

    Alle Maße im UI-Kit sind in *logischen* Einheiten (96 dpi) notiert und
    werden über `px()` in physische Pixel umgerechnet.

    Schriftgrößen bleiben ausdrücklich in **Punkt** und laufen NICHT über
    `px()`: Tk rechnet Punkt über `tk scaling` selbst in Pixel um. Beides
    zusammen wäre doppelte Skalierung.
    """

    BASE_DPI = 96
    dpi: int = BASE_DPI
    factor: float = 1.0
    _callbacks: list[Callable[[float], None]] = []

    @classmethod
    def register(cls, callback: Callable[[float], None]) -> None:
        """Meldet einen Rückruf an, der bei jedem DPI-Wechsel neu zeichnet."""
        cls._callbacks.append(callback)

    @classmethod
    def unregister(cls, callback: Callable[[float], None]) -> None:
        """Meldet einen Rückruf wieder ab.

        Controls tun das beim Zerstören — sonst hielte die Liste tote Widgets
        am Leben und würde bei jedem DPI-Wechsel länger.
        """
        try:
            cls._callbacks.remove(callback)
        except ValueError:
            pass

    @classmethod
    def apply(cls, root: tk.Misc, dpi: int) -> bool:
        """Setzt den Faktor neu. Gibt True zurück, wenn sich etwas geändert hat."""
        dpi = max(48, int(dpi))
        if dpi == cls.dpi and _FONTS:
            return False
        cls.dpi = dpi
        cls.factor = dpi / cls.BASE_DPI
        # Unter X11 rechnet Tk Pixelgrößen über `tk scaling` in Punkt um, Xft
        # aber mit dem festen Server-DPI zurück; ein geändertes scaling hebt die
        # neue Pixelgröße dort wieder auf. Die Schriften sind in Pixeln gesetzt
        # und brauchen es nicht.
        if sys.platform == "win32":
            try:
                root.tk.call("tk", "scaling", dpi / 72.0)
            except tk.TclError:
                pass
        _img_cache.clear()          # Bilder gelten immer nur für einen Faktor
        init_fonts(root, force=True)
        for callback in list(cls._callbacks):
            try:
                callback(cls.factor)
            except Exception:       # ein defekter Rückruf darf nichts blockieren
                pass
        return True


def keep_logical_width(widget: tk.Misc, logical: int) -> None:
    """Setzt eine Breite in logischen Pixeln — und hält sie bei DPI-Wechsel nach.

    `configure(width=px(...))` einmal beim Aufbau genügt nicht: wechselt das
    Fenster auf einen anders skalierten Monitor, zeichnen die Controls zwar neu,
    behalten aber ihre alte Pixelbreite. Gemessen bei 96 → 144 dpi: das
    Auswahlfeld blieb bei 170 px statt auf 255 zu wachsen.

    Der Rückruf meldet sich beim Zerstören wieder ab, damit die Liste nicht mit
    toten Widgets wächst.
    """
    def apply(_factor: float = 0.0) -> None:
        try:
            widget.configure(width=px(logical))
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            Scale.unregister(apply)

    def forget(event: tk.Event) -> None:
        if event.widget is widget:
            Scale.unregister(apply)

    apply()
    Scale.register(apply)
    widget.bind("<Destroy>", forget, add="+")


def px(value: float) -> int:
    """Logisches Maß in physische Pixel. 0 bleibt 0, alles andere mindestens 1."""
    if not value:
        return 0
    scaled = int(round(value * Scale.factor))
    return scaled if scaled else (1 if value > 0 else -1)


def init_scale(root: tk.Misc) -> float:
    """Ermittelt das DPI des Fensters und setzt Faktor, `tk scaling` und Fonts."""
    Scale.apply(root, window_dpi(root))
    return Scale.factor


_FONTS: dict[str, tkfont.Font] = {}

#: Serifenschriften für Überschriften, in dieser Reihenfolge gesucht. Georgia
#: liegt jedem Windows bei; die übrigen sind nur Rückfall.
SERIF_FAMILIES = ("Georgia", "Cambria", "Times New Roman")

#: Halbfetter Schnitt für Schaltflächen. Tk kennt nur „normal“ und „bold“;
#: Windows legt die Zwischenschnitte als eigene Familien ab.
SEMIBOLD_FAMILIES = ("Segoe UI Variable Text Semibold", "Segoe UI Semibold")

# Rolle -> (Familienschlüssel, Punktgröße, Schnitt). Die Schlüssel werden beim
# Initialisieren gegen die tatsächlich installierten Familien aufgelöst.
# Mosaik-Design: vier Größen (16, 13, 12, 11 pt). Überschriften tragen die
# Serifenschrift, Schaltflächen den halbfetten Schnitt; fett ist nichts.
_FONT_ROLES: dict[str, tuple] = {
    "window_title": ("serif", 16, "normal"),  # Fenstertitel — Serifenschrift
    "section": ("serif", 13, "normal"),       # Abschnittsueberschrift in den Einstellungen
    "row_title": ("text", 12, "normal"),      # Titel einer Einstellungszeile
    "description": ("small", 11, "normal"),   # Hilfstext unter einem Titel
    "status": ("text", 12, "normal"),         # die eine Statuszeile
    "button": ("semibold", 12, "normal"),     # Beschriftung von Schaltflächen
    "shortcut": ("small", 11, "normal"),      # Tasten-Chips
    "meta": ("small", 11, "normal"),          # Zusatzangaben im Popover
    "icon": ("text", 13, "normal"),           # Glyph in Icon-Buttons
}


def points_to_px(points: float) -> int:
    """Punkt in physische Pixel — 72 pt entsprechen einem Zoll.

    Die Rollen sind in Punkt notiert, weil das die Entwurfsgröße ist. Die
    Umrechnung passiert genau hier und nirgends sonst, damit keine doppelte
    Skalierung entstehen kann.
    """
    return max(1, int(round(points * Scale.dpi / 72.0)))


def init_fonts(root: tk.Misc, force: bool = False) -> dict[str, tkfont.Font]:
    """Legt die Schriftrollen als *benannte* Fonts an. Idempotent, außer `force`.

    Benannt deshalb, weil Tk Fonts anhand ihrer Spezifikation zwischenspeichert:
    Ein als Tupel übergebener 10-Punkt-Font behält seine einmal berechnete
    Pixelgröße, auch wenn sich die Skalierung später ändert. Benannte Fonts
    lassen sich dagegen nachkonfigurieren — nur so wächst der Text beim Wechsel
    auf einen Monitor mit anderer Skalierung tatsächlich mit.

    Die Größe wird als negativer Wert gesetzt; das ist in Tk die Angabe in
    Pixeln und macht die Umrechnung unabhängig von `tk scaling`.
    """
    families = set(tkfont.families(root))
    resolved = {
        "display": "Segoe UI Variable Display" if "Segoe UI Variable Display" in families else "Segoe UI",
        "text": "Segoe UI Variable Text" if "Segoe UI Variable Text" in families else "Segoe UI",
        "small": "Segoe UI Variable Small" if "Segoe UI Variable Small" in families else "Segoe UI",
        "semibold": next((f for f in SEMIBOLD_FAMILIES if f in families), "Segoe UI"),
        "mono": "Cascadia Mono" if "Cascadia Mono" in families else "Consolas",
        "serif": next((f for f in SERIF_FAMILIES if f in families), "Times New Roman"),
    }
    if sys.platform != "win32":
        # Segoe UI und Georgia gibt es nur unter Windows.
        from wisper.linux import FONT_SUBSTITUTES

        def pick(key: str) -> str:
            return next((f for f in FONT_SUBSTITUTES[key] if f in families), "TkDefaultFont")

        resolved.update(display=pick("text"), text=pick("text"), small=pick("text"),
                        semibold=pick("semibold"), mono=pick("mono"), serif=pick("serif"))
    if _FONTS and not force:
        return _FONTS

    for role, (family_key, points, weight) in _FONT_ROLES.items():
        family = resolved[family_key]
        size = -points_to_px(points)
        existing = _FONTS.get(role)
        if existing is not None:
            existing.configure(family=family, size=size, weight=weight)
        else:
            _FONTS[role] = tkfont.Font(
                root=root,
                name=f"WisperFont_{role}",
                family=family,
                size=size,
                weight=weight,
                exists=False,
            )
    return _FONTS


def font(role: str) -> "tkfont.Font | tuple":
    """Schriftrolle. Vor `init_fonts()` ein neutrales Tupel als Rückfall."""
    resolved = _FONTS.get(role)
    if resolved is not None:
        return resolved
    return _FONTS.get("row_title", ("Segoe UI", 10))
