"""Tests der Basis-Controls: Zustände, Tastatur, DPI-Wechsel, Pegel, Schalter."""

import tkinter as tk

import pytest

from conftest import fokus_erzwingen

from wisper import ui_kit as uk
from wisper.ui_kit import (
    IconButton, PrimaryButton, SecondaryButton, SignalMeter, State, Switch, Theme,
)

BG = Theme.WINDOW_BG
STATES = [State.HOVER, State.PRESSED, State.FOCUSED, State.SELECTED,
          State.DISABLED, State.LOADING, State.ERROR]


@pytest.fixture
def window(root):
    """Sichtbares Fenster — Fokus lässt sich nur an einem gemappten prüfen."""
    win = tk.Toplevel(root)
    win.geometry("320x360+90+90")
    win.configure(bg=BG)
    win.deiconify()
    win.lift()
    fokus_erzwingen(win, root)
    yield win
    win.destroy()
    root.update()


@pytest.fixture
def controls(window, root):
    fired = {"primary": 0, "secondary": 0, "icon": 0, "switch": []}
    made = {
        "primary": PrimaryButton(window, "Aufnahme starten",
                                 lambda: fired.__setitem__("primary", fired["primary"] + 1),
                                 bg_under=BG),
        "secondary": SecondaryButton(window, "Stoppen",
                                     lambda: fired.__setitem__("secondary", fired["secondary"] + 1),
                                     bg_under=BG, icon_name="stop",
                                     icon_colour=Theme.RECORDING),
        "icon": IconButton(window, "settings",
                           lambda: fired.__setitem__("icon", fired["icon"] + 1), bg_under=BG),
        "switch": Switch(window, bg_under=BG, value=False,
                         command=lambda v: fired["switch"].append(v)),
        "meter": SignalMeter(window, bg_under=BG),
    }
    for widget in made.values():
        widget.pack(fill="x", padx=12, pady=6)
    root.update()
    made["fired"] = fired
    yield made


@pytest.fixture
def factor(monkeypatch):
    def _set(value):
        monkeypatch.setattr(uk.Scale, "factor", value)
        monkeypatch.setattr(uk.Scale, "dpi", int(round(96 * value)))
        return value

    return _set


class TestStatesPerControl:
    @pytest.mark.parametrize("name", ["primary", "secondary", "icon"])
    @pytest.mark.parametrize("state", STATES)
    def test_every_state_renders(self, controls, root, name, state):
        widget = controls[name]
        widget.set_state(state, True)
        root.update()
        assert widget.has_state(state) or state in (State.HOVER, State.PRESSED,
                                                    State.FOCUSED)

    @pytest.mark.parametrize("name", ["primary", "secondary", "icon"])
    def test_colours_follow_priority(self, controls, name):
        widget = controls[name]
        widget.set_state(State.HOVER, True)
        hover = widget.colours()
        widget.set_state(State.PRESSED, True)
        assert widget.colours() != hover, "pressed muss hover übersteuern"

    @pytest.mark.parametrize("name", ["primary", "secondary", "icon", "switch"])
    def test_disabled_blocks_activation(self, controls, root, name):
        widget = controls[name]
        widget.set_enabled(False)
        root.update()
        widget.event_generate("<space>")
        widget.event_generate("<ButtonPress-1>")
        widget.event_generate("<ButtonRelease-1>")
        root.update()
        fired = controls["fired"]
        assert fired["switch"] == [] if name == "switch" else fired[name] == 0

    @pytest.mark.parametrize("name", ["primary", "secondary", "icon", "switch"])
    def test_disabled_drops_hover(self, controls, root, name):
        widget = controls[name]
        widget.set_state(State.HOVER, True)
        widget.set_enabled(False)
        root.update()
        assert not widget.has_state(State.HOVER)

    def test_primary_can_switch_role(self, controls, root):
        button = controls["primary"]
        before = button.colours()
        button.set_role("recording")
        root.update()
        assert button.colours() != before


class TestMouse:
    @pytest.mark.parametrize("name", ["primary", "secondary", "icon"])
    def test_click_activates(self, controls, root, name):
        widget = controls[name]
        widget.event_generate("<ButtonPress-1>")
        widget.event_generate("<ButtonRelease-1>")
        root.update()
        assert controls["fired"][name] == 1

    @pytest.mark.parametrize("name", ["primary", "secondary", "icon"])
    def test_hover_enter_and_leave(self, controls, root, name):
        widget = controls[name]
        widget.event_generate("<Enter>")
        root.update()
        assert widget.has_state(State.HOVER)
        widget.event_generate("<Leave>")
        root.update()
        assert not widget.has_state(State.HOVER)

    def test_switch_click_toggles(self, controls, root):
        switch = controls["switch"]
        switch.event_generate("<ButtonPress-1>")
        switch.event_generate("<ButtonRelease-1>")
        root.update()
        assert switch.value is True
        assert controls["fired"]["switch"] == [True]


class TestKeyboard:
    @pytest.mark.parametrize("name", ["primary", "secondary", "icon"])
    @pytest.mark.parametrize("key", ["<Return>", "<space>"])
    def test_enter_and_space(self, controls, root, name, key):
        widget = controls[name]
        widget.focus_set()
        root.update()
        widget.event_generate(key)
        root.update()
        assert controls["fired"][name] == 1

    def test_switch_space_toggles(self, controls, root):
        switch = controls["switch"]
        switch.focus_set()
        root.update()
        switch.event_generate("<space>")
        root.update()
        assert switch.value is True

    def test_tab_chain(self, controls, root):
        order = [controls["primary"], controls["secondary"],
                 controls["icon"], controls["switch"]]
        for first, second in zip(order, order[1:]):
            assert first.tk_focusNext() is second

    def test_shift_tab_chain(self, controls, root):
        order = [controls["primary"], controls["secondary"],
                 controls["icon"], controls["switch"]]
        for first, second in zip(order, order[1:]):
            assert second.tk_focusPrev() is first

    def test_focus_sets_state_and_ring(self, controls, root):
        button = controls["primary"]
        button.focus_set()
        root.update()
        assert button.has_state(State.FOCUSED)
        assert button.ring() == Theme.ACCENT_TINT

    @pytest.mark.parametrize("name", ["primary", "secondary", "icon", "switch"])
    def test_disabled_leaves_the_tab_chain(self, controls, root, name):
        widget = controls[name]
        widget.set_enabled(False)
        root.update()
        assert str(widget.cget("takefocus")) == "0"


class TestSwitch:
    def test_knob_moves_with_value(self, controls, root):
        """Position kodiert den Zustand zusätzlich zur Farbe."""
        switch = controls["switch"]
        switch.set(False)
        root.update()
        off = [switch.coords(item)[0] for item in switch.find_all()][-1]
        switch.set(True)
        root.update()
        on = [switch.coords(item)[0] for item in switch.find_all()][-1]
        assert on > off, f"Knopf bewegt sich nicht: {off} -> {on}"

    def test_state_readable_without_colour(self, controls, root):
        """Graustufenprüfung: On und Off bleiben unterscheidbar."""
        switch = controls["switch"]
        switch.set(False)
        root.update()
        off_x = [switch.coords(item)[0] for item in switch.find_all()][-1]
        switch.set(True)
        root.update()
        on_x = [switch.coords(item)[0] for item in switch.find_all()][-1]
        # Der Abstand muss deutlich sein, nicht nur ein paar Pixel
        assert on_x - off_x >= uk.px(12)

    def test_track_colour_uses_success(self, controls):
        switch = controls["switch"]
        switch.set(True)
        assert switch._track_colour() == Theme.SUCCESS

    def test_track_neutral_when_off(self, controls):
        switch = controls["switch"]
        switch.set(False)
        assert switch._track_colour() in (Theme.CONTROL_SURFACE, Theme.CONTROL_HOVER)

    def test_disabled_switch_is_muted(self, controls, root):
        switch = controls["switch"]
        switch.set(True)
        switch.set_enabled(False)
        root.update()
        assert switch._track_colour() == Theme.CONTROL_SURFACE


class TestSignalMeter:
    @pytest.mark.parametrize("level", [0.0, 0.25, 0.5, 0.75, 1.0])
    def test_levels_light_the_right_share(self, controls, root, level):
        meter = controls["meter"]
        meter.set_level(level)
        root.update()
        assert meter.level == pytest.approx(level, abs=0.01)
        assert meter.segments > 0
        assert len(meter.find_all()) == meter.segments

    def test_level_is_clamped(self, controls, root):
        meter = controls["meter"]
        meter.set_level(5.0)
        assert meter.level == 1.0
        meter.set_level(-3.0)
        assert meter.level == 0.0

    def test_segments_follow_width(self, controls, root, window):
        meter = controls["meter"]
        window.geometry("500x360")
        root.update()
        wide = meter.segments
        window.geometry("240x360")
        root.update()
        assert meter.segments < wide

    def test_always_visible_at_rest(self, controls, root):
        """Kein pack_forget — der Pegel behält seine Höhe auch bei Pegel null."""
        meter = controls["meter"]
        meter.set_level(0.0)
        root.update()
        assert meter.winfo_manager() == "pack"
        assert meter.winfo_height() > 1


class TestDpi:
    @pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
    def test_geometry_follows_factor(self, root, window, factor, scale):
        factor(scale)
        button = PrimaryButton(window, "Test", lambda: None, bg_under=BG)
        icon = IconButton(window, "close", lambda: None, bg_under=BG)
        switch = Switch(window, bg_under=BG, value=True, command=lambda _v: None)
        for widget in (button, icon, switch):
            widget.pack()
        root.update()
        assert icon.winfo_reqwidth() == uk.px(icon._side)
        assert switch.winfo_reqwidth() >= uk.px(Switch.WIDTH)
        for widget in (button, icon, switch):
            widget.destroy()

    def test_runtime_scale_change_rebuilds_everything(self, controls, root):
        """Der offene Punkt aus Phase 2: Controls skalieren jetzt zur Laufzeit nach."""
        icon, switch, meter = controls["icon"], controls["switch"], controls["meter"]
        before = (icon.winfo_reqwidth(), switch.winfo_reqwidth(), meter.winfo_reqheight())
        start = uk.Scale.dpi
        try:
            uk.Scale.apply(root, start * 2)
            root.update()
            after = (icon.winfo_reqwidth(), switch.winfo_reqwidth(), meter.winfo_reqheight())
        finally:
            uk.Scale.apply(root, start)
            root.update()
        assert all(a > b for a, b in zip(after, before)), f"{before} -> {after}"

    def test_scale_callbacks_are_removed_on_destroy(self, root, window):
        before = len(uk.Scale._callbacks)
        widgets = [
            PrimaryButton(window, "X", lambda: None, bg_under=BG),
            IconButton(window, "close", lambda: None, bg_under=BG),
            Switch(window, bg_under=BG, value=False, command=lambda _v: None),
            SignalMeter(window, bg_under=BG),
        ]
        for widget in widgets:
            widget.pack()
        root.update()
        assert len(uk.Scale._callbacks) == before + 4
        for widget in widgets:
            widget.destroy()
        root.update()
        assert len(uk.Scale._callbacks) == before, "tote Rückrufe bleiben hängen"

    def test_no_duplicate_callbacks(self, root, window):
        before = len(uk.Scale._callbacks)
        button = PrimaryButton(window, "X", lambda: None, bg_under=BG)
        root.update()
        button._render()
        button._render()
        assert len(uk.Scale._callbacks) == before + 1
        button.destroy()


class TestNoLegacyGlyphs:
    def test_controls_carry_no_unicode_icons(self, controls):
        for name in ("primary", "secondary", "icon", "switch"):
            widget = controls[name]
            text = widget.cget("text") if "text" in widget.keys() else ""
            for glyph in ("⚙", "✕", "⌄", "✓", "↓", "×"):
                assert glyph not in str(text), f"{name} enthält {glyph}"

    def test_icon_button_uses_the_icon_set(self, controls):
        assert controls["icon"]._icon_name in uk.icon_names()

    def test_secondary_icon_is_from_the_set(self, controls):
        assert controls["secondary"]._icon_name in uk.icon_names()
