"""The level at the mouse pointer (hams/cursor_level.py): placement, showing and hiding, the follower thread and
its rate, the switch, the window.

Most tests drive the overlay through a fake desktop that records what would happen on screen; the follower thread
runs for real and ticks when a test lets it. One class builds the real window with the real Win32 calls (Windows
only, on the invisible test desktop like every Tk test).
"""

import logging
import sys
import threading
import time

import pytest

from hams import config as config_mod
from hams import cursor_level as cl
from hams.config import CFG, Config, _load_config
from hams.ui_kit import Theme

AREA = (0, 0, 1920, 1040)


class FakeDesktop:
    """Records what would happen on screen. The follower thread runs for real, but each of its frames waits for
    `step`, so a test knows exactly how many ticks have passed."""

    def __init__(self, cursor=(800, 500), area=AREA, factor=1.0, hz=240):
        self.position = cursor
        self.area = area
        self.factor = factor
        self.handle = 1
        self.rates = {1: hz}
        self.covered = False
        self.fail: Exception | None = None
        self.calls: list = []
        self.periods: list[float] = []
        self.visible = False
        self.painted = None
        self.created = self.destroyed = 0
        self.owners: set[str] = set()
        self._gate = threading.Semaphore(0)
        self._waits = 0
        self._changed = threading.Condition()

    def cursor(self):
        if self.fail is not None:
            raise self.fail
        return self.position

    def monitor(self, cursor):
        return self.handle, self.area, self.factor

    def refresh_rate(self, handle):
        return self.rates.get(handle, 0)

    def fullscreen(self):
        return self.covered

    def create(self):
        self.owners.add(threading.current_thread().name)
        self.created += 1
        self.calls.append(("create",))
        return 100 + self.created

    def destroy(self, hwnd):
        self.destroyed += 1
        self.calls.append(("destroy",))

    def pump(self):
        self.owners.add(threading.current_thread().name)

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

    def wait_frame(self, previous, period, stop):
        self.periods.append(period)
        with self._changed:
            self._waits += 1
            self._changed.notify_all()
        while not stop.is_set():
            if self._gate.acquire(timeout=0.002):
                return

    def _wait_for(self, waits: int) -> None:
        with self._changed:
            assert self._changed.wait_for(lambda: self._waits >= waits, timeout=5), "the follower did not tick"

    def settle(self):
        """Until the follower has done its first tick and waits for the next."""
        self._wait_for(1)

    def step(self, count=1):
        """Lets the follower do `count` more ticks."""
        for _ in range(count):
            target = self._waits + 1
            self._gate.release()
            self._wait_for(target)


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr(CFG, "cursor_level", True)


@pytest.fixture
def overlay(root, switch_on):
    desktop = FakeDesktop()
    level = cl.CursorLevel(root, desktop, supported=True)
    yield level
    level.close()


def show(level, state="recording"):
    level.follow_state(state)
    level._desktop.settle()


def frame(level):
    """One tick of the Tk timer (the bars), run by hand."""
    level._cancel()
    level._frame()


def follower_threads():
    return [thread for thread in threading.enumerate() if thread.name == "cursor-level"]


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


def expected_place(level, cursor):
    geometry = level._geometry
    return cursor[0] - geometry.gap - geometry.width, cursor[1] - geometry.gap - geometry.height


class TestStates:
    def test_a_recording_shows_it_above_left_of_the_pointer(self, overlay):
        show(overlay)
        assert overlay.shown and overlay.follower.alive
        calls = overlay._desktop.calls
        assert calls[0] == ("create",)
        assert calls[-1] == ("show", *expected_place(overlay, (800, 500)))
        assert overlay._desktop.painted is overlay.frame_image, "the first frame is painted before it shows"
        assert overlay._job is not None

    def test_the_window_belongs_to_the_follower_thread(self, overlay):
        show(overlay)
        assert overlay._desktop.owners == {"cursor-level"}, "created and pumped by the follower, never by Tk"
        thread = follower_threads()[0]
        assert thread.daemon, "never keeps Hams from quitting"

    @pytest.mark.parametrize("state", ["ready", "transcribing", "cleaning", "generating", "done", "error", "no_signal"])
    def test_every_other_state_hides_it_at_once(self, overlay, state):
        show(overlay)
        overlay.set_level(0.8)
        overlay.follow_state(state)
        assert not overlay.shown and not overlay._desktop.visible
        assert overlay._desktop.calls[-2:] == [("hide",), ("destroy",)]
        assert overlay._job is None and overlay.follower is None, "nothing runs while hidden"
        assert follower_threads() == [], "the thread has ended"
        assert overlay.level == 0.0

    def test_dictation_is_turquoise_and_ask_ai_in_the_ai_colour(self, overlay):
        show(overlay)
        assert overlay._bar_colour == Theme.ACCENT_TINT
        overlay.follow_state("recording_prompt")
        assert overlay._bar_colour == Theme.AI
        assert overlay._desktop.created == 1, "the same window, only repainted"

    def test_switched_off_it_never_shows(self, overlay, monkeypatch):
        monkeypatch.setattr(CFG, "cursor_level", False)
        overlay.follow_state("recording")
        overlay.set_level(1.0)
        assert not overlay.shown
        assert overlay.follower is None, "not even the thread is started"
        assert overlay._desktop.calls == []

    def test_the_switch_applies_during_a_recording(self, overlay, monkeypatch):
        show(overlay)
        monkeypatch.setattr(CFG, "cursor_level", False)
        overlay.refresh()
        assert not overlay.shown and overlay._desktop.calls[-1] == ("destroy",)
        monkeypatch.setattr(CFG, "cursor_level", True)
        overlay.refresh()
        overlay._desktop._wait_for(2)
        assert overlay.shown and overlay._desktop.calls[-1][0] == "show"

    def test_a_running_timer_notices_the_switch_too(self, overlay, monkeypatch):
        show(overlay)
        monkeypatch.setattr(CFG, "cursor_level", False)
        frame(overlay)
        assert not overlay.shown and overlay._job is None and follower_threads() == []

    def test_off_linux_or_any_unsupported_system_it_never_shows(self, root, switch_on):
        desktop = FakeDesktop()
        level = cl.CursorLevel(root, desktop, supported=False)
        level.follow_state("recording")
        assert not level.shown and level.follower is None and desktop.calls == []

    def test_after_quit_it_stays_away(self, overlay):
        show(overlay)
        overlay.close()
        assert overlay.follower is None and not overlay.shown and follower_threads() == []
        overlay.follow_state("recording")
        assert not overlay.shown


class TestFollowing:
    def test_it_moves_only_when_the_pointer_did(self, overlay):
        show(overlay)
        desktop = overlay._desktop
        calls = len(desktop.calls)
        desktop.step(5)
        assert len(desktop.calls) == calls, "a still pointer moves nothing"
        desktop.position = (900, 600)
        desktop.step()
        assert desktop.calls[-1] == ("move", *expected_place(overlay, (900, 600)))
        assert overlay.moves == 1 and overlay.counters()[1] == 1

    def test_every_tick_follows_without_the_tk_timer(self, overlay):
        """The pill moves at the follower's rate, however seldom Tk gets to run."""
        show(overlay)
        desktop = overlay._desktop
        frames = overlay.frames
        for step in range(1, 11):
            desktop.position = (800 + step, 500)
            desktop.step()
        assert overlay.moves == 10 and overlay.frames == frames

    def test_silence_redraws_nothing(self, overlay):
        show(overlay)
        for _ in range(30):
            frame(overlay)  # settles at the dots
        before = overlay.redraws
        for _ in range(10):
            frame(overlay)
        assert overlay.redraws == before

    def test_a_level_raises_the_bars_and_the_follower_paints_them(self, overlay):
        show(overlay)
        overlay.set_level(1.0)
        for _ in range(20):
            frame(overlay)
        geometry = overlay._geometry
        assert max(overlay._shown_heights) > geometry.bar_min + (geometry.bar_max - geometry.bar_min) // 2
        assert all(geometry.bar_min <= h <= geometry.bar_max for h in overlay._shown_heights)
        overlay._desktop.step()
        assert overlay._desktop.painted is overlay.frame_image

    def test_another_monitor_scale_renders_the_pill_again(self, overlay):
        show(overlay)
        desktop = overlay._desktop
        desktop.area, desktop.factor, desktop.handle = (1920, 0, 3840, 1040), 1.5, 2
        desktop.rates[2] = 144
        desktop.position = (2500, 500)
        desktop.step()
        frame(overlay)
        assert overlay._geometry.factor == 1.5
        large = cl.Geometry.for_factor(1.5)
        assert overlay.frame_image.size == (large.width, large.height)
        desktop.step()
        assert desktop.painted is overlay.frame_image
        assert desktop.calls[-1] == ("move", 2500 - large.gap - large.width, 500 - large.gap - large.height)

    def test_without_a_pointer_position_it_keeps_the_last_one_and_slows_down(self, overlay):
        show(overlay)
        desktop = overlay._desktop
        desktop.position = None
        calls = len(desktop.calls)
        desktop.step()
        assert len(desktop.calls) == calls
        assert desktop.periods[-1] == cl.NO_POINTER_S, "no 240 ticks a second on the lock screen"
        overlay.follow_state("ready")
        waits = desktop._waits
        overlay.follow_state("recording")  # shown at the last known place
        desktop._wait_for(waits + 1)
        assert desktop.calls[-1] == ("show", *expected_place(overlay, (800, 500)))

    def test_a_window_per_show_and_none_left_after(self, overlay):
        desktop = overlay._desktop
        for _ in range(20):
            show(overlay)
            overlay.follow_state("recording_prompt")
            overlay.set_level(0.6)
            frame(overlay)
            overlay.follow_state("ready")
        assert desktop.created == desktop.destroyed == 20
        assert follower_threads() == []
        assert len(overlay._images) <= overlay._geometry.bar_max, "one picture per bar height at most"

    def test_a_new_appearance_is_picked_up_at_the_next_show(self, overlay):
        before = Theme.mode
        try:
            show(overlay)
            Theme.apply(Theme.DARK if before == Theme.LIGHT else Theme.LIGHT)
            overlay.follow_state("ready")
            show(overlay)
            paper = Theme.GROUP_SURFACE.lstrip("#")
            expected = tuple(int(paper[i : i + 2], 16) for i in (0, 2, 4))
            pixel = overlay.frame_image.getpixel((overlay._geometry.width // 2, 3))
            assert all(abs(a - b) <= 3 for a, b in zip(pixel[:3], expected, strict=True)), pixel
            assert overlay._bar_colour == Theme.ACCENT_TINT
        finally:
            Theme.apply(before)

    def test_the_counters_are_logged_when_hidden(self, overlay, caplog):
        show(overlay)
        frame(overlay)
        with caplog.at_level(logging.DEBUG, logger="hams"):
            overlay.follow_state("ready")
        assert any("Level at the pointer hidden" in r.getMessage() for r in caplog.records)
        assert overlay.counters()[0] == 1


# --------------------------------------------------------------------------- follow rate


class TestFollowRate:
    @pytest.mark.parametrize(
        "reported,hz",
        [(240, 240), (144, 144), (120, 120), (60, 60), (0, 60), (1, 60), (None, 60), (5, 24), (5000, 1000)],
    )
    def test_the_monitor_rate_and_the_fallback(self, reported, hz):
        assert cl.frame_rate(reported) == hz

    @pytest.mark.parametrize("hz", [240, 165, 144, 120])
    def test_it_follows_at_the_rate_of_the_monitor(self, overlay, hz):
        overlay._desktop.rates[1] = hz
        show(overlay)
        assert overlay.follower.hz == hz
        assert overlay._desktop.periods[-1] == pytest.approx(1 / hz)

    def test_a_monitor_without_a_rate_gets_60(self, overlay):
        overlay._desktop.rates[1] = 0
        show(overlay)
        assert overlay._desktop.periods[-1] == pytest.approx(1 / 60)

    def test_onto_another_monitor_it_takes_that_rate_at_once(self, overlay):
        show(overlay)
        desktop = overlay._desktop
        desktop.handle, desktop.area, desktop.rates[2] = 2, (1920, 0, 3840, 1040), 144
        desktop.position = (2500, 500)
        desktop.step()
        assert overlay.follower.hz == 144 and desktop.periods[-1] == pytest.approx(1 / 144)
        desktop.handle, desktop.area, desktop.position = 1, AREA, (800, 500)
        desktop.step()
        assert overlay.follower.hz == 240

    def test_a_new_display_mode_is_noticed(self, overlay, monkeypatch):
        monkeypatch.setattr(cl, "RATE_CHECK_S", 0.0)
        show(overlay)
        overlay._desktop.rates[1] = 120
        overlay._desktop.step()
        assert overlay._desktop.periods[-1] == pytest.approx(1 / 120)

    def test_over_a_full_screen_application_it_stays_hidden(self, overlay, monkeypatch, caplog):
        monkeypatch.setattr(cl, "FULLSCREEN_CHECK_S", 0.0)
        show(overlay)
        desktop = overlay._desktop
        with caplog.at_level(logging.INFO, logger="hams"):
            desktop.covered = True
            desktop.step()
            assert desktop.calls[-1] == ("hide",) and not desktop.visible
            desktop.position = (900, 600)
            desktop.step(3)
            assert desktop.calls[-1] == ("hide",), "no moves while hidden"
            desktop.covered = False
            desktop.step()
        assert desktop.calls[-1] == ("show", *expected_place(overlay, (900, 600)))
        messages = [r.getMessage() for r in caplog.records]
        assert "Level at the pointer paused: full-screen application" in messages
        assert "Level at the pointer resumed" in messages

    def test_a_game_already_in_front_never_sees_it(self, overlay):
        overlay._desktop.covered = True
        show(overlay)
        overlay._desktop.step(3)
        assert not overlay._desktop.visible
        assert not [call for call in overlay._desktop.calls if call[0] == "show"]

    def test_stop_ends_the_thread_at_once(self, overlay):
        show(overlay)
        began = time.perf_counter()
        overlay.follow_state("ready")
        assert time.perf_counter() - began < 0.5
        assert follower_threads() == []

    def test_an_error_in_the_thread_ends_it_cleanly(self, overlay, caplog):
        show(overlay)
        follower = overlay.follower
        with caplog.at_level(logging.ERROR, logger="hams"):
            overlay._desktop.fail = RuntimeError("broken")
            overlay._desktop._gate.release()
            follower._thread.join(5)
        assert not follower.alive
        assert overlay._desktop.calls[-2:] == [("hide",), ("destroy",)], "the window goes with it"
        assert any("following stopped" in r.getMessage() for r in caplog.records)
        frame(overlay)  # the Tk side carries on without complaint
        overlay.follow_state("ready")
        assert not overlay.shown

    def test_a_window_that_cannot_be_created_is_logged(self, overlay, caplog):
        def refuse():
            raise OSError("no window")

        overlay._desktop.create = refuse
        with caplog.at_level(logging.ERROR, logger="hams"):
            overlay.follow_state("recording")
            overlay.follower._thread.join(5)
        assert any("window not created" in r.getMessage() for r in caplog.records)
        overlay.follow_state("ready")
        assert overlay._desktop.destroyed == 0


class TestWaitFrame:
    """`Win32Desktop.wait_frame`: in step with the compositor, a timer where it does not block."""

    class Dwm:
        def __init__(self, block: float, result: int = 0):
            self.block, self.result, self.calls = block, result, 0

        def DwmFlush(self):  # noqa: N802 - the Win32 name
            self.calls += 1
            time.sleep(self.block)
            return self.result

    @pytest.fixture
    def desktop(self):
        if sys.platform != "win32":
            pytest.skip("Win32")
        return cl.Win32Desktop()

    def wait(self, desktop, period):
        began = time.perf_counter()
        desktop.wait_frame(began, period, threading.Event())
        return time.perf_counter() - began

    def test_a_compositor_at_the_monitor_rate_is_one_flush(self, desktop):
        desktop.dwmapi = self.Dwm(1 / 240)
        assert self.wait(desktop, 1 / 240) < 0.02
        assert desktop.dwmapi.calls == 1

    def test_a_faster_compositor_is_waited_out(self, desktop):
        desktop.dwmapi = self.Dwm(1 / 240)
        assert self.wait(desktop, 1 / 60) >= 0.75 / 60
        assert desktop.dwmapi.calls >= 3

    @pytest.mark.parametrize("dwm", [None, "error", "not blocking"])
    def test_without_a_blocking_flush_a_timer_waits(self, desktop, dwm):
        desktop.dwmapi = {None: None, "error": self.Dwm(0, result=-1), "not blocking": self.Dwm(0)}[dwm]
        elapsed = self.wait(desktop, 0.008)
        assert 0.006 <= elapsed < 0.05

    def test_a_stop_returns_at_once(self, desktop):
        desktop.dwmapi = self.Dwm(0.05)
        stop = threading.Event()
        stop.set()
        began = time.perf_counter()
        desktop.wait_frame(began, 0.1, stop)
        assert time.perf_counter() - began < 0.01 and desktop.dwmapi.calls == 0

    def test_the_real_compositor_paces_the_frames(self, desktop):
        """On the test desktop as on the real one, `DwmFlush` blocks for a frame (measured: 240 Hz on 240 Hz)."""
        hz = cl.frame_rate(desktop.refresh_rate(desktop.monitor((10, 10))[0]))
        stop = threading.Event()
        previous, gaps = time.perf_counter(), []
        for _ in range(30):
            desktop.wait_frame(previous, 1 / hz, stop)
            now = time.perf_counter()
            gaps.append(now - previous)
            previous = now
        gaps.sort()
        median = gaps[len(gaps) // 2]
        assert 0.5 / hz <= median <= 2.0 / hz, (hz, median)


@pytest.mark.skipif(sys.platform != "win32", reason="the window styles are Win32")
class TestRealWindow:
    class Desktop(cl.Win32Desktop):
        """Real calls; only the pointer is fixed (GetCursorPos fails on the invisible test desktop) and no
        full-screen application is in front (the test desktop has no shell and counts as busy)."""

        def cursor(self):
            return 800, 500

        def fullscreen(self):
            return False

    @staticmethod
    def until(condition, timeout=5.0):
        end = time.perf_counter() + timeout
        while not condition():
            assert time.perf_counter() < end, "timed out"
            time.sleep(0.005)

    def test_click_through_never_focused_no_taskbar_button(self, root, switch_on):
        import ctypes

        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.IsWindowVisible.argtypes = user32.IsWindow.argtypes = [ctypes.c_void_p]
        desktop = self.Desktop()
        level = cl.CursorLevel(root, desktop)
        before = user32.GetForegroundWindow()
        try:
            level.follow_state("recording")
            follower = level.follower
            self.until(lambda: follower.visible)
            hwnd = follower.hwnd
            assert user32.IsWindowVisible(hwnd)
            styles = desktop.styles(hwnd)
            assert styles & cl.OVERLAY_STYLES == cl.OVERLAY_STYLES and styles & cl.WS_EX_TOPMOST
            assert not styles & cl.WS_EX_APPWINDOW
            assert user32.GetForegroundWindow() == before, "showing must not take the focus"
            frame_image = level.frame_image
            assert frame_image.getpixel((0, 0))[3] == 0, "the corners are transparent, no colour key"
            assert frame_image.getpixel((frame_image.width // 2, frame_image.height // 2))[3] == 255
            self.until(lambda: follower.ticks > 20)
            frame(level)
            level.follow_state("error")
            assert not user32.IsWindow(hwnd), "the window is destroyed with its thread"
        finally:
            level.close()

    def test_moving_and_painting_the_real_window(self):
        import ctypes

        desktop = cl.Win32Desktop()
        result: dict = {}

        def owner():  # the window belongs to the thread that creates it, as with the follower
            hwnd = desktop.create()
            try:
                result["painted"] = desktop.paint(hwnd, cl.stadium(56, 20, "#FFFCF5"))
                desktop.show(hwnd, 100, 100)
                desktop.move(hwnd, 321, 222)
                desktop.pump()
                rect = cl._Rect()
                ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
                result["rect"] = (rect.left, rect.top, rect.right, rect.bottom)
            finally:
                desktop.destroy(hwnd)

        thread = threading.Thread(target=owner)
        thread.start()
        thread.join(5)
        assert result == {"painted": True, "rect": (321, 222, 321 + 56, 222 + 20)}

    def test_the_real_monitor_lookup(self):
        desktop = cl.Win32Desktop()
        handle, (left, top, right, bottom), factor = desktop.monitor((10, 10))
        assert handle and right > left and bottom > top and factor > 0
        position = desktop.cursor()
        assert position is None or len(position) == 2
        assert desktop.refresh_rate(handle) >= 0 and cl.frame_rate(desktop.refresh_rate(handle)) >= cl.MIN_HZ
        assert desktop.refresh_rate(0) == 0
        assert desktop.fullscreen() in (True, False)

    def test_show_and_hide_leave_no_handles_behind(self, root, switch_on):
        """Twenty dictations: no window, GDI object or thread is left over."""
        import ctypes

        kernel32, user32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        user32.GetGuiResources.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        process = kernel32.GetCurrentProcess()

        def handles():
            return user32.GetGuiResources(process, 0), user32.GetGuiResources(process, 1)

        level = cl.CursorLevel(root, self.Desktop())
        try:
            level.follow_state("recording")
            self.until(lambda: level.follower.visible)
            level.follow_state("ready")
            gdi, user = handles()
            threads = threading.active_count()
            for _ in range(20):
                level.follow_state("recording")
                self.until(lambda: level.follower.visible)
                level.follow_state("ready")
            gdi_after, user_after = handles()
            assert gdi_after - gdi <= 2 and user_after - user <= 2, (gdi, gdi_after, user, user_after)
            assert threading.active_count() == threads and follower_threads() == []
        finally:
            level.close()


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
