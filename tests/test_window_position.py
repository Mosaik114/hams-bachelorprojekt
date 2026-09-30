"""Tests der gemerkten Fensterposition.

Die Rechenlogik ist bewusst frei von Tk: `window_position` bekommt die
Arbeitsflächen als Liste und liefert eine Koordinate zurück. So lassen sich
zweiter Monitor, verschwundener Monitor und kaputte Werte prüfen, ohne
Bildschirme umzustecken. Der Lauf gegen die echten Monitore steht daneben.
"""

import tkinter as tk

import pytest

from wisper import ui_kit as uk
from wisper.main import (MIN_VISIBLE_H, MIN_VISIBLE_W, POSITION_FALLBACK,
                         _clean_coordinate, _visible_enough, window_position)

#: Ein üblicher Verbund: Hauptbildschirm plus einer rechts daneben.
PRIMARY = (0, 0, 2560, 1392)
SECOND = (2560, 0, 5120, 1392)
LEFT_OF_PRIMARY = (-1920, 0, 0, 1080)
SIZE = (340, 190)      # logische Fenstermasse; px() macht daraus physische


@pytest.fixture
def size(root):
    """Fenstergrösse in physischen Pixeln, wie die Anwendung sie kennt."""
    return uk.px(SIZE[0]), uk.px(SIZE[1])


class TestCleanCoordinate:
    @pytest.mark.parametrize("value,expected", [
        (0, 0), (48, 48), (-1920, -1920), (2560, 2560), (12.0, 12),
    ])
    def test_numbers_pass(self, value, expected):
        assert _clean_coordinate(value) == expected

    @pytest.mark.parametrize("value", [None, "48", "", True, False, [], {},
                                       float("nan"), float("inf"), 10 ** 9])
    def test_everything_else_is_discarded(self, value):
        assert _clean_coordinate(value) is None

    def test_negative_is_valid(self):
        """Monitore links des Hauptbildschirms haben negative Koordinaten."""
        assert _clean_coordinate(-1920) == -1920


class TestVisibility:
    def test_a_window_in_the_middle_is_fine(self, size):
        assert _visible_enough(600, 400, *size, [PRIMARY])

    def test_a_window_at_the_origin_is_fine(self, size):
        assert _visible_enough(0, 0, *size, [PRIMARY])

    def test_far_beyond_the_right_edge(self, size):
        assert not _visible_enough(2555, 400, *size, [PRIMARY])

    def test_just_inside_the_right_edge(self, size):
        x = PRIMARY[2] - uk.px(MIN_VISIBLE_W)
        assert _visible_enough(x, 400, *size, [PRIMARY])

    def test_above_the_work_area_is_not_reachable(self, size):
        """Was oben herausragt, hat keine Kopfzeile mehr zum Anfassen."""
        assert not _visible_enough(600, -20, *size, [PRIMARY])

    def test_below_the_work_area(self, size):
        assert not _visible_enough(600, PRIMARY[3] - uk.px(MIN_VISIBLE_H) + 4,
                                   *size, [PRIMARY])

    def test_on_the_second_monitor(self, size):
        assert _visible_enough(3000, 300, *size, [PRIMARY, SECOND])

    def test_on_a_monitor_left_of_the_primary(self, size):
        assert _visible_enough(-1800, 200, *size, [LEFT_OF_PRIMARY, PRIMARY])

    def test_the_gap_between_two_monitors_does_not_count(self, size):
        """Zwei Bildschirme nebeneinander, das Fenster genau dazwischen."""
        detached = (0, 0, 1000, 800)
        far = (3000, 0, 4000, 800)
        assert not _visible_enough(1500, 100, *size, [detached, far])

    def test_no_monitor_at_all(self, size):
        assert not _visible_enough(0, 0, *size, [])


class TestWindowPosition:
    def test_a_saved_position_comes_back_unchanged(self, size):
        assert window_position(700, 350, *size, [PRIMARY]) == (700, 350)

    def test_missing_values_fall_back(self, size):
        fallback = (uk.px(POSITION_FALLBACK), uk.px(POSITION_FALLBACK))
        assert window_position(None, None, *size, [PRIMARY]) == fallback

    @pytest.mark.parametrize("saved", [(700, None), (None, 350)])
    def test_half_a_position_is_no_position(self, size, saved):
        fallback = (uk.px(POSITION_FALLBACK), uk.px(POSITION_FALLBACK))
        assert window_position(saved[0], saved[1], *size, [PRIMARY]) == fallback

    @pytest.mark.parametrize("saved", [("links", "oben"), (10 ** 9, 10 ** 9),
                                       (float("nan"), 0)])
    def test_broken_values_fall_back(self, size, saved):
        result = window_position(saved[0], saved[1], *size, [PRIMARY])
        assert result == (uk.px(POSITION_FALLBACK), uk.px(POSITION_FALLBACK))

    def test_the_second_monitor_is_kept(self, size):
        assert window_position(3000, 300, *size, [PRIMARY, SECOND]) == (3000, 300)

    def test_a_removed_monitor_brings_the_window_home(self, size):
        """Wisper lag auf Monitor 2, den es nicht mehr gibt."""
        result = window_position(3000, 300, *size, [PRIMARY])
        assert result == (uk.px(POSITION_FALLBACK), uk.px(POSITION_FALLBACK))

    def test_a_negative_position_survives(self, size):
        assert window_position(-1800, 200, *size,
                               [LEFT_OF_PRIMARY, PRIMARY]) == (-1800, 200)

    def test_the_fallback_follows_the_primary_monitor(self, size):
        """Liegt der Hauptbildschirm links im Negativen, folgt der Rückfall."""
        primary_left = (-1920, 0, 0, 1080)
        result = window_position(None, None, *size, [primary_left, PRIMARY])
        assert result == (-1920 + uk.px(POSITION_FALLBACK), uk.px(POSITION_FALLBACK))

    def test_a_shrunken_resolution_pushes_the_window_back(self, size):
        small = (0, 0, 1280, 720)
        assert window_position(2000, 100, *size, [small]) != (2000, 100)

    def test_a_taskbar_at_the_top_shifts_the_fallback(self, size):
        with_taskbar = (0, 48, 2560, 1392)
        result = window_position(None, None, *size, [with_taskbar])
        assert result == (uk.px(POSITION_FALLBACK), 48 + uk.px(POSITION_FALLBACK))


class TestNoDrift:
    def test_ten_cycles_keep_the_same_coordinates(self, size):
        """Speichern und Laden darf die Zahl nicht Lauf für Lauf verschieben."""
        position = (812, 377)
        for _ in range(10):
            position = window_position(position[0], position[1], *size, [PRIMARY])
        assert position == (812, 377)

    @pytest.mark.parametrize("dpi", [96, 120, 144, 192])
    def test_no_drift_across_scalings(self, root, dpi):
        """Physische Koordinaten bleiben physisch — auch bei 200 %."""
        before = uk.Scale.dpi
        try:
            uk.Scale.apply(root, dpi)
            size = (uk.px(SIZE[0]), uk.px(SIZE[1]))
            position = (812, 377)
            for _ in range(10):
                position = window_position(position[0], position[1], *size, [PRIMARY])
            assert position == (812, 377)
        finally:
            uk.Scale.apply(root, before)

    def test_the_fallback_scales_with_the_dpi(self, root):
        """Der Rückfallabstand ist ein Entwurfsmass und wächst mit."""
        before = uk.Scale.dpi
        try:
            uk.Scale.apply(root, 192)
            size = (uk.px(SIZE[0]), uk.px(SIZE[1]))
            assert window_position(None, None, *size, [PRIMARY]) == (96, 96)
        finally:
            uk.Scale.apply(root, before)


class TestWorkAreas:
    def test_the_list_is_never_empty(self):
        assert uk.work_areas()

    def test_every_entry_is_a_sane_rectangle(self):
        for left, top, right, bottom in uk.work_areas():
            assert right > left and bottom > top

    def test_the_primary_monitor_comes_first(self):
        """Der Rückfall setzt darauf, dass Eintrag null der Hauptschirm ist."""
        left, top, _right, _bottom = uk.work_areas()[0]
        assert left <= 0 and top >= 0

    def test_it_matches_the_area_under_the_window(self, root):
        """Beide Wege beschreiben dieselben Bildschirme."""
        single = uk.work_area(root)
        assert single in uk.work_areas() or single[2] > 0


class TestApplication:
    """Die Anwendungsseite ohne Fenster, Tray und Modell."""

    class _FakeRoot:
        def __init__(self, x=100, y=200):
            self.x, self.y = x, y
            self.jobs = []
            self.cancelled = []
            self.mapped = True

        def after(self, _delay, callback=None):
            self.jobs.append(callback)
            return f"job{len(self.jobs)}"

        def after_cancel(self, job):
            self.cancelled.append(job)

        def winfo_exists(self):
            return True

        def winfo_ismapped(self):
            return self.mapped

        def winfo_x(self):
            return self.x

        def winfo_y(self):
            return self.y

    @pytest.fixture
    def app(self, monkeypatch):
        from wisper.main import CFG, FloatingTranscriberApp as App

        instance = App.__new__(App)
        instance.root = self._FakeRoot()
        instance._window_pos = (100, 200)
        instance._position_job = None
        instance.written = []
        monkeypatch.setattr("wisper.main._persist_config_value",
                            lambda section, key, value:
                            instance.written.append((section, key, value)) or True)
        monkeypatch.setattr(CFG, "window_x", None)
        monkeypatch.setattr(CFG, "window_y", None)
        return instance

    def test_dragging_does_not_write_at_once(self, app):
        """Sonst entstünde beim Ziehen Schreiblast im Hundertfachen."""
        for step in range(50):
            app._remember_position(300 + step, 400 + step)
        assert app.written == []
        assert app._window_pos == (349, 449)

    def test_only_one_pending_write_survives(self, app):
        for step in range(50):
            app._remember_position(300 + step, 400)
        assert len(app.root.cancelled) == 49

    def test_the_delayed_write_stores_both_values(self, app):
        app._remember_position(812, 377)
        app._save_position()
        assert app.written == [("ui", "window_x", 812), ("ui", "window_y", 377)]

    def test_an_unchanged_position_writes_nothing(self, app, monkeypatch):
        from wisper.main import CFG

        monkeypatch.setattr(CFG, "window_x", 812)
        monkeypatch.setattr(CFG, "window_y", 377)
        app._window_pos = (812, 377)
        app._save_position()
        assert app.written == []

    def test_hiding_saves_the_current_place(self, app):
        app.root.x, app.root.y = 640, 480
        app._flush_position()
        assert app.written == [("ui", "window_x", 640), ("ui", "window_y", 480)]

    def test_a_hidden_window_keeps_the_remembered_place(self, app):
        """Ein verstecktes Fenster meldet keine brauchbare Lage."""
        app._window_pos = (812, 377)
        app.root.mapped = False
        app.root.x, app.root.y = 0, 0
        app._flush_position()
        assert app.written == [("ui", "window_x", 812), ("ui", "window_y", 377)]

    def test_flushing_cancels_a_pending_job(self, app):
        app._remember_position(500, 500)
        app._flush_position()
        assert app.root.cancelled == ["job1"]
        assert app._position_job is None
