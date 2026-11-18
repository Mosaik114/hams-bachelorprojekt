"""The one thread that loads, gives back and frees the CTranslate2 model (`ModelThread`).

Measured with CTranslate2 4.8.5 (CUDA, its default allocator `cuda_malloc_async`) on an RTX 5070: a model loaded on
a thread that had ended and then given back (`unload_model`) from another thread made the process crash when it quit
(0xC0000409), also when it was loaded again in between. With loading, giving back, loading again and freeing on one
thread that lives as long as the process, every case ended with 0. Transcriptions may run on other threads, as
before; that never crashed. CTranslate2 #2136 (fixed in 4.8.5) was about the same family: its allocator and the
thread that allocated.

The other allocator, `cub_caching` (the workaround named in faster-whisper #71), avoided the crash as well, but kept
about 4 MB of graphics memory for every transcription thread that had ended, so Hams leaves the allocator alone.
"""

from __future__ import annotations

import collections
import logging
import threading
import time

logger = logging.getLogger("hams")


class _Job:
    """One callable for the thread, with its result or exception."""

    __slots__ = ("fn", "done", "result", "error", "waited")

    def __init__(self, fn, waited: bool) -> None:
        self.fn = fn
        self.done = threading.Event()
        self.result = None
        self.error: BaseException | None = None
        #: Someone waits for it (`call`) and gets the exception; otherwise it is logged here.
        self.waited = waited

    def run(self) -> None:
        try:
            self.result = self.fn()
        except BaseException as exc:  # noqa: BLE001 - handed to the caller or logged
            self.error = exc
            if not self.waited:
                logger.exception("Model thread job failed")
        finally:
            self.done.set()


class ModelThread:
    """Runs callables one after the other on a single daemon thread, started on first use.

    `submit` queues work and returns; `call` waits for the result and raises what the work raised. Urgent work goes
    before queued work, and also runs while the thread waits for a lock (`acquire`): a transcription that holds
    `transcribe_lock` and needs the model loaded must not wait for queued work that waits for that very lock.
    """

    #: How long `acquire` waits in one go before it looks for urgent work (s).
    POLL_SECONDS = 0.02

    def __init__(self, name: str = "model") -> None:
        self._name = name
        self._cv = threading.Condition()
        self._urgent: collections.deque[_Job] = collections.deque()
        self._queue: collections.deque[_Job] = collections.deque()
        self._thread: threading.Thread | None = None
        #: The thread ends once its queue is empty (`stop`); new work starts a new one.
        self._stopping = False

    @property
    def started(self) -> bool:
        return self._thread is not None

    def on_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def submit(self, fn, *, urgent: bool = False, waited: bool = False) -> _Job:
        job = _Job(fn, waited)
        with self._cv:
            (self._urgent if urgent else self._queue).append(job)
            self._stopping = False
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True, name=self._name)
                self._thread.start()
            self._cv.notify_all()
        return job

    def call(self, fn, *, urgent: bool = False, timeout: float | None = None):
        """`fn()` on the thread (at once when already there); its result, or its exception raised here.

        Raises:
            TimeoutError: the thread did not get to it within `timeout` seconds.
        """
        if self.on_thread():
            return fn()
        job = self.submit(fn, urgent=urgent, waited=True)
        if not job.done.wait(timeout):
            raise TimeoutError(f"model thread busy for {timeout} s")
        if job.error is not None:
            raise job.error
        return job.result

    def stop(self) -> None:
        """The thread ends after the work queued so far (at quit, once the model is freed)."""
        with self._cv:
            self._stopping = True
            self._cv.notify_all()

    def drain(self, timeout: float | None = None) -> bool:
        """Waits until the work queued so far is done; False after `timeout`. At once when never started."""
        if self._thread is None or self.on_thread():
            return True
        return self.submit(lambda: None).done.wait(timeout)

    def acquire(self, lock, timeout: float | None = None) -> bool:
        """`lock.acquire()`; on this thread it runs urgent work while it waits."""
        if not self.on_thread():
            return lock.acquire(timeout=-1 if timeout is None else timeout)
        deadline = None if timeout is None else time.monotonic() + timeout
        while not lock.acquire(timeout=self.POLL_SECONDS):
            self._run_urgent()
            if deadline is not None and time.monotonic() >= deadline:
                return False
        return True

    def _run_urgent(self) -> None:
        while True:
            with self._cv:
                if not self._urgent:
                    return
                job = self._urgent.popleft()
            job.run()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._urgent and not self._queue:
                    if self._stopping:
                        self._thread = None
                        return
                    self._cv.wait()
                job = self._urgent.popleft() if self._urgent else self._queue.popleft()
            job.run()
