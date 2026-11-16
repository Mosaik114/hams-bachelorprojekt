"""Snapshot run: pictures of every window, tab and state, light and dark. Off unless asked for.

    set HAMS_SNAPSHOT_DIR=C:\\somewhere\\new
    pytest tests/test_snapshots.py

Then compare two runs with `python tools/compare_snapshots.py <before> <after>`. Runs only on Windows (the
pictures come from `PrintWindow`) and only on a desktop of its own, like every Tk test here.
"""

import os
import sys
from pathlib import Path

import pytest
from setup_fakes import answer_ollama, fake_services, online
from window_capture import capture

from hams import setup_window as window_mod
from hams import ui_kit as uk
from hams.config import CFG
from hams.setup_window import SetupWindow
from hams.ui_kit.popover import Popover

TARGET = os.environ.get("HAMS_SNAPSHOT_DIR")
pytestmark = pytest.mark.skipif(
    not TARGET or sys.platform != "win32", reason="snapshot run: set HAMS_SNAPSHOT_DIR (Windows only)"
)
MODES = ("light", "dark")


@pytest.fixture
def out():
    return Path(TARGET)


@pytest.fixture
def themed(live_app):
    def apply(mode):
        uk.Theme.apply(mode)
        uk.apply_theme_tree(live_app.root)
        live_app.root.update()

    yield apply
    apply("light")


@pytest.mark.parametrize("mode", MODES)
def test_main_window(live_app, themed, out, mode):
    themed(mode)
    live_app._show_window()
    for state in ("ready", "recording", "transcribing", "error"):
        live_app.apply_state(state)
        capture(live_app.root, out / f"{mode}-main-{state}.png")
    live_app.apply_state("ready")


@pytest.mark.parametrize("mode", MODES)
def test_settings_pages(live_app, hams, themed, out, mode):
    themed(mode)
    answer_ollama(live_app, hams, online("qwen3:8b"))
    live_app._open_settings()
    live_app.root.update()
    win = live_app.settings_win
    win._repaint()
    for page in range(len(win._pages)):
        win.select_page(page)
        capture(win.win, out / f"{mode}-settings-{page}.png")
    win.select_page(1)
    win.transcription.model_select.open_popover()
    live_app.root.update()
    if Popover.current is not None:
        capture(Popover.current, out / f"{mode}-popover.png")
        Popover.dismiss_current()
    win.select_page(0)
    win.close()


def _row_titled(widget, title):
    for child in widget.winfo_children():
        if isinstance(child, uk.SettingRow) and child._title_text == title:
            return child
        found = _row_titled(child, title)
        if found is not None:
            return found
    return None


@pytest.mark.parametrize("mode", MODES)
def test_settings_row_hover(live_app, hams, themed, out, mode):
    """A row under the pointer: the rounded hover surface lies behind its text (KI and Allgemein)."""
    themed(mode)
    answer_ollama(live_app, hams, online("qwen3:8b"))
    live_app._open_settings()
    live_app.root.update()
    win = live_app.settings_win
    win._repaint()
    for page in range(len(win._pages)):
        win.select_page(page)
        win._stop_warming()
        live_app.root.update()
        for name, title in (("ki", "Text bereinigen"), ("allgemein", "Nach Updates suchen")):
            row = _row_titled(win.scroll.body, title)
            if row is None or not row.winfo_ismapped():
                continue
            capture(win.win, out / f"{mode}-hover-{name}-rest.png")
            row._on_enter()
            capture(win.win, out / f"{mode}-hover-{name}.png")
            row._on_leave()
    win.select_page(0)
    win.close()


def test_settings_sizes(live_app, out):
    live_app._open_settings()
    live_app.root.update()
    win = live_app.settings_win.win
    for name, (width, height) in {"min": (400, 420), "wide": (900, 700)}.items():
        win.geometry(f"{uk.px(width)}x{uk.px(height)}")
        capture(win, out / f"light-settings-{name}.png")
    live_app.settings_win.close()


@pytest.mark.parametrize("mode", MODES)
def test_setup_pages(live_app, themed, out, mode, monkeypatch, tmp_path):
    themed(mode)
    monkeypatch.setattr(window_mod, "CONFIG_PATH", tmp_path / "config.toml")
    CFG.model_size = "turbo"
    wizard = SetupWindow(live_app, fake_services(gpu_present=lambda: True, gpu_libraries_found=lambda: True))
    live_app.setup_win = wizard
    number = 0
    while True:
        wizard.show_page()
        capture(wizard.win, out / f"{mode}-setup-{number}-{wizard.controller.page}.png")
        if wizard.controller.is_last:
            break
        wizard.controller.next()
        number += 1
    wizard.close(confirmed=True)
