"""Round 7: tabs that say where you are, a focus that shows on filled buttons, a scroll bar you can see and use.

* `TabBar`: the chosen tab lies on a paper sheet with its word in the accent colour, not filled like the main
  action; one stop in the Tab order, the arrow keys move between the tabs;
* the keyboard focus on a filled button (main action, recording, AI) is drawn in the button's text colour: the
  turquoise focus colour would not show on the turquoise fill (1,07:1);
* `ScrollArea`: the thumb has at least 3:1 to the paper, grows under the pointer, can be dragged, and a click
  into the gutter turns a page;
* tooltips for the icon buttons.
"""

import tkinter as tk
from types import SimpleNamespace

import kit as uk
import pytest
from test_tokens import contrast

from hams.ui_kit.states import ControlStyle, State


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.configure(bg=uk.Theme.WINDOW_BG)
    win.geometry("420x300+200+150")
    yield win
    win.destroy()


def hex_of(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb[:3])


# ---------------------------------------------------------------- tabs


class TestTabBar:
    @pytest.fixture
    def bar(self, window, root):
        chosen = []
        bar = uk.TabBar(window, ["Eins", "Zwei", "Drei"], chosen.append, bg_under=uk.Theme.WINDOW_BG)
        bar.pack(fill="x")
        bar.show(0)
        root.update()
        bar.chosen = chosen
        return bar

    def test_the_chosen_tab_is_a_sheet_not_a_main_action(self, bar):
        assert [b._role for b in bar.buttons] == ["tab_selected", "tab", "tab"]
        fill, text = ControlStyle.resolve("tab_selected", set())
        assert fill == uk.Theme.GROUP_SURFACE and text == uk.Theme.ACCENT_TINT
        assert fill != ControlStyle.resolve("primary", set())[0]
        assert contrast(text, fill) >= 4.5
        assert ControlStyle.resolve("tab", set())[0] is None, "a tab in rest is only its word"

    def test_one_stop_in_the_tab_order(self, bar):
        assert [int(str(b.cget("takefocus"))) for b in bar.buttons] == [1, 0, 0]

    def test_the_arrow_keys_move_and_choose(self, bar, root):
        bar.buttons[0].focus_force()
        root.update()
        bar.buttons[0].event_generate("<Right>")
        root.update()
        assert bar.selected == 1 and bar.chosen == [1]
        bar.buttons[1].event_generate("<End>")
        bar.buttons[2].event_generate("<Right>")  # already the last one: stays
        bar.buttons[2].event_generate("<Home>")
        assert bar.chosen == [1, 2, 0]

    def test_a_click_chooses(self, bar):
        bar.buttons[2]._invoke()
        assert bar.selected == 2 and bar.chosen == [2]
        assert bar.buttons[2]._role == "tab_selected"

    def test_tabs_wrap_without_stretching(self, window, root):
        titles = ["Aufnahme", "Transkription", "Kurzbefehle", "Allgemein"]
        bar = uk.TabBar(window, titles, lambda _i: None, bg_under=uk.Theme.WINDOW_BG)
        bar.pack(fill="x")
        root.update()
        window.geometry("200x300")
        root.update()
        assert bar.rows >= 2
        assert all(b.winfo_width() == b.natural_width() for b in bar.buttons)


# ---------------------------------------------------------------- focus on filled buttons


class TestFocusOnFills:
    @pytest.mark.parametrize("role", ["primary", "recording", "ai"])
    def test_the_focus_edge_takes_the_text_colour_on_a_fill(self, role):
        focused = ControlStyle.surface_spec(role, {State.FOCUSED}, 100, 30, 8, under=uk.Theme.WINDOW_BG)
        fill, text = ControlStyle.resolve(role, set())
        assert focused.focus == text
        assert contrast(focused.focus, fill) >= 3, "the edge must stand out from the fill"

    def test_on_paper_controls_the_focus_stays_turquoise(self):
        focused = ControlStyle.surface_spec("secondary", {State.FOCUSED}, 100, 30, 8, under=uk.Theme.WINDOW_BG)
        assert focused.focus == uk.Theme.FOCUS

    @pytest.mark.parametrize("mode", ["light", "dark"])
    def test_focused_and_unfocused_differ_clearly(self, mode):
        uk.Theme.apply(mode)
        try:
            plain = ControlStyle.surface_spec("primary", set(), 100, 30, 8, under=uk.Theme.WINDOW_BG).render()
            focus = ControlStyle.surface_spec("primary", {State.FOCUSED}, 100, 30, 8, under=uk.Theme.WINDOW_BG)
            shown, rest = focus.render(), plain
            # the strongest change across the top edge (its rows are antialiased)
            best = max(contrast(hex_of(shown.getpixel((50, y))), hex_of(rest.getpixel((50, y)))) for y in range(4))
            assert best >= 3
        finally:
            uk.Theme.apply("light")


# ---------------------------------------------------------------- scroll bar


class TestScrollBar:
    @pytest.fixture
    def area(self, window, root):
        area = uk.ScrollArea(window, bg_under=uk.Theme.WINDOW_BG, edges=True)
        area.pack(fill="both", expand=True)
        for _ in range(30):
            tk.Frame(area.body, height=40, bg=uk.Theme.GROUP_SURFACE).pack(fill="x", pady=2)
        root.update()
        area.refresh()
        assert area.scrollable > 0
        return area

    @pytest.mark.parametrize("mode", ["light", "dark"])
    def test_the_thumb_can_be_seen(self, mode):
        palette = uk.Theme.palette(mode)
        assert contrast(palette["SCROLL_THUMB"], palette["WINDOW_BG"]) >= 3

    def test_the_thumb_grows_under_the_pointer(self, area):
        narrow = area._bar_key[2]
        area._set_bar_hover(True)
        assert area._bar_key[2] > narrow and area._bar_key[1] == uk.Theme.TEXT_TERTIARY
        area._set_bar_hover(False)
        assert area._bar_key[2] == narrow and area._bar_key[1] == uk.Theme.SCROLL_THUMB

    def test_dragging_the_thumb_scrolls(self, area):
        top, length = area._thumb()
        press = SimpleNamespace(x=2, y=top + length // 2)
        assert area._on_bar_press(press) == "break"
        area._on_bar_drag(SimpleNamespace(x=2, y=press.y + 40))
        assert area.offset > 0
        area._on_bar_drag(SimpleNamespace(x=2, y=press.y + 10_000))
        assert area.offset == area.scrollable
        area._on_bar_release(SimpleNamespace(x=-5, y=0))
        assert area._bar_drag is None and not area._bar_hover

    def test_a_click_into_the_gutter_turns_a_page(self, area, monkeypatch):
        monkeypatch.setattr(uk.Animator, "enabled", False)
        top, length = area._thumb()
        area._on_bar_press(SimpleNamespace(x=2, y=top + length + 20))
        assert area.offset > 0
        moved = area.offset
        area._on_bar_press(SimpleNamespace(x=2, y=0))
        assert area.offset < moved

    def test_the_bar_is_no_place_to_move_the_window(self, area):
        from hams.ui_kit.window import is_free_area

        assert not is_free_area(area.bar)

    def test_without_content_beyond_there_are_no_lines(self, window, root):
        area = uk.ScrollArea(window, bg_under=uk.Theme.WINDOW_BG, edges=True)
        area.pack(fill="both", expand=True)
        tk.Frame(area.body, height=20).pack()
        root.update()
        area.refresh()
        assert area.top_edge.cget("bg") == area.bottom_edge.cget("bg") == uk.Theme.WINDOW_BG
        assert not area.more_below


# ---------------------------------------------------------------- tooltips


class TestTooltip:
    @pytest.fixture
    def button(self, window, root):
        button = uk.IconButton(window, "settings", lambda: None, bg_under=uk.Theme.WINDOW_BG, tooltip="Einstellungen")
        button.pack(padx=40, pady=40)
        root.update()
        return button

    def test_an_icon_button_keeps_its_name(self, button):
        assert button.tooltip is not None and button.tooltip.text == "Einstellungen"

    def test_without_a_name_there_is_no_tip(self, window):
        assert uk.IconButton(window, "close", lambda: None, bg_under=uk.Theme.WINDOW_BG).tooltip is None

    def test_it_shows_after_a_rest_and_goes_on_leave(self, button, root):
        tip = button.tooltip
        tip._schedule()
        assert tip._job is not None and not tip.shown, "not at once"
        tip.show()
        root.update()
        assert tip.shown and tip.tip.winfo_viewable()
        label = tip.tip.winfo_children()[0]
        assert label.cget("text") == "Einstellungen"
        tip.hide()
        assert not tip.shown

    def test_it_stands_below_the_button_and_on_the_screen(self, button):
        x, y = button.tooltip.place_for(80, 20)
        assert y >= button.winfo_rooty() + button.winfo_height()
        assert x >= 0
        x, y = button.tooltip.place_for(80, button.winfo_screenheight())
        assert y < button.winfo_rooty(), "above when there is no room below"

    def test_the_keyboard_shows_it_too(self, button, root, monkeypatch):
        from hams.ui_kit.states import InputModality

        monkeypatch.setattr(InputModality, "keyboard", True)
        button.tooltip._on_focus_in()
        assert button.tooltip.shown
        button.event_generate("<FocusOut>")
        assert not button.tooltip.shown

    def test_a_click_hides_it_and_a_destroyed_button_takes_it_along(self, button, root):
        tip = button.tooltip
        tip.show()
        tip.hide()
        tip.show()
        button.destroy()
        assert not tip.shown


# ---------------------------------------------------------------- smaller fixes


class TestSmallerFixes:
    def test_an_error_without_detail_still_says_something(self, live_app):
        live_app.apply_state("error")
        assert live_app.status_label.cget("text")

    def test_a_control_keeps_its_font_role_across_a_dpi_change(self, window):
        select = uk.Select(window, bg_under=uk.Theme.WINDOW_BG, options=[uk.Option("a", "Eins")], value="a")
        assert select._font_role_name() == "row_title"
        select._on_scale_change(1.0)
        assert str(select._font) == str(uk.font("row_title"))

    def test_composed_button_pictures_come_from_the_store(self, window, root):
        button = uk.SecondaryButton(window, "Laden", lambda: None, bg_under=uk.Theme.WINDOW_BG, icon_name="download")
        button.pack()
        root.update()
        first = button._paint(button._last_width, uk.px(button._logical_height))
        assert button._paint(button._last_width, uk.px(button._logical_height)) is first
        stone = uk.IconButton(window, "close", lambda: None, bg_under=uk.Theme.WINDOW_BG)
        assert stone._paint(0, 0) is stone._paint(0, 0)


class TestFlatSurface:
    """A control without a surface of its own (a tab at rest) is painted as plain paper.

    It used to be a rounded shape in the colour of the paper: the smoothed edges left light angles at the
    corners, seen as faint brackets around every tab.
    """

    def test_a_tab_at_rest_is_plain_paper(self, window, root):
        from PIL import ImageTk

        bar = uk.TabBar(window, ["Eins", "Zwei"], lambda _i: None, bg_under=uk.Theme.WINDOW_BG)
        bar.pack(fill="x")
        root.update()
        bar.show(0)
        root.update()
        rest = bar.buttons[1]
        image = ImageTk.getimage(rest._image).convert("RGB")
        paper = tuple(int(uk.Theme.WINDOW_BG[i : i + 2], 16) for i in (1, 3, 5))
        assert image.getcolors() == [(image.width * image.height, paper)]
        bar.destroy()

    def test_flat_only_without_ink_side_or_focus(self):
        from hams.ui_kit.surfaces import SurfaceSpec

        assert SurfaceSpec(20, 10, 4, "#FFFFFF").flat
        assert not SurfaceSpec(20, 10, 4, "#FFFFFF", focus="#000000").flat
        assert not SurfaceSpec(20, 10, 4, "#FFFFFF", ink="#000000", ink_width=1.0).flat
        assert SurfaceSpec(20, 10, 4, "#123456").render().getcolors() == [(200, (0x12, 0x34, 0x56, 255))]
