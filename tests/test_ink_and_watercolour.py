"""The "Tusche und Aquarell" design language (as in Loratu): ink edge, stone side, painted stage, tabs, mark."""

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
        assert int(button.cget("height")) == uk.px(uk.Height.STANDARD) + uk.px(uk.Depth.CONTROL)
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
        _title, widgets = settings._pages[1]
        assert all(getattr(widget, "hidden_page", False) for widget, _ in widgets)
        select = settings.transcription.model_select
        assert on_hidden_page(select)
        select._stale = True
        settings.select_page(1)
        live_app.root.update()
        assert not on_hidden_page(select) and not select._stale

    def test_wide_windows_put_all_tabs_in_one_row(self, settings, live_app):
        assert settings._tab_columns == 3
        win = settings.win
        win.geometry(f"{uk.px(settings.ONE_ROW_WIDTH + 60)}x{win.winfo_height()}")
        live_app.root.update()
        assert settings._tab_columns == len(settings._tab_buttons)

    def test_the_stage_follows_the_colour_mode(self, settings, live_app):
        before = settings._stage_image
        uk.Theme.apply("dark")
        try:
            settings._repaint()
            assert settings._stage_image is not before
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
