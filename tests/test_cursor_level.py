"""The level at the mouse pointer (hams/cursor_level.py): placement, showing and hiding, the switch, the window.

Most tests drive the overlay through a fake desktop that records what would happen on screen; one class builds
the real window with the real user32 calls (Windows only, on the invisible test desktop like every Tk test).
"""

import logging
import sys

import pytest

from hams import config as config_mod
from hams import cursor_level as cl
from hams.config import CFG, Config, _load_config
from hams.ui_kit import Theme

AREA = (0, 0, 1920, 1040)


class FakeDesktop:
    def __init__(self, cursor=(800, 500), area=AREA, factor=1.0):
        self.position = cursor
        self.area = area
        self.factor = factor
        self.calls: list = []
        self.prepared: list = []
        self.visible = False

    def cursor(self):
        return self.position

    def monitor(self, cursor):
        return 1, self.area, self.factor

    def prepare(self, hwnd):
        self.prepared.append(hwnd)

    def show(self, hwnd, x, y):
        self.visible = True
        self.calls.append(("show", x, y))

    def move(self, hwnd, x, y):
        self.calls.append(("move", x, y))

    def hide(self, hwnd):
        self.visible = False
        self.calls.append(("hide",))

    def paint(self, hwnd, image):
        self.painted = image
        return True


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr(CFG, "cursor_level", True)


@pytest.fixture
def overlay(root, switch_on):
    desktop = FakeDesktop()
    level = cl.CursorLevel(root, desktop, supported=True)
    yield level
    level.close()


def frame(level):
    """One tick of the follow timer, run by hand."""
    level._cancel()
    level._frame()


# --------------------------------------------------------------------------- placement


class TestPlace:
    SIZE = (44, 24)

    def test_above_and_left_of_the_pointer(self):
        x, y = cl.place((800, 500), self.SIZE, 14, 26, AREA)
        assert (x, y) == (800 - 14 - 44, 500 - 14 - 24)
        assert x + self.SIZE[0] < 800 and y + self.SIZE[1] < 500, "the pill never covers the hot spot"

    def test_at_the_left_edge_it_moves_right_of_the_pointer(self):
        x, y = cl.place((20, 500), self.SIZE, 14, 26, AREA)
        assert x == 20 + 14 and y == 500 - 14 - 24

    def test_at_the_top_edge_it_moves_below_the_arrow(self):
        x, y = cl.place((800, 10), self.SIZE, 14, 26, AREA)
        assert x == 800 - 14 - 44
        assert y >= 10 + 26, "below the arrow, not on it"

    def test_in_the_top_left_corner_it_keeps_clear_of_the_arrow(self):
        x, y = cl.place((5, 5), self.SIZE, 14, 26, AREA)
        assert x > 5 and y >= 5 + 26

    def test_it_never_leaves_the_monitor(self):
        for cursor in [(1919, 1039), (1919, 5), (0, 1039), (960, 1039)]:
            x, y = cl.place(cursor, self.SIZE, 14, 26, AREA)
            assert 0 <= x <= 1920 - 44 and 0 <= y <= 1040 - 24, cursor

    def test_a_monitor_left_of_the_primary_one(self):
        """Negative coordinates: the second screen to the left."""
        area = (-1920, 0, 0, 1040)
        assert cl.place((-1000, 500), self.SIZE, 14, 26, area) == (-1000 - 58, 500 - 38)
        x, _y = cl.place((-1915, 500), self.SIZE, 14, 26, area)
        assert x == -1915 + 14


class TestGeometry:
    def test_sizes_follow_the_monitor_scale(self):
        normal, large = cl.Geometry.for_factor(1.0), cl.Geometry.for_factor(1.5)
        assert (normal.width, normal.height, normal.gap) == (cl.PILL_WIDTH, cl.PILL_HEIGHT, cl.GAP)
        assert (large.width, large.height, large.gap) == (84, 30, 21)

    def test_the_bars_sit_centred_in_the_pill(self):
        geometry = cl.Geometry.for_factor(1.0)
        left = geometry.bar_left(0)
        right = geometry.width - (geometry.bar_left(cl.BAR_COUNT - 1) + geometry.bar_width)
        assert abs(left - right) <= 1

    def test_silence_shows_dots_and_a_loud_signal_the_tallest_middle(self):
        geometry = cl.Geometry.for_factor(1.0)
        assert cl.bar_targets(0.0, 3.0, geometry) == [geometry.bar_min] * cl.BAR_COUNT
        for now in (0.0, 0.4, 1.3):
            loud = cl.bar_targets(1.0, now, geometry)
            assert all(geometry.bar_min <= h <= geometry.bar_max for h in loud)
        assert cl.bar_targets(5.0, 0.0, geometry) == cl.bar_targets(1.0, 0.0, geometry)  # clamped

    @pytest.mark.parametrize("size,border", [((56, 20), 1.0), ((84, 30), 1.5), ((3, 12), 0.0)])
    def test_the_pill_is_a_clean_stadium(self, size, border):
        """No square corner tips: transparent corners, symmetric on both axes, the round ends smooth."""
        width, height = size
        image = cl.stadium(width, height, "#FFFCF5", border="#8E877F" if border else None, border_w=border)
        alpha = image.getchannel("A")
        for x in range(width):
            for y in range(height):
                value = alpha.getpixel((x, y))
                assert value == alpha.getpixel((width - 1 - x, y)) == alpha.getpixel((x, height - 1 - y))
        if width > height:
            assert alpha.getpixel((0, 0)) == 0 and alpha.getpixel((1, 1)) == 0
            assert alpha.getpixel((width // 2, 0)) == 255 and alpha.getpixel((0, height // 2)) == 255
            assert 0 < alpha.getpixel((height // 2 - 3, 0)) < 255, "the curve is anti-aliased"

    def test_the_bitmap_is_premultiplied_bgra_top_down(self):
        from PIL import Image

        image = Image.new("RGBA", (2, 1), (200, 100, 50, 128))
        image.putpixel((1, 0), (10, 20, 30, 0))
        data = cl.premultiplied_bgra(image)
        assert len(data) == 8
        blue, green, red, alpha = data[:4]
        assert alpha == 128 and abs(blue - 25) <= 1 and abs(green - 50) <= 1 and abs(red - 100) <= 1
        assert data[4:] == bytes(4), "a transparent pixel carries no colour"


# --------------------------------------------------------------------------- showing and hiding


class TestStates:
    def test_a_recording_shows_it_above_left_of_the_pointer(self, overlay):
        overlay.follow_state("recording")
        assert overlay.shown
        geometry = overlay._geometry
        expected = ("show", 800 - geometry.gap - geometry.width, 500 - geometry.gap - geometry.height)
        assert overlay._desktop.calls[-1] == expected
        assert overlay._job is not None

    @pytest.mark.parametrize("state", ["ready", "transcribing", "cleaning", "generating", "done", "error", "no_signal"])
    def test_every_other_state_hides_it_at_once(self, overlay, state):
        overlay.follow_state("recording")
        overlay.set_level(0.8)
        overlay.follow_state(state)
        assert not overlay.shown and not overlay._desktop.visible
        assert overlay._job is None, "nothing runs while hidden"
        assert overlay.level == 0.0

    def test_dictation_is_turquoise_and_ask_ai_in_the_ai_colour(self, overlay):
        overlay.follow_state("recording")
        assert overlay._bar_colour == Theme.ACCENT_TINT
        overlay.follow_state("recording_prompt")
        assert overlay._bar_colour == Theme.AI

    def test_switched_off_it_never_shows(self, overlay, monkeypatch):
        monkeypatch.setattr(CFG, "cursor_level", False)
        overlay.follow_state("recording")
        overlay.set_level(1.0)
        assert not overlay.shown
        assert overlay.win is None, "not even the window is built"
        assert overlay._desktop.calls == []

    def test_the_switch_applies_during_a_recording(self, overlay, monkeypatch):
        overlay.follow_state("recording")
        monkeypatch.setattr(CFG, "cursor_level", False)
        overlay.refresh()
        assert not overlay.shown and overlay._desktop.calls[-1] == ("hide",)
        monkeypatch.setattr(CFG, "cursor_level", True)
        overlay.refresh()
        assert overlay.shown and overlay._desktop.calls[-1][0] == "show"

    def test_a_running_timer_notices_the_switch_too(self, overlay, monkeypatch):
        overlay.follow_state("recording")
        monkeypatch.setattr(CFG, "cursor_level", False)
        frame(overlay)
        assert not overlay.shown and overlay._job is None

    def test_off_linux_or_any_unsupported_system_it_never_shows(self, root, switch_on):
        desktop = FakeDesktop()
        level = cl.CursorLevel(root, desktop, supported=False)
        level.follow_state("recording")
        assert not level.shown and level.win is None and desktop.calls == []

    def test_after_quit_it_stays_away(self, overlay):
        overlay.follow_state("recording")
        overlay.close()
        assert overlay.win is None and not overlay.shown
        overlay.follow_state("recording")
        assert not overlay.shown


class TestFollowing:
    def test_it_moves_only_when_the_pointer_did(self, overlay):
        overlay.follow_state("recording")
        desktop = overlay._desktop
        calls = len(desktop.calls)
        for _ in range(5):
            frame(overlay)
        assert len(desktop.calls) == calls, "a still pointer moves nothing"
        desktop.position = (900, 600)
        frame(overlay)
        geometry = overlay._geometry
        assert desktop.calls[-1] == ("move", 900 - geometry.gap - geometry.width, 600 - geometry.gap - geometry.height)
        assert overlay.moves == 1

    def test_silence_redraws_nothing(self, overlay):
        overlay.follow_state("recording")
        for _ in range(30):
            frame(overlay)  # settles at the dots
        before = overlay.redraws
        for _ in range(10):
            frame(overlay)
        assert overlay.redraws == before

    def test_a_level_raises_the_bars(self, overlay):
        overlay.follow_state("recording")
        overlay.set_level(1.0)
        for _ in range(20):
            frame(overlay)
        geometry = overlay._geometry
        assert max(overlay._shown_heights) > geometry.bar_min + (geometry.bar_max - geometry.bar_min) // 2
        assert all(geometry.bar_min <= h <= geometry.bar_max for h in overlay._shown_heights)

    def test_another_monitor_scale_renders_the_pill_again(self, overlay):
        overlay.follow_state("recording")
        desktop = overlay._desktop
        desktop.area, desktop.factor = (1920, 0, 3840, 1040), 1.5
        desktop.position = (2500, 500)
        frame(overlay)
        assert overlay._geometry.factor == 1.5
        large = cl.Geometry.for_factor(1.5)
        assert overlay.frame_image.size == (large.width, large.height)
        assert overlay._desktop.painted is overlay.frame_image

    def test_without_a_pointer_position_it_keeps_the_last_one(self, overlay):
        overlay.follow_state("recording")
        overlay._desktop.position = None
        calls = len(overlay._desktop.calls)
        frame(overlay)
        assert len(overlay._desktop.calls) == calls
        overlay.follow_state("ready")
        overlay.follow_state("recording")  # shown at the last known place
        assert overlay._desktop.calls[-1][0] == "show"

    def test_the_window_is_built_once(self, overlay, root):
        overlay.follow_state("recording")
        window = overlay.win
        for _ in range(20):
            overlay.follow_state("ready")
            overlay.follow_state("recording_prompt")
            overlay.set_level(0.6)
            frame(overlay)
        assert overlay.win is window
        assert len(overlay._images) <= overlay._geometry.bar_max, "one picture per bar height at most"

    def test_a_new_appearance_is_picked_up_at_the_next_show(self, overlay):
        before = Theme.mode
        try:
            overlay.follow_state("recording")
            Theme.apply(Theme.DARK if before == Theme.LIGHT else Theme.LIGHT)
            overlay.follow_state("ready")
            overlay.follow_state("recording")
            paper = Theme.GROUP_SURFACE.lstrip("#")
            expected = tuple(int(paper[i : i + 2], 16) for i in (0, 2, 4))
            pixel = overlay.frame_image.getpixel((overlay._geometry.width // 2, 3))
            assert all(abs(a - b) <= 3 for a, b in zip(pixel[:3], expected, strict=True)), pixel
            assert overlay._bar_colour == Theme.ACCENT_TINT
        finally:
            Theme.apply(before)

    def test_the_counters_are_logged_when_hidden(self, overlay, caplog):
        overlay.follow_state("recording")
        frame(overlay)
        with caplog.at_level(logging.DEBUG, logger="hams"):
            overlay.follow_state("ready")
        assert any("Level at the pointer hidden" in r.getMessage() for r in caplog.records)
        assert overlay.counters()[0] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="the window styles are Win32")
class TestRealWindow:
    def test_click_through_never_focused_no_taskbar_button(self, root, switch_on):
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        desktop = cl.Win32Desktop()
        level = cl.CursorLevel(root, desktop)
        before = user32.GetForegroundWindow()
        try:
            level.follow_state("recording")
            root.update()
            assert user32.IsWindowVisible(level.hwnd)
            assert desktop.styles(level.hwnd) & cl.OVERLAY_STYLES == cl.OVERLAY_STYLES
            assert not desktop.styles(level.hwnd) & cl.WS_EX_APPWINDOW
            assert user32.GetForegroundWindow() == before, "showing must not take the focus"
            frame_image = level.frame_image
            assert frame_image.getpixel((0, 0))[3] == 0, "the corners are transparent, no colour key"
            assert frame_image.getpixel((frame_image.width // 2, frame_image.height // 2))[3] == 255
            assert desktop.paint(level.hwnd, frame_image), "UpdateLayeredWindow accepts the bitmap"
            frame(level)
            level.follow_state("error")
            root.update()
            assert not user32.IsWindowVisible(level.hwnd)
        finally:
            level.close()

    def test_the_real_monitor_lookup(self):
        desktop = cl.Win32Desktop()
        handle, (left, top, right, bottom), factor = desktop.monitor((10, 10))
        assert handle and right > left and bottom > top and factor > 0
        position = desktop.cursor()
        assert position is None or len(position) == 2


# --------------------------------------------------------------------------- setting


class TestSetting:
    def test_on_by_default(self):
        import tomllib

        template = tomllib.loads(config_mod.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        assert template["ui"]["cursor_level"] is True
        assert "integrations" not in template
        assert Config().cursor_level is True

    def _load(self, tmp_path, monkeypatch, user: str, template: str | None = None):
        user_file = tmp_path / "config.toml"
        user_file.write_text(user, encoding="utf-8")
        monkeypatch.setattr(config_mod, "CONFIG_PATH", user_file)
        if template is not None:
            template_file = tmp_path / "template.toml"
            template_file.write_text(template, encoding="utf-8")
            monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", template_file)
        return _load_config()

    def test_the_old_key_is_taken_over_quietly(self, tmp_path, monkeypatch, caplog):
        """`[integrations] cursor_dot = true` from 1.3.1 means the switch is on - without a warning."""
        with caplog.at_level(logging.WARNING, logger="hams"):
            cfg = self._load(
                tmp_path, monkeypatch, "[integrations]\ncursor_dot = true\n", template="[ui]\ncursor_level = false\n"
            )
        assert cfg.cursor_level is True
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_the_new_key_wins_over_the_old_one(self, tmp_path, monkeypatch):
        cfg = self._load(tmp_path, monkeypatch, "[ui]\ncursor_level = false\n\n[integrations]\ncursor_dot = true\n")
        assert cfg.cursor_level is False

    def test_the_old_default_false_does_not_switch_it_off(self, tmp_path, monkeypatch, caplog):
        with caplog.at_level(logging.WARNING, logger="hams"):
            cfg = self._load(tmp_path, monkeypatch, "[integrations]\ncursor_dot = false\n")
        assert cfg.cursor_level is True
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    @pytest.mark.parametrize("data", [{"integrations": "x"}, {"integrations": {"cursor_dot": "true"}}, {}])
    def test_anything_else_under_the_old_name_is_ignored(self, data):
        cfg = Config()
        cfg.cursor_level = False
        config_mod._take_over_cursor_dot(cfg, data)
        assert cfg.cursor_level is False
