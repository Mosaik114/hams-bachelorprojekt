"""The painted head of the settings and the setup assistant: track, mark, title and close button on one canvas.

Both windows had a head of their own: the settings a painted stage, the assistant a row of labels. Now they share
`Stage`. Mark and title are canvas items, not labels, so nothing stands there as a box in the paper colour. The
painted voice track (`banner_image`) fills the free stretch between title and cross and stands against the cross.
The cross stands with the middle of its *face* on the middle of the stage: a stone is taller than its face by its
side, and centring the whole widget put the cross visibly higher than the title.

Right to left (Arabic) the stage is mirrored: mark and title on the right, the cross on the left, the track
between them.

A new width only *moves* the track and the cross. The track's own width comes in steps, so while the window is
resized a new picture is painted only when the free stretch crosses a step; everything else is two `coords` calls.
Before, every pixel of a resize painted the whole stage anew (3-6 ms a step).
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable

from hams import ui_kit as uk
from hams.ui_kit import Theme
from hams.ui_kit.banner import track_width
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
        self._track_width = -1
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
        size = (event.width, event.height)
        if size == self._size:
            return
        same_height = size[1] == self._size[1] and self.find_withtag("title")
        self._size = size
        if same_height:
            self.place_items()
        else:
            self.paint()

    def free_areas(self, width: int) -> tuple[int, int]:
        """Where the track may begin and must end (pixels): between the title and the cross."""
        margin = uk.px(uk.Space.WINDOW)
        head = 0
        mark = mark_photo()
        if mark is not None:
            head += mark.width() + uk.px(uk.Space.MD)
        with contextlib.suppress(tk.TclError):  # fonts not yet created
            head += int(self.tk.call("font", "measure", uk.font("window_title"), self._title))
        head += uk.px(self.TITLE_GAP)
        cross = uk.px(uk.IconButton.SIZE) + uk.px(uk.Space.LG) if self.has_close else 0
        if uk.Writing.rtl:
            left, right = margin + cross, width - margin - head
        else:
            left, right = margin + head, width - margin - cross
        return left, max(left, right)

    def paint(self) -> None:
        """Mark, title, cross and the track for the current size, from scratch."""
        width, height = self._size
        if width <= 1 or height <= 1:
            return
        self.delete("all")
        self._track_width = -1
        mark = mark_photo()
        self._images = [mark]
        self.create_image(0, 0, anchor="ne", tags="track")
        margin, middle = uk.px(uk.Space.WINDOW), height // 2
        x = margin
        lead = uk.mirror_anchor("w")
        if mark is not None:
            self.create_image(x, middle, anchor=lead, image=mark, tags="mark")
            x += mark.width() + uk.px(uk.Space.MD)
        self.create_text(
            x,
            middle,
            anchor=lead,
            text=self._title,
            font=uk.font("window_title"),
            fill=Theme.TEXT_PRIMARY,
            tags="title",
        )
        if self.has_close:
            # The face of the stone on the middle line, its side hangs below it.
            self.create_window(
                0,
                middle + uk.px(uk.Depth.CONTROL) // 2,
                anchor=uk.mirror_anchor("e"),
                window=self.close_button,
                tags="close",
            )
        self.place_items()

    def place_items(self) -> None:
        """Puts track and cross where the current width wants them; paints a track only for a new step."""
        width, height = self._size
        if width <= 1 or height <= 1:
            return
        left, right = self.free_areas(width)
        track = track_width(right - left, uk.Scale.factor)
        if track != self._track_width:
            self._track_width = track
            image = uk.banner_image(track, height) if track else ""
            self._images[1:] = [image]
            self.itemconfigure("track", image=image)
        self.coords("track", right, 0)
        margin, middle = uk.px(uk.Space.WINDOW), height // 2
        if not uk.Writing.rtl:
            self.coords("close", width - margin, middle + uk.px(uk.Depth.CONTROL) // 2)
            return
        # Right to left the head hangs from the right edge, so it follows every new width.
        self.coords("close", margin, middle + uk.px(uk.Depth.CONTROL) // 2)
        x = width - margin
        mark = mark_photo()
        if mark is not None:
            self.coords("mark", x, middle)
            x -= mark.width() + uk.px(uk.Space.MD)
        self.coords("title", x, middle)

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
