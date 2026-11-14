"""The painted head of the settings and the setup assistant: wash, mark, title and close button on one canvas.

Both windows had a head of their own: the settings a painted stage, the assistant a row of labels. Now they share
`Stage`. Mark and title are canvas items, not labels, so nothing stands there as a box in the paper colour. The
wash and its ink line keep clear of mark, title and cross at any width (`banner_image` with free areas). The
cross stands with the middle of its *face* on the middle of the stage: a stone is taller than its face by its
side, and centring the whole widget put the cross visibly higher than the title.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable

from hams import ui_kit as uk
from hams.ui_kit import Theme
from hams.window_mark import mark_photo


class Stage(tk.Canvas):
    """Head of a window: painted wash, the Hams mark, a title and (with a borderless window) a close button."""

    #: Height in logical pixels.
    HEIGHT = 76
    #: Free space between the title and the beginning of the wash, logical pixels.
    TITLE_GAP = uk.Space.XL

    #: Dragging the stage moves the window, like a title bar.
    drag_surface = True

    def __init__(self, master: tk.Misc, title: str, on_close: Callable[[], object], *, close_tooltip: str) -> None:
        super().__init__(master, bg=Theme.WINDOW_BG, bd=0, highlightthickness=0, height=uk.px(self.HEIGHT))
        self._title = title
        self._size = (0, 0)
        self._images: list = []  # Tk keeps no reference of its own
        self.close_button = uk.IconButton(self, "close", on_close, bg_under=Theme.WINDOW_BG, tooltip=close_tooltip)
        self.close_button.set_hover_colour(Theme.ERROR)
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        uk.Scale.register(self._on_scale_change)

    @property
    def title(self) -> str:
        return self._title

    def set_title(self, title: str) -> None:
        self._title = title
        self.paint()

    @property
    def has_close(self) -> bool:
        """On Linux the window manager's title bar has the cross."""
        return not uk.system_frame()

    # ---------------------------------------------------------- painting

    def _on_configure(self, event: tk.Event) -> None:
        if (event.width, event.height) != self._size:
            self._size = (event.width, event.height)
            self.paint()

    def free_areas(self, width: int) -> tuple[int, int]:
        """Where the wash may begin and must end (pixels): after the title, before the cross."""
        margin = uk.px(uk.Space.WINDOW)
        left = margin
        mark = mark_photo()
        if mark is not None:
            left += mark.width() + uk.px(uk.Space.MD)
        with contextlib.suppress(tk.TclError):  # fonts not yet created
            left += int(self.tk.call("font", "measure", uk.font("window_title"), self._title))
        left += uk.px(self.TITLE_GAP)
        right = width - margin
        if self.has_close:
            right -= uk.px(uk.IconButton.SIZE) + uk.px(uk.Space.LG)
        return left, max(left, right)

    def paint(self) -> None:
        """The wash for the current width, then mark, title and cross on it."""
        width, height = self._size
        if width <= 1 or height <= 1:
            return
        self.delete("all")
        clear_left, clear_right = self.free_areas(width)
        wash = uk.banner_image(width, height, clear_left=clear_left, clear_right=clear_right)
        mark = mark_photo()
        self._images = [wash, mark]
        self.create_image(0, 0, anchor="nw", image=wash)
        margin, middle = uk.px(uk.Space.WINDOW), height // 2
        x = margin
        if mark is not None:
            self.create_image(x, middle, anchor="w", image=mark)
            x += mark.width() + uk.px(uk.Space.MD)
        self.create_text(
            x, middle, anchor="w", text=self._title, font=uk.font("window_title"), fill=Theme.TEXT_PRIMARY, tags="title"
        )
        if self.has_close:
            # The face of the stone on the middle line, its side hangs below it.
            self.create_window(
                width - margin, middle + uk.px(uk.Depth.CONTROL) // 2, anchor="e", window=self.close_button
            )

    def _on_theme_change(self) -> None:
        self.configure(bg=Theme.WINDOW_BG)
        self.paint()

    def _on_scale_change(self, _factor: float) -> None:
        with contextlib.suppress(tk.TclError):
            self.configure(height=uk.px(self.HEIGHT))
            self.paint()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            uk.Scale.unregister(self._on_scale_change)
