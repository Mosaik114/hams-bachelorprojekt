"""A short label that names an icon button when the pointer rests on it, or when the keyboard reaches it.

The icon buttons (gear, cross, cancel) carry no word of their own. Their names stood ready in both languages, but
`IconButton` threw the text away. `Tooltip` shows it after a short rest (`DELAY_MS`) in a small floating sheet in
the look of the popover: the popover paper with an ink edge. It goes away on leaving, on a click and when the
focus moves on.
"""

from __future__ import annotations

import contextlib
import tkinter as tk

from hams.ui_kit.scale import font, px
from hams.ui_kit.states import InputModality
from hams.ui_kit.theme import Space, Theme


class Tooltip:
    """Names `widget` with `text`; one tip per widget, it cleans up after itself."""

    #: How long the pointer rests before the tip shows (ms).
    DELAY_MS = 600
    #: Distance between the widget and the tip (logical pixels).
    GAP = Space.SM

    def __init__(self, widget: tk.Misc, text: str) -> None:
        self.widget = widget
        self.text = text
        self._job: str | None = None
        self.tip: tk.Toplevel | None = None
        for sequence, handler in (
            ("<Enter>", self._schedule),
            ("<Leave>", self.hide),
            ("<ButtonPress>", self.hide),
            ("<FocusIn>", self._on_focus_in),
            ("<FocusOut>", self.hide),
            ("<Destroy>", self._on_destroy),
        ):
            widget.bind(sequence, handler, add="+")

    @property
    def shown(self) -> bool:
        return self.tip is not None

    def _schedule(self, _event=None) -> None:
        if not self.text or self._job is not None or self.tip is not None:
            return
        with contextlib.suppress(tk.TclError):
            self._job = self.widget.after(self.DELAY_MS, self.show)

    def _on_focus_in(self, _event=None) -> None:
        if InputModality.keyboard:
            self.show()

    def show(self) -> None:
        self._job = None
        if self.tip is not None or not self.text:
            return
        try:
            if not self.widget.winfo_viewable():
                return
            tip = tk.Toplevel(self.widget)
        except tk.TclError:  # the widget is going away
            return
        tip.withdraw()
        tip.overrideredirect(True)
        with contextlib.suppress(tk.TclError):
            tip.attributes("-topmost", True)
        # the ink edge is the frame around the label
        tip.configure(bg=Theme.INK)
        label = tk.Label(
            tip,
            text=self.text,
            bg=Theme.POPOVER_SURFACE,
            fg=Theme.TEXT_PRIMARY,
            font=font("description"),
            padx=px(Space.MD),
            pady=px(Space.XS),
        )
        label.pack(padx=max(1, px(1)), pady=max(1, px(1)))
        tip.update_idletasks()
        x, y = self.place_for(tip.winfo_reqwidth(), tip.winfo_reqheight())
        tip.geometry(f"+{x}+{y}")
        tip.deiconify()
        self.tip = tip

    def place_for(self, width: int, height: int) -> tuple[int, int]:
        """Below the widget and centred on it; above it when the screen ends below; never off the left edge."""
        widget = self.widget
        left, top = widget.winfo_rootx(), widget.winfo_rooty()
        x = left + widget.winfo_width() // 2 - width // 2
        y = top + widget.winfo_height() + px(self.GAP)
        if y + height > widget.winfo_screenheight():
            y = top - px(self.GAP) - height
        x = max(0, min(x, widget.winfo_screenwidth() - width))
        return x, y

    def hide(self, _event=None) -> None:
        if self._job is not None:
            with contextlib.suppress(tk.TclError, ValueError):
                self.widget.after_cancel(self._job)
            self._job = None
        tip, self.tip = self.tip, None
        if tip is not None:
            with contextlib.suppress(tk.TclError):
                tip.destroy()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self.widget:
            self.hide()
