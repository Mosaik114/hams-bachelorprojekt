"""Tests des Laufzeit-Modellwechsels.

Die Nebenläufigkeit wird hier ohne echte Whisper-Instanzen geprüft: `_build_model`
ist der einzige Punkt, der tatsächlich lädt, und lässt sich ersetzen. Der reale
Durchlauf mit GPU-Speichermessung lief als Integrationstest.
"""

import threading
import time

import pytest

from wisper.main import FloatingTranscriberApp as App


class _FakeRoot:
    """Ersetzt Tk: führt `after`-Rückrufe sofort aus."""

    def __init__(self):
        self.calls = []

    def after(self, _delay, callback=None, *args):
        if callback is not None:
            self.calls.append(callback)
            callback(*args)
        return "job"

    def after_cancel(self, _job):
        pass


@pytest.fixture
def app(monkeypatch):
    """Anwendung ohne Fenster, Tray und echtes Modell."""
    instance = App.__new__(App)
    instance.root = _FakeRoot()
    instance.model = object()
    instance.active_model_size = "turbo"
    instance.model_ready = threading.Event()
    instance.model_ready.set()
    instance.model_error = None
    instance._model_lock = threading.Lock()
    instance._transcribe_lock = threading.Lock()
    instance._model_generation = 0
    instance._switching_to = None
    instance._active_device = "cuda"
    instance._active_compute_type = "float16"
    instance.is_recording = False
    instance._prompt_mode = False
    instance.settings_win = None
    instance.states = []
    instance.persisted = []

    monkeypatch.setattr(App, "_apply_state",
                        lambda self, state, detail=None, hint=None:
                        self.states.append((state, detail, hint)))
    monkeypatch.setattr("wisper.main._persist_config_value",
                        lambda section, key, value: instance.persisted.append(value))
    monkeypatch.setattr(App, "_on_model_error", lambda self: self.states.append(("error", None, None)))
    return instance


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
    monkeypatch.setattr(App, "_build_model", fake_build(**kwargs))
    monkeypatch.setattr(App, "_start_switch_thread",
                        lambda self, mid, gen: self._switch_worker(mid, gen))
    return app.switch_model(model_id)


class TestGuards:
    def test_switching_to_the_active_model_is_a_no_op(self, app, monkeypatch):
        assert run_switch(app, "turbo", monkeypatch) is False

    def test_recording_blocks_the_switch(self, app, monkeypatch):
        app.is_recording = True
        assert run_switch(app, "tiny", monkeypatch) is False
        texts = [(detail or "") + (hint or "") for _s, detail, hint in app.states]
        assert any("Aufnahme" in text for text in texts), texts

    def test_a_second_switch_is_refused_while_one_runs(self, app, monkeypatch):
        monkeypatch.setattr(App, "_build_model", fake_build())
        app._switching_to = "tiny"
        assert app.switch_model("small") is False


class TestHappyPath:
    def test_model_becomes_active(self, app, monkeypatch):
        run_switch(app, "tiny", monkeypatch)
        assert app.active_model_size == "tiny"
        assert app.model_ready.is_set()

    def test_config_is_written_only_after_success(self, app, monkeypatch):
        run_switch(app, "tiny", monkeypatch)
        assert app.persisted == ["tiny"]

    def test_switching_flag_is_cleared(self, app, monkeypatch):
        run_switch(app, "tiny", monkeypatch)
        assert not app.is_switching_model

    def test_repeated_switches(self, app, monkeypatch):
        for target in ("tiny", "small", "tiny", "turbo"):
            run_switch(app, target, monkeypatch)
        assert app.active_model_size == "turbo"
        assert app.persisted == ["tiny", "small", "tiny", "turbo"]


class TestFailure:
    def test_falls_back_to_the_previous_model(self, app, monkeypatch):
        run_switch(app, "kaputt", monkeypatch)
        assert app.active_model_size == "turbo", "Rückfall fehlt"
        assert app.model_ready.is_set(), "App bliebe ohne Modell zurück"

    def test_config_keeps_the_working_model(self, app, monkeypatch):
        run_switch(app, "kaputt", monkeypatch)
        assert app.persisted == ["turbo"]

    def test_error_without_any_previous_model(self, app, monkeypatch):
        app.active_model_size = None
        run_switch(app, "kaputt", monkeypatch)
        assert not app.model_ready.is_set()
        assert app.model_error
        assert ("error", None, None) in app.states


class TestConcurrency:
    def test_transcription_keeps_its_own_reference(self, app):
        """Ein Wechsel darf einem laufenden Lauf das Modell nicht wegziehen."""
        original = app.current_model()
        with app._transcribe_lock:
            held = app.current_model()
            with app._model_lock:
                app.model = object()          # Wechsel schreibt dazwischen
            assert held is original

    def test_switch_waits_for_a_running_transcription(self, app, monkeypatch):
        monkeypatch.setattr(App, "_build_model", fake_build())
        order = []
        app._transcribe_lock.acquire()

        def worker():
            app._switch_worker("tiny", 1)
            order.append("switch")

        app._model_generation = 1
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        time.sleep(0.2)
        assert order == [], "Wechsel lief trotz laufender Transkription"
        order.append("transcription")
        app._transcribe_lock.release()
        thread.join(timeout=5)
        assert order == ["transcription", "switch"]

    def test_stale_load_does_not_win(self, app, monkeypatch):
        """Ein überholter Ladevorgang darf den neueren Zustand nicht kippen."""
        monkeypatch.setattr(App, "_build_model", fake_build())
        app._model_generation = 5
        app._switch_worker("tiny", 2)          # veraltete Generation
        assert app.active_model_size == "turbo"
        assert app.persisted == []

    def test_generation_increases_per_request(self, app, monkeypatch):
        before = app._model_generation
        run_switch(app, "tiny", monkeypatch)
        assert app._model_generation == before + 1


class TestSettingsLifecycle:
    def test_missing_settings_window_is_fine(self, app, monkeypatch):
        app.settings_win = None
        run_switch(app, "tiny", monkeypatch)
        assert app.active_model_size == "tiny"

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

        app.settings_win = _Live()
        run_switch(app, "tiny", monkeypatch)
        assert touched == [1]
