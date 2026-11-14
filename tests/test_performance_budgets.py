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
"""

import os
import statistics
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
