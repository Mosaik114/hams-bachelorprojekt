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
from hams import config as hams_config
from hams import ui_kit as uk
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import t
from hams.settings_about import AboutSection
from hams.settings_extras import AiSection
from hams.settings_models import TranscriptionSection
from hams.settings_sections import GeneralSection, Section
from hams.settings_shortcuts import ShortcutsSection
from hams.settings_status import RecordingSection
from hams.ui_kit import Theme
from hams.window_mark import mark_photo

if TYPE_CHECKING:
    from hams.app import FloatingTranscriberApp


class SettingsWindow:
    """Frameless settings window in the Mosaik design.

    Shows the microphone choice with live level, the state of model, Ollama and microphone, and the switches.
    While it is shown and nothing is being recorded, it keeps a level stream of its own open, so the level is
    visible *before* speaking.
    """

    #: Smallest size in logical pixels; the user's size is `CFG.settings_width/height`.
    MIN_SIZE = (hams_config.SETTINGS_MIN_WIDTH, hams_config.SETTINGS_MIN_HEIGHT)
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
        self.chrome = uk.frame_window(self.win, min_size=self.MIN_SIZE, on_resized=self._remember_size)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.configure(bg=Theme.WINDOW_BG)
        self.win.geometry(self._size())
        self.win.bind("<Escape>", lambda _e: self.close())
        self.win.bind("<Destroy>", self._on_destroy, add="+")
        self._shown = False
        self._destroyed = False
        #: The colour scheme the widgets are painted in; a switch while hidden is caught up on `show`.
        self._painted_mode = Theme.mode
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
        changed = uk.apply_theme_tree(self.win)
        self._paint_stage()
        return changed

    def _on_scale_change(self, _factor: float) -> None:
        """Follows a new DPI with the window size.

        The controls inside scale themselves; the window itself nobody asked, and it would keep the old pixel size.
        """
        if not self.reusable():
            return
        try:
            self.win.geometry(self._size())
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

    # ---------------------------------------------------------- size

    @staticmethod
    def _size() -> str:
        """The remembered size (logical pixels) as a Tk geometry in screen pixels."""
        return f"{uk.px(CFG.settings_width)}x{uk.px(CFG.settings_height)}"

    def _remember_size(self, width: int, height: int) -> None:
        """After a resize by edge or corner: keep the size in logical pixels, so it holds at any DPI."""
        factor = max(uk.Scale.factor, 0.1)
        logical = (round(width / factor), round(height / factor))
        if logical == (CFG.settings_width, CFG.settings_height):
            return
        CFG.settings_width, CFG.settings_height = logical
        _persist_config_value("ui", "settings_width", logical[0])
        _persist_config_value("ui", "settings_height", logical[1])
        logger.info("Settings window size saved: %dx%d", *logical)

    # ---------------------------------------------------------- build

    def _build(self) -> None:
        """Header, scroll area, the groups.

        The groups are one surface each with hairlines, not one card per setting - that is the difference that
        makes the window feel calm.
        """
        # The stage (Tusche und Aquarell): a quiet painted wash with the mark and the title on it.
        head = tk.Canvas(self.win, bg=Theme.WINDOW_BG, bd=0, highlightthickness=0, height=uk.px(self.STAGE_HEIGHT))
        head.pack(fill="x")
        self._stage = head
        self._stage_size = (0, 0)
        self._close_btn = uk.IconButton(
            head, "close", self.close, bg_under=Theme.WINDOW_BG, tooltip=t("settings.close_tooltip")
        )
        self._close_btn.set_hover_colour(Theme.ERROR)
        head.bind("<Configure>", self._paint_stage, add="+")

        # One topic at a time: a slim row of tab stones, each as wide as its word, wrapping when narrow.
        self._tabs = tk.Frame(self.win, bg=Theme.WINDOW_BG, height=1)
        self._tabs.pack(fill="x", padx=uk.px(uk.Space.WINDOW), pady=(0, uk.px(uk.Space.MD)))
        self._tabs.bind("<Configure>", lambda _e: self._lay_out_tabs(), add="+")
        self._tab_buttons: list[uk.SecondaryButton] = []
        self._tab_places: list[tuple[int, int]] = []

        # The hairline appears only when content scrolls away below it.
        self._head_line = tk.Frame(self.win, bg=Theme.WINDOW_BG, height=max(1, uk.px(1)))
        self._head_line.pack(fill="x")

        self.scroll = uk.ScrollArea(self.win, bg_under=Theme.WINDOW_BG, on_scroll=self._on_scroll)
        self.scroll.pack(fill="both", expand=True, padx=(uk.px(uk.Space.WINDOW), 0), pady=(0, uk.px(uk.Space.WINDOW)))
        self.scroll.bind_keys(self.win)

        body = self.scroll.body
        #: Per section: (title, its widgets in the scroll body with their pack options).
        self._pages: list[tuple[str, list[tuple[tk.Misc, dict]]]] = []
        self.recording = self._page(RecordingSection, body)
        self.transcription = self._page(TranscriptionSection, body)
        self.ai = self._page(AiSection, body)
        self.shortcuts = self._page(ShortcutsSection, body)
        self.general = self._page(GeneralSection, body)
        self.about = self._page(AboutSection, body)
        self.sections: list[Section] = [
            self.recording,
            self.transcription,
            self.ai,
            self.shortcuts,
            self.general,
            self.about,
        ]
        for index, (title, _widgets) in enumerate(self._pages):
            button = uk.SecondaryButton(
                self._tabs,
                title,
                lambda i=index: self.select_page(i),
                bg_under=Theme.WINDOW_BG,
                height=uk.Height.STANDARD,
            )
            self._tab_buttons.append(button)
        self.page = -1
        self.select_page(0)

        # The tab order follows the stacking order, and that is the order of creation. The header is created first,
        # so the cross would come *before* all settings. `lift` moves the header to the end of the chain; it stays
        # visible on top because packing decides that.
        tk.Misc.lift(head)  # on a Canvas `lift` would raise canvas items

    # ---------------------------------------------------------- stage and pages

    #: Height of the painted stage in logical pixels.
    STAGE_HEIGHT = 76

    def _paint_stage(self, event: tk.Event | None = None) -> None:
        """The wash for the current width, then mark, title and cross on it."""
        stage = self._stage
        width, height = (event.width, event.height) if event is not None else self._stage_size
        if width <= 1:
            return
        self._stage_size = (width, height)
        stage.delete("all")
        self._stage_image = uk.banner_image(width, height)
        stage.create_image(0, 0, anchor="nw", image=self._stage_image)
        margin, middle = uk.px(uk.Space.WINDOW), height // 2
        # Mark and title are painted onto the stage, not placed as labels: a label would stand there as a box
        # in the paper colour.
        self._stage_mark = mark_photo()
        x = margin
        if self._stage_mark is not None:
            stage.create_image(x, middle, anchor="w", image=self._stage_mark)
            x += self._stage_mark.width() + uk.px(uk.Space.MD)
        stage.create_text(
            x, middle, anchor="w", text=t("settings.title"), font=uk.font("window_title"), fill=Theme.TEXT_PRIMARY
        )
        if not uk.system_frame():  # on Linux the KDE title bar has the cross
            stage.create_window(width - margin, middle, anchor="e", window=self._close_btn)

    def _page(self, section_class, body: tk.Frame) -> Section:
        """Builds a section and remembers which widgets of the scroll body belong to it."""
        before = len(body.pack_slaves())
        section = section_class(self, body)
        widgets = [(widget, widget.pack_info()) for widget in body.pack_slaves()[before:]]
        headers = [widget for widget, _info in widgets if isinstance(widget, uk.SectionHeader)]
        title = str(headers[0].cget("text")) if headers else section_class.__name__
        self._pages.append((title, widgets))
        return section

    def select_page(self, index: int) -> None:
        """Shows one topic. Its first heading is the tab itself, so it is not repeated above the group."""
        if index == self.page or not 0 <= index < len(self._pages):
            return
        self.page = index
        for number, (_title, widgets) in enumerate(self._pages):
            for widget, _info in widgets:
                widget.pack_forget()
                widget.hidden_page = number != index  # its controls skip drawing while hidden
        title, widgets = self._pages[index]
        first = True
        for widget, info in widgets:
            if first and isinstance(widget, uk.SectionHeader) and str(widget.cget("text")) == title:
                first = False
                continue
            first = False
            options = {key: value for key, value in info.items() if key != "in"}
            widget.pack(**options)
        for number, button in enumerate(self._tab_buttons):
            button.set_role("primary" if number == index else "secondary")
        self.win.after_idle(self._render_shown_page)
        self.scroll.scroll_to(0)
        self._on_scroll(0)

    def _render_shown_page(self) -> None:
        """Controls that changed size while their page was hidden draw themselves now."""
        pending = [self.scroll.body]
        while pending:
            widget = pending.pop()
            render = getattr(widget, "render_if_stale", None)
            if render is not None:
                render()
            try:
                pending.extend(widget.winfo_children())
            except tk.TclError:  # pragma: no cover - the window is going away
                return

    def _lay_out_tabs(self) -> None:
        """Places the tab stones left to right at their natural width; a stone that does not fit starts a new row.

        Nothing is stretched: a tab is as wide as its word. The rows only change when the window width does.
        """
        try:
            width = self._tabs.winfo_width()
        except tk.TclError:  # pragma: no cover - the window is going away
            return
        if width <= 1 or not self._tab_buttons:
            return
        gap = uk.px(uk.Space.SM)
        x = y = 0
        row_height = max(button.winfo_reqheight() for button in self._tab_buttons)
        places = []
        for button in self._tab_buttons:
            wide = button.winfo_reqwidth()
            if x > 0 and x + wide > width:
                x, y = 0, y + row_height + gap
            places.append((x, y))
            x += wide + gap
        if places == self._tab_places:
            return
        self._tab_places = places
        for button, (left, top) in zip(self._tab_buttons, places, strict=True):
            button.place(x=left, y=top)
        self._tabs.configure(height=y + row_height)

    def _on_scroll(self, offset: int) -> None:
        """The line appears only when something lies under it."""
        self._head_line.configure(bg=Theme.HAIRLINE if offset > 0 else Theme.WINDOW_BG)
