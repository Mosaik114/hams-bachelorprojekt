"""Tests der Einstellungs-Bausteine: ScrollArea, GroupSurface, SettingRow."""

import tkinter as tk

import pytest

from conftest import fokus_erzwingen

from wisper import ui_kit as uk
from wisper.ui_kit import (
    GroupSurface, Height, ScrollArea, SectionHeader, SettingRow, Switch, Theme,
)

BG = Theme.WINDOW_BG


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.geometry("400x300+120+120")
    win.configure(bg=BG)
    win.deiconify()
    win.lift()
    fokus_erzwingen(win, root)   # ohne echten Fokus kommen Tastenereignisse nicht an
    root.update()
    yield win
    win.destroy()
    root.update()


@pytest.fixture
def area(window, root):
    scroll = ScrollArea(window, bg_under=BG)
    scroll.pack(fill="both", expand=True)
    labels = [tk.Label(scroll.body, text=f"Zeile {i}", bg=BG, fg=Theme.TEXT_PRIMARY)
              for i in range(24)]
    for label in labels:
        label.pack()
    for _ in range(3):
        root.update_idletasks()
        root.update()
    yield scroll


@pytest.fixture
def factor(monkeypatch):
    def _set(value):
        monkeypatch.setattr(uk.Scale, "factor", value)
        monkeypatch.setattr(uk.Scale, "dpi", int(round(96 * value)))
        return value

    return _set


class TestScrollArea:
    def test_content_is_scrollable(self, area):
        assert area.scrollable > 0

    def test_scrolls_within_bounds(self, area, root):
        area.scroll_by(80)
        root.update()
        assert area.offset == 80

    def test_stops_at_the_top(self, area, root):
        area.scroll_by(-500)
        root.update()
        assert area.offset == 0

    def test_stops_at_the_bottom(self, area, root):
        area.scroll_by(10_000)
        root.update()
        assert area.offset == area.scrollable

    def test_mouse_wheel(self, area, root):
        area.canvas.event_generate("<Enter>")
        root.update()
        area._on_wheel(type("E", (), {"delta": -120})())
        root.update()
        assert area.offset > 0

    def test_keyboard_scrolling(self, area, window, root):
        area.bind_keys(window)
        window.event_generate("<Down>")
        root.update()
        assert area.offset > 0
        window.event_generate("<Home>")
        root.update()
        assert area.offset == 0
        window.event_generate("<End>")
        root.update()
        assert area.offset == area.scrollable

    def test_page_keys(self, area, window, root):
        area.bind_keys(window)
        window.event_generate("<Next>")
        root.update()
        assert area.offset > 0

    def test_bar_appears_only_when_needed(self, window, root):
        scroll = ScrollArea(window, bg_under=BG)
        scroll.pack(fill="both", expand=True)
        for _ in range(3):
            root.update_idletasks()
            root.update()
        assert scroll.bar.winfo_manager() != "pack", "Leiste ohne Grund sichtbar"

        labels = [tk.Label(scroll.body, text="x", bg=BG) for _ in range(30)]
        for label in labels:
            label.pack()
        for _ in range(3):
            root.update_idletasks()
            root.update()
        assert scroll.bar.winfo_manager() == "pack"

    def test_bar_stays_hidden_for_short_content(self, window, root):
        """Wenig Inhalt: keine Leiste."""
        scroll = ScrollArea(window, bg_under=BG)
        scroll.pack(fill="both", expand=True)
        for _ in range(2):
            tk.Label(scroll.body, text="kurz", bg=BG).pack()
        for _ in range(3):
            root.update_idletasks()
            root.update()
        assert scroll.scrollable == 0
        assert scroll.bar.winfo_manager() != "pack"

    def test_no_horizontal_scrolling(self, area):
        region = area.canvas.cget("scrollregion").split()
        assert region[0] == "0" and region[2] == "0"

    def test_scroll_callback_reports_offset(self, window, root):
        seen = []
        scroll = ScrollArea(window, bg_under=BG, on_scroll=seen.append)
        scroll.pack(fill="both", expand=True)
        for _ in range(20):
            tk.Label(scroll.body, text="x", bg=BG).pack()
        for _ in range(3):
            root.update_idletasks()
            root.update()
        scroll.scroll_by(50)
        root.update()
        assert seen and seen[-1] == 50

    def test_wheel_binding_is_released(self, area, root):
        area.canvas.event_generate("<Enter>")
        root.update()
        area.canvas.event_generate("<Leave>")
        root.update()
        assert not area.canvas.bind_all("<MouseWheel>")

    def test_callbacks_removed_on_destroy(self, window, root):
        before = len(uk.Scale._callbacks)
        scroll = ScrollArea(window, bg_under=BG)
        scroll.pack()
        root.update()
        assert len(uk.Scale._callbacks) == before + 1
        scroll.destroy()
        root.update()
        assert len(uk.Scale._callbacks) == before


class TestGroupSurface:
    def test_height_follows_its_rows(self, window, root):
        group = GroupSurface(window, bg_under=BG)
        group.pack(fill="x")
        root.update()
        empty = int(group.cget("height"))
        for index in range(3):
            SettingRow(group.body, f"Zeile {index}").pack(fill="x")
        for _ in range(3):
            root.update_idletasks()
            root.update()
        assert int(group.cget("height")) > empty

    def test_rows_are_inset_so_the_rounding_survives(self, window, root):
        group = GroupSurface(window, bg_under=BG)
        group.pack(fill="x")
        root.update()
        assert group.inset == uk.px(uk.Radius.GROUP)

    def test_plate_has_rounded_corners(self):
        plate = uk.render_surface(300, 120, uk.px(uk.Radius.GROUP),
                                  Theme.GROUP_SURFACE, squircle=True)
        assert plate.getpixel((0, 0))[3] < 60
        assert plate.getpixel((150, 60))[3] == 255

    def test_callbacks_removed_on_destroy(self, window, root):
        before = len(uk.Scale._callbacks)
        group = GroupSurface(window, bg_under=BG)
        group.pack()
        root.update()
        group.destroy()
        root.update()
        assert len(uk.Scale._callbacks) == before


class TestSectionHeader:
    def test_uses_the_section_role(self, window, root):
        header = SectionHeader(window, "Aufnahme", bg_under=BG)
        header.pack()
        root.update()
        assert header.cget("text") == "Aufnahme"
        assert header.cget("fg") == Theme.TEXT_SECONDARY

    def test_is_not_all_caps_grey_noise(self, window):
        """Die alte schwache MIKROFON-Typografie entfällt."""
        header = SectionHeader(window, "Transkription", bg_under=BG)
        assert header.cget("text") != header.cget("text").upper()


class TestSettingRow:
    @pytest.mark.parametrize("variant,height", [
        ("simple", Height.ROW_SIMPLE),
        ("description", Height.ROW_DETAIL),
        ("status", Height.ROW_DETAIL),
        ("display", Height.ROW_SIMPLE),
    ])
    def test_variant_heights(self, window, root, variant, height):
        row = SettingRow(window, "Titel", variant=variant,
                         description="Text", value="Wert")
        row.pack(fill="x")
        root.update()
        assert int(row._line.cget("height")) == uk.px(height)

    def test_status_variant_has_a_dot(self, window, root):
        row = SettingRow(window, "Mikrofon", variant="status")
        row.pack(fill="x")
        root.update()
        assert row.status_dot is not None
        row.set_status("ready", "Gutes Signal")
        assert row.status_dot.status == "ready"
        assert row.sub_label.cget("text") == "Gutes Signal"

    def test_display_variant_shows_a_value(self, window, root):
        row = SettingRow(window, "Hardware", variant="display", value="Automatisch")
        row.pack(fill="x")
        root.update()
        assert row.value_label.cget("text") == "Automatisch"
        row.set_value("Automatisch · GPU")
        assert row.value_label.cget("text") == "Automatisch · GPU"

    def test_hairline_can_be_suppressed(self, window):
        assert SettingRow(window, "A", hairline=False).hairline is None
        assert SettingRow(window, "B", hairline=True).hairline is not None

    def test_control_sits_on_the_right(self, window, root):
        row = SettingRow(window, "Mikrofon")
        row.pack(fill="x")
        control = tk.Label(window, text="Ein sehr langer Gerätename", bg=BG)
        row.set_control(control)
        root.update()
        assert control.winfo_reqwidth() <= control.winfo_width() + 2, "Control beschnitten"


class TestToggleRow:
    @pytest.fixture
    def toggle_row(self, window, root):
        fired = []
        row = SettingRow(window, "Text-Cleanup", variant="description",
                         description="Bereinigt automatisch", interactive=True,
                         command=lambda: fired.append("row"))
        row.pack(fill="x")
        switch = Switch(window, bg_under=Theme.GROUP_SURFACE, value=False,
                        command=lambda value: fired.append(value))
        row.set_control(switch)
        root.update()
        return row, switch, fired

    def test_click_on_title_fires_once(self, toggle_row, root):
        row, _switch, fired = toggle_row
        row.title_label.event_generate("<Button-1>")
        root.update()
        assert fired == ["row"]

    def test_click_on_description_fires(self, toggle_row, root):
        row, _switch, fired = toggle_row
        row.sub_label.event_generate("<Button-1>")
        root.update()
        assert fired == ["row"]

    def test_click_on_switch_does_not_double_fire(self, toggle_row, root):
        _row, switch, fired = toggle_row
        switch.event_generate("<ButtonPress-1>")
        switch.event_generate("<ButtonRelease-1>")
        root.update()
        assert fired == [True], f"Doppelauslösung: {fired}"

    def test_space_toggles_the_switch(self, toggle_row, root):
        _row, switch, fired = toggle_row
        switch.focus_set()
        root.update()
        switch.event_generate("<space>")
        root.update()
        assert fired == [True]

    def test_hover_paints_the_whole_row(self, toggle_row, root):
        row, _switch, _fired = toggle_row
        row.title_label.event_generate("<Enter>")
        root.update()
        assert row.title_label.cget("bg") == Theme.CONTROL_SURFACE
        row.title_label.event_generate("<Leave>")
        root.update()
        assert row.title_label.cget("bg") == Theme.GROUP_SURFACE


class TestDisabledRow:
    def test_height_survives_disabling(self, window, root):
        row = SettingRow(window, "Cleanup-Modell", variant="description",
                         description="Text")
        row.pack(fill="x")
        root.update()
        before = int(row._line.cget("height"))
        row.set_enabled(False)
        root.update()
        assert int(row._line.cget("height")) == before

    def test_text_loses_contrast(self, window, root):
        row = SettingRow(window, "Cleanup-Modell", variant="description",
                         description="Text")
        row.pack(fill="x")
        root.update()
        row.set_enabled(False)
        assert row.title_label.cget("fg") == Theme.TEXT_TERTIARY

    def test_control_is_disabled_too(self, window, root):
        row = SettingRow(window, "Cleanup-Modell", interactive=True,
                         command=lambda: None)
        row.pack(fill="x")
        switch = Switch(window, bg_under=Theme.GROUP_SURFACE, value=True,
                        command=lambda _v: None)
        row.set_control(switch)
        root.update()
        row.set_enabled(False)
        root.update()
        assert not switch.enabled

    def test_disabled_row_ignores_clicks(self, window, root):
        fired = []
        row = SettingRow(window, "X", interactive=True, command=lambda: fired.append(1))
        row.pack(fill="x")
        root.update()
        row.set_enabled(False)
        row.title_label.event_generate("<Button-1>")
        root.update()
        assert fired == []


class TestDpi:
    @pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
    def test_row_height_scales(self, window, root, factor, scale):
        factor(scale)
        row = SettingRow(window, "Titel", variant="description", description="x")
        row.pack(fill="x")
        root.update()
        assert int(row._line.cget("height")) == uk.px(Height.ROW_DETAIL)

    def test_runtime_scale_change_grows_rows(self, window, root):
        row = SettingRow(window, "Titel", variant="description", description="x")
        row.pack(fill="x")
        root.update()
        before = int(row._line.cget("height"))
        start = uk.Scale.dpi
        try:
            uk.Scale.apply(root, start * 2)
            root.update()
            after = int(row._line.cget("height"))
        finally:
            uk.Scale.apply(root, start)
            root.update()
        assert after > before

    def test_row_callbacks_removed_on_destroy(self, window, root):
        before = len(uk.Scale._callbacks)
        row = SettingRow(window, "Titel", variant="status")
        row.pack(fill="x")
        root.update()
        row.destroy()
        root.update()
        assert len(uk.Scale._callbacks) == before
