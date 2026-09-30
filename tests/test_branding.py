"""Tests der Produktmarke Hams.

Zwei Dinge werden hier festgehalten: dass die Bildmarke wirklich aus den
mitgelieferten Vorlagen kommt (und nicht wieder ein gezeichneter Buchstabe
ist), und dass in der sichtbaren Oberfläche kein alter Produktname steht.
"""

import ast
import pathlib

import pytest
from PIL import Image

from wisper import branding
from wisper import main as wm

MARK_SIZES = (16, 20, 24, 32, 64)


class TestName:
    def test_the_visible_product_name(self):
        assert branding.PRODUCT_NAME == "Hams"

    def test_the_arabic_form(self):
        assert branding.PRODUCT_NAME_AR == "همس"

    def test_the_window_title_uses_it(self):
        source = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        assert 'self.root.title(branding.PRODUCT_NAME)' in source
        assert 'self.root.title("Wisper")' not in source

    def test_the_settings_title_uses_it(self):
        source = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        assert 'f"{branding.PRODUCT_NAME} — Einstellungen"' in source


class TestAssets:
    def test_the_asset_folder_ships_with_the_package(self):
        assert branding.ASSET_DIR.is_dir()
        assert branding.ASSET_DIR.parent.parent == wm.APP_DIR

    def test_the_windows_icon_exists(self):
        assert branding.icon_path() is not None

    def test_the_icon_carries_every_size_windows_asks_for(self):
        with Image.open(branding.icon_path()) as image:
            sizes = {size[0] for size in image.info.get("sizes", [])}
        for needed in (16, 20, 24, 32, 48, 256):
            assert needed in sizes, f"{needed} px fehlt im ICO"

    @pytest.mark.parametrize("size", sorted(branding._TRAY_MASKS))
    def test_every_tray_template_is_readable(self, size):
        path = branding.ASSET_DIR / branding._TRAY_MASKS[size]
        with Image.open(path) as image:
            assert image.size == (size, size)
            assert image.mode in ("RGBA", "LA", "P")

    def test_the_app_icon_is_readable(self):
        assert branding.app_icon_image(64) is not None


class TestTrayImage:
    @pytest.mark.parametrize("size", MARK_SIZES)
    def test_the_requested_size_comes_back(self, size):
        assert branding.tray_image("#0A84FF", size).size == (size, size)

    @pytest.mark.parametrize("size", MARK_SIZES)
    def test_the_shape_is_the_shipped_mark(self, size):
        """Die Geometrie stammt aus der Vorlage — nicht aus gezeichnetem Text."""
        image = branding.tray_image("#0A84FF", size)
        path = branding.ASSET_DIR / branding._TRAY_MASKS[size]
        with Image.open(path) as template:
            expected = template.convert("RGBA").getchannel("A")
        assert list(image.getchannel("A").getdata()) == list(expected.getdata())

    def test_the_colour_follows_the_state(self):
        for colour in ("#0A84FF", "#FF453A", "#BF5AF2"):
            image = branding.tray_image(colour, 32)
            pixels = [p for p in image.convert("RGBA").getdata() if p[3] > 250]
            assert pixels, "die Marke ist unsichtbar"
            expected = branding._hex_to_rgb(colour)
            assert all(p[:3] == expected for p in pixels)

    def test_different_states_look_different(self):
        ready = branding.tray_image("#0A84FF", 32).tobytes()
        recording = branding.tray_image("#FF453A", 32).tobytes()
        assert ready != recording

    def test_it_is_not_a_filled_disc(self):
        """Der alte Kreis mit weissem Buchstaben deckte fast die ganze Kachel."""
        alpha = branding.tray_image("#0A84FF", 32).getchannel("A")
        coverage = sum(1 for p in alpha.getdata() if p > 0) / (32 * 32)
        assert 0.2 < coverage < 0.7, f"Deckung {coverage:.0%} sieht nach Fläche aus"

    def test_a_size_between_the_templates_still_works(self):
        assert branding.tray_image("#0A84FF", 22).size == (22, 22)

    def test_a_size_beyond_the_templates_still_works(self):
        assert branding.tray_image("#0A84FF", 128).size == (128, 128)

    def test_the_application_asks_for_the_mark(self):
        image = wm._create_tray_icon_image("#0A84FF")
        assert image.size == branding.tray_image("#0A84FF", 64).size
        assert image.tobytes() == branding.tray_image("#0A84FF", 64).tobytes()

    @pytest.mark.parametrize("state,colour", [
        ("ready", "#0A84FF"), ("recording", None), ("error", None),
        ("generating", None),
    ])
    def test_every_state_produces_a_mark(self, state, colour):
        chosen = colour or wm.FloatingTranscriberApp._tray_colour(state)
        alpha = branding.tray_image(chosen, 32).getchannel("A")
        assert sum(1 for p in alpha.getdata() if p > 0) > 100


class TestNoOldLetter:
    def test_no_letter_is_drawn_any_more(self):
        """Frueher stand hier `draw.text(..., "W", ...)` als Symbol."""
        source = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        assert 'draw.text' not in source
        assert '"W"' not in source

    def test_the_tray_helper_delegates_to_the_brand(self):
        source = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        start = source.index("def _create_tray_icon_image")
        body = source[start:start + 700]
        assert "branding.tray_image" in body
        assert "ellipse" not in body


#: Wo Text sichtbar gesetzt wird — dieselben Wege wie in test_accessibility.
UI_KEYWORDS = {"text", "description", "value", "placeholder", "label", "meta",
               "trailing", "title", "tooltip", "hint", "detail", "summary"}
UI_CALLS = {"SettingRow", "SecondaryButton", "PrimaryButton", "IconButton",
            "SectionHeader", "Option", "_apply_state", "set_description",
            "set_status", "set_value"}
OLD_NAMES = ("Wisper", "Transcriber")


def visible_strings() -> list:
    found = []
    for path in sorted((wm.APP_DIR).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(
                func, "id", "")
            named = name in UI_CALLS or name in ("title", "Icon")
            for argument in (node.args if named else ()):
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    found.append((path.name, argument.value))
            for keyword in node.keywords:
                if not (named or keyword.arg in UI_KEYWORDS):
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(
                        keyword.value.value, str):
                    found.append((path.name, keyword.value.value))
    return found


class TestNoOldBrandInTheInterface:
    def test_there_is_something_to_check(self):
        assert len(visible_strings()) > 30

    @pytest.mark.parametrize("old", OLD_NAMES)
    def test_no_old_product_name_is_shown(self, old):
        offenders = [f"{name}: {text!r}" for name, text in visible_strings()
                     if old in text]
        assert not offenders, "\n".join(offenders)

    def test_the_tooltip_carries_the_new_name(self):
        source = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        assert "base = branding.PRODUCT_NAME" in source


class TestKeptInternally:
    """Was bewusst `Wisper` heisst und es bleiben soll."""

    def test_the_package_keeps_its_name(self):
        assert wm.APP_DIR.name == "wisper"

    def test_the_configuration_stays_where_it_is(self):
        """Ein Umzug nach %APPDATA%\\Hams waere eine eigene Migration."""
        source = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        assert '"Hams" / "config.toml"' not in source
        source = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        assert '"Wisper" / "config.toml"' in source

    def test_the_single_instance_lock_keeps_its_name(self):
        """Ein neuer Name liesse eine zweite Instanz neben einer alten starten."""
        assert "Wisper" in wm.MUTEX_NAME
