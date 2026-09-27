"""Farben und Masse des Mosaik-Designs: Theme, Space, Radius, Height, Umfaerben."""

from __future__ import annotations

import contextlib
import tkinter as tk

from wisper.ui_kit.caches import _icon_cache, _img_cache


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
    dunkler, weil leuchtende Systemtoene wie `#30D158` oder `#FF9F0A` auf
    hellem Grund nur rund 1,7:1 bis 1,9:1 erreichen und damit selbst als Punkt
    durchfielen.

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
            # --- Flaechen: warmes Dunkel, vier Ebenen mit kleinen Schritten --
            "WINDOW_BG": "#1F1E1D",        # Fenstergrund
            "GROUP_SURFACE": "#2A2927",    # Einstellungsgruppen, 1,15:1
            "CONTROL_SURFACE": "#363532",  # Controls, deaktivierte Flaechen
            "CONTROL_HOVER": "#42413D",    # Control unter dem Zeiger
            "POPOVER_SURFACE": "#302F2C",  # schwebende Ebene
            "HAIRLINE": "#3D3C38",         # Trennlinie und Gruppenrand
            "CONTROL_OUTLINE": "#8C887E",  # >= 3:1 auf GROUP_SURFACE — Schalterspur

            # --- Text: warmes Weiss, keine kalten Grautoene --------------------
            "TEXT_PRIMARY": "#F5F4EE",
            "TEXT_SECONDARY": "#B8B4A9",   # 8,04:1 auf WINDOW_BG
            "TEXT_TERTIARY": "#9D998E",    # 5,85:1 auf WINDOW_BG, 5,11:1 auf Gruppe

            # --- Akzent: Terrakotta ------------------------------------------
            "ACCENT_SURFACE": "#B4532F",   # Fuellung, ON_ACCENT 4,98:1
            "ACCENT_HOVER": "#A34A29",
            "ACCENT_PRESSED": "#8F4124",
            "ACCENT_TINT": "#D97757",      # Punkte, Icons, Fokusring; 5,33:1

            # --- Semantik ----------------------------------------------------
            "SUCCESS": "#7DBE7A",          # Salbeigruen
            "WARNING": "#E3A94B",          # Ocker
            "ERROR": "#F0806A",            # 6,34:1 — traegt auch als Text
            "ERROR_SURFACE": "#B0392A",    # gefuellt, ON_FILL 6,06:1
            "RECORDING": "#F0806A",        # eigene Semantik, gleicher Ton wie ERROR
            "RECORDING_SURFACE": "#B0392A",
            "RECORDING_HOVER": "#9C3225",
            "RECORDING_PRESSED": "#872B20",
            "AI": "#C49AD6",               # gedaempftes Heidekraut, 7,09:1
            "AI_SURFACE": "#8A5A9E",       # gefuellt, ON_FILL 5,20:1
            "AI_HOVER": "#7B508D",
            "AI_PRESSED": "#6B457B",

            # --- Text auf gefuellten Flaechen --------------------------------
            "ON_ACCENT": "#FFFFFF",
            "ON_FILL": "#FFFFFF",          # auf Rot, Violett und Fehlerflaechen
            "ON_SUCCESS": "#14210F",       # 7,59:1 auf SUCCESS
            "KNOB": "#FFFFFF",             # Schaltknopf des Toggles
        },
        LIGHT: {
            # --- Flaechen: Creme statt Weiss ---------------------------------
            # Dieselbe Bauart wie im Dunkeln, nur andersherum: der Fenstergrund
            # ist die ruhigste Ebene, jede weitere tritt einen kleinen Schritt
            # nach unten heraus. Alle Toene warm, wie ungebleichtes Papier.
            "WINDOW_BG": "#FAF9F5",
            "GROUP_SURFACE": "#F0EEE6",    # 1,10:1 gegen den Fenstergrund
            "CONTROL_SURFACE": "#E3E0D5",  # 1,14:1 gegen die Gruppenflaeche
            "CONTROL_HOVER": "#D6D2C5",    # dunkler, nicht heller
            # Die schwebende Ebene ist die einzige fast weisse Flaeche; ihre
            # Kante traegt der 1-px-Rahmen in HAIRLINE.
            "POPOVER_SURFACE": "#FFFEFB",
            "HAIRLINE": "#DAD6CA",         # Trennlinie und Gruppenrand
            "CONTROL_OUTLINE": "#8A857A",  # >= 3:1 auf GROUP_SURFACE

            # --- Text: warmes Tintenschwarz ----------------------------------
            # Einen Hauch vom dunklen WINDOW_BG (#1F1E1D) entfernt: `remap`
            # erkennt Tokens am Wert, und ein Wert mit zwei Rollen bliebe beim
            # Wechsel nach Dunkel stehen — dunkle Schrift auf dunklem Grund.
            "TEXT_PRIMARY": "#1F1E1C",
            "TEXT_SECONDARY": "#56534C",   # 7,28:1 auf WINDOW_BG
            "TEXT_TERTIARY": "#67635A",    # 5,68:1 auf WINDOW_BG, 5,15:1 auf Gruppe

            # --- Akzent: Terrakotta ------------------------------------------
            "ACCENT_SURFACE": "#B4532F",   # dieselbe Fuellung wie im Dunkeln
            "ACCENT_HOVER": "#A34A29",
            "ACCENT_PRESSED": "#8F4124",
            # Dunkler als im Dunkeln: #D97757 erreicht auf Creme nur 2,9:1.
            "ACCENT_TINT": "#AD4F2C",      # 5,07:1 auf WINDOW_BG

            # --- Semantik ----------------------------------------------------
            "SUCCESS": "#3B7A3E",
            "WARNING": "#8F5B12",
            "ERROR": "#B3261E",            # 6,20:1 — traegt auch als Text
            "ERROR_SURFACE": "#A8231C",    # gefuellt, ON_FILL 7,19:1
            "RECORDING": "#B3261E",
            "RECORDING_SURFACE": "#A8231C",
            "RECORDING_HOVER": "#961F19",
            "RECORDING_PRESSED": "#801A15",
            "AI": "#7A4A8C",               # 6,26:1
            "AI_SURFACE": "#6E4380",
            "AI_HOVER": "#613A71",
            "AI_PRESSED": "#533262",

            # --- Text auf gefuellten Flaechen --------------------------------
            "ON_ACCENT": "#FFFFFF",
            "ON_FILL": "#FFFFFF",
            "ON_SUCCESS": "#FFFFFF",       # 5,20:1 auf SUCCESS
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
    def palette(cls, mode: str | None = None) -> dict:
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
        with contextlib.suppress(ValueError):
            cls._callbacks.remove(callback)

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
    WINDOW = 20        # Fensterrand
    SECTION_GAP = 24   # zwischen zwei Abschnitten
    GROUP_GAP = 8      # zwischen Überschrift und Gruppe
    GROUP_INSET = 4    # Gruppenrand bis Zeile; die Zeile bleibt innerhalb der Rundung
    ROW_X = 12         # Zeileninnenabstand waagerecht (plus Gruppeneinzug = 16)
    ROW_Y = 12         # Zeileninnenabstand senkrecht, oben und unten gleich
    CONTROL_GAP = 12   # zwischen Text und Control
    STACK_GAP = 8      # zwischen Text und einem darunter stehenden Control
    KEY_GAP = 4        # zwischen zwei Tasten-Chips
    TEXT_GAP = 2       # zwischen Titel und Beschreibung (optisch, Teil der Zeile)
    ICON_GAP = 8       # zwischen Icon und Text


class Radius:
    """Verwandte Radienfamilie: innen = außen − Abstand."""

    WINDOW = 12
    POPOVER = 12
    GROUP = 10
    CONTROL = 8
    BUTTON = 8
    KEYCAP = 4
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

    KEYCAP = 22         # Tasten-Chip; Anzeige, kein Control

    #: Mindesthöhe einer einzeiligen Einstellungszeile: Titel plus einzeilige
    #: Beschreibung (21 + 2 + 20 = 43 px bei 100 %, gerundet auf 44) plus
    #: `Space.ROW_Y` oben und unten. Das Standard-Control mit Fokusring-Rand
    #: (32 + 2·4 = 40) passt darunter. So sind Zeilen mit und ohne
    #: Beschreibung, mit Schalter, Auswahl oder Tasten gleich hoch. Mehr
    #: Inhalt macht die Zeile höher, nie enger.
    ROW_MIN = 68
