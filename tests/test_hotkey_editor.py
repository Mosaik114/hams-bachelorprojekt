"""Recording and checking a new shortcut, without a window (hams/hotkey_editor.py)."""

import pytest

from hams import hotkey_editor as he
from hams.config import Config
from hams.system_hotkeys import parse_combo

CTRL, SHIFT, ALT, WIN = 0x11, 0x10, 0x12, 0x5B


class TestNames:
    @pytest.mark.parametrize(
        ("vk", "name"),
        [(0x41, "a"), (0x5A, "z"), (0x30, "0"), (0x39, "9"), (0x70, "f1"), (0x87, "f24"), (0x20, "space"),
         (0x21, "page up"), (0x26, "up"), (0x13, "pause")],
    )  # fmt: skip
    def test_keys_are_named_like_the_config(self, vk, name):
        assert he.key_name(vk) == name

    def test_every_named_key_is_understood_by_register_hot_key(self):
        for vk in [*range(0x41, 0x5B), *range(0x30, 0x3A), *range(0x70, 0x88), *he._NAMED_VKS]:
            assert parse_combo(f"ctrl+{he.key_name(vk)}") == (0x0002, vk)

    @pytest.mark.parametrize("vk", [0xBA, 0xDE, 0x14, 0x90, 0x00])
    def test_keys_without_a_name_are_refused(self, vk):
        assert he.key_name(vk) is None

    @pytest.mark.parametrize(
        ("combo", "expected"),
        [
            ("ctrl+shift+space", "ctrl+shift+space"),
            ("Strg+Umschalt+Leertaste", "ctrl+shift+space"),
            ("alt + ctrl + D", "ctrl+alt+d"),
            ("windows+escape", "win+esc"),
            ("super+pagedown", "win+page down"),
            ("F5", "f5"),
        ],
    )
    def test_canonical_spelling(self, combo, expected):
        assert he.canonical(combo) == expected

    @pytest.mark.parametrize("combo", ["", "ctrl+", "ctrl+shift", "ctrl+a+b", None])
    def test_no_canonical_spelling_without_exactly_one_key(self, combo):
        assert he.canonical(combo) is None

    def test_display_uses_the_key_labels(self):
        assert he.display("ctrl+shift+space") == "Strg+Umschalt+Leertaste"

    def test_function_keys(self):
        assert he.is_function_key("f1") and he.is_function_key("f24")
        assert not he.is_function_key("f25") and not he.is_function_key("f") and not he.is_function_key("a")

    def test_defaults_match_the_config(self):
        expected = {"transcript": Config.hotkey_transcript, "prompt": Config.hotkey_prompt}
        assert expected == he.DEFAULTS


class TestRecorder:
    def test_modifiers_wait_for_a_key(self):
        recorder = he.ComboRecorder()
        assert recorder.press(CTRL) == he.PENDING
        assert recorder.press(0xA1) == he.PENDING  # rechte Umschalttaste
        assert recorder.held() == "ctrl+shift"
        assert recorder.press(0x44) == "ctrl+shift+d"

    def test_order_follows_the_defaults_not_the_fingers(self):
        recorder = he.ComboRecorder()
        for vk in (WIN, ALT, SHIFT, CTRL):
            recorder.press(vk)
        assert recorder.press(0x20) == "ctrl+shift+alt+win+space"

    def test_released_modifiers_no_longer_count(self):
        recorder = he.ComboRecorder()
        recorder.press(CTRL)
        recorder.press(ALT)
        recorder.release(0xA4)  # linke Alt-Taste
        recorder.release(0x41)  # keine Modifikatortaste: nichts passiert
        assert recorder.press(0x70) == "ctrl+f1"

    def test_escape_alone_cancels(self):
        assert he.ComboRecorder().press(he.VK_ESCAPE) == he.CANCEL

    def test_escape_with_a_modifier_is_a_combination(self):
        recorder = he.ComboRecorder()
        recorder.press(CTRL)
        assert recorder.press(he.VK_ESCAPE) == "ctrl+esc"

    def test_an_unknown_key_is_reported(self):
        recorder = he.ComboRecorder()
        recorder.press(CTRL)
        assert recorder.press(0xBA) == he.UNSUPPORTED

    def test_reset_forgets_held_keys(self):
        recorder = he.ComboRecorder()
        recorder.press(CTRL)
        recorder.reset()
        assert recorder.held() == ""
        assert recorder.press(0x41) == "a"


CURRENT = {"transcript": "ctrl+shift+space", "prompt": "ctrl+shift+alt+space"}


class TestCheck:
    def test_a_good_combination_is_returned_in_canonical_spelling(self):
        result = he.check("Alt+Ctrl+D", "transcript", CURRENT)
        assert result.ok and result.combo == "ctrl+alt+d"

    @pytest.mark.parametrize("combo", ["a", "space", "shift+a", "shift+space"])
    def test_needs_ctrl_alt_or_win(self, combo):
        result = he.check(combo, "transcript", CURRENT)
        assert not result.ok
        assert result.problem == "Mit Strg, Alt oder Win kombinieren; nur F-Tasten gehen allein"

    @pytest.mark.parametrize("combo", ["f9", "shift+f9"])
    def test_function_keys_may_stand_alone(self, combo):
        assert he.check(combo, "transcript", CURRENT).ok

    @pytest.mark.parametrize("combo", ["alt+tab", "ctrl+v", "win+l", "ctrl+alt+delete", "f12", "Strg+C"])
    def test_reserved_combinations_are_refused(self, combo):
        result = he.check(combo, "transcript", CURRENT)
        assert "von Windows oder für Kopieren und Einfügen belegt" in result.problem

    def test_the_other_shortcut_is_not_taken_twice(self):
        result = he.check("ctrl+alt+shift+space", "transcript", CURRENT)
        assert result.problem == "Strg+Umschalt+Alt+Leertaste ist schon der Kurzbefehl für KI fragen"

    def test_garbage_is_unsupported(self):
        assert he.check("ctrl+a+b", "prompt", CURRENT).problem == "Diese Taste geht nicht für einen Kurzbefehl"

    def test_a_combination_held_by_another_app(self):
        asked = []

        def taken(combo):
            asked.append(combo)
            return False

        result = he.check("ctrl+alt+d", "prompt", CURRENT, taken)
        assert asked == ["ctrl+alt+d"]
        assert result.problem == "Strg+Alt+D wird schon von einer anderen App benutzt"

    def test_the_current_combination_is_not_probed(self):
        """It is Hams' own; probing it while registered would always say "taken"."""
        result = he.check("ctrl+shift+space", "transcript", CURRENT, lambda combo: pytest.fail("probed"))
        assert result.ok and result.combo == "ctrl+shift+space"

    def test_rules_come_before_the_probe(self):
        he.check("ctrl+v", "prompt", CURRENT, lambda combo: pytest.fail("probed"))

    def test_english_messages(self, english):
        assert he.check("a", "prompt", CURRENT).problem == "Add Ctrl, Alt or Win; only F keys work on their own"
