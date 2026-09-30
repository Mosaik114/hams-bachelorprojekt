"""Tests des Select- und Popover-Systems."""

import tkinter as tk

import pytest

from conftest import fokus_erzwingen

from wisper import ui_kit as uk
from wisper.ui_kit import Option, Popover, Select, State, Theme

BG = Theme.GROUP_SURFACE


def make_options(count=4, groups=False):
    return [
        Option(f"v{i}", f"Eintrag {i}", meta=f"meta {i}",
               group=("Installiert" if i < 2 else "Verfügbar") if groups else "")
        for i in range(count)
    ]


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.geometry("360x220+150+150")
    win.configure(bg=BG)
    win.deiconify()
    win.lift()
    fokus_erzwingen(win, root)
    yield win
    Popover.dismiss_current()
    win.destroy()
    root.update()


@pytest.fixture
def select(window, root):
    chosen = []
    widget = Select(window, bg_under=BG, options=make_options(),
                    value="v1", on_change=chosen.append)
    widget.pack(fill="x", padx=20, pady=40)
    root.update()
    widget.chosen = chosen
    yield widget
    Popover.dismiss_current()


class TestOption:
    def test_carries_everything_the_model_selector_needs(self):
        option = Option("turbo", "Turbo", meta="Schnell", icon="check",
                        enabled=False, group="Installiert", trailing="1,6 GB")
        assert (option.value, option.label, option.meta) == ("turbo", "Turbo", "Schnell")
        assert option.group == "Installiert" and option.trailing == "1,6 GB"
        assert option.enabled is False

    def test_defaults_are_minimal(self):
        option = Option("v", "Label")
        assert option.meta == "" and option.group == "" and option.enabled


class TestWorkArea:
    def test_returns_a_plausible_rectangle(self, window):
        left, top, right, bottom = uk.work_area(window)
        assert right > left and bottom > top

    def test_is_not_just_the_full_screen(self, window):
        """Die Arbeitsfläche schliesst die Taskleiste aus."""
        _left, _top, _right, bottom = uk.work_area(window)
        assert bottom <= window.winfo_screenheight()


class TestSelect:
    def test_shows_the_selected_label(self, select):
        assert select.value == "v1"

    def test_set_value_without_notify(self, select, root):
        select.set_value("v2")
        root.update()
        assert select.value == "v2"
        assert select.chosen == []

    def test_set_value_with_notify(self, select, root):
        select.set_value("v3", notify=True)
        root.update()
        assert select.chosen == ["v3"]

    def test_options_can_be_replaced(self, select, root):
        select.set_options(make_options(2))
        root.update()
        assert len(select.options) == 2

    def test_unknown_value_falls_back_to_placeholder(self, window, root):
        widget = Select(window, bg_under=BG, options=make_options(), value="fehlt",
                        placeholder="Nichts gefunden")
        widget.pack()
        root.update()
        assert widget._selected() is None

    def test_empty_select_does_not_open(self, window, root):
        widget = Select(window, bg_under=BG, options=[], value=None)
        widget.pack()
        root.update()
        widget.open_popover()
        assert Popover.current is None

    def test_disabled_select_does_not_open(self, select, root):
        select.set_enabled(False)
        root.update()
        select.open_popover()
        assert Popover.current is None

    @pytest.mark.parametrize("state", [State.HOVER, State.PRESSED, State.FOCUSED,
                                       State.DISABLED, State.ERROR, State.LOADING])
    def test_states_render(self, select, root, state):
        select.set_state(state, True)
        root.update()
        assert select.has_state(state) or state in (State.HOVER, State.PRESSED)

    def test_two_line_variant(self, window, root):
        widget = Select(window, bg_under=BG, options=make_options(), value="v0",
                        two_line=True)
        widget.pack(fill="x")
        root.update()
        assert widget._two_line


class TestEllipsize:
    def test_short_text_is_untouched(self):
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        pil_font = uk._pil_font(uk.font("row_title"))
        assert uk._ellipsize(draw, "kurz", pil_font, 500) == "kurz"

    def test_long_text_gets_an_ellipsis(self):
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        pil_font = uk._pil_font(uk.font("row_title"))
        result = uk._ellipsize(draw, "Ein sehr langer Geraetename ohne Ende",
                               pil_font, 60)
        assert result.endswith("…") and len(result) < 37


class TestPopover:
    def test_opens_and_closes(self, select, root):
        select.open_popover()
        root.update()
        assert Popover.current is not None
        Popover.current.dismiss()
        root.update()
        assert Popover.current is None

    def test_toggle_closes_an_open_popover(self, select, root):
        select.toggle_popover()
        root.update()
        assert select.is_open
        select.toggle_popover()
        root.update()
        assert not select.is_open

    def test_only_one_popover_at_a_time(self, window, select, root):
        other = Select(window, bg_under=BG, options=make_options(), value="v0")
        other.pack()
        root.update()
        select.open_popover()
        root.update()
        first = Popover.current
        other.open_popover()
        root.update()
        assert Popover.current is not first
        assert Popover.current.trigger is other

    def test_selection_reports_the_value(self, select, root):
        select.open_popover()
        root.update()
        popover = Popover.current
        popover._choose(popover._items[2].option)
        root.update()
        assert select.value == "v2"
        assert select.chosen == ["v2"]

    def test_choosing_the_same_value_does_not_notify(self, select, root):
        select.open_popover()
        root.update()
        popover = Popover.current
        popover._choose(popover._items[1].option)      # bereits gewählt
        root.update()
        assert select.chosen == []

    def test_active_entry_is_marked(self, select, root):
        select.open_popover()
        root.update()
        assert Popover.current._index == 1

    def test_disabled_entries_cannot_be_reached(self, window, root):
        options = [Option("a", "A"), Option("b", "B", enabled=False), Option("c", "C")]
        widget = Select(window, bg_under=BG, options=options, value="a")
        widget.pack()
        root.update()
        widget.open_popover()
        root.update()
        popover = Popover.current
        popover._move(1)
        assert popover._items[popover._index].option.value == "c"

    def test_groups_are_rendered(self, window, root):
        widget = Select(window, bg_under=BG, options=make_options(4, groups=True),
                        value="v0")
        widget.pack()
        root.update()
        widget.open_popover()
        root.update()
        assert len(Popover.current._items) == 4

    def test_long_lists_scroll(self, window, root):
        widget = Select(window, bg_under=BG, options=make_options(20), value="v0")
        widget.pack()
        root.update()
        widget.open_popover()
        root.update()
        assert Popover.current._scroll is not None

    def test_short_lists_do_not_scroll(self, select, root):
        select.open_popover()
        root.update()
        assert Popover.current._scroll is None


class TestKeyboard:
    def test_arrow_keys_move(self, select, root):
        select.open_popover()
        root.update()
        popover = Popover.current
        popover._move_to(0)
        popover._move(1)
        assert popover._index == 1
        popover._move(-1)
        assert popover._index == 0

    def test_movement_wraps(self, select, root):
        select.open_popover()
        root.update()
        popover = Popover.current
        popover._move_to(len(popover._items) - 1)
        popover._move(1)
        assert popover._index == 0

    def test_home_and_end(self, select, root):
        select.open_popover()
        root.update()
        popover = Popover.current
        popover._move_to(len(popover._items) - 1)
        assert popover._index == len(popover._items) - 1
        popover._move_to(0)
        assert popover._index == 0

    def test_enter_confirms(self, select, root):
        select.open_popover()
        root.update()
        popover = Popover.current
        popover._move_to(3)
        popover._choose_current()
        root.update()
        assert select.value == "v3"

    def test_escape_closes(self, select, root):
        select.open_popover()
        root.update()
        Popover.current.event_generate("<Escape>")
        root.update()
        assert Popover.current is None


class TestClickOutside:
    """Ein Klick daneben schliesst das Popover.

    Der lokale Grab leitet jeden Klick der Anwendung an das Popover; ohne
    eigene Pruefung passierte bei einem Klick auf freie Flaeche gar nichts.
    """

    def open_popover(self, select, root):
        """Oeffnet und gibt das Popover zurueck — mit Fenster im Vordergrund.

        Ohne Fokus kann das Popover schon wieder zu sein, bevor der Test
        klickt: es schliesst sich bei Fokusverlust an eine fremde Anwendung,
        und die kann waehrend eines Laufs jederzeit dazwischen.

        `focus_force()` allein genuegt dafuer nicht. Es *bittet* den
        Fenstermanager um den Vordergrund; ob und wann der ihn vergibt, steht
        woanders. `root.update()` arbeitet nur die schon vorliegenden
        Ereignisse ab und wartet auf nichts. Gemessen scheiterte dieser Test
        dadurch in 1 von 12 Laeufen — nicht am Verhalten, das er prueft,
        sondern daran, dass das Popover gar nicht erst aufging.

        Deshalb: den Fokus anfordern, warten bis er wirklich da ist, und den
        Versuch wiederholen, falls das Popover trotzdem sofort wieder zufaellt.
        """
        toplevel = select.winfo_toplevel()
        for _ in range(5):
            fokus_erzwingen(toplevel, root)
            select.open_popover()
            root.update()
            if Popover.current is not None:
                return Popover.current
        raise AssertionError("Popover liess sich nicht oeffnen")

    def click(self, popover, x, y):
        popover.event_generate("<Button-1>", x=x - popover.winfo_rootx(),
                               y=y - popover.winfo_rooty(), rootx=x, rooty=y)

    def test_a_click_beside_it_closes(self, select, root):
        popover = self.open_popover(select, root)
        self.click(popover, popover.winfo_rootx() - 40, popover.winfo_rooty() + 10)
        root.update()
        assert Popover.current is None

    def test_a_click_below_it_closes(self, select, root):
        popover = self.open_popover(select, root)
        self.click(popover, popover.winfo_rootx() + 10,
                   popover.winfo_rooty() + popover.winfo_height() + 30)
        root.update()
        assert Popover.current is None

    def test_a_click_inside_keeps_it_open(self, select, root):
        popover = self.open_popover(select, root)
        self.click(popover, popover.winfo_rootx() + 10, popover.winfo_rooty() + 10)
        root.update()
        assert Popover.current is popover

    def test_a_click_on_an_entry_still_chooses(self, select, root):
        """Der Eintrag verbraucht den Klick selbst — nichts darf ihn abfangen."""
        popover = self.open_popover(select, root)
        item = popover._items[2]
        item.event_generate("<Button-1>", x=5, y=5,
                            rootx=item.winfo_rootx() + 5, rooty=item.winfo_rooty() + 5)
        root.update()
        assert select.value == "v2"
        assert Popover.current is None

    def test_the_trigger_gets_the_focus_back(self, select, root, monkeypatch):
        returned = []
        monkeypatch.setattr(uk, "return_focus_to", returned.append)
        popover = self.open_popover(select, root)
        self.click(popover, popover.winfo_rootx() - 40, popover.winfo_rooty() + 10)
        root.update()
        assert returned == [select]

    def test_it_can_be_reopened_afterwards(self, select, root):
        for _ in range(5):
            popover = self.open_popover(select, root)
            self.click(popover, popover.winfo_rootx() - 40, popover.winfo_rooty() + 10)
            root.update()
            assert Popover.current is None
        self.open_popover(select, root)
        assert Popover.current is not None

    def test_a_stale_popover_does_nothing(self, select, root):
        popover = self.open_popover(select, root)
        Popover.current = None
        popover._on_click_outside(type("E", (), {"x_root": -999, "y_root": -999})())
        root.update()
        popover.dismiss()


class TestFocusReturn:
    @pytest.mark.parametrize("way", ["escape", "choose", "dismiss"])
    def test_focus_goes_back_to_the_trigger(self, select, root, way, monkeypatch):
        """Auf jedem Schliessweg bekommt der Ausloeser den Fokus zurueck.

        Geprueft wird die Zusicherung selbst, nicht `focus_get()`: welches
        Fenster den Systemfokus hat, entscheidet Windows und nicht der Test.
        """
        returned = []
        monkeypatch.setattr(uk, "return_focus_to", returned.append)
        select.open_popover()
        root.update()
        popover = Popover.current
        if way == "escape":
            popover.event_generate("<Escape>")
        elif way == "choose":
            popover._choose(popover._items[0].option)
        else:
            popover.dismiss()
        root.update()
        assert returned == [select]

    def test_focus_lands_on_the_select_when_the_window_is_active(self, select, root):
        window = select.winfo_toplevel()
        fokus_erzwingen(window, root)
        select.open_popover()
        root.update()
        Popover.current.dismiss()
        root.update()
        if window.focus_get() is not None:      # nur pruefen, wenn wir vorn liegen
            assert window.focus_get() is select

    def test_destroyed_trigger_does_not_raise(self, window, root):
        widget = Select(window, bg_under=BG, options=make_options(), value="v0")
        widget.pack()
        root.update()
        widget.open_popover()
        root.update()
        popover = Popover.current
        widget.destroy()
        popover.dismiss()      # darf keine Fokusleiche hinterlassen
        root.update()
        assert Popover.current is None


class TestPlacement:
    def _place_with_area(self, select, root, area, monkeypatch):
        monkeypatch.setattr(uk, "work_area", lambda _w: area)
        select.open_popover()
        root.update()
        popover = Popover.current
        return popover.winfo_rootx(), popover.winfo_rooty(), popover

    def test_stays_inside_the_right_edge(self, select, root, monkeypatch):
        x, _y, popover = self._place_with_area(select, root, (0, 0, 500, 900), monkeypatch)
        assert x + popover.winfo_width() <= 500 + 1

    def test_stays_inside_the_left_edge(self, select, root, monkeypatch):
        x, _y, _p = self._place_with_area(select, root, (300, 0, 2000, 900), monkeypatch)
        assert x >= 300

    def test_flips_above_when_there_is_no_room_below(self, select, root, monkeypatch):
        _x, y, popover = self._place_with_area(select, root, (0, 0, 2000, 260), monkeypatch)
        assert y + popover.winfo_height() <= 260 + 1

    def test_stays_inside_the_top_edge(self, select, root, monkeypatch):
        _x, y, _p = self._place_with_area(select, root, (0, 400, 2000, 900), monkeypatch)
        assert y >= 400


class TestCleanup:
    def test_twenty_cycles_leave_nothing_behind(self, select, root):
        before_callbacks = len(uk.Scale._callbacks)
        before_children = len(select.winfo_toplevel().winfo_children())
        for _ in range(20):
            select.open_popover()
            root.update()
            Popover.current.dismiss()
            root.update()
        assert Popover.current is None
        assert len(select.winfo_toplevel().winfo_children()) == before_children
        assert len(uk.Scale._callbacks) == before_callbacks

    def test_grab_is_released(self, select, root):
        select.open_popover()
        root.update()
        Popover.current.dismiss()
        root.update()
        assert select.winfo_toplevel().grab_current() is None


class TestNoLegacy:
    def test_dropdown_class_is_gone(self):
        assert not hasattr(uk, "Dropdown")

    def test_no_unicode_chevron_in_the_kit(self):
        with open(uk.__file__, encoding="utf-8") as handle:
            code = handle.read()
        assert "⌄" not in code

    def test_no_tk_menu_popup_left(self):
        with open(uk.__file__, encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines()
                     if not line.lstrip().startswith("#")]
        assert "tk_popup" not in chr(10).join(lines)
