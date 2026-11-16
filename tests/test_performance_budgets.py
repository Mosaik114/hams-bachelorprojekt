"""Time budgets for the interactions a user feels: resizing, moving, switching tabs, opening the settings.

Off unless asked for (`set HAMS_PERF=1`): timings depend on the machine and on what else runs, and a shared CI
runner is no place to judge 20 ms. Run it on the development machine before and after a change to the kit.

Budgets (per step, median over the run, on the Tk thread):

* moving a window: 2 ms
* resizing the settings at an edge: 45 ms, the content laid out live in every step. Measured on the development
  PC (median): 16-26 ms on the first page, 27-39 ms on "Allgemein", the longest page - 25 to 60 frames a second.
  Almost all of it is Tk laying out and drawing its ~260 native child windows; the Python part is about 5 ms.
  60 frames on every page would need the rows painted on one canvas instead of built from single widgets.
* switching a settings tab, also the first visit: 30 ms (median), a first visit no dearer than a later one
* showing the prebuilt settings window: 60 ms
* the level at the pointer: one move per frame of the monitor (at least 90 % of its rate), at most 8 % of one
  core for the follower thread
"""

import os
import statistics
import sys
import time
from types import SimpleNamespace

import pytest

from hams import ui_kit as uk

pytestmark = pytest.mark.skipif(not os.environ.get("HAMS_PERF"), reason="time budgets: set HAMS_PERF=1")

MOVE_MS = 2.0
RESIZE_MS = 45.0
TAB_MS = 30.0
#: What a first visit may cost more than a later one (noise; a page laid out on its first visit cost 30-90 ms).
FIRST_EXTRA_MS = 12.0
OPEN_MS = 60.0
#: CPU share of one core the pointer follower may use while the pointer moves (measured: about 3-4 % at 240 Hz).
FOLLOW_CPU = 0.08


def median_ms(samples: list[float]) -> float:
    return statistics.median(samples) * 1000


def event(widget, x, y):
    return SimpleNamespace(widget=widget, x_root=x, y_root=y)


@pytest.fixture
def settings(live_app):
    live_app._open_settings()
    live_app.root.update()
    return live_app.settings_win


def drag(root, chrome, widget, start, steps, dx, dy):
    chrome._on_press(event(widget, *start))
    samples = []
    for step in range(1, steps + 1):
        began = time.perf_counter()
        chrome._on_drag(event(widget, start[0] + dx * step, start[1] + dy * step))
        root.update()
        samples.append(time.perf_counter() - began)
    chrome._on_release()
    root.update()
    return samples


def test_moving_a_window(settings, live_app):
    win = settings.win
    chrome = uk.WindowChrome(win)
    x, y = win.winfo_x() + 60, win.winfo_y() + 20
    samples = drag(live_app.root, chrome, win, (x, y), 40, 3, 1)
    assert median_ms(samples) <= MOVE_MS


@pytest.mark.parametrize("page", [1, 4])
def test_resizing_the_settings(settings, live_app, page):
    settings.select_page(page)
    live_app.root.update()
    win = settings.win
    chrome = uk.WindowChrome(win, min_size=settings.MIN_SIZE)
    edge = (win.winfo_rootx() + win.winfo_width() - 2, win.winfo_rooty() + win.winfo_height() // 2)
    samples = drag(live_app.root, chrome, win, edge, 40, 4, 0)
    print(f"resize page {page}: median {median_ms(samples):.1f} ms, max {max(samples) * 1000:.1f} ms")
    assert median_ms(samples) <= RESIZE_MS


def test_switching_tabs_also_the_first_time(settings, live_app):
    """The hidden pages are laid out ahead in quiet steps after the window appears (`warm_pages`)."""
    for _ in range(60):
        live_app.root.update()
        time.sleep(0.01)

    def visit(page):
        began = time.perf_counter()
        settings.select_page(page)
        live_app.root.update()
        return (time.perf_counter() - began) * 1000

    first = {page: visit(page) for page in (1, 2, 3, 4, 5)}
    visit(0)
    again = {page: visit(page) for page in (1, 2, 3, 4, 5)}
    print("first visits (ms):", {p: round(v, 1) for p, v in first.items()})
    print("later visits (ms):", {p: round(v, 1) for p, v in again.items()})
    assert statistics.median(first.values()) <= TAB_MS
    for page, ms in first.items():
        # Showing a page costs what mapping its widgets costs; the first visit adds no layout of its own.
        assert ms <= again[page] + FIRST_EXTRA_MS, page


def test_showing_the_prebuilt_settings(live_app):
    live_app.prebuild_settings()
    for _ in range(30):
        live_app.root.update()
        time.sleep(0.01)
    began = time.perf_counter()
    live_app._open_settings()
    live_app.root.update()
    assert (time.perf_counter() - began) * 1000 <= OPEN_MS


@pytest.mark.skipif(sys.platform != "win32", reason="the level at the pointer is Win32")
def test_the_level_at_the_pointer_follows_at_the_monitor_rate(root, monkeypatch):
    """The pill moves once per frame of the monitor under the pointer (240 Hz on the development PC, never the
    30-60 Hz of a Tk timer) and its thread costs at most `FOLLOW_CPU` of one core while the pointer moves."""
    import ctypes
    import math

    from hams import cursor_level as cl
    from hams.config import CFG

    monkeypatch.setattr(CFG, "cursor_level", True)
    began = time.perf_counter()

    class Circling(cl.Win32Desktop):
        def cursor(self):  # GetCursorPos fails on the invisible test desktop
            t = time.perf_counter() - began
            return int(800 + 300 * math.cos(t * 3)), int(500 + 200 * math.sin(t * 3))

        def fullscreen(self):
            return False

    desktop = Circling()
    hz = cl.frame_rate(desktop.refresh_rate(desktop.monitor((800, 500))[0]))
    level = cl.CursorLevel(root, desktop)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenThread.restype = ctypes.c_void_p
    kernel32.GetThreadTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_uint64)] * 4
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    def thread_cpu(handle) -> float:
        times = [ctypes.c_uint64() for _ in range(4)]
        kernel32.GetThreadTimes(handle, *map(ctypes.byref, times))
        return (times[2].value + times[3].value) / 1e7

    try:
        level.follow_state("recording")
        query = 0x0800  # THREAD_QUERY_LIMITED_INFORMATION
        handle = kernel32.OpenThread(query, False, level.follower._thread.native_id)
        start, ticks, cpu = time.perf_counter(), level.follower.ticks, thread_cpu(handle)
        while time.perf_counter() - start < 3.0:
            root.update()
            time.sleep(0.005)
        wall = time.perf_counter() - start
        rate = (level.follower.ticks - ticks) / wall
        share = (thread_cpu(handle) - cpu) / wall
        kernel32.CloseHandle(handle)
        print(f"follower: {rate:.1f} ticks/s on a {hz} Hz monitor, {level.moves} moves, {100 * share:.2f} % CPU")
        assert rate >= 0.9 * hz
        assert level.moves >= 0.8 * rate * wall, "a circling pointer moves the pill in nearly every tick"
        assert share <= FOLLOW_CPU
    finally:
        level.close()
