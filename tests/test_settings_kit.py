"""Tests der Einstellungs-Bausteine: ScrollArea, GroupSurface, SettingRow."""

import time
import tkinter as tk

import kit as uk
import pytest
from conftest import force_focus
from kit import (
    GroupSurface,
    Height,
    ScrollArea,
    SectionHeader,
    SettingRow,
    Switch,
    Theme,
)

BG = Theme.WINDOW_BG


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.geometry("400x300+120+120")
    win.configure(bg=BG)
    win.deiconify()
    win.lift()
    force_focus(win, root)  # ohne echten Fokus kommen Tastenereignisse nicht an
    root.update()
    yield win
    win.destroy()
    root.update()


@pytest.fixture
def area(window, root):
    scroll = ScrollArea(window, bg_under=BG)
    scroll.pack(fill="both", expand=True)
    labels = [tk.Label(scroll.body, text=f"Zeile {i}", bg=BG, fg=Theme.TEXT_PRIMARY) for i in range(24)]
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

    def test_a_touchpad_delta_below_one_notch_still_scrolls(self, area, root):
        area._on_wheel(type("E", (), {"delta": -30})())
        root.update()
        assert area.offset == round(uk.px(ScrollArea.STEP) / 4)

    def test_the_wheel_glides_to_its_target(self, area, root, monkeypatch):
        monkeypatch.setattr(uk.Animator, "enabled", True)
        offsets = []
        area._on_scroll = offsets.append
        area._on_wheel(type("E", (), {"delta": -120})())
        area._on_wheel(type("E", (), {"delta": -120})())  # a second notch extends the same glide
        assert area._target == 2 * uk.px(ScrollArea.STEP)
        end = time.monotonic() + 3
        while uk.Animator.pending(area.canvas) and time.monotonic() < end:
            root.update()
            time.sleep(0.002)
        assert area.offset == 2 * uk.px(ScrollArea.STEP)
        assert area._target is None
        assert len(offsets) > 2, "it moved in steps, not in one jump"
        assert offsets == sorted(offsets)

    def test_a_glide_to_where_it_already_is_does_nothing(self, area, monkeypatch):
        monkeypatch.setattr(uk.Animator, "enabled", True)
        area.glide_to(0)
        assert area._target is None
        assert uk.Animator.pending(area.canvas) == 0

    def test_scrolling_moves_the_thumb_instead_of_redrawing_it(self, area, root):
        root.update()
        item = area._bar_item
        assert item is not None
        top = area.bar.coords(item)[1]
        area.scroll_by(40)
        root.update()
        assert area._bar_item == item
        assert area.bar.coords(item)[1] > top

    def test_mouse_wheel_on_linux_scrolls_down_and_up(self, area, root):
        area.canvas.event_generate("<Enter>")
        root.update()
        area.canvas.event_generate("<Button-5>")
        root.update()
        assert area.offset > 0
        area.canvas.event_generate("<Button-4>")
        root.update()
        assert area.offset == 0

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
        assert not any(area.canvas.bind_all(sequence) for sequence in area.WHEEL_EVENTS)

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
        assert group.inset == uk.px(uk.Space.GROUP_INSET)
        # (4, 4) liegt innerhalb des Viertelkreises um (10, 10): die Zeile
        # bleibt drinnen, die Rundung sichtbar.
        corner = uk.Radius.GROUP - uk.Space.GROUP_INSET
        assert (corner**2 * 2) ** 0.5 < uk.Radius.GROUP - 1

    def test_the_plate_is_drawn_once_after_the_rows_settle(self, window, root, monkeypatch):
        """While rows are added the group passes many heights; only the final one is rendered."""
        group = GroupSurface(window, bg_under=BG)
        group.pack(fill="x")
        rendered = []
        original = GroupSurface._render
        monkeypatch.setattr(GroupSurface, "_render", lambda self, w, h: rendered.append((w, h)) or original(self, w, h))
        for index in range(6):
            SettingRow(group.body, f"Zeile {index}").pack(fill="x")
        for _ in range(3):
            root.update()
        assert len(rendered) == 1
        assert rendered[0] == (group.winfo_width(), group.winfo_height())
        assert group.find_withtag("plate")

    def test_a_pending_plate_is_dropped_with_the_group(self, window, root):
        group = GroupSurface(window, bg_under=BG)
        group.pack(fill="x")
        root.update()
        SettingRow(group.body, "Zeile").pack(fill="x")
        root.update_idletasks()
        group._schedule_render()
        group.destroy()
        root.update()
        assert group._render_job is None

    def test_plate_has_rounded_corners(self):
        plate = uk.render_surface(300, 120, uk.px(uk.Radius.GROUP), Theme.GROUP_SURFACE, squircle=True)
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
        assert header.cget("fg") == Theme.TEXT_PRIMARY

    def test_is_not_all_caps_grey_noise(self, window):
        """Die alte schwache MIKROFON-Typografie entfällt."""
        header = SectionHeader(window, "Transkription", bg_under=BG)
        assert header.cget("text") != header.cget("text").upper()


class TestSettingRow:
    @pytest.mark.parametrize("variant", ["simple", "description", "status", "display"])
    def test_every_variant_has_the_same_minimum_height(self, window, root, variant):
        row = SettingRow(window, "Titel", variant=variant, description="Text", value="Wert")
        row.pack(fill="x")
        root.update()
        assert row._line.winfo_height() == uk.px(Height.ROW_MIN)

    def test_padding_is_symmetric(self, window, root):
        row = SettingRow(window, "Titel", variant="description", description="Text")
        row.pack(fill="x")
        root.update()
        assert int(str(row._line.cget("pady"))) == uk.px(uk.Space.ROW_Y)

    def test_long_description_wraps_instead_of_truncating(self, window, root):
        text = "Eine lange Beschreibung, die nie und nimmer in eine Zeile passt"
        window.geometry("260x400")
        row = SettingRow(window, "Titel", variant="description", description=text)
        row.pack(fill="x")
        for _ in range(3):
            root.update()
        assert row.sub_label.cget("text") == text
        assert "…" not in row.sub_label.cget("text")
        assert row._line.winfo_height() > uk.px(Height.ROW_MIN)

    def test_stacked_control_sits_below_the_text(self, window, root):
        window.geometry("400x400")
        row = SettingRow(window, "Mikrofon", variant="status", stacked=True)
        row.pack(fill="x")
        control = uk.Select(
            window, bg_under=Theme.GROUP_SURFACE, options=[uk.Option("a", "Ein sehr langer Gerätename")], value="a"
        )
        row.set_control(control)
        for _ in range(3):
            root.update()
        assert control.winfo_y() > row._text_box.winfo_y() + row._text_box.winfo_height()
        # volle Breite, sichtbare Kante bündig mit dem Titel
        inset = uk.px(uk.ControlStyle.RING_WIDTH) + uk.px(uk.ControlStyle.RING_GAP)
        assert control.winfo_x() + inset == row._text_box.winfo_x()
        assert control.winfo_width() >= row._text_box.winfo_width()

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
        row = SettingRow(
            window,
            "Text-Cleanup",
            variant="description",
            description="Bereinigt automatisch",
            interactive=True,
            command=lambda: fired.append("row"),
        )
        row.pack(fill="x")
        switch = Switch(window, bg_under=Theme.GROUP_SURFACE, value=False, command=lambda value: fired.append(value))
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
        row = SettingRow(window, "Cleanup-Modell", variant="description", description="Text")
        row.pack(fill="x")
        root.update()
        before = row._line.winfo_height()
        row.set_enabled(False)
        root.update()
        assert row._line.winfo_height() == before

    def test_text_loses_contrast(self, window, root):
        row = SettingRow(window, "Cleanup-Modell", variant="description", description="Text")
        row.pack(fill="x")
        root.update()
        row.set_enabled(False)
        assert row.title_label.cget("fg") == Theme.TEXT_TERTIARY

    def test_control_is_disabled_too(self, window, root):
        row = SettingRow(window, "Cleanup-Modell", interactive=True, command=lambda: None)
        row.pack(fill="x")
        switch = Switch(window, bg_under=Theme.GROUP_SURFACE, value=True, command=lambda _v: None)
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
        assert row._line.winfo_height() == uk.px(Height.ROW_MIN)

    def test_runtime_scale_change_grows_rows(self, window, root):
        row = SettingRow(window, "Titel", variant="description", description="x")
        row.pack(fill="x")
        root.update()
        before = row._line.winfo_height()
        start = uk.Scale.dpi
        try:
            uk.Scale.apply(root, start * 2)
            root.update()
            after = row._line.winfo_height()
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
