"""Phase 5 of the rebuild: windows that stay calm while they change.

* resizing lays the content out live, once a frame, with nothing stale on screen (`LiveResize`, `Resizing`);
* the settings lay out their hidden pages ahead, so even the first visit of a tab is quick (`warm_pages`);
* after the invisible prebuild the settings are no layered window any more (`clear_layered`);
* every window notices a move to a monitor with another scaling, not only the main window (`check_dpi`);
* settings and assistant share one painted head (`Stage`) whose wash keeps clear of title and cross;
* the assistant closes with Escape, and its close question goes away when the download it asked about ends.
"""

import sys
import time
import tkinter as tk
from types import SimpleNamespace

import numpy as np
import pytest
from setup_fakes import InlineThread, fake_services

from hams import setup_window as setup_mod
from hams import setup_wizard as sw
from hams import ui_kit as uk
from hams.settings_window import _pad_pair
from hams.setup_window import SetupWindow
from hams.ui_kit import banner as banner_mod
from hams.ui_kit import window as window_mod
from hams.ui_kit.resizing import Resizing
from hams.window_stage import Stage

#: Taken before the autouse fixture replaces it for the simulated gestures.
REAL_BUTTON_DOWN = window_mod.primary_button_down


def event(widget, x, y):
    return SimpleNamespace(widget=widget, x_root=x, y_root=y)


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.configure(bg=uk.Theme.WINDOW_BG)
    win.geometry("300x200+200+150")
    yield win
    win.destroy()


# ---------------------------------------------------------------- live resizing


class TestLiveResize:
    def build(self, win, root):
        top = tk.Frame(win, bg="red", height=20)
        top.pack(fill="x", padx=4)
        middle = tk.Frame(win, bg="green")
        middle.pack(fill="both", expand=True)
        root.update()
        return top, middle

    def test_the_content_follows_while_dragging_not_after(self, window, root):
        _top, middle = self.build(window, root)
        chrome = uk.WindowChrome(window, min_size=(200, 120))
        chrome._on_press(event(window, 499, 250))
        assert chrome.live.active and Resizing.active()
        chrome._on_drag(event(window, 539, 250))
        root.update()
        assert middle.winfo_width() == 340, "laid out during the drag"
        chrome._on_drag(event(window, 519, 250))
        root.update()
        assert middle.winfo_width() == 320
        chrome._on_release()
        root.update()
        assert not chrome.live.active and not Resizing.active()
        assert window.pack_slaves() and middle.winfo_manager() == "pack"

    def test_a_plate_paints_its_new_size_at_once_while_resizing(self, window, root):
        plate = uk.GroupSurface(window, bg_under=uk.Theme.WINDOW_BG)
        plate.pack(fill="x")
        tk.Frame(plate.body, height=40).pack(fill="x")
        root.update()
        Resizing.enter()
        try:
            window.geometry("360x200")
            window.update_idletasks()
            assert plate._plate.width() == plate.winfo_width(), "no frame with the old picture"
        finally:
            Resizing.leave()

    def test_outside_a_resize_the_plate_waits_for_the_layout_to_settle(self, window, root):
        plate = uk.GroupSurface(window, bg_under=uk.Theme.WINDOW_BG)
        plate.pack(fill="x")
        root.update()
        plate.event_generate("<Configure>", width=250, height=60)
        assert plate._render_job is not None
        root.update()

    @pytest.mark.skipif(sys.platform != "win32", reason="window classes are a Windows matter")
    def test_while_resizing_windows_does_not_redraw_the_whole_window(self, window, root):
        import ctypes

        self.build(window, root)
        user32 = ctypes.windll.user32
        inner = window.winfo_id()
        handles = (user32.GetParent(inner), inner)
        before = [user32.GetClassLongW(hwnd, -26) for hwnd in handles]
        live = uk.LiveResize(window)
        live.begin()
        live.begin()
        assert all(not user32.GetClassLongW(hwnd, -26) & 0x3 for hwnd in handles)
        live.end()
        live.end()
        assert [user32.GetClassLongW(hwnd, -26) for hwnd in handles] == before
        assert not Resizing.active()

    def test_a_lost_release_ends_the_gesture(self, window, root, monkeypatch):
        """Win+D or the lock screen swallow the release; the guard sees the button is up and ends the gesture."""
        self.build(window, root)
        chrome = uk.WindowChrome(window, min_size=(200, 120))
        monkeypatch.setattr(window_mod, "primary_button_down", lambda: True)
        chrome._on_press(event(window, 499, 250))
        assert chrome._watch_job is not None
        chrome._watch()
        assert chrome.live.active, "still held: the gesture goes on"
        monkeypatch.setattr(window_mod, "primary_button_down", lambda: False)
        chrome._watch()
        assert chrome._gesture is None and not chrome.live.active and not Resizing.active()

    def test_where_nobody_can_tell_the_guard_waits_for_the_release(self, window, root, monkeypatch):
        self.build(window, root)
        chrome = uk.WindowChrome(window, min_size=(200, 120))
        monkeypatch.setattr(window_mod, "primary_button_down", lambda: None)
        chrome._on_press(event(window, 499, 250))
        chrome._watch()
        assert chrome.live.active
        chrome._on_release()
        assert chrome._watch_job is None and not chrome.live.active

    def test_a_window_that_goes_away_mid_gesture_ends_it(self, root):
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.geometry("300x200+200+150")
        root.update()
        chrome = uk.WindowChrome(win, min_size=(200, 120))
        chrome._on_press(event(win, 499, 250))
        assert Resizing.active()
        win.destroy()
        assert not chrome.live.active and not Resizing.active()

    def test_the_button_state_is_read(self):
        expected = (True, False) if sys.platform == "win32" else (None,)
        assert REAL_BUTTON_DOWN() in expected

    def test_moving_does_not_resize(self, window, root):
        self.build(window, root)
        chrome = uk.WindowChrome(window, min_size=(200, 120))
        chrome._on_press(event(window, 350, 250))
        assert not chrome.live.active
        chrome._on_release()


# ---------------------------------------------------------------- settings: hidden pages, layered style


@pytest.fixture
def settings(live_app):
    live_app._open_settings()
    live_app.root.update()
    return live_app.settings_win


def settle(app, done=None, timeout=10.0):
    """Runs the event loop until `done()` holds (default: no warming step left), with a generous time limit.

    Waits for the state, not for a number of rounds: on a loaded machine a fixed count was sometimes too short.
    """
    settings = app.settings_win

    def finished():
        return settings._warm_job is None and not settings._warm_queue and settings._warm_current is None

    done = done or finished
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.root.update()
        if done():
            return True
        time.sleep(0.005)
    return False


def laid_out(settings, app):
    """The settings window has its final width, so the pages are placed at the width they will be shown at."""
    return settle(app, lambda: settings.scroll.body.winfo_width() > 1)


def one_warm_step(settings):
    """Runs exactly one warming step by hand: the timer is taken off, so the step count does not depend on timing."""
    if settings._warm_job is not None:
        settings.win.after_cancel(settings._warm_job)
        settings._warm_job = None
    settings._warm_step()
    if settings._warm_job is not None:
        settings.win.after_cancel(settings._warm_job)
        settings._warm_job = None


class TestWarmPages:
    def test_pad_pair_reads_every_form_tk_reports(self):
        assert _pad_pair(0) == (0, 0)
        assert _pad_pair(7) == (7, 7)
        assert _pad_pair((3, 9)) == (3, 9)
        assert _pad_pair("4 12") == (4, 12)
        assert _pad_pair("") == (0, 0)

    def test_hidden_pages_are_laid_out_at_the_shown_width(self, settings, live_app, monkeypatch):
        monkeypatch.setattr(settings, "WARM_STEP_MS", 1)
        assert laid_out(settings, live_app)
        settings.warm_pages()
        assert settle(live_app)
        width = settings.scroll.body.winfo_width()
        for index in range(1, len(settings._pages)):
            for widget, info in settings._page_widgets(index):
                left, right = _pad_pair(info.get("padx", 0))
                assert widget.winfo_width() == width - left - right, (index, widget)
                assert widget.winfo_manager() == "", "taken away again after its step"
                assert widget.hidden_page

    def test_warming_does_not_change_the_scroll_height(self, settings, live_app, monkeypatch):
        assert laid_out(settings, live_app)
        assert settle(live_app)
        before = settings.scroll.body.winfo_reqheight()
        settings.warm_pages()
        one_warm_step(settings)
        live_app.root.update_idletasks()
        assert settings._warm_current is not None, "a page is placed during its step"
        assert settings.scroll.body.winfo_reqheight() == before
        settings._stop_warming()

    def test_selecting_the_page_being_warmed_shows_it(self, settings, live_app):
        assert laid_out(settings, live_app)
        settings.warm_pages()
        one_warm_step(settings)
        index = settings._warm_current
        assert index is not None
        settings.select_page(index)
        settings._warm_step()
        widgets = settings._page_widgets(index)
        assert all(widget.winfo_manager() == "pack" for widget, _info in widgets)
        assert not any(widget.hidden_page for widget, _info in widgets)

    def test_closing_stops_warming(self, settings, live_app):
        settings.warm_pages()
        live_app.root.update()
        settings.close()
        assert settings._warm_job is None and settings._warm_queue == []
        assert all(widget.winfo_manager() != "place" for _t, widgets in settings._pages for widget, _info in widgets)

    def test_a_resize_lays_the_hidden_pages_out_again(self, settings, monkeypatch):
        calls = []
        monkeypatch.setattr(settings, "warm_pages", lambda: calls.append(1))
        settings._remember_size(settings.win.winfo_width(), settings.win.winfo_height())
        assert calls == [1]


@pytest.mark.skipif(sys.platform != "win32", reason="layered windows are a Windows matter")
class TestLayered:
    def test_the_prebuilt_settings_are_no_layered_window(self, live_app, monkeypatch):
        live_app.prebuild_settings()
        win = live_app.settings_win
        assert win.win.attributes("-alpha") == 0.0
        win._end_hidden_layout()
        assert not uk.is_layered(win.win)
        assert float(win.win.attributes("-alpha")) == 1.0

    def test_clear_layered_leaves_a_transparent_window_alone(self, window, root):
        window.attributes("-alpha", 0.5)
        root.update()
        assert not uk.clear_layered(window)
        assert uk.is_layered(window)
        window.attributes("-alpha", 1.0)
        root.update()
        uk.clear_layered(window)
        assert not uk.is_layered(window)


def test_clear_layered_is_a_windows_matter(window, monkeypatch):
    monkeypatch.setattr(window_mod.sys, "platform", "linux")
    assert not uk.clear_layered(window)
    assert not uk.is_layered(window)


# ---------------------------------------------------------------- DPI per window


class TestDpiPerWindow:
    def test_the_settings_on_another_monitor_rescale_the_app(self, settings, live_app, monkeypatch):
        applied = []
        base = uk.Scale.dpi
        dpis = {str(live_app.root): base, str(settings.win): base + 48}
        monkeypatch.setattr(uk, "window_dpi", lambda win: dpis[str(win)])
        monkeypatch.setattr(uk.Scale, "apply", lambda _root, dpi: applied.append(dpi))
        assert live_app.check_dpi(settings.win)
        assert applied == [base + 48]

    def test_the_main_window_on_its_old_monitor_does_not_switch_back(self, settings, live_app, monkeypatch):
        base = uk.Scale.dpi
        dpis = {str(live_app.root): base, str(settings.win): base}
        monkeypatch.setattr(uk, "window_dpi", lambda win: dpis[str(win)])
        live_app.check_dpi(live_app.root)
        live_app.check_dpi(settings.win)
        applied = []

        def apply(_root, dpi):
            applied.append(dpi)
            uk.Scale.dpi = dpi

        monkeypatch.setattr(uk.Scale, "apply", apply)
        monkeypatch.setattr(uk.Scale, "dpi", base)
        dpis[str(settings.win)] = base + 48
        assert live_app.check_dpi(settings.win)
        assert not live_app.check_dpi(live_app.root), "the main window did not move"
        dpis[str(settings.win)] = base
        assert live_app.check_dpi(settings.win), "back on the first monitor"
        assert applied == [base + 48, base]

    def test_the_settings_watch_their_own_configure(self, settings, live_app, monkeypatch):
        seen = []
        monkeypatch.setattr(live_app, "check_dpi", lambda win: seen.append(win))
        settings.win.event_generate("<Configure>")
        live_app.root.update()
        assert settings.win in seen


# ---------------------------------------------------------------- the painted head


class TestStage:
    def test_the_stage_is_free_area(self, window):
        stage = Stage(window, "Einstellungen", lambda: None, close_tooltip="x")
        assert window_mod.is_free_area(stage)
        stage.destroy()

    def test_wash_and_line_keep_clear_of_title_and_cross(self, window, root, monkeypatch):
        monkeypatch.setattr(uk, "system_frame", lambda: False)
        stage = Stage(window, "Einstellungen", lambda: None, close_tooltip="x")
        stage.pack(fill="x")
        root.update()
        width = stage.winfo_width()
        left, right = stage.free_areas(width)
        title_end = stage.bbox("title")[2]
        assert left >= title_end + uk.px(Stage.TITLE_GAP) - 2  # the canvas box is a pixel wider than the text
        assert right <= width - uk.px(uk.Space.WINDOW) - uk.px(uk.IconButton.SIZE)
        stage.destroy()

    def test_the_cross_sits_with_its_face_on_the_middle(self, window, root, monkeypatch):
        monkeypatch.setattr(uk, "system_frame", lambda: False)
        stage = Stage(window, "Einstellungen", lambda: None, close_tooltip="x")
        stage.pack(fill="x")
        root.update()
        button = stage.close_button
        face_middle = button.winfo_y() + uk.px(uk.IconButton.SIZE) / 2
        assert abs(face_middle - stage.winfo_height() / 2) <= 1
        stage.destroy()

    def test_without_own_frame_there_is_no_cross(self, window, root, monkeypatch):
        monkeypatch.setattr(uk, "system_frame", lambda: True)
        stage = Stage(window, "Einstellungen", lambda: None, close_tooltip="x")
        stage.pack(fill="x")
        root.update()
        assert not stage.close_button.winfo_ismapped()
        stage.destroy()

    def test_a_new_title_repaints(self, window, root):
        stage = Stage(window, "Eins", lambda: None, close_tooltip="x")
        stage.pack(fill="x")
        root.update()
        stage.set_title("Zwei")
        assert stage.title == "Zwei"
        assert stage.itemcget("title", "text") == "Zwei"
        stage.destroy()

    def test_it_follows_theme_and_scale(self, window, root):
        stage = Stage(window, "Eins", lambda: None, close_tooltip="x")
        stage.pack(fill="x")
        root.update()
        stage._on_theme_change()
        stage._on_scale_change(1.0)
        assert stage.cget("bg") == uk.Theme.WINDOW_BG
        stage.destroy()
        assert stage._on_scale_change not in uk.Scale._callbacks


class TestBannerAreas:
    def deviation(self, image, x0, x1):
        paper = np.array([int(uk.Theme.WINDOW_BG[i : i + 2], 16) for i in (1, 3, 5)], np.float32)
        part = np.asarray(image, np.float32)[:, x0:x1]
        return float(np.abs(part - paper).mean())

    def test_both_ends_fade_into_the_paper(self):
        image = banner_mod.render_banner(300, 76)
        assert self.deviation(image, 0, 3) < 1.5, "the start towards the title is paper"
        assert self.deviation(image, 297, 300) < 1.5, "the end at the cross is paper"
        assert self.deviation(image, 100, 200) > 4, "the voice is there in between"

    def test_a_pause_between_two_words_shows_only_the_zero_line(self):
        image = banner_mod.render_banner(400, 76)
        word, pause = (banner_mod.WORDS[1][0] + banner_mod.WORDS[1][1]) / 2, banner_mod.WORDS[0][1] + 0.025
        assert self.deviation(image, int(word * 400) - 3, int(word * 400) + 3) > 2 * self.deviation(
            image, int(pause * 400) - 2, int(pause * 400) + 2
        )

    def test_the_swings_are_symmetric_around_the_middle(self):
        image = np.asarray(banner_mod.render_banner(400, 76), np.float32)
        paper = np.array([int(uk.Theme.WINDOW_BG[i : i + 2], 16) for i in (1, 3, 5)], np.float32)
        ink = np.abs(image - paper).sum(axis=2).sum(axis=1)
        upper, lower = ink[: 76 // 2].sum(), ink[76 // 2 + 1 :].sum()
        assert abs(upper - lower) / max(upper, lower) < 0.2

    def test_too_short_a_stretch_is_plain_paper(self):
        image = banner_mod.render_banner(40, 76)
        assert self.deviation(image, 0, 40) == 0

    def test_the_width_comes_in_steps_and_is_capped(self):
        step = banner_mod.STEP
        assert banner_mod.track_width(banner_mod.MIN_LINE - 1, 1.0) == 0
        assert banner_mod.track_width(10 * step + 5, 1.0) == 10 * step
        assert banner_mod.track_width(10 * step + 5, 1.0) == banner_mod.track_width(10 * step + 1, 1.0)
        assert banner_mod.track_width(5000, 1.0) <= banner_mod.MAX_LINE
        assert banner_mod.track_width(10 * 2 * step + 7, 2.0) == 10 * 2 * step

    def test_the_tk_image_is_cached_per_size(self, root):
        a = uk.banner_image(160, 76)
        assert uk.banner_image(160, 76) is a
        assert uk.banner_image(176, 76) is not a


class TestStageTrack:
    def test_the_track_stands_against_the_cross_inside_the_free_stretch(self, window, root, monkeypatch):
        monkeypatch.setattr(uk, "system_frame", lambda: False)
        window.geometry("480x200")
        stage = Stage(window, "Einstellungen", lambda: None, close_tooltip="x")
        stage.pack(fill="x")
        root.update()
        left, right = stage.free_areas(stage.winfo_width())
        x0, _y0, x1, _y1 = stage.bbox("track")
        assert x1 == right and x0 >= left
        stage.destroy()

    def test_a_small_resize_moves_the_track_without_a_new_picture(self, window, root, monkeypatch):
        monkeypatch.setattr(uk, "system_frame", lambda: False)
        window.geometry("480x200")
        stage = Stage(window, "Einstellungen", lambda: None, close_tooltip="x")
        stage.pack(fill="x")
        root.update()
        before = stage.itemcget("track", "image")
        left, right = stage.free_areas(stage.winfo_width())
        step = round(banner_mod.STEP * uk.Scale.factor)
        delta = 1 if (right - left) % step < step - 1 else -1  # stay inside the same step
        window.geometry(f"{480 + delta}x200")
        root.update()
        assert stage.itemcget("track", "image") == before
        assert stage.bbox("track")[2] == stage.free_areas(stage.winfo_width())[1], "moved with the cross"
        assert stage.coords("close")[0] == stage.winfo_width() - uk.px(uk.Space.WINDOW)
        stage.destroy()


# ---------------------------------------------------------------- the assistant


@pytest.fixture
def wizard(live_app, hams, monkeypatch, tmp_path):
    monkeypatch.setattr(sw, "TaskThread", InlineThread)
    monkeypatch.setattr(setup_mod, "CONFIG_PATH", tmp_path / "config.toml")
    win = live_app.setup_win = SetupWindow(live_app, fake_services())
    live_app.root.update()
    yield win
    if win.alive():
        win.close(confirmed=True)
    live_app.root.update()


class TestAssistant:
    def test_escape_closes(self, wizard, live_app):
        wizard.win.event_generate("<Escape>")
        live_app.root.update()
        assert not wizard.alive()

    def test_the_close_question_goes_when_the_download_ends(self, wizard, live_app):
        task = wizard.controller.pull_task
        task.begin()
        wizard.close()
        assert wizard._close_box is not None
        task.succeed()
        wizard._after_task_change()
        live_app.root.update()
        assert wizard._close_box is None
        assert wizard.next_button.winfo_manager() == "pack"

    def test_it_has_the_painted_head(self, wizard):
        assert isinstance(wizard.stage, Stage)
        assert wizard.close_button is wizard.stage.close_button

    def test_lines_mark_content_beyond_the_edges(self, wizard, live_app):
        wizard.win.geometry(f"{uk.px(wizard.WIDTH)}x{uk.px(360)}")
        live_app.root.update()
        if wizard.scroll.scrollable == 0:
            pytest.skip("the page fits even at this height")
        assert wizard.scroll.bottom_edge.cget("bg") == uk.Theme.HAIRLINE
        assert wizard.scroll.top_edge.cget("bg") == uk.Theme.WINDOW_BG
        wizard.scroll.scroll_to(wizard.scroll.scrollable)
        assert wizard.scroll.top_edge.cget("bg") == uk.Theme.HAIRLINE
        assert wizard.scroll.bottom_edge.cget("bg") == uk.Theme.WINDOW_BG

    def test_every_page_fits_its_window(self, wizard, live_app):
        """At its own size the assistant shows every page whole: nothing cut in the middle of a line."""
        for index in range(wizard.controller.count):
            wizard.controller.index = index
            wizard.show_page()
            live_app.root.update()
            assert wizard.scroll.scrollable == 0, f"page {wizard.controller.page} does not fit"

    def test_it_can_be_resized_and_the_text_follows(self, wizard, live_app):
        assert wizard.chrome is None or wizard.chrome.resizable
        before = wizard._text_width()
        wizard.win.geometry(f"{uk.px(wizard.WIDTH + 80)}x{uk.px(wizard.HEIGHT)}")
        live_app.root.update()
        assert wizard._text_width() > before
        assert all(int(label.cget("wraplength")) == wizard._text_width() for label in wizard._paragraphs)
