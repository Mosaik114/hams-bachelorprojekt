"""The settings window: frame, header, scroll area and the six sections.

The window is built once and then only shown and hidden. Building it costs about 300 ms on the Tk thread (about
250 widgets, each a native window on Windows); showing a built one costs about 20 ms. The app builds it ahead in
an idle moment after the start (`FloatingTranscriberApp.prebuild_settings`), so even the first opening is quick.
Each time it is shown, every section first takes over the current state (`Section.sync`), so a hidden window
never shows anything stale; each time it is hidden, the sections stop their timers, streams and captures.
"""

from __future__ import annotations

import contextlib
import sys
import tkinter as tk
from typing import TYPE_CHECKING

from hams import branding
from hams import ui_kit as uk
from hams.i18n import t
from hams.settings_about import AboutSection
from hams.settings_extras import AiSection
from hams.settings_models import TranscriptionSection
from hams.settings_sections import GeneralSection, Section
from hams.settings_shortcuts import ShortcutsSection
from hams.settings_status import RecordingSection
from hams.ui_kit import Theme

if TYPE_CHECKING:
    from hams.app import FloatingTranscriberApp


class SettingsWindow:
    """Frameless settings window in the Mosaik design.

    Shows the microphone choice with live level, the state of model, Ollama and microphone, and the switches.
    While it is shown and nothing is being recorded, it keeps a level stream of its own open, so the level is
    visible *before* speaking.
    """

    WIDTH = 440  # logical
    HEIGHT = 680  # logical
    #: How long the invisible first layout stays mapped (`lay_out_hidden`); the layout needs two or three idle
    #: rounds of the main loop, measured well under 100 ms.
    LAYOUT_HIDDEN_MS = 250
    #: Far outside every screen, for the invisible first layout.
    OFFSCREEN = -32000

    def __init__(self, app: FloatingTranscriberApp, *, show: bool = True) -> None:
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.title(t("settings.window_title", product=branding.PRODUCT_NAME))
        uk.frame_window(self.win)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.configure(bg=Theme.WINDOW_BG)
        self.win.geometry(f"{uk.px(self.WIDTH)}x{uk.px(self.HEIGHT)}")
        self.win.bind("<Escape>", lambda _e: self.close())
        self.win.bind("<Destroy>", self._on_destroy, add="+")
        self._shown = False
        self._destroyed = False
        #: The colour scheme the widgets are painted in; a switch while hidden is caught up on `show`.
        self._painted_mode = Theme.mode
        self._drag = (0, 0)
        self._layout_job: str | None = None

        self._build()
        uk.Scale.register(self._on_scale_change)
        self.win.update_idletasks()
        if show:
            self.show()

    # ---------------------------------------------------------- lifecycle

    def alive(self) -> bool:
        """Whether the window is shown. A hidden one keeps its widgets but takes no events."""
        try:
            return self._shown and not self._destroyed and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def reusable(self) -> bool:
        """Whether this (hidden) window can be shown again instead of building a new one."""
        try:
            return not self._destroyed and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def show(self) -> None:
        """Takes over the current state, then appears next to the main window."""
        if self._shown:
            self.focus()
            return
        self._end_hidden_layout(show=True)
        if self._painted_mode != Theme.mode:
            self._repaint()
        for section in self.sections:
            section.sync()
        self._shown = True
        try:
            x, y = self.app.root.winfo_rootx(), self.app.root.winfo_rooty()
        except tk.TclError:
            x, y = 80, 80
        self.win.geometry(f"+{x + uk.px(24)}+{y + uk.px(24)}")
        self.scroll.scroll_to(0)
        self._on_scroll(self.scroll.offset)
        self.win.deiconify()
        self.win.update_idletasks()
        self.win.focus_force()
        uk.round_window_corners(self.win)
        for section in self.sections:
            section.on_show()

    def focus(self) -> None:
        uk.bring_to_front(self.win)

    def lay_out_hidden(self) -> None:
        """Windows: maps the window once, fully transparent and off screen, so that Tk lays it out ahead.

        A withdrawn Tk window does not arrange its content at all (its canvases stay 1 x 1 px); without this the
        first `show()` paid for the whole layout and every surface rendered at its final size (measured 120 ms).
        Tk maps a toplevel without activating it (`SW_SHOWNOACTIVATE`), the window is frameless (no taskbar
        entry) and at alpha 0, so nothing is seen and no focus moves. Not on Linux: there the window manager
        decides where a window goes and may show it in the task list.
        """
        if sys.platform != "win32" or self._shown or self._layout_job is not None or not self.reusable():
            return
        try:
            self.win.attributes("-alpha", 0.0)
            self.win.geometry(f"+{self.OFFSCREEN}+{self.OFFSCREEN}")
            self.win.deiconify()
        except tk.TclError:  # pragma: no cover - the window is going away
            return
        self._layout_job = self.win.after(self.LAYOUT_HIDDEN_MS, self._end_hidden_layout)

    def _end_hidden_layout(self, *, show: bool = False) -> None:
        if self._layout_job is None:
            return
        if show:
            with contextlib.suppress(ValueError, tk.TclError):
                self.win.after_cancel(self._layout_job)
        self._layout_job = None
        with contextlib.suppress(tk.TclError):
            if not show:
                self.win.withdraw()
            self.win.attributes("-alpha", 1.0)

    def close(self) -> None:
        """Hides the window; everything it ran stops. The widgets stay for the next opening."""
        if not self._shown:
            return
        self._shown = False
        popover = uk.Popover.current
        if popover is not None and str(popover.trigger).startswith(str(self.win)):
            uk.Popover.dismiss_current()
        for section in self.sections:
            section.on_hide()
        with contextlib.suppress(tk.TclError):
            self.win.withdraw()

    def destroy(self) -> None:
        """For good: hides, then removes the widgets."""
        self.close()
        with contextlib.suppress(tk.TclError):
            self.win.destroy()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self.win:
            self._destroyed = True
            self._shown = False
            uk.Scale.unregister(self._on_scale_change)

    def retint(self) -> int:
        """Recolours the window - without jumping.

        The scroll position is saved and set again explicitly: repainting the group surfaces causes Configure
        events, and they can move the view. The header line is evaluated again afterwards, because it is only
        visible with scrolled content.
        """
        offset = self.scroll.offset
        painted = self._repaint()
        self.scroll.scroll_to(offset)
        self._on_scroll(self.scroll.offset)
        return painted

    def _repaint(self) -> int:
        self._painted_mode = Theme.mode
        return uk.apply_theme_tree(self.win)

    def _on_scale_change(self, _factor: float) -> None:
        """Follows a new DPI with the window size.

        The controls inside scale themselves; the window itself nobody asked, and it would keep the old pixel size.
        """
        if not self.reusable():
            return
        try:
            self.win.geometry(f"{uk.px(self.WIDTH)}x{uk.px(self.HEIGHT)}")
            uk.round_window_corners(self.win)
        except tk.TclError:  # pragma: no cover - the window is just going away
            pass

    # ---------------------------------------------------------- events from the app

    def on_model_changed(self) -> None:
        self.transcription.on_model_changed()

    def on_download_progress(self, done: int, total: int, percent: int) -> None:
        self.transcription.on_download_progress(done, total, percent)

    def on_download_finished(self, model_id: str, error: str, switching: bool) -> None:
        self.transcription.on_download_finished(model_id, error, switching)

    def show_update(self, release) -> None:
        self.general.show_update(release)

    # ---------------------------------------------------------- drag

    def _start_drag(self, e: tk.Event) -> None:
        self._drag = (e.x, e.y)

    def _on_drag(self, e: tk.Event) -> None:
        x = self.win.winfo_x() + e.x - self._drag[0]
        y = self.win.winfo_y() + e.y - self._drag[1]
        self.win.geometry(f"+{x}+{y}")

    # ---------------------------------------------------------- build

    def _build(self) -> None:
        """Header, scroll area, the groups.

        The groups are one surface each with hairlines, not one card per setting - that is the difference that
        makes the window feel calm.
        """
        head = tk.Frame(self.win, bg=Theme.WINDOW_BG)
        head.pack(fill="x", padx=uk.px(uk.Space.WINDOW), pady=(uk.px(uk.Space.WINDOW), 0))

        title = tk.Label(
            head,
            text=t("settings.title"),
            bg=Theme.WINDOW_BG,
            fg=Theme.TEXT_PRIMARY,
            font=uk.font("window_title"),
            anchor="w",
        )
        title.pack(side="left")
        # Dragging by the header only without a system frame; on Linux one drags by the KDE title bar.
        for widget in (head, title) if not uk.system_frame() else ():
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

        self._close_btn = uk.IconButton(
            head, "close", self.close, bg_under=Theme.WINDOW_BG, tooltip=t("settings.close_tooltip")
        )
        self._close_btn.set_hover_colour(Theme.ERROR)
        if not uk.system_frame():  # on Linux the KDE title bar has the cross
            self._close_btn.pack(side="right")

        # The hairline appears only when content scrolls away below it.
        self._head_line = tk.Frame(self.win, bg=Theme.WINDOW_BG, height=max(1, uk.px(1)))
        self._head_line.pack(fill="x", pady=(uk.px(uk.Space.LG), 0))

        self.scroll = uk.ScrollArea(self.win, bg_under=Theme.WINDOW_BG, on_scroll=self._on_scroll)
        self.scroll.pack(fill="both", expand=True, padx=(uk.px(uk.Space.WINDOW), 0), pady=(0, uk.px(uk.Space.WINDOW)))
        self.scroll.bind_keys(self.win)

        body = self.scroll.body
        self.recording = RecordingSection(self, body)
        self.transcription = TranscriptionSection(self, body)
        self.ai = AiSection(self, body)
        self.shortcuts = ShortcutsSection(self, body)
        self.general = GeneralSection(self, body)
        self.about = AboutSection(self, body)
        self.sections: list[Section] = [
            self.recording,
            self.transcription,
            self.ai,
            self.shortcuts,
            self.general,
            self.about,
        ]

        # The tab order follows the stacking order, and that is the order of creation. The header is created first,
        # so the cross would come *before* all settings. `lift` moves the header to the end of the chain; it stays
        # visible on top because packing decides that.
        head.lift()

    def _on_scroll(self, offset: int) -> None:
        """The line appears only when something lies under it."""
        self._head_line.configure(bg=Theme.HAIRLINE if offset > 0 else Theme.WINDOW_BG)
