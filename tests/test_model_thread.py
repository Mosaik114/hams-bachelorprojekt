"""The one thread that loads, gives back and frees the model (hams/model_thread.py).

Why it exists: with CTranslate2's default CUDA allocator a model loaded on a thread that had ended and given back
from another thread made Hams crash when it quit (0xC0000409, measured on an RTX 5070). These tests pin the
mechanics; the crash itself was checked by hand with the real model (see the report of 1.3.7).
"""

import threading
import time

import pytest
from test_idle_release import FakeWhisper, loaded_manager, wake_finished

from hams.model_thread import ModelThread
from hams.whisper_model import ModelManager


@pytest.fixture
def worker():
    thread = ModelThread("test-model")
    yield thread
    thread.stop()


class TestModelThread:
    def test_runs_everything_on_one_thread(self, worker):
        seen = {worker.call(threading.get_ident) for _ in range(5)}
        assert len(seen) == 1 and threading.get_ident() not in seen

    def test_call_hands_the_exception_over(self, worker):
        def broken():
            raise ValueError("kaputt")

        with pytest.raises(ValueError, match="kaputt"):
            worker.call(broken)

    def test_a_job_nobody_waits_for_is_logged(self, worker, caplog):
        def broken():
            raise ValueError("kaputt")

        worker.submit(broken)
        assert worker.drain(5)
        assert "Model thread job failed" in caplog.text

    def test_queued_work_runs_in_order(self, worker):
        order = []
        for number in range(5):
            worker.submit(lambda n=number: order.append(n))
        assert worker.drain(5)
        assert order == [0, 1, 2, 3, 4]

    def test_urgent_work_runs_while_the_thread_waits_for_a_lock(self, worker):
        """A transcription holds the lock and needs the model loaded; a queued load waits for that lock."""
        lock = threading.Lock()
        lock.acquire()
        waited = threading.Event()

        def wants_the_lock():
            waited.set()
            assert worker.acquire(lock)
            lock.release()

        worker.submit(wants_the_lock)
        assert waited.wait(5)
        assert worker.call(lambda: "loaded", urgent=True, timeout=5) == "loaded"
        lock.release()
        assert worker.drain(5)

    def test_acquire_can_give_up(self, worker):
        lock = threading.Lock()
        lock.acquire()
        assert worker.call(lambda: worker.acquire(lock, timeout=0.05)) is False
        lock.release()

    def test_a_call_on_the_thread_itself_runs_at_once(self, worker):
        assert worker.call(lambda: worker.call(lambda: 42)) == 42

    def test_a_call_that_does_not_come_back_in_time(self, worker):
        gate = threading.Event()
        worker.submit(lambda: gate.wait(5))
        with pytest.raises(TimeoutError):
            worker.call(lambda: None, timeout=0.05)
        gate.set()

    def test_stop_and_start_again(self, worker):
        first = worker.call(threading.get_ident)
        worker.stop()
        assert wait_for(lambda: not worker.started)
        assert worker.call(threading.get_ident) != first

    def test_drain_without_a_thread(self):
        assert ModelThread().drain(0) is True


def wait_for(condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class TestTheModelStaysOnItsThread:
    def test_load_park_reload_and_free_on_one_thread(self, monkeypatch):
        threads = {}

        class Engine:
            model_is_loaded = True

            def unload_model(self, to_cpu=False):
                threads.setdefault("unload", set()).add(threading.get_ident())
                self.model_is_loaded = False

            def load_model(self, keep_cache=False):
                threads.setdefault("load", set()).add(threading.get_ident())
                self.model_is_loaded = True

        class Model(FakeWhisper):
            def __init__(self):
                super().__init__()
                self.model = Engine()
                threads.setdefault("build", set()).add(threading.get_ident())

            def __del__(self):
                threads.setdefault("free", set()).add(threading.get_ident())

        monkeypatch.setattr(ModelManager, "_build_model", lambda self, model_id: (Model(), "cpu", "int8"))
        models = loaded_manager()
        models.model = None
        models.request("turbo", persist=False)
        assert models._thread.drain(5) and models.model_ready.is_set()
        models.park()
        models.wake()
        assert wake_finished(models) and models._thread.drain(5)
        models.park()
        with models.transcribe_lock:  # the transcription's safety net
            assert models.ensure_loaded()
        models.close()
        assert wait_for(lambda: "free" in threads)
        used = set().union(*threads.values())
        assert len(used) == 1 and threading.get_ident() not in used, threads
