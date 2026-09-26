"""Hams UI-Kit — das Mosaik-Design für tkinter.

Ziel ist das Mosaik-Design (siehe `Mosaik-Design.md` im Projektordner): warmes
Creme oder warmes Dunkel, Terrakotta als einziger Akzent, Überschriften in
Serifenschrift, flache Flächen mit feinem Rand, viel Luft und wenig Fettung.
tkinter kann das nicht von Haus aus, daher:

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

Das Kit ist nach Themen auf Module verteilt; dieses Paket bietet alle
Namen wieder an, damit `from wisper import ui_kit as uk` genuegt.
"""

from wisper.ui_kit.caches import (
    _img_cache, _icon_cache,
)
from wisper.ui_kit.theme import (
    Theme, TINTED_OPTIONS, set_surface_under, retint, apply_theme_tree, Space, Radius, Height,
)
from wisper.ui_kit.scale import (
    enable_dpi_awareness, window_dpi, Scale, keep_logical_width, px, init_scale, _FONTS,
    SERIF_FAMILIES, SEMIBOLD_FAMILIES, _FONT_ROLES, points_to_px, init_fonts, font,
)
from wisper.ui_kit.animation import (
    SPI_GETCLIENTAREAANIMATION, FRAME_MS, system_animations_enabled, ease_out, blend, Animator,
    init_animations,
)
from wisper.ui_kit.surfaces import (
    SUPERSAMPLE, SQUIRCLE_EXPONENT, CACHE_LIMIT, _hex_to_rgb, _squircle_points, _shape_mask,
    _vertical_ramp, render_surface, surface, rounded_image,
)
from wisper.ui_kit.states import (
    State, ControlStyle, StateMixin, FocusMixin, focus_chain, return_focus_to,
)
from wisper.ui_kit.icons import (
    ICON_BOX, ICON_STROKE, ICON_CACHE_LIMIT, _stroke, _icon_settings, _icon_close,
    _icon_chevron, _icon_check, _icon_download, _icon_stop, _icon_trash, _ICON_PAINTERS,
    icon_names, render_icon, icon,
)
from wisper.ui_kit.text import (
    _line_height, _text_width, _fit_text, _pil_font, _ellipsize,
)
from wisper.ui_kit.monitors import (
    _Rect, _MonitorInfo, MONITORINFOF_PRIMARY, work_area, work_areas,
)
from wisper.ui_kit.window import (
    _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND, _WIN11_BUILD, _windows_build,
    _window_handle, round_window_corners,
)
from wisper.ui_kit.buttons import (
    BaseControl, _SurfaceControl, PrimaryButton, SecondaryButton, IconButton,
)
from wisper.ui_kit.controls import (
    StatusDot, Switch, SignalMeter, KEY_NAMES, key_labels, KeyCombo,
)
from wisper.ui_kit.layout import (
    ScrollArea, SectionHeader, GroupSurface, SettingRow, separator,
)
from wisper.ui_kit.popover import (
    Option, _PopoverItem, Popover, Select,
)
