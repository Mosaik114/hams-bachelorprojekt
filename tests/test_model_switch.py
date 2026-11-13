"""Tests des Laufzeit-Modellwechsels.

Die Nebenläufigkeit wird hier ohne echte Whisper-Instanzen geprüft: `_build_model`
ist der einzige Punkt, der tatsächlich lädt, und lässt sich ersetzen. Der reale
Durchlauf mit GPU-Speichermessung lief als Integrationstest.
"""

import threading
import time

import pytest

from hams.whisper_model import ModelManager


@pytest.fixture
def app(live_app, monkeypatch):
    """The live app with `turbo` loaded on the GPU; states and saved settings are recorded."""
    models = live_app.models
    models.model = object()
    models.active_model_size = "turbo"
    models.model_ready.set()
    models.active_device = "cuda"
    models.active_compute_type = "float16"
    models._phase = "ready"
    live_ui_call = models._ui_call

    def ui_call(fn):
        """Results of a synchronous load come back at once; a load thread still goes through the dispatcher."""
        if threading.current_thread() is threading.main_thread():
            fn()
        else:
            live_ui_call(fn)

    models._ui_call = ui_call
    live_app.states = []
    live_app.persisted = []
    original = live_app.apply_state

    def record(state, detail=None, hint=None, **options):
        live_app.states.append((state, detail, hint))
        original(state, detail, hint, **options)

    monkeypatch.setattr(live_app, "apply_state", record)
    monkeypatch.setattr(live_app, "_on_model_error", lambda: live_app.states.append(("error", None, None)))
    monkeypatch.setattr(
        "hams.whisper_model._persist_config_value", lambda section, key, value: live_app.persisted.append(value)
    )
    return live_app


def fake_build(loadable=("turbo", "tiny", "small"), delay=0.0):
    def _build(self, model_id):
        if delay:
            time.sleep(delay)
        if model_id in loadable:
            return object(), "cuda", "float16"
        return None, None, f"{model_id}: nicht ladbar"

    return _build


def run_switch(app, model_id, monkeypatch, **kwargs):
    """Wechsel synchron ausfuehren — deterministisch statt ueber einen Thread."""
    monkeypatch.setattr(ModelManager, "_build_model", fake_build(**kwargs))
    monkeypatch.setattr(ModelManager, "_start_switch_thread", lambda self, *args: self._switch_worker(*args))
    return app.switch_model(model_id)


class TestGuards:
    def test_switching_to_the_active_model_is_a_no_op(self, app, monkeypatch):
        assert run_switch(app, "turbo", monkeypatch) is False

    def test_recording_blocks_the_switch(self, app, monkeypatch):
        app.recorder.is_recording = True
        assert run_switch(app, "tiny", monkeypatch) is False
        texts = [(detail or "") + (hint or "") for _s, detail, hint in app.states]
        assert any("Aufnahme" in text for text in texts), texts

    def test_a_second_switch_supersedes_the_running_one(self, app, monkeypatch):
        started = []
        monkeypatch.setattr(
            ModelManager, "_start_switch_thread", lambda self, mid, gen, persist: started.append((mid, gen))
        )
        assert app.switch_model("tiny") is True
        assert app.switch_model("small") is True
        assert started == [("tiny", 1), ("small", 2)]
        assert app.models._switching_to == "small"

    def test_asking_again_for_the_model_on_its_way_starts_nothing(self, app, monkeypatch):
        started = []
        monkeypatch.setattr(ModelManager, "_start_switch_thread", lambda self, mid, gen, persist: started.append(mid))
        app.switch_model("tiny")
        assert app.switch_model("tiny") is True
        assert started == ["tiny"]


class TestHappyPath:
    def test_model_becomes_active(self, app, monkeypatch):
        run_switch(app, "tiny", monkeypatch)
        assert app.models.active_model_size == "tiny"
        assert app.models.model_ready.is_set()

    def test_config_is_written_only_after_success(self, app, monkeypatch):
        run_switch(app, "tiny", monkeypatch)
        assert app.persisted == ["tiny"]

    def test_switching_flag_is_cleared(self, app, monkeypatch):
        run_switch(app, "tiny", monkeypatch)
        assert not app.models.is_switching

    def test_repeated_switches(self, app, monkeypatch):
        for target in ("tiny", "small", "tiny", "turbo"):
            run_switch(app, target, monkeypatch)
        assert app.models.active_model_size == "turbo"
        assert app.persisted == ["tiny", "small", "tiny", "turbo"]


class TestFailure:
    def test_falls_back_to_the_previous_model(self, app, monkeypatch):
        run_switch(app, "kaputt", monkeypatch)
        assert app.models.active_model_size == "turbo", "Rückfall fehlt"
        assert app.models.model_ready.is_set(), "App bliebe ohne Modell zurück"

    def test_the_fallback_is_not_saved_over_the_choice(self, app, monkeypatch):
        run_switch(app, "kaputt", monkeypatch)
        assert app.persisted == [], "the fallback must not overwrite the user's choice"

    def test_the_fallback_is_said_out_loud(self, app, monkeypatch):
        run_switch(app, "kaputt", monkeypatch)
        # the synchronous test worker reports before switch_model shows "loading"
        state, detail, hint = next(entry for entry in app.states if entry[0] == "ready")
        assert detail == "kaputt ließ sich nicht laden"
        assert hint.startswith("Weiter mit Turbo")
        assert app.models.fallback == "kaputt"

    def test_a_later_good_switch_clears_the_fallback(self, app, monkeypatch):
        run_switch(app, "kaputt", monkeypatch)
        run_switch(app, "tiny", monkeypatch)
        assert app.models.fallback is None
        assert [entry for entry in app.states if entry[0] == "ready"][-1] == ("ready", None, None)

    def test_error_without_any_previous_model(self, app, monkeypatch):
        app.models.active_model_size = None
        run_switch(app, "kaputt", monkeypatch)
        assert not app.models.model_ready.is_set()
        assert app.models.model_error
        assert ("error", None, None) in app.states


class TestConcurrency:
    def test_transcription_keeps_its_own_reference(self, app):
        """Ein Wechsel darf einem laufenden Lauf das Modell nicht wegziehen."""
        original = app.models.current_model()
        with app.models.transcribe_lock:
            held = app.models.current_model()
            with app.models._lock:
                app.models.model = object()  # Wechsel schreibt dazwischen
            assert held is original

    def test_switch_waits_for_a_running_transcription(self, app, monkeypatch):
        monkeypatch.setattr(ModelManager, "_build_model", fake_build())
        order = []
        monkeypatch.setattr(app.models, "_ui_call", lambda fn: None)  # only the order counts here
        app.models.transcribe_lock.acquire()

        def worker():
            app.models._switch_worker("tiny", 1)
            order.append("switch")

        app.models._generation = 1
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        time.sleep(0.2)
        assert order == [], "Wechsel lief trotz laufender Transkription"
        order.append("transcription")
        app.models.transcribe_lock.release()
        thread.join(timeout=5)
        assert order == ["transcription", "switch"]

    def test_stale_load_does_not_win(self, app, monkeypatch):
        """Ein überholter Ladevorgang darf den neueren Zustand nicht kippen."""
        monkeypatch.setattr(ModelManager, "_build_model", fake_build())
        app.models._generation = 5
        app.models._switch_worker("tiny", 2)  # veraltete Generation
        assert app.models.active_model_size == "turbo"
        assert app.persisted == []

    def test_generation_increases_per_request(self, app, monkeypatch):
        before = app.models._generation
        run_switch(app, "tiny", monkeypatch)
        assert app.models._generation == before + 1


class TestSettingsLifecycle:
    def test_missing_settings_window_is_fine(self, app, monkeypatch):
        app.settings_win = None
        run_switch(app, "tiny", monkeypatch)
        assert app.models.active_model_size == "tiny"

    def test_closed_settings_window_is_not_touched(self, app, monkeypatch):
        touched = []

        class _Dead:
            def alive(self):
                return False

            def on_model_changed(self):
                touched.append(1)

        app.settings_win = _Dead()
        run_switch(app, "tiny", monkeypatch)
        assert touched == [], "Zugriff auf ein geschlossenes Fenster"

    def test_open_settings_window_is_notified(self, app, monkeypatch):
        touched = []

        class _Live:
            def alive(self):
                return True

            def on_model_changed(self):
                touched.append(1)

            def close(self):
                pass

        app.settings_win = _Live()
        run_switch(app, "tiny", monkeypatch)
        assert touched == [1]


# ------------------------------------------------------------------ lifecycle


class _Built:
    """Stands in for a loaded WhisperModel; remembers which model it is."""

    def __init__(self, model_id):
        self.model_id = model_id


def slow_build(delays, built):
    def _build(self, model_id):
        time.sleep(delays.get(model_id, 0.0))
        instance = _Built(model_id)
        built.append(model_id)
        return instance, "cpu", "int8"

    return _build


@pytest.fixture
def cold(app, hams, monkeypatch):
    """The app right after start: nothing loaded yet; `_load_model_async` is the real one."""
    monkeypatch.setattr(app, "_load_model_async", lambda: hams.load_model(app))
    app.models.model = None
    app.models.active_model_size = None
    app.models.model_ready.clear()
    app.models._phase = "idle"
    return app


def join_loads(timeout=5.0):
    """Waits for every model-load thread, so none outlives the monkeypatches of its test."""
    for thread in [t for t in threading.enumerate() if t.name == "model-load"]:
        thread.join(timeout)
        assert not thread.is_alive()


def wait_until(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class TestLifecycle:
    def test_a_switch_during_the_first_load_wins(self, cold, monkeypatch):
        """Regression: the slow first load finished last and overwrote the newer choice."""
        from hams.config import CFG

        built = []
        monkeypatch.setattr(ModelManager, "_build_model", slow_build({"small": 0.4, "turbo": 0.05}, built))
        monkeypatch.setattr(CFG, "model_size", "small")
        cold._load_model_async()
        time.sleep(0.05)
        assert cold.models.phase == "loading"
        assert cold.switch_model("turbo") is True
        join_loads()
        cold._ui_dispatcher.drain()  # the result reaches the app on the Tk thread
        assert cold.models.model.model_id == "turbo"
        assert cold.models.active_model_size == "turbo"
        assert cold.models.phase == "ready"
        assert cold.persisted == ["turbo"]
        assert built == ["small", "turbo"], "loads must run one after the other"

    def test_the_stale_model_is_freed(self, cold, monkeypatch):
        import weakref

        refs = []

        def build(self, model_id):
            if model_id == "small":
                time.sleep(0.3)
            instance = _Built(model_id)
            refs.append(weakref.ref(instance))
            return instance, "cpu", "int8"

        monkeypatch.setattr(ModelManager, "_build_model", build)
        cold.models.request("small", persist=False)
        time.sleep(0.05)
        cold.switch_model("turbo")
        join_loads()
        assert len(refs) == 2
        assert wait_until(lambda: refs[0]() is None), "the discarded model still lives"
        assert refs[1]() is cold.models.model

    def test_retry_while_loading_starts_no_second_load(self, cold, monkeypatch):
        started = []
        monkeypatch.setattr(ModelManager, "_start_switch_thread", lambda self, mid, gen, persist: started.append(mid))
        cold._load_model_async()
        cold.models.model_error = "old error"
        assert cold.reload_model() is False
        assert started == [cold.models._switching_to]
        assert cold.states[-1][0] == "model_loading"

    def test_retry_after_a_failure_loads_again(self, cold, monkeypatch):
        cold.models.model_error = "cuda missing"
        cold.models._phase = "failed"
        monkeypatch.setattr(ModelManager, "_build_model", fake_build())
        monkeypatch.setattr(ModelManager, "_start_switch_thread", lambda self, *args: self._switch_worker(*args))
        from hams.config import CFG

        monkeypatch.setattr(CFG, "model_size", "tiny")
        assert cold.reload_model() is True
        assert cold.models.model_ready.is_set() and cold.models.model_error is None
        assert cold.models.phase == "ready"
        assert cold.persisted == [], "the first load does not rewrite the setting"
        assert cold.states[-1][0] == "ready"

    def test_a_failed_first_load_ends_in_failed(self, cold, monkeypatch):
        monkeypatch.setattr(ModelManager, "_build_model", fake_build(loadable=()))
        monkeypatch.setattr(ModelManager, "_start_switch_thread", lambda self, *args: self._switch_worker(*args))
        cold._load_model_async()
        assert cold.models.phase == "failed"
        assert cold.models.model_error
        assert not cold.models.is_switching
        assert ("error", None, None) in cold.states

    def test_a_crashing_load_is_reported_not_silent(self, cold, monkeypatch):
        def broken(self, model_id):
            raise MemoryError("out of memory")

        monkeypatch.setattr(ModelManager, "_build_model", broken)
        monkeypatch.setattr(ModelManager, "_start_switch_thread", lambda self, *args: self._switch_worker(*args))
        cold._load_model_async()
        assert cold.models.phase == "failed"
        assert "MemoryError" in cold.models.model_error
        assert not cold.models.is_switching
        assert ("error", None, None) in cold.states

    def test_a_load_finishing_after_shutdown_is_discarded(self, cold, monkeypatch):
        gate = threading.Event()

        def build(self, model_id):
            gate.wait(5)
            return _Built(model_id), "cpu", "int8"

        monkeypatch.setattr(ModelManager, "_build_model", build)
        cold._load_model_async()
        cold.models.close()
        gate.set()
        join_loads()
        assert cold.models.model is None and not cold.models.model_ready.is_set()
        assert cold.models.phase == "idle"
        assert cold.states == []

    def test_switching_from_a_loaded_model_is_the_switching_phase(self, app, monkeypatch):
        monkeypatch.setattr(ModelManager, "_start_switch_thread", lambda self, *args: None)
        app.switch_model("tiny")
        assert app.models.phase == "switching"

    def test_an_old_result_shown_while_a_newer_load_runs_keeps_the_loading_state(self, app, monkeypatch):
        app.models._switching_to = "small"  # a newer request is under way
        app.models._finish_load("tiny", True, persist=True, requested="tiny")
        assert app.persisted == ["tiny"]
        assert app.states == [], "the newer load reports by itself"
