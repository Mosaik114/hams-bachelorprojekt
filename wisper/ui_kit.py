"""Wisper UI-Kit — ein kleines, modernes Design-System für tkinter.

Ziel: ein klares, ruhiges Apple-artiges Erscheinungsbild (dunkel, viel
Weißraum, weiche Rundungen, dezente Akzente). tkinter kann das nicht von Haus
aus, daher:

* Runde Ecken für Controls werden als anti-aliaste PIL-Bilder gerendert und auf
  einen *soliden* Hintergrund gelegt — so verschmelzen die Ecken sauber mit der
  Fläche darunter (kein Transparenz-Halo).
* Das Fenster selbst wird randlos (overrideredirect) und bekommt auf Windows
  über `SetWindowRgn` echte runde Ecken — ohne Transparenz-Tricks, daher crisp.

Alle Farben/Fonts sind hier zentral, damit Haupt- und Einstellungsfenster
identisch aussehen.

Barrierefreiheit — was dieses Kit leistet und was nicht
------------------------------------------------------

Geleistet wird die Bedienung ohne Maus: jedes Bedienelement liegt in der
Tabulatorkette (`focus_chain`), zeigt einen sichtbaren Fokusring, reagiert auf
Enter und Leertaste, gibt den Fokus nach einem Popover an seinen Auslöser
zurück und verlässt die Kette, sobald es deaktiviert oder ausgeblendet ist.
Zustände sind doppelt kodiert — Farbe *und* Form oder Text —, die Kontraste
sind gemessen (`tests/test_accessibility.py`), und Systemanimationen werden
respektiert.

Nicht geleistet wird Screenreader-Semantik. Gemessen am laufenden Programm:
die beiden Fenster sind Win32-Fenster der Klasse `TkTopLevel` mit 13 bzw. 90
Kindfenstern der Klassen `TkChild` und `Static`; **keines** davon trägt einen
Fenstertext, und eine UI-Automation-Suche über den Desktop findet das Fenster
unter seinem Titel gar nicht erst. Damit gibt es für eine Vorlesehilfe weder
Namen noch Rollen noch Zustände — Tk zeichnet seine Controls selbst und meldet
sie nirgends an.

Das ist eine Eigenschaft von tkinter, keine Nachlässigkeit dieses Kits, und mit
Bordmitteln nicht zu beheben: dafür bräuchte es einen echten UIA-Provider je
Control. Wenn das einmal gewünscht ist, ist es eine eigene
Architekturentscheidung und kein Feinschliff.
"""

from __future__ import annotations

import ctypes
import math
import time
import tkinter as tk
from collections import OrderedDict
from tkinter import font as tkfont
from typing import Callable, Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageTk


class Theme:
    """Semantische Farb-Tokens — in zwei vollstaendigen Darstellungsmodi.

    Es gibt genau zwei Paletten, `LIGHT` und `DARK`, mit **identischem
    Schluesselsatz**. Jedes Token traegt in beiden Modi dieselbe Bedeutung; nur
    der Farbwert unterscheidet sich. Umgeschaltet wird ueber `Theme.apply()`,
    das die Klassenattribute neu bindet — deshalb liest jede Zeichenroutine
    einfach `Theme.WINDOW_BG` und bekommt automatisch den Wert des gerade
    aktiven Modus.

    Drei Regeln halten beide Paletten zusammen:

    1. **Jede Semantikfarbe hat zwei Werte** — einen kraeftigen fuer Punkte,
       Balken und Icons und einen gedaempften fuer gefuellte Flaechen, auf denen
       Text steht. Ein einzelner Wert schafft beides nicht: Weiss auf dem
       kraeftigen Blau ergibt 3,65:1 und faellt durch, derselbe Ton als Punkt
       auf dem dunklen Fenstergrund dagegen 4,76:1.
    2. **Hover verdunkelt, es hellt nicht auf.** Im dunklen Modus gilt das fuer
       die gesaettigten Fuellungen — schon 1 % Aufhellung druecken den
       Textkontrast unter 4,5:1; im hellen Modus fuer alle Flaechen.
    3. **Text auf gefuellten Flaechen ist `ON_FILL`, nie `TEXT_PRIMARY`.** Im
       Dunkeln waeren beide fast gleich. Im Hellen waere `TEXT_PRIMARY` nahezu
       schwarz und stuende auf Rot oder Violett unlesbar da.

    Der helle Modus ist die Voreinstellung und keine Umkehrung des dunklen: die
    Flaechen sind eigenstaendig gestuft, und die Semantikfarben sind deutlich
    dunkler, weil Apples helle Systemtoene (`#30D158`, `#FF9F0A`) auf Weiss nur
    rund 1,7:1 bis 1,9:1 erreichen und damit selbst als Punkt durchfielen.

    Beide Paletten sind in `tests/test_tokens.py` getrennt gegen WCAG AA
    geprueft — was im Dunkeln reicht, reicht auf Weiss noch lange nicht.
    """

    LIGHT = "light"
    DARK = "dark"
    MODES = (LIGHT, DARK)

    #: Ohne Nutzereinstellung startet Hams hell. Bewusst *nicht* das
    #: Windows-Systemtheme: der Nutzer entscheidet selbst.
    DEFAULT_MODE = LIGHT

    _PALETTES: dict = {
        DARK: {
            # --- Flaechen: vier Ebenen mit kleinen Helligkeitsschritten ------
            "WINDOW_BG": "#1A1A1C",        # Fenstergrund
            "GROUP_SURFACE": "#232326",    # Einstellungsgruppen
            "CONTROL_SURFACE": "#2E2E32",  # Controls, deaktivierte Flaechen
            "CONTROL_HOVER": "#38383C",    # Control unter dem Zeiger
            "POPOVER_SURFACE": "#2A2A2E",  # schwebende Ebene
            "HAIRLINE": "#343436",         # Trennlinie innerhalb einer Gruppe
            "EDGE_HIGHLIGHT": "#3B3B3E",   # 1-px-Lichtkante oben auf einer Flaeche
            "CONTROL_OUTLINE": "#787880",  # 3,58:1 auf GROUP_SURFACE — Schalterspur

            # --- Text --------------------------------------------------------
            "TEXT_PRIMARY": "#F2F2F5",     # 15,55:1 auf WINDOW_BG
            "TEXT_SECONDARY": "#A8A8AE",   #  7,35:1
            "TEXT_TERTIARY": "#8A8A91",    #  5,07:1 auf WINDOW_BG, 4,57:1 auf Gruppe

            # --- Akzent ------------------------------------------------------
            "ACCENT_SURFACE": "#0A6FE8",   # Fuellung, ON_ACCENT 4,72:1
            "ACCENT_HOVER": "#0964D1",
            "ACCENT_PRESSED": "#0857B5",
            "ACCENT_TINT": "#0A84FF",      # Punkte, Icons, Fokusring

            # --- Semantik ----------------------------------------------------
            "SUCCESS": "#30D158",          # 8,60:1 als Punkt
            "WARNING": "#FF9F0A",          # 8,45:1
            "ERROR": "#FF453A",            # 5,10:1 als Punkt
            "ERROR_SURFACE": "#CC372E",    # gefuellt, ON_FILL 5,28:1
            "RECORDING": "#FF453A",        # eigene Semantik, gleicher Ton wie ERROR
            "RECORDING_SURFACE": "#CC372E",
            "RECORDING_HOVER": "#B83229",
            "RECORDING_PRESSED": "#9F2B24",
            "AI": "#BF5AF2",               # 4,93:1 als Punkt
            "AI_SURFACE": "#9B49C4",       # gefuellt, ON_FILL 5,29:1
            "AI_HOVER": "#8C42B0",
            "AI_PRESSED": "#793999",

            # --- Text auf gefuellten Flaechen --------------------------------
            "ON_ACCENT": "#FFFFFF",        # 4,72:1 auf ACCENT_SURFACE
            "ON_FILL": "#FFFFFF",          # auf Rot, Violett und Fehlerflaechen
            "ON_SUCCESS": "#0A1F12",       # 8,53:1 auf SUCCESS
            "KNOB": "#FFFFFF",             # Schaltknopf des Toggles
        },
        LIGHT: {
            # --- Flaechen ----------------------------------------------------
            # Dieselbe Bauart wie im Dunkeln, nur andersherum: der Fenstergrund
            # ist die ruhigste Ebene, jede weitere Ebene tritt einen kleinen
            # Schritt aus ihm heraus — hier nach unten. Bewusst kein reines
            # Weiss als Grund; die Flaechen im Einstellungsfenster sind gross.
            "WINDOW_BG": "#F2F2F6",        # kuehles Off-White
            "GROUP_SURFACE": "#E5E5EB",    # 1,12:1 gegen den Fenstergrund
            "CONTROL_SURFACE": "#D8D8E0",  # 1,13:1 gegen die Gruppenflaeche
            "CONTROL_HOVER": "#C8C8D2",    # dunkler, nicht heller
            # Die schwebende Ebene ist die einzige fast weisse Flaeche. Ihre
            # Tiefe traegt zur Haelfte der 1-px-Rahmen (1,49:1 gegen die
            # Flaeche) — im Hellen die verlaesslichere Kante als Helligkeit.
            "POPOVER_SURFACE": "#FDFDFF",
            "HAIRLINE": "#CECED7",         # 1,25:1 auf der Gruppenflaeche
            "EDGE_HIGHLIGHT": "#FEFEFE",   # Lichtkante auf Controls, 1,41:1
            "CONTROL_OUTLINE": "#82828A",  # 3,29:1 auf GROUP_SURFACE

            # --- Text --------------------------------------------------------
            "TEXT_PRIMARY": "#1C1C1E",     # 14,63:1 auf WINDOW_BG
            "TEXT_SECONDARY": "#52525B",   #  6,71:1
            "TEXT_TERTIARY": "#62626B",    #  5,18:1 auf WINDOW_BG, 4,52:1 auf Gruppe

            # --- Akzent ------------------------------------------------------
            "ACCENT_SURFACE": "#0A6FE8",   # dieselbe Fuellung wie im Dunkeln
            "ACCENT_HOVER": "#0963D0",
            "ACCENT_PRESSED": "#0857B5",
            # Dunkler als im Dark Mode: #0A84FF erreicht auf dem hellen Grund
            # nur 3,07:1 und faellt als Text durch.
            "ACCENT_TINT": "#0A5FC7",      # 5,20:1 auf WINDOW_BG

            # --- Semantik ----------------------------------------------------
            "SUCCESS": "#1A8038",          # 4,31:1 als Punkt
            "WARNING": "#96590A",          # 4,83:1
            "ERROR": "#C2261B",            # 4,97:1 — traegt auch als Text
            "ERROR_SURFACE": "#B8241A",    # gefuellt, ON_FILL 6,31:1
            "RECORDING": "#C2261B",
            "RECORDING_SURFACE": "#B8241A",
            "RECORDING_HOVER": "#A11F16",
            "RECORDING_PRESSED": "#8A1B13",
            "AI": "#8028B8",               # 5,90:1 als Punkt
            "AI_SURFACE": "#7322A6",
            "AI_HOVER": "#651E92",
            "AI_PRESSED": "#57197E",

            # --- Text auf gefuellten Flaechen --------------------------------
            "ON_ACCENT": "#FFFFFF",
            "ON_FILL": "#FFFFFF",
            "ON_SUCCESS": "#FFFFFF",       # 5,01:1 auf SUCCESS
            "KNOB": "#FFFFFF",
        },
    }

    #: Farbwert -> Tokenname, getrennt je Palette. Getrennt, weil derselbe Wert
    #: in den beiden Paletten unterschiedliche Rollen tragen kann.
    _INDEX: dict = {
        mode: {value.upper(): name for name, value in palette.items()}
        for mode, palette in _PALETTES.items()
    }

    mode: str = DEFAULT_MODE
    _callbacks: list = []

    # --- Aktive Werte. `apply()` bindet sie neu; alles liest sie zur Laufzeit.
    WINDOW_BG = _PALETTES[DEFAULT_MODE]["WINDOW_BG"]
    GROUP_SURFACE = _PALETTES[DEFAULT_MODE]["GROUP_SURFACE"]
    CONTROL_SURFACE = _PALETTES[DEFAULT_MODE]["CONTROL_SURFACE"]
    CONTROL_HOVER = _PALETTES[DEFAULT_MODE]["CONTROL_HOVER"]
    POPOVER_SURFACE = _PALETTES[DEFAULT_MODE]["POPOVER_SURFACE"]
    HAIRLINE = _PALETTES[DEFAULT_MODE]["HAIRLINE"]
    EDGE_HIGHLIGHT = _PALETTES[DEFAULT_MODE]["EDGE_HIGHLIGHT"]
    CONTROL_OUTLINE = _PALETTES[DEFAULT_MODE]["CONTROL_OUTLINE"]
    TEXT_PRIMARY = _PALETTES[DEFAULT_MODE]["TEXT_PRIMARY"]
    TEXT_SECONDARY = _PALETTES[DEFAULT_MODE]["TEXT_SECONDARY"]
    TEXT_TERTIARY = _PALETTES[DEFAULT_MODE]["TEXT_TERTIARY"]
    ACCENT_SURFACE = _PALETTES[DEFAULT_MODE]["ACCENT_SURFACE"]
    ACCENT_HOVER = _PALETTES[DEFAULT_MODE]["ACCENT_HOVER"]
    ACCENT_PRESSED = _PALETTES[DEFAULT_MODE]["ACCENT_PRESSED"]
    ACCENT_TINT = _PALETTES[DEFAULT_MODE]["ACCENT_TINT"]
    SUCCESS = _PALETTES[DEFAULT_MODE]["SUCCESS"]
    WARNING = _PALETTES[DEFAULT_MODE]["WARNING"]
    ERROR = _PALETTES[DEFAULT_MODE]["ERROR"]
    ERROR_SURFACE = _PALETTES[DEFAULT_MODE]["ERROR_SURFACE"]
    RECORDING = _PALETTES[DEFAULT_MODE]["RECORDING"]
    RECORDING_SURFACE = _PALETTES[DEFAULT_MODE]["RECORDING_SURFACE"]
    RECORDING_HOVER = _PALETTES[DEFAULT_MODE]["RECORDING_HOVER"]
    RECORDING_PRESSED = _PALETTES[DEFAULT_MODE]["RECORDING_PRESSED"]
    AI = _PALETTES[DEFAULT_MODE]["AI"]
    AI_SURFACE = _PALETTES[DEFAULT_MODE]["AI_SURFACE"]
    AI_HOVER = _PALETTES[DEFAULT_MODE]["AI_HOVER"]
    AI_PRESSED = _PALETTES[DEFAULT_MODE]["AI_PRESSED"]
    ON_ACCENT = _PALETTES[DEFAULT_MODE]["ON_ACCENT"]
    ON_FILL = _PALETTES[DEFAULT_MODE]["ON_FILL"]
    ON_SUCCESS = _PALETTES[DEFAULT_MODE]["ON_SUCCESS"]
    KNOB = _PALETTES[DEFAULT_MODE]["KNOB"]

    # ---------------------------------------------------------------- Modus

    @classmethod
    def normalise(cls, mode) -> str:
        """Bringt einen beliebigen Konfigurationswert auf einen gueltigen Modus.

        Alles Unbekannte — ein Tippfehler, ein alter Wert, `None` — wird hell.
        Eine unbekannte Einstellung darf nie ein Fenster ohne Farben ergeben.
        """
        if isinstance(mode, str) and mode.strip().lower() in cls.MODES:
            return mode.strip().lower()
        return cls.DEFAULT_MODE

    @classmethod
    def palette(cls, mode: Optional[str] = None) -> dict:
        """Die Tokens eines Modus als Kopie — fuer Tests und feste Paletten."""
        return dict(cls._PALETTES[cls.normalise(mode if mode else cls.mode)])

    @classmethod
    def token_names(cls) -> list:
        return sorted(cls._PALETTES[cls.DARK])

    @classmethod
    def apply(cls, mode: str) -> bool:
        """Setzt den Darstellungsmodus. True, wenn sich etwas geaendert hat.

        Danach sind beide Bildspeicher leer und alle angemeldeten Rueckrufe
        gelaufen. Die Widgets faerbt `apply_theme_tree()` ein — der Aufrufer,
        der den Fensterbaum kennt.
        """
        mode = cls.normalise(mode)
        if mode == cls.mode:
            return False
        cls.mode = mode
        for name, value in cls._PALETTES[mode].items():
            setattr(cls, name, value)
        # Beide Zwischenspeicher tragen die Farbe im Schluessel und koennten
        # deshalb gar nicht kollidieren. Geleert werden sie trotzdem: nach einem
        # Wechsel ist jeder Eintrag der alten Palette tot und belegte nur noch
        # Plaetze, bis er von selbst herausfaellt.
        _img_cache.clear()
        _icon_cache.clear()
        for callback in list(cls._callbacks):
            try:
                callback()
            except Exception:       # ein defekter Rueckruf darf nichts blockieren
                pass
        return True

    @classmethod
    def register(cls, callback) -> None:
        """Meldet einen Rueckruf an, der nach jedem Moduswechsel laeuft."""
        cls._callbacks.append(callback)

    @classmethod
    def unregister(cls, callback) -> None:
        try:
            cls._callbacks.remove(callback)
        except ValueError:
            pass

    @classmethod
    def remap(cls, colour):
        """Uebersetzt eine Farbe der *anderen* Palette in die aktive.

        Widgets halten Farben als Zeichenkette fest — ein `bg` an einem
        `tk.Frame`, ein gemerktes `bg_under`. Nach einem Moduswechsel stehen
        dort Werte der alten Palette. `remap` sucht den Tokennamen und liefert
        den Wert desselben Tokens im neuen Modus.

        Gehoert die Farbe schon zur aktiven Palette, bleibt sie unveraendert —
        der Aufruf ist damit wiederholbar. Was in keiner Palette vorkommt (etwa
        `SystemButtonFace`), bleibt ebenfalls stehen.
        """
        if not isinstance(colour, str) or not colour:
            return colour
        key = colour.upper()
        if key in cls._INDEX[cls.mode]:
            return colour
        for other in cls.MODES:
            token = cls._INDEX[other].get(key)
            if token is not None:
                return getattr(cls, token)
        return colour


#: Tk-Optionen, die eine Farbe tragen und nach einem Moduswechsel mitwandern.
TINTED_OPTIONS = (
    "bg", "fg", "highlightbackground", "highlightcolor", "activebackground",
    "activeforeground", "disabledforeground", "selectbackground",
    "selectforeground", "insertbackground", "troughcolor",
)


def set_surface_under(widget: tk.Misc, colour: str) -> None:
    """Setzt den Untergrund eines Controls — samt allem, was darin sitzt.

    Eine Einstellungszeile wechselt unter dem Zeiger ihre Flaeche. Das Control
    darauf muss mitwechseln, sonst steht es als Rechteck der alten Farbe
    darin: seine Flaeche ist rund, die Ecken lassen den Untergrund durch, und
    der ist dann noch der alte. Im dunklen Modus fiel das kaum auf (1,16:1
    zwischen den beiden Flaechen), im hellen sofort.

    Bauteile merken sich ihren Untergrund in `_bg_under` und zeichnen ihn in
    die Ecken; deshalb wird er hier gesetzt *und* neu gezeichnet.
    """
    try:
        widget.configure(bg=colour)
    except (tk.TclError, TypeError):      # kein Widget mit Hintergrund
        return
    if hasattr(widget, "_bg_under"):
        widget._bg_under = colour
        for name in ("_draw", "_render"):
            zeichnen = getattr(widget, name, None)
            if callable(zeichnen):
                try:
                    zeichnen()
                except tk.TclError:      # pragma: no cover - schon zerstoert
                    pass
                break
    try:
        kinder = widget.winfo_children()
    except tk.TclError:      # pragma: no cover
        return
    for kind in kinder:
        set_surface_under(kind, colour)


def retint(widget: tk.Misc) -> int:
    """Schluesselt die Farboptionen *eines* Widgets auf die aktive Palette um.

    Gibt zurueck, wie viele Optionen sich geaendert haben. Angefasst werden nur
    Werte, die in einer der Paletten stehen — alles andere bleibt, wie es ist.
    """
    changed = 0
    for option in TINTED_OPTIONS:
        try:
            current = str(widget.cget(option))
        except (tk.TclError, TypeError):
            continue                     # Option gibt es an diesem Widget nicht
        wanted = Theme.remap(current)
        if wanted != current:
            try:
                widget.configure(**{option: wanted})
                changed += 1
            except tk.TclError:          # pragma: no cover - Widget zerstoert
                pass
    return changed


def apply_theme_tree(widget: tk.Misc) -> int:
    """Faerbt einen ganzen Widgetbaum nach einem Moduswechsel neu ein.

    Zwei Schritte je Widget: erst die schlichten Tk-Farboptionen umschluesseln,
    dann — falls das Bauteil einen eigenen Haken mitbringt — dessen
    `_on_theme_change()` rufen. Der Haken kommt zuletzt, damit ein Control, das
    seine Farben selbst berechnet, das letzte Wort behaelt.

    Bewusst ein Baumdurchlauf und keine globale Widget-Liste: eine solche Liste
    muesste jedes Widget beim Zerstoeren wieder abmelden und waere die naechste
    Quelle fuer Lecks. Der Baum weiss von selbst, wer noch lebt.

    Gibt die Zahl der besuchten Widgets zurueck.
    """
    try:
        children = list(widget.winfo_children())
    except tk.TclError:      # pragma: no cover - Widget bereits zerstoert
        return 0
    retint(widget)
    hook = getattr(widget, "_on_theme_change", None)
    if callable(hook):
        try:
            hook()
        except tk.TclError:  # pragma: no cover
            pass
    seen = 1
    for child in children:
        seen += apply_theme_tree(child)
    return seen


class Space:
    """Abstände auf einem 4-px-Raster, in logischen Pixeln.

    Die Rollen unten sind die einzigen Werte, die im Layout auftauchen sollen —
    keine Einzelwerte nach Gefühl mehr. Physische Pixel liefert `px()`.
    """

    STEP = 4

    XS = 2
    SM = 4
    MD = 8
    LG = 12
    XL = 16
    XXL = 20

    # Rollen
    WINDOW = 16        # Fensterrand
    SECTION_GAP = 20   # zwischen zwei Abschnitten
    GROUP_GAP = 16     # zwischen Überschrift und Gruppe
    ROW_X = 12         # Zeileninnenabstand waagerecht
    ROW_Y = 9          # Zeileninnenabstand senkrecht
    CONTROL_GAP = 8    # zwischen zwei Controls
    TEXT_GAP = 2       # zwischen Titel und Beschreibung
    ICON_GAP = 6       # zwischen Icon und Text


class Radius:
    """Verwandte Radienfamilie: innen = außen − Abstand."""

    WINDOW = 12
    POPOVER = 12
    GROUP = 10
    CONTROL = 8
    BUTTON = 8
    SEGMENT = 2

    @staticmethod
    def pill(height: int) -> int:
        """Pillenform — nur für Chip und Toggle."""
        return max(1, height // 2)


class Height:
    """Control-Höhen in logischen Pixeln. Bewusst nur drei."""

    PRIMARY = 36        # Hauptaktion
    STANDARD = 32       # Select, Icon-Button, Zeilencontrol
    COMPACT = 24        # kompakte Anzeige

    ROW_SIMPLE = 44     # Einstellungszeile mit einer Textzeile
    ROW_DETAIL = 56     # Einstellungszeile mit Beschreibung oder Status


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
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0)
        return bool(ok) and bool(value.value)
    except Exception:      # pragma: no cover - kein Windows oder API fehlt
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
    mixed = tuple(int(round(x + (y - x) * clamped)) for x, y in zip(a, b))
    return "#%02X%02X%02X" % mixed


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
    def run(cls, widget: tk.Misc, key: str, duration_ms: int,
            step: Callable[[float], None],
            done: Optional[Callable[[], None]] = None) -> bool:
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
            except tk.TclError:      # Widget verschwand mitten im Lauf
                cls._jobs.pop(token, None)
                return
            if last:
                cls._jobs.pop(token, None)
                if done is not None:
                    done()
                return
            try:
                cls._jobs[token] = widget.after(FRAME_MS, tick)
            except tk.TclError:      # pragma: no cover - Fenster schon weg
                cls._jobs.pop(token, None)

        tick()
        return token in cls._jobs

    @classmethod
    def cancel(cls, widget: tk.Misc, key: str) -> None:
        job = cls._jobs.pop((id(widget), key), None)
        if job is None:
            return
        try:
            widget.after_cancel(job)
        except Exception:      # pragma: no cover - Widget bereits zerstört
            pass

    @classmethod
    def cancel_all(cls, widget: tk.Misc) -> None:
        """Beim Zerstören: alles zu diesem Widget abräumen."""
        target = id(widget)
        for token in [t for t in cls._jobs if t[0] == target]:
            job = cls._jobs.pop(token, None)
            try:
                widget.after_cancel(job)
            except Exception:      # pragma: no cover
                pass

    @classmethod
    def pending(cls, widget: Optional[tk.Misc] = None) -> int:
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
        except tk.TclError:      # pragma: no cover
            return
        widget._anim_watched = True


def init_animations() -> bool:
    """Übernimmt die Systemeinstellung. Gibt zurück, ob animiert wird."""
    Animator.enabled = system_animations_enabled()
    return Animator.enabled


_FONTS: dict[str, tkfont.Font] = {}

# Rolle -> (Familienschlüssel, Punktgröße, Schnitt). "display" und "text" werden
# beim Initialisieren gegen die tatsächlich installierten Familien aufgelöst.
_FONT_ROLES: dict[str, tuple] = {
    "window_title": ("text", 13, "bold"),     # Fensterbeschriftung, keine Ueberschrift
    "section": ("small", 11, "bold"),         # Abschnittslabel in den Einstellungen
    "row_title": ("text", 12, "normal"),      # Titel einer Einstellungszeile
    "description": ("small", 11, "normal"),   # Hilfstext unter einem Titel
    "status": ("text", 12, "normal"),         # die eine Statuszeile
    "button": ("text", 13, "bold"),
    "shortcut": ("mono", 11, "normal"),       # Tastenkuerzel
    "meta": ("small", 10, "normal"),          # Chip, Zusatzangaben
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
        "mono": "Cascadia Mono" if "Cascadia Mono" in families else "Consolas",
    }
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


# ---------------------------------------------------------------- Flächen-Rendering

SUPERSAMPLE = 4
"""Faktor, mit dem Formen überabgetastet gezeichnet und dann verkleinert werden.

Getrennt zu halten von `px()`: `px()` rechnet logische Maße in physische Pixel,
das Supersampling arbeitet ausschließlich innerhalb dieser Funktion und ist nach
dem Verkleinern wieder verschwunden. Beides nie miteinander multiplizieren.
"""

SQUIRCLE_EXPONENT = 4.2
"""Exponent der Superellipse. 2 wäre ein Kreisbogen, gegen unendlich eine Ecke.

4,2 liegt nah an Apples durchgehender Eckenkrümmung: sichtbar weicher als ein
Kreisbogen, ohne dass die Ecke ausfranst.
"""

CACHE_LIMIT = 256
"""Obergrenze des Bild-Caches.

Gerechnet für den ungünstigsten Fall: eine 600 x 60 grosse RGBA-Fläche belegt
rund 140 KB, 256 Einträge also gut 35 MB. Real liegt der Verbrauch weit
darunter, weil die meisten Flächen klein sind. Ohne Grenze wüchse der Cache mit
jeder Breite, jedem Zustand und jedem DPI-Faktor unbegrenzt weiter.
"""

_img_cache: "OrderedDict[tuple, ImageTk.PhotoImage]" = OrderedDict()


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _squircle_points(width: int, height: int, radius: int, steps: int = 24) -> list:
    """Umriss eines Rechtecks mit Superellipsen-Ecken.

    Nur die Ecken folgen der Superellipse; die Kanten dazwischen bleiben gerade.
    Eine Superellipse über die gesamte Fläche würde einen breiten Button in eine
    Ellipse verwandeln.
    """
    radius = max(0, min(radius, width // 2, height // 2))
    if radius == 0:
        return [(0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)]

    exponent = 2.0 / SQUIRCLE_EXPONENT
    quarter = [
        (radius * (math.cos(t) ** exponent), radius * (math.sin(t) ** exponent))
        for t in (i * (math.pi / 2) / (steps - 1) for i in range(steps))
    ]

    right, bottom = width - 1, height - 1
    points: list[tuple[float, float]] = []
    # oben rechts: von der oberen Kante zur rechten Kante
    points += [(right - radius + y, radius - x) for x, y in quarter]
    # unten rechts
    points += [(right - radius + x, bottom - radius + y) for x, y in quarter]
    # unten links
    points += [(radius - y, bottom - radius + x) for x, y in quarter]
    # oben links
    points += [(radius - x, radius - y) for x, y in quarter]
    return points


def _shape_mask(size: tuple[int, int], radius: int, squircle: bool) -> Image.Image:
    """Weisse Form auf schwarzem Grund — die Alpha-Maske der Fläche."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if squircle:
        draw.polygon(_squircle_points(size[0], size[1], radius), fill=255)
    else:
        draw.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def _vertical_ramp(size: tuple[int, int], top: int, stop: float) -> Image.Image:
    """Senkrechter Verlauf von `top` (oben) auf 0 bei `stop` * Höhe."""
    width, height = size
    ramp = Image.new("L", (1, height), 0)
    limit = max(1, int(height * stop))
    for y in range(limit):
        ramp.putpixel((0, y), int(top * (1.0 - y / limit)))
    return ramp.resize((width, height))


def render_surface(
    width: int,
    height: int,
    radius: int,
    fill: str,
    *,
    edge: Optional[str] = None,
    gradient: float = 0.0,
    shadow: Optional[tuple[float, float, float]] = None,
    squircle: bool = False,
    border: Optional[str] = None,
    border_w: int = 0,
    ring: Optional[str] = None,
    ring_width: int = 0,
    ring_gap: int = 0,
    dither: bool = True,
) -> Image.Image:
    """Rendert eine UI-Fläche als anti-aliastes RGBA-Bild.

    Reines PIL, ohne Tk und ohne Cache — dadurch ohne Anzeige testbar.
    Die zwischenspeichernde Tk-Variante ist `surface()`.

    Alle Masse sind **physische** Pixel — der Aufrufer hat sie bereits durch
    `px()` geschickt. Gezeichnet wird um `SUPERSAMPLE` vergrössert und danach
    mit Lanczos verkleinert; daher die weichen Kanten.

    Args:
        radius: Eckenradius. Bei `squircle=True` Superellipsen-Ecken.
        edge: Farbe einer 1 px feinen Lichtkante am oberen Rand der Form. Sie
            folgt der Rundung und ragt nie darüber hinaus, weil sie als
            Differenz der Form zu ihrer eigenen Verschiebung entsteht.
        gradient: Anteil einer weissen Aufhellung am oberen Rand (0…1). Werte
            über etwa 0,05 wirken sichtbar als Verlauf statt als Material.
        shadow: (Weichzeichnung, Versatz nach unten, Deckkraft 0…1). Der
            Schatten wird in die Bounding Box eingerechnet — die Form wird dafür
            entsprechend eingerückt, das Bild bleibt gleich gross. Nur sinnvoll,
            wenn der Untergrund bekannt ist, etwa beim Schaltknopf.
        border: Konturfarbe; `border_w` deren Stärke in physischen Pixeln.
        ring: Farbe des Fokusrings. `ring_width` und `ring_gap` reservieren den
            Platz dafür *immer*, auch wenn `ring` None ist — sonst spränge die
            Fläche in dem Moment, in dem sie den Fokus bekommt.
        dither: Sehr feines Rauschen gegen Streifenbildung im Verlauf. Liegt
            unterhalb der Wahrnehmungsschwelle und verschiebt die mittlere
            Farbe nicht.
    """
    width, height = max(1, int(width)), max(1, int(height))
    scale = SUPERSAMPLE
    big = (width * scale, height * scale)

    ring_inset = int((ring_width + ring_gap) * scale)
    pad = ring_inset
    if shadow is not None:
        blur, offset, _alpha = shadow
        pad += int(math.ceil((blur + abs(offset)) * scale))

    inner = (max(1, big[0] - 2 * pad), max(1, big[1] - 2 * pad))
    mask = _shape_mask(inner, int(radius * scale), squircle)

    canvas = Image.new("RGBA", big, (0, 0, 0, 0))

    # --- Schatten zuerst, damit die Fläche darüber liegt ---
    if shadow is not None:
        blur, offset, alpha = shadow
        shadow_layer = Image.new("RGBA", big, (0, 0, 0, 0))
        blurred = mask.filter(ImageFilter.GaussianBlur(blur * scale))
        tinted = Image.new("RGBA", inner, (0, 0, 0, int(255 * alpha)))
        shadow_layer.paste(tinted, (pad, pad + int(offset * scale)), blurred)
        canvas = Image.alpha_composite(canvas, shadow_layer)

    # --- Füllung, optional mit Mikro-Verlauf ---
    body = Image.new("RGBA", inner, _hex_to_rgb(fill) + (255,))
    if gradient > 0:
        highlight = Image.new("RGBA", inner, (255, 255, 255, 255))
        body = Image.composite(
            Image.alpha_composite(body, highlight), body,
            _vertical_ramp(inner, int(255 * gradient), 0.55),
        )

    # --- Lichtkante: Form minus verschobene Form = genau der obere Saum ---
    if edge is not None:
        edge_w = max(1, scale)                       # 1 logisches Pixel
        shifted = Image.new("L", inner, 0)
        shifted.paste(mask, (0, edge_w))
        rim = ImageChops.subtract(mask, shifted)
        rim = ImageChops.multiply(rim, _vertical_ramp(inner, 255, 0.5))
        body.paste(Image.new("RGBA", inner, _hex_to_rgb(edge) + (255,)), (0, 0), rim)

    if border is not None and border_w:
        outline = Image.new("RGBA", inner, (0, 0, 0, 0))
        draw = ImageDraw.Draw(outline)
        line = max(1, int(border_w * scale))
        if squircle:
            draw.line(
                _squircle_points(inner[0], inner[1], int(radius * scale))
                + [_squircle_points(inner[0], inner[1], int(radius * scale))[0]],
                fill=border, width=line, joint="curve",
            )
        else:
            draw.rounded_rectangle(
                [0, 0, inner[0] - 1, inner[1] - 1],
                radius=int(radius * scale), outline=border, width=line,
            )
        body = Image.alpha_composite(body, outline)

    body.putalpha(mask)
    layer = Image.new("RGBA", big, (0, 0, 0, 0))
    layer.paste(body, (pad, pad), body)
    canvas = Image.alpha_composite(canvas, layer)

    if ring is not None and ring_width:
        line = max(1, int(ring_width * scale))
        draw = ImageDraw.Draw(canvas)
        outer = [line // 2, line // 2, big[0] - 1 - line // 2, big[1] - 1 - line // 2]
        outer_radius = int((radius + ring_gap + ring_width) * scale)
        draw.rounded_rectangle(outer, radius=outer_radius, outline=ring, width=line)

    image = canvas.resize((width, height), Image.LANCZOS)

    if dither and gradient > 0:
        # Rund +-1 Helligkeitsstufe, nach dem Verkleinern aufgebracht: bricht
        # Streifenbildung, ohne als Textur sichtbar zu werden.
        # effect_noise liegt um 128; add(scale=1, offset=-128) rechnet
        # a + b - 128 und zentriert das Rauschen damit auf null.
        noise = Image.effect_noise((width, height), 1.0).convert("L")
        rgb, alpha_channel = image.convert("RGB"), image.getchannel("A")
        rgb = ImageChops.add(
            rgb, Image.merge("RGB", (noise, noise, noise)), scale=1.0, offset=-128
        )
        image = Image.merge("RGBA", (*rgb.split(), alpha_channel))

    return image


def surface(
    width: int,
    height: int,
    radius: int,
    fill: str,
    *,
    edge: Optional[str] = None,
    gradient: float = 0.0,
    shadow: Optional[tuple[float, float, float]] = None,
    squircle: bool = False,
    border: Optional[str] = None,
    border_w: int = 0,
    ring: Optional[str] = None,
    ring_width: int = 0,
    ring_gap: int = 0,
    dither: bool = True,
) -> ImageTk.PhotoImage:
    """Wie `render_surface()`, aber als Tk-Bild und zwischengespeichert.

    Der Cache-Schlüssel enthält jeden Parameter *und* den DPI-Faktor: ein Bild
    aus 100 % darf bei 150 % nicht wiederverwendet werden. Die Grösse ist auf
    `CACHE_LIMIT` begrenzt, der älteste Eintrag fliegt heraus.
    """
    width, height = max(1, int(width)), max(1, int(height))
    key = (width, height, radius, fill, edge, round(gradient, 4), shadow,
           squircle, border, border_w, ring, ring_width, ring_gap, dither,
           Scale.factor)
    cached = _img_cache.get(key)
    if cached is not None:
        _img_cache.move_to_end(key)
        return cached

    photo = ImageTk.PhotoImage(
        render_surface(width, height, radius, fill, edge=edge, gradient=gradient,
                       shadow=shadow, squircle=squircle, border=border,
                       border_w=border_w, ring=ring, ring_width=ring_width,
                       ring_gap=ring_gap, dither=dither)
    )
    _img_cache[key] = photo
    if len(_img_cache) > CACHE_LIMIT:
        _img_cache.popitem(last=False)      # ältester Eintrag fliegt raus
    return photo


def rounded_image(
    w: int,
    h: int,
    radius: int,
    fill: str,
    border: Optional[str] = None,
    border_w: int = 0,
    scale: int = SUPERSAMPLE,
) -> ImageTk.PhotoImage:
    """Schlichte abgerundete Fläche — Kurzform von `surface()`.

    Bleibt erhalten, weil die vorhandenen Controls sie benutzen; sie wandern in
    Phase 7 auf `surface()` mit Lichtkante und Verlauf.
    """
    return surface(w, h, radius, fill, border=border, border_w=border_w)


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
        "plain": {   # Icon-Buttons und Zeilen: ohne Flaeche im Ruhezustand
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
    RING_WIDTH = 2      # logisch
    RING_GAP = 2        # logisch

    @classmethod
    def roles(cls) -> list[str]:
        return list(cls._ROLES)

    @classmethod
    def resolve(cls, role: str, active) -> tuple[Optional[str], str]:
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
    def ring_colour(cls, active) -> Optional[str]:
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

    def colours(self) -> tuple[Optional[str], str]:
        return ControlStyle.resolve(self._role, self._states)

    def ring(self) -> Optional[str]:
        return ControlStyle.ring_colour(self._states)

    def on_state_change(self) -> None:      # pragma: no cover - überschrieben
        """Wird bei jeder Zustandsänderung gerufen; Controls zeichnen hier neu."""


class FocusMixin:
    """Tastaturfokus für ein Control.

    Bindet ausschliesslich am Widget selbst — keine globale Tastaturbehandlung,
    die anderen Controls oder den globalen Hotkeys in die Quere käme.
    """

    def init_focus(self, activate: Optional[Callable[[], None]] = None) -> None:
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
        return "break"      # verhindert, dass Tk die Taste weiterreicht


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
        except tk.TclError:      # pragma: no cover - Fenster verschwindet gerade
            break
        if not name:
            break
        widget = window.nametowidget(name)
        if widget in chain:
            break               # einmal herum
        chain.append(widget)
        current = widget
    return chain


def return_focus_to(widget: Optional[tk.Misc]) -> None:
    """Gibt den Fokus an das Element zurück, das ein Popup geöffnet hat.

    Regel für alle künftigen Popover: wer öffnet, bekommt den Fokus zurück.
    """
    if widget is None:
        return
    try:
        if widget.winfo_exists():
            widget.focus_set()
    except tk.TclError:      # pragma: no cover - Widget bereits zerstört
        pass


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


# ---------------------------------------------------------------- Icons

ICON_BOX = 16
"""Logische Kantenlänge des Rasters, in dem alle Icons gezeichnet werden."""

ICON_STROKE = 1.5
"""Strichstärke in logischen Pixeln — für alle Motive identisch."""

_icon_cache: "OrderedDict[tuple, ImageTk.PhotoImage]" = OrderedDict()
ICON_CACHE_LIMIT = 128


def _stroke(draw: ImageDraw.ImageDraw, points: list, width: float, color: str) -> None:
    """Polylinie mit runden Ecken und runden Enden.

    PIL kennt keine Linienenden; die Kreise an jedem Stützpunkt liefern sie —
    und zugleich saubere Innenecken.
    """
    draw.line(points, fill=color, width=max(1, int(round(width))), joint="curve")
    radius = width / 2.0
    for x, y in points:
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)


def _icon_settings(draw, unit: float, stroke: float, color: str) -> None:
    """Regler statt Zahnrad — drei Linien mit versetzten Reglerpunkten."""
    # Knopfradius und Linienlaenge bewusst knapp: mit drei vollen Linien wirkt
    # das Motiv sonst deutlich schwerer als die uebrigen fuenf Icons.
    knob = stroke * 1.15
    for y, knob_x in ((4.3, 5.6), (8.0, 9.8), (11.7, 6.6)):
        _stroke(draw, [(3.0 * unit, y * unit), (13.0 * unit, y * unit)], stroke, color)
        cx, cy = knob_x * unit, y * unit
        draw.ellipse([cx - knob, cy - knob, cx + knob, cy + knob], fill=color)


def _icon_close(draw, unit: float, stroke: float, color: str) -> None:
    """Symmetrisches Kreuz — durch die Symmetrie zugleich optisch zentriert."""
    _stroke(draw, [(4.6 * unit, 4.6 * unit), (11.4 * unit, 11.4 * unit)], stroke, color)
    _stroke(draw, [(11.4 * unit, 4.6 * unit), (4.6 * unit, 11.4 * unit)], stroke, color)


def _icon_chevron(draw, unit: float, stroke: float, color: str) -> None:
    """Nach unten zeigender Winkel; um die eigene Höhe optisch ausgemittelt."""
    _stroke(
        draw,
        [(3.8 * unit, 6.4 * unit), (8.0 * unit, 10.2 * unit), (12.2 * unit, 6.4 * unit)],
        stroke, color,
    )


def _icon_check(draw, unit: float, stroke: float, color: str) -> None:
    _stroke(
        draw,
        [(3.6 * unit, 8.4 * unit), (6.6 * unit, 11.4 * unit), (12.4 * unit, 4.9 * unit)],
        stroke, color,
    )


def _icon_download(draw, unit: float, stroke: float, color: str) -> None:
    """Abwärtspfeil ohne Unterkante — bleibt auch klein eindeutig."""
    _stroke(draw, [(8.0 * unit, 3.4 * unit), (8.0 * unit, 11.6 * unit)], stroke, color)
    _stroke(
        draw,
        [(4.6 * unit, 8.2 * unit), (8.0 * unit, 11.6 * unit), (11.4 * unit, 8.2 * unit)],
        stroke, color,
    )


def _icon_stop(draw, unit: float, stroke: float, color: str) -> None:
    """Gefülltes Quadrat mit leicht gerundeten Ecken."""
    draw.rounded_rectangle(
        [4.6 * unit, 4.6 * unit, 11.4 * unit, 11.4 * unit],
        radius=stroke * 0.9, fill=color,
    )


def _icon_trash(draw, unit: float, stroke: float, color: str) -> None:
    """Papierkorb: Deckel, Griff, Korpus — ohne Innenstriche.

    Die ueblichen zwei senkrechten Striche im Korpus fallen weg. Bei 16 px
    liegen sie knapp zwei Bildpunkte auseinander und laufen beim Verkleinern
    zu einem grauen Fleck zusammen; die Silhouette allein bleibt eindeutig.
    """
    _stroke(draw, [(3.4 * unit, 5.0 * unit), (12.6 * unit, 5.0 * unit)],
            stroke, color)
    _stroke(draw, [(6.3 * unit, 5.0 * unit), (6.3 * unit, 3.2 * unit),
                   (9.7 * unit, 3.2 * unit), (9.7 * unit, 5.0 * unit)],
            stroke, color)
    _stroke(draw, [(4.9 * unit, 5.0 * unit), (5.5 * unit, 12.8 * unit),
                   (10.5 * unit, 12.8 * unit), (11.1 * unit, 5.0 * unit)],
            stroke, color)


_ICON_PAINTERS = {
    "settings": _icon_settings,
    "close": _icon_close,
    "chevron": _icon_chevron,
    "check": _icon_check,
    "download": _icon_download,
    "stop": _icon_stop,
    "trash": _icon_trash,
}


def icon_names() -> list[str]:
    return list(_ICON_PAINTERS)


def render_icon(name: str, size: int, color: str) -> Image.Image:
    """Zeichnet ein Icon als RGBA-Bild. Reines PIL, ohne Tk und ohne Cache.

    Args:
        name: Schlüssel aus `icon_names()`.
        size: Kantenlänge in **physischen** Pixeln (der Aufrufer hat bereits
            `px()` angewandt).
        color: Strichfarbe. Die Geometrie ist für alle Zustände dieselbe —
            Hover, Disabled und Auswahl unterscheiden sich nur hierin.
    """
    painter = _ICON_PAINTERS.get(name)
    if painter is None:
        raise KeyError(f"Unbekanntes Icon: {name!r}")

    size = max(1, int(size))
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    unit = big / ICON_BOX                       # ein Rasterschritt in Zielpixeln
    stroke = ICON_STROKE * (size / ICON_BOX) * SUPERSAMPLE
    painter(draw, unit, stroke, color)

    return image.resize((size, size), Image.LANCZOS)


def icon(name: str, size: Optional[int] = None, color: Optional[str] = None):
    """Wie `render_icon()`, aber als Tk-Bild und zwischengespeichert.

    `size` ist eine **logische** Größe und wird hier skaliert; der Cache-Schlüssel
    trägt Motiv, Größe, Farbe und DPI-Faktor.

    Die Standardfarbe wird erst hier aufgeloest, nicht im Vorgabewert des
    Parameters: der waere beim Import eingefroren und bliebe nach einem
    Moduswechsel auf der alten Palette stehen.
    """
    if color is None:
        color = Theme.TEXT_SECONDARY
    logical = ICON_BOX if size is None else size
    physical = px(logical)
    key = (name, physical, color, Scale.factor)
    cached = _icon_cache.get(key)
    if cached is not None:
        _icon_cache.move_to_end(key)
        return cached

    photo = ImageTk.PhotoImage(render_icon(name, physical, color))
    _icon_cache[key] = photo
    if len(_icon_cache) > ICON_CACHE_LIMIT:
        _icon_cache.popitem(last=False)
    return photo


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
        command: Optional[Callable[[], None]] = None,
        squircle: bool = True,
        font_role: str = "button",
        **label_kwargs,
    ) -> None:
        super().__init__(
            master, bg=bg_under, compound="center", bd=0, highlightthickness=0,
            font=font(font_role), cursor="hand2", **label_kwargs,
        )
        self.init_states(role)
        self._bg_under = bg_under
        self._logical_height = height
        self._logical_radius = radius
        self._squircle = squircle
        self._command = command
        self._last_width = 0
        self._image: Optional[ImageTk.PhotoImage] = None

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
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
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
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
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
        except tk.TclError:      # pragma: no cover
            return
        self._image = image
        _fill, foreground = self.colours()
        self.configure(image=image, fg=foreground)

    def _paint(self, width: int, height: int) -> ImageTk.PhotoImage:  # pragma: no cover
        raise NotImplementedError


class _SurfaceControl(BaseControl):
    """Control mit gefüllter Fläche, Lichtkante und Fokusring."""

    GRADIENT = 0.025

    def _paint(self, width: int, height: int) -> ImageTk.PhotoImage:
        fill, _foreground = self.colours()
        ring_w, ring_gap = px(ControlStyle.RING_WIDTH), px(ControlStyle.RING_GAP)
        return surface(
            width, height + 2 * (ring_w + ring_gap), px(self._logical_radius),
            fill if fill is not None else self._bg_under,
            edge=Theme.EDGE_HIGHLIGHT if fill is not None else None,
            gradient=self.GRADIENT if fill is not None else 0.0,
            squircle=self._squircle,
            ring=self.ring(), ring_width=ring_w, ring_gap=ring_gap,
        )


class PrimaryButton(_SurfaceControl):
    """Die eine dominante Aktion eines Fensters."""

    def __init__(self, master: tk.Misc, text: str, command: Callable[[], None], *,
                 bg_under: str, role: str = "primary") -> None:
        super().__init__(master, bg_under=bg_under, role=role,
                         height=Height.PRIMARY, radius=Radius.BUTTON,
                         command=command, text=text)


class SecondaryButton(_SurfaceControl):
    """Neutrale, materialartige Aktion — optional mit Icon links vom Text.

    Das Icon wird in die Fläche einkomponiert und aus der gemessenen Textbreite
    positioniert, damit Icon und Text zusammen optisch mittig stehen. Tk kann
    einen Text mit `compound="center"` nicht versetzen.
    """

    def __init__(self, master: tk.Misc, text: str, command: Callable[[], None], *,
                 bg_under: str, icon_name: Optional[str] = None,
                 icon_colour: Optional[str] = None,
                 height: int = Height.PRIMARY, role: str = "secondary") -> None:
        self._icon_name = icon_name
        self._icon_colour = icon_colour
        super().__init__(master, bg_under=bg_under, role=role, height=height,
                         radius=Radius.BUTTON, command=command, text=text)

    def set_icon(self, icon_name: Optional[str], colour: Optional[str] = None) -> None:
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
        text_width = int(self.tk.call("font", "measure",
                                      self.cget("font"), self.cget("text")))
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
        self._composed = composed        # Referenz halten
        return composed


class IconButton(BaseControl):
    """Quadratische Schaltfläche mit Icon — Zahnrad und Kreuz in der Kopfzeile.

    Im Ruhezustand ohne Fläche; erst der Zeiger legt eine dezente Fläche unter.
    """

    def __init__(self, master: tk.Misc, icon_name: str, command: Callable[[], None], *,
                 bg_under: str, size: int = Height.STANDARD,
                 icon_size: int = ICON_BOX, tooltip: str = "") -> None:
        self._icon_name = icon_name
        self._icon_size = icon_size
        self._hover_colour: Optional[str] = None
        super().__init__(master, bg_under=bg_under, role="plain", height=size,
                         radius=Radius.CONTROL, command=command)
        self._side = size
        self.configure(width=px(size), height=px(size))
        self._last_width = px(size)
        self._render()

    def set_hover_colour(self, colour: Optional[str]) -> None:
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
        pass      # feste Grösse, die Breite kommt nicht vom Layout

    def _on_scale_change(self, _factor: float) -> None:
        try:
            side = px(self._side)
            self.configure(width=side, height=side)
            self._last_width = side
            self._render()
        except tk.TclError:      # pragma: no cover
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
            plate = Image.new("RGBA", (side, side),
                              _hex_to_rgb(self._bg_under) + (255,))
        else:
            ring_w, ring_gap = px(ControlStyle.RING_WIDTH), px(ControlStyle.RING_GAP)
            plate = ImageTk.getimage(surface(
                side, side, px(Radius.CONTROL),
                fill if fill is not None else self._bg_under,
                edge=Theme.EDGE_HIGHLIGHT if fill is not None else None,
                squircle=True, ring=ring, ring_width=ring_w, ring_gap=ring_gap,
            ))
        glyph_size = px(self._icon_size)
        glyph = render_icon(self._icon_name, glyph_size, foreground)
        offset = ((plate.width - glyph_size) // 2, (plate.height - glyph_size) // 2)
        plate.paste(glyph, offset, glyph)
        return ImageTk.PhotoImage(plate)


class Switch(tk.Canvas, StateMixin, FocusMixin):
    """Schalter im Apple-Zuschnitt.

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
        return blend(off, Theme.SUCCESS, self._progress)

    def _draw(self) -> None:
        self.delete("all")
        width, height = px(self.WIDTH), px(self.HEIGHT)
        ring_w, ring_gap = px(ControlStyle.RING_WIDTH), px(ControlStyle.RING_GAP)
        pad = ring_w + ring_gap

        # Die ausgeschaltete Spur hebt sich mit 1,16:1 kaum vom Zeilengrund ab —
        # ohne Umriss wäre nicht zu sehen, dass dort überhaupt ein Bedienelement
        # sitzt. Eingeschaltet trägt die Fläche selbst 7,75:1, dann verschwindet
        # der Umriss in ihr.
        track = surface(
            width + 2 * pad, height + 2 * pad, Radius.pill(height),
            self._track_colour(), squircle=False,
            border=blend(Theme.CONTROL_OUTLINE, Theme.SUCCESS, self._progress),
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
    """Segmentierter Pegel — dauerhaft sichtbar, im Ruhezustand dunkel.

    Die Segmentzahl folgt der verfügbaren Breite, damit die Segmentbreite über
    alle Skalierungen und Fensterbreiten gleich bleibt.
    """

    SEGMENT_WIDTH = 9      # logisch, Zielbreite eines Segments
    GAP = 3                # logisch
    HEIGHT = 8             # logisch

    def __init__(self, master: tk.Misc, *, bg_under: str,
                 height: int = HEIGHT) -> None:
        self._bg_under = bg_under
        self._logical_height = height
        self._level = 0.0
        self._last_width = 0
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
    def segments(self) -> int:
        if self._last_width <= 1:
            return 0
        step = px(self.SEGMENT_WIDTH) + px(self.GAP)
        return max(4, (self._last_width + px(self.GAP)) // step)

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
    def _segment_colour(position: float) -> str:
        if position > 0.82:
            return Theme.ERROR
        if position > 0.60:
            return Theme.WARNING
        return Theme.SUCCESS

    def _draw(self) -> None:
        self.delete("all")
        count = self.segments
        if not count:
            return
        gap = px(self.GAP)
        height = px(self._logical_height)
        seg_width = max(2, (self._last_width - gap * (count - 1)) // count)
        lit = int(round(self._level * count))
        self._images = []
        for index in range(count):
            position = index / max(1, count - 1)
            colour = (self._segment_colour(position) if index < lit
                      else Theme.CONTROL_SURFACE)
            image = surface(seg_width, height,
                            min(px(Radius.SEGMENT), seg_width // 2), colour,
                            squircle=False)
            self._images.append(image)
            self.create_image(index * (seg_width + gap), 0, anchor="nw", image=image)


# ---------------------------------------------------------------- Einstellungs-Bausteine


class ScrollArea(tk.Frame):
    """Scrollbarer Bereich mit schlanker eigener Leiste.

    Tk bringt keinen scrollbaren Container mit; der übliche Weg ist ein Canvas
    mit einem Fenster darin. Die Leiste ist ein selbst gezeichneter Balken statt
    einer klassischen Tk-Scrollbar, und sie erscheint nur, wenn der Inhalt
    tatsächlich höher ist als der Ausschnitt.
    """

    BAR_WIDTH = 4        # logisch
    BAR_INSET = 3        # logisch
    STEP = 40            # logisch, eine Tastenstufe

    def __init__(self, master: tk.Misc, *, bg_under: str,
                 on_scroll: Optional[Callable[[float], None]] = None) -> None:
        super().__init__(master, bg=bg_under)
        self._bg_under = bg_under
        self._on_scroll = on_scroll
        self._offset = 0

        self.canvas = tk.Canvas(self, bg=bg_under, bd=0, highlightthickness=0,
                                takefocus=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=bg_under)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.bar = tk.Canvas(self, width=px(self.BAR_WIDTH + 2 * self.BAR_INSET),
                             bg=bg_under, bd=0, highlightthickness=0, takefocus=0)

        self.body.bind("<Configure>", self._on_body_configure, add="+")
        self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        # Mausrad nur, solange der Zeiger drin ist — sonst würde der Bereich
        # das Rad auch für andere Fenster abfangen.
        self.canvas.bind("<Enter>", self._grab_wheel, add="+")
        self.canvas.bind("<Leave>", self._release_wheel, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    # ------------------------------------------------------------ Scrollen

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def scrollable(self) -> int:
        view = self.canvas.winfo_height()
        if view <= 1:
            return 0      # noch nicht gelayoutet — sonst erschiene die Leiste grundlos
        return max(0, self.body.winfo_reqheight() - view)

    def scroll_to(self, offset: int) -> None:
        offset = max(0, min(int(offset), self.scrollable))
        if offset != self._offset:
            self._offset = offset
            self.canvas.yview_moveto(offset / max(1, self.body.winfo_reqheight()))
            self._draw_bar()
            if self._on_scroll is not None:
                self._on_scroll(offset)
        elif self._offset == 0:
            self.canvas.yview_moveto(0)

    def scroll_by(self, delta: int) -> None:
        self.scroll_to(self._offset + delta)

    def scroll_page(self, direction: int) -> None:
        self.scroll_by(direction * max(1, self.canvas.winfo_height() - px(self.STEP)))

    def refresh(self) -> None:
        """Neu bewerten, ob die Leiste noch gebraucht wird.

        Nötig, wenn Inhalt entfernt wurde: Tk rechnet die geforderte Höhe eines
        Frames nicht neu, solange kein Configure-Ereignis anfällt.
        """
        self.update_idletasks()
        self.canvas.configure(scrollregion=(0, 0, 0, self.body.winfo_reqheight()))
        self.scroll_to(self._offset)
        self._draw_bar()

    def bind_keys(self, widget: tk.Misc) -> None:
        """Tastaturscrollen an ein Fenster hängen (Pfeile, Bild auf/ab, Pos1/Ende)."""
        widget.bind("<Up>", lambda _e: self.scroll_by(-px(self.STEP)), add="+")
        widget.bind("<Down>", lambda _e: self.scroll_by(px(self.STEP)), add="+")
        widget.bind("<Prior>", lambda _e: self.scroll_page(-1), add="+")
        widget.bind("<Next>", lambda _e: self.scroll_page(1), add="+")
        widget.bind("<Home>", lambda _e: self.scroll_to(0), add="+")
        widget.bind("<End>", lambda _e: self.scroll_to(self.scrollable), add="+")

    def _grab_wheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _release_wheel(self, _event=None) -> None:
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:      # pragma: no cover
            pass

    def _on_wheel(self, event: tk.Event) -> None:
        self.scroll_by(-int(event.delta / 120) * px(self.STEP))

    # ------------------------------------------------------------ Layout

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=(0, 0, 0, self.body.winfo_reqheight()))
        self._draw_bar()

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self.scroll_to(self._offset)      # Position halten, Grenzen neu prüfen
        self._draw_bar()

    def _show_bar(self, visible: bool) -> None:
        """Sichtbarkeit an genau einer Stelle — konsistent mit dem Gezeichneten."""
        if visible and self.bar.winfo_manager() != "pack":
            # `before` ist noetig: der Canvas ist mit expand=True gepackt und
            # liesse der Leiste sonst keine Breite uebrig.
            self.bar.pack(side="right", fill="y", before=self.canvas)
        elif not visible and self.bar.winfo_manager() == "pack":
            self.bar.pack_forget()

    def _draw_bar(self) -> None:
        self.bar.delete("all")
        span = self.body.winfo_reqheight()
        view = self.canvas.winfo_height()
        if span <= view or view <= 1:
            self._show_bar(False)
            return
        self._show_bar(True)
        track = view
        length = max(px(20), int(track * view / span))
        top = int((track - length) * (self._offset / max(1, self.scrollable)))
        image = surface(px(self.BAR_WIDTH), length, Radius.pill(px(self.BAR_WIDTH)),
                        Theme.CONTROL_HOVER, squircle=False)
        self._bar_image = image
        self.bar.create_image(px(self.BAR_INSET), top, anchor="nw", image=image)

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self.bar.configure(width=px(self.BAR_WIDTH + 2 * self.BAR_INSET))
            self._draw_bar()
        except tk.TclError:      # pragma: no cover
            pass

    def _on_theme_change(self) -> None:
        """Nur der gemerkte Untergrund und der Balken.

        Rahmen, Canvas, Körper und Leiste sind gewöhnliche Tk-Widgets im selben
        Baum — deren `bg` hat `retint()` bereits umgeschlüsselt. Die
        Rollposition bleibt unangetastet.
        """
        self._bg_under = Theme.remap(self._bg_under)
        try:
            self._draw_bar()
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)
            self._release_wheel()


class SectionHeader(tk.Label):
    """Abschnittsüberschrift über einer Gruppe."""

    def __init__(self, master: tk.Misc, text: str, *, bg_under: str) -> None:
        super().__init__(master, text=text, bg=bg_under, fg=Theme.TEXT_SECONDARY,
                         font=font("section"), anchor="w")
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self.configure(font=font("section"))
        except tk.TclError:      # pragma: no cover
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)


class GroupSurface(tk.Canvas):
    """Eine abgerundete Fläche, die mehrere Zeilen zusammenfasst.

    Die Zeilen liegen in einem Frame, das um den Eckenradius eingerückt im
    Canvas sitzt — sonst würden seine rechteckigen Ecken die Rundung wieder
    zudecken. Der Einzug ist zugleich der seitliche Innenabstand der Gruppe.
    """

    def __init__(self, master: tk.Misc, *, bg_under: str) -> None:
        super().__init__(master, bg=bg_under, bd=0, highlightthickness=0,
                         takefocus=0, height=px(Radius.GROUP * 2))
        self._bg_under = bg_under
        self._last_size = (0, 0)
        self.body = tk.Frame(self, bg=Theme.GROUP_SURFACE)
        self._window = self.create_window((0, 0), window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_configure, add="+")
        self.body.bind("<Configure>", lambda _e: self._sync_height(), add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    @property
    def inset(self) -> int:
        return px(Radius.GROUP)

    def _sync_height(self) -> None:
        try:
            wanted = self.body.winfo_reqheight() + 2 * self.inset
            if wanted != int(self.cget("height")):
                self.configure(height=wanted)
        except tk.TclError:      # pragma: no cover
            pass

    def _on_configure(self, event: tk.Event) -> None:
        if (event.width, event.height) == self._last_size or event.width <= 1:
            return
        self._last_size = (event.width, event.height)
        inset = self.inset
        self.coords(self._window, inset, inset)
        self.itemconfigure(self._window, width=max(1, event.width - 2 * inset))
        self._render(event.width, event.height)

    def _render(self, width: int, height: int) -> None:
        self.delete("plate")
        image = surface(width, height, px(Radius.GROUP), Theme.GROUP_SURFACE,
                        edge=Theme.EDGE_HIGHLIGHT, squircle=True)
        self._plate = image
        self.create_image(0, 0, anchor="nw", image=image, tags="plate")
        self.tag_lower("plate")

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self._last_size = (0, 0)
            self._sync_height()
            self.event_generate("<Configure>")
        except tk.TclError:      # pragma: no cover
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
        except tk.TclError:      # pragma: no cover - Widget bereits zerstört
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)


class SettingRow(tk.Frame):
    """Eine Einstellungszeile in vier Ausprägungen.

    * `simple`      Titel — Control
    * `description` Titel / Beschreibung — Control
    * `status`      Titel / Statuspunkt + Text — Control
    * `display`     Titel — Wert, kein Control

    Bei `interactive=True` ist die ganze Zeile Auslöser (für Schalter). Bei
    Auswahlzeilen bleibt das ausdrücklich aus: ein Klick auf die Beschreibung
    soll kein Popover öffnen.
    """

    def __init__(self, master: tk.Misc, title: str, *, variant: str = "simple",
                 description: str = "", value: str = "",
                 interactive: bool = False,
                 command: Optional[Callable[[], None]] = None,
                 hairline: bool = True) -> None:
        super().__init__(master, bg=Theme.GROUP_SURFACE)
        self._variant = variant
        self._interactive = interactive
        self._command = command
        self._enabled = True
        self._hovered = False
        self._control: Optional[tk.Misc] = None
        #: Die vollständigen Texte. Angezeigt wird, was in die Zeile passt.
        self._title_text = title
        self._sub_text = description

        tall = variant in ("description", "status")
        self._logical_height = Height.ROW_DETAIL if tall else Height.ROW_SIMPLE

        self._line = tk.Frame(self, bg=Theme.GROUP_SURFACE, height=px(self._logical_height))
        self._line.pack(fill="x")
        self._line.pack_propagate(False)

        self._text_box = tk.Frame(self._line, bg=Theme.GROUP_SURFACE)
        self._text_box.pack(side="left", fill="both", expand=True,
                            padx=(px(Space.ROW_X), 0))

        self.title_label = tk.Label(self._text_box, text=title, bg=Theme.GROUP_SURFACE,
                                    fg=Theme.TEXT_PRIMARY, font=font("row_title"),
                                    anchor="w")
        self.title_label.pack(anchor="w", pady=(px(Space.ROW_Y), 0) if not tall
                              else (px(Space.MD), 0))

        self.sub_box: Optional[tk.Frame] = None
        self.status_dot: Optional[StatusDot] = None
        self.sub_label: Optional[tk.Label] = None
        if tall:
            self.sub_box = tk.Frame(self._text_box, bg=Theme.GROUP_SURFACE)
            self.sub_box.pack(anchor="w", pady=(px(Space.TEXT_GAP), 0))
            if variant == "status":
                self.status_dot = StatusDot(self.sub_box, bg_under=Theme.GROUP_SURFACE,
                                            size=StatusDot.SIZE_ROW)
                self.status_dot.pack(side="left", padx=(0, px(Space.ICON_GAP)))
            self.sub_label = tk.Label(self.sub_box, text=description,
                                      bg=Theme.GROUP_SURFACE, fg=Theme.TEXT_TERTIARY,
                                      font=font("description"), anchor="w")
            self.sub_label.pack(side="left")

        self.value_label: Optional[tk.Label] = None
        if variant == "display":
            self.value_label = tk.Label(self._line, text=value, bg=Theme.GROUP_SURFACE,
                                        fg=Theme.TEXT_SECONDARY, font=font("row_title"),
                                        anchor="e", justify="right")
            self.value_label.pack(side="right", before=self._text_box,
                                  padx=(px(Space.CONTROL_GAP), px(Space.ROW_X)))

        self.hairline: Optional[tk.Frame] = None
        if hairline:
            holder = tk.Frame(self, bg=Theme.GROUP_SURFACE)
            holder.pack(fill="x")
            self.hairline = tk.Frame(holder, bg=Theme.HAIRLINE, height=max(1, px(1)))
            self.hairline.pack(fill="x", padx=(px(Space.ROW_X), 0))

        # Der Textblock bekommt erst nach dem Einhängen des Controls seine
        # endgültige Breite; vorher lässt sich nicht kürzen.
        self._text_box.bind("<Configure>", self._refit, add="+")

        if interactive:
            for widget in self._hover_widgets():
                widget.bind("<Enter>", self._on_enter, add="+")
                widget.bind("<Leave>", self._on_leave, add="+")
                widget.bind("<Button-1>", self._on_click, add="+")
                widget.configure(cursor="hand2")

        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    # ------------------------------------------------------------ Aufbau

    def set_control(self, control: tk.Misc) -> None:
        """Hängt das Control rechts in die Zeile — und löst ein früheres ab.

        Der Austausch ist gewollt: eine Zeile zeigt während eines Downloads
        eine Abbruch-Schaltfläche statt des Auswahlfeldes.
        """
        if self._control is not None and self._control is not control:
            try:
                self._control.pack_forget()
            except tk.TclError:      # pragma: no cover - bereits zerstoert
                pass
        self._control = control
        # `before` ist noetig: der Textblock ist mit expand=True gepackt und
        # bekaeme sonst den ganzen Platz, das Control wuerde abgeschnitten.
        control.pack(in_=self._line, side="right", before=self._text_box,
                     padx=(px(Space.CONTROL_GAP), px(Space.ROW_X)))

    def _hover_widgets(self) -> list:
        widgets = [self._line, self._text_box, self.title_label]
        if self.sub_box is not None:
            widgets += [self.sub_box]
        if self.sub_label is not None:
            widgets += [self.sub_label]
        return widgets

    # ------------------------------------------------------------ Zustand

    def _refit(self, _event=None) -> None:
        """Kürzt Titel und Unterzeile auf den Platz, der wirklich übrig ist.

        Ohne das schnitt der Rahmen den Text mitten im Wort ab — gemessen an
        „Tempo und Genauigkeit“ neben dem Modell-Auswahlfeld. Ein Auslassungs-
        zeichen sagt wenigstens, dass da noch etwas steht.
        """
        try:
            width = self._text_box.winfo_width()
        except tk.TclError:      # pragma: no cover - Zeile wird gerade zerstört
            return
        if width <= 1:
            return
        self.title_label.configure(
            text=_fit_text(self, self._title_text, font("row_title"), width))
        if self.sub_label is None:
            return
        used = 0
        if self.status_dot is not None:
            used = px(StatusDot.SIZE_ROW) + px(Space.ICON_GAP)
        self.sub_label.configure(
            text=_fit_text(self, self._sub_text, font("description"), width - used))

    def set_status(self, status: str, text: str) -> None:
        if self.status_dot is not None:
            self.status_dot.set_status(status)
        self._sub_text = text
        self._refit()

    def set_description(self, text: str) -> None:
        self._sub_text = text
        self._refit()

    def set_value(self, text: str) -> None:
        if self.value_label is not None:
            self.value_label.configure(text=text)

    def set_enabled(self, enabled: bool) -> None:
        """Deaktiviert die Zeile — die Höhe bleibt, damit nichts springt."""
        self._enabled = enabled
        self.title_label.configure(
            fg=Theme.TEXT_PRIMARY if enabled else Theme.TEXT_TERTIARY)
        if self.sub_label is not None:
            self.sub_label.configure(
                fg=Theme.TEXT_TERTIARY if enabled else Theme.CONTROL_HOVER)
        if self.value_label is not None:
            self.value_label.configure(
                fg=Theme.TEXT_SECONDARY if enabled else Theme.TEXT_TERTIARY)
        if self._control is not None and hasattr(self._control, "set_enabled"):
            self._control.set_enabled(enabled)
        if not enabled:
            self._paint(Theme.GROUP_SURFACE)
        for widget in self._hover_widgets() if self._interactive else []:
            widget.configure(cursor="hand2" if enabled else "arrow")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------ Interaktion

    def _paint(self, colour: str) -> None:
        for widget in (self._line, self._text_box, self.title_label,
                       self.sub_box, self.sub_label, self.value_label):
            if widget is not None:
                # Tk-Kinder erben keinen Hintergrund; jedes muss einzeln gesetzt
                # werden, sonst bleiben Textkästchen in der alten Farbe stehen.
                widget.configure(bg=colour)
        if self.status_dot is not None:
            self.status_dot.configure(bg=colour)
        if self._control is not None:
            # Das Control gehoert zur Zeile. Ohne diesen Schritt blieb es beim
            # Hover als Rechteck der alten Farbe stehen — am deutlichsten am
            # Schalter, dessen runde Spur den Untergrund durchscheinen laesst.
            set_surface_under(self._control, colour)

    def _on_enter(self, _event=None) -> None:
        if self._enabled and self._interactive:
            self._hovered = True
            self._paint(Theme.CONTROL_SURFACE)

    def _on_leave(self, _event=None) -> None:
        self._hovered = False
        self._paint(Theme.GROUP_SURFACE)

    def _on_click(self, _event=None) -> str:
        if self._enabled and self._command is not None:
            self._command()
        return "break"      # verhindert, dass der Klick zusätzlich am Control landet

    # ------------------------------------------------------------ Darstellung

    def _on_theme_change(self) -> None:
        """Zeilengrund und Textfarben neu setzen.

        `set_enabled()` ist hier der ehrlichste Weg: dort steht bereits, welche
        Textfarbe zu welchem Zustand gehört, und die Zeile weiss selbst, ob sie
        gerade abgeblendet ist. Danach entscheidet der Hover über die Fläche —
        sonst verlöre eine Zeile unter dem Zeiger beim Wechsel ihre Hervorhebung.
        """
        self.set_enabled(self._enabled)
        hovered = self._hovered and self._enabled and self._interactive
        self._paint(Theme.CONTROL_SURFACE if hovered else Theme.GROUP_SURFACE)

    # ------------------------------------------------------------ DPI

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self._line.configure(height=px(self._logical_height))
            self.title_label.configure(font=font("row_title"))
            if self.sub_label is not None:
                self.sub_label.configure(font=font("description"))
            if self.value_label is not None:
                self.value_label.configure(font=font("row_title"))
            self._refit()
            if self.hairline is not None:
                self.hairline.configure(height=max(1, px(1)))
        except tk.TclError:      # pragma: no cover
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)


# ---------------------------------------------------------------- Select und Popover


class Option:
    """Ein Eintrag einer Auswahlliste.

    `group` fasst Einträge unter einer Überschrift zusammen; `meta` ist die
    kleine zweite Zeile. Beides braucht das Mikrofon noch nicht, der
    Modell-Selector später schon.
    """

    __slots__ = ("value", "label", "meta", "icon", "enabled", "group", "trailing")

    def __init__(self, value, label: str, *, meta: str = "", icon: Optional[str] = None,
                 enabled: bool = True, group: str = "", trailing: str = "") -> None:
        self.value = value
        self.label = label
        self.meta = meta
        self.icon = icon
        self.enabled = enabled
        self.group = group
        self.trailing = trailing


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _Rect),
                ("rcWork", _Rect), ("dwFlags", ctypes.c_ulong)]


MONITORINFOF_PRIMARY = 0x1


def work_area(widget: tk.Misc) -> tuple[int, int, int, int]:
    """Arbeitsfläche des Monitors, auf dem das Fenster liegt.

    `winfo_screenwidth()` kennt weder mehrere Monitore noch die Taskleiste;
    beides zusammen liesse ein Popover am Rand halb im Nichts aufgehen.

    Die Koordinaten sind physische Bildschirmpixel — dieselbe Einheit, in der
    Tk `geometry()` entgegennimmt und `winfo_x()` antwortet.
    """
    try:
        hwnd = ctypes.windll.user32.GetParent(widget.winfo_id()) or widget.winfo_id()
        monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)   # NEAREST
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            area = info.rcWork
            return area.left, area.top, area.right, area.bottom
    except (AttributeError, OSError, tk.TclError):
        pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def work_areas() -> list:
    """Arbeitsflächen **aller** Monitore, der primäre zuerst.

    Dieselben Strukturen wie `work_area()`, nur über alle Bildschirme statt
    über den einen unter einem Fenster. Gebraucht wird das beim Start: eine
    gemerkte Position kann auf einem Monitor liegen, den es nicht mehr gibt.

    Die Liste ist nie leer — schlägt die Abfrage fehl, steht wenigstens der
    Hauptbildschirm darin.
    """
    found: list = []
    primary: list = []

    def _collect(monitor, _dc, _rect, _data) -> int:
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            area = info.rcWork
            entry = (area.left, area.top, area.right, area.bottom)
            if info.dwFlags & MONITORINFOF_PRIMARY:
                primary.append(entry)
            else:
                found.append(entry)
        return 1

    try:
        callback = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(_Rect), ctypes.c_double)(_collect)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, callback, 0)
    except (AttributeError, OSError):      # pragma: no cover - kein Windows
        pass

    areas = primary + found
    if areas:
        return areas
    try:      # pragma: no cover - nur wenn die Aufzaehlung versagt
        width = ctypes.windll.user32.GetSystemMetrics(0)
        height = ctypes.windll.user32.GetSystemMetrics(1)
        return [(0, 0, width, height)]
    except (AttributeError, OSError):
        return [(0, 0, 1920, 1080)]


class _PopoverItem(tk.Frame):
    """Eine Zeile im Popover: Haken, Titel, Meta, rechte Zusatzangabe."""

    def __init__(self, master: tk.Misc, option: Option, *, selected: bool,
                 command: Callable[[Option], None], text_width: int = 0) -> None:
        super().__init__(master, bg=Theme.POPOVER_SURFACE)
        self.option = option
        self._command = command
        self._selected = selected
        self._hovered = False

        # Immer ein Bild setzen — auch bei nicht gewaehlten Eintraegen, dort in
        # Hintergrundfarbe. `width` zaehlt bei einem Label *ohne* Bild in
        # Zeichen, nicht in Pixeln; die Spalte waere sonst um ein Vielfaches zu
        # breit und draengte den Titel aus der Zeile.
        self._check_image = icon(
            "check", ICON_BOX,
            Theme.ACCENT_TINT if selected else Theme.POPOVER_SURFACE)
        self._check = tk.Label(self, bg=Theme.POPOVER_SURFACE, bd=0,
                               image=self._check_image)
        self._check.pack(side="left", padx=(px(Space.MD), px(Space.ICON_GAP)))

        text_box = tk.Frame(self, bg=Theme.POPOVER_SURFACE)
        text_box.pack(side="left", fill="x", expand=True, pady=px(Space.SM))
        colour = Theme.TEXT_PRIMARY if option.enabled else Theme.TEXT_TERTIARY
        title = _fit_text(self, option.label, font("row_title"), text_width)
        self._title = tk.Label(text_box, text=title, bg=Theme.POPOVER_SURFACE,
                               fg=colour, font=font("row_title"), anchor="w")
        self._title.pack(anchor="w")
        self._meta = None
        if option.meta:
            meta = _fit_text(self, option.meta, font("meta"), text_width)
            self._meta = tk.Label(text_box, text=meta, bg=Theme.POPOVER_SURFACE,
                                  fg=Theme.TEXT_SECONDARY, font=font("meta"), anchor="w")
            self._meta.pack(anchor="w")

        self._trailing = None
        if option.trailing:
            self._trailing = tk.Label(self, text=option.trailing,
                                      bg=Theme.POPOVER_SURFACE, fg=Theme.TEXT_SECONDARY,
                                      font=font("meta"))
            # `before`, weil der Textblock mit expand=True gepackt ist und der
            # Zusatzangabe sonst keinen Platz uebrig laesst.
            self._trailing.pack(side="right", before=text_box, padx=(0, px(Space.MD)))

        for widget in self._parts():
            widget.bind("<Enter>", lambda _e: self.set_hover(True), add="+")
            widget.bind("<Leave>", lambda _e: self.set_hover(False), add="+")
            widget.bind("<Button-1>", self._on_click, add="+")
            if option.enabled:
                widget.configure(cursor="hand2")

    def _parts(self) -> list:
        parts = [self, self._check, self._title]
        if self._meta is not None:
            parts.append(self._meta)
        if self._trailing is not None:
            parts.append(self._trailing)
        return parts

    def set_hover(self, hovered: bool) -> None:
        if not self.option.enabled:
            return
        self._hovered = hovered
        colour = Theme.CONTROL_HOVER if hovered else Theme.POPOVER_SURFACE
        for widget in self._parts() + [self._title.master]:
            widget.configure(bg=colour)

    @property
    def hovered(self) -> bool:
        return self._hovered

    def _on_click(self, _event=None) -> str:
        if self.option.enabled:
            self._command(self.option)
        return "break"


class Popover(tk.Toplevel):
    """Schwebende Auswahlliste.

    Tiefe entsteht ohne nativen Schatten — den gibt Windows für randlose
    Fenster nicht her (in Phase 9 gemessen). Stattdessen: eigene hellere
    Fläche, feine Umrandung, Lichtkante oben, Rundung und Abstand.
    """

    MAX_VISIBLE = 8          # Einträge, danach wird gescrollt
    GAP = 6                  # logisch, Abstand zum Auslöser
    ALPHA = 0.98             # sehr dezent; mehr kostet Textkontrast
    MAX_WIDTH = 340          # logisch; darüber wird gekürzt statt verbreitert
    MIN_WIDTH = 200          # logisch
    #: Einblenden über das Fenster-Alpha. Geschlossen wird ohne Übergang:
    #: ein Ausblenden müsste Grab und Fokus überdauern, und das ist es nicht
    #: wert — Funktion vor Symmetrie.
    FADE_MS = 110

    current: Optional["Popover"] = None

    def __init__(self, trigger: tk.Misc, options: list, value, *,
                 on_choose: Callable[[Option], None]) -> None:
        super().__init__(trigger.winfo_toplevel())
        self.trigger = trigger
        self._on_choose = on_choose
        self._items: list = []
        self._index = -1

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.0)      # das Einblenden folgt unten
        except tk.TclError:      # pragma: no cover - Alpha nicht verfügbar
            pass
        self.configure(bg=Theme.HAIRLINE)      # 1 px Rand als äußerer Rahmen

        inner = tk.Frame(self, bg=Theme.POPOVER_SURFACE)
        inner.pack(fill="both", expand=True, padx=max(1, px(1)), pady=max(1, px(1)))
        # Lichtkante: eine Spur heller als die Fläche, nur oben.
        tk.Frame(inner, bg=Theme.EDGE_HIGHLIGHT, height=max(1, px(1))).pack(fill="x")

        self._body_holder = tk.Frame(inner, bg=Theme.POPOVER_SURFACE)
        self._body_holder.pack(fill="both", expand=True, pady=px(Space.SM))
        self._scroll: Optional[ScrollArea] = None
        self._width = self._measure_width(options)
        self._build_items(options, value)

        self.bind("<Escape>", lambda _e: self.dismiss(), add="+")
        self.bind("<Return>", lambda _e: self._choose_current(), add="+")
        self.bind("<Down>", lambda _e: self._move(1), add="+")
        self.bind("<Up>", lambda _e: self._move(-1), add="+")
        self.bind("<Home>", lambda _e: self._move_to(0), add="+")
        self.bind("<End>", lambda _e: self._move_to(len(self._items) - 1), add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<Button-1>", self._on_click_outside, add="+")

        self._place()
        self.update_idletasks()
        round_window_corners(self, Radius.POPOVER)
        self.focus_force()
        self.grab_set()          # lokal, nie global — sonst friert der Desktop ein
        Popover.current = self
        Animator.run(self, "fade", self.FADE_MS,
                     lambda fraction: self.attributes("-alpha", self.ALPHA * fraction))

    # ------------------------------------------------------------ Aufbau

    def _measure_width(self, options: list) -> int:
        """Breite aus dem Inhalt, begrenzt auf `MAX_WIDTH`.

        Ohne Deckel machte ein einziger langer Gerätename das Popover doppelt so
        breit wie nötig; ohne Untergrenze wirkte es bei kurzen Namen gequetscht.
        """
        self._trailing_width = 0
        widest = 0
        for option in options:
            widest = max(widest, _text_width(self, option.label, font("row_title")))
            if option.meta:
                widest = max(widest, _text_width(self, option.meta, font("meta")))
        self._trailing_width = max(
            (_text_width(self, o.trailing, font("meta")) for o in options if o.trailing),
            default=0)
        content = widest + self._chrome_width()
        floor = max(px(self.MIN_WIDTH), self.trigger.winfo_width())
        return max(floor, min(content, px(self.MAX_WIDTH)))

    def _chrome_width(self) -> int:
        """Alles ausser dem Text: Haken, Abstände und die rechte Zusatzangabe."""
        return (px(ICON_BOX) + px(Space.ICON_GAP) + 3 * px(Space.MD)
                + getattr(self, "_trailing_width", 0))

    def _text_budget(self) -> int:
        """Platz, der einer Zeile für Text bleibt.

        Die Zusatzangabe rechts muss mitgerechnet werden — sonst schiebt sich
        die Meta-Zeile darunter und beide überlappen.
        """
        # Untergrenze zusaetzlich als Anteil der Breite: so bleibt der Titel
        # lesbar, auch wenn die Zusatzangabe ungewoehnlich breit ausfaellt.
        return max(px(60), int(self._width * 0.55),
                   self._width - self._chrome_width())

    def _build_items(self, options: list, value) -> None:
        rows = sum(1 for option in options)
        groups = sorted({option.group for option in options if option.group})
        needs_scroll = rows > self.MAX_VISIBLE

        if needs_scroll:
            self._scroll = ScrollArea(self._body_holder, bg_under=Theme.POPOVER_SURFACE)
            self._scroll.pack(fill="both", expand=True)
            container = self._scroll.body
            container.configure(bg=Theme.POPOVER_SURFACE)
        else:
            container = self._body_holder

        def add_group(name: str) -> None:
            tk.Label(container, text=name.upper(), bg=Theme.POPOVER_SURFACE,
                     fg=Theme.TEXT_SECONDARY, font=font("meta"), anchor="w").pack(
                fill="x", padx=(px(Space.MD), 0), pady=(px(Space.SM), px(Space.XS)))

        if groups:
            for name in groups:
                add_group(name)
                for option in [o for o in options if o.group == name]:
                    self._add_item(container, option, value)
            for option in [o for o in options if not o.group]:
                self._add_item(container, option, value)
        else:
            for option in options:
                self._add_item(container, option, value)

    def _add_item(self, container: tk.Misc, option: Option, value) -> None:
        item = _PopoverItem(container, option, selected=option.value == value,
                            command=self._choose, text_width=self._text_budget())
        item.pack(fill="x")
        self._items.append(item)
        if option.value == value:
            self._index = len(self._items) - 1

    # ------------------------------------------------------------ Platzierung

    def _place(self) -> None:
        self.update_idletasks()
        width = self._width
        height = self.winfo_reqheight()
        left, top, right, bottom = work_area(self.trigger)

        gap = px(self.GAP)
        x = self.trigger.winfo_rootx()
        y = self.trigger.winfo_rooty() + self.trigger.winfo_height() + gap

        if y + height > bottom:                       # unten kein Platz -> nach oben
            above = self.trigger.winfo_rooty() - height - gap
            y = above if above >= top else max(top, bottom - height)
        if x + width > right:                         # rechts kein Platz -> einrücken
            x = right - width
        x = max(left, x)
        # Zum Schluss beidseitig einfangen. Der Umschlag nach oben allein genügt
        # nicht: wird das Fenster so weit nach unten gezogen, dass der Auslöser
        # selbst unter der Arbeitsfläche liegt, landete das Popover sonst
        # ebenfalls dort (gemessen: 345 px unterhalb des Randes).
        y = max(top, min(y, bottom - height))
        self.geometry(f"{width}x{height}+{int(x)}+{int(y)}")

    # ------------------------------------------------------------ Auswahl

    def _move(self, delta: int) -> str:
        usable = [i for i, item in enumerate(self._items) if item.option.enabled]
        if not usable:
            return "break"
        if self._index in usable:
            position = usable.index(self._index)
            position = (position + delta) % len(usable)
        else:
            position = 0 if delta > 0 else len(usable) - 1
        return self._move_to(usable[position])

    def _move_to(self, index: int) -> str:
        if not self._items:
            return "break"
        index = max(0, min(index, len(self._items) - 1))
        for position, item in enumerate(self._items):
            item.set_hover(position == index)
        self._index = index
        return "break"

    def _choose_current(self) -> str:
        if 0 <= self._index < len(self._items):
            item = self._items[self._index]
            if item.option.enabled:
                self._choose(item.option)
        return "break"

    def _choose(self, option: Option) -> None:
        callback = self._on_choose
        self.dismiss()
        callback(option)

    def _on_focus_out(self, _event=None) -> None:
        # Klick in ein *fremdes* Fenster: Tk meldet Fokusverlust am Popover.
        if Popover.current is self:
            self.after(1, self._dismiss_if_unfocused)

    def _on_click_outside(self, event) -> str:
        """Ein Klick neben das Popover schliesst es.

        Der lokale Grab leitet jeden Klick der Anwendung hierher — auch den auf
        ein anderes Bedienelement oder auf freie Fläche im Einstellungsfenster.
        `FocusOut` greift dabei nicht, denn innerhalb derselben Anwendung
        wandert der Fokus gar nicht. Ohne diese Prüfung versackte so ein Klick
        wirkungslos, und das Popover liess sich nur über einen Eintrag oder
        Escape wieder loswerden.

        Der schliessende Klick wird verbraucht und nicht an das Bedienelement
        darunter weitergereicht — genauso verhalten sich Menüs unter Windows.
        """
        if Popover.current is not self:
            return ""
        left, top = self.winfo_rootx(), self.winfo_rooty()
        inside = (left <= event.x_root < left + self.winfo_width()
                  and top <= event.y_root < top + self.winfo_height())
        if not inside:
            self.dismiss()
            return "break"
        return ""

    def _dismiss_if_unfocused(self) -> None:
        try:
            if self.focus_displayof() is None:
                self.dismiss()
        except (tk.TclError, KeyError):      # pragma: no cover
            self.dismiss()

    def _on_theme_change(self) -> None:
        """Beim Moduswechsel schliessen statt live umfärben.

        Der robustere der beiden Wege: ein schwebendes Toplevel trägt Grab,
        Fokus, eigenes Alpha und abgerundete Fensterecken. Es mitten im
        Wechsel umzufärben hiesse, all das zu halten, während unter ihm die
        Palette wechselt. Es zu schliessen und den Fokus an den Auslöser
        zurückzugeben ist eindeutig — und der Wechsel wurde ohnehin gerade vom
        Nutzer im Einstellungsfenster ausgelöst.
        """
        self.dismiss()

    def dismiss(self) -> None:
        """Schliessen und den Fokus an den Auslöser zurückgeben."""
        if Popover.current is self:
            Popover.current = None
        try:
            self.grab_release()
        except tk.TclError:      # pragma: no cover
            pass
        trigger = self.trigger
        try:
            self.destroy()
        except tk.TclError:      # pragma: no cover
            pass
        return_focus_to(trigger)

    @classmethod
    def dismiss_current(cls) -> None:
        """Es darf immer nur ein Popover offen sein."""
        if cls.current is not None:
            cls.current.dismiss()


class Select(BaseControl):
    """Auswahlfeld mit eigenem Popover — Ersatz für das alte tk.Menu.

    Ein- oder zweizeilig; der Chevron ist ein echtes Icon und sitzt immer
    rechtsbündig, statt als Zeichen an den Text gehängt zu werden.
    """

    def __init__(self, master: tk.Misc, *, bg_under: str,
                 options: Optional[list] = None, value=None,
                 on_change: Optional[Callable[[object], None]] = None,
                 two_line: bool = False, placeholder: str = "—") -> None:
        # Nicht `_options` nennen: das ist eine Methode von tkinter.Misc und
        # wuerde vor super().__init__() ueberschrieben.
        self._option_list: list = options or []
        self._value = value
        self._on_change = on_change
        self._two_line = two_line
        self._placeholder = placeholder
        height = Height.ROW_DETAIL - 12 if two_line else Height.STANDARD
        super().__init__(master, bg_under=bg_under, role="secondary",
                         height=height, radius=Radius.CONTROL,
                         command=self.toggle_popover, font_role="row_title")

    # ------------------------------------------------------------ Daten

    def set_options(self, options: list) -> None:
        self._option_list = list(options)
        self._render()

    def set_value(self, value, notify: bool = False) -> None:
        self._value = value
        self._render()
        if notify and self._on_change is not None:
            self._on_change(value)

    @property
    def value(self):
        return self._value

    @property
    def options(self) -> list:
        return list(self._option_list)

    def _selected(self) -> Optional[Option]:
        for option in self._option_list:
            if option.value == self._value:
                return option
        return None

    # ------------------------------------------------------------ Popover

    @property
    def is_open(self) -> bool:
        return Popover.current is not None and Popover.current.trigger is self

    def toggle_popover(self) -> None:
        if self.is_open:
            Popover.dismiss_current()
        else:
            self.open_popover()

    def open_popover(self) -> None:
        if not State.is_interactive(self._states) or not self._option_list:
            return
        Popover.dismiss_current()       # nie zwei gleichzeitig
        Popover(self, self._option_list, self._value, on_choose=self._on_choose)
        self.set_state(State.SELECTED, True)

    def _on_choose(self, option: Option) -> None:
        self.set_state(State.SELECTED, False)
        if option.value != self._value:
            self.set_value(option.value, notify=True)
        else:
            self._render()

    # ------------------------------------------------------------ Zeichnen

    def _paint(self, width: int, height: int) -> ImageTk.PhotoImage:
        fill, foreground = self.colours()
        ring_w, ring_gap = px(ControlStyle.RING_WIDTH), px(ControlStyle.RING_GAP)
        plate = ImageTk.getimage(surface(
            width, height + 2 * (ring_w + ring_gap), px(self._logical_radius),
            fill if fill is not None else self._bg_under,
            edge=Theme.EDGE_HIGHLIGHT, gradient=0.02, squircle=True,
            ring=self.ring(), ring_width=ring_w, ring_gap=ring_gap,
        ))

        chevron_size = px(ICON_BOX)
        glyph = render_icon("chevron", chevron_size, foreground)
        plate.paste(glyph, (plate.width - chevron_size - px(Space.MD),
                            (plate.height - chevron_size) // 2), glyph)

        selected = self._selected()
        label = selected.label if selected is not None else self._placeholder
        meta = selected.meta if selected is not None and self._two_line else ""
        self.configure(text="")
        self._draw_text(plate, label, meta, foreground, chevron_size)
        composed = ImageTk.PhotoImage(plate)
        self._composed = composed
        return composed

    def _draw_text(self, plate, label: str, meta: str, colour: str,
                   chevron: int) -> None:
        """Beschriftung ins Bild setzen — so bleibt der Chevron rechtsbündig."""
        available = plate.width - chevron - 3 * px(Space.MD)
        draw = ImageDraw.Draw(plate)
        title_font = _pil_font(font("row_title"))
        meta_font = _pil_font(font("meta"))
        x = px(Space.MD)
        if meta:
            total = title_font.size + meta_font.size + px(2)
            top = (plate.height - total) // 2
            draw.text((x, top), _ellipsize(draw, label, title_font, available),
                      font=title_font, fill=colour)
            draw.text((x, top + title_font.size + px(2)),
                      _ellipsize(draw, meta, meta_font, available),
                      font=meta_font, fill=Theme.TEXT_SECONDARY)
        else:
            text = _ellipsize(draw, label, title_font, available)
            box = draw.textbbox((0, 0), text, font=title_font)
            draw.text((x, (plate.height - (box[3] - box[1])) // 2 - box[1]),
                      text, font=title_font, fill=colour)


def _text_width(widget: tk.Misc, text: str, tk_font) -> int:
    """Textbreite in Pixeln, über Tk gemessen."""
    try:
        return int(widget.tk.call("font", "measure", tk_font, text))
    except tk.TclError:      # pragma: no cover
        return len(text) * px(7)


def _fit_text(widget: tk.Misc, text: str, tk_font, available: int) -> str:
    """Kürzt mit Auslassungspunkten, wenn der Text nicht passt."""
    if available <= 0 or _text_width(widget, text, tk_font) <= available:
        return text
    ellipsis = "…"
    while text and _text_width(widget, text + ellipsis, tk_font) > available:
        text = text[:-1]
    return text + ellipsis


def _pil_font(tk_font):
    """Tk-Schriftrolle als PIL-Schrift — für Text, der ins Bild gezeichnet wird."""
    try:
        family = tk_font.cget("family")
        size = abs(int(tk_font.cget("size")))
    except AttributeError:
        family, size = "Segoe UI", px(12)
    for candidate in (family, "Segoe UI Variable Text", "Segoe UI", "arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()      # pragma: no cover


def _ellipsize(draw, text: str, pil_font, available: int) -> str:
    """Kürzt mit Auslassungspunkten, statt den Text hart abzuschneiden."""
    if draw.textlength(text, font=pil_font) <= available:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=pil_font) > available:
        text = text[:-1]
    return text + ellipsis


# --- Fensterecken -----------------------------------------------------------
#
# Gemessen an einem randlosen Tk-Fenster (WS_POPUP ohne Rahmen) unter
# Windows 11 Build 26200:
#
#   DWMWA_WINDOW_CORNER_PREFERENCE   wirkt      -> Ecken werden vom Compositor
#                                                 gerundet und kantengeglaettet
#   DWMWA_BORDER_COLOR               wirkungslos -> HRESULT 0, aber kein Pixel
#                                                 aendert sich; ohne Fensterrahmen
#                                                 zeichnet DWM keinen Rand
#   DwmExtendFrameIntoClientArea     wirkungslos -> kein Schatten
#   CS_DROPSHADOW am Klassenstil     wirkungslos -> kein Schatten
#
# Deshalb wird hier ausschliesslich die Eckenrundung nativ gesetzt. Ein Rand
# muesste als eigene Hairline im Fensterinhalt gezeichnet werden; ein Schatten
# braeuchte zusaetzliche Ebenenfenster, was der Plan ausdruecklich ausschliesst.

_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2
_WIN11_BUILD = 22000


def _windows_build() -> int:
    """Build-Nummer über RtlGetVersion — GetVersionEx lügt ohne Manifest."""
    class _Info(ctypes.Structure):
        _fields_ = [("dwOSVersionInfoSize", ctypes.c_ulong),
                    ("dwMajorVersion", ctypes.c_ulong),
                    ("dwMinorVersion", ctypes.c_ulong),
                    ("dwBuildNumber", ctypes.c_ulong),
                    ("dwPlatformId", ctypes.c_ulong),
                    ("szCSDVersion", ctypes.c_wchar * 128)]

    try:
        info = _Info()
        info.dwOSVersionInfoSize = ctypes.sizeof(info)
        ctypes.windll.ntdll.RtlGetVersion(ctypes.byref(info))
        return int(info.dwBuildNumber)
    except (AttributeError, OSError):
        return 0


def _window_handle(root: tk.Misc) -> int:
    return ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()


def round_window_corners(root: tk.Misc, radius: int = Radius.WINDOW) -> str:
    """Rundet die Fensterecken. Gibt den benutzten Weg zurück (für das Log).

    Auf Windows 11 übernimmt das der Compositor: die Ecke wird kantengeglättet
    und passt sich der Systemdarstellung an. Darunter bleibt der bisherige Weg
    über `SetWindowRgn`, der mit einer 1-Bit-Maske schneidet und deshalb harte
    Treppen erzeugt.

    Beide Verfahren werden nie gleichzeitig angewandt — die Region wird vor dem
    DWM-Weg ausdrücklich entfernt.
    """
    try:
        root.update_idletasks()
        hwnd = _window_handle(root)
        if not hwnd:
            return "keine"
    except tk.TclError:      # pragma: no cover - Fenster bereits zerstört
        return "keine"

    if _windows_build() >= _WIN11_BUILD:
        try:
            ctypes.windll.user32.SetWindowRgn(hwnd, None, True)   # Maske weg
            preference = ctypes.c_int(_DWMWCP_ROUND)
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(_DWMWA_WINDOW_CORNER_PREFERENCE),
                ctypes.byref(preference), ctypes.sizeof(preference),
            )
            if result == 0:
                return "dwm"
        except (AttributeError, OSError):
            pass      # fällt unten auf die Region zurück

    try:
        width, height = root.winfo_width(), root.winfo_height()
        if width <= 1 or height <= 1:
            return "keine"
        scaled = px(radius)
        region = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, scaled * 2, scaled * 2)
        ctypes.windll.user32.SetWindowRgn(hwnd, region, True)
        return "region"
    except (AttributeError, OSError, tk.TclError):
        return "keine"      # Rundung ist kosmetisch — nie den Start blockieren


def separator(master: tk.Misc, bg_under: str) -> tk.Frame:
    line = tk.Frame(master, bg=Theme.HAIRLINE, height=px(1))
    return line
