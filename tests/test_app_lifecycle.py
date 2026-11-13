"""Die echte Anwendung: Aufbau, Kurzbefehle, Zustaende, Aufnahme, Beenden.

Andere Testdateien pruefen einzelne Methoden an einer Attrappe ohne Fenster.
Hier entsteht das Hauptfenster wirklich - mit allen Widgets, der echten
Zustandsmaschine und dem echten Ablauf von Start bis Stopp. Nur was den
Rechner beruehrt, ist ersetzt (siehe tests/live_app.py).
"""

import logging
import threading
import time
import tkinter as tk
from types import SimpleNamespace

import kit as uk
import numpy as np
import pytest
from live_app import FakeHotkeys, FakeStream

from hams import recording as recording_mod
from hams import ui_kit as hams_kit  # the package the app reads, for module-level patches
from hams.config import CFG
from hams.settings_window import SettingsWindow


def flush(app) -> None:
    """Arbeitet alles ab, was per `after(0, …)` auf den Tk-Thread wartet."""
    app.root.update()


def button_text(app) -> str:
    return app.main_button.cget("text")


class TestConstruction:
    def test_it_starts_hidden_and_loading(self, live_app, hams):
        assert live_app.root.state() == "withdrawn"
        assert live_app._state == "model_loading"
        assert live_app.status_label.cget("text") == "Modell wird geladen …"
        assert hams.loads == 1, "das Modell wird beim Start genau einmal angefordert"

    def test_recording_is_possible_while_the_model_loads(self, live_app):
        assert button_text(live_app) == "Aufnahme starten"
        assert not live_app.main_button.has_state(uk.State.DISABLED)

    def test_the_footer_shows_both_shortcuts(self, live_app):
        assert set(live_app.shortcut_keys) == {"transcript", "prompt"}
        assert live_app.shortcut_box.winfo_manager() == "pack"
        assert not live_app.footer_label.winfo_manager()

    def test_the_window_has_the_calculated_size(self, live_app):
        width, height = live_app._window_size
        assert width == uk.px(live_app.WINDOW_WIDTH)
        assert height >= uk.px(live_app.WINDOW_MIN_HEIGHT)

    def test_the_tray_exists(self, live_app):
        assert live_app.tray_icon is not None


class TestHotkeys:
    def test_both_shortcuts_go_through_the_system_first(self, live_app, hams):
        combos = [combo for combo, _handler in hams.hotkeys.entries]
        assert combos == [CFG.hotkey_transcript, CFG.hotkey_prompt]
        assert hams.keyboard.hotkeys == {}, "ohne Fehlschlag kein Tastaturhook"

    def test_a_system_hotkey_runs_on_the_tk_thread(self, live_app, hams, monkeypatch):
        calls = []
        monkeypatch.setattr(live_app, "toggle_recording", lambda: calls.append("diktat"))
        # Die Handler sind bei der Registrierung gebunden; neu registrieren,
        # damit die ersetzte Methode greift.
        live_app._register_hotkeys()
        handler = hams.hotkeys.entries[0][1]
        handler()
        assert calls == [], "der Handler darf nicht im Kurzbefehl-Thread arbeiten"
        flush(live_app)
        assert calls == ["diktat"]

    def test_only_a_failed_combo_falls_back_to_the_library(self, root, hams):
        FakeHotkeys.failing = {CFG.hotkey_prompt}
        from hams.app import FloatingTranscriberApp as App

        window = tk.Toplevel(root)
        try:
            App(window)
            assert list(hams.keyboard.hotkeys) == [CFG.hotkey_prompt]
        finally:
            window.destroy()

    @pytest.mark.parametrize(
        "error, text",
        [
            (ValueError("unbekannte Taste"), "lässt sich nicht registrieren"),
            (RuntimeError("belegt"), "Vermutlich belegt ihn ein anderes Programm"),
        ],
    )
    def test_a_refused_combo_is_shown_not_swallowed(self, live_app, hams, error, text):
        FakeHotkeys.failing = {CFG.hotkey_transcript}
        hams.keyboard.refuse[CFG.hotkey_transcript] = error
        live_app._register_hotkeys()
        flush(live_app)
        assert live_app._state == "error"
        assert live_app.status_label.cget("text") == "Kurzbefehl nicht verfügbar"
        assert text in live_app.footer_label.cget("text")
        assert live_app.footer_label.winfo_manager() == "pack"
        assert not live_app.shortcut_box.winfo_manager()


class TestStates:
    def test_ready_names_the_model(self, live_app):
        CFG.model_size = "large-v3-turbo"
        live_app._apply_state("ready")
        assert live_app.status_label.cget("text").startswith("Bereit · ")

    def test_a_hint_replaces_the_shortcuts_and_goes_again(self, live_app):
        before = live_app.footer.cget("height")
        live_app._apply_state("error", "Kaputt", "Einzelheiten stehen im Log.")
        assert live_app.footer_label.cget("text") == "Einzelheiten stehen im Log."
        live_app._apply_state("ready")
        assert live_app.shortcut_box.winfo_manager() == "pack"
        assert not live_app.footer_label.winfo_manager()
        assert live_app.footer.cget("height") == before, "das Fenster darf nicht springen"

    def test_recording_shows_the_stop_symbol(self, live_app):
        live_app._apply_state("recording")
        assert button_text(live_app) == "Stoppen"
        assert live_app.main_button._icon_name == "stop"
        live_app._apply_state("ready")
        assert live_app.main_button._icon_name is None

    def test_the_cursor_dot_follows_every_state(self, live_app, hams):
        live_app._apply_state("recording_prompt")
        assert hams.dots[-1] == ("recording_prompt", None)

    def test_done_falls_back_to_ready_by_itself(self, live_app):
        live_app.model_ready.set()
        live_app._apply_state("done")
        assert live_app._done_job is not None
        live_app._back_to_ready()
        assert live_app._state == "ready"

    def test_done_does_not_interrupt_a_new_recording(self, live_app):
        live_app.model_ready.set()
        live_app.is_recording = True
        live_app._apply_state("done")
        live_app._back_to_ready()
        assert live_app._state == "done"
        live_app.is_recording = False

    def test_a_new_state_cancels_the_pending_fallback(self, live_app):
        live_app._apply_state("done")
        live_app._apply_state("transcribing")
        assert live_app._done_job is None

    @pytest.mark.parametrize(
        "state, key",
        [
            ("recording", "RECORDING"),
            ("recording_prompt", "AI"),
            ("generating", "AI"),
            ("error", "ERROR"),
            ("ready", "ACCENT_TINT"),
        ],
    )
    def test_the_tray_colour_follows_the_state(self, live_app, state, key):
        from hams.tray import TRAY_PALETTE

        assert live_app._tray_colour(state) == TRAY_PALETTE[key]
        live_app._apply_state(state)
        assert live_app.tray_icon.icon is not None


class TestMainAction:
    def test_retry_reloads_a_failed_model(self, live_app, hams):
        live_app.model_error = "CUDA fehlt"
        live_app._apply_state("error")
        live_app._on_main_action()
        assert live_app.model_error is None
        assert live_app._state == "model_loading"
        assert hams.loads == 2

    def test_retry_with_a_loaded_model_just_returns_to_ready(self, live_app, hams):
        live_app.model_ready.set()
        live_app._apply_state("error")
        live_app._on_main_action()
        assert live_app._state == "ready"
        assert hams.loads == 1

    def test_outside_an_error_the_button_records(self, live_app, monkeypatch):
        calls = []
        monkeypatch.setattr(live_app, "toggle_recording", lambda: calls.append(1))
        live_app._on_main_action()
        assert calls == [1]


class TestMicConsumers:
    def test_each_consumer_is_asked_even_if_one_fails(self, live_app):
        log = []

        class Broken:
            def suspend_microphone(self):
                raise RuntimeError("kaputt")

            def resume_microphone(self):
                raise RuntimeError("kaputt")

        class Good:
            def suspend_microphone(self):
                log.append("aus")

            def resume_microphone(self):
                log.append("an")

        good = Good()
        live_app.register_mic_consumer(Broken())
        live_app.register_mic_consumer(good)
        live_app.register_mic_consumer(good)  # doppelt zaehlt nicht
        live_app._suspend_mic_consumers()
        live_app._resume_mic_consumers()
        assert log == ["aus", "an"]
        live_app.unregister_mic_consumer(good)
        live_app.unregister_mic_consumer(good)  # zweimal abmelden ist harmlos
        assert good not in live_app._mic_consumers


class TestDpiChange:
    def test_an_event_from_a_child_is_ignored(self, live_app, monkeypatch):
        monkeypatch.setattr(hams_kit, "window_dpi", lambda _w: pytest.fail("nicht gefragt"))
        live_app._on_root_configure(SimpleNamespace(widget=live_app.main_button))

    def test_the_same_dpi_changes_nothing(self, live_app, monkeypatch):
        monkeypatch.setattr(hams_kit, "window_dpi", lambda _w: uk.Scale.dpi)
        monkeypatch.setattr(uk.Scale, "apply", lambda *_a: pytest.fail("umskaliert"))
        live_app._on_root_configure(SimpleNamespace(widget=live_app.root))

    def test_a_new_dpi_rescales_and_refits(self, live_app, monkeypatch):
        applied = []
        monkeypatch.setattr(hams_kit, "window_dpi", lambda _w: uk.Scale.dpi + 48)
        monkeypatch.setattr(uk.Scale, "apply", lambda root, dpi: applied.append(dpi))
        live_app._on_root_configure(SimpleNamespace(widget=live_app.root))
        assert applied == [uk.Scale.dpi + 48]


class TestWindow:
    def test_show_and_hide(self, live_app):
        live_app._toggle_visibility()
        flush(live_app)
        assert live_app._window_visible
        assert live_app.root.state() == "normal"
        live_app._toggle_visibility()
        flush(live_app)
        assert not live_app._window_visible
        assert live_app.root.state() == "withdrawn"

    def test_dragging_moves_the_window_and_saves_once_it_rests(self, live_app, hams):
        live_app._show_window()
        flush(live_app)
        live_app._start_drag(SimpleNamespace(x=10, y=10))
        live_app._on_drag(SimpleNamespace(x=30, y=25))
        assert live_app._position_job is not None
        assert not [s for s in hams.saved if s[1] in ("window_x", "window_y")]
        live_app._save_position()
        assert (CFG.window_x, CFG.window_y) == live_app._window_pos
        assert {s[1] for s in hams.saved} >= {"window_x", "window_y"}

    def test_settings_open_once_and_are_focused_afterwards(self, live_app):
        live_app._open_settings()
        flush(live_app)
        first = live_app.settings_win
        assert isinstance(first, SettingsWindow) and first.alive()
        live_app._open_settings()
        flush(live_app)
        assert live_app.settings_win is first

    def test_switching_the_appearance_repaints_both_windows(self, live_app):
        live_app._open_settings()
        flush(live_app)
        other = uk.Theme.LIGHT if uk.Theme.mode == uk.Theme.DARK else uk.Theme.DARK
        live_app.set_appearance(other)
        assert CFG.appearance == other
        assert live_app.root.cget("bg") == uk.Theme.WINDOW_BG
        assert live_app.settings_win.win.cget("bg") == uk.Theme.WINDOW_BG


class TestShutdown:
    def test_releasing_frees_every_resource(self, live_app, hams):
        stream = FakeStream(callback=None)
        stream.running = True
        live_app.stream = stream
        live_app.model = object()
        live_app.model_ready.set()
        live_app._release_resources()
        assert stream.closed and live_app.stream is None
        assert hams.hotkeys.stopped and hams.keyboard.unhooked
        assert live_app.model is None and not live_app.model_ready.is_set()

    def test_quitting_stops_the_tray_and_closes_the_window(self, live_app, hams):
        window = live_app.root
        live_app._quit_app()
        live_app._quit_on_ui_thread()  # what the Tk thread runs next
        assert live_app._shutting_down
        assert live_app.tray_icon.stopped
        assert hams.beeps == [(440, 200)]
        window.update()
        assert not window.winfo_exists()

    def test_quit_from_the_tray_thread_waits_for_the_tk_thread(self, live_app, hams):
        """pystray calls "Quit" on its own thread; nothing may be released there."""
        window = live_app.root
        caller = threading.Thread(target=live_app._quit_app, name="tray")
        caller.start()
        caller.join(2)
        assert not caller.is_alive(), "the tray thread must not block on Tk"
        assert not live_app._shutting_down and not live_app.tray_icon.stopped
        ende = time.monotonic() + 2
        while window.winfo_exists() and time.monotonic() < ende:
            window.update()
            time.sleep(0.01)
        assert live_app._shutting_down and live_app.tray_icon.stopped
        assert not window.winfo_exists()

    def test_quitting_twice_shuts_down_once(self, live_app, hams):
        live_app._quit_on_ui_thread()
        live_app._quit_on_ui_thread()
        assert hams.beeps == [(440, 200)]

    def test_a_broken_tray_does_not_block_quitting(self, live_app):
        def broken():
            raise RuntimeError("tray weg")

        live_app.tray_icon.stop = broken
        window = live_app.root
        live_app._quit_on_ui_thread()
        window.update()
        assert not window.winfo_exists()


# ---------------------------------------------------------------- Aufnahme


def wait_for_thread(name: str, timeout: float = 2.0) -> None:
    ende = time.monotonic() + timeout
    while time.monotonic() < ende:
        if not any(t.name == name and t.is_alive() for t in threading.enumerate()):
            return
        time.sleep(0.01)
    raise AssertionError(f"Thread {name} laeuft noch")


class TestRecording:
    def test_start_opens_the_microphone_and_shows_it(self, live_app, hams):
        live_app.model_ready.set()
        live_app.toggle_recording()
        try:
            stream = hams.streams[-1]
            assert stream.running and stream.kwargs["device"] == 7
            assert live_app.is_recording and live_app._state == "recording"
            assert CFG.mic_device == 7, "das Geraet wird je Aufnahme neu aufgeloest"
            assert live_app._max_duration_timer is not None
            assert live_app.elapsed_label.cget("text") == "0:00"
            assert live_app.tray_icon.title.endswith("Aufnahme")
        finally:
            live_app._release_resources()
            live_app.is_recording = False

    def test_prompt_mode_has_its_own_state(self, live_app):
        live_app.toggle_recording_generate()
        try:
            assert live_app._state == "recording_prompt"
            assert live_app._prompt_mode
        finally:
            live_app._release_resources()
            live_app.is_recording = False

    def test_the_level_meter_and_cursor_dot_follow_the_signal(self, live_app, hams):
        live_app.toggle_recording()
        try:
            hams.streams[-1].feed(np.full(160, 0.3))
            live_app._level_tick()
            level = hams.dots[-1][1]
            assert hams.dots[-1][0] == "recording" and 0.4 < level <= 1.0
            assert live_app.frames and live_app.frames[-1].shape == (160, 1)
        finally:
            live_app._release_resources()
            live_app.is_recording = False

    def test_a_consumer_lets_go_of_the_microphone_first(self, live_app):
        order = []
        consumer = SimpleNamespace(
            suspend_microphone=lambda: order.append("frei"), resume_microphone=lambda: order.append("zurueck")
        )
        live_app.register_mic_consumer(consumer)
        live_app.start_recording()
        assert order == ["frei"]
        live_app.stop_recording_and_transcribe()
        assert order == ["frei", "zurueck"]

    def test_stopping_without_audio_says_so(self, live_app):
        live_app.toggle_recording()
        live_app.frames.clear()
        live_app.toggle_recording()
        assert live_app._state == "no_signal"
        assert live_app.status_label.cget("text") == "Keine Aufnahme erkannt"
        assert live_app.stream is None and not live_app._prompt_mode

    def test_silence_is_reported_as_a_muted_microphone(self, live_app, hams):
        live_app.toggle_recording_generate()
        hams.streams[-1].feed(np.full(1600, 0.0001))
        live_app.toggle_recording()
        assert live_app._state == "no_signal"
        assert "Ist das Mikrofon stummgeschaltet?" in live_app.footer_label.cget("text")
        assert not live_app._prompt_mode

    def test_speech_goes_on_to_transcription(self, live_app, hams, monkeypatch):
        received = []
        monkeypatch.setattr(live_app, "_transcribe_audio", lambda audio, **_job: received.append(audio))
        live_app.toggle_recording()
        hams.streams[-1].feed(np.full(800, 0.2))
        hams.streams[-1].feed(np.full(800, -0.2))
        live_app.toggle_recording()
        wait_for_thread("transcribe")
        assert live_app._state == "transcribing"
        assert received and received[0].shape == (1600,)

    def test_a_missing_device_is_named(self, live_app):
        import sounddevice as sd

        FakeStream.fail_with = sd.PortAudioError("weg")
        CFG.mic_device_raw = "Headset"
        live_app.toggle_recording()
        assert not live_app.is_recording
        assert live_app.status_label.cget("text") == "Mikrofon nicht verfügbar"
        assert "„Headset“" in live_app.footer_label.cget("text")

    def test_an_unplugged_microphone_chosen_by_name_is_named_too(self, live_app):
        """sounddevice raises ValueError for a name it can't find (audit finding H9), not PortAudioError."""
        FakeStream.fail_with = ValueError("No input device matching 'Headset'")
        CFG.mic_device_raw = "Headset"
        live_app.toggle_recording()
        assert not live_app.is_recording
        assert live_app.status_label.cget("text") == "Mikrofon nicht verfügbar"
        assert "„Headset“" in live_app.footer_label.cget("text")

    def test_any_other_failure_still_ends_in_a_clear_state(self, live_app):
        FakeStream.fail_with = OSError("irgendwas")
        live_app.toggle_recording()
        assert not live_app.is_recording
        assert live_app.status_label.cget("text") == "Aufnahme nicht gestartet"

    def test_a_failed_model_refuses_before_opening_the_microphone(self, live_app, hams):
        live_app.model_error = "CUDA fehlt"
        live_app.toggle_recording()
        live_app.toggle_recording_generate()
        assert hams.streams == []
        assert live_app.status_label.cget("text") == "Modell konnte nicht geladen werden"

    def test_the_maximum_duration_stops_by_itself(self, live_app, hams):
        live_app.toggle_recording()
        live_app._on_max_duration_reached()
        flush(live_app)
        assert not live_app.is_recording
        live_app._auto_stop_recording()  # zweites Mal: nichts mehr zu tun
        assert live_app._state == "no_signal"


class TestAwaitModel:
    def test_a_ready_model_is_there_at_once(self, live_app):
        live_app.model_ready.set()
        assert live_app._await_model(time.monotonic() + 5)

    def test_a_failed_load_ends_the_wait(self, live_app):
        live_app.model_error = "kaputt"
        start = time.monotonic()
        assert not live_app._await_model(start + 5)
        assert time.monotonic() - start < 1

    def test_a_switch_is_waited_out_despite_an_old_error(self, live_app):
        live_app.model_error = "alter Fehler"
        live_app._switching_to = "small"
        threading.Timer(0.3, live_app.model_ready.set).start()
        assert live_app._await_model(time.monotonic() + 5)

    def test_the_deadline_is_respected(self, live_app):
        assert not live_app._await_model(time.monotonic() + 0.3)


class TestNormalize:
    def test_quiet_speech_is_raised(self):
        out = recording_mod._normalize_audio(np.array([0.05, -0.1], dtype=np.float32))
        assert float(np.max(np.abs(out))) == pytest.approx(0.35)

    def test_silence_and_loud_audio_stay(self):
        for samples in ([0.001, -0.002], [0.5, -0.9]):
            audio = np.array(samples, dtype=np.float32)
            assert recording_mod._normalize_audio(audio) is audio

    def test_empty_audio_is_passed_through(self):
        empty = np.array([], dtype=np.float32)
        assert recording_mod._normalize_audio(empty) is empty


class TestDisplayLevel:
    def test_speech_from_half_a_metre_is_clearly_visible(self):
        assert recording_mod._display_level(0.003**2) > 0.3

    def test_room_noise_stays_at_zero(self):
        assert recording_mod._display_level(10 ** (-64 / 10)) == 0.0
        assert recording_mod._display_level(0.0) == 0.0

    def test_close_speech_fills_the_meter(self):
        assert recording_mod._display_level(0.05**2) == 1.0


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
