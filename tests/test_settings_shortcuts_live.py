"""Changing a shortcut in the real settings window (hams/settings_shortcuts.py).

Key presses arrive as Tk events with the Windows virtual-key code; here they are
handed to the capture handlers directly. Registering, probing and saving go to
the fakes of the `hams` fixture, never to the system.
"""

from types import SimpleNamespace

import pytest
from test_settings_window_live import disk, open_settings  # noqa: F401 - Fixtures

from hams import settings_shortcuts
from hams.config import CFG

CTRL, SHIFT, ALT = 0x11, 0x10, 0x12


def press(win, *vks):
    """Drueckt die Tasten nacheinander und laesst sie danach los."""
    for vk in vks:
        assert win._on_capture_key(SimpleNamespace(keycode=vk)) == "break"
    for vk in vks:
        assert win._on_capture_release(SimpleNamespace(keycode=vk)) == "break"


@pytest.fixture
def win(open_settings, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings_shortcuts, "editable", lambda: True)
    CFG.hotkey_transcript, CFG.hotkey_prompt = "ctrl+shift+space", "ctrl+shift+alt+space"
    return open_settings()


def combos(hams):
    return [combo for combo, _handler in hams.hotkeys.entries]


class TestCapture:
    def test_change_frees_the_shortcuts_and_asks_for_keys(self, win, hams):
        win._start_capture("transcript")
        parts = win.shortcut_rows["transcript"]
        assert hams.hotkeys.entries == [] and hams.keyboard.unhooked
        assert parts.row.sub_text == "Neue Tastenkombination drücken … Esc bricht ab"
        assert parts.change.winfo_manager() == ""
        assert parts.cancel.winfo_manager() == "pack"
        assert parts.default.winfo_manager() == "", "already the default"

    def test_held_modifiers_are_shown_while_building(self, win):
        win._start_capture("transcript")
        win._on_capture_key(SimpleNamespace(keycode=CTRL))
        win._on_capture_key(SimpleNamespace(keycode=ALT))
        assert win.shortcut_rows["transcript"].keys.labels == ["Strg", "Alt"]

    def test_a_good_combination_is_saved_and_active_at_once(self, win, hams, live_app):
        win._start_capture("transcript")
        press(win, CTRL, ALT, ord("D"))
        assert CFG.hotkey_transcript == "ctrl+alt+d"
        assert ("hotkeys", "transcript", "ctrl+alt+d") in hams.saved
        assert combos(hams) == ["ctrl+alt+d", "ctrl+shift+alt+space"]
        assert hams.hotkeys.probed == ["ctrl+alt+d"]
        parts = win.shortcut_rows["transcript"]
        assert parts.row.sub_text == "Geändert · gilt sofort"
        assert parts.keys.labels == ["Strg", "Alt", "D"]
        assert live_app.shortcut_keys["transcript"].labels == ["Strg", "Alt", "D"]
        assert parts.change.winfo_manager() == "pack" and parts.cancel.winfo_manager() == ""
        assert win._capture is None

    def test_a_failed_save_still_applies_but_says_so(self, win, hams, patch_everywhere):
        patch_everywhere("_persist_config_value", lambda *args: False)
        win._start_capture("prompt")
        press(win, CTRL, ALT, ord("P"))
        assert CFG.hotkey_prompt == "ctrl+alt+p"
        assert win.shortcut_rows["prompt"].row.sub_text.startswith("Gilt jetzt")

    @pytest.mark.parametrize(
        ("keys", "problem"),
        [
            ((SHIFT, ord("A")), "Mit Strg, Alt oder Win kombinieren; nur F-Tasten gehen allein"),
            ((CTRL, ord("V")), "Strg+V ist von Windows oder für Kopieren und Einfügen belegt"),
            ((CTRL, SHIFT, ALT, 0x20), "Strg+Umschalt+Alt+Leertaste ist schon der Kurzbefehl für KI fragen"),
            ((CTRL, 0xBA), "Diese Taste geht nicht für einen Kurzbefehl"),
        ],
    )
    def test_a_refused_combination_keeps_capturing(self, win, hams, keys, problem):
        win._start_capture("transcript")
        press(win, *keys)
        parts = win.shortcut_rows["transcript"]
        assert parts.row.sub_text == problem
        assert win._capture == "transcript"
        assert CFG.hotkey_transcript == "ctrl+shift+space"
        assert hams.saved == [] and hams.hotkeys.entries == []

    def test_a_combination_another_app_holds(self, win, hams):
        hams.hotkeys.taken = {"ctrl+alt+d"}
        win._start_capture("transcript")
        press(win, CTRL, ALT, ord("D"))
        assert win.shortcut_rows["transcript"].row.sub_text == "Strg+Alt+D wird schon von einer anderen App benutzt"
        press(win, CTRL, ALT, ord("E"))
        assert CFG.hotkey_transcript == "ctrl+alt+e"

    def test_escape_cancels_and_restores(self, win, hams):
        win._start_capture("prompt")
        press(win, 0x1B)
        assert win._capture is None
        assert combos(hams) == ["ctrl+shift+space", "ctrl+shift+alt+space"]
        hint = "Frage oder Auftrag sprechen; die Antwort der KI wird eingefügt"
        assert win.shortcut_rows["prompt"].row.sub_text == hint
        assert hams.saved == []

    def test_the_cancel_button_does_the_same(self, win, hams):
        win._start_capture("transcript")
        win._cancel_capture()
        win._cancel_capture()  # ein zweites Mal ist harmlos
        assert combos(hams) == ["ctrl+shift+space", "ctrl+shift+alt+space"]

    def test_keys_outside_a_capture_are_swallowed(self, win, hams):
        assert win._on_capture_key(SimpleNamespace(keycode=ord("A"))) == "break"
        assert hams.saved == []

    def test_starting_the_other_row_ends_the_first(self, win, hams):
        win._start_capture("transcript")
        win._start_capture("prompt")
        assert win._capture == "prompt"
        assert win.shortcut_rows["transcript"].change.winfo_manager() == "pack"
        assert win.shortcut_rows["transcript"].row.sub_text == "Startet und beendet ein Diktat"

    def test_closing_the_window_during_a_capture_registers_again(self, win, hams, live_app):
        win._start_capture("transcript")
        win.close()
        assert combos(hams) == ["ctrl+shift+space", "ctrl+shift+alt+space"]


class TestDefault:
    def test_reset_appears_only_for_a_changed_shortcut_and_restores_it(self, win, hams):
        CFG.hotkey_prompt = "ctrl+alt+p"
        win._start_capture("prompt")
        parts = win.shortcut_rows["prompt"]
        assert parts.default.winfo_manager() == "pack"
        win._reset_shortcut("prompt")
        assert CFG.hotkey_prompt == "ctrl+shift+alt+space"
        assert ("hotkeys", "prompt", "ctrl+shift+alt+space") in hams.saved
        assert parts.default.winfo_manager() == ""

    def test_reset_refuses_a_default_the_other_shortcut_now_uses(self, win, hams):
        CFG.hotkey_transcript, CFG.hotkey_prompt = "ctrl+shift+alt+space", "ctrl+alt+p"
        win._start_capture("prompt")
        win._reset_shortcut("prompt")
        assert CFG.hotkey_prompt == "ctrl+alt+p"
        assert "schon der Kurzbefehl für Diktieren" in win.shortcut_rows["prompt"].row.sub_text


class TestLinux:
    def test_the_system_manages_them(self, open_settings, monkeypatch):  # noqa: F811
        monkeypatch.setattr(settings_shortcuts, "editable", lambda: False)
        win = open_settings()
        parts = win.shortcut_rows["transcript"]
        assert parts.change is None
        assert parts.row.sub_text == "Ändern unter Systemeinstellungen → Tastatur → Kurzbefehle"
        assert win.shortcut_keys["prompt"].labels == ["Strg", "Umschalt", "Alt", "Leertaste"]

    def test_only_windows_records_keys(self, monkeypatch):
        monkeypatch.setattr(settings_shortcuts.sys, "platform", "linux")
        assert settings_shortcuts.editable() is False
        monkeypatch.setattr(settings_shortcuts.sys, "platform", "win32")
        assert settings_shortcuts.editable() is True


class TestApp:
    def test_a_new_registration_clears_the_old_shortcut_error(self, live_app, hams):
        from live_app import FakeHotkeys

        hams.keyboard.refuse[CFG.hotkey_transcript] = RuntimeError("belegt")
        FakeHotkeys.failing = {CFG.hotkey_transcript}
        live_app._register_hotkeys()
        live_app.root.update()
        assert live_app._state == "error"
        FakeHotkeys.failing = set()
        hams.keyboard.refuse.clear()
        live_app.reregister_hotkeys()
        live_app.root.update()
        assert live_app._state != "error"

    def test_other_errors_are_left_alone(self, live_app):
        live_app.apply_state("error", "Mikrofon fehlt")
        live_app.reregister_hotkeys()
        assert live_app._state == "error"

    def test_availability_without_a_probe_is_assumed(self, live_app, monkeypatch):
        monkeypatch.setattr(live_app, "_system_hotkeys", SimpleNamespace(stop=lambda: None))
        assert live_app.shortcut_available("ctrl+alt+d") is True

    def test_a_failing_probe_does_not_block(self, live_app, monkeypatch):
        def broken(_combo):
            raise OSError("kaputt")

        monkeypatch.setattr(live_app, "_system_hotkeys", SimpleNamespace(available=broken, stop=lambda: None))
        assert live_app.shortcut_available("ctrl+alt+d") is True

    def test_a_failing_unhook_is_logged(self, live_app, hams, monkeypatch, caplog):
        def broken():
            raise RuntimeError("kaputt")

        monkeypatch.setattr(hams.keyboard, "unhook_all_hotkeys", broken)
        live_app.suspend_hotkeys()
        assert "Hotkey unhook failed" in caplog.text


class TestLeavingARejectedCapture:
    """Third review: a refused combination left the capture open, and leaving the window kept the hotkeys off."""

    def test_focus_out_registers_the_shortcuts_again_and_focus_in_frees_them(self, win, hams):
        win._start_capture("transcript")
        press(win, SHIFT, ord("A"))  # refused, the capture stays open
        assert win._capture == "transcript" and hams.hotkeys.entries == []
        win._on_capture_focus_out()
        assert combos(hams) == ["ctrl+shift+space", "ctrl+shift+alt+space"], "the user went elsewhere"
        win._on_capture_focus_in()
        assert hams.hotkeys.entries == [], "back in the capture: the old combination must not fire"

    def test_focus_changes_outside_a_capture_change_nothing(self, win, hams):
        before = combos(hams)
        win._on_capture_focus_out()
        win._on_capture_focus_in()
        assert combos(hams) == before
