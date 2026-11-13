"""`UiDispatcher`: background threads reach the Tk thread without ever waiting for it."""

import threading
import time
import tkinter as tk

import pytest

from hams.ui_dispatch import UiDispatcher


@pytest.fixture
def window(root):
    top = tk.Toplevel(root)
    top.withdraw()
    yield top
    if top.winfo_exists():
        top.destroy()
    root.update()


def from_thread(fn) -> float:
    """Runs fn on a fresh thread and returns how long it took."""
    start = time.monotonic()
    thread = threading.Thread(target=fn)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    return time.monotonic() - start


def pump(window, condition, timeout=2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        window.update()
        if condition():
            return True
        time.sleep(0.005)
    return False


class TestUiDispatcher:
    def test_a_foreign_thread_never_waits_for_tk(self, window):
        dispatcher = UiDispatcher(window)
        ran = []
        # Tk is not serviced while the thread runs; a cross-thread root.after would hang here.
        assert from_thread(lambda: dispatcher.call(lambda: ran.append(1))) < 0.5
        assert ran == []
        assert pump(window, lambda: ran == [1])
        dispatcher.close()

    def test_order_is_kept(self, window):
        dispatcher = UiDispatcher(window)
        ran = []
        from_thread(lambda: [dispatcher.call(lambda i=i: ran.append(i)) for i in range(20)])
        assert pump(window, lambda: len(ran) == 20)
        assert ran == list(range(20))
        dispatcher.close()

    def test_callbacks_run_on_the_tk_thread(self, window):
        dispatcher = UiDispatcher(window)
        threads = []
        from_thread(lambda: dispatcher.call(lambda: threads.append(threading.get_ident())))
        assert pump(window, lambda: threads)
        assert threads == [threading.get_ident()]
        dispatcher.close()

    def test_a_broken_callback_does_not_stop_the_others(self, window, caplog):
        dispatcher = UiDispatcher(window)
        ran = []

        def broken():
            raise RuntimeError("kaputt")

        def produce():
            dispatcher.call(broken)
            dispatcher.call(lambda: ran.append("after"))

        from_thread(produce)
        assert dispatcher.drain() == 2
        assert ran == ["after"]
        assert "UI callback failed" in caplog.text
        dispatcher.close()

    def test_on_the_tk_thread_it_schedules_directly(self, window):
        dispatcher = UiDispatcher(window)
        ran = []
        dispatcher.call(lambda: ran.append(1))
        assert dispatcher.drain() == 0, "nothing goes through the queue"
        window.update()
        assert ran == [1]
        dispatcher.close()

    def test_after_close_everything_is_dropped(self, window):
        dispatcher = UiDispatcher(window)
        ran = []
        from_thread(lambda: dispatcher.call(lambda: ran.append("queued")))
        dispatcher.close()
        dispatcher.call(lambda: ran.append("late"))
        from_thread(lambda: dispatcher.call(lambda: ran.append("late too")))
        window.update()
        assert dispatcher.drain() == 0
        assert ran == []
        assert dispatcher.closed

    def test_it_stops_polling_once_the_window_is_gone(self, window):
        dispatcher = UiDispatcher(window, poll_ms=1)
        window.destroy()
        assert pump(window.master, lambda: dispatcher.closed)

    def test_a_dead_window_on_the_tk_thread_is_quiet(self, window):
        dispatcher = UiDispatcher(window)
        dispatcher.close()
        window.destroy()
        dispatcher._closed = False  # simulate a call that races the shutdown
        dispatcher.call(lambda: None)
