"""Zerlegen der Kurzbefehle fuer `RegisterHotKey`.

Die Anmeldung selbst wird hier nicht ausprobiert: sie belegte die Kombination
fuer den ganzen Rechner, auch fuer ein gerade laufendes Hams.
"""

from types import SimpleNamespace

import pytest

from hams import system_hotkeys
from hams.system_hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    MOD_WIN,
    PROBE_ID,
    SystemHotkeys,
    parse_combo,
)


class TestParseCombo:
    def test_the_two_default_shortcuts(self):
        assert parse_combo("ctrl+shift+space") == (MOD_CONTROL | MOD_SHIFT, 0x20)
        assert parse_combo("ctrl+shift+alt+space") == (MOD_CONTROL | MOD_SHIFT | MOD_ALT, 0x20)

    def test_letters_digits_and_function_keys(self):
        assert parse_combo("win+h") == (MOD_WIN, ord("H"))
        assert parse_combo("alt+1") == (MOD_ALT, ord("1"))
        assert parse_combo("ctrl+F9") == (MOD_CONTROL, 0x78)

    def test_spaces_and_case_do_not_matter(self):
        assert parse_combo(" Ctrl + Shift + Space ") == (MOD_CONTROL | MOD_SHIFT, 0x20)

    def test_anything_ambiguous_is_left_to_the_library(self):
        for combo in ("ctrl+shift", "ctrl+a+b", "ctrl+ä", "ctrl+", "ctrl+shift+space, a"):
            assert parse_combo(combo) is None


class FakeUser32:
    """`RegisterHotKey` ohne echte Anmeldung; `taken` gilt als von einem anderen Programm belegt."""

    def __init__(self, taken=()):
        self.taken = set(taken)
        self.calls = []

    def RegisterHotKey(self, hwnd, hid, mods, vk):  # noqa: N802 - Name der Windows-Funktion
        self.calls.append(("register", hid, mods, vk))
        return (mods & ~MOD_NOREPEAT, vk) not in self.taken

    def UnregisterHotKey(self, hwnd, hid):  # noqa: N802
        self.calls.append(("unregister", hid))
        return True


class TestAvailable:
    @pytest.fixture
    def user32(self, monkeypatch):
        fake = FakeUser32(taken={(MOD_CONTROL | MOD_ALT, ord("D"))})
        monkeypatch.setattr(system_hotkeys, "ctypes", SimpleNamespace(windll=SimpleNamespace(user32=fake)))
        return fake

    def test_a_free_combination_is_registered_and_released_at_once(self, user32):
        assert SystemHotkeys.available("ctrl+alt+p") is True
        assert user32.calls == [
            ("register", PROBE_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("P")),
            ("unregister", PROBE_ID),
        ]

    def test_a_taken_combination_is_reported_and_nothing_is_released(self, user32):
        assert SystemHotkeys.available("ctrl+alt+d") is False
        assert [call[0] for call in user32.calls] == ["register"]

    def test_an_unparsable_combination_is_never_available(self, user32):
        assert SystemHotkeys.available("ctrl+ä") is False
        assert user32.calls == []
