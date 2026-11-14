"""One click target made of many Tk widgets: a settings row, an entry of a popover.

Tk delivers a click to the innermost widget under the pointer, and a row consists of several (frame, text box,
title, description, status dot). Binding a list of parts by hand left every forgotten part dead: in a popover entry
only the text reacted, not the free space next to it, and the hover flickered off and on while the pointer crossed
from one part to the next.

`PressArea` joins a widget *and everything inside it* to one click target. Hover is decided by the pointer position
against the whole area, not by Enter/Leave of single parts, so it does not flicker when the pointer crosses from
one part to the next. Pressing works like a Windows button: the press holds until release, sliding off lifts it,
sliding back sinks it again, and a click fires on release over the area.

All areas share **two class bind tags**, bound once per Tcl interpreter (`PressRegistry`); a registry maps each
widget path to its area. An earlier version gave every area its own tag with its own bindings, and Tkinter never
frees the Tcl commands of `bind_class`: each popover left 18 behind.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable

MEMBER_TAG = "HamsPress"
WATCH_TAG = "HamsPressWatch"


class PressRegistry:
    """Which area a widget belongs to, and the two shared bindings that dispatch to it."""

    _members: dict[tuple[str, str], PressArea] = {}
    _watched: dict[tuple[str, str], list[PressArea]] = {}
    _bound: set[str] = set()

    @staticmethod
    def _key(widget: tk.Misc) -> tuple[str, str]:
        return (str(widget.tk), str(widget))

    @classmethod
    def ensure_bound(cls, widget: tk.Misc) -> None:
        interp = str(widget.tk)
        if interp in cls._bound:
            return
        cls._bound.add(interp)
        for sequence, handler in (
            ("<Enter>", cls._on_enter),
            ("<Leave>", cls._on_leave),
            ("<ButtonPress-1>", cls._on_press),
            ("<ButtonRelease-1>", cls._on_release),
        ):
            widget.bind_class(MEMBER_TAG, sequence, handler)
        widget.bind_class(WATCH_TAG, "<Enter>", cls._on_watch_enter)
        widget.bind_class(WATCH_TAG, "<Leave>", cls._on_watch_leave)

    @classmethod
    def add_member(cls, widget: tk.Misc, area: PressArea) -> None:
        cls._members[cls._key(widget)] = area

    @classmethod
    def add_watch(cls, widget: tk.Misc, area: PressArea) -> None:
        areas = cls._watched.setdefault(cls._key(widget), [])
        if area not in areas:
            areas.append(area)

    @classmethod
    def forget(cls, area: PressArea) -> None:
        for key in [key for key, member in cls._members.items() if member is area]:
            del cls._members[key]
        for key in list(cls._watched):
            cls._watched[key] = [other for other in cls._watched[key] if other is not area]
            if not cls._watched[key]:
                del cls._watched[key]

    @classmethod
    def area_of(cls, widget) -> PressArea | None:
        try:
            return cls._members.get(cls._key(widget))
        except (AttributeError, tk.TclError):
            return None

    # ------------------------------------------------------------ dispatch

    @classmethod
    def _on_enter(cls, event):
        area = cls.area_of(event.widget)
        if area is not None:
            area.on_enter(event)

    @classmethod
    def _on_leave(cls, event):
        area = cls.area_of(event.widget)
        if area is not None:
            area.on_leave(event)

    @classmethod
    def _on_press(cls, event):
        area = cls.area_of(event.widget)
        if area is None:
            return None
        area.on_press(event)
        return "break"  # keeps the click from also reaching the window (drag) or a popover's click-outside

    @classmethod
    def _on_release(cls, event):
        area = cls.area_of(event.widget)
        if area is None:
            return None
        area.on_release(event)
        return "break"

    @classmethod
    def _on_watch_enter(cls, event):
        for area in cls._watched.get(cls._key(event.widget), ()):
            area.on_enter(event)

    @classmethod
    def _on_watch_leave(cls, event):
        for area in list(cls._watched.get(cls._key(event.widget), ())):
            area.on_leave(event)


class PressArea:
    """Makes `owner` and all its descendants one click and hover target.

    `command()` runs on a completed click while `enabled()` is true. `on_hover(bool)` reports when the pointer
    enters or leaves the area, `on_press(bool)` when it is pressed down or lets go (for a pressed look).
    `watch(widget)` adds a widget that lies over the area but keeps its own click (the switch of a row): it only
    keeps the hover alive while the pointer is on it.
    """

    def __init__(
        self,
        owner: tk.Misc,
        command: Callable[[], object],
        *,
        on_hover: Callable[[bool], object] | None = None,
        on_press: Callable[[bool], object] | None = None,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._owner = owner
        self._command = command
        self._on_hover = on_hover
        self._on_press_look = on_press
        self._enabled = enabled or (lambda: True)
        self._members: list[tk.Misc] = []
        self._armed = False
        self._pressed_look = False
        self._hovered = False
        PressRegistry.ensure_bound(owner)
        owner.bind("<Destroy>", self._on_destroy, add="+")
        self.join(owner)

    # ------------------------------------------------------------ members

    def join(self, widget: tk.Misc) -> None:
        """Adds `widget` and everything inside it to the area."""
        self._add_tag(widget, MEMBER_TAG)
        PressRegistry.add_member(widget, self)
        self._members.append(widget)
        for child in widget.winfo_children():
            self.join(child)

    def watch(self, widget: tk.Misc) -> None:
        """A widget over the area with a click of its own: it keeps the hover, never triggers `command`."""
        self._add_tag(widget, WATCH_TAG)
        PressRegistry.add_watch(widget, self)

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

    def on_enter(self, _event=None) -> None:
        self._set_hover(True)
        if self._armed:
            self._set_pressed_look(True)

    def on_leave(self, event) -> None:
        # Crossing from one part to the next sends a Leave whose point still lies on the area: the hover stays.
        if not self.contains(event.x_root, event.y_root):
            self._set_hover(False)
            self._set_pressed_look(False)

    def on_press(self, _event=None) -> None:
        self._armed = self._enabled()
        self._set_pressed_look(self._armed)

    def on_release(self, event) -> None:
        armed, self._armed = self._armed, False
        self._set_pressed_look(False)
        if armed and self._enabled() and self.contains(event.x_root, event.y_root):
            self._command()

    def _set_hover(self, inside: bool) -> None:
        if inside == self._hovered:
            return
        self._hovered = inside
        if self._on_hover is not None:
            self._on_hover(inside)

    def _set_pressed_look(self, pressed: bool) -> None:
        if pressed == self._pressed_look:
            return
        self._pressed_look = pressed
        if self._on_press_look is not None:
            self._on_press_look(pressed)

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self._owner:
            PressRegistry.forget(self)
            self._members.clear()
