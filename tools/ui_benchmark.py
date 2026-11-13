"""Reproducible UI benchmark: settings window and setup assistant.

Measures on the Tk thread: opening (cold = empty image caches, warm, prebuilt, reopened), scrolling (wheel
notch, 4 px steps, glide), hover, switch animation (frame rate and cost), popover, light/dark switch, level meter
tick, download progress, idle CPU, Hugging Face cache scan, assistant opening and page switch. It builds the real
windows with the fakes of the test suite (tests/conftest.py, tests/live_app.py): no microphone, model, Ollama or
network is used and no user data is read (the cache scan only reads the Hugging Face cache).

Run from the repository root, ideally on a separate desktop so no window appears (on Windows a hidden desktop
does not paint at all, so the figures leave out the GDI painting itself; they are the Python and Tk work):

    PYTHONPATH=tests python -m pytest -p conftest -q -s -p no:cacheprovider tools/ui_benchmark.py

Environment: BENCH_OUT (JSON result file, default ui_benchmark.json in the working directory), BENCH_DPI (force a
DPI, e.g. 144 for 150 %), BENCH_PROF (directory for cProfile dumps). Run nothing else heavy meanwhile: parallel
load slows every figure several times. The helper `part()` also reads windows of versions before 1.3.0 (mixins),
so the same file measures an older checkout for a before/after comparison.
"""

from __future__ import annotations

import cProfile
import json
import os
import statistics
import sys
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from hams import models as whisper_models
from hams import settings_models as models_mod
from hams import settings_status as status_mod
from hams import ui_kit as uk
from hams.config import CFG
from hams.ui_kit.animation import Animator
from hams.ui_kit.caches import _icon_cache, _img_cache

OUT = Path(os.environ.get("BENCH_OUT", "ui_benchmark.json"))
PROF = os.environ.get("BENCH_PROF")
RESULTS: dict = {}


def stats(samples_ms: list[float]) -> dict:
    data = sorted(samples_ms)
    if not data:
        return {}

    def pct(p: float) -> float:
        return data[min(len(data) - 1, int(round(p * (len(data) - 1))))]

    return {
        "n": len(data),
        "median_ms": round(statistics.median(data), 2),
        "p95_ms": round(pct(0.95), 2),
        "max_ms": round(data[-1], 2),
        "under_8_3ms_pct": round(100 * sum(1 for v in data if v <= 8.33) / len(data), 1),
        "under_16_7ms_pct": round(100 * sum(1 for v in data if v <= 16.67) / len(data), 1),
    }


def record(name: str, value) -> None:
    RESULTS[name] = value
    OUT.write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[bench] {name}: {value}", flush=True)


class InlineThread:
    def __init__(self, target, args=(), **_options):
        self.target, self.args = target, args

    def start(self) -> None:
        self.target(*self.args)


@pytest.fixture(autouse=True)
def dpi(root):
    import hams

    RESULTS["hams_source"] = str(Path(hams.__file__).parent)
    forced = os.environ.get("BENCH_DPI")
    if forced:
        uk.Scale.apply(root, int(forced))
    RESULTS["scale_factor"] = uk.Scale.factor
    yield


@pytest.fixture
def disk(monkeypatch):
    state = SimpleNamespace(installed={"large-v3-turbo"}, cached=[])
    monkeypatch.setattr(whisper_models, "installed_whisper_models", lambda: set(state.installed))
    monkeypatch.setattr(whisper_models, "cached_whisper_models", lambda: tuple(state.cached))
    return state


@pytest.fixture
def opener(live_app, disk, monkeypatch):
    inline = SimpleNamespace(Thread=InlineThread)
    monkeypatch.setattr(status_mod, "threading", inline)
    monkeypatch.setattr(models_mod, "threading", inline)
    CFG.model_size = "large-v3-turbo"
    CFG.mic_device_raw = "Headset"

    def _open():
        t0 = time.perf_counter()
        live_app._open_settings()
        live_app.root.update()
        elapsed = (time.perf_counter() - t0) * 1000
        return live_app.settings_win, elapsed

    def _close():
        """Removes the window for good, so the next open builds a new one (the "build" path)."""
        win = live_app.settings_win
        if win is not None:
            if hasattr(win, "destroy"):
                win.destroy()
            else:
                win.close()
        live_app.settings_win = None
        live_app.root.update()

    def _hide():
        live_app.settings_win.close()
        live_app.root.update()

    return SimpleNamespace(open=_open, close=_close, hide=_hide, app=live_app)


def part(win, name):
    """New API: attributes live on the section objects; old API: on the window."""
    paths = {
        "mic_select": ("recording", "monitor", "mic_select"),
        "switch_sound": ("general", "switch_sound"),
        "monitor": ("recording", "monitor"),
        "transcription": ("transcription",),
    }
    if hasattr(win, "recording"):
        obj = win
        for step in paths[name]:
            obj = getattr(obj, step)
        return obj
    return win if name in ("monitor", "transcription") else getattr(win, name)


def clear_caches() -> None:
    _img_cache.clear()
    _icon_cache.clear()


def pump(root, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.001)


# ------------------------------------------------------------------ opening


def test_open_settings(opener):
    cold, warm = [], []
    for _ in range(5):
        clear_caches()
        _win, ms = opener.open()
        cold.append(ms)
        opener.close()
    for _ in range(5):
        _win, ms = opener.open()
        warm.append(ms)
        opener.close()
    record("settings_open_cold", stats(cold))
    record("settings_open_warm", stats(warm))
    if PROF:
        clear_caches()
        profile = cProfile.Profile()
        profile.enable()
        opener.open()
        profile.disable()
        profile.dump_stats(str(Path(PROF) / "open_cold.prof"))
        opener.close()
        profile = cProfile.Profile()
        profile.enable()
        opener.open()
        profile.disable()
        profile.dump_stats(str(Path(PROF) / "open_warm.prof"))
        opener.close()


def test_widget_count(opener):
    win, _ms = opener.open()

    def count(widget) -> int:
        return 1 + sum(count(child) for child in widget.winfo_children())

    images = len(_img_cache)
    record("settings_widgets", {"widgets": count(win.win), "cached_images": images})
    opener.close()


# ------------------------------------------------------------------ scrolling


def scroll_run(win, root, step: int) -> list[float]:
    times = []
    scroll = win.scroll
    for direction in (1, -1):
        guard = 0
        while guard < 2000:
            guard += 1
            before = scroll.offset
            t0 = time.perf_counter()
            scroll.scroll_by(direction * step)
            root.update()
            times.append((time.perf_counter() - t0) * 1000)
            if scroll.offset == before:
                break
    return times


def test_scroll(opener):
    win, _ms = opener.open()
    root = opener.app.root
    pump(root, 0.3)
    record("scroll_range_px", win.scroll.scrollable)
    record("scroll_wheel_notch_40px", stats(scroll_run(win, root, uk.px(40))))
    record("scroll_smooth_4px", stats(scroll_run(win, root, uk.px(4))))
    if PROF:
        profile = cProfile.Profile()
        profile.enable()
        scroll_run(win, root, uk.px(4))
        profile.disable()
        profile.dump_stats(str(Path(PROF) / "scroll.prof"))
    opener.close()


# ------------------------------------------------------------------ hover


def hoverables(widget) -> list:
    found = []
    try:
        if "<Enter>" in widget.bind():
            found.append(widget)
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        found.extend(hoverables(child))
    return found


def test_hover(opener):
    win, _ms = opener.open()
    root = opener.app.root
    pump(root, 0.2)
    targets = hoverables(win.win)
    cold, warm = [], []
    for samples in (cold, warm):
        for widget in targets:
            for sequence in ("<Enter>", "<Leave>"):
                t0 = time.perf_counter()
                widget.event_generate(sequence, x=2, y=2)
                root.update()
                samples.append((time.perf_counter() - t0) * 1000)
    record("hover_targets", len(targets))
    record("hover_first", stats(cold))
    record("hover_repeat", stats(warm))
    opener.close()


# ------------------------------------------------------------------ animation


def run_animation(root, trigger) -> dict:
    """Runs one animation with Animator on; returns frame intervals and per-frame cost."""
    stamps, costs = [], []
    original = Animator.run.__func__

    def wrapped(cls, widget, key, duration_ms, step, done=None):
        def timed(fraction):
            t0 = time.perf_counter()
            step(fraction)
            widget.update_idletasks()
            costs.append((time.perf_counter() - t0) * 1000)
            stamps.append(t0)

        return original(cls, widget, key, duration_ms, timed, done)

    Animator.run = classmethod(wrapped)
    Animator.enabled = True
    try:
        trigger()
        end = time.perf_counter() + 2
        while Animator.pending() and time.perf_counter() < end:
            root.update()
            time.sleep(0.0005)
        root.update()
    finally:
        Animator.run = classmethod(original)
        Animator.enabled = False
    intervals = [(b - a) * 1000 for a, b in zip(stamps, stamps[1:], strict=False)]
    span = (stamps[-1] - stamps[0]) if len(stamps) > 1 else 0
    return {
        "frames": len(stamps),
        "fps": round((len(stamps) - 1) / span, 1) if span else None,
        "interval": stats(intervals),
        "cost": stats(costs),
    }


def test_switch_animation(opener):
    win, _ms = opener.open()
    root = opener.app.root
    pump(root, 0.2)
    first = run_animation(root, part(win, 'switch_sound').toggle)
    again = run_animation(root, part(win, 'switch_sound').toggle)
    record("switch_animation_first", first)
    record("switch_animation_repeat", again)
    opener.close()


def test_popover(opener):
    win, _ms = opener.open()
    root = opener.app.root
    pump(root, 0.2)
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        part(win, 'mic_select').open_popover()
        root.update()
        times.append((time.perf_counter() - t0) * 1000)
        uk.Popover.dismiss_current()
        root.update()
    record("popover_open", stats(times))
    opener.close()


# ------------------------------------------------------------------ theme, meter, download


def test_theme_switch(opener):
    win, _ms = opener.open()
    root = opener.app.root
    app = opener.app
    times = []
    start = uk.Theme.mode
    for mode in (uk.Theme.DARK, uk.Theme.LIGHT, uk.Theme.DARK, uk.Theme.LIGHT):
        t0 = time.perf_counter()
        app.set_appearance(mode)
        root.update()
        times.append((time.perf_counter() - t0) * 1000)
    if PROF:
        profile = cProfile.Profile()
        profile.enable()
        app.set_appearance(uk.Theme.DARK)
        root.update()
        app.set_appearance(uk.Theme.LIGHT)
        root.update()
        profile.disable()
        profile.dump_stats(str(Path(PROF) / "theme.prof"))
    app.set_appearance(start)
    record("theme_switch", stats(times))
    opener.close()


def test_level_meter(opener):
    win, _ms = opener.open()
    root = opener.app.root
    times = []
    rng = np.random.default_rng(1)
    mon = part(win, "monitor")
    new = hasattr(win, "recording")
    for value in rng.random(300):
        t0 = time.perf_counter()
        if new:
            mon.rms = float(value) * 0.2
            if mon._tick_job is not None:
                win.win.after_cancel(mon._tick_job)
            mon.tick()
        else:
            win._mon_rms = float(value) * 0.2
            if win._tick_job is not None:
                win.win.after_cancel(win._tick_job)
            win._tick()
        root.update()
        times.append((time.perf_counter() - t0) * 1000)
    record("level_meter_tick", stats(times))
    opener.close()


def test_download_progress(opener):
    win, _ms = opener.open()
    root = opener.app.root
    tr = part(win, "transcription")
    attr = "downloading" if hasattr(win, "recording") else "_downloading"
    setattr(tr, attr, "large-v3")
    times = []
    total = 3_000_000_000
    for i in range(300):
        t0 = time.perf_counter()
        tr.on_download_progress(i * 10_000_000, total, int(100 * i * 10_000_000 / total))
        root.update()
        times.append((time.perf_counter() - t0) * 1000)
    setattr(tr, attr, "")
    record("download_progress", stats(times))
    opener.close()


def test_idle_cpu(opener):
    win, _ms = opener.open()
    root = opener.app.root
    pump(root, 0.3)
    cpu0, wall0 = time.thread_time(), time.perf_counter()
    root.after(3000, root.quit)
    root.mainloop()
    cpu = time.thread_time() - cpu0
    wall = time.perf_counter() - wall0
    record("idle_cpu_tk_thread_pct", round(100 * cpu / wall, 2))
    opener.close()


def test_hf_cache_scan():
    """The real scan, read-only, against whatever Hugging Face cache this machine has."""
    from importlib import reload

    from hams import models as real_models

    reload(real_models)
    times_cached, times_installed = [], []
    for _ in range(5):
        t0 = time.perf_counter()
        real_models.cached_whisper_models()
        times_cached.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        real_models.installed_whisper_models()
        times_installed.append((time.perf_counter() - t0) * 1000)
    record("hf_cache_scan_cached", stats(times_cached))
    record("hf_cache_scan_installed", stats(times_installed))


# ------------------------------------------------------------------ setup wizard


@pytest.fixture
def wizard(live_app, hams, monkeypatch, tmp_path):
    from setup_fakes import fake_services

    from hams import setup_window as window_mod
    from hams import setup_wizard as sw
    from hams.setup_window import SetupWindow

    monkeypatch.setattr(sw, "TaskThread", InlineThread)
    monkeypatch.setattr(window_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(live_app.models, "_start_switch_thread", lambda *a: None)
    CFG.model_size = "turbo"
    CFG.mic_device_raw = "Headset"

    def open_():
        t0 = time.perf_counter()
        live_app.setup_win = SetupWindow(live_app, fake_services(installed_models=lambda: {"turbo"}))
        live_app.root.update()
        return live_app.setup_win, (time.perf_counter() - t0) * 1000

    def close_():
        win = live_app.setup_win
        if win is not None and win.alive():
            win.close(confirmed=True)
        live_app.root.update()

    return SimpleNamespace(open=open_, close=close_, app=live_app)


def test_wizard(wizard):
    cold, warm, pages = [], [], []
    for samples in (cold, warm):
        for _ in range(3):
            if samples is cold:
                clear_caches()
            _win, ms = wizard.open()
            samples.append(ms)
            wizard.close()
    win, _ms = wizard.open()
    root = wizard.app.root
    for _ in range(2):
        for _step in range(4):
            t0 = time.perf_counter()
            win.go_next()
            root.update()
            pages.append((time.perf_counter() - t0) * 1000)
        for _step in range(4):
            t0 = time.perf_counter()
            win.go_back()
            root.update()
            pages.append((time.perf_counter() - t0) * 1000)
    record("wizard_open_cold", stats(cold))
    record("wizard_open_warm", stats(warm))
    record("wizard_page_switch", stats(pages))
    if PROF:
        profile = cProfile.Profile()
        profile.enable()
        for _step in range(4):
            win.go_next()
            root.update()
        profile.disable()
        profile.dump_stats(str(Path(PROF) / "wizard_pages.prof"))
    wizard.close()


def test_reshow_prebuilt(opener):
    """Prototype: keep the built window and only hide/show it."""
    win, _ms = opener.open()
    root = opener.app.root
    pump(root, 0.2)
    times = []
    for _ in range(10):
        win.win.withdraw()
        root.update()
        t0 = time.perf_counter()
        win.win.deiconify()
        root.update()
        times.append((time.perf_counter() - t0) * 1000)
    record("reshow_prebuilt", stats(times))
    opener.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows timer resolution")
def test_timer_resolution(root):
    """How regular is after(8) with the default timer vs. timeBeginPeriod(1)?"""
    import ctypes

    def run(label):
        stamps = []

        def tick():
            stamps.append(time.perf_counter())
            if len(stamps) < 120:
                root.after(8, tick)
            else:
                root.quit()

        root.after(8, tick)
        root.mainloop()
        intervals = [(b - a) * 1000 for a, b in zip(stamps, stamps[1:], strict=False)]
        span = stamps[-1] - stamps[0]
        record(label, {"fps": round((len(stamps) - 1) / span, 1), "interval": stats(intervals)})

    run("timer_after8_default")
    ctypes.windll.winmm.timeBeginPeriod(1)
    try:
        run("timer_after8_period1")
    finally:
        ctypes.windll.winmm.timeEndPeriod(1)


def test_configure_cascade(opener):
    """Counts <Configure> events and Python handler time during one warm open."""
    import collections

    opener.open()
    opener.close()
    counts = collections.Counter()
    root = opener.app.root

    def tick(event):
        counts[event.widget.winfo_class()] += 1

    root.bind_class("Frame", "<Configure>", tick, add="+")
    root.bind_class("Canvas", "<Configure>", tick, add="+")
    root.bind_class("Label", "<Configure>", tick, add="+")
    t0 = time.perf_counter()
    win, _ = opener.open()
    total = (time.perf_counter() - t0) * 1000
    for widget_class in ("Frame", "Canvas", "Label"):
        root.unbind_class(widget_class, "<Configure>")
    record("configure_events", {"total_ms": round(total, 1), **dict(counts)})
    opener.close()


def test_prebuilt_and_reopen(opener):
    """New API only: build hidden ahead (prebuild), first show, then hide/show again."""
    app = opener.app
    if not hasattr(app, "prebuild_settings"):
        pytest.skip("old version")
    clear_caches()
    t0 = time.perf_counter()
    app.prebuild_settings()
    app.root.update()
    build = (time.perf_counter() - t0) * 1000
    pump(app.root, 0.3)
    t0 = time.perf_counter()
    app._open_settings()
    app.root.update()
    first = (time.perf_counter() - t0) * 1000
    reopen = []
    for _ in range(10):
        opener.hide()
        pump(app.root, 0.05)
        t0 = time.perf_counter()
        app._open_settings()
        app.root.update()
        reopen.append((time.perf_counter() - t0) * 1000)
    if PROF:
        opener.hide()
        profile = cProfile.Profile()
        profile.enable()
        app._open_settings()
        app.root.update()
        profile.disable()
        profile.dump_stats(str(Path(PROF) / "reopen.prof"))
    record("prebuild_hidden_ms", round(build, 1))
    record("open_prebuilt_first_show", round(first, 1))
    record("reopen_hidden", stats(reopen))
    opener.close()


def test_scroll_glide(opener):
    """Wheel notches with animations on: frame intervals and per-frame cost of the glide."""
    win, _ms = opener.open()
    root = opener.app.root
    pump(root, 0.3)
    if not hasattr(win.scroll, "glide_to"):
        pytest.skip("old version: no glide")
    event = type("E", (), {"delta": -120})

    def spin():
        for _ in range(3):
            win.scroll._on_wheel(event())

    record("scroll_glide", run_animation(root, spin))
    opener.close()
