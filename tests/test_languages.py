"""The seven interface languages: complete catalogs, the choice, right to left for Arabic, fonts with its letters."""

import string
import sys
from types import SimpleNamespace

import kit as uk
import pytest

from hams import bootstrap, dictation_language, i18n, settings_sections
from hams.messages_de import MESSAGES as DE


def placeholders(text: str) -> set[str]:
    return {name for _literal, name, _spec, _conv in string.Formatter().parse(text) if name is not None}


TRANSLATIONS = [language for language in i18n.SUPPORTED if language != "de"]


class TestCatalogs:
    @pytest.mark.parametrize("language", TRANSLATIONS)
    def test_exactly_the_keys_of_german(self, language):
        catalog = i18n.CATALOGS[language]
        assert sorted(set(DE) - set(catalog)) == [], f"missing in {language}"
        assert sorted(set(catalog) - set(DE)) == [], f"only in {language}"

    @pytest.mark.parametrize("language", TRANSLATIONS)
    def test_the_placeholders_of_german(self, language):
        catalog = i18n.CATALOGS[language]
        wrong = [key for key in DE if placeholders(catalog[key]) != placeholders(DE[key])]
        assert wrong == []

    @pytest.mark.parametrize("language", TRANSLATIONS)
    def test_nothing_empty_and_every_message_formats(self, language):
        catalog = i18n.CATALOGS[language]
        for key, text in catalog.items():
            assert text.strip(), key
            text.format(**dict.fromkeys(placeholders(text), "x"))

    def test_language_names_are_shown_in_their_own_language_everywhere(self):
        names = {
            "de": "Deutsch",
            "en": "English",
            "ru": "Русский",
            "es": "Español",
            "fr": "Français",
            "ar": "العربية",
            "tr": "Türkçe",
        }
        for catalog in i18n.CATALOGS.values():
            for language, name in names.items():
                assert catalog[f"language.{language}"] == name
        assert DE["language.auto"] == "Automatisch"

    def test_the_choice_lists_all_languages_in_order(self):
        options = settings_sections.language_options()
        assert [option.value for option in options] == ["auto", "de", "en", "ru", "es", "fr", "ar", "tr"]
        assert [option.label for option in options][:3] == ["Automatisch", "Deutsch", "English"]

    def test_arabic_reads_from_an_arabic_word(self):
        """Right to left, a message that starts with a Latin name would put that name on the wrong side."""
        exceptions = {key for key in DE if key.startswith(("language.", "dictation.", "key."))}
        exceptions |= {"setup.cleanup.service", "settings.download.failed", "number.decimal_separator"}
        catalog = i18n.CATALOGS["ar"]
        latin = [
            key
            for key, text in catalog.items()
            if key not in exceptions
            and not i18n._RTL_LETTER.match(text.lstrip("{"))
            and not i18n._RTL_LETTER.search(text.split()[0])
        ]
        assert latin == []


class TestResolution:
    @pytest.mark.parametrize(
        ("langid", "expected"),
        [
            (0x0419, "ru"),
            (0x0C0A, "es"),
            (0x080A, "es"),
            (0x040C, "fr"),
            (0x0401, "ar"),
            (0x0C01, "ar"),
            (0x041F, "tr"),
            (0x0411, "en"),
        ],
    )
    def test_windows_ui_language(self, monkeypatch, langid, expected):
        kernel32 = SimpleNamespace(GetUserDefaultUILanguage=lambda: langid)
        monkeypatch.setattr("ctypes.windll", SimpleNamespace(kernel32=kernel32), raising=False)
        assert i18n._windows_ui_language() == expected

    @pytest.mark.parametrize(
        ("lang", "expected"),
        [
            ("ru_RU.UTF-8", "ru"),
            ("es_MX.UTF-8", "es"),
            ("fr_CA.UTF-8", "fr"),
            ("tr_TR.UTF-8", "tr"),
            ("ar_EG.UTF-8", "en"),
            ("ja_JP.UTF-8", "en"),
        ],
    )
    def test_linux_locale(self, lang, expected):
        """Arabic is not picked on Linux: Tk there draws it neither shaped nor right to left."""
        assert i18n.resolve_language("auto", platform="linux", environ={"LANG": lang}) == expected

    def test_explicit_settings(self):
        for language in i18n.SUPPORTED:
            assert i18n.resolve_language(language) == language
        assert i18n.normalise_setting("AR") == "ar"
        assert i18n.normalise_setting("ja") == "auto"


class TestRightToLeft:
    RLE = "‫"

    def test_each_line_and_each_arabic_stretch_is_embedded(self):
        text = "يتوفر Hams 1.3.4 الآن\nسطر"
        expected = f"{self.RLE}يتوفر {self.RLE}Hams 1.3.4 {self.RLE}الآن\n{self.RLE}سطر"
        assert i18n.right_to_left(text) == expected

    def test_latin_words_stay_together(self):
        assert i18n.right_to_left("Hams Dictate 1.3.4") == self.RLE + "Hams Dictate 1.3.4"

    def test_lookups_in_arabic_are_embedded_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        assert i18n.t_for("ar", "tray.quit").startswith(self.RLE)
        assert not i18n.t_for("de", "tray.quit").startswith(self.RLE)
        monkeypatch.setattr(sys, "platform", "linux")
        assert not i18n.t_for("ar", "tray.quit").startswith(self.RLE)

    def test_direction(self):
        assert i18n.is_rtl("ar") and not i18n.is_rtl("tr")
        assert not i18n.is_rtl()  # the tests speak German


@pytest.fixture
def arabic(root):
    bootstrap.apply_language("ar", root)
    yield
    bootstrap.apply_language("de", root)


class TestMirroredLayout:
    def test_helpers_mirror_only_right_to_left(self, arabic):
        assert uk.Writing.rtl
        assert (uk.mirror_side("left"), uk.mirror_side("top")) == ("right", "top")
        assert (uk.mirror_anchor("w"), uk.mirror_anchor("nw"), uk.mirror_anchor("ew")) == ("e", "ne", "we")
        assert uk.mirror_pad((1, 2)) == (2, 1) and uk.text_justify() == "right"

    def test_left_to_right_is_unchanged(self):
        assert not uk.Writing.rtl
        assert (uk.mirror_side("left"), uk.mirror_anchor("w"), uk.mirror_pad((1, 2)), uk.text_justify()) == (
            "left",
            "w",
            (1, 2),
            "left",
        )

    def test_a_setting_row_has_its_text_right_and_its_control_left(self, arabic, root):
        import tkinter as tk

        frame = tk.Frame(root)
        row = uk.SettingRow(frame, "عنوان", variant="description", description="وصف")
        row.set_control(uk.Switch(frame, bg_under=uk.Theme.GROUP_SURFACE, value=True, command=lambda value: None))
        assert row._text_box.grid_info()["column"] == 1
        assert row._control.grid_info()["column"] == 0
        assert row.title_label.cget("anchor") == "e" and row.sub_label.cget("justify") == "right"
        frame.destroy()

    def test_fonts_with_arabic_letters(self, arabic):
        families = {uk.font(role).actual("family") for role in ("row_title", "description", "button", "window_title")}
        if sys.platform == "win32":
            assert families <= {"Segoe UI", "Segoe UI Semibold"}

    def test_settings_and_glossary_build_mirrored(self, arabic, live_app):
        live_app._open_settings()
        live_app.root.update()
        general = live_app.settings_win.general
        assert general.row_update.title_label.cget("anchor") == "e"
        assert live_app.settings_win._tabs.buttons[0].winfo_x() > live_app.settings_win._tabs.buttons[1].winfo_x()
        live_app.settings_win.close()


class TestDictation:
    @pytest.mark.parametrize("language", ["tr", "ar"])
    def test_turkish_and_arabic(self, language):
        assert language in dictation_language.SETTINGS
        frame = dictation_language.hotword_frame(language)
        assert frame.count("{}") == 1 and frame.format("Hams").endswith("Hams.")
        assert dictation_language.ENGLISH_NAMES[language] in ("Turkish", "Arabic")
        assert "Turkish" in dictation_language.cleanup_template("tr", dictation_language.DEFAULT_CLEANUP_PROMPT) or (
            language == "ar"
        )

    def test_frames_are_written_in_their_language(self):
        assert dictation_language.HOTWORD_FRAMES["tr"].startswith("Türkçe")
        assert i18n._RTL_LETTER.match(dictation_language.HOTWORD_FRAMES["ar"])

    def test_german_is_unchanged(self):
        """The thesis measurements depend on it word for word."""
        assert dictation_language.HOTWORD_FRAMES["de"] == "Ein deutscher Text. Namen und Begriffe darin: {}."

    @pytest.mark.parametrize(("ui", "dictation"), [("tr", "tr"), ("ar", "ar"), ("ru", "auto"), ("de", "de")])
    def test_the_default_follows_the_interface(self, ui, dictation):
        assert dictation_language.default_setting(ui) == dictation
