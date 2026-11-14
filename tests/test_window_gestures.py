"""Moving and resizing the borderless windows (`ui_kit.window.WindowChrome`).

One rule for every window: free area (window background, no control) moves the window, the edge of a resizable
window resizes it. The gestures are driven with the event fields Tk would deliver (`widget`, `x_root`, `y_root`);
The window always moves itself: handing the drag to the Windows move loop crashed Hams (see `ui_kit.window`).
"""

import tkinter as tk
from types import SimpleNamespace

import kit as uk
import pytest

from hams.config import CFG
from hams.ui_kit import window as window_mod


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.configure(bg=uk.Theme.WINDOW_BG)
    win.geometry("300x200+200+150")
    root.update()
    yield win
    win.destroy()


def event(widget, x, y):
    return SimpleNamespace(widget=widget, x_root=x, y_root=y)


def gesture(chrome, widget, start, end, root):
    chrome._on_press(event(widget, *start))
    chrome._on_drag(event(widget, *end))
    chrome._on_release()
    root.update()


def frame(win):
    return win.winfo_x(), win.winfo_y(), win.winfo_width(), win.winfo_height()


class TestFreeArea:
    def test_window_background_is_free(self, window):
        label = tk.Label(window, text="Titel", bg=uk.Theme.WINDOW_BG)
        assert window_mod.is_free_area(window)
        assert window_mod.is_free_area(label)

    def test_controls_and_surfaces_are_not(self, window):
        button = uk.SecondaryButton(window, "OK", lambda: None, bg_under=uk.Theme.WINDOW_BG)
        card = tk.Frame(window, bg=uk.Theme.GROUP_SURFACE)
        entry = tk.Entry(window, bg=uk.Theme.WINDOW_BG)
        link = tk.Label(window, text="Link", bg=uk.Theme.WINDOW_BG, cursor="hand2")
        for widget in (button, card, entry, link):
            assert not window_mod.is_free_area(widget), widget

    def test_a_disabled_button_does_not_move_the_window_either(self, window):
        button = uk.SecondaryButton(window, "OK", lambda: None, bg_under=uk.Theme.WINDOW_BG)
        button.set_enabled(False)
        assert not window_mod.is_free_area(button)


class TestMoving:
    def test_dragging_free_area_moves_and_reports_once(self, window, root):
        moved = []
        chrome = uk.WindowChrome(window, on_moved=lambda x, y: moved.append((x, y)))
        gesture(chrome, window, (250, 170), (290, 200), root)
        assert frame(window)[:2] == (240, 180)
        assert moved == [(240, 180)]

    def test_dragging_a_control_does_not_move(self, window, root):
        button = uk.SecondaryButton(window, "OK", lambda: None, bg_under=uk.Theme.WINDOW_BG)
        chrome = uk.WindowChrome(window)
        gesture(chrome, button, (250, 170), (290, 200), root)
        assert frame(window)[:2] == (200, 150)

    def test_the_drag_never_goes_through_the_windows_move_loop(self):
        assert not hasattr(window_mod, "native_move")


class TestResizing:
    @pytest.fixture
    def chrome(self, window):
        sizes = []
        chrome = uk.WindowChrome(window, min_size=(200, 120), on_resized=lambda w, h: sizes.append((w, h)))
        chrome.sizes = sizes
        return chrome

    def test_edges_and_corners(self, chrome):
        x, y, w, h = 200, 150, 300, 200
        assert chrome.edge_at(x + 1, y + 100) == "w"
        assert chrome.edge_at(x + w - 1, y + 100) == "e"
        assert chrome.edge_at(x + 150, y + 1) == "n"
        assert chrome.edge_at(x + 150, y + h - 1) == "s"
        assert chrome.edge_at(x + w - 1, y + h - 1) == "se"
        assert chrome.edge_at(x + 1, y + 1) == "nw"
        assert chrome.edge_at(x + 8, y + h - 2) == "sw", "corners reach twice as far along the edge"
        assert chrome.edge_at(x + 150, y + 100) == ""
        assert chrome.edge_at(x - 5, y + 100) == "", "outside the window"

    def test_a_fixed_window_has_no_edges(self, window):
        assert uk.WindowChrome(window).edge_at(201, 250) == ""

    def test_dragging_the_bottom_right_corner(self, chrome, window, root):
        gesture(chrome, window, (499, 349), (559, 399), root)
        assert frame(window) == (200, 150, 360, 250)
        assert chrome.sizes == [(360, 250)]

    def test_dragging_the_left_edge_keeps_the_right_edge(self, chrome, window, root):
        gesture(chrome, window, (201, 250), (181, 250), root)
        assert frame(window) == (180, 150, 320, 200)

    def test_never_smaller_than_the_minimum(self, chrome, window, root):
        gesture(chrome, window, (201, 151), (450, 330), root)
        assert frame(window) == (300, 230, 200, 120)

    def test_the_edge_shows_a_resize_cursor_and_gives_it_back(self, chrome, window):
        label = tk.Label(window, bg=uk.Theme.WINDOW_BG, cursor="")
        chrome._on_motion(event(label, 499, 250))
        assert label.cget("cursor") == "sb_h_double_arrow"
        chrome._on_motion(event(label, 350, 250))
        assert label.cget("cursor") == ""

    def test_one_layout_per_idle_round(self, chrome, window, root):
        """Dragging sends more events than Tk can lay out; only the last position of a round counts."""
        chrome._on_press(event(window, 499, 250))
        for x in range(500, 540, 5):
            chrome._on_drag(event(window, x, 250))
        assert frame(window)[2] == 300, "nothing applied before the idle round"
        root.update()
        assert frame(window)[2] == 336
        chrome._on_release()


class TestSettingsWindowSize:
    @pytest.fixture
    def settings(self, live_app, monkeypatch):
        monkeypatch.setattr(uk, "system_frame", lambda: False)
        monkeypatch.setattr(window_mod, "system_frame", lambda: False)
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win

    def test_it_is_resizable_and_remembers_the_size(self, settings, hams):
        assert settings.chrome is not None and settings.chrome.resizable
        factor = uk.Scale.factor
        settings._remember_size(round(600 * factor), round(820 * factor))
        assert (CFG.settings_width, CFG.settings_height) == (600, 820)
        assert ("ui", "settings_width", 600) in hams.saved
        assert ("ui", "settings_height", 820) in hams.saved

    def test_the_content_follows_the_window(self, settings, live_app):
        win = settings.win
        before = settings.scroll.winfo_width()
        win.geometry(f"{win.winfo_width() + 200}x{win.winfo_height()}")
        live_app.root.update()
        assert settings.scroll.winfo_width() == before + 200


class TestPlates:
    def test_the_rim_of_a_plate_does_not_move_the_window(self, window):
        plate = uk.GroupSurface(window, bg_under=uk.Theme.WINDOW_BG)
        assert not window_mod.is_free_area(plate)
