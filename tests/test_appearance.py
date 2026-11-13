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

import kit as uk
import pytest
from appearance_support import (  # noqa: F401 – Fixtures, pytest findet sie über den Namen
    restore_mode,
    surface,
    wechsel,
    window,
)
from kit import (
    Popover,
    Theme,
)

from hams import config as config_mod

MODES = list(Theme.MODES)


# ------------------------------------------------------------- Voreinstellung


class TestDefault:
    def test_light_is_the_default_mode(self):
        assert Theme.DEFAULT_MODE == Theme.LIGHT

    def test_a_config_without_the_key_gives_light(self):
        """Der Rückwärtskompatibilitätsfall: bestehende Nutzerdateien.

        Keine Migration, kein Fehler — es gilt schlicht hell.
        """
        cfg = config_mod.Config()
        config_mod._apply_toml(cfg, {"ui": {"sound_feedback": True}})
        assert cfg.appearance == Theme.LIGHT

    def test_an_empty_file_gives_light(self):
        cfg = config_mod.Config()
        config_mod._apply_toml(cfg, {})
        assert cfg.appearance == Theme.LIGHT

    @pytest.mark.parametrize("raw_value", ["", "Hell", "system", "auto", None, 3, "  "])
    def test_an_unknown_value_falls_back_to_light(self, raw_value):
        """Ein Tippfehler darf nie ein Fenster ohne Farben ergeben."""
        assert Theme.normalise(raw_value) == Theme.LIGHT

    @pytest.mark.parametrize(
        "raw_value,expected",
        [
            ("dark", Theme.DARK),
            ("DARK", Theme.DARK),
            (" dark ", Theme.DARK),
            ("light", Theme.LIGHT),
            ("Light", Theme.LIGHT),
        ],
    )
    def test_known_values_survive_normalisation(self, raw_value, expected):
        assert Theme.normalise(raw_value) == expected

    def test_the_repository_default_is_light(self):
        from hams.config import DEFAULT_CONFIG_PATH

        text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        assert 'appearance = "light"' in text


# ------------------------------------------------------------------ Persistenz


class TestPersistence:
    @pytest.fixture(autouse=True)
    def restore_file(self):
        before = config_mod.CFG.appearance
        yield
        config_mod._persist_config_value("ui", "appearance", before)

    def test_dark_survives_a_restart(self):
        """Hell -> Dunkel, Anwendung neu starten -> Dunkel."""
        assert config_mod._persist_config_value("ui", "appearance", "dark") is True
        assert config_mod._load_config().appearance == Theme.DARK

    def test_light_survives_a_restart(self):
        config_mod._persist_config_value("ui", "appearance", "dark")
        assert config_mod._persist_config_value("ui", "appearance", "light") is True
        assert config_mod._load_config().appearance == Theme.LIGHT

    def test_the_value_lands_in_the_ui_section(self):
        config_mod._persist_config_value("ui", "appearance", "dark")
        text = config_mod.CONFIG_PATH.read_text(encoding="utf-8")
        abschnitt = text[text.index("[ui]") :]
        ende = abschnitt.find("\n[", 1)
        abschnitt = abschnitt if ende < 0 else abschnitt[:ende]
        assert 'appearance = "dark"' in abschnitt

    def test_a_broken_value_in_the_file_still_starts_light(self):
        config_mod._persist_config_value("ui", "appearance", "dunkelblau")
        assert config_mod._load_config().appearance == Theme.LIGHT

    def test_writing_does_not_disturb_the_rest_of_the_file(self):
        before = config_mod._load_config()
        config_mod._persist_config_value("ui", "appearance", "dark")
        after = config_mod._load_config()
        assert after.sound_feedback == before.sound_feedback
        assert after.model_size == before.model_size
        assert after.hotkey_transcript == before.hotkey_transcript


# ----------------------------------------------------------------- Palette


class TestPaletteSwitch:
    def test_no_value_carries_two_roles(self):
        """`remap` erkennt Tokens am Wert. Ein Wert, der in der einen Palette
        Schrift und in der anderen Fenstergrund ist, bliebe beim Wechsel
        stehen — so blieben Überschriften nach Dunkel dunkel."""
        light, dark = Theme.palette(Theme.LIGHT), Theme.palette(Theme.DARK)
        for mode, here, there in (("hell", light, dark), ("dunkel", dark, light)):
            for name, value in here.items():
                for other, other_value in there.items():
                    # Geteilte Werte sind nur harmlos, wenn das andere Token in
                    # beiden Modi denselben Wert hat (#FFFFFF, Terrakotta).
                    # ON_SUCCESS wechselt bewusst Weiss gegen Dunkel und wird
                    # von keinem Widget als rohe Option getragen (test_tokens).
                    if "ON_SUCCESS" in (name, other):
                        continue
                    if other_value.upper() == value.upper():
                        assert here[other].upper() == value.upper(), f"{value}: {mode} {name}, anders {other}"

    def test_apply_reports_whether_something_changed(self):
        Theme.apply(Theme.LIGHT)
        assert Theme.apply(Theme.DARK) is True
        assert Theme.apply(Theme.DARK) is False

    def test_every_token_moves(self):
        Theme.apply(Theme.DARK)
        for name, raw_value in Theme.palette(Theme.DARK).items():
            assert getattr(Theme, name) == raw_value
        Theme.apply(Theme.LIGHT)
        for name, raw_value in Theme.palette(Theme.LIGHT).items():
            assert getattr(Theme, name) == raw_value

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
        once = Theme.remap(Theme.palette(Theme.LIGHT)["CONTROL_SURFACE"])
        assert Theme.remap(once) == once

    @pytest.mark.parametrize("fremd", ["SystemButtonFace", "#123456", "", None, 7])
    def test_remap_leaves_unknown_values_alone(self, fremd):
        assert Theme.remap(fremd) == fremd

    def test_callbacks_run_and_can_be_removed(self):
        runs = []
        Theme.register(lambda: runs.append(1))
        Theme.apply(Theme.DARK)
        assert runs == [1]
        Theme.unregister(Theme._callbacks[-1])
        Theme.apply(Theme.LIGHT)
        assert runs == [1]

    def test_a_broken_callback_does_not_stop_the_switch(self):
        def broken():
            raise RuntimeError("absichtlich")

        Theme.register(broken)
        try:
            assert Theme.apply(Theme.DARK) is True
            assert Theme.palette(Theme.DARK)["WINDOW_BG"] == Theme.WINDOW_BG
        finally:
            Theme.unregister(broken)


# ------------------------------------------------------------ Bauteile


class TestEveryComponentFollows:
    @pytest.mark.parametrize("target", MODES)
    def test_all_parts_carry_the_new_palette(self, surface, root, target):
        wechsel(surface["window"], target, root)
        palette = Theme.palette(target)
        assert surface["window"].cget("bg") == palette["WINDOW_BG"]
        assert surface["scroll"].cget("bg") == palette["WINDOW_BG"]
        assert surface["scroll"].canvas.cget("bg") == palette["WINDOW_BG"]
        assert surface["scroll"].body.cget("bg") == palette["WINDOW_BG"]
        assert surface["section"].cget("bg") == palette["WINDOW_BG"]
        assert surface["section"].cget("fg") == palette["TEXT_PRIMARY"]
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

    @pytest.mark.parametrize("target", MODES)
    def test_the_remembered_underground_moves_too(self, surface, root, target):
        """`bg_under` ist gemerkter Zustand — ohne Umschlüsselung malten die
        Controls ihre Ecken weiter in der alten Farbe aus."""
        wechsel(surface["window"], target, root)
        palette = Theme.palette(target)
        assert surface["primary"]._bg_under == palette["WINDOW_BG"]
        assert surface["select"]._bg_under == palette["GROUP_SURFACE"]
        assert surface["switch"]._bg_under == palette["GROUP_SURFACE"]
        assert surface["dot"]._bg_under == palette["GROUP_SURFACE"]
        assert surface["meter"]._bg_under == palette["WINDOW_BG"]
        assert surface["scroll"]._bg_under == palette["WINDOW_BG"]

    @pytest.mark.parametrize("target", MODES)
    def test_state_colours_follow(self, surface, root, target):
        wechsel(surface["window"], target, root)
        palette = Theme.palette(target)
        assert surface["primary"].colours()[0] == palette["ACCENT_SURFACE"]
        assert surface["primary"].colours()[1] == palette["ON_ACCENT"]
        assert surface["icon_button"]._hover_colour == palette["ERROR"]
        assert surface["dot"]._colour == palette["TEXT_TERTIARY"]

    @pytest.mark.parametrize("target", MODES)
    def test_status_dot_shows_the_new_semantic_colour(self, surface, root, target):
        surface["dot"].set_status("error")
        root.update()
        wechsel(surface["window"], target, root)
        assert surface["dot"]._colour == Theme.palette(target)["ERROR"]

    @pytest.mark.parametrize("target", MODES)
    def test_a_disabled_row_keeps_its_dimmed_look(self, surface, root, target):
        surface["row"].set_enabled(False)
        root.update()
        wechsel(surface["window"], target, root)
        palette = Theme.palette(target)
        assert surface["row"].title_label.cget("fg") == palette["TEXT_TERTIARY"]
        assert surface["row"].sub_label.cget("fg") == palette["CONTROL_HOVER"]
        assert surface["row"].enabled is False

    @pytest.mark.parametrize("target", MODES)
    def test_a_hovered_row_keeps_its_highlight(self, surface, root, target):
        surface["row"]._on_enter()
        root.update()
        wechsel(surface["window"], target, root)
        assert surface["row"]._line.cget("bg") == Theme.palette(target)["CONTROL_SURFACE"]

    @pytest.mark.parametrize("target", MODES)
    def test_the_meter_redraws_its_track(self, surface, root, target):
        wechsel(surface["window"], target, root)
        assert surface["meter"]._images, "Pegelspur fehlt nach dem Wechsel"

    def test_no_widget_keeps_a_colour_of_the_other_palette(self, surface, root):
        """Die eigentliche Zusage: keine halb helle, halb dunkle Oberfläche."""
        for target in (Theme.DARK, Theme.LIGHT, Theme.DARK):
            wechsel(surface["window"], target, root)
            fremd = {
                raw_value.upper()
                for name, raw_value in Theme.palette(Theme.LIGHT if target == Theme.DARK else Theme.DARK).items()
            }
            # Werte, die in beiden Paletten vorkommen, sind kein Befund.
            fremd -= {w.upper() for w in Theme.palette(target).values()}
            leftover = []

            def pruefe(widget, fremd=fremd, leftover=leftover):
                for option in ("bg", "fg"):
                    try:
                        raw_value = str(widget.cget(option))
                    except (tk.TclError, TypeError):
                        continue
                    if raw_value.upper() in fremd:
                        leftover.append((widget, option, raw_value))
                for kind in widget.winfo_children():
                    pruefe(kind, fremd, leftover)

            pruefe(surface["window"])
            assert not leftover, f"{target}: {leftover}"


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

    def test_the_cache_key_already_carries_the_colour(self, root):  # surface() braucht eine Tk-Wurzel
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
        before = surface["primary"]._image
        wechsel(surface["window"], Theme.DARK, root)
        assert surface["primary"]._image is not before


# ------------------------------------------------------------ Dauerbetrieb


class TestManySwitches:
    ROUNDEN = 10  # 10 Runden hell/dunkel = 20 Wechsel

    def test_twenty_switches_leave_nothing_behind(self, surface, root):
        widget_window = surface["window"]
        children_before = self._count(widget_window)
        scale_before = len(uk.Scale._callbacks)
        theme_before = len(Theme._callbacks)

        for _ in range(self.ROUNDEN):
            wechsel(widget_window, Theme.DARK, root)
            wechsel(widget_window, Theme.LIGHT, root)

        assert self._count(widget_window) == children_before, "es sind Widgets dazugekommen"
        assert len(uk.Scale._callbacks) == scale_before
        assert len(Theme._callbacks) == theme_before
        assert uk.Animator.pending() == 0
        assert len(uk._img_cache) <= uk.CACHE_LIMIT

    def test_twenty_switches_keep_every_control_alive(self, surface, root):
        for _ in range(self.ROUNDEN):
            wechsel(surface["window"], Theme.DARK, root)
            wechsel(surface["window"], Theme.LIGHT, root)
        for name in (
            "scroll",
            "group",
            "row",
            "switch",
            "select",
            "primary",
            "secondary",
            "icon_button",
            "meter",
            "dot",
        ):
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
        remembered = scroll.offset
        assert remembered > 0, "der Bereich muss wirklich rollbar sein"

        for _ in range(self.ROUNDEN):
            for target in (Theme.DARK, Theme.LIGHT):
                Popover.dismiss_current()
                Theme.apply(target)
                offset = scroll.offset
                uk.apply_theme_tree(surface["window"])
                scroll.scroll_to(offset)
                root.update()
        assert scroll.offset == remembered

    def test_the_window_does_not_resize(self, surface, root):
        widget_window = surface["window"]
        before = (widget_window.winfo_width(), widget_window.winfo_height())
        for _ in range(self.ROUNDEN):
            wechsel(widget_window, Theme.DARK, root)
            wechsel(widget_window, Theme.LIGHT, root)
        assert (widget_window.winfo_width(), widget_window.winfo_height()) == before

    @staticmethod
    def _count(widget) -> int:
        return 1 + sum(TestManySwitches._count(k) for k in widget.winfo_children())


# ------------------------------------------------------------------- DPI
