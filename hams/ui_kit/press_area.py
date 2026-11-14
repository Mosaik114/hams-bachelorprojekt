"""One click target made of many Tk widgets: a settings row, an entry of a popover.

Tk delivers a click to the innermost widget under the pointer, and a row consists of several (frame, text box,
title, description, status dot). Binding a list of parts by hand left every forgotten part dead: in a popover entry
only the text reacted, not the free space next to it, and the hover flickered off and on while the pointer crossed
from one part to the next.

`PressArea` joins a widget *and everything inside it* through one shared bind tag, so no part can be forgotten.
Hover is decided by the pointer position against the whole area, not by Enter/Leave of single parts, so it no
longer flickers when the pointer crosses from one part to the next. A click fires on release while the pointer is
still over the area, like a button: pressing and sliding off cancels it.
"""

from __future__ import annotations

import contextlib
import itertools
import tkinter as tk
from collections.abc import Callable

_ids = itertools.count(1)


class PressArea:
    """Makes `owner` and all its descendants one click and hover target.

    `command()` runs on a completed click while `enabled()` is true. `on_hover(bool)` reports when the pointer
    enters or leaves the area. `watch(widget)` adds a widget that lies over the area but keeps its own click (the
    switch of a row): it only keeps the hover alive while the pointer is on it.
    """

    def __init__(
        self,
        owner: tk.Misc,
        command: Callable[[], object],
        *,
        on_hover: Callable[[bool], object] | None = None,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._owner = owner
        self._command = command
        self._on_hover = on_hover
        self._enabled = enabled or (lambda: True)
        number = next(_ids)
        self._tag = f"HamsPressArea{number}"
        self._watch_tag = f"HamsPressWatch{number}"
        self._members: list[tk.Misc] = []
        self._pressed = False
        self._hovered = False
        owner.bind_class(self._tag, "<Enter>", self._on_enter)
        owner.bind_class(self._tag, "<Leave>", self._on_leave)
        owner.bind_class(self._tag, "<ButtonPress-1>", self._on_press)
        owner.bind_class(self._tag, "<ButtonRelease-1>", self._on_release)
        owner.bind_class(self._watch_tag, "<Enter>", self._on_enter)
        owner.bind_class(self._watch_tag, "<Leave>", self._on_leave)
        owner.bind("<Destroy>", self._on_destroy, add="+")
        self.join(owner)

    # ------------------------------------------------------------ members

    def join(self, widget: tk.Misc) -> None:
        """Adds `widget` and everything inside it to the area."""
        self._add_tag(widget, self._tag)
        self._members.append(widget)
        for child in widget.winfo_children():
            self.join(child)

    def watch(self, widget: tk.Misc) -> None:
        """A widget over the area with a click of its own: it keeps the hover, never triggers `command`."""
        self._add_tag(widget, self._watch_tag)

    @staticmethod
    def _add_tag(widget: tk.Misc, tag: str) -> None:
        tags = widget.bindtags()
        if tag not in tags:
            # Right after the widget's own tag: before the class and window bindings, so a "break" here keeps
            # the click from also reaching them (a window drag, a popover's click-outside).
            widget.bindtags((tags[0], tag, *tags[1:]))

    def set_cursor(self, cursor: str) -> None:
        for widget in self._members:
            with contextlib.suppress(tk.TclError):
                widget.configure(cursor=cursor)

    @property
    def hovered(self) -> bool:
        return self._hovered

    # ------------------------------------------------------------ events

    def contains(self, x_root: int, y_root: int) -> bool:
        """Whether a screen point lies on the area (the owner's rectangle, with everything over it)."""
        owner = self._owner
        try:
            left, top = owner.winfo_rootx(), owner.winfo_rooty()
            return left <= x_root < left + owner.winfo_width() and top <= y_root < top + owner.winfo_height()
        except tk.TclError:
            return False

    def _on_enter(self, _event=None) -> None:
        self._set_hover(True)

    def _on_leave(self, event: tk.Event) -> None:
        # Crossing from one part to the next sends a Leave whose point still lies on the area: the hover stays.
        if not self.contains(event.x_root, event.y_root):
            self._set_hover(False)

    def _set_hover(self, inside: bool) -> None:
        if inside == self._hovered:
            return
        self._hovered = inside
        if not inside:
            self._pressed = False
        if self._on_hover is not None:
            self._on_hover(inside)

    def _on_press(self, _event=None) -> str:
        self._pressed = self._enabled()
        return "break"

    def _on_release(self, event: tk.Event) -> str:
        fired, self._pressed = self._pressed, False
        if fired and self._enabled() and self.contains(event.x_root, event.y_root):
            self._command()
        return "break"

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self._owner:
            return
        for tag in (self._tag, self._watch_tag):
            for sequence in ("<Enter>", "<Leave>", "<ButtonPress-1>", "<ButtonRelease-1>"):
                with contextlib.suppress(tk.TclError):
                    self._owner.unbind_class(tag, sequence)
