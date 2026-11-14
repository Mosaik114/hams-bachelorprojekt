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
from hams.window_stage import Stage

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
    #: Pause between laying out two hidden pages (`warm_pages`): one page per step keeps the app responsive.
    WARM_STEP_MS = 40
    #: Where a hidden page is laid out inside the scroll body: far above it, where nothing of it is seen.
    WARM_Y = -20000

    def __init__(self, app: FloatingTranscriberApp, *, show: bool = True) -> None:
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.title(t("settings.window_title", product=branding.PRODUCT_NAME))
        self.chrome = uk.frame_window(self.win, min_size=self.MIN_SIZE, on_resized=self._remember_size)
        app.watch_dpi(self.win)
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
        #: Hidden pages still to be laid out ahead, and the page laid out in the running step.
        self._warm_queue: list[int] = []
        self._warm_current: int | None = None
        self._warm_job: str | None = None

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
        self.warm_pages()

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
        self.warm_pages()
        # The page steps run while the window is mapped; it stays mapped until the last one is done.
        hold = self.LAYOUT_HIDDEN_MS + len(self._pages) * self.WARM_STEP_MS
        self._layout_job = self.win.after(hold, self._end_hidden_layout)

    def _end_hidden_layout(self, *, show: bool = False) -> None:
        if self._layout_job is None:
            return
        if show:
            with contextlib.suppress(ValueError, tk.TclError):
                self.win.after_cancel(self._layout_job)
        self._layout_job = None
        with contextlib.suppress(tk.TclError):
            if not show:
                self._stop_warming()
                self.win.withdraw()
            self.win.attributes("-alpha", 1.0)
            # Tk keeps WS_EX_LAYERED after the alpha is back at 1.0; a layered window costs more on every resize.
            uk.clear_layered(self.win)

    def close(self) -> None:
        """Hides the window; everything it ran stops. The widgets stay for the next opening."""
        if not self._shown:
            return
        self._shown = False
        popover = uk.Popover.current
        if popover is not None and str(popover.trigger).startswith(str(self.win)):
            uk.Popover.dismiss_current()
        self._stop_warming()
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
        return uk.apply_theme_tree(self.win)  # the stage repaints itself in its theme hook

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
        """After a resize by edge or corner: keep the size in logical pixels, so it holds at any DPI.

        The hidden pages are laid out again for the new width, so that switching to one stays quick.
        """
        self.warm_pages()
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
        head = Stage(self.win, t("settings.title"), self.close, close_tooltip=t("settings.close_tooltip"))
        head.pack(fill="x")
        self._stage = head
        self._close_btn = head.close_button

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
        with contextlib.suppress(tk.TclError):  # the window is going away
            _render_stale(self.scroll.body)

    # ---------------------------------------------------------- hidden pages laid out ahead

    def warm_pages(self) -> None:
        """Lays out every hidden page ahead, one per step, so that also the first visit of a tab is quick.

        Only the shown page is packed into the scroll body. A hidden page would be laid out on its first visit
        (measured 45–113 ms) and again after every change of the width. Here each hidden page is placed for one
        step at the width it would have, far above the visible part of the body, where the body clips it away;
        `place` does not change the body's height, so the scroll area does not notice. Its controls render at
        their final size, then it is taken away again. Shown later with `pack` at the same width, nothing in it
        changes size, and nothing has to be laid out or drawn.
        """
        self._warm_queue = [index for index in range(len(self._pages)) if index != self.page]
        if self._warm_job is None:
            # Not right away: the window has to be laid out first, or the pages get the width of the moment.
            with contextlib.suppress(tk.TclError):
                self._warm_job = self.win.after(self.WARM_STEP_MS, self._warm_step)

    def _warm_step(self) -> None:
        self._warm_job = None
        self._unplace_warm()
        if not self._warm_queue or not self.reusable():
            self._warm_queue = []
            return
        index = self._warm_queue.pop(0)
        if index != self.page:
            self._place_warm(index)
        with contextlib.suppress(tk.TclError):
            self._warm_job = self.win.after(self.WARM_STEP_MS, self._warm_step)

    def _page_widgets(self, index: int) -> list[tuple[tk.Misc, dict]]:
        """The widgets of a page as `select_page` packs them (without the heading the tab already shows)."""
        title, widgets = self._pages[index]
        if widgets and isinstance(widgets[0][0], uk.SectionHeader) and str(widgets[0][0].cget("text")) == title:
            return widgets[1:]
        return widgets

    def _place_warm(self, index: int) -> None:
        try:
            width = self.scroll.body.winfo_width()
        except tk.TclError:  # pragma: no cover - the window is going away
            return
        if width <= 1:
            return
        self._warm_current = index
        for widget, info in self._page_widgets(index):
            left, right = _pad_pair(info.get("padx", 0))
            widget.hidden_page = False
            with contextlib.suppress(tk.TclError):
                widget.place(x=left, y=self.WARM_Y, width=max(1, width - left - right))

    def _unplace_warm(self) -> None:
        index, self._warm_current = self._warm_current, None
        if index is None:
            return
        for widget, _info in self._page_widgets(index):
            with contextlib.suppress(tk.TclError):
                if widget.winfo_manager() == "place":
                    _render_stale(widget)  # drawn now, at the final size, not on the first visit
                    widget.place_forget()
            if index != self.page:
                widget.hidden_page = True

    def _stop_warming(self) -> None:
        if self._warm_job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.win.after_cancel(self._warm_job)
            self._warm_job = None
        self._warm_queue = []
        self._unplace_warm()

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


def _render_stale(widget: tk.Misc) -> None:
    """Lets every control in `widget` catch up on a drawing it skipped while hidden."""
    pending = [widget]
    while pending:
        node = pending.pop()
        render = getattr(node, "render_if_stale", None)
        if render is not None:
            render()
        pending.extend(node.winfo_children())


def _pad_pair(value) -> tuple[int, int]:
    """`padx` of `pack_info` as (left, right): Tk reports one number, a pair, or a string of one or two."""
    if isinstance(value, (tuple, list)):
        parts = [int(float(str(part))) for part in value]
    else:
        parts = [int(float(part)) for part in str(value).split()] or [0]
    return (parts[0], parts[-1])
