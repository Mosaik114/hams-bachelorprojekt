"""The "Tusche und Aquarell" design language (as in Loratu): ink edge, stone side, painted stage, tabs, mark."""

import sys

import kit as uk
import pytest

from hams import branding
from hams.ui_kit import banner, surfaces
from hams.ui_kit.buttons import on_hidden_page
from hams.ui_kit.states import ControlStyle, State


def rgb(image, x, y):
    return image.convert("RGB").getpixel((x, y))


class TestStoneSurface:
    def test_the_side_lies_below_the_body(self):
        image = surfaces.render_surface(80, 40, 8, "#FFFFFF", side="#000000", depth=6, dither=False)
        assert rgb(image, 40, 10) == (255, 255, 255), "body on top"
        assert rgb(image, 40, 37) == (0, 0, 0), "the side shows below"

    def test_pressed_sinks_onto_the_side(self):
        image = surfaces.render_surface(80, 40, 8, "#FFFFFF", side="#000000", depth=6, pressed=True, dither=False)
        assert rgb(image, 40, 37) == (255, 255, 255), "the side is hidden under the sunk body"
        assert image.getpixel((40, 2))[3] == 0, "the top edge moved down: nothing above the body"

    def test_stretched_and_full_render_agree(self):
        """The nine-slice shortcut must also be exact with a side."""
        options = dict(side="#123456", depth=4, border="#000000", border_w=1.5, dither=False)
        fast = surfaces.render_surface(300, 60, 10, "#EEDDCC", **options)
        full = surfaces._render_full(300, 60, 10, "#EEDDCC", **options)
        assert list(fast.getdata()) == list(full.getdata())

    def test_every_role_has_a_side_that_follows_the_state(self):
        for role in ControlStyle.roles():
            assert ControlStyle.side(role, set()).startswith("#")
        assert ControlStyle.side("primary", set()) == uk.Theme.ACCENT_SIDE
        assert ControlStyle.side("primary", {State.DISABLED}) == uk.Theme.CONTROL_SIDE
        assert ControlStyle.side("secondary", {State.ERROR}) == uk.Theme.ERROR_SIDE


class TestStage:
    def test_same_size_same_picture(self):
        a = banner.render_banner(300, 70)
        b = banner.render_banner(300, 70)
        assert a.size == (300, 70) and list(a.getdata()) == list(b.getdata())

    def test_title_and_cross_stand_on_plain_paper(self):
        """No wash where the title (left) and the cross (right) stand, and none at the bottom edge."""
        image = banner.render_banner(400, 80)
        paper = tuple(int(uk.Theme.WINDOW_BG[i : i + 2], 16) for i in (1, 3, 5))

        def near(pixel):
            return max(abs(a - b) for a, b in zip(pixel, paper, strict=True)) <= 6

        assert near(image.getpixel((20, 40)))
        assert near(image.getpixel((392, 40)))
        assert near(image.getpixel((260, 79)))

    def test_the_wash_is_there_but_quiet(self):
        image = banner.render_banner(400, 80)
        paper = tuple(int(uk.Theme.WINDOW_BG[i : i + 2], 16) for i in (1, 3, 5))
        pixel = image.getpixel((300, 20))
        assert 4 < max(abs(a - b) for a, b in zip(pixel, paper, strict=True)) < 60

    def test_the_tk_image_is_cached(self, root):
        assert banner.banner_image(200, 60) is banner.banner_image(200, 60)


class TestMark:
    def test_the_painted_mark_is_centred_and_square(self):
        image = branding.ink_mark_image(64)
        assert image.size == (64, 64)
        left, top, right, bottom = image.getchannel("A").getbbox()
        assert abs((left + right) / 2 - 32) <= 1.5 and abs((top + bottom) / 2 - 32) <= 1.5

    def test_a_missing_file_gives_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(branding, "ASSET_DIR", tmp_path)
        assert branding.ink_mark_image(32) is None

    def test_mark_label_shows_it(self, root):
        from hams.window_mark import mark_label

        label = mark_label(root, bg=uk.Theme.WINDOW_BG)
        assert label.cget("image")
        label.destroy()


class TestIconStone:
    def test_hover_and_press(self, root):
        button = uk.IconButton(root, "close", lambda: None, bg_under=uk.Theme.WINDOW_BG)
        button.pack()
        root.update()
        assert int(button.cget("height")) == uk.px(uk.IconButton.SIZE) + uk.px(uk.Depth.CONTROL)
        button.set_state(State.HOVER, True)
        button.set_state(State.PRESSED, True)
        root.update()
        button.destroy()


class TestSettingsTabs:
    @pytest.fixture
    def settings(self, live_app):
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win

    def test_one_topic_at_a_time(self, settings, live_app):
        assert settings.page == 0
        assert [str(b.cget("text")) for b in settings._tab_buttons] == [title for title, _ in settings._pages]
        assert settings._tab_buttons[0]._role == "primary"
        settings._tab_buttons[4]._invoke()
        live_app.root.update()
        assert settings.page == 4
        assert settings._tab_buttons[4]._role == "primary" and settings._tab_buttons[0]._role == "secondary"
        assert settings.general.group.winfo_ismapped()
        assert not settings.recording.group.winfo_ismapped()

    def test_the_tab_title_is_not_repeated_above_the_group(self, settings, live_app):
        title, widgets = settings._pages[settings.page]
        headers = [w for w, _ in widgets if isinstance(w, uk.SectionHeader) and str(w.cget("text")) == title]
        assert headers and not headers[0].winfo_ismapped()

    def test_hidden_pages_do_not_draw_until_shown(self, settings, live_app):
        settings._stop_warming()  # the ahead layout draws hidden pages on purpose (test_windows_phase5)
        _title, widgets = settings._pages[1]
        assert all(getattr(widget, "hidden_page", False) for widget, _ in widgets)
        select = settings.transcription.model_select
        assert on_hidden_page(select)
        select._stale = True
        settings.select_page(1)
        live_app.root.update()
        assert not on_hidden_page(select) and not select._stale

    def test_tabs_are_as_wide_as_their_word_and_wrap_when_narrow(self, settings, live_app):
        win = settings.win
        win.geometry(f"{uk.px(900)}x{win.winfo_height()}")
        live_app.root.update()
        rows = {button.winfo_y() for button in settings._tab_buttons}
        assert len(rows) == 1, "a wide window holds all tabs in one row"
        for button in settings._tab_buttons:
            assert button.winfo_width() == button.winfo_reqwidth() == button.natural_width(), "not stretched"
        win.geometry(f"{uk.px(400)}x{win.winfo_height()}")
        live_app.root.update()
        assert len({button.winfo_y() for button in settings._tab_buttons}) >= 2
        right = settings._tabs.winfo_width()
        assert all(b.winfo_x() + b.winfo_width() <= right for b in settings._tab_buttons), "nothing sticks out"

    def test_the_stage_follows_the_colour_mode(self, settings, live_app):
        before = settings._stage._images[0]
        uk.Theme.apply("dark")
        try:
            settings._repaint()
            assert settings._stage._images[0] is not before
        finally:
            uk.Theme.apply("light")
            settings._repaint()


class TestDisabledRows:
    def test_a_status_stays_readable_when_the_row_is_off(self, root):
        row = uk.SettingRow(root, "Modell", variant="status", description="Ollama erreichbar")
        row.set_enabled(False)
        assert row.sub_label.cget("fg") == uk.Theme.TEXT_TERTIARY
        assert row.title_label.cget("fg") == uk.Theme.TEXT_DISABLED
        row.destroy()

    def test_a_description_greys_out_readably(self, root):
        row = uk.SettingRow(root, "Ton", variant="description", description="Ein leises Klicken")
        row.set_enabled(False)
        assert row.sub_label.cget("fg") == uk.Theme.TEXT_DISABLED
        row.destroy()


class TestOneCache:
    def test_the_budget_holds_by_count_and_bytes(self, root):
        from PIL import Image, ImageTk

        from hams.ui_kit.caches import ImageCache

        cache = ImageCache(limit=3, budget_bytes=10 * 10 * 4 * 2)
        for number in range(5):
            cache.store(number, ImageTk.PhotoImage(Image.new("RGBA", (10, 10))))
        assert len(cache) == 2 and cache.bytes == 800
        assert cache.fetch(4) is not None and cache.fetch(0) is None
        assert (cache.hits, cache.misses) == (1, 1) and cache.evictions == 3
        cache.clear()
        assert cache.bytes == 0

    def test_the_stage_and_the_mark_go_through_it(self, root):
        from hams.ui_kit.caches import _img_cache
        from hams.window_mark import mark_photo

        banner.banner_image(123, 45)
        assert any(isinstance(key, tuple) and key[:2] == ("banner", 123) for key in _img_cache)
        assert mark_photo() is mark_photo()

    def test_the_mark_fills_its_square(self):
        image = branding.ink_mark_image(40)
        left, top, right, bottom = image.getchannel("A").getbbox()
        assert max(right - left, bottom - top) >= 38, "trimmed to what is painted"


class TestSurfaceSpec:
    def test_the_side_has_no_contour(self):
        spec = surfaces.SurfaceSpec(60, 30, 6, "#FFFFFF", ink="#000000", ink_width=1.0, side="#888888", depth=6)
        image = spec.render().convert("RGB")
        assert image.getpixel((30, 27)) == (136, 136, 136), "the side below is plain, no ink line in it"


class TestBundledFont:
    @pytest.mark.skipif(sys.platform != "win32", reason="private font loading is a Windows path")
    def test_nunito_is_used_for_headings_and_buttons(self, root):
        uk.init_fonts(root, force=True)
        assert uk.font("window_title").actual("family").startswith("Nunito")
        assert uk.font("button").actual("weight") == "bold"

    def test_the_licence_ships_with_the_font(self):
        from pathlib import Path

        folder = Path(branding.__file__).parent / "assets" / "fonts"
        assert (folder / "Nunito-wght.ttf").is_file()
        assert "SIL Open Font License" in (folder / "OFL.txt").read_text(encoding="utf-8")


@pytest.fixture
def window(root):
    import tkinter as tk

    win = tk.Toplevel(root)
    win.geometry("420x200+80+80")
    root.update()
    yield win
    win.destroy()


class TestStoneText:
    def test_the_label_sits_on_the_middle_of_the_surface(self, window, root):
        button = uk.SecondaryButton(window, "Überspringen", lambda: None, bg_under=uk.Theme.WINDOW_BG)
        button.pack()
        root.update()
        x, y = button.coords(button.find_withtag("label")[0])
        assert x == button.winfo_width() // 2
        assert y == uk.px(uk.Height.PRIMARY) // 2, "centred on the surface, not on surface plus side"
        button.destroy()

    def test_a_button_is_never_narrower_than_its_word(self, window, root):
        button = uk.SecondaryButton(window, "Erneut versuchen", lambda: None, bg_under=uk.Theme.WINDOW_BG)
        uk.keep_logical_width(button, 40)
        button.pack()
        root.update()
        text = int(button.tk.call("font", "measure", button.cget("font"), "Erneut versuchen"))
        assert button.winfo_width() >= text + 2 * uk.px(button.TEXT_PADDING)
        button.destroy()

    def test_pressed_text_sinks_with_the_surface(self, window, root):
        from hams.ui_kit.states import State

        button = uk.SecondaryButton(window, "OK", lambda: None, bg_under=uk.Theme.WINDOW_BG)
        button.pack()
        root.update()
        _x, before = button.coords(button.find_withtag("label")[0])
        button.set_state(State.PRESSED, True)
        _x, after = button.coords(button.find_withtag("label")[0])
        assert after - before == uk.px(uk.Depth.CONTROL)
        button.destroy()

    def test_the_select_writes_its_value_as_text_and_shortens_it(self, window, root):
        select = uk.Select(
            window, bg_under=uk.Theme.WINDOW_BG, options=[uk.Option("a", "Ein sehr, sehr langer Gerätename")], value="a"
        )
        uk.keep_logical_width(select, 120)
        select.pack()
        root.update()
        assert select.shown_text.endswith("…")
        assert select.itemcget(select.find_withtag("label")[0], "font") == str(uk.font("row_title"))
        select.destroy()


class TestSmallStones:
    def test_the_level_fill_stays_inside_the_inked_groove(self, window, root):
        meter = uk.SignalMeter(window, bg_under=uk.Theme.WINDOW_BG)
        meter.pack(fill="x")
        root.update()
        meter.set_level(1.0)
        inset = meter.inset()
        for item in meter.find_all()[1:]:
            left, top, right, bottom = meter.bbox(item)
            assert top >= inset - 1 and left >= inset - 1
        meter.destroy()

    def test_status_dot_and_icon_stone_sizes(self, root):
        dot = uk.StatusDot(root, bg_under=uk.Theme.WINDOW_BG)
        assert int(dot.cget("width")) == uk.px(uk.StatusDot.SIZE_MAIN) == uk.px(10)
        icon = uk.IconButton(root, "settings", lambda: None, bg_under=uk.Theme.WINDOW_BG)
        assert int(icon.cget("width")) == uk.px(34)
        dot.destroy()
        icon.destroy()


class TestRowHoverCorners:
    def test_the_hover_surface_is_rounded_like_the_plate(self, window, root):
        row = uk.SettingRow(window, "Ton", interactive=True, command=lambda: None)
        row.pack(fill="x")
        root.update()
        row._on_enter()
        root.update()
        corners = row._corner_labels
        assert len(corners) == 4 and all(label.winfo_ismapped() for label in corners)
        size = uk.px(uk.Radius.GROUP - uk.Space.GROUP_INSET)
        assert int(corners[0].winfo_reqwidth()) == size
        row._on_leave()
        root.update()
        assert not any(label.winfo_ismapped() for label in corners)

    def test_a_corner_is_plate_outside_and_hover_inside(self):
        from PIL import ImageTk

        from hams.ui_kit.setting_row import corner_image

        image = ImageTk.getimage(corner_image("nw", 16, "#000000", "#FFFFFF")).convert("RGB")
        assert image.getpixel((0, 0)) == (255, 255, 255)
        assert image.getpixel((15, 15)) == (0, 0, 0)


class TestTray:
    def test_every_size_comes_from_its_own_drawing(self, tmp_path):
        from PIL import Image

        frames = branding.tray_images("#123456")
        assert sorted(frames) == list(branding.TRAY_SIZES)
        path = tmp_path / "tray.ico"
        branding.write_tray_ico(frames, path)
        with Image.open(path) as ico:
            assert {16, 20, 24, 32} <= {size[0] for size in ico.info["sizes"]}

    def test_error_has_its_own_tone_on_both_taskbars(self, monkeypatch):
        from hams import tray as tray_mod
        from hams.app import FloatingTranscriberApp

        for light in (False, True):
            monkeypatch.setattr(tray_mod, "taskbar_is_light", lambda light=light: light)
            colours = {state: FloatingTranscriberApp._tray_colour(state) for state in ("ready", "recording", "error")}
            assert len(set(colours.values())) == 3, colours

    def test_on_a_light_taskbar_the_mark_stands_out(self, monkeypatch):
        from hams import tray as tray_mod

        monkeypatch.setattr(tray_mod, "taskbar_is_light", lambda: True)

        def lum(value):
            channels = [int(value[i : i + 2], 16) / 255 for i in (1, 3, 5)]
            lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

        taskbar = lum("#F3F3F3")
        for colour in tray_mod.tray_palette().values():
            assert (taskbar + 0.05) / (lum(colour) + 0.05) >= 3.0, colour

    def test_the_tray_image_carries_all_sizes(self):
        from hams import tray as tray_mod

        image = tray_mod._create_tray_icon_image()
        assert sorted(image.hams_frames) == list(branding.TRAY_SIZES)

    def test_reading_the_taskbar_theme_never_fails(self):
        from hams import tray as tray_mod

        assert tray_mod.taskbar_is_light() in (True, False)


class TestPopoverEdge:
    def test_the_popover_is_drawn_like_a_plate(self, window, root):
        select = uk.Select(window, bg_under=uk.Theme.WINDOW_BG, options=[uk.Option("a", "A")], value="a")
        select.pack()
        root.update()
        select.open_popover()
        root.update()
        popover = uk.Popover.current
        assert popover.cget("bg") == uk.Theme.INK
        assert any(str(child.cget("bg")) == uk.Theme.GROUP_SIDE for child in popover.winfo_children())
        uk.Popover.dismiss_current()
        root.update()
