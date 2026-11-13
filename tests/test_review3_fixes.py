"""Regressions from the third independent review.

Each class pins one confirmed finding: a hidden window on a manual start, TOML files in the wrong encoding, a stop
hotkey refused during a recording, a late download overriding a newer model choice, dictations dropped on quit,
Tk called from a worker thread, the microphone lifecycle, and empty or broken AI answers.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from live_app import FakeStream

from hams import cli, config, glossary, main, setup_autostart
from hams import main_window as main_window_mod
from hams import transcription as transcription_mod
from hams.app import FloatingTranscriberApp as App
from hams.config import CFG
from hams.config import _persist_config_value as REAL_PERSIST  # before any fixture replaces it

AUDIO = np.full(1600, 0.2, dtype=np.float32)
ISS = (Path(__file__).resolve().parent.parent / "packaging" / "hams.iss").read_text(encoding="utf-8-sig")


# ---------------------------------------------------------------- 1. window on a manual start


class _Root:
    def __init__(self):
        self.idle = []

    def after_idle(self, fn):
        self.idle.append(fn)


class TestTheWindowOnStart:
    @pytest.fixture
    def app(self):
        return SimpleNamespace(_show_window="show", _open_setup="setup", _open_settings="settings")

    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            ({}, ["show"]),
            ({"background": True}, []),
            ({"setup": True}, ["setup"]),
            ({"setup": True, "background": True}, ["setup"]),
            ({"settings": True}, ["show", "settings"]),
            ({"settings": True, "background": True}, ["settings"]),
        ],
    )
    def test_only_the_autostart_starts_hidden(self, app, flags, expected):
        root = _Root()
        options = {"setup": False, "settings": False, "background": False, **flags}
        main._first_windows(root, app, **options)
        assert root.idle == expected

    def test_the_command_line_passes_background_on(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(main, "main", lambda **kwargs: seen.update(kwargs))
        assert cli.run(["--background"]) == 0
        assert seen == {"show_settings": False, "show_setup": False, "background": True}
        cli.run([])
        assert seen["background"] is False

    def test_the_installer_autostart_entry_and_only_it_starts_in_the_background(self):
        run_value = next(line for line in ISS.splitlines() if "CurrentVersion\\Run" in line and "ValueData" in line)
        assert "--background" in run_value
        icons_and_launch = [line for line in ISS.splitlines() if line.startswith(("Name:", "Filename:"))]
        assert icons_and_launch and not any("--background" in line for line in icons_and_launch)

    def test_a_source_autostart_entry_starts_in_the_background(self, monkeypatch):
        monkeypatch.setattr(setup_autostart.sys, "frozen", True, raising=False)
        assert setup_autostart._resolve_launch_command().endswith(" --background")

    def test_the_start_script_passes_its_arguments_on(self):
        script = (setup_autostart.PROJECT_ROOT / "scripts" / "start_hams.vbs").read_text(encoding="utf-8")
        assert "WScript.Arguments" in script and "& args" in script

    @pytest.mark.skipif(sys.platform == "win32", reason="hams.linux needs Linux")
    def test_the_linux_service_is_the_autostart_and_starts_in_the_background(self):
        from hams import linux

        assert '"/main.py" --background' in linux.service_unit("/py", "/main.py")


class _NotifyingTray:
    HAS_NOTIFICATION = True

    def __init__(self):
        self.notes = []
        self.icon = None
        self.title = ""

    def notify(self, message, title):
        self.notes.append((title, message))

    def stop(self):
        pass


class TestTheFirstHideExplainsTheTray:
    def test_once_per_installation(self, live_app, hams):
        live_app.tray_icon = _NotifyingTray()
        CFG.tray_hint_shown = False
        live_app._hide_window()
        live_app._show_window()
        live_app._hide_window()
        assert live_app.tray_icon.notes == [
            (
                "Hams läuft im Tray weiter",
                "Die Kurzbefehle funktionieren weiter. Ein Klick auf das Tray-Symbol zeigt das Fenster wieder; "
                "es kann hinter dem Pfeil ^ liegen.",
            )
        ]
        assert ("ui", "tray_hint_shown", True) in hams.saved
        assert CFG.tray_hint_shown is True

    def test_not_again_after_a_restart(self, live_app):
        live_app.tray_icon = _NotifyingTray()
        CFG.tray_hint_shown = True
        live_app._hide_window()
        assert live_app.tray_icon.notes == []

    def test_a_tray_without_notifications_asks_again_next_time(self, live_app, hams):
        CFG.tray_hint_shown = False
        live_app._hide_window()  # FakeTray: no notifications
        assert CFG.tray_hint_shown is False
        assert not [entry for entry in hams.saved if entry[1] == "tray_hint_shown"]

    def test_a_failing_notification_does_not_block_hiding(self, live_app):
        tray = _NotifyingTray()

        def broken(message, title):
            raise RuntimeError("shell weg")

        tray.notify = broken
        live_app.tray_icon = tray
        CFG.tray_hint_shown = False
        live_app._hide_window()
        assert live_app._window_visible is False
        assert CFG.tray_hint_shown is False

    def test_the_setting_is_read_from_the_file(self):
        cfg = config.Config()
        config._apply_toml(cfg, {"ui": {"tray_hint_shown": True}})
        assert cfg.tray_hint_shown is True


# ---------------------------------------------------------------- 2. encodings


@pytest.fixture
def pipeline_app(monkeypatch, tmp_path):
    """The app without window: UI calls run at once and are recorded, pasting is recorded too."""
    instance = App.__new__(App)
    instance.model_ready = threading.Event()
    instance.model_ready.set()
    instance._transcribe_lock = threading.Lock()
    instance._model_lock = threading.Lock()
    instance.model = object()
    instance.is_recording = False
    instance._prompt_mode = False
    instance.states = []
    instance.pasted = []
    instance._ui_call = lambda fn: fn()
    instance._apply_state = lambda state, detail=None, hint=None, **_: instance.states.append((state, detail, hint))
    instance._add_to_history = lambda text: None
    instance._copy_and_paste = instance.pasted.append
    instance._save_transcript_file = lambda text: None
    instance.elapsed_label = SimpleNamespace(config=lambda **_: None)
    monkeypatch.setattr(CFG, "ollama_cleanup_enabled", False)
    return instance


GLOSSARY = 'words = ["Müller"]\n[replace]\n"mueller" = "Müller"\n'


class TestGlossaryEncodings:
    @pytest.fixture
    def path(self, tmp_path, monkeypatch):
        target = tmp_path / "glossary.toml"
        monkeypatch.setattr(glossary, "glossary_path", lambda: target)
        monkeypatch.setitem(glossary._cache, "path", None)
        return target

    @pytest.mark.parametrize("encoding", ["cp1252", "utf-16"])
    def test_a_file_in_another_encoding_never_breaks_a_dictation(self, pipeline_app, path, encoding, caplog):
        path.write_bytes(GLOSSARY.encode(encoding))
        pipeline_app._run_transcription = lambda model, audio: ("frau mueller", "de")
        with caplog.at_level(logging.WARNING, logger="hams"):
            pipeline_app._transcribe_audio(AUDIO)
            pipeline_app._transcribe_audio(AUDIO)
        assert pipeline_app.pasted == ["frau mueller", "frau mueller"], "an empty glossary, the text still comes"
        warnings = [r for r in caplog.records if "Glossar" in r.getMessage()]
        assert len(warnings) == 1, "cached for this file version: one warning, not one per dictation"
        assert glossary.hotwords("de") is None

    def test_a_utf8_file_with_byte_order_mark_counts(self, pipeline_app, path):
        path.write_bytes(GLOSSARY.encode("utf-8-sig"))
        pipeline_app._run_transcription = lambda model, audio: ("frau mueller", "de")
        pipeline_app._transcribe_audio(AUDIO)
        assert pipeline_app.pasted == ["frau Müller"]
        assert "Müller" in glossary.hotwords("de")

    def test_glossary_does_not_import_tomllib_itself(self):
        """Python 3.10 has no tomllib; the shared reader in config_toml has the fallback."""
        source = Path(glossary.__file__).read_text(encoding="utf-8")
        assert "import tomllib" not in source


class TestConfigEncodings:
    @pytest.fixture
    def path(self, tmp_path, monkeypatch):
        target = tmp_path / "config.toml"
        monkeypatch.setattr(config, "CONFIG_PATH", target)
        monkeypatch.setattr(config, "BROKEN_CONFIG", None)
        return target

    def test_a_cp1252_file_is_not_written_and_does_not_raise(self, path):
        original = '[audio]\ndevice = "Mikrofon (Realtek\xae Audio)"\n'.encode("cp1252")
        path.write_bytes(original)
        assert REAL_PERSIST("ui", "window_x", 5) is False
        assert path.read_bytes() == original

    def test_a_cp1252_file_is_reported_as_unreadable(self, path):
        path.write_bytes('[audio]\ndevice = "Realtek\xae"\n'.encode("cp1252"))
        assert config._read_toml(path) == {}
        assert config.BROKEN_CONFIG is not None and "UTF-8" in config.BROKEN_CONFIG.error

    def test_a_file_with_byte_order_mark_loads_and_saves(self, path):
        path.write_bytes('[ui]\nsound_feedback = false\n'.encode("utf-8-sig"))
        assert config._read_toml(path) == {"ui": {"sound_feedback": False}}
        assert config.BROKEN_CONFIG is None
        assert REAL_PERSIST("ui", "window_x", 5) is True
        assert config._read_toml(path) == {"ui": {"sound_feedback": False, "window_x": 5}}

    def test_quit_reaches_the_shutdown_with_a_cp1252_config(self, live_app, path, monkeypatch):
        path.write_bytes('[audio]\ndevice = "Realtek\xae"\n'.encode("cp1252"))
        monkeypatch.setattr(main_window_mod, "_persist_config_value", REAL_PERSIST)
        live_app._window_pos = (CFG.window_x or 0) + 7, (CFG.window_y or 0) + 7
        window = live_app.root
        live_app._quit_on_ui_thread()
        assert live_app._shutting_down and live_app.tray_icon.stopped
        window.update()
        assert not window.winfo_exists()

    def test_hiding_survives_a_failing_save(self, live_app, monkeypatch):
        def broken():
            raise RuntimeError("Datei weg")

        monkeypatch.setattr(live_app, "_save_position", broken)
        live_app._hide_window()
        assert live_app._window_visible is False


# ---------------------------------------------------------------- 3. stopping during a failed load


class TestStoppingAlwaysWorks:
    def _recording_app(self):
        app = App.__new__(App)
        app.model_ready = threading.Event()
        app._switching_to = None
        app._prompt_mode = False
        app.is_recording = True
        app.model_error = "download failed"
        app._state = "error"
        app.stopped = []
        app.stop_recording_and_transcribe = lambda: app.stopped.append(True)
        app._on_model_error = lambda: pytest.fail("the error must not refuse the stop")
        return app

    @pytest.mark.parametrize("toggle", ["toggle_recording", "toggle_recording_generate"])
    def test_a_failed_model_never_refuses_the_stop(self, toggle):
        app = self._recording_app()
        getattr(App, toggle)(app)
        assert app.stopped == [True]

    def test_a_model_error_during_the_recording_waits_for_the_stop(self, live_app, hams):
        live_app.toggle_recording()
        assert live_app._state == "recording"
        live_app._after_switch("turbo", False, persist=False, requested="turbo")
        assert live_app._state == "recording", "the window keeps saying Recording"
        live_app.toggle_recording()  # nothing recorded
        assert live_app.is_recording is False
        assert live_app._state == "error"
        assert live_app.status_label.cget("text") == "Modell konnte nicht geladen werden"

    def test_a_failed_download_during_the_recording_waits_for_the_stop(self, live_app, hams):
        live_app.toggle_recording()
        live_app._on_model_download_failed("small", "Server nicht erreichbar")
        assert live_app._state == "recording"
        assert live_app.model_error == "Server nicht erreichbar"
        live_app.toggle_recording()
        assert live_app.footer_label.cget("text") == "Server nicht erreichbar"

    def test_a_fallback_during_the_recording_follows_the_pasted_dictation(self, live_app, hams, monkeypatch):
        monkeypatch.setattr(live_app, "_enqueue_transcription", lambda *job: None)
        live_app.model_ready.set()
        live_app.toggle_recording()
        live_app._after_switch("turbo", True, persist=True, requested="small")
        assert live_app._state == "recording"
        hams.streams[-1].feed(np.full(800, 0.2))
        live_app.toggle_recording()
        assert live_app._state == "transcribing"
        live_app._back_to_ready()  # the pasted dictation's "done" runs out
        assert live_app.status_label.cget("text") == "Small ließ sich nicht laden"

    def test_a_missing_model_is_left_to_the_queued_dictation(self, live_app, hams, monkeypatch):
        monkeypatch.setattr(live_app, "_enqueue_transcription", lambda *job: None)
        live_app.toggle_recording()
        live_app.model_error = "CUDA out of memory"  # what the failed load leaves behind
        live_app._after_switch("turbo", False, persist=False, requested="turbo")
        hams.streams[-1].feed(np.full(800, 0.2))
        live_app.toggle_recording()
        assert live_app._notice_after_pipeline is None, "the dictation reports the missing model itself"

    def test_a_model_ready_during_the_recording_leaves_the_window_alone(self, live_app):
        live_app.toggle_recording()
        live_app._after_switch("turbo", True, persist=False, requested="turbo")
        assert live_app._state == "recording"


# ---------------------------------------------------------------- 5. a late download


class _Downloader:
    def __init__(self):
        self.model_id, self.active, self.started = None, False, []

    def start(self, info, on_progress, on_finished):
        self.started.append(info.model_id)
        self.model_id, self.active = info.model_id, True
        return ""

    def cancel(self):
        self.active = False


class TestALateDownloadNeverOverridesANewerChoice:
    @pytest.fixture
    def app(self, live_app, model_cache_check, monkeypatch):
        from hams.whisper_model import WhisperModelMixin

        live_app.downloader = _Downloader()
        live_app.loaded = []

        def run_now(model_id, generation, persist=True):
            live_app.loaded.append(model_id)
            live_app._switch_worker(model_id, generation, persist)

        monkeypatch.setattr(live_app, "_start_switch_thread", run_now)
        monkeypatch.setattr(live_app, "_build_model", lambda model_id: (object(), "cpu", "int8"))
        monkeypatch.setattr(live_app, "_load_model_async", lambda: WhisperModelMixin._load_model_async(live_app))
        CFG.model_size = "small"
        return live_app

    def settle(self, app):
        app._ui_dispatcher.drain()
        app.root.update()

    def test_download_a_switch_to_b_a_finishes_b_stays(self, app, hams):
        app._load_model_async()  # "small" isn't cached: it is downloaded first
        assert app._load_after_download == "small"
        assert app.switch_model("base")
        self.settle(app)
        assert app._load_after_download is None
        assert (app.active_model_size, CFG.model_size) == ("base", "base")
        app.downloader.active = False
        app._on_download_finished("small", "")
        self.settle(app)
        assert app.loaded == ["base"], "the finished download of small loads nothing"
        assert (app.active_model_size, CFG.model_size) == ("base", "base")
        assert ("model", "size", "small") not in hams.saved

    def test_a_download_from_the_settings_still_switches_when_nothing_else_was_chosen(self, app):
        app.start_download("base")
        app.downloader.active = False
        app._on_download_finished("base", "")
        self.settle(app)
        assert app.loaded == ["base"]
        assert app.active_model_size == "base"

    def test_the_awaited_download_does_not_draw_over_a_recording(self, app):
        app._load_model_async()
        app.toggle_recording()
        app.downloader.active = False
        app._on_download_finished("small", "")
        self.settle(app)
        assert app.loaded == ["small"], "the recording waits for this model: load it"
        assert app._state == "recording"


# ---------------------------------------------------------------- 6. quit with queued dictations


class TestQuitWaitsForQueuedDictations:
    def test_quit_waits_while_a_dictation_is_transcribed(self, live_app, hams):
        live_app._pipeline_job_running = True
        live_app._quit_on_ui_thread()
        assert not live_app._shutting_down
        assert hams.hotkeys.entries == [], "no new dictation starts while quitting"
        live_app._pipeline_job_running = False
        live_app._quit_on_ui_thread()  # the next poll
        assert live_app._shutting_down

    def test_after_the_grace_period_the_rest_is_dropped_and_logged(self, live_app, caplog, monkeypatch):
        monkeypatch.setattr(live_app, "QUIT_DRAIN_SECONDS", 0.0)
        live_app._pipeline_job_running = True
        live_app._pipeline_jobs = __import__("collections").deque([("audio", False, 1)])
        with caplog.at_level(logging.WARNING, logger="hams"):
            live_app._quit_on_ui_thread()
        assert live_app._shutting_down
        assert any("2 dictation(s)" in r.getMessage() for r in caplog.records)

    def test_nothing_queued_quits_at_once(self, live_app):
        live_app._quit_on_ui_thread()
        assert live_app._shutting_down


# ---------------------------------------------------------------- 7. Tk from a worker thread


class TestTheOllamaPullReportsThroughTheDispatcher:
    def test_post_pull_never_calls_tk_from_the_worker(self, live_app, monkeypatch):
        live_app._open_settings()
        live_app.root.update()
        win = live_app.settings_win
        queued = []
        monkeypatch.setattr(live_app, "_ui_call", queued.append)
        touched = []
        win.win.after = lambda *args: touched.append(args)
        try:
            worker = threading.Thread(target=win._post_pull, args=(lambda: None,), name="pull")
            worker.start()
            worker.join(2)
        finally:
            del win.win.after
        assert touched == [], "Tk called from the pull thread"
        assert len(queued) == 1
        queued[0]()  # on the Tk thread: runs the callback and refreshes the row


# ---------------------------------------------------------------- 8. microphone lifecycle


class _Consumer:
    def __init__(self):
        self.events = []

    def suspend_microphone(self):
        self.events.append("suspend")

    def resume_microphone(self):
        streams = FakeStream.opened
        self.events.append(("resume", bool(streams) and streams[-1].closed))


class TestTheMicrophoneIsSharedCleanly:
    @pytest.fixture
    def consumer(self, live_app):
        consumer = _Consumer()
        live_app.register_mic_consumer(consumer)
        return consumer

    def test_the_level_monitor_comes_back_after_the_recording_released_the_device(self, live_app, consumer):
        live_app.toggle_recording()
        live_app.toggle_recording()
        assert consumer.events == ["suspend", ("resume", True)]

    def test_a_start_that_fails_gives_the_microphone_back(self, live_app, consumer):
        import sounddevice as sd

        FakeStream.fail_with = sd.PortAudioError("weg")
        live_app.toggle_recording()
        assert live_app._state == "error"
        assert consumer.events == ["suspend", ("resume", False)]

    def test_a_stream_that_opens_but_does_not_start_is_closed(self, live_app, consumer, monkeypatch):
        def refuse(self):
            raise RuntimeError("Gerät belegt")

        monkeypatch.setattr(FakeStream, "start", refuse)
        live_app.toggle_recording()
        assert FakeStream.opened[-1].closed
        assert live_app.stream is None and live_app.is_recording is False
        assert consumer.events == ["suspend", ("resume", True)]


# ---------------------------------------------------------------- 9. empty or broken AI answers


class _Response:
    def __init__(self, body: bytes, lines=None):
        self.body = body
        self.lines = lines or []

    def read(self):
        return self.body

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestAnUnusableAiAnswerIsAFailure:
    @pytest.fixture
    def answer(self, monkeypatch, pipeline_app):
        def install(response):
            monkeypatch.setattr(transcription_mod.urllib.request, "urlopen", lambda request, timeout=None: response)
            pipeline_app._run_transcription = lambda model, audio: ("was ist die hauptstadt", "de")
            pipeline_app._transcribe_audio(AUDIO, prompt_mode=True)
            return pipeline_app

        return install

    @pytest.mark.parametrize(
        "response",
        [
            _Response(b"", [b'{"response": "", "done": true}\n']),
            _Response(b"", [b'{"error": "model not found"}\n']),
            _Response(b"", [b'{"response": "Oslo"}\n', b'{"error": "out of memory"}\n']),
            _Response(b"", [b'{"response": "Os"}\n', b'{"response": "lo"}\n']),  # cut off, no "done"
            _Response(b"", [b'{"response": "   ", "done": true}\n']),
        ],
        ids=["empty", "error-field", "error-in-stream", "no-done", "blank-stream"],
    )
    def test_nothing_is_pasted_and_the_error_says_so(self, answer, response):
        app = answer(response)
        assert app.pasted == [], "the clipboard stays untouched"
        state, detail, hint = app.states[-1]
        assert (state, detail) == ("error", "KI-Antwort fehlgeschlagen")
        assert hint.startswith("Die KI hat keine brauchbare Antwort geliefert")

    def test_a_complete_stream_is_pasted(self, answer):
        app = answer(_Response(b"", [b'{"response": "Os"}\n', b'{"response": "lo", "done": true}\n']))
        assert app.pasted == ["Oslo"]
