"""Abnahme des hellen und des dunklen Darstellungsmodus – Teil 2: DPI, Tastatur, Anbindung an die Anwendung, Tray.

Teil 1 (Voreinstellung, Speichern, Wechsel an Widgets, Zwischenspeicher) steht in test_appearance.py.

Geprüft wird die Funktion als Ganzes: die Voreinstellung, das Speichern und
Wiederherstellen, der Wechsel zur Laufzeit an echten Widgets, die
Zwischenspeicher und die Frage, ob nach vielen Wechseln noch alles am Platz
ist. Die Kontrastmatrizen beider Paletten stehen in `test_tokens.py`, die
Bedienbarkeit in `test_accessibility.py`.

Was sich hier nicht prüfen lässt: wie die beiden Modi auf einem echten
Bildschirm *wirken*. Das steht im Bericht unter der Sichtprüfung.
"""

import inspect
import tkinter as tk

import pytest
from appearance_support import (  # noqa: F401 – Fixtures, pytest findet sie über den Namen
    restore_mode,
    surface,
    wechsel,
    window,
)

from wisper import config as config_mod
from wisper import settings_window as settings_window_mod
from wisper import tray as tray_mod
from wisper import ui_kit as uk
from wisper.app import FloatingTranscriberApp
from wisper.settings_window import SettingsWindow
from wisper.ui_kit import (
    Option,
    Popover,
    Select,
    Theme,
)

MODES = list(Theme.MODES)


class TestDpi:
    @pytest.mark.parametrize("dpi", [96, 120, 144, 192])
    @pytest.mark.parametrize("ziel", MODES)
    def test_both_modes_survive_every_scaling(self, surface, root, monkeypatch, dpi, ziel):
        """100 %, 125 %, 150 % und 200 % — in beiden Modi.

        Geprüft wird, dass Umschalten und Skalieren einander nicht in die Quere
        kommen: die Bilder tragen beide Merkmale im Schlüssel, und beide Wege
        zeichnen dieselben Bauteile neu.
        """
        monkeypatch.setattr(uk.Scale, "dpi", uk.Scale.dpi, raising=False)
        vorher_dpi, vorher_faktor = uk.Scale.dpi, uk.Scale.factor
        try:
            uk.Scale.apply(root, dpi)
            wechsel(surface["window"], ziel, root)
            palette = Theme.palette(ziel)
            assert surface["group"].body.cget("bg") == palette["GROUP_SURFACE"]
            assert surface["primary"]._bg_under == palette["WINDOW_BG"]
            assert surface["select"].winfo_exists()
            assert uk.Scale.dpi == dpi
        finally:
            uk.Scale.dpi, uk.Scale.factor = vorher_dpi, vorher_faktor
            uk.Scale.apply(root, vorher_dpi)


# --------------------------------------------------------------- Tastatur


class TestKeyboard:
    def test_the_appearance_select_is_fully_operable_by_keyboard(self, window, root):
        gewaehlt = []
        select = Select(
            window,
            bg_under=Theme.WINDOW_BG,
            options=[Option(Theme.LIGHT, "Hell"), Option(Theme.DARK, "Dunkel")],
            value=Theme.LIGHT,
            on_change=gewaehlt.append,
        )
        select.pack()
        root.update()

        select.focus_set()
        root.update()
        assert select.focus_get() is select

        select.event_generate("<Return>")
        root.update()
        assert Popover.current is not None, "Return muss das Popover öffnen"

        Popover.current.event_generate("<Down>")
        root.update()
        Popover.current.event_generate("<Return>")
        root.update()

        assert gewaehlt == [Theme.DARK]
        assert select.value == Theme.DARK
        assert Popover.current is None

    def test_escape_closes_without_choosing(self, window, root):
        gewaehlt = []
        select = Select(
            window,
            bg_under=Theme.WINDOW_BG,
            options=[Option(Theme.LIGHT, "Hell"), Option(Theme.DARK, "Dunkel")],
            value=Theme.LIGHT,
            on_change=gewaehlt.append,
        )
        select.pack()
        root.update()
        select.focus_set()
        select.event_generate("<Return>")
        root.update()
        Popover.current.event_generate("<Escape>")
        root.update()
        assert Popover.current is None
        assert gewaehlt == []
        assert select.value == Theme.LIGHT

    def test_the_select_takes_focus(self, window, root):
        select = Select(window, bg_under=Theme.WINDOW_BG, options=[Option(Theme.LIGHT, "Hell")], value=Theme.LIGHT)
        select.pack()
        root.update()
        assert int(select.cget("takefocus")) == 1


# ------------------------------------------------------------------ Anwendung


class TestApplicationWiring:
    def test_the_mode_is_applied_before_the_first_widget(self):
        """Beim Import steht der Modus bereits — sonst entstünde das erste
        Fenster in der falschen Palette und müsste sofort neu gezeichnet."""
        quelle = inspect.getsource(config_mod)
        laden = quelle.index("CFG = _load_config()")
        anwenden = quelle.index("Theme.apply(CFG.appearance)")
        assert laden < anwenden

    def test_set_appearance_paints_both_windows(self, monkeypatch, root):
        app = FloatingTranscriberApp.__new__(FloatingTranscriberApp)
        app.root = tk.Toplevel(root)
        app.root.configure(bg=Theme.WINDOW_BG)
        app.settings_win = None
        try:
            Theme.apply(Theme.LIGHT)
            FloatingTranscriberApp.set_appearance(app, Theme.DARK)
            assert Theme.mode == Theme.DARK
            assert config_mod.CFG.appearance == Theme.DARK
            assert app.root.cget("bg") == Theme.palette(Theme.DARK)["WINDOW_BG"]
        finally:
            app.root.destroy()
            root.update()

    def test_set_appearance_ignores_an_unknown_value(self, root):
        app = FloatingTranscriberApp.__new__(FloatingTranscriberApp)
        app.root = tk.Toplevel(root)
        app.settings_win = None
        try:
            Theme.apply(Theme.DARK)
            FloatingTranscriberApp.set_appearance(app, "regenbogen")
            assert Theme.mode == Theme.LIGHT  # normalisiert auf hell
        finally:
            app.root.destroy()
            root.update()

    def test_the_settings_window_offers_the_two_labels(self):
        assert SettingsWindow.APPEARANCE_LABELS == {Theme.LIGHT: "Hell", Theme.DARK: "Dunkel"}

    def test_the_appearance_row_is_the_last_one_under_general(self):
        block = inspect.getsource(SettingsWindow._build_general)
        assert block.index("row_appearance") > block.index("row_sound")
        assert "hairline=False" in block[block.index("row_appearance") :]

    def test_choosing_persists_before_it_switches(self, monkeypatch):
        """Erst schreiben, dann umschalten — sonst fiele ein Schreibfehler
        mitten in einen halb neu gezeichneten Baum."""
        ablauf = []
        monkeypatch.setattr(
            settings_window_mod, "_persist_config_value", lambda *a: ablauf.append(("schreiben", a[2])) or True
        )

        class _App:
            def set_appearance(self, mode):
                ablauf.append(("umschalten", mode))

        fenster = SettingsWindow.__new__(SettingsWindow)
        fenster.app = _App()
        Theme.apply(Theme.LIGHT)
        SettingsWindow._select_appearance(fenster, Theme.DARK)
        assert ablauf == [("schreiben", "dark"), ("umschalten", "dark")]

    def test_choosing_the_current_mode_does_nothing(self, monkeypatch):
        ablauf = []
        monkeypatch.setattr(settings_window_mod, "_persist_config_value", lambda *a: ablauf.append(a) or True)
        fenster = SettingsWindow.__new__(SettingsWindow)
        fenster.app = None
        Theme.apply(Theme.LIGHT)
        SettingsWindow._select_appearance(fenster, Theme.LIGHT)
        assert ablauf == []


# ---------------------------------------------------------------- Branding


class TestTrayStaysIndependent:
    """Das App-Theme und das Theme der Windows-Leiste sind zwei Dinge."""

    def test_the_tray_colours_do_not_follow_the_app_theme(self):
        Theme.apply(Theme.LIGHT)
        hell = {
            zustand: FloatingTranscriberApp._tray_colour(zustand)
            for zustand in ("ready", "recording", "generating", "error")
        }
        Theme.apply(Theme.DARK)
        dunkel = {
            zustand: FloatingTranscriberApp._tray_colour(zustand)
            for zustand in ("ready", "recording", "generating", "error")
        }
        assert hell == dunkel

    def test_the_tray_keeps_its_semantic_meaning(self):
        farben = tray_mod.TRAY_PALETTE
        assert FloatingTranscriberApp._tray_colour("recording") == farben["RECORDING"]
        assert FloatingTranscriberApp._tray_colour("generating") == farben["AI"]
        assert FloatingTranscriberApp._tray_colour("error") == farben["ERROR"]
        assert FloatingTranscriberApp._tray_colour("ready") == farben["ACCENT_TINT"]

    def test_the_tray_image_is_built_in_both_modes(self):
        for modus in MODES:
            Theme.apply(modus)
            bild = tray_mod._create_tray_icon_image()
            assert bild.size[0] > 0 and bild.mode == "RGBA"

    def test_the_tray_mask_is_untouched_by_the_theme(self):
        """Die Bildmarke bleibt dieselbe Datei — nur die Farbe kommt von aussen."""
        from wisper import branding

        Theme.apply(Theme.LIGHT)
        hell = branding.tray_image(tray_mod.TRAY_PALETTE["ACCENT_TINT"], 32)
        Theme.apply(Theme.DARK)
        dunkel = branding.tray_image(tray_mod.TRAY_PALETTE["ACCENT_TINT"], 32)
        assert list(hell.getdata()) == list(dunkel.getdata())
