"""Giving memory back while Hams is idle (`IdleRelease`).

After a while without dictation (`[model] release_after`, default 10 minutes) the speech model gives its weights back
to the graphics card (`ModelManager.park`); while a game runs full screen (`[model] release_for_games`) already after
30 seconds, and then the cleanup model in Ollama goes too, if it is one Hams loaded. The next hotkey loads the speech
model again while the user speaks (`ModelManager.wake`).

The decision is taken on the Tk thread every few seconds and costs a few attribute reads and one Windows call
(`SHQueryUserNotificationState`, the same as the level at the pointer uses). Giving back runs on a thread of its own
and never waits: a model in use is simply left for the next round.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
import tkinter as tk

from hams.config import CFG, logger

#: Idle time after which the memory goes while a full-screen application (a game, also a video) is in front.
GAME_SECONDS = 30.0
#: How often the decision is taken (ms).
CHECK_MS = 10_000


def full_screen_detector():
    """A function telling whether a full-screen application is in front; on Linux always False."""
    if sys.platform != "win32":
        return lambda: False
    from hams.cursor_level import Win32Desktop

    desktop = None

    def full_screen() -> bool:
        nonlocal desktop
        try:
            if desktop is None:
                desktop = Win32Desktop()
            return bool(desktop.fullscreen())
        except Exception:  # noqa: BLE001 - not knowing means no game
            logger.debug("Full-screen query failed", exc_info=True)
            return False

    return full_screen


class IdleRelease:
    """Parks the speech model after the idle time and frees Ollama's model during games. One per app.

    `app` is the running app: its recorder, pipeline, model manager and Ollama client. `full_screen` and `clock` are
    replaced in tests; `spawn` starts the worker (a thread; tests run it inline).
    """

    def __init__(self, app, *, full_screen=None, clock=time.monotonic, spawn=None) -> None:
        self.app = app
        self._clock = clock
        self._full_screen = full_screen or full_screen_detector()
        self._spawn = spawn or self._thread
        self._last_activity = clock()
        self._job: str | None = None
        #: A worker is giving memory back right now.
        self._working = False
        #: The current full-screen spell has had its memory given back already (Ollama is asked once per spell).
        self._game_handled = False

    # ------------------------------------------------------------------ timer

    def start(self) -> None:
        self._schedule()

    def stop(self) -> None:
        if self._job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.app.root.after_cancel(self._job)
            self._job = None

    def _schedule(self) -> None:
        self._job = self.app.root.after(CHECK_MS, self._tick)

    def _tick(self) -> None:
        self._job = None
        try:
            self.check()
        except Exception:  # noqa: BLE001 - the timer must keep running
            logger.exception("Idle check failed")
        if not self.app._shutting_down:
            self._schedule()

    # ------------------------------------------------------------------ decision

    def touch(self) -> None:
        """Something happened (a recording started or ended): the idle time starts again. Any thread."""
        self._last_activity = self._clock()

    def busy(self) -> bool:
        """A recording, a dictation in the queue, a download or a model load: nothing is given back now."""
        app = self.app
        models = app.models
        return bool(app.is_recording or app.pipeline.pending() or models.downloading is not None or models.is_switching)

    @staticmethod
    def limit(full_screen: bool) -> float | None:
        """Seconds of idle time before the memory goes; None: never."""
        minutes = CFG.model_release_minutes
        regular = minutes * 60.0 if minutes > 0 else None
        if full_screen and CFG.model_release_for_games:
            return GAME_SECONDS if regular is None else min(regular, GAME_SECONDS)
        return regular

    def check(self) -> None:
        """One decision, on the Tk thread."""
        if self._working or self.app._shutting_down:
            return
        if self.busy():
            self.touch()
            return
        full_screen = bool(CFG.model_release_for_games) and self._full_screen()
        if not full_screen:
            self._game_handled = False
        limit = self.limit(full_screen)
        if limit is None or self._clock() - self._last_activity < limit:
            return
        game = full_screen and not self._game_handled
        if not game and not self.app.models.can_park():
            return  # parked already, or nothing to give back
        if game:
            self._game_handled = True
            logger.info("Full-screen application in front: giving memory back")
        self._start(ollama=game)

    def release_now(self) -> bool:
        """Tray "Free memory now": the speech model and Hams' Ollama model, unless a dictation runs. Tk thread."""
        if self._working or self.busy():
            logger.info("Free memory now: Hams is busy, nothing given back")
            return False
        logger.info("Free memory now")
        self._start(ollama=True)
        return True

    # ------------------------------------------------------------------ work

    def _start(self, *, ollama: bool) -> None:
        self._working = True
        self._spawn(self._work, self.app.models.wake_count(), ollama)

    @staticmethod
    def _thread(target, *args) -> None:
        threading.Thread(target=target, args=args, daemon=True, name="memory-release").start()

    def _work(self, wakes: int, ollama: bool) -> None:
        """On the worker: park the speech model, then (in a game or from the tray) Ollama's model."""
        try:
            parked = self.app.models.park(wakes)
            if not parked and self.app.models.can_park():
                logger.info("Model in use, parking it at the next check")
            if ollama:
                released = self.app.ollama.release_own_models()
                if released:
                    self.app.pipeline.forget_warm_up()
                    logger.info("Ollama model(s) unloaded: %s", ", ".join(released))
        except Exception:  # noqa: BLE001 - boundary of the thread
            logger.exception("Giving memory back failed")
        finally:
            self._working = False
