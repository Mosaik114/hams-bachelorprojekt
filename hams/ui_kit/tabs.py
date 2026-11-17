"""A row of tabs: words on the paper, the chosen one on a sheet of its own.

Before, the settings had six stone buttons, and the chosen one wore the fill of the main action ("press me"). A
tab says where you are, not what to do: the tabs here have no stone in rest (role `tab`), the chosen one lies on a
paper sheet with ink edge and its word in the accent colour (role `tab_selected`).

Keyboard like a tab row in Windows: the row is *one* stop in the Tab order (the chosen tab), the arrow keys move
to the neighbour and show it at once, Home and End jump to the ends.

The tabs keep their natural width (the word plus padding) and wrap into a second row when the window is narrow;
nothing is stretched.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable, Sequence

from hams.ui_kit.buttons import SecondaryButton
from hams.ui_kit.scale import px
from hams.ui_kit.text import Writing
from hams.ui_kit.theme import Height, Space


class TabBar(tk.Frame):
    """Tabs for `titles`; `on_select(index)` runs when the user chooses one (click or keys)."""

    #: Padding left and right of a tab's word (logical pixels); tabs are slimmer than buttons.
    PADDING = Space.LG
    #: Gap between two tabs (logical pixels).
    GAP = Space.XS

    def __init__(
        self, master: tk.Misc, titles: Sequence[str], on_select: Callable[[int], object], *, bg_under: str
    ) -> None:
        super().__init__(master, bg=bg_under, height=1)
        self._on_select = on_select
        self.selected = -1
        self._places: list[tuple[int, int]] = []
        self.buttons: list[SecondaryButton] = []
        for index, title in enumerate(titles):
            button = SecondaryButton(
                self, title, lambda i=index: self._choose(i), bg_under=bg_under, height=Height.STANDARD
            )
            button.TEXT_PADDING = self.PADDING
            button.set_role("tab")
            button._update_request()
            back = 1 if Writing.rtl else -1  # right to left, the left arrow goes on to the next tab
            button.bind("<Left>", lambda _e, i=index, b=back: self._step(i, b), add="+")
            button.bind("<Right>", lambda _e, i=index, b=back: self._step(i, -b), add="+")
            button.bind("<Home>", lambda _e: self._jump(0), add="+")
            button.bind("<End>", lambda _e: self._jump(len(self.buttons) - 1), add="+")
            self.buttons.append(button)
        self.bind("<Configure>", lambda _e: self.lay_out(), add="+")

    # ------------------------------------------------------------ choosing

    def show(self, index: int) -> None:
        """Marks `index` as the chosen tab (look and Tab stop), without calling `on_select`."""
        self.selected = index
        for number, button in enumerate(self.buttons):
            chosen = number == index
            button.set_role("tab_selected" if chosen else "tab")
            # One stop in the Tab order: only the chosen tab takes the focus, the arrows reach the others.
            with contextlib.suppress(tk.TclError):
                button.configure(takefocus=1 if chosen else 0)

    def _choose(self, index: int) -> None:
        if 0 <= index < len(self.buttons):
            self.show(index)
            self._on_select(index)

    def _step(self, index: int, direction: int) -> str:
        self._jump(index + direction)
        return "break"  # the arrows belong to the tabs here, not to the scroll area

    def _jump(self, index: int) -> str:
        index = max(0, min(len(self.buttons) - 1, index))
        if index != self.selected:
            self._choose(index)
        with contextlib.suppress(tk.TclError):
            self.buttons[index].focus_set()
        return "break"

    # ------------------------------------------------------------ layout

    def lay_out(self) -> None:
        """Places the tabs left to right at their natural width; a tab that does not fit starts a new row."""
        try:
            width = self.winfo_width()
        except tk.TclError:  # pragma: no cover - the window is going away
            return
        if width <= 1 or not self.buttons:
            return
        gap = px(self.GAP)
        x = y = 0
        row_height = max(button.winfo_reqheight() for button in self.buttons)
        places = []
        for button in self.buttons:
            wide = button.winfo_reqwidth()
            if x > 0 and x + wide > width:
                x, y = 0, y + row_height + gap
            places.append((x, y))
            x += wide + gap
        if Writing.rtl:  # the first tab on the right
            places = [
                (width - left - button.winfo_reqwidth(), top)
                for button, (left, top) in zip(self.buttons, places, strict=True)
            ]
        if places == self._places:
            return
        self._places = places
        for button, (left, top) in zip(self.buttons, places, strict=True):
            button.place(x=left, y=top)
        self.configure(height=y + row_height)

    @property
    def rows(self) -> int:
        return len({top for _left, top in self._places})
