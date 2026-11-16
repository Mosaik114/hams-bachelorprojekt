"""Farben und Masse des Mosaik-Designs: Theme, Space, Radius, Height, Umfaerben."""

from __future__ import annotations

import contextlib
import tkinter as tk

from hams.ui_kit.caches import _icon_cache, _img_cache


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
            # --- Flaechen: Nachthimmel wie Loratu, vier Ebenen mit kleinen Schritten --
            "WINDOW_BG": "#0E1525",  # Fenstergrund
            "GROUP_SURFACE": "#172036",  # Papierplatte, 1,13:1
            "CONTROL_SURFACE": "#212C46",  # Steine, deaktivierte Flaechen
            "CONTROL_HOVER": "#2B3857",  # Stein unter dem Zeiger
            "CONTROL_PRESSED": "#1C263F",  # gedrückt: sinkt ein, wird dunkler
            "POPOVER_SURFACE": "#1D2741",  # schwebende Ebene
            "HAIRLINE": "#2E3A57",  # Trennlinie
            "CONTROL_OUTLINE": "#8A93AA",  # >= 3:1 auf GROUP_SURFACE — Schalterspur
            # --- Tusche: Rand und Seite der gemalten Flaechen -----------------
            "INK": "#3A4666",  # Tuscherand: Mondlicht-Kante, 1,95:1 zum Grund
            "SCROLL_THUMB": "#6A7390",  # Daumen der Bildlaufleiste, 3,9:1 zum Grund
            "GROUP_SIDE": "#2A3552",  # Seite unter einer Platte, 1,5:1 zum Grund
            "CONTROL_SIDE": "#303C5C",  # Seite unter einem Stein
            "ACCENT_SIDE": "#06453F",
            "RECORDING_SIDE": "#6A1E1E",
            "ERROR_SIDE": "#5E1818",
            "AI_SIDE": "#4E3159",
            # --- Text: Mondweiss ----------------------------------------------
            "TEXT_PRIMARY": "#F4F1EA",
            "TEXT_SECONDARY": "#CDD3E0",  # 12,1:1 auf WINDOW_BG
            "TEXT_TERTIARY": "#939CB1",  # im Grau klar von SUCCESS getrennt
            "TEXT_DISABLED": "#8890A6",  # ausgegraut, aber lesbar: 5,1:1 auf der Platte
            # --- Akzent: Tuerkis ---------------------------------------------
            # Markenton #0E8C80 (hell) / #2AC8A6 (dunkel). Weiss auf #0E8C80
            # ergaebe nur 4,13:1 — die Fuellung liegt deshalb etwas dunkler.
            "ACCENT_SURFACE": "#0B7D72",  # Fuellung, ON_ACCENT 5,01:1
            "ACCENT_HOVER": "#0A7167",  # ON_ACCENT 5,88:1
            "ACCENT_PRESSED": "#09635B",  # ON_ACCENT 7,12:1
            "ACCENT_TINT": "#2CC4A8",  # Punkte, Icons; 8,3:1
            "FOCUS": "#2CC4A8",  # Tuscherand eines Controls mit Tastaturfokus
            # --- Semantik ----------------------------------------------------
            "SUCCESS": "#7DBE7A",  # Salbeigruen
            "WARNING": "#E3A94B",  # Ocker
            # Reines Rot (Farbton 0 Grad), kein Lachs: ein Ton nahe 10 Grad
            # liest sich neben dem Tuerkis wie das fruehere Orange.
            "ERROR": "#F37C7C",  # 6,33:1, auf CONTROL_SURFACE 4,66:1 — traegt als Text
            "ERROR_SURFACE": "#B03030",  # gefuellt, ON_FILL 6,34:1
            "RECORDING": "#F37C7C",  # eigene Semantik, gleicher Ton wie ERROR
            "RECORDING_SURFACE": "#B03030",
            "RECORDING_HOVER": "#9C2B2B",  # ON_FILL 7,52:1
            "RECORDING_PRESSED": "#862525",  # ON_FILL 9,11:1
            "AI": "#C49AD6",  # gedaempftes Heidekraut, 7,09:1
            "AI_SURFACE": "#8A5A9E",  # gefuellt, ON_FILL 5,20:1
            "AI_HOVER": "#7B508D",
            "AI_PRESSED": "#6B457B",
            # --- Text auf gefuellten Flaechen --------------------------------
            "ON_ACCENT": "#FFFFFF",
            "ON_FILL": "#FFFFFF",  # auf Rot, Violett und Fehlerflaechen
            "ON_SUCCESS": "#14210F",  # 7,59:1 auf SUCCESS
            "KNOB": "#FFFFFF",  # Schaltknopf des Toggles
        },
        LIGHT: {
            # --- Flaechen: Kalkweiss -------------------------------------------
            # Fast neutral mit einem Hauch Waerme; das fruehere Creme-Papier war zu
            # gelb. Der Fenstergrund ist Kalk; die Platten darauf sind weiss, die
            # Steine darauf wieder dunkler. Die Stufen sind klein (Platte zu Grund
            # 1,09:1) — getragen wird die Tiefe vom Tuscherand und der Seite.
            "WINDOW_BG": "#F5F4F1",
            # Weiss, aber nicht #FFFFFF: das ist schon ON_ACCENT/ON_FILL/KNOB, und
            # `Theme.remap()` braucht je Wert genau ein Token (sonst wird eine
            # Platte nach dem Wechsel auf dunkel zum weissen Knopf).
            "GROUP_SURFACE": "#FEFEFD",  # Platte
            "CONTROL_SURFACE": "#F0EEEA",  # Stein, 1,15:1 gegen die Platte
            "CONTROL_HOVER": "#E4E1DD",  # dunkler, nicht heller; 1,12:1 zum Stein
            "CONTROL_PRESSED": "#D4D0CA",  # gedrückt: noch dunkler, Text 9,2:1
            # Die schwebende Ebene ist die hellste Flaeche (aus demselben Grund
            # wie bei der Platte nicht #FFFFFF).
            "POPOVER_SURFACE": "#FFFFFE",
            "HAIRLINE": "#EEECE8",  # Trennlinie, 1,17:1 auf der Platte
            "CONTROL_OUTLINE": "#888279",  # 3,8:1 auf GROUP_SURFACE
            # --- Tusche: Rand und Seite der gemalten Flaechen -----------------
            "INK": "#A09D98",  # Tuscherand: helles, fast neutrales Grau, 2,5:1 zum Grund
            "SCROLL_THUMB": "#87827B",  # Daumen der Bildlaufleiste, 3,5:1 zum Grund
            "GROUP_SIDE": "#DEDBD5",  # Seite unter einer Platte, 1,26:1 zum Grund
            "CONTROL_SIDE": "#CFCCC7",  # Seite unter einem Stein, 1,38:1 zum Stein
            "ACCENT_SIDE": "#075A52",
            "RECORDING_SIDE": "#6E1712",
            "ERROR_SIDE": "#7A1A14",
            "AI_SIDE": "#4A2C57",
            # --- Text: Tinte -------------------------------------------------
            "TEXT_PRIMARY": "#1C2B45",
            "TEXT_SECONDARY": "#4C5568",  # 6,8:1 auf WINDOW_BG
            # Tertiaer- und Ausgegraut-Text halten ihren Kontrast zur Platte (6,1:1 und 4,8:1). Heller darf der
            # Tertiaertext nicht werden: als Punkt „untaetig“ muss er sich in Grau vom Gruen „bereit“ abheben.
            "TEXT_TERTIARY": "#5A6274",  # 5,6:1 auf WINDOW_BG, 6,1:1 auf Platte
            "TEXT_DISABLED": "#6C7282",  # ausgegraut, aber lesbar: 4,8:1 auf der Platte
            # --- Akzent: Tuerkis ---------------------------------------------
            "ACCENT_SURFACE": "#0B7D72",  # dieselbe Fuellung wie im Dunkeln
            "ACCENT_HOVER": "#0A7167",
            "ACCENT_PRESSED": "#09635B",
            # Dunkler als der Markenton: #0E8C80 erreicht auf dem Grund nur 3,76:1,
            # auf der Hoverflaeche 3,17:1.
            "ACCENT_TINT": "#0A786E",  # 4,86:1 auf WINDOW_BG, 4,11:1 auf CONTROL_HOVER
            "FOCUS": "#0A786E",  # Tuscherand eines Controls mit Tastaturfokus
            # --- Semantik ----------------------------------------------------
            "SUCCESS": "#3B7A3E",
            "WARNING": "#8F5B12",
            "ERROR": "#B3261E",  # 6,5:1 auf der Platte — traegt auch als Text
            "ERROR_SURFACE": "#A8231C",  # gefuellt, ON_FILL 7,19:1
            "RECORDING": "#B3261E",
            "RECORDING_SURFACE": "#A8231C",
            "RECORDING_HOVER": "#961F19",
            "RECORDING_PRESSED": "#801A15",
            "AI": "#7A4A8C",  # 6,5:1 auf der Platte
            "AI_SURFACE": "#6E4380",
            "AI_HOVER": "#613A71",
            "AI_PRESSED": "#533262",
            # --- Text auf gefuellten Flaechen --------------------------------
            "ON_ACCENT": "#FFFFFF",
            "ON_FILL": "#FFFFFF",
            "ON_SUCCESS": "#FFFFFF",  # 5,20:1 auf SUCCESS
            "KNOB": "#FFFFFF",
        },
    }

    #: Farbwert -> Tokenname, getrennt je Palette. Getrennt, weil derselbe Wert
    #: in den beiden Paletten unterschiedliche Rollen tragen kann.
    _INDEX: dict = {
        mode: {value.upper(): name for name, value in palette.items()} for mode, palette in _PALETTES.items()
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
    CONTROL_PRESSED = _PALETTES[DEFAULT_MODE]["CONTROL_PRESSED"]
    TEXT_DISABLED = _PALETTES[DEFAULT_MODE]["TEXT_DISABLED"]
    FOCUS = _PALETTES[DEFAULT_MODE]["FOCUS"]
    INK = _PALETTES[DEFAULT_MODE]["INK"]
    SCROLL_THUMB = _PALETTES[DEFAULT_MODE]["SCROLL_THUMB"]
    GROUP_SIDE = _PALETTES[DEFAULT_MODE]["GROUP_SIDE"]
    CONTROL_SIDE = _PALETTES[DEFAULT_MODE]["CONTROL_SIDE"]
    ACCENT_SIDE = _PALETTES[DEFAULT_MODE]["ACCENT_SIDE"]
    RECORDING_SIDE = _PALETTES[DEFAULT_MODE]["RECORDING_SIDE"]
    ERROR_SIDE = _PALETTES[DEFAULT_MODE]["ERROR_SIDE"]
    AI_SIDE = _PALETTES[DEFAULT_MODE]["AI_SIDE"]
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
            with contextlib.suppress(Exception):  # ein defekter Rueckruf darf nichts blockieren
                callback()
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
    "bg",
    "fg",
    "highlightbackground",
    "highlightcolor",
    "activebackground",
    "activeforeground",
    "disabledforeground",
    "selectbackground",
    "selectforeground",
    "insertbackground",
    "troughcolor",
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
    except (tk.TclError, TypeError):  # kein Widget mit Hintergrund
        return
    if hasattr(widget, "_bg_under"):
        widget._bg_under = colour
        for name in ("_draw", "_render"):
            draw = getattr(widget, name, None)
            if callable(draw):
                with contextlib.suppress(tk.TclError):  # schon zerstoert
                    draw()
                break
    try:
        child_widgets = widget.winfo_children()
    except tk.TclError:  # pragma: no cover
        return
    for kind in child_widgets:
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
            continue  # Option gibt es an diesem Widget nicht
        wanted = Theme.remap(current)
        if wanted != current:
            try:
                widget.configure(**{option: wanted})
                changed += 1
            except tk.TclError:  # pragma: no cover - Widget zerstoert
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
    except tk.TclError:  # pragma: no cover - Widget bereits zerstoert
        return 0
    retint(widget)
    hook = getattr(widget, "_on_theme_change", None)
    if callable(hook):
        with contextlib.suppress(tk.TclError):
            hook()
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
    WINDOW = 20  # Fensterrand
    SECTION_GAP = 24  # zwischen zwei Abschnitten
    GROUP_GAP = 8  # zwischen Überschrift und Gruppe
    GROUP_INSET = 6  # Gruppenrand bis Zeile; die Zeile bleibt innerhalb der Rundung (Radius 14)
    ROW_X = 10  # Zeileninnenabstand waagerecht (plus Gruppeneinzug = 16)
    ROW_Y = 12  # Zeileninnenabstand senkrecht, oben und unten gleich
    CONTROL_GAP = 12  # zwischen Text und Control
    STACK_GAP = 8  # zwischen Text und einem darunter stehenden Control
    KEY_GAP = 4  # zwischen zwei Tasten-Chips
    TEXT_GAP = 2  # zwischen Titel und Beschreibung (optisch, Teil der Zeile)
    ICON_GAP = 8  # zwischen Icon und Text


class Radius:
    """Verwandte Radienfamilie: innen = außen − Abstand."""

    WINDOW = 18
    POPOVER = 18
    GROUP = 14  # Papierplatte: weicher als ein Stein, wie in Loratu
    CONTROL = 10
    BUTTON = 10
    KEYCAP = 6
    SEGMENT = 2

    @staticmethod
    def pill(height: int) -> int:
        """Pillenform — nur für Chip und Toggle."""
        return max(1, height // 2)


class Depth:
    """Tusche und Aquarell (Loratu): jede gemalte Fläche hat einen Tuscherand und eine Seite darunter.

    Logische Pixel. Die Seite ist die „Dicke“ eines Steins oder einer Platte; ein gedrückter Knopf sinkt
    genau um sie ein. `INK` ist die Breite des Tuscherands.
    """

    # Stufe „dezenter“: dieselbe Sprache wie Loratu, aber flachere Steine und eine feinere Linie.
    CONTROL = 2  # Knopf, Auswahlfeld, Wahlstein
    GROUP = 3  # Papierplatte
    KEYCAP = 1  # Tasten-Stein
    INK = 1.25  # Tuscherand


def ink_width() -> float:
    """Breite des Tuscherands in physischen Pixeln (bewusst nicht gerundet: der Renderer zeichnet überabgetastet)."""
    from hams.ui_kit.scale import Scale

    return max(1.0, Depth.INK * Scale.factor)


class Height:
    """Control-Höhen in logischen Pixeln. Bewusst nur drei."""

    PRIMARY = 36  # Hauptaktion
    STANDARD = 32  # Select, Icon-Button, Zeilencontrol
    COMPACT = 24  # kompakte Anzeige

    KEYCAP = 22  # Tasten-Chip; Anzeige, kein Control

    #: Mindesthöhe einer einzeiligen Einstellungszeile: Titel plus einzeilige
    #: Beschreibung (21 + 2 + 20 = 43 px bei 100 %, gerundet auf 44) plus
    #: `Space.ROW_Y` oben und unten. Das Standard-Control mit seiner Seite
    #: (32 + 2 = 34) passt darunter. So sind Zeilen mit und ohne
    #: Beschreibung, mit Schalter, Auswahl oder Tasten gleich hoch. Mehr
    #: Inhalt macht die Zeile höher, nie enger.
    ROW_MIN = 68
