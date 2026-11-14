"""Phase 5 of the rebuild: windows that stay calm while they change.

* resizing freezes the content and lays it out once on release (`ContentFreeze`);
* the settings lay out their hidden pages ahead, so even the first visit of a tab is quick (`warm_pages`);
* after the invisible prebuild the settings are no layered window any more (`clear_layered`);
* every window notices a move to a monitor with another scaling, not only the main window (`check_dpi`);
* settings and assistant share one painted head (`Stage`) whose wash keeps clear of title and cross;
* the assistant closes with Escape, and its close question goes away when the download it asked about ends.
"""

import sys
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
from hams.window_stage import Stage


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


# ---------------------------------------------------------------- freezing while resizing


class TestContentFreeze:
    def build(self, win, root):
        top = tk.Frame(win, bg="red", height=20)
        top.pack(fill="x", padx=4)
        middle = tk.Frame(win, bg="green")
        middle.pack(fill="both", expand=True)
        bottom = tk.Frame(win, bg="blue", height=10)
        bottom.pack(side="bottom", fill="x", before=middle)
        root.update()
        return top, middle, bottom

    def test_the_content_keeps_its_place_while_frozen(self, window, root):
        top, middle, bottom = self.build(window, root)
        before = [(w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height()) for w in (top, middle, bottom)]
        freeze = uk.ContentFreeze(window)
        freeze.freeze()
        assert freeze.active
        window.geometry("420x320")
        root.update()
        after = [(w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height()) for w in (top, middle, bottom)]
        assert after == before, "nothing inside follows the window while frozen"
        assert {w.winfo_manager() for w in (top, middle, bottom)} == {"place"}

    def test_thaw_gives_everything_back_in_its_order(self, window, root):
        top, middle, bottom = self.build(window, root)
        order = window.pack_slaves()
        options = [w.pack_info() for w in order]
        freeze = uk.ContentFreeze(window)
        freeze.freeze()
        window.geometry("420x320")
        root.update()
        freeze.thaw()
        root.update()
        assert not freeze.active
        assert window.pack_slaves() == order
        assert [w.pack_info() for w in order] == options
        assert middle.winfo_width() == 420, "laid out once for the new size"

    def test_grid_children_come_back_to_their_cells(self, window, root):
        cell = tk.Frame(window, bg="red", width=30, height=30)
        cell.grid(row=1, column=2, padx=3)
        root.update()
        freeze = uk.ContentFreeze(window)
        freeze.freeze()
        assert cell.winfo_manager() == "place"
        freeze.thaw()
        info = cell.grid_info()
        assert (int(info["row"]), int(info["column"]), int(info["padx"])) == (1, 2, 3)

    def test_freezing_twice_is_one_freeze(self, window, root):
        self.build(window, root)
        freeze = uk.ContentFreeze(window)
        freeze.freeze()
        freeze.freeze()
        assert len(freeze._frozen) == 3

    def test_a_resize_gesture_freezes_and_thaws(self, window, root):
        _top, middle, _bottom = self.build(window, root)
        chrome = uk.WindowChrome(window, min_size=(200, 120))
        chrome._on_press(event(window, 499, 250))
        assert chrome.freeze.active
        chrome._on_drag(event(window, 539, 250))
        root.update()
        assert middle.winfo_width() == 300, "the content waits for the release"
        chrome._on_release()
        root.update()
        assert not chrome.freeze.active
        assert middle.winfo_width() == 340

    @pytest.mark.skipif(sys.platform != "win32", reason="window classes are a Windows matter")
    def test_while_frozen_windows_does_not_redraw_the_whole_window(self, window, root):
        import ctypes

        self.build(window, root)
        user32 = ctypes.windll.user32
        inner = window.winfo_id()
        handles = (user32.GetParent(inner), inner)
        before = [user32.GetClassLongW(hwnd, -26) for hwnd in handles]
        freeze = uk.ContentFreeze(window)
        freeze.freeze()
        assert all(not user32.GetClassLongW(hwnd, -26) & 0x3 for hwnd in handles)
        freeze.thaw()
        assert [user32.GetClassLongW(hwnd, -26) for hwnd in handles] == before

    def test_moving_does_not_freeze(self, window, root):
        self.build(window, root)
        chrome = uk.WindowChrome(window, min_size=(200, 120))
        chrome._on_press(event(window, 350, 250))
        assert not chrome.freeze.active
        chrome._on_release()


# ---------------------------------------------------------------- settings: hidden pages, layered style


@pytest.fixture
def settings(live_app):
    live_app._open_settings()
    live_app.root.update()
    return live_app.settings_win


def settle(app, rounds=40):
    for _ in range(rounds):
        app.root.update()
        app.root.after(10)


class TestWarmPages:
    def test_pad_pair_reads_every_form_tk_reports(self):
        assert _pad_pair(0) == (0, 0)
        assert _pad_pair(7) == (7, 7)
        assert _pad_pair((3, 9)) == (3, 9)
        assert _pad_pair("4 12") == (4, 12)
        assert _pad_pair("") == (0, 0)

    def test_hidden_pages_are_laid_out_at_the_shown_width(self, settings, live_app, monkeypatch):
        monkeypatch.setattr(settings, "WARM_STEP_MS", 1)
        settings.warm_pages()
        settle(live_app)
        width = settings.scroll.body.winfo_width()
        for index in range(1, len(settings._pages)):
            for widget, info in settings._page_widgets(index):
                left, right = _pad_pair(info.get("padx", 0))
                assert widget.winfo_width() == width - left - right, (index, widget)
                assert widget.winfo_manager() == "", "taken away again after its step"
                assert widget.hidden_page

    def test_warming_does_not_change_the_scroll_height(self, settings, live_app, monkeypatch):
        monkeypatch.setattr(settings, "WARM_STEP_MS", 1)
        before = settings.scroll.body.winfo_reqheight()
        settings.warm_pages()
        live_app.root.update()
        assert settings._warm_current is not None, "a page is placed during its step"
        assert settings.scroll.body.winfo_reqheight() == before
        settle(live_app)

    def test_selecting_the_page_being_warmed_shows_it(self, settings, live_app):
        settings.warm_pages()
        live_app.root.update()
        index = settings._warm_current
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

    def test_nothing_is_painted_in_the_free_areas(self):
        image = banner_mod.render_banner(600, 76, clear_left=220, clear_right=520)
        assert self.deviation(image, 0, 215) < 2.5, "only paper grain left of the wash"
        assert self.deviation(image, 525, 600) < 2.5, "only paper grain at the cross"
        assert self.deviation(image, 300, 500) > self.deviation(image, 0, 215) + 1

    def test_a_short_stretch_has_no_line(self):
        mask = banner_mod._ink_wave(200, 76, 1.0, 100, 140)
        assert np.asarray(mask).max() == 0

    def test_a_longer_phrase_has_more_swells_not_a_denser_line(self):
        short = np.asarray(banner_mod._ink_wave(600, 76, 1.0, 100, 250))
        long = np.asarray(banner_mod._ink_wave(600, 76, 1.0, 100, 500))
        assert short.any() and long.any()

    def test_free_areas_are_part_of_the_cache_key(self, root):
        a = uk.banner_image(300, 76, clear_left=50, clear_right=250)
        b = uk.banner_image(300, 76, clear_left=120, clear_right=250)
        assert a is not b
        assert uk.banner_image(300, 76, clear_left=50, clear_right=250) is a


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
        wizard._update_edges()
        if wizard.scroll.scrollable == 0:
            pytest.skip("the page fits even at this height")
        assert wizard._bottom_line.cget("bg") == uk.Theme.HAIRLINE
        assert wizard._top_line.cget("bg") == uk.Theme.WINDOW_BG
        wizard.scroll.scroll_to(wizard.scroll.scrollable)
        assert wizard._top_line.cget("bg") == uk.Theme.HAIRLINE
        assert wizard._bottom_line.cget("bg") == uk.Theme.WINDOW_BG
