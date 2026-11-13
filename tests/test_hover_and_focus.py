"""Zwei Dinge, die im hellen Modus auffielen und im dunklen kaum.

1. Unter dem Zeiger wechselt eine Einstellungszeile ihre Fläche. Das Control
   darauf blieb in der alten Farbe stehen — sichtbar als Rechteck um den
   Schalter, weil dessen runde Spur den Untergrund durchscheinen lässt.
2. Ein Klick setzte den Fokusring. Wer klickt, weiß aber schon, wo er ist;
   der Ring wirkte dort wie eine dicke Markierung um das eben Angefasste.

Beides fiel im dunklen Modus kaum auf: zwischen Gruppen- und Controlfläche
liegen dort 1,16:1, im hellen 1,13:1 — aber auf hellem Grund trennt das Auge
Flächen deutlich schärfer.
"""

import tkinter as tk

import kit as uk
import pytest
from conftest import force_focus
from kit import IconButton, Option, SecondaryButton, Select, SettingRow, State, StatusDot, Switch, Theme


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.geometry("420x260+150+150")
    win.configure(bg=Theme.WINDOW_BG)
    win.deiconify()
    win.lift()
    force_focus(win, root)
    yield win
    win.destroy()
    root.update()


def _row(master, root, control_bauen, **kwargs):
    row = SettingRow(
        master,
        "Text-Cleanup",
        variant="description",
        description="Bereinigt das Transkript automatisch",
        interactive=True,
        command=lambda: None,
        **kwargs,
    )
    row.pack(fill="x")
    control = control_bauen(master)
    row.set_control(control)
    root.update()
    return row, control


# ------------------------------------------------------- Das Rechteck


class TestTheControlFollowsTheRow:
    def test_a_switch_shares_the_hovered_surface(self, window, root):
        row, switch = _row(
            window, root, lambda m: Switch(m, bg_under=Theme.GROUP_SURFACE, value=True, command=lambda _v: None)
        )
        assert switch.cget("bg") == Theme.GROUP_SURFACE

        row._on_enter()
        root.update()
        assert row._line.cget("bg") == Theme.CONTROL_SURFACE
        assert switch.cget("bg") == Theme.CONTROL_SURFACE, (
            "sonst steht der Schalter als Rechteck der alten Farbe in der Zeile"
        )

    def test_the_switch_also_remembers_the_new_underground(self, window, root):
        """`_bg_under` malt die Ecken aus — ohne Nachziehen blieben sie alt."""
        row, switch = _row(
            window, root, lambda m: Switch(m, bg_under=Theme.GROUP_SURFACE, value=True, command=lambda _v: None)
        )
        row._on_enter()
        root.update()
        assert switch._bg_under == Theme.CONTROL_SURFACE

    def test_leaving_puts_the_group_surface_back(self, window, root):
        row, switch = _row(
            window, root, lambda m: Switch(m, bg_under=Theme.GROUP_SURFACE, value=False, command=lambda _v: None)
        )
        row._on_enter()
        root.update()
        row._on_leave()
        root.update()
        assert switch.cget("bg") == Theme.GROUP_SURFACE
        assert switch._bg_under == Theme.GROUP_SURFACE

    def test_it_works_for_a_select_too(self, window, root):
        row, feld = _row(
            window, root, lambda m: Select(m, bg_under=Theme.GROUP_SURFACE, options=[Option("a", "A")], value="a")
        )
        row._on_enter()
        root.update()
        assert feld.cget("bg") == Theme.CONTROL_SURFACE
        assert feld._bg_under == Theme.CONTROL_SURFACE

    def test_a_disabled_row_keeps_its_control_on_the_group_surface(self, window, root):
        row, switch = _row(
            window, root, lambda m: Switch(m, bg_under=Theme.GROUP_SURFACE, value=True, command=lambda _v: None)
        )
        row.set_enabled(False)
        row._on_enter()
        root.update()
        assert switch.cget("bg") == Theme.GROUP_SURFACE


class TestSetSurfaceUnder:
    def test_it_reaches_the_children_of_a_container(self, window, root):
        """Die Löschen-Rückfrage ist ein Rahmen mit zwei Schaltflächen darin."""
        rahmen = tk.Frame(window, bg=Theme.GROUP_SURFACE)
        rahmen.pack()
        button = SecondaryButton(rahmen, "Löschen", lambda: None, bg_under=Theme.GROUP_SURFACE, role="recording")
        button.pack(side="left")
        kreuz = IconButton(rahmen, "close", lambda: None, bg_under=Theme.GROUP_SURFACE)
        kreuz.pack(side="left")
        root.update()

        uk.set_surface_under(rahmen, Theme.CONTROL_SURFACE)
        root.update()
        assert rahmen.cget("bg") == Theme.CONTROL_SURFACE
        assert button._bg_under == Theme.CONTROL_SURFACE
        assert kreuz._bg_under == Theme.CONTROL_SURFACE

    def test_a_widget_without_a_remembered_underground_is_left_alone(self, window, root):
        label = tk.Label(window, text="nur Text", bg=Theme.GROUP_SURFACE)
        label.pack()
        root.update()
        uk.set_surface_under(label, Theme.CONTROL_SURFACE)
        assert label.cget("bg") == Theme.CONTROL_SURFACE
        assert not hasattr(label, "_bg_under")

    def test_a_status_dot_follows(self, window, root):
        punkt = StatusDot(window, bg_under=Theme.GROUP_SURFACE, status="ready")
        punkt.pack()
        root.update()
        uk.set_surface_under(punkt, Theme.CONTROL_SURFACE)
        assert punkt.cget("bg") == Theme.CONTROL_SURFACE
        assert punkt._bg_under == Theme.CONTROL_SURFACE


# --------------------------------------------------------- Der Fokusring


class TestFocusRingOnlyForTheKeyboard:
    def _switch(self, window, root):
        switch = Switch(window, bg_under=Theme.WINDOW_BG, value=False, command=lambda _v: None)
        switch.pack(pady=10)
        root.update()
        return switch

    def test_a_click_does_not_draw_the_ring(self, window, root):
        switch = self._switch(window, root)
        switch.event_generate("<ButtonPress-1>", x=10, y=10)
        switch.event_generate("<ButtonRelease-1>", x=10, y=10)
        root.update()
        assert not switch.has_state(State.FOCUSED)
        assert switch.ring() is None

    def test_the_keyboard_still_draws_it(self, window, root):
        """Ohne Ring wüsste beim Tabulieren niemand, wo er steht."""
        switch = self._switch(window, root)
        switch.focus_set()
        root.update()
        assert switch.has_state(State.FOCUSED)
        assert switch.ring() == Theme.ACCENT_TINT

    def test_a_click_still_gives_the_widget_the_real_focus(self, window, root):
        """Nur die Anzeige entfällt — Leertaste und Eingabe müssen wirken."""
        switch = self._switch(window, root)
        switch.event_generate("<ButtonPress-1>", x=10, y=10)
        switch.event_generate("<ButtonRelease-1>", x=10, y=10)
        root.update()
        assert switch.focus_get() is switch
        before = switch.value
        switch.event_generate("<space>")
        root.update()
        assert switch.value is not before

    def test_tabbing_away_and_back_shows_the_ring_again(self, window, root):
        switch = self._switch(window, root)
        other = self._switch(window, root)
        switch.event_generate("<ButtonPress-1>", x=10, y=10)
        switch.event_generate("<ButtonRelease-1>", x=10, y=10)
        root.update()
        assert switch.ring() is None
        other.focus_set()
        root.update()
        switch.focus_set()
        root.update()
        assert switch.ring() == Theme.ACCENT_TINT, "der Zeiger-Vermerk darf nur für genau einen Fokuswechsel gelten"

    def test_it_holds_for_buttons_as_well(self, window, root):
        button = SecondaryButton(window, "Neu starten", lambda: None, bg_under=Theme.WINDOW_BG)
        button.pack()
        nachbar = self._switch(window, root)
        root.update()

        button.event_generate("<ButtonPress-1>", x=5, y=5)
        button.event_generate("<ButtonRelease-1>", x=5, y=5)
        root.update()
        assert button.ring() is None

        # Erst weg, dann wieder hin: ein `focus_set()` auf das bereits
        # fokussierte Widget erzeugt kein FocusIn und damit auch keinen Ring.
        nachbar.focus_set()
        root.update()
        button.focus_set()
        root.update()
        assert button.ring() == Theme.ACCENT_TINT

    def test_the_select_behaves_the_same(self, window, root):
        feld = Select(window, bg_under=Theme.WINDOW_BG, options=[Option("a", "A")], value="a")
        uk.keep_logical_width(feld, 150)
        feld.pack()
        root.update()
        feld.event_generate("<ButtonPress-1>", x=5, y=5)
        root.update()
        assert not feld.has_state(State.FOCUSED)
        uk.Popover.dismiss_current()
        root.update()
