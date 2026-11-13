"""The settings window is built once and then only shown and hidden.

Checks the lifecycle through the real app: building ahead (`prebuild_settings`), showing, hiding, showing the
same window again with the current state, a colour scheme switched while hidden, the model cache read in the
background, and that showing again does not redraw fields that did not change.
"""

import sys
import time
from types import SimpleNamespace

import kit as uk
import pytest
from setup_fakes import InlineThread

from hams import models as whisper_models
from hams import settings_extras as extras_mod
from hams import settings_models as models_mod
from hams.config import CFG
from hams.models import CachedModel
from hams.settings_window import SettingsWindow


@pytest.fixture
def disk(monkeypatch):
    state = SimpleNamespace(installed={"large-v3-turbo"}, cached=[], scans=0, fail=False)

    def cached():
        state.scans += 1
        if state.fail:
            raise OSError("cache gone")
        return tuple(state.cached)

    monkeypatch.setattr(whisper_models, "installed_whisper_models", lambda: set(state.installed))
    monkeypatch.setattr(whisper_models, "cached_whisper_models", cached)
    return state


@pytest.fixture
def app(live_app, disk, monkeypatch):
    inline = SimpleNamespace(Thread=InlineThread)
    monkeypatch.setattr(models_mod, "threading", inline)
    monkeypatch.setattr(extras_mod, "threading", inline)
    CFG.model_size = "large-v3-turbo"
    CFG.mic_device_raw = "Headset"
    return live_app


def settle_hidden_layout(app):
    """Runs the main loop past the invisible layout ahead (`SettingsWindow.lay_out_hidden`)."""
    end = time.monotonic() + SettingsWindow.LAYOUT_HIDDEN_MS / 1000 + 1.0
    while time.monotonic() < end and app.settings_win._layout_job is not None:
        app.root.update()
        time.sleep(0.01)
    app.root.update()


def open_(app):
    app._open_settings()
    app.root.update()
    return app.settings_win


class TestPrebuild:
    def test_builds_hidden_and_the_first_opening_only_shows_it(self, app):
        app.prebuild_settings()
        settle_hidden_layout(app)
        built = app.settings_win
        assert isinstance(built, SettingsWindow)
        assert not built.alive() and built.reusable()
        assert built.win.state() == "withdrawn"
        assert float(built.win.attributes("-alpha")) == 1.0
        assert open_(app) is built
        assert built.alive()

    @pytest.mark.skipif(sys.platform != "win32", reason="the invisible layout only runs on Windows")
    def test_the_layout_ahead_is_invisible_and_off_screen(self, app):
        app.prebuild_settings()
        app.root.update()
        win = app.settings_win
        assert win.win.state() == "normal"
        assert float(win.win.attributes("-alpha")) == 0.0
        assert win.win.winfo_x() <= SettingsWindow.OFFSCREEN + 1
        assert not win.alive()
        # Laid out at the real size: the groups have their final width instead of Tk's 1 px or 10 cm default.
        assert win.general.group.winfo_width() > 300
        settle_hidden_layout(app)
        assert win.win.state() == "withdrawn"

    @pytest.mark.skipif(sys.platform != "win32", reason="the invisible layout only runs on Windows")
    def test_opening_during_the_layout_ahead_shows_it_opaque_on_screen(self, app):
        app.prebuild_settings()
        app.root.update()
        win = open_(app)
        assert win.alive()
        assert float(win.win.attributes("-alpha")) == 1.0
        assert win.win.winfo_x() > SettingsWindow.OFFSCREEN + 1000
        settle_hidden_layout(app)
        assert win.win.state() == "normal", "the cancelled layout timer must not hide the shown window"

    def test_the_layout_ahead_is_skipped_for_a_shown_window(self, app):
        win = open_(app)
        win.lay_out_hidden()
        assert win._layout_job is None
        assert float(win.win.attributes("-alpha")) == 1.0

    def test_nothing_runs_while_built_ahead(self, app, hams):
        app.prebuild_settings()
        app.root.update()
        monitor = app.settings_win.recording.monitor
        assert hams.streams == []
        assert monitor not in app.recorder.consumers
        assert not app.settings_win.ai.pending("ollama")

    def test_a_second_prebuild_keeps_the_first_window(self, app):
        app.prebuild_settings()
        first = app.settings_win
        app.prebuild_settings()
        assert app.settings_win is first

    def test_a_recording_postpones_it(self, app, monkeypatch):
        monkeypatch.setattr(type(app), "is_recording", property(lambda self: True))
        scheduled = []
        monkeypatch.setattr(app.root, "after", lambda ms, fn: scheduled.append((ms, fn)))
        app.prebuild_settings()
        assert app.settings_win is None
        assert scheduled == [(app.PREBUILD_DELAY_MS, app.prebuild_settings)]

    def test_not_while_shutting_down(self, app):
        app._shutting_down = True
        app.prebuild_settings()
        assert app.settings_win is None


class TestHideAndShowAgain:
    def test_closing_hides_and_stops_everything(self, app, hams):
        win = open_(app)
        monitor = win.recording.monitor
        assert monitor.stream is not None
        win.close()
        app.root.update()
        assert not win.alive() and win.reusable()
        assert win.win.state() == "withdrawn"
        assert monitor.stream is None and not monitor.active
        assert monitor not in app.recorder.consumers
        assert not win.ai.pending("ollama")
        assert app.settings_win is win

    def test_reopening_reuses_the_window_with_the_current_state(self, app):
        win = open_(app)
        win.close()
        CFG.sound_feedback = not CFG.sound_feedback
        CFG.save_transcripts = not CFG.save_transcripts
        again = open_(app)
        assert again is win
        assert win.general.switch_sound.value == CFG.sound_feedback
        assert win.general.switch_transcripts.value == CFG.save_transcripts

    def test_a_switch_synced_while_hidden_jumps_instead_of_gliding(self, app, monkeypatch):
        win = open_(app)
        win.close()
        monkeypatch.setattr(uk.Animator, "enabled", True)
        CFG.sound_feedback = not CFG.sound_feedback
        open_(app)
        assert uk.Animator.pending(win.general.switch_sound) == 0

    def test_reopening_scrolls_back_to_the_top(self, app):
        win = open_(app)
        win.scroll.scroll_by(200)
        app.root.update()
        assert win.scroll.offset > 0
        win.close()
        open_(app)
        assert win.scroll.offset == 0

    def test_opening_twice_only_focuses(self, app):
        win = open_(app)
        assert open_(app) is win and win.alive()

    def test_a_destroyed_window_is_built_anew(self, app):
        win = open_(app)
        win.destroy()
        app.root.update()
        assert not win.reusable()
        again = open_(app)
        assert again is not win and again.alive()

    def test_a_colour_scheme_switched_while_hidden_is_caught_up(self, app):
        win = open_(app)
        start = uk.Theme.mode
        other = uk.Theme.DARK if start == uk.Theme.LIGHT else uk.Theme.LIGHT
        win.close()
        app.set_appearance(other)
        assert win._painted_mode == start, "a hidden window is not repainted at once"
        open_(app)
        assert win._painted_mode == other
        assert win.win.cget("bg") == uk.Theme.WINDOW_BG
        app.set_appearance(start)

    def test_an_open_popover_of_the_window_closes_with_it(self, app):
        win = open_(app)
        win.general.select_appearance.open_popover()
        app.root.update()
        assert uk.Popover.current is not None
        win.close()
        assert uk.Popover.current is None

    def test_showing_again_does_not_redraw_unchanged_fields(self, app, monkeypatch):
        win = open_(app)
        win.close()
        app.root.update()
        painted = []
        original = uk.Select._paint
        monkeypatch.setattr(uk.Select, "_paint", lambda self, w, h: painted.append(self) or original(self, w, h))
        open_(app)
        assert win.general.select_appearance not in painted
        assert win.transcription.select_dictation not in painted


class TestInventory:
    def test_the_cache_is_read_off_the_window_and_applied(self, app, disk):
        disk.cached = [CachedModel("large-v3-turbo", "Turbo", 1_600_000_000, True, ("abc",))]
        win = open_(app)
        app.root.update()
        assert win.transcription.cached == disk.cached
        assert win.transcription.installed == {"large-v3-turbo"}

    def test_reopening_reads_it_again(self, app, disk):
        win = open_(app)
        app.root.update()
        before = disk.scans
        win.close()
        disk.installed = {"large-v3-turbo", "tiny"}
        open_(app)
        app.root.update()
        assert disk.scans > before
        assert "tiny" in win.transcription.installed

    def test_a_failed_scan_shows_nothing_cached(self, app, disk):
        disk.fail = True
        win = open_(app)
        app.root.update()
        assert win.transcription.cached == []
        assert win.transcription.installed == set()
        assert not win.transcription._scanning

    def test_a_scan_already_running_is_not_started_twice(self, app, monkeypatch):
        win = open_(app)
        started = []
        monkeypatch.setattr(
            models_mod,
            "threading",
            SimpleNamespace(Thread=lambda **kw: SimpleNamespace(start=lambda: started.append(kw))),
        )
        win.transcription.scan_inventory()
        win.transcription.scan_inventory()
        assert len(started) == 1


class TestKitHelpers:
    def test_switch_set_without_animation_lands_at_once(self, root, monkeypatch):
        import tkinter as tk

        monkeypatch.setattr(uk.Animator, "enabled", True)
        top = tk.Toplevel(root)
        switch = uk.Switch(top, bg_under=uk.Theme.GROUP_SURFACE, value=False, command=lambda value: None)
        switch.set(True, animate=False)
        assert switch.value and switch._progress == 1.0
        assert uk.Animator.pending(switch) == 0
        switch.set(True, animate=False)  # unchanged: nothing happens
        assert switch._progress == 1.0
        top.destroy()

    def test_select_redraws_only_when_its_label_changes(self, root, monkeypatch):
        import tkinter as tk

        top = tk.Toplevel(root)
        select = uk.Select(
            top, bg_under=uk.Theme.GROUP_SURFACE, options=[uk.Option("a", "A"), uk.Option("b", "B")], value="a"
        )
        select.pack()
        root.update()
        renders = []
        original = select._render
        monkeypatch.setattr(select, "_render", lambda: renders.append(1) or original())
        select.set_value("a")
        select.set_options([uk.Option("a", "A"), uk.Option("c", "C")])
        assert renders == []
        select.set_value("c")
        assert renders == [1]
        select.set_options([uk.Option("c", "C neu")])
        assert renders == [1, 1]
        top.destroy()
