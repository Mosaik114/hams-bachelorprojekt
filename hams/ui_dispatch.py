"""Hands work from background threads to the Tk thread without ever blocking them.

Tkinter is not thread-safe. A call such as ``root.after(0, fn)`` from another
thread is marshalled to the Tk thread by `_tkinter`, and the calling thread
*waits* until the Tk thread has serviced it - or, outside ``mainloop``, fails
after a second with "main thread is not in main loop". Waiting is fatal for a
thread the Tk thread itself waits for: the hotkey thread calls a handler, the
handler calls ``root.after``, and at the same moment the Tk thread joins the
hotkey thread in `SystemHotkeys.stop`. Both wait until the join times out, and
the old thread keeps its `RegisterHotKey` registrations.

So `UiDispatcher.call` never touches Tk from a foreign thread. It only puts the
callback into a queue, which the Tk thread drains every `POLL_MS` (a few
microseconds of work when empty). Order is kept (FIFO). Called on the Tk
thread itself, `call` schedules directly with ``after(0)``.

After `close` callbacks are dropped quietly: a late result of a background
thread must not crash the shutdown.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import tkinter as tk
from collections.abc import Callable

logger = logging.getLogger("hams")

#: How often the Tk thread looks for work from other threads. Short enough that a
#: hotkey feels instant, long enough to cost nothing measurable.
POLL_MS = 20


class UiDispatcher:
    """Thread-safe, non-blocking ``after(0, fn)`` for one Tk root."""

    def __init__(self, root, poll_ms: int = POLL_MS) -> None:
        self.root = root
        self.poll_ms = poll_ms
        #: The thread that owns Tk - the one creating the dispatcher.
        self.tk_thread = threading.get_ident()
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._closed = False
        self._job = None
        # Mit dem Fenster verschwinden auch seine Tcl-Befehle; danach gibt es niemanden mehr zu bedienen.
        root.bind("<Destroy>", self._on_destroy, add="+")
        self._schedule_drain()

    def _on_destroy(self, event) -> None:
        if event.widget is self.root:
            self._closed = True
            self._job = None

    @property
    def closed(self) -> bool:
        return self._closed

    def call(self, fn: Callable[[], object]) -> None:
        """Runs ``fn`` on the Tk thread soon. Never blocks, never raises."""
        if self._closed:
            return
        if threading.get_ident() != self.tk_thread:
            self._queue.put(fn)
            return
        try:
            self.root.after(0, fn)
        except tk.TclError:  # the window is gone
            logger.debug("UI callback dropped, Tk is gone")

    def drain(self) -> int:
        """On the Tk thread: runs everything queued so far. Returns how many callbacks ran."""
        count = 0
        while not self._closed:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                break
            count += 1
            try:
                fn()
            except Exception:  # noqa: BLE001 - one broken callback must not stop the others
                logger.exception("UI callback failed")
        return count

    def close(self) -> None:
        """Stops polling and drops whatever is still queued."""
        self._closed = True
        if self._job is not None:
            with contextlib.suppress(tk.TclError, ValueError):
                self.root.after_cancel(self._job)
            self._job = None

    def _poll(self) -> None:
        self._job = None
        try:
            self.drain()
        finally:
            self._schedule_drain()

    def _schedule_drain(self) -> None:
        if self._closed:
            return
        try:
            if self.root.winfo_exists():
                self._job = self.root.after(self.poll_ms, self._poll)
                return
        except tk.TclError:
            pass
        self._closed = True  # the window is gone: nothing left to serve
