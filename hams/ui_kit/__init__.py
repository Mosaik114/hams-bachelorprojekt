"""Hams UI-Kit — das Mosaik-Design für tkinter.

Die Designsprache ist „Tusche und Aquarell“ (Loratu, Stufe „dezenter“): Papiergrund, Flächen mit
Tuscherand und einer Steinseite darunter, Türkis als einziger Akzent, Überschriften und Knöpfe in der
runden Nunito, viel Luft. tkinter kann das nicht von Haus aus, daher:

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
Tabulatorkette, zeigt nach Tastaturbedienung einen kräftigen Fokusrand, reagiert auf
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

Das Kit ist nach Themen auf Module verteilt; dieses Paket bietet die Namen an,
die die Anwendung braucht, damit `from hams import ui_kit as uk` genuegt.
Es ist eingefroren: keine neuen Eigenbau-Controls (siehe docs/architecture.md).
"""

from hams.ui_kit.animation import init_animations
from hams.ui_kit.banner import banner_image
from hams.ui_kit.buttons import IconButton, SecondaryButton
from hams.ui_kit.controls import (
    ProgressBar,
    SignalMeter,
    StatusDot,
    Switch,
)
from hams.ui_kit.keys import KeyCombo, key_labels
from hams.ui_kit.layout import GroupSurface, ScrollArea, SectionHeader
from hams.ui_kit.monitors import work_areas
from hams.ui_kit.popover import Option, Popover
from hams.ui_kit.scale import (
    Scale,
    enable_dpi_awareness,
    font,
    init_fonts,
    init_scale,
    keep_logical_width,
    px,
    window_dpi,
)
from hams.ui_kit.select import Select
from hams.ui_kit.setting_row import SettingRow
from hams.ui_kit.tabs import TabBar
from hams.ui_kit.theme import (
    Depth,
    Height,
    Space,
    Theme,
    apply_theme_tree,
)
from hams.ui_kit.window import (
    LiveResize,
    WindowChrome,
    bring_to_front,
    clear_layered,
    frame_window,
    is_layered,
    round_window_corners,
    system_frame,
)

__all__ = [
    "init_animations",
    "IconButton",
    "SecondaryButton",
    "ProgressBar",
    "SignalMeter",
    "StatusDot",
    "Switch",
    "KeyCombo",
    "key_labels",
    "GroupSurface",
    "ScrollArea",
    "TabBar",
    "SectionHeader",
    "work_areas",
    "Option",
    "Popover",
    "Scale",
    "enable_dpi_awareness",
    "font",
    "init_fonts",
    "init_scale",
    "keep_logical_width",
    "px",
    "window_dpi",
    "banner_image",
    "Select",
    "SettingRow",
    "Depth",
    "Height",
    "Space",
    "Theme",
    "apply_theme_tree",
    "LiveResize",
    "WindowChrome",
    "bring_to_front",
    "clear_layered",
    "frame_window",
    "is_layered",
    "round_window_corners",
    "system_frame",
]
