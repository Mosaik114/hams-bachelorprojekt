"""Giving the graphics memory back while Hams is idle, and getting the model back in time.

CTranslate2 is replaced by `FakeEngine`, which behaves like the real one where it matters: `unload_model` gives the
weights back (or silently keeps them while busy), `load_model` brings them back or fails, and a transcription with
the weights gone raises the same `RuntimeError` CTranslate2 raises ("No model replica is available"). The real
thing on the GPU is measured by hand (see the report of 1.3.7); these tests pin the order of events.
"""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from live_app import FakeOllama, RecordingSink, make_pipeline, ready_models

from hams import idle_release
from hams import transcription as transcription_mod
from hams import whisper_model as whisper_model_mod
from hams.config import CFG
from hams.i18n import t
from hams.idle_release import GAME_SECONDS, IdleRelease
from hams.whisper_model import ModelManager

SPEECH = (np.sin(np.linspace(0, 400, 16_000)) * 0.3).astype(np.float32)


@pytest.fixture(autouse=True)
def raw_text(monkeypatch):
    """The dictated text goes out as it is: no Ollama cleanup in these tests."""
    monkeypatch.setattr(CFG, "ollama_cleanup_enabled", False)


def footer(app) -> str:
    return app.footer_label.cget("text")


class FakeEngine:
    """`ctranslate2.models.Whisper`, as far as parking goes."""

    def __init__(self) -> None:
        self.model_is_loaded = True
        self.compute_type = "float16"
        self.unloads = 0
        self.loads = 0
        #: An exception `load_model` raises (a driver reset, a full graphics card).
        self.load_error: BaseException | None = None
        #: CTranslate2 keeps the weights silently while a batch runs.
        self.keep = False
        #: Set to hold `unload_model` / `load_model` until the test releases it.
        self.unload_gate: threading.Event | None = None
        self.unload_entered = threading.Event()
        self.load_gate: threading.Event | None = None
        self.load_entered = threading.Event()

    def unload_model(self, to_cpu: bool = False) -> None:
        self.unload_entered.set()
        if self.unload_gate is not None:
            assert self.unload_gate.wait(5)
        self.unloads += 1
        if not self.keep:
            self.model_is_loaded = False

    def load_model(self, keep_cache: bool = False) -> None:
        self.load_entered.set()
        if self.load_gate is not None:
            assert self.load_gate.wait(5)
        if self.load_error is not None:
            raise self.load_error
        self.loads += 1
        self.model_is_loaded = True


class FakeWhisper:
    """`faster_whisper.WhisperModel` around a `FakeEngine`."""

    def __init__(self, text: str = "Hallo Welt") -> None:
        self.model = FakeEngine()
        self.text = text
        self.calls = 0

    def transcribe(self, audio, **options):
        if not self.model.model_is_loaded:
            raise RuntimeError("No model replica is available in this thread")
        self.calls += 1
        return iter([SimpleNamespace(text=self.text)]), SimpleNamespace(language="de")


def loaded_manager(model=None, device="cuda") -> ModelManager:
    models = ready_models(model or FakeWhisper())
    models.active_device = device
    models.active_compute_type = "float16"
    models._phase = "ready"
    return models


def wait_for(condition, seconds=5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def wake_finished(models) -> bool:
    return wait_for(lambda: not models._waking)


# ---------------------------------------------------------------------- the model manager


class TestParking:
    def test_the_weights_go_and_the_model_stays_usable(self):
        models = loaded_manager()
        assert models.park() is True
        assert models.parked and models.phase == "parked"
        assert models.model.model.model_is_loaded is False
        # Everything that reads `model_ready` means "usable": tray, settings, assistant, pipeline.
        assert models.model_ready.is_set()
        assert not models.failed

    def test_not_during_a_transcription(self):
        """`transcribe_lock` held: no waiting, no parking; the next check tries again."""
        models = loaded_manager()
        with models.transcribe_lock:
            assert models.park() is False
        assert models.model.model.unloads == 0
        assert not models.parked and models.phase == "ready"

    def test_not_while_ctranslate2_keeps_the_weights(self):
        """CTranslate2 returns silently when it is busy; `model_is_loaded` tells."""
        models = loaded_manager()
        models.model.model.keep = True
        assert models.park() is False
        assert not models.parked and models.phase == "ready"

    def test_not_during_a_model_switch(self):
        models = loaded_manager()
        models._switching_to = "small"
        assert models.can_park() is False
        assert models.park() is False
        assert models.model.model.unloads == 0

    def test_not_during_a_download(self, monkeypatch):
        models = loaded_manager()
        monkeypatch.setattr(type(models.downloader), "active", property(lambda self: True))
        assert models.park() is False

    def test_not_twice(self):
        models = loaded_manager()
        assert models.park() is True
        assert models.park() is False
        assert models.model.model.unloads == 1

    def test_a_double_without_ctranslate2_is_left_alone(self):
        models = ready_models(object())
        models._phase = "ready"
        assert models.park() is False

    def test_not_after_close(self):
        models = loaded_manager()
        models.close()
        assert models.park() is False


class TestTheHotkeyBringsItBack:
    def test_a_parked_model_loads_again_in_the_background(self):
        models = loaded_manager()
        models.park()
        models.wake()
        assert wake_finished(models)
        assert models.model.model.model_is_loaded and models.model.model.loads == 1
        assert not models.parked and models.phase == "ready"

    def test_a_loaded_model_is_not_touched(self):
        models = loaded_manager()
        models.wake()
        assert wake_finished(models)
        assert models.model.model.loads == 0

    def test_the_hotkey_exactly_while_parking(self):
        """The hotkey comes while `unload_model` runs: the weights come straight back, not at the dictation."""
        models = loaded_manager()
        engine = models.model.model
        engine.unload_gate = threading.Event()
        parker = threading.Thread(target=models.park)
        parker.start()
        assert engine.unload_entered.wait(5)
        models.wake()
        engine.unload_gate.set()
        parker.join(5)
        assert wake_finished(models)
        assert engine.model_is_loaded
        assert not models.parked and models.phase == "ready"

    def test_a_hotkey_after_the_decision_stops_the_park(self):
        """Decided on the Tk thread, parked on a worker: a hotkey in between wins."""
        models = loaded_manager()
        decided = models.wake_count()
        models.wake()  # the user presses the hotkey before the worker runs
        assert models.park(decided) is False
        assert models.model.model.unloads == 0

    def test_a_switch_during_the_reload_wins(self, monkeypatch):
        """The reload was for the old generation; the newer request brings its own model."""
        models = loaded_manager()
        models.park()
        old_engine = models.model.model
        new_model = FakeWhisper()
        monkeypatch.setattr(ModelManager, "_build_model", lambda self, model_id: (new_model, "cuda", "float16"))
        monkeypatch.setattr(ModelManager, "_start_switch_thread", lambda self, *args: self._switch_worker(*args))
        with models.transcribe_lock:
            models.wake()  # waits for the lock
            switch = threading.Thread(target=models.request, args=("small",), kwargs={"persist": False})
            switch.start()
            assert wait_for(lambda: models._generation == 1)
        switch.join(5)
        assert wake_finished(models)
        assert old_engine.loads == 0
        assert models.current_model() is new_model and models.active_model_size == "small"
        assert not models.parked and models.phase == "ready"


class TestTheTranscriptionChecksForItself:
    def test_a_parked_model_in_the_job_is_loaded_and_the_text_comes(self):
        """Without the check the dictation raised "No model replica" and was lost."""
        pipeline = make_pipeline(FakeWhisper())
        models = pipeline.models
        models._phase = "ready"
        models.park()
        pipeline.transcribe(SPEECH)
        assert pipeline.pasted == ["Hallo Welt"]
        assert models.model.model.loads == 1 and not models.parked
        # Parked counts as usable: no "model still loading" for a dictation that only waits a moment.
        assert t("status.model_still_loading") not in [detail for _state, detail, _hint in pipeline.states]

    def test_the_job_waits_for_a_reload_that_is_under_way(self):
        pipeline = make_pipeline(FakeWhisper())
        models = pipeline.models
        models._phase = "ready"
        models.park()
        engine = models.model.model
        engine.load_gate = threading.Event()
        models.wake()
        assert engine.load_entered.wait(5)
        job = threading.Thread(target=pipeline.transcribe, args=(SPEECH,))
        job.start()
        time.sleep(0.05)
        assert pipeline.pasted == []  # waits on the lock, does not raise
        engine.load_gate.set()
        job.join(5)
        assert pipeline.pasted == ["Hallo Welt"] and engine.loads == 1


class TestWhenTheReloadFails:
    def test_the_model_is_built_anew_down_to_the_cpu(self, monkeypatch):
        """A driver reset kills the CUDA context: the reload raises, the device list ends on the CPU."""
        pipeline = make_pipeline(FakeWhisper())
        models = pipeline.models
        models.active_device, models._phase = "cuda", "ready"
        models.park()
        models.model.model.load_error = RuntimeError("CUDA failed with error unspecified launch failure")
        on_cpu = FakeWhisper("Vom Prozessor")
        built = []

        def build(self, model_id):
            built.append(model_id)
            return on_cpu, "cpu", "int8"

        monkeypatch.setattr(ModelManager, "_build_model", build)
        pipeline.transcribe(SPEECH)
        assert built == ["turbo"]
        assert pipeline.pasted == ["Vom Prozessor"]  # the dictation is not lost
        assert models.active_device == "cpu" and models.reload_trouble == "device"
        assert models.events.loaded[-1] == ("turbo", True, "turbo")

    def test_nothing_loads_any_more(self, monkeypatch):
        pipeline = make_pipeline(FakeWhisper())
        models = pipeline.models
        models._phase = "ready"
        models.park()
        models.model.model.load_error = RuntimeError("out of memory")
        monkeypatch.setattr(ModelManager, "_build_model", lambda self, model_id: (None, None, "cuda: oom | cpu: oom"))
        pipeline.transcribe(SPEECH)
        assert pipeline.pasted == []
        assert pipeline.states[-1][0] == "error"
        assert models.failed and models.reload_trouble == "failed"
        assert models.events.loaded[-1] == ("turbo", False, "turbo")

    def test_the_app_offers_a_restart(self, live_app, monkeypatch):
        restarts = []
        monkeypatch.setattr(live_app, "restart_app", lambda: restarts.append(1) or "")
        live_app.models.model_error = "cuda: oom | cpu: oom"
        live_app.models.reload_trouble = "failed"
        live_app._on_model_error()
        assert live_app._state == "restart_needed"
        assert live_app.main_button.cget("text") == "Neu starten"
        assert "Neustart von Hams" in footer(live_app)
        live_app._on_main_action()
        assert restarts == [1]

    def test_a_restart_that_cannot_happen_now_says_why(self, live_app, monkeypatch):
        monkeypatch.setattr(live_app, "restart_app", lambda: "Ein Diktat wird noch verarbeitet")
        live_app.apply_state("restart_needed")
        live_app._on_main_action()
        assert live_app._state == "restart_needed"
        assert footer(live_app) == "Ein Diktat wird noch verarbeitet"

    def test_the_app_says_calmly_that_the_cpu_works_now(self, live_app):
        models = live_app.models
        models.model, models.active_model_size, models.active_device = FakeWhisper(), "turbo", "cpu"
        models.model_ready.set()
        models.reload_trouble = "device"
        live_app.model_loaded("turbo", True, "turbo")
        assert live_app._state == "ready"
        assert "Prozessor" in footer(live_app)

    def test_a_new_request_clears_the_trouble(self, monkeypatch):
        models = loaded_manager()
        models.reload_trouble = "device"
        monkeypatch.setattr(ModelManager, "_start_switch_thread", lambda self, *args: None)
        models.request("turbo", persist=False)
        assert models.reload_trouble is None


class TestClose:
    def test_close_waits_for_a_load_that_runs(self, monkeypatch):
        models = loaded_manager()
        gate, entered = threading.Event(), threading.Event()
        built = FakeWhisper()

        def build(self, model_id):
            entered.set()
            assert gate.wait(5)
            return built, "cuda", "float16"

        monkeypatch.setattr(ModelManager, "_build_model", build)
        models.request("small", persist=False)
        assert entered.wait(5)
        threading.Timer(0.2, gate.set).start()
        started = time.monotonic()
        models.close()
        assert time.monotonic() - started >= 0.15
        assert models.model is None and not models.model_ready.is_set()
        assert models.events.loaded == []  # the late result is discarded, nothing reported after close

    def test_close_does_not_wait_forever(self, monkeypatch):
        models = loaded_manager()
        monkeypatch.setattr(ModelManager, "CLOSE_WAIT_SECONDS", 0.1)
        with_lock = threading.Event()
        release = threading.Event()

        def hold():
            with models.transcribe_lock:
                with_lock.set()
                release.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        assert with_lock.wait(5)
        started = time.monotonic()
        models.close()
        assert time.monotonic() - started < 2
        assert models.model is None
        release.set()
        holder.join(5)

    def test_close_while_a_parked_model_loads_again(self):
        models = loaded_manager()
        models.park()
        engine = models.model.model
        engine.load_gate = threading.Event()
        models.wake()
        assert engine.load_entered.wait(5)
        threading.Timer(0.1, engine.load_gate.set).start()
        models.close()
        assert models.model is None and not models.parked
        assert wake_finished(models)

    def test_a_reload_asked_for_after_close_does_nothing(self):
        models = loaded_manager()
        models.park()
        engine = models.model.model
        models.close()
        models.wake()
        assert wake_finished(models)
        assert engine.loads == 0


# ---------------------------------------------------------------------- the decision (IdleRelease)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Ollama(FakeOllama):
    def __init__(self, released=("qwen3.5:9b",)) -> None:
        super().__init__()
        self.released = list(released)
        self.release_calls = 0

    def release_own_models(self, timeout=None):
        self.release_calls += 1
        return list(self.released)


@pytest.fixture
def idle(monkeypatch):
    """An `IdleRelease` on a stand-in app with a parkable model, a fake clock and a full-screen switch."""
    monkeypatch.setattr(CFG, "model_release_minutes", 10)
    monkeypatch.setattr(CFG, "model_release_for_games", True)
    models = loaded_manager()
    pipeline = make_pipeline(FakeWhisper())
    pipeline.models = models
    app = SimpleNamespace(
        models=models,
        pipeline=pipeline,
        ollama=Ollama(),
        is_recording=False,
        _shutting_down=False,
        root=None,
    )
    clock = FakeClock()
    screen = SimpleNamespace(full=False)
    release = IdleRelease(app, full_screen=lambda: screen.full, clock=clock, spawn=lambda target, *args: target(*args))
    return SimpleNamespace(release=release, app=app, models=models, clock=clock, screen=screen)


class TestTheTimer:
    def test_parks_after_ten_minutes_without_dictation(self, idle):
        idle.clock.now += 599
        idle.release.check()
        assert not idle.models.parked
        idle.clock.now += 1
        idle.release.check()
        assert idle.models.parked
        assert idle.app.ollama.release_calls == 0  # Ollama keeps its own `keep_alive`

    def test_a_recording_starts_the_time_again(self, idle):
        idle.clock.now += 500
        idle.app.is_recording = True
        idle.release.check()
        idle.app.is_recording = False
        idle.clock.now += 500
        idle.release.check()
        assert not idle.models.parked
        idle.clock.now += 100
        idle.release.check()
        assert idle.models.parked

    @pytest.mark.parametrize(
        "busy",
        [
            pytest.param(lambda app: setattr(app, "is_recording", True), id="recording"),
            pytest.param(lambda app: setattr(app.pipeline, "job_running", True), id="transcription"),
            pytest.param(lambda app: app.pipeline._jobs.append(("audio", False, 1)), id="queue"),
            pytest.param(lambda app: setattr(app.models, "_switching_to", "small"), id="model switch"),
        ],
    )
    def test_nothing_is_given_back_while_busy(self, idle, busy):
        busy(idle.app)
        idle.clock.now += 3600
        idle.release.check()
        assert idle.models.model.model.unloads == 0

    def test_touch_restarts_the_idle_time(self, idle):
        idle.clock.now += 590
        idle.release.touch()
        idle.clock.now += 590
        idle.release.check()
        assert not idle.models.parked

    @pytest.mark.parametrize(("minutes", "parked_after"), [(2, 120), (5, 300), (30, 1800), (60, 3600)])
    def test_the_chosen_minutes(self, idle, monkeypatch, minutes, parked_after):
        monkeypatch.setattr(CFG, "model_release_minutes", minutes)
        idle.clock.now += parked_after - 1
        idle.release.check()
        assert not idle.models.parked
        idle.clock.now += 1
        idle.release.check()
        assert idle.models.parked

    def test_never(self, idle, monkeypatch):
        monkeypatch.setattr(CFG, "model_release_minutes", 0)
        idle.clock.now += 10 * 24 * 3600
        idle.release.check()
        assert not idle.models.parked

    def test_a_model_in_use_is_tried_again_at_the_next_check(self, idle):
        idle.clock.now += 600
        with idle.models.transcribe_lock:
            idle.release.check()
        assert not idle.models.parked
        idle.release.check()
        assert idle.models.parked

    def test_the_timer_runs_on_tk(self, root, idle):
        idle.app.root = root
        idle.release.start()
        assert idle.release._job is not None
        idle.release.stop()
        assert idle.release._job is None


class TestGames:
    def test_a_full_screen_game_gets_the_memory_after_thirty_seconds(self, idle):
        idle.screen.full = True
        idle.clock.now += GAME_SECONDS - 1
        idle.release.check()
        assert not idle.models.parked
        idle.clock.now += 1
        idle.release.check()
        assert idle.models.parked
        assert idle.app.ollama.release_calls == 1

    def test_ollama_is_asked_once_per_game(self, idle):
        idle.screen.full = True
        idle.clock.now += 60
        idle.release.check()
        idle.release.check()
        assert idle.app.ollama.release_calls == 1
        idle.screen.full = False
        idle.release.check()
        idle.screen.full = True
        idle.release.check()
        assert idle.app.ollama.release_calls == 2

    def test_also_with_never(self, idle, monkeypatch):
        monkeypatch.setattr(CFG, "model_release_minutes", 0)
        idle.screen.full = True
        idle.clock.now += GAME_SECONDS
        idle.release.check()
        assert idle.models.parked

    def test_switched_off(self, idle, monkeypatch):
        monkeypatch.setattr(CFG, "model_release_for_games", False)
        idle.screen.full = True
        idle.clock.now += 300
        idle.release.check()
        assert not idle.models.parked and idle.app.ollama.release_calls == 0

    def test_the_warm_up_starts_anew_after_ollama_was_unloaded(self, idle, monkeypatch):
        monkeypatch.setattr(CFG, "ollama_cleanup_enabled", True)
        pipeline = idle.app.pipeline
        pipeline._warm_until["qwen3.5:9b"] = time.monotonic() + 999
        idle.screen.full = True
        idle.clock.now += GAME_SECONDS
        idle.release.check()
        assert pipeline._warm_until == {}

    def test_the_full_screen_detector_answers_on_every_system(self):
        assert idle_release.full_screen_detector()() in (True, False)


class TestFreeNow:
    def test_frees_the_model_and_ollama(self, idle):
        assert idle.release.release_now() is True
        assert idle.models.parked and idle.app.ollama.release_calls == 1

    def test_not_during_a_dictation(self, idle):
        idle.app.is_recording = True
        assert idle.release.release_now() is False
        assert not idle.models.parked and idle.app.ollama.release_calls == 0

    def test_the_tray_entry(self, live_app, monkeypatch):
        calls = []
        monkeypatch.setattr(live_app.idle_release, "release_now", lambda: calls.append(1))
        menu = live_app._build_tray_menu()
        items = [item for item in menu.items if getattr(item, "text", "") == "Speicher jetzt freigeben"]
        assert len(items) == 1
        items[0](None)
        live_app.root.update()
        assert calls == [1]


class TestTheAppAroundIt:
    def test_the_hotkey_wakes_the_model_and_ollama(self, live_app, monkeypatch):
        woken, warmed = [], []
        monkeypatch.setattr(live_app.models, "wake", lambda: woken.append(1))
        monkeypatch.setattr(
            live_app.pipeline, "warm_up_ollama_async", lambda prompt_mode=False: warmed.append(prompt_mode)
        )
        live_app.toggle_recording_generate()
        assert woken == [1] and warmed == [True]

    def test_ollama_is_not_warmed_up_at_start(self, root, hams, monkeypatch):
        from hams import transcription
        from hams.app import FloatingTranscriberApp

        warmed = []
        monkeypatch.setattr(transcription.Pipeline, "warm_up_ollama_async", lambda self, *a: warmed.append(a))
        import tkinter as tk

        window = tk.Toplevel(root)
        app = FloatingTranscriberApp(window)
        try:
            assert warmed == []
        finally:
            app._shutting_down = True
            app._release_resources()
            window.destroy()

    def test_quit_stops_the_timer(self, live_app, monkeypatch):
        stopped = []
        monkeypatch.setattr(live_app.idle_release, "stop", lambda: stopped.append(1))
        live_app._release_resources()
        assert stopped == [1]


class TestOllamaWarmUpAtTheHotkey:
    @pytest.fixture
    def pipeline(self, monkeypatch):
        monkeypatch.setattr(CFG, "ollama_cleanup_enabled", True)
        pipeline = make_pipeline(FakeWhisper(), ollama=FakeOllama(), sink=RecordingSink())
        started = []
        monkeypatch.setattr(pipeline, "warm_up_ollama", lambda prompt_mode=False: started.append(prompt_mode))
        inline = SimpleNamespace(Thread=lambda target, args=(), **kwargs: SimpleNamespace(start=lambda: target(*args)))
        monkeypatch.setattr(transcription_mod, "threading", inline)
        pipeline.started = started
        return pipeline

    def test_not_again_right_away(self, pipeline):
        pipeline.warm_up_ollama_async(False)
        pipeline.warm_up_ollama_async(False)
        assert pipeline.started == [False]

    def test_the_prompt_model_is_its_own(self, pipeline, monkeypatch):
        monkeypatch.setattr(CFG, "ollama_model_prompt", "gross:27b")
        pipeline.warm_up_ollama_async(False)
        pipeline.warm_up_ollama_async(True)
        assert pipeline.started == [False, True]

    def test_nothing_without_cleanup(self, pipeline, monkeypatch):
        monkeypatch.setattr(CFG, "ollama_cleanup_enabled", False)
        pipeline.warm_up_ollama_async(False)
        assert pipeline.started == []

    def test_not_while_ollama_was_just_missing(self, pipeline):
        pipeline._cleanup_retry_at = time.monotonic() + 60
        pipeline.warm_up_ollama_async(False)
        assert pipeline.started == []

    def test_the_prompt_model_is_warmed(self, monkeypatch):
        ollama = FakeOllama()
        pipeline = make_pipeline(FakeWhisper(), ollama=ollama)
        monkeypatch.setattr(CFG, "ollama_model_prompt", "gross:27b")
        pipeline.warm_up_ollama(True)
        assert ollama.warmed == [("gross:27b", CFG.ollama_keep_alive)]


# ---------------------------------------------------------------------- loading: warm-up and the real compute type


class TestTheLoad:
    def test_the_gpu_model_is_warmed_up_once(self):
        model = FakeWhisper()
        whisper_model_mod.warm_up(model, "cuda")
        assert model.calls == 1

    @pytest.mark.parametrize("device", ["cpu", None])
    def test_not_on_the_cpu(self, device):
        model = FakeWhisper()
        whisper_model_mod.warm_up(model, device)
        assert model.calls == 0

    def test_a_failed_warm_up_is_only_logged(self, caplog):
        model = FakeWhisper()
        model.model.model_is_loaded = False  # transcribe raises
        whisper_model_mod.warm_up(model, "cuda")
        assert "Model warm-up failed" in caplog.text

    def test_a_load_warms_up_before_the_model_counts_as_ready(self, monkeypatch):
        models = ready_models()
        model = FakeWhisper()
        seen = []
        original = model.transcribe

        def transcribe(audio, **options):
            seen.append(models.model_ready.is_set())
            return original(audio, **options)

        model.transcribe = transcribe
        monkeypatch.setattr(ModelManager, "_build_model", lambda self, model_id: (model, "cuda", "float16"))
        monkeypatch.setattr(ModelManager, "_start_switch_thread", lambda self, *args: self._switch_worker(*args))
        models.request("turbo", persist=False)
        assert seen == [False] and models.model_ready.is_set()

    def test_the_compute_type_ctranslate2_really_uses_is_kept(self, monkeypatch, caplog):
        """Asked for int8, CTranslate2 may take int8_float16 without a word; Hams used to log the request."""

        class Model(FakeWhisper):
            def __init__(self, model_id, device, compute_type, **options):
                super().__init__()
                self.model.compute_type = "int8_float16"

        monkeypatch.setattr(whisper_model_mod, "WhisperModel", Model)
        monkeypatch.setattr(whisper_model_mod, "usable_devices", lambda priority: ("cuda",))
        monkeypatch.setattr(whisper_model_mod, "COMPUTE_PRIORITY", {"cuda": ("int8",)})
        caplog.set_level("INFO", logger="hams")
        model, device, compute_type = whisper_model_mod._load_on_devices("turbo", local=True)
        assert (device, compute_type) == ("cuda", "int8_float16")
        assert "Model ready (cuda/int8_float16, asked for int8)" in caplog.text

    def test_parking_and_loading_log_the_graphics_memory(self, monkeypatch, caplog):
        monkeypatch.setattr(whisper_model_mod.gpu_memory, "describe", lambda: "1234 MiB")
        caplog.set_level("INFO", logger="hams")
        models = loaded_manager()
        models.park()
        models.wake()
        assert wake_finished(models)
        assert "Graphics memory of Hams with the model parked: 1234 MiB" in caplog.text
        assert "Graphics memory of Hams with the model loaded again: 1234 MiB" in caplog.text


# ---------------------------------------------------------------------- settings and configuration


class TestConfiguration:
    def test_the_defaults(self):
        from hams.config import Config

        cfg = Config()
        assert (cfg.model_release_minutes, cfg.model_release_for_games, cfg.ollama_keep_alive) == (10, True, "10m")

    def test_the_template_says_the_same(self):
        import tomllib

        from hams import config

        template = tomllib.loads(config.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        assert template["model"]["release_after"] == 10
        assert template["model"]["release_for_games"] is True
        assert template["ollama"]["keep_alive"] == "10m"

    @pytest.mark.parametrize("minutes", [0, 2, 5, 10, 30, 60])
    def test_the_offered_minutes_are_read(self, minutes):
        from hams.config import Config, _apply_toml

        cfg = Config()
        _apply_toml(cfg, {"model": {"release_after": minutes, "release_for_games": False}})
        assert (cfg.model_release_minutes, cfg.model_release_for_games) == (minutes, False)

    def test_other_minutes_are_reported_and_ignored(self, caplog):
        from hams.config import Config, _apply_toml

        cfg = Config()
        _apply_toml(cfg, {"model": {"release_after": 7}})
        assert cfg.model_release_minutes == 10
        assert "model.release_after should be one of 0, 2, 5, 10, 30, 60" in caplog.text

    def _load_user_file(self, monkeypatch, tmp_path, text):
        from hams import config

        user = tmp_path / "config.toml"
        user.write_text(text, encoding="utf-8")
        monkeypatch.setattr(config, "CONFIG_PATH", user)
        return config._load_config()

    def test_the_old_keep_alive_of_the_template_becomes_the_new_one(self, monkeypatch, tmp_path):
        """Every user file started as a copy of the template, which said 30m up to 1.3.6; nobody chose it."""
        cfg = self._load_user_file(monkeypatch, tmp_path, '[ollama]\nkeep_alive = "30m"\n')
        assert cfg.ollama_keep_alive == "10m"
        assert 'keep_alive = "30m"' in (tmp_path / "config.toml").read_text(encoding="utf-8")  # never rewritten

    @pytest.mark.parametrize("value", ['"1h"', '"5m"', "-1", '"0"'])
    def test_a_keep_alive_of_ones_own_stays(self, monkeypatch, tmp_path, value):
        cfg = self._load_user_file(monkeypatch, tmp_path, f"[ollama]\nkeep_alive = {value}\n")
        assert cfg.ollama_keep_alive == value.strip('"')


class TestTheSettings:
    @pytest.fixture
    def section(self, live_app):
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win.transcription

    def test_the_rows_show_the_stored_values(self, section, monkeypatch):
        assert [option.value for option in section.select_release.options] == [0, 2, 5, 10, 30, 60]
        assert [option.label for option in section.select_release.options] == [
            "Nie",
            "2 Minuten",
            "5 Minuten",
            "10 Minuten",
            "30 Minuten",
            "1 Stunde",
        ]
        assert section.select_release.value == CFG.model_release_minutes
        assert section.switch_games.value is CFG.model_release_for_games

    def test_a_choice_is_saved_and_applies_at_once(self, section, hams, monkeypatch):
        monkeypatch.setattr(CFG, "model_release_minutes", 10)
        section.select_release.set_value(30, notify=True)
        assert CFG.model_release_minutes == 30
        assert ("model", "release_after", 30) in hams.saved

    def test_the_game_switch_is_saved(self, section, hams, monkeypatch):
        monkeypatch.setattr(CFG, "model_release_for_games", True)
        section.switch_games.toggle()
        assert CFG.model_release_for_games is False
        assert ("model", "release_for_games", False) in hams.saved

    def test_reopening_shows_a_value_changed_meanwhile(self, section, live_app, monkeypatch):
        monkeypatch.setattr(CFG, "model_release_minutes", 60)
        monkeypatch.setattr(CFG, "model_release_for_games", False)
        live_app.settings_win.close()
        live_app._open_settings()
        live_app.root.update()
        assert section.select_release.value == 60 and section.switch_games.value is False
