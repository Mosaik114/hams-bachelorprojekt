"""Zerlegen der Kurzbefehle fuer `RegisterHotKey`.

Die Anmeldung selbst wird hier nicht ausprobiert: sie belegte die Kombination
fuer den ganzen Rechner, auch fuer ein gerade laufendes Hams.
"""

from wisper.system_hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, parse_combo


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
