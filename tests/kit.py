"""The whole ui_kit for the tests.

`hams.ui_kit` only exports what the app uses (the kit is frozen: no new controls). The tests also reach
internals such as caches, icon painters and animation helpers; they import them from here.
"""

from hams.ui_kit import *  # noqa: F403 - everything the package exports
from hams.ui_kit.animation import (  # noqa: F401
    FRAME_MS,
    FRAME_PERIOD_MS,
    SPI_GETCLIENTAREAANIMATION,
    Animator,
    HighResolutionTimer,
    blend,
    ease_out,
    init_animations,
    system_animations_enabled,
)
from hams.ui_kit.buttons import BaseControl, IconButton, SecondaryButton, _SurfaceControl  # noqa: F401
from hams.ui_kit.caches import _icon_cache, _img_cache  # noqa: F401
from hams.ui_kit.controls import ProgressBar, SignalMeter, StatusDot, Switch  # noqa: F401
from hams.ui_kit.icons import (  # noqa: F401
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
    render_icon,
)
from hams.ui_kit.keys import KEY_MESSAGES, KeyCombo, key_labels  # noqa: F401
from hams.ui_kit.layout import GroupSurface, ScrollArea, SectionHeader  # noqa: F401
from hams.ui_kit.monitors import MONITORINFOF_PRIMARY, _MonitorInfo, _Rect, work_area, work_areas  # noqa: F401
from hams.ui_kit.popover import Option, Popover, _PopoverItem  # noqa: F401
from hams.ui_kit.scale import (  # noqa: F401
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
from hams.ui_kit.select import Select  # noqa: F401
from hams.ui_kit.setting_row import SettingRow  # noqa: F401
from hams.ui_kit.states import ControlStyle, FocusMixin, State, StateMixin, return_focus_to  # noqa: F401
from hams.ui_kit.surfaces import (  # noqa: F401
    CACHE_LIMIT,
    SLICE_MARGIN,
    SQUIRCLE_EXPONENT,
    SUPERSAMPLE,
    _hex_to_rgb,
    _render_full,
    _shape_mask,
    _squircle_points,
    _vertical_ramp,
    render_surface,
    surface,
)
from hams.ui_kit.text import _ellipsize, _fit_text, _line_height, _pil_font, _pil_font_for, _text_width  # noqa: F401
from hams.ui_kit.theme import (  # noqa: F401
    TINTED_OPTIONS,
    Height,
    Radius,
    Space,
    Theme,
    apply_theme_tree,
    retint,
    set_surface_under,
)
from hams.ui_kit.window import (  # noqa: F401
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
