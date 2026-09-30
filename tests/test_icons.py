"""Tests des Icon-Systems: Raster, Strichstärke, Gewichtung, Farbe, Cache, DPI."""

import pytest

from wisper import ui_kit as uk
from wisper.ui_kit import Theme

INK = "#F2F2F5"
LINE_ICONS = ["settings", "close", "chevron", "check", "download"]


def alpha_channel(image):
    return image.getchannel("A")


def ink_amount(image):
    """Summe der Deckung in Pixeläquivalenten."""
    return sum(alpha_channel(image).getdata()) / 255


def ink_box(image, threshold=20):
    """Bounding Box der sichtbaren Tinte, ohne das Ausschwingen des Filters."""
    alpha = alpha_channel(image)
    width, height = image.size
    points = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if alpha.getpixel((x, y)) > threshold
    ]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.fixture
def factor(monkeypatch):
    def _set(value):
        monkeypatch.setattr(uk.Scale, "factor", value)
        monkeypatch.setattr(uk.Scale, "dpi", int(round(96 * value)))
        return value

    return _set


class TestSet:
    def test_exactly_the_planned_icons(self):
        assert set(uk.icon_names()) == {
            "settings", "close", "chevron", "check", "download", "stop", "trash"
        }

    def test_unknown_icon_fails_loudly(self):
        with pytest.raises(KeyError):
            uk.render_icon("zahnrad", 16, INK)

    def test_settings_is_not_a_gear(self):
        """Das Regler-Motiv besteht aus waagerechten Linien, nicht aus Zähnen."""
        image = uk.render_icon("settings", 32, INK)
        alpha = alpha_channel(image)
        # In den drei Linienhöhen muss über die Breite durchgehend Tinte liegen.
        rows_with_long_runs = 0
        for y in range(32):
            run = sum(1 for x in range(32) if alpha.getpixel((x, y)) > 128)
            if run > 16:
                rows_with_long_runs += 1
        assert rows_with_long_runs >= 6, "keine drei durchgehenden Linien erkennbar"


class TestGrid:
    @pytest.mark.parametrize("name", ["settings", "close", "chevron", "check", "download", "stop"])
    def test_stays_inside_the_box(self, name):
        image = uk.render_icon(name, 16, INK)
        assert image.size == (16, 16)
        left, top, right, bottom = ink_box(image)
        assert left >= 1 and top >= 1, f"{name} klebt an der Kante: {(left, top)}"
        assert right <= 14 and bottom <= 14, f"{name} klebt an der Kante: {(right, bottom)}"

    @pytest.mark.parametrize("name", LINE_ICONS)
    def test_uses_the_available_width(self, name):
        """Kein Icon darf verloren klein in der Box sitzen."""
        left, _top, right, _bottom = ink_box(uk.render_icon(name, 16, INK))
        assert right - left >= 8, f"{name} nur {right - left} von 16 breit"

    def test_stop_is_a_filled_square(self):
        image = uk.render_icon("stop", 16, INK)
        left, top, right, bottom = ink_box(image)
        assert abs((right - left) - (bottom - top)) <= 1, "nicht quadratisch"
        alpha = alpha_channel(image)
        assert alpha.getpixel(((left + right) // 2, (top + bottom) // 2)) > 200


class TestStroke:
    @pytest.mark.parametrize("size,expected", [(16, 1.5), (32, 3.0), (48, 4.5)])
    def test_line_thickness_scales_with_size(self, size, expected):
        """Senkrechter Schnitt durch eine Reglerlinie, abseits der Knöpfe."""
        image = uk.render_icon("settings", size, INK)
        alpha = alpha_channel(image)
        x = int(size * 3.6 / 16)                       # links, vor dem ersten Knopf
        column = [alpha.getpixel((x, y)) for y in range(size)]
        runs, current = [], 0
        for value in column:
            if value > 100:
                current += 1
            elif current:
                runs.append(current)
                current = 0
        if current:
            runs.append(current)
        assert runs, "keine Linie gefunden"
        assert abs(max(runs) - expected) <= 1.6, f"Strichstärke {max(runs)} statt {expected}"

    def test_all_line_icons_share_one_weight_class(self):
        """Kein Motiv darf deutlich schwerer wirken als die übrigen."""
        amounts = {n: ink_amount(uk.render_icon(n, 16, INK)) for n in LINE_ICONS}
        assert max(amounts.values()) / min(amounts.values()) < 4.5, amounts


class TestColour:
    def test_geometry_is_identical_across_colours(self):
        """Farbe ist Parameter — die Geometrie darf sich nicht unterscheiden."""
        bright = uk.render_icon("check", 24, Theme.TEXT_PRIMARY)
        muted = uk.render_icon("check", 24, Theme.TEXT_TERTIARY)
        assert list(alpha_channel(bright).getdata()) == list(alpha_channel(muted).getdata())

    def test_colour_reaches_the_pixels(self):
        image = uk.render_icon("stop", 16, Theme.ERROR)
        left, top, right, bottom = ink_box(image)
        pixel = image.getpixel(((left + right) // 2, (top + bottom) // 2))
        assert pixel[:3] == tuple(int(Theme.ERROR[i:i + 2], 16) for i in (1, 3, 5))

    def test_every_state_colour_renders(self):
        for colour in (Theme.TEXT_PRIMARY, Theme.TEXT_SECONDARY, Theme.TEXT_TERTIARY,
                       Theme.ACCENT_TINT, Theme.RECORDING, Theme.SUCCESS):
            assert uk.render_icon("chevron", 16, colour).size == (16, 16)


class TestDpi:
    @pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
    def test_size_follows_the_factor(self, scale, factor, root):
        factor(scale)
        uk._icon_cache.clear()
        photo = uk.icon("close")
        assert photo.width() == uk.px(uk.ICON_BOX)

    @pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
    def test_proportions_stay_stable(self, scale, factor):
        factor(scale)
        size = uk.px(uk.ICON_BOX)
        image = uk.render_icon("download", size, INK)
        left, top, right, bottom = ink_box(image)
        coverage = (right - left) / size
        assert 0.45 < coverage < 0.85, f"{scale}x: Deckung {coverage:.2f}"

    def test_thickness_grows_with_dpi(self, factor):
        thin = ink_amount(uk.render_icon("close", 16, INK))
        thick = ink_amount(uk.render_icon("close", 32, INK))
        assert thick > thin * 2.5


class TestCache:
    def test_same_request_is_cached(self, root):
        uk._icon_cache.clear()
        first = uk.icon("check")
        second = uk.icon("check")
        assert first is second
        assert len(uk._icon_cache) == 1

    def test_key_separates_motif_size_and_colour(self, root):
        uk._icon_cache.clear()
        uk.icon("check")
        uk.icon("close")
        uk.icon("check", size=24)
        uk.icon("check", color=Theme.ACCENT_TINT)
        assert len(uk._icon_cache) == 4

    def test_key_separates_dpi(self, root, factor):
        uk._icon_cache.clear()
        factor(1.0)
        uk.icon("check")
        factor(2.0)
        uk.icon("check")
        assert len(uk._icon_cache) == 2

    def test_limit_is_respected(self, root):
        uk._icon_cache.clear()
        for size in range(8, 8 + uk.ICON_CACHE_LIMIT + 30):
            uk.icon("close", size=size)
        assert len(uk._icon_cache) == uk.ICON_CACHE_LIMIT
