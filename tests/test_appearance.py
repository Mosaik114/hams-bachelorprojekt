"""Abnahme des hellen und des dunklen Darstellungsmodus.

Geprüft wird die Funktion als Ganzes: die Voreinstellung, das Speichern und
Wiederherstellen, der Wechsel zur Laufzeit an echten Widgets, die
Zwischenspeicher und die Frage, ob nach vielen Wechseln noch alles am Platz
ist. Die Kontrastmatrizen beider Paletten stehen in `test_tokens.py`, die
Bedienbarkeit in `test_accessibility.py`.

Was sich hier nicht prüfen lässt: wie die beiden Modi auf einem echten
Bildschirm *wirken*. Das steht im Bericht unter der Sichtprüfung.
"""

import tkinter as tk

import pytest

from conftest import fokus_erzwingen

from wisper import main as wm
from wisper import ui_kit as uk
from wisper.ui_kit import (GroupSurface, IconButton, Option, Popover, PrimaryButton,
                           ScrollArea, SectionHeader, SecondaryButton, Select,
                           SettingRow, SignalMeter, StatusDot, Switch, Theme)

MODES = list(Theme.MODES)


@pytest.fixture(autouse=True)
def restore_mode():
    """Kein Test darf den Modus für den nächsten stehen lassen.

    Die anderen Testdateien lesen ihre Farben teils beim Import; ein hier
    hängengebliebener Modus verschöbe sie unbemerkt.
    """
    before = Theme.mode
    yield
    Theme.apply(before)


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.geometry("420x420+130+130")
    win.configure(bg=Theme.WINDOW_BG)
    win.deiconify()
    win.lift()
    fokus_erzwingen(win, root)
    yield win
    win.destroy()
    root.update()


@pytest.fixture
def surface(window, root):
    """Ein Ausschnitt, der jedes Bauteil einmal enthält — wie die Einstellungen.

    Bewusst aus denselben Bausteinen zusammengesetzt wie das echte Fenster:
    Rollbereich, Abschnitt, Gruppenfläche, Zeilen mit Schalter und Auswahlfeld,
    Schaltflächen, Statuspunkt und Pegel. Das vollständige `SettingsWindow`
    liesse sich ohne Audio und Tray nicht bauen.
    """
    teile = {}
    scroll = ScrollArea(window, bg_under=Theme.WINDOW_BG)
    scroll.pack(fill="both", expand=True)
    teile["scroll"] = scroll

    teile["section"] = SectionHeader(scroll.body, "Allgemein", bg_under=Theme.WINDOW_BG)
    teile["section"].pack(fill="x")

    group = GroupSurface(scroll.body, bg_under=Theme.WINDOW_BG)
    group.pack(fill="x")
    teile["group"] = group

    row = SettingRow(group.body, "Sound", variant="description",
                     description="Kurzer Ton", interactive=True)
    row.pack(fill="x")
    teile["row"] = row
    switch = Switch(group.body, bg_under=Theme.GROUP_SURFACE, value=True,
                    command=lambda _v: None)
    row.set_control(switch)
    teile["switch"] = switch

    status_row = SettingRow(group.body, "Mikrofon", variant="status")
    status_row.pack(fill="x")
    teile["status_row"] = status_row
    teile["dot"] = status_row.status_dot

    select_row = SettingRow(group.body, "Darstellung", variant="description",
                            description="Hell oder dunkel", hairline=False)
    select_row.pack(fill="x")
    select = Select(group.body, bg_under=Theme.GROUP_SURFACE,
                    options=[Option("light", "Hell"), Option("dark", "Dunkel")],
                    value="light")
    # Wie im echten Fenster: ohne feste logische Breite bliebe das Auswahlfeld
    # bei seiner Eigenbreite von 2 px stehen — es traegt nur ein Bild, keinen Text.
    uk.keep_logical_width(select, 150)
    select_row.set_control(select)
    teile["select"] = select

    teile["primary"] = PrimaryButton(scroll.body, "Aufnahme", lambda: None,
                                     bg_under=Theme.WINDOW_BG)
    teile["primary"].pack(fill="x")
    teile["secondary"] = SecondaryButton(scroll.body, "Abbrechen", lambda: None,
                                         bg_under=Theme.WINDOW_BG, icon_name="stop")
    teile["secondary"].pack(fill="x")
    teile["icon_button"] = IconButton(scroll.body, "settings", lambda: None,
                                      bg_under=Theme.WINDOW_BG)
    teile["icon_button"].pack()
    teile["icon_button"].set_hover_colour(Theme.ERROR)
    teile["meter"] = SignalMeter(scroll.body, bg_under=Theme.WINDOW_BG)
    teile["meter"].pack(fill="x")
    teile["meter"].set_level(0.7)

    for _ in range(3):
        root.update_idletasks()
        root.update()
    teile["window"] = window
    yield teile


def wechsel(window, mode, root):
    """Modus setzen und den Baum einfärben — genau wie die Anwendung es tut."""
    Popover.dismiss_current()
    Theme.apply(mode)
    besucht = uk.apply_theme_tree(window)
    root.update()
    return besucht


# ------------------------------------------------------------- Voreinstellung


class TestDefault:
    def test_light_is_the_default_mode(self):
        assert Theme.DEFAULT_MODE == Theme.LIGHT

    def test_a_config_without_the_key_gives_light(self):
        """Der Rückwärtskompatibilitätsfall: bestehende Nutzerdateien.

        Keine Migration, kein Fehler — es gilt schlicht hell.
        """
        cfg = wm.Config()
        wm._apply_toml(cfg, {"ui": {"sound_feedback": True}})
        assert cfg.appearance == Theme.LIGHT

    def test_an_empty_file_gives_light(self):
        cfg = wm.Config()
        wm._apply_toml(cfg, {})
        assert cfg.appearance == Theme.LIGHT

    @pytest.mark.parametrize("wert", ["", "Hell", "system", "auto", None, 3, "  "])
    def test_an_unknown_value_falls_back_to_light(self, wert):
        """Ein Tippfehler darf nie ein Fenster ohne Farben ergeben."""
        assert Theme.normalise(wert) == Theme.LIGHT

    @pytest.mark.parametrize("wert,erwartet", [
        ("dark", Theme.DARK), ("DARK", Theme.DARK), (" dark ", Theme.DARK),
        ("light", Theme.LIGHT), ("Light", Theme.LIGHT),
    ])
    def test_known_values_survive_normalisation(self, wert, erwartet):
        assert Theme.normalise(wert) == erwartet

    def test_the_repository_default_is_light(self):
        from wisper.main import DEFAULT_CONFIG_PATH

        text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        assert 'appearance = "light"' in text

    def test_the_windows_theme_is_never_consulted(self):
        """Ausdrücklich keine Systemabfrage — der Nutzer entscheidet selbst.

        Geprüft am Quelltext: die beiden Registry-Werte, über die Windows sein
        eigenes Theme meldet, dürfen nirgends vorkommen.
        """
        text = (open(uk.__file__, encoding="utf-8").read()
                + open(wm.__file__, encoding="utf-8").read())
        for schluessel in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            assert schluessel not in text, schluessel


# ------------------------------------------------------------------ Persistenz


class TestPersistence:
    @pytest.fixture(autouse=True)
    def restore_file(self):
        vorher = wm.CFG.appearance
        yield
        wm._persist_config_value("ui", "appearance", vorher)

    def test_dark_survives_a_restart(self):
        """Hell -> Dunkel, Anwendung neu starten -> Dunkel."""
        assert wm._persist_config_value("ui", "appearance", "dark") is True
        assert wm._load_config().appearance == Theme.DARK

    def test_light_survives_a_restart(self):
        wm._persist_config_value("ui", "appearance", "dark")
        assert wm._persist_config_value("ui", "appearance", "light") is True
        assert wm._load_config().appearance == Theme.LIGHT

    def test_the_value_lands_in_the_ui_section(self):
        wm._persist_config_value("ui", "appearance", "dark")
        text = wm.CONFIG_PATH.read_text(encoding="utf-8")
        abschnitt = text[text.index("[ui]"):]
        ende = abschnitt.find("\n[", 1)
        abschnitt = abschnitt if ende < 0 else abschnitt[:ende]
        assert 'appearance = "dark"' in abschnitt

    def test_a_broken_value_in_the_file_still_starts_light(self):
        wm._persist_config_value("ui", "appearance", "dunkelblau")
        assert wm._load_config().appearance == Theme.LIGHT

    def test_writing_does_not_disturb_the_rest_of_the_file(self):
        vorher = wm._load_config()
        wm._persist_config_value("ui", "appearance", "dark")
        nachher = wm._load_config()
        assert nachher.sound_feedback == vorher.sound_feedback
        assert nachher.model_size == vorher.model_size
        assert nachher.hotkey_transcript == vorher.hotkey_transcript


# ----------------------------------------------------------------- Palette


class TestPaletteSwitch:
    def test_apply_reports_whether_something_changed(self):
        Theme.apply(Theme.LIGHT)
        assert Theme.apply(Theme.DARK) is True
        assert Theme.apply(Theme.DARK) is False

    def test_every_token_moves(self):
        Theme.apply(Theme.DARK)
        for name, wert in Theme.palette(Theme.DARK).items():
            assert getattr(Theme, name) == wert
        Theme.apply(Theme.LIGHT)
        for name, wert in Theme.palette(Theme.LIGHT).items():
            assert getattr(Theme, name) == wert

    def test_remap_moves_a_colour_to_the_same_token(self):
        Theme.apply(Theme.LIGHT)
        hell = Theme.WINDOW_BG
        Theme.apply(Theme.DARK)
        assert Theme.remap(hell) == Theme.palette(Theme.DARK)["WINDOW_BG"]

    def test_remap_leaves_a_current_colour_alone(self):
        Theme.apply(Theme.DARK)
        assert Theme.remap(Theme.GROUP_SURFACE) == Theme.GROUP_SURFACE

    def test_remap_is_repeatable(self):
        Theme.apply(Theme.DARK)
        einmal = Theme.remap(Theme.palette(Theme.LIGHT)["CONTROL_SURFACE"])
        assert Theme.remap(einmal) == einmal

    @pytest.mark.parametrize("fremd", ["SystemButtonFace", "#123456", "", None, 7])
    def test_remap_leaves_unknown_values_alone(self, fremd):
        assert Theme.remap(fremd) == fremd

    def test_callbacks_run_and_can_be_removed(self):
        laeufe = []
        Theme.register(lambda: laeufe.append(1))
        Theme.apply(Theme.DARK)
        assert laeufe == [1]
        Theme.unregister(Theme._callbacks[-1])
        Theme.apply(Theme.LIGHT)
        assert laeufe == [1]

    def test_a_broken_callback_does_not_stop_the_switch(self):
        def kaputt():
            raise RuntimeError("absichtlich")

        Theme.register(kaputt)
        try:
            assert Theme.apply(Theme.DARK) is True
            assert Theme.WINDOW_BG == Theme.palette(Theme.DARK)["WINDOW_BG"]
        finally:
            Theme.unregister(kaputt)


# ------------------------------------------------------------ Bauteile


class TestEveryComponentFollows:
    @pytest.mark.parametrize("ziel", MODES)
    def test_all_parts_carry_the_new_palette(self, surface, root, ziel):
        wechsel(surface["window"], ziel, root)
        palette = Theme.palette(ziel)
        assert surface["window"].cget("bg") == palette["WINDOW_BG"]
        assert surface["scroll"].cget("bg") == palette["WINDOW_BG"]
        assert surface["scroll"].canvas.cget("bg") == palette["WINDOW_BG"]
        assert surface["scroll"].body.cget("bg") == palette["WINDOW_BG"]
        assert surface["section"].cget("bg") == palette["WINDOW_BG"]
        assert surface["section"].cget("fg") == palette["TEXT_SECONDARY"]
        assert surface["group"].cget("bg") == palette["WINDOW_BG"]
        assert surface["group"].body.cget("bg") == palette["GROUP_SURFACE"]
        assert surface["row"].cget("bg") == palette["GROUP_SURFACE"]
        assert surface["row"].title_label.cget("fg") == palette["TEXT_PRIMARY"]
        assert surface["row"].sub_label.cget("fg") == palette["TEXT_TERTIARY"]
        assert surface["row"].hairline.cget("bg") == palette["HAIRLINE"]
        assert surface["switch"].cget("bg") == palette["GROUP_SURFACE"]
        assert surface["dot"].cget("bg") == palette["GROUP_SURFACE"]
        assert surface["select"].cget("bg") == palette["GROUP_SURFACE"]
        assert surface["primary"].cget("bg") == palette["WINDOW_BG"]
        assert surface["secondary"].cget("bg") == palette["WINDOW_BG"]
        assert surface["icon_button"].cget("bg") == palette["WINDOW_BG"]
        assert surface["meter"].cget("bg") == palette["WINDOW_BG"]

    @pytest.mark.parametrize("ziel", MODES)
    def test_the_remembered_underground_moves_too(self, surface, root, ziel):
        """`bg_under` ist gemerkter Zustand — ohne Umschlüsselung malten die
        Controls ihre Ecken weiter in der alten Farbe aus."""
        wechsel(surface["window"], ziel, root)
        palette = Theme.palette(ziel)
        assert surface["primary"]._bg_under == palette["WINDOW_BG"]
        assert surface["select"]._bg_under == palette["GROUP_SURFACE"]
        assert surface["switch"]._bg_under == palette["GROUP_SURFACE"]
        assert surface["dot"]._bg_under == palette["GROUP_SURFACE"]
        assert surface["meter"]._bg_under == palette["WINDOW_BG"]
        assert surface["scroll"]._bg_under == palette["WINDOW_BG"]

    @pytest.mark.parametrize("ziel", MODES)
    def test_state_colours_follow(self, surface, root, ziel):
        wechsel(surface["window"], ziel, root)
        palette = Theme.palette(ziel)
        assert surface["primary"].colours()[0] == palette["ACCENT_SURFACE"]
        assert surface["primary"].colours()[1] == palette["ON_ACCENT"]
        assert surface["icon_button"]._hover_colour == palette["ERROR"]
        assert surface["dot"]._colour == palette["TEXT_TERTIARY"]

    @pytest.mark.parametrize("ziel", MODES)
    def test_status_dot_shows_the_new_semantic_colour(self, surface, root, ziel):
        surface["dot"].set_status("error")
        root.update()
        wechsel(surface["window"], ziel, root)
        assert surface["dot"]._colour == Theme.palette(ziel)["ERROR"]

    @pytest.mark.parametrize("ziel", MODES)
    def test_a_disabled_row_keeps_its_dimmed_look(self, surface, root, ziel):
        surface["row"].set_enabled(False)
        root.update()
        wechsel(surface["window"], ziel, root)
        palette = Theme.palette(ziel)
        assert surface["row"].title_label.cget("fg") == palette["TEXT_TERTIARY"]
        assert surface["row"].sub_label.cget("fg") == palette["CONTROL_HOVER"]
        assert surface["row"].enabled is False

    @pytest.mark.parametrize("ziel", MODES)
    def test_a_hovered_row_keeps_its_highlight(self, surface, root, ziel):
        surface["row"]._on_enter()
        root.update()
        wechsel(surface["window"], ziel, root)
        assert surface["row"]._line.cget("bg") == Theme.palette(ziel)["CONTROL_SURFACE"]

    @pytest.mark.parametrize("ziel", MODES)
    def test_the_meter_redraws_its_segments(self, surface, root, ziel):
        wechsel(surface["window"], ziel, root)
        assert surface["meter"].segments > 0
        assert len(surface["meter"]._images) == surface["meter"].segments

    def test_no_widget_keeps_a_colour_of_the_other_palette(self, surface, root):
        """Die eigentliche Zusage: keine halb helle, halb dunkle Oberfläche."""
        for ziel in (Theme.DARK, Theme.LIGHT, Theme.DARK):
            wechsel(surface["window"], ziel, root)
            fremd = {wert.upper() for name, wert in
                     Theme.palette(Theme.LIGHT if ziel == Theme.DARK
                                   else Theme.DARK).items()}
            # Werte, die in beiden Paletten vorkommen, sind kein Befund.
            fremd -= {w.upper() for w in Theme.palette(ziel).values()}
            uebrig = []

            def pruefe(widget):
                for option in ("bg", "fg"):
                    try:
                        wert = str(widget.cget(option))
                    except (tk.TclError, TypeError):
                        continue
                    if wert.upper() in fremd:
                        uebrig.append((widget, option, wert))
                for kind in widget.winfo_children():
                    pruefe(kind)

            pruefe(surface["window"])
            assert not uebrig, f"{ziel}: {uebrig}"


# --------------------------------------------------------------- Popover


class TestPopover:
    def test_an_open_popover_is_closed_by_the_switch(self, surface, root):
        select = surface["select"]
        select.focus_set()
        root.update()
        select.open_popover()
        root.update()
        assert Popover.current is not None
        wechsel(surface["window"], Theme.DARK, root)
        assert Popover.current is None, "kein halb gefärbtes Toplevel stehen lassen"

    def test_the_trigger_gets_the_focus_back(self, surface, root):
        """Geprueft ueber `focus_lastfor()`, nicht ueber `focus_get()`.

        `focus_get()` fragt, wer im *ganzen* Programm gerade den Fokus hat —
        und liefert None, sobald ein anderes Fenster ihn an sich gezogen hat.
        In einem Testlauf mit vielen Fenstern ist das nicht vorhersagbar.
        `focus_lastfor()` beantwortet die Frage, um die es hier geht: Wer
        bekommt den Fokus, wenn dieses Fenster ihn erhaelt?
        """
        select = surface["select"]
        select.focus_set()
        root.update()
        select.open_popover()
        root.update()
        wechsel(surface["window"], Theme.DARK, root)
        assert Popover.current is None
        assert select.winfo_toplevel().focus_lastfor() is select

    def test_a_popover_opened_afterwards_is_in_the_new_palette(self, surface, root):
        wechsel(surface["window"], Theme.DARK, root)
        surface["select"].open_popover()
        root.update()
        popover = Popover.current
        try:
            assert popover.cget("bg") == Theme.palette(Theme.DARK)["HAIRLINE"]
        finally:
            Popover.dismiss_current()
            root.update()


# ------------------------------------------------------------ Zwischenspeicher


class TestCaches:
    def test_both_caches_are_empty_after_a_switch(self, surface, root):
        assert uk._img_cache, "der Aufbau muss Bilder erzeugt haben"
        Theme.apply(Theme.DARK)
        assert not uk._img_cache
        assert not uk._icon_cache

    def test_the_cache_key_already_carries_the_colour(self):
        """Zweite Sicherung: selbst ohne Leeren könnten sich Bilder nicht mischen."""
        uk._img_cache.clear()
        uk.surface(20, 20, 4, Theme.palette(Theme.LIGHT)["GROUP_SURFACE"])
        uk.surface(20, 20, 4, Theme.palette(Theme.DARK)["GROUP_SURFACE"])
        assert len(uk._img_cache) == 2

    def test_icons_change_colour_but_not_geometry(self):
        hell = uk.render_icon("settings", 32, Theme.palette(Theme.LIGHT)["TEXT_SECONDARY"])
        dunkel = uk.render_icon("settings", 32, Theme.palette(Theme.DARK)["TEXT_SECONDARY"])
        assert hell.size == dunkel.size
        # Gleiche Geometrie heisst: dieselben Bildpunkte sind deckend.
        assert list(hell.getchannel("A").getdata()) == list(dunkel.getchannel("A").getdata())
        assert list(hell.getdata()) != list(dunkel.getdata())

    def test_a_redrawn_control_uses_the_new_images(self, surface, root):
        vorher = surface["primary"]._image
        wechsel(surface["window"], Theme.DARK, root)
        assert surface["primary"]._image is not vorher


# ------------------------------------------------------------ Dauerbetrieb


class TestManySwitches:
    ROUNDEN = 10        # 10 Runden hell/dunkel = 20 Wechsel

    def test_twenty_switches_leave_nothing_behind(self, surface, root):
        fenster = surface["window"]
        kinder_vorher = self._zaehle(fenster)
        scale_vorher = len(uk.Scale._callbacks)
        theme_vorher = len(Theme._callbacks)

        for _ in range(self.ROUNDEN):
            wechsel(fenster, Theme.DARK, root)
            wechsel(fenster, Theme.LIGHT, root)

        assert self._zaehle(fenster) == kinder_vorher, "es sind Widgets dazugekommen"
        assert len(uk.Scale._callbacks) == scale_vorher
        assert len(Theme._callbacks) == theme_vorher
        assert uk.Animator.pending() == 0
        assert len(uk._img_cache) <= uk.CACHE_LIMIT

    def test_twenty_switches_keep_every_control_alive(self, surface, root):
        for _ in range(self.ROUNDEN):
            wechsel(surface["window"], Theme.DARK, root)
            wechsel(surface["window"], Theme.LIGHT, root)
        for name in ("scroll", "group", "row", "switch", "select", "primary",
                     "secondary", "icon_button", "meter", "dot"):
            assert surface[name].winfo_exists(), name
        assert surface["switch"].value is True
        assert surface["select"].value == "light"

    def test_twenty_switches_keep_the_scroll_position(self, surface, root):
        scroll = surface["scroll"]
        for _ in range(24):
            tk.Label(scroll.body, text="Fuellzeile", bg=Theme.GROUP_SURFACE).pack()
        root.update_idletasks()
        root.update()
        scroll.refresh()
        scroll.scroll_to(60)
        root.update()
        gemerkt = scroll.offset
        assert gemerkt > 0, "der Bereich muss wirklich rollbar sein"

        for _ in range(self.ROUNDEN):
            for ziel in (Theme.DARK, Theme.LIGHT):
                Popover.dismiss_current()
                Theme.apply(ziel)
                offset = scroll.offset
                uk.apply_theme_tree(surface["window"])
                scroll.scroll_to(offset)
                root.update()
        assert scroll.offset == gemerkt

    def test_the_window_does_not_resize(self, surface, root):
        fenster = surface["window"]
        vorher = (fenster.winfo_width(), fenster.winfo_height())
        for _ in range(self.ROUNDEN):
            wechsel(fenster, Theme.DARK, root)
            wechsel(fenster, Theme.LIGHT, root)
        assert (fenster.winfo_width(), fenster.winfo_height()) == vorher

    @staticmethod
    def _zaehle(widget) -> int:
        return 1 + sum(TestManySwitches._zaehle(k) for k in widget.winfo_children())


# ------------------------------------------------------------------- DPI


class TestDpi:
    @pytest.mark.parametrize("dpi", [96, 120, 144, 192])
    @pytest.mark.parametrize("ziel", MODES)
    def test_both_modes_survive_every_scaling(self, surface, root, monkeypatch, dpi,
                                              ziel):
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
        select = Select(window, bg_under=Theme.WINDOW_BG,
                        options=[Option(Theme.LIGHT, "Hell"), Option(Theme.DARK, "Dunkel")],
                        value=Theme.LIGHT, on_change=gewaehlt.append)
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
        select = Select(window, bg_under=Theme.WINDOW_BG,
                        options=[Option(Theme.LIGHT, "Hell"), Option(Theme.DARK, "Dunkel")],
                        value=Theme.LIGHT, on_change=gewaehlt.append)
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
        select = Select(window, bg_under=Theme.WINDOW_BG,
                        options=[Option(Theme.LIGHT, "Hell")], value=Theme.LIGHT)
        select.pack()
        root.update()
        assert int(select.cget("takefocus")) == 1


# ------------------------------------------------------------------ Anwendung


class TestApplicationWiring:
    def test_the_mode_is_applied_before_the_first_widget(self):
        """Beim Import steht der Modus bereits — sonst entstünde das erste
        Fenster in der falschen Palette und müsste sofort neu gezeichnet."""
        quelle = open(wm.__file__, encoding="utf-8").read()
        laden = quelle.index("CFG = _load_config()")
        anwenden = quelle.index("Theme.apply(CFG.appearance)")
        klasse = quelle.index("class FloatingTranscriberApp")
        assert laden < anwenden < klasse

    def test_set_appearance_paints_both_windows(self, monkeypatch, root):
        app = wm.FloatingTranscriberApp.__new__(wm.FloatingTranscriberApp)
        app.root = tk.Toplevel(root)
        app.root.configure(bg=Theme.WINDOW_BG)
        app.settings_win = None
        try:
            Theme.apply(Theme.LIGHT)
            wm.FloatingTranscriberApp.set_appearance(app, Theme.DARK)
            assert Theme.mode == Theme.DARK
            assert wm.CFG.appearance == Theme.DARK
            assert app.root.cget("bg") == Theme.palette(Theme.DARK)["WINDOW_BG"]
        finally:
            app.root.destroy()
            root.update()

    def test_set_appearance_ignores_an_unknown_value(self, root):
        app = wm.FloatingTranscriberApp.__new__(wm.FloatingTranscriberApp)
        app.root = tk.Toplevel(root)
        app.settings_win = None
        try:
            Theme.apply(Theme.DARK)
            wm.FloatingTranscriberApp.set_appearance(app, "regenbogen")
            assert Theme.mode == Theme.LIGHT      # normalisiert auf hell
        finally:
            app.root.destroy()
            root.update()

    def test_the_settings_window_offers_the_two_labels(self):
        assert wm.SettingsWindow.APPEARANCE_LABELS == {Theme.LIGHT: "Hell",
                                                       Theme.DARK: "Dunkel"}

    def test_the_appearance_row_is_the_last_one_under_general(self):
        quelle = open(wm.__file__, encoding="utf-8").read()
        block = quelle[quelle.index("def _build_general"):]
        block = block[:block.index("\n    def ", 1)]
        assert block.index("row_appearance") > block.index("Kurzbefehle")
        assert "hairline=False" in block[block.index("row_appearance"):]

    def test_choosing_persists_before_it_switches(self, monkeypatch):
        """Erst schreiben, dann umschalten — sonst fiele ein Schreibfehler
        mitten in einen halb neu gezeichneten Baum."""
        ablauf = []
        monkeypatch.setattr(wm, "_persist_config_value",
                            lambda *a: ablauf.append(("schreiben", a[2])) or True)

        class _App:
            def set_appearance(self, mode):
                ablauf.append(("umschalten", mode))

        fenster = wm.SettingsWindow.__new__(wm.SettingsWindow)
        fenster.app = _App()
        Theme.apply(Theme.LIGHT)
        wm.SettingsWindow._select_appearance(fenster, Theme.DARK)
        assert ablauf == [("schreiben", "dark"), ("umschalten", "dark")]

    def test_choosing_the_current_mode_does_nothing(self, monkeypatch):
        ablauf = []
        monkeypatch.setattr(wm, "_persist_config_value",
                            lambda *a: ablauf.append(a) or True)
        fenster = wm.SettingsWindow.__new__(wm.SettingsWindow)
        fenster.app = None
        Theme.apply(Theme.LIGHT)
        wm.SettingsWindow._select_appearance(fenster, Theme.LIGHT)
        assert ablauf == []


# ---------------------------------------------------------------- Branding


class TestTrayStaysIndependent:
    """Das App-Theme und das Theme der Windows-Leiste sind zwei Dinge."""

    def test_the_tray_colours_do_not_follow_the_app_theme(self):
        Theme.apply(Theme.LIGHT)
        hell = {zustand: wm.FloatingTranscriberApp._tray_colour(zustand)
                for zustand in ("ready", "recording", "generating", "error")}
        Theme.apply(Theme.DARK)
        dunkel = {zustand: wm.FloatingTranscriberApp._tray_colour(zustand)
                  for zustand in ("ready", "recording", "generating", "error")}
        assert hell == dunkel

    def test_the_tray_keeps_its_semantic_meaning(self):
        farben = wm.TRAY_PALETTE
        assert wm.FloatingTranscriberApp._tray_colour("recording") == farben["RECORDING"]
        assert wm.FloatingTranscriberApp._tray_colour("generating") == farben["AI"]
        assert wm.FloatingTranscriberApp._tray_colour("error") == farben["ERROR"]
        assert wm.FloatingTranscriberApp._tray_colour("ready") == farben["ACCENT_TINT"]

    def test_the_tray_image_is_built_in_both_modes(self):
        for modus in MODES:
            Theme.apply(modus)
            bild = wm._create_tray_icon_image()
            assert bild.size[0] > 0 and bild.mode == "RGBA"

    def test_the_tray_mask_is_untouched_by_the_theme(self):
        """Die Bildmarke bleibt dieselbe Datei — nur die Farbe kommt von aussen."""
        from wisper import branding

        Theme.apply(Theme.LIGHT)
        hell = branding.tray_image(wm.TRAY_PALETTE["ACCENT_TINT"], 32)
        Theme.apply(Theme.DARK)
        dunkel = branding.tray_image(wm.TRAY_PALETTE["ACCENT_TINT"], 32)
        assert list(hell.getdata()) == list(dunkel.getdata())
