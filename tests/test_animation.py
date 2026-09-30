"""Tests der Mikroanimationen.

Anders als der Rest der Suite laufen diese Tests *mit* eingeschalteter
Animation — die autouse-Sperre aus conftest wird hier bewusst aufgehoben.
"""

import time
import tkinter as tk

import pytest

from wisper import ui_kit as uk
from wisper.ui_kit import Animator, Popover, Select, StatusDot, Switch, Theme

BG = Theme.GROUP_SURFACE


@pytest.fixture
def animated():
    """Animationen fuer diesen Test einschalten und danach aufraeumen."""
    before = Animator.enabled
    Animator.enabled = True
    yield
    Animator.enabled = before
    for token in list(Animator._jobs):
        Animator._jobs.pop(token, None)


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.geometry("320x240+120+120")
    win.configure(bg=BG)
    win.deiconify()
    root.update()
    yield win
    Popover.dismiss_current()
    win.destroy()
    root.update()


def spin(root, seconds):
    """Ereignisschleife laufen lassen — `after`-Jobs brauchen echte Zeit."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.005)


def settle(root, widget=None, timeout=5.0):
    """Laeuft, bis keine Animation mehr offen ist.

    Feste Wartezeiten waeren eine Wette auf die Maschine: unter Last reichten
    400 ms einmal nicht. Geprueft wird, *dass* das Ziel erreicht wird.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if Animator.pending(widget) == 0:
            root.update()
            return True
        time.sleep(0.005)
    return False


# ------------------------------------------------------------------ Grundlagen


class TestEasing:
    def test_starts_and_ends_exactly(self):
        assert uk.ease_out(0.0) == 0.0
        assert uk.ease_out(1.0) == 1.0

    def test_is_monotonic(self):
        values = [uk.ease_out(i / 20) for i in range(21)]
        assert values == sorted(values)

    def test_front_loaded(self):
        """Auslaufend heisst: die halbe Zeit deckt mehr als die halbe Strecke."""
        assert uk.ease_out(0.5) > 0.5

    def test_out_of_range_is_clamped(self):
        assert uk.ease_out(-1.0) == 0.0 and uk.ease_out(2.0) == 1.0


class TestBlend:
    def test_ends_are_the_originals(self):
        assert uk.blend("#2E2E32", "#30D158", 0.0) == "#2E2E32"
        assert uk.blend("#2E2E32", "#30D158", 1.0) == "#30D158"

    def test_middle_lies_between(self):
        mixed = uk.blend("#000000", "#FFFFFF", 0.5)
        assert mixed == "#808080"

    def test_out_of_range_is_clamped(self):
        assert uk.blend("#000000", "#FFFFFF", 5.0) == "#FFFFFF"


class TestSystemPreference:
    def test_returns_a_boolean(self):
        assert isinstance(uk.system_animations_enabled(), bool)

    def test_init_sets_the_flag(self):
        before = Animator.enabled
        try:
            assert uk.init_animations() == Animator.enabled
        finally:
            Animator.enabled = before


# ------------------------------------------------------------------- Animator


class TestAnimator:
    def test_disabled_jumps_to_the_end(self, window, root):
        seen = []
        Animator.enabled = False
        assert Animator.run(window, "x", 200, seen.append) is False
        assert seen == [1.0]
        assert Animator.pending(window) == 0

    def test_disabled_still_calls_done(self, window):
        finished = []
        Animator.enabled = False
        Animator.run(window, "x", 200, lambda _t: None, lambda: finished.append(1))
        assert finished == [1]

    def test_running_reaches_one(self, animated, window, root):
        seen = []
        finished = []
        Animator.run(window, "x", 120, seen.append, lambda: finished.append(1))
        assert settle(root, window)
        assert seen[0] < 1.0, "erster Schritt darf nicht schon am Ziel sein"
        assert seen[-1] == 1.0
        assert finished == [1]
        assert Animator.pending(window) == 0

    def test_intermediate_values_are_in_order(self, animated, window, root):
        seen = []
        Animator.run(window, "x", 150, seen.append)
        assert settle(root, window)
        assert seen == sorted(seen)
        assert len(seen) >= 3, f"zu wenige Bilder: {len(seen)}"

    def test_a_second_run_replaces_the_first(self, animated, window, root):
        first, second = [], []
        Animator.run(window, "x", 400, first.append)
        spin(root, 0.05)
        Animator.run(window, "x", 100, second.append)
        assert settle(root, window)
        assert first[-1] < 1.0, "der abgeloeste Lauf darf nicht zu Ende laufen"
        assert second[-1] == 1.0
        assert Animator.pending(window) == 0

    def test_different_keys_run_side_by_side(self, animated, window, root):
        Animator.run(window, "a", 300, lambda _t: None)
        Animator.run(window, "b", 300, lambda _t: None)
        assert Animator.pending(window) == 2
        assert settle(root, window)
        assert Animator.pending(window) == 0

    def test_cancel_stops_it(self, animated, window, root):
        seen = []
        Animator.run(window, "x", 400, seen.append)
        spin(root, 0.05)
        Animator.cancel(window, "x")
        count = len(seen)
        spin(root, 0.2)
        assert len(seen) == count
        assert Animator.pending(window) == 0

    def test_destroy_takes_the_timers_along(self, animated, root):
        victim = tk.Toplevel(root)
        root.update()
        Animator.run(victim, "x", 500, lambda _t: None)
        assert Animator.pending(victim) == 1
        victim.destroy()
        root.update()
        assert Animator.pending(victim) == 0

    def test_a_dying_widget_does_not_raise(self, animated, root):
        victim = tk.Toplevel(root)
        root.update()

        def step(_fraction):
            victim.winfo_width()      # wirft TclError, sobald zerstoert

        Animator.run(victim, "x", 300, step)
        victim.destroy()
        spin(root, 0.2)      # darf keine Ausnahme in die Schleife tragen
        assert Animator.pending(victim) == 0

    def test_zero_duration_is_immediate(self, animated, window):
        seen = []
        assert Animator.run(window, "x", 0, seen.append) is False
        assert seen == [1.0]


# --------------------------------------------------------------------- Switch


class TestSwitchAnimation:
    def _switch(self, window, root, value=False):
        changes = []
        switch = Switch(window, bg_under=BG, value=value, command=changes.append)
        switch.pack(pady=20)
        root.update()
        switch.changes = changes
        return switch

    def test_state_changes_before_the_movement(self, animated, window, root):
        """Der Schalter meldet sofort — die Bewegung darf nichts verzoegern."""
        switch = self._switch(window, root)
        switch.toggle()
        assert switch.value is True
        assert switch.changes == [True]
        assert switch._progress < 1.0, "der Knopf steht noch nicht am Ziel"

    def test_the_knob_arrives(self, animated, window, root):
        switch = self._switch(window, root)
        switch.toggle()
        assert settle(root, switch)
        assert switch._progress == 1.0
        assert Animator.pending(switch) == 0

    def test_intermediate_positions_exist(self, animated, window, root):
        switch = self._switch(window, root)
        positions = []
        real_draw = switch._draw

        def spy():
            positions.append(switch._progress)
            real_draw()

        switch._draw = spy
        switch.toggle()
        assert settle(root, switch)
        between = [p for p in positions if 0.0 < p < 1.0]
        assert between, f"keine Zwischenstellung: {positions}"

    def test_a_quick_reversal_turns_around(self, animated, window, root):
        """Zwanzig Klicks duerfen keine Warteschlange aufbauen."""
        switch = self._switch(window, root)
        for _ in range(20):
            switch.toggle()
            spin(root, 0.01)
        assert settle(root, switch)
        assert switch.value is False          # gerade Anzahl
        assert switch._progress == 0.0
        assert Animator.pending(switch) == 0
        assert len(switch.changes) == 20

    def test_the_track_follows_the_knob(self, animated, window, root):
        switch = self._switch(window, root)
        switch.toggle()
        spin(root, 0.05)
        colour = switch._track_colour()
        assert colour not in (Theme.CONTROL_SURFACE, Theme.SUCCESS), colour
        assert settle(root, switch)
        assert switch._track_colour() == Theme.SUCCESS

    def test_disabled_does_not_move(self, animated, window, root):
        switch = self._switch(window, root)
        switch.set_enabled(False)
        switch.toggle()
        spin(root, 0.2)
        assert switch.value is False and switch._progress == 0.0

    def test_destroy_during_the_movement(self, animated, window, root):
        switch = self._switch(window, root)
        switch.toggle()
        spin(root, 0.03)
        switch.destroy()
        spin(root, 0.3)
        assert Animator.pending(switch) == 0

    def test_without_animation_it_jumps(self, window, root):
        Animator.enabled = False
        switch = self._switch(window, root)
        switch.toggle()
        assert switch._progress == 1.0
        assert Animator.pending(switch) == 0


# ------------------------------------------------------------------ StatusDot


class TestStatusDotAnimation:
    def _dot(self, window, root, status="idle"):
        dot = StatusDot(window, bg_under=BG, status=status)
        dot.pack()
        root.update()
        return dot

    def test_starts_at_the_old_colour(self, animated, window, root):
        dot = self._dot(window, root, "ready")
        dot.set_status("error")
        assert dot._colour != StatusDot.colour("error")
        assert dot.status == "error", "der Zustand selbst wechselt sofort"

    def test_reaches_the_new_colour(self, animated, window, root):
        dot = self._dot(window, root, "ready")
        dot.set_status("error")
        assert settle(root, dot)
        assert dot._colour == StatusDot.colour("error")
        assert Animator.pending(dot) == 0

    def test_a_change_during_the_fade_starts_where_it_is(self, animated, window, root):
        dot = self._dot(window, root, "ready")
        dot.set_status("error")
        spin(root, 0.05)
        middle = dot._colour
        dot.set_status("warning")
        assert dot._colour == middle, "der neue Lauf setzt an der sichtbaren Farbe an"
        assert settle(root, dot)
        assert dot._colour == StatusDot.colour("warning")

    def test_same_status_does_nothing(self, animated, window, root):
        dot = self._dot(window, root, "ready")
        dot.set_status("ready")
        assert Animator.pending(dot) == 0

    def test_without_animation_it_jumps(self, window, root):
        Animator.enabled = False
        dot = self._dot(window, root, "ready")
        dot.set_status("error")
        assert dot._colour == StatusDot.colour("error")
        assert Animator.pending(dot) == 0

    def test_unknown_status_still_raises(self, animated, window, root):
        dot = self._dot(window, root)
        with pytest.raises(KeyError):
            dot.set_status("gibtsnicht")


# -------------------------------------------------------------------- Popover


class TestPopoverFade:
    def _select(self, window, root):
        options = [uk.Option(f"v{i}", f"Eintrag {i}") for i in range(4)]
        select = Select(window, bg_under=BG, options=options, value="v1")
        select.pack(fill="x", padx=20, pady=30)
        root.update()
        return select

    def test_starts_transparent(self, animated, window, root):
        select = self._select(window, root)
        select.open_popover()
        popover = Popover.current
        assert float(popover.attributes("-alpha")) < Popover.ALPHA

    def test_reaches_full_alpha(self, animated, window, root):
        select = self._select(window, root)
        select.open_popover()
        popover = Popover.current
        assert settle(root, popover)
        assert float(popover.attributes("-alpha")) == pytest.approx(Popover.ALPHA, abs=0.01)
        assert Animator.pending(popover) == 0

    def test_escape_during_the_fade(self, animated, window, root):
        select = self._select(window, root)
        select.open_popover()
        popover = Popover.current
        spin(root, 0.03)
        popover.event_generate("<Escape>")
        spin(root, 0.3)
        assert Popover.current is None
        assert Animator.pending(popover) == 0

    def test_choosing_during_the_fade(self, animated, window, root):
        select = self._select(window, root)
        select.open_popover()
        popover = Popover.current
        spin(root, 0.03)
        popover._choose(popover._items[2].option)
        spin(root, 0.3)
        assert select.value == "v2"
        assert Popover.current is None

    def test_reopening_quickly_leaves_nothing_behind(self, animated, window, root):
        select = self._select(window, root)
        for _ in range(10):
            select.open_popover()
            spin(root, 0.02)
            Popover.dismiss_current()
            spin(root, 0.02)
        spin(root, 0.3)
        assert Popover.current is None
        assert Animator.pending() == 0

    def test_without_animation_it_is_opaque_at_once(self, window, root):
        Animator.enabled = False
        select = self._select(window, root)
        select.open_popover()
        popover = Popover.current
        assert float(popover.attributes("-alpha")) == pytest.approx(Popover.ALPHA, abs=0.01)


# --------------------------------------------------------------------- Ruhe


class TestIdle:
    def test_nothing_pending_after_everything_settles(self, animated, window, root):
        changes = []
        switch = Switch(window, bg_under=BG, value=False, command=changes.append)
        switch.pack()
        dot = StatusDot(window, bg_under=BG, status="idle")
        dot.pack()
        root.update()
        switch.toggle()
        dot.set_status("ready")
        assert settle(root)
        assert Animator.pending() == 0, "im Ruhezustand darf kein Job offen sein"

    def test_no_animation_without_a_change(self, animated, window, root):
        switch = Switch(window, bg_under=BG, value=True, command=lambda _v: None)
        switch.pack()
        root.update()
        switch.set(True)      # gleicher Wert
        assert Animator.pending(switch) == 0
