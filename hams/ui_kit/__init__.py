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
Namen wieder an, damit `from hams import ui_kit as uk` genuegt.
"""

from hams.ui_kit.animation import (
    FRAME_MS,
    SPI_GETCLIENTAREAANIMATION,
    Animator,
    blend,
    ease_out,
    init_animations,
    system_animations_enabled,
)
from hams.ui_kit.buttons import (
    BaseControl,
    IconButton,
    PrimaryButton,
    SecondaryButton,
    _SurfaceControl,
)
from hams.ui_kit.caches import (
    _icon_cache,
    _img_cache,
)
from hams.ui_kit.controls import (
    SignalMeter,
    StatusDot,
    Switch,
)
from hams.ui_kit.icons import (
    _ICON_PAINTERS,
    ICON_BOX,
    ICON_CACHE_LIMIT,
    ICON_STROKE,
    _icon_check,
    _icon_chevron,
    _icon_close,
    _icon_download,
    _icon_settings,
    _icon_stop,
    _icon_trash,
    _stroke,
    icon,
    icon_names,
    render_icon,
)
from hams.ui_kit.keys import KEY_NAMES, KeyCombo, key_labels
from hams.ui_kit.layout import (
    GroupSurface,
    ScrollArea,
    SectionHeader,
    separator,
)
from hams.ui_kit.monitors import (
    MONITORINFOF_PRIMARY,
    _MonitorInfo,
    _Rect,
    work_area,
    work_areas,
)
from hams.ui_kit.popover import (
    Option,
    Popover,
    _PopoverItem,
)
from hams.ui_kit.scale import (
    _FONT_ROLES,
    _FONTS,
    SEMIBOLD_FAMILIES,
    SERIF_FAMILIES,
    Scale,
    enable_dpi_awareness,
    font,
    init_fonts,
    init_scale,
    keep_logical_width,
    points_to_px,
    px,
    window_dpi,
)
from hams.ui_kit.select import Select
from hams.ui_kit.setting_row import SettingRow
from hams.ui_kit.states import (
    ControlStyle,
    FocusMixin,
    State,
    StateMixin,
    focus_chain,
    return_focus_to,
)
from hams.ui_kit.surfaces import (
    CACHE_LIMIT,
    SQUIRCLE_EXPONENT,
    SUPERSAMPLE,
    _hex_to_rgb,
    _shape_mask,
    _squircle_points,
    _vertical_ramp,
    render_surface,
    rounded_image,
    surface,
)
from hams.ui_kit.text import (
    _ellipsize,
    _fit_text,
    _line_height,
    _pil_font,
    _text_width,
)
from hams.ui_kit.theme import (
    TINTED_OPTIONS,
    Height,
    Radius,
    Space,
    Theme,
    apply_theme_tree,
    retint,
    set_surface_under,
)
from hams.ui_kit.window import (
    _DWMWA_WINDOW_CORNER_PREFERENCE,
    _DWMWCP_ROUND,
    _WIN11_BUILD,
    _window_handle,
    _windows_build,
    bring_to_front,
    frame_window,
    round_window_corners,
    system_frame,
)
