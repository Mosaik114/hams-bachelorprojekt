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


def press(sc, *vks):
    """Drueckt die Tasten nacheinander und laesst sie danach los."""
    for vk in vks:
        assert sc.on_capture_key(SimpleNamespace(keycode=vk)) == "break"
    for vk in vks:
        assert sc.on_capture_release(SimpleNamespace(keycode=vk)) == "break"


@pytest.fixture
def sc(open_settings, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings_shortcuts, "editable", lambda: True)
    CFG.hotkey_transcript, CFG.hotkey_prompt = "ctrl+shift+space", "ctrl+shift+alt+space"
    return open_settings().shortcuts


def combos(hams):
    return [combo for combo, _handler in hams.hotkeys.entries]


class TestCapture:
    def test_change_frees_the_shortcuts_and_asks_for_keys(self, sc, hams):
        sc.start_capture("transcript")
        parts = sc.shortcut_rows["transcript"]
        assert hams.hotkeys.entries == [] and hams.keyboard.unhooked
        assert parts.row.sub_text == "Neue Tastenkombination drücken … Esc bricht ab"
        assert parts.change.winfo_manager() == ""
        assert parts.cancel.winfo_manager() == "pack"
        assert parts.default.winfo_manager() == "", "already the default"

    def test_held_modifiers_are_shown_while_building(self, sc):
        sc.start_capture("transcript")
        sc.on_capture_key(SimpleNamespace(keycode=CTRL))
        sc.on_capture_key(SimpleNamespace(keycode=ALT))
        assert sc.shortcut_rows["transcript"].keys.labels == ["Strg", "Alt"]

    def test_a_good_combination_is_saved_and_active_at_once(self, sc, hams, live_app):
        sc.start_capture("transcript")
        press(sc, CTRL, ALT, ord("D"))
        assert CFG.hotkey_transcript == "ctrl+alt+d"
        assert ("hotkeys", "transcript", "ctrl+alt+d") in hams.saved
        assert combos(hams) == ["ctrl+alt+d", "ctrl+shift+alt+space"]
        assert hams.hotkeys.probed == ["ctrl+alt+d"]
        parts = sc.shortcut_rows["transcript"]
        assert parts.row.sub_text == "Geändert · gilt sofort"
        assert parts.keys.labels == ["Strg", "Alt", "D"]
        assert live_app.shortcut_keys["transcript"].labels == ["Strg", "Alt", "D"]
        assert parts.change.winfo_manager() == "pack" and parts.cancel.winfo_manager() == ""
        assert sc.capture is None

    def test_a_failed_save_still_applies_but_says_so(self, sc, hams, patch_everywhere):
        patch_everywhere("_persist_config_value", lambda *args: False)
        sc.start_capture("prompt")
        press(sc, CTRL, ALT, ord("P"))
        assert CFG.hotkey_prompt == "ctrl+alt+p"
        assert sc.shortcut_rows["prompt"].row.sub_text.startswith("Gilt jetzt")

    @pytest.mark.parametrize(
        ("keys", "problem"),
        [
            ((SHIFT, ord("A")), "Mit Strg, Alt oder Win kombinieren; nur F-Tasten gehen allein"),
            ((CTRL, ord("V")), "Strg+V ist von Windows oder für Kopieren und Einfügen belegt"),
            ((CTRL, SHIFT, ALT, 0x20), "Strg+Umschalt+Alt+Leertaste ist schon der Kurzbefehl für KI fragen"),
            ((CTRL, 0xBA), "Diese Taste geht nicht für einen Kurzbefehl"),
        ],
    )
    def test_a_refused_combination_keeps_capturing(self, sc, hams, keys, problem):
        sc.start_capture("transcript")
        press(sc, *keys)
        parts = sc.shortcut_rows["transcript"]
        assert parts.row.sub_text == problem
        assert sc.capture == "transcript"
        assert CFG.hotkey_transcript == "ctrl+shift+space"
        assert hams.saved == [] and hams.hotkeys.entries == []

    def test_a_combination_another_app_holds(self, sc, hams):
        hams.hotkeys.taken = {"ctrl+alt+d"}
        sc.start_capture("transcript")
        press(sc, CTRL, ALT, ord("D"))
        assert sc.shortcut_rows["transcript"].row.sub_text == "Strg+Alt+D wird schon von einer anderen App benutzt"
        press(sc, CTRL, ALT, ord("E"))
        assert CFG.hotkey_transcript == "ctrl+alt+e"

    def test_escape_cancels_and_restores(self, sc, hams):
        sc.start_capture("prompt")
        press(sc, 0x1B)
        assert sc.capture is None
        assert combos(hams) == ["ctrl+shift+space", "ctrl+shift+alt+space"]
        hint = "Frage oder Auftrag sprechen; die Antwort der KI wird eingefügt"
        assert sc.shortcut_rows["prompt"].row.sub_text == hint
        assert hams.saved == []

    def test_the_cancel_button_does_the_same(self, sc, hams):
        sc.start_capture("transcript")
        sc.cancel_capture()
        sc.cancel_capture()  # ein zweites Mal ist harmlos
        assert combos(hams) == ["ctrl+shift+space", "ctrl+shift+alt+space"]

    def test_keys_outside_a_capture_are_swallowed(self, sc, hams):
        assert sc.on_capture_key(SimpleNamespace(keycode=ord("A"))) == "break"
        assert hams.saved == []

    def test_starting_the_other_row_ends_the_first(self, sc, hams):
        sc.start_capture("transcript")
        sc.start_capture("prompt")
        assert sc.capture == "prompt"
        assert sc.shortcut_rows["transcript"].change.winfo_manager() == "pack"
        assert sc.shortcut_rows["transcript"].row.sub_text == "Startet und beendet ein Diktat"

    def test_closing_the_window_during_a_capture_registers_again(self, sc, hams, live_app):
        sc.start_capture("transcript")
        sc.window.close()
        assert combos(hams) == ["ctrl+shift+space", "ctrl+shift+alt+space"]
        assert sc.capture is None and not sc.alive()

    def test_reopening_after_an_abandoned_capture_shows_the_rows_fresh(self, sc, live_app):
        sc.start_capture("transcript")
        sc.window.close()
        live_app._open_settings()
        live_app.root.update()
        assert live_app.settings_win is sc.window and sc.alive()
        parts = sc.shortcut_rows["transcript"]
        assert parts.row.sub_text == "Startet und beendet ein Diktat"
        assert parts.change.winfo_manager() == "pack" and parts.cancel.winfo_manager() == ""


class TestDefault:
    def test_reset_appears_only_for_a_changed_shortcut_and_restores_it(self, sc, hams):
        CFG.hotkey_prompt = "ctrl+alt+p"
        sc.start_capture("prompt")
        parts = sc.shortcut_rows["prompt"]
        assert parts.default.winfo_manager() == "pack"
        sc.reset_shortcut("prompt")
        assert CFG.hotkey_prompt == "ctrl+shift+alt+space"
        assert ("hotkeys", "prompt", "ctrl+shift+alt+space") in hams.saved
        assert parts.default.winfo_manager() == ""

    def test_reset_refuses_a_default_the_other_shortcut_now_uses(self, sc, hams):
        CFG.hotkey_transcript, CFG.hotkey_prompt = "ctrl+shift+alt+space", "ctrl+alt+p"
        sc.start_capture("prompt")
        sc.reset_shortcut("prompt")
        assert CFG.hotkey_prompt == "ctrl+alt+p"
        assert "schon der Kurzbefehl für Diktieren" in sc.shortcut_rows["prompt"].row.sub_text


class TestLinux:
    def test_the_system_manages_them(self, open_settings, monkeypatch):  # noqa: F811
        monkeypatch.setattr(settings_shortcuts, "editable", lambda: False)
        sc = open_settings().shortcuts
        parts = sc.shortcut_rows["transcript"]
        assert parts.change is None
        assert parts.row.sub_text == "Ändern unter Systemeinstellungen → Tastatur → Kurzbefehle"
        assert sc.shortcut_keys["prompt"].labels == ["Strg", "Umschalt", "Alt", "Leertaste"]

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

    def test_focus_out_registers_the_shortcuts_again_and_focus_in_frees_them(self, sc, hams):
        sc.start_capture("transcript")
        press(sc, SHIFT, ord("A"))  # refused, the capture stays open
        assert sc.capture == "transcript" and hams.hotkeys.entries == []
        sc.on_capture_focus_out()
        assert combos(hams) == ["ctrl+shift+space", "ctrl+shift+alt+space"], "the user went elsewhere"
        sc.on_capture_focus_in()
        assert hams.hotkeys.entries == [], "back in the capture: the old combination must not fire"

    def test_focus_changes_outside_a_capture_change_nothing(self, sc, hams):
        before = combos(hams)
        sc.on_capture_focus_out()
        sc.on_capture_focus_in()
        assert combos(hams) == before
