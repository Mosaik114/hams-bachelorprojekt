"""Tests der Flächen-Renderpipeline: Geometrie, Lichtkante, Verlauf, Cache."""

import pytest

from wisper import ui_kit as uk

FILL = "#0A6FE8"


def alpha_at(image, x, y):
    return image.getpixel((x, y))[3]


def rgb_at(image, x, y):
    return image.getpixel((x, y))[:3]


def opaque_pixels(image):
    return sum(1 for pixel in image.getdata() if pixel[3] > 127)


@pytest.fixture
def factor(monkeypatch):
    def _set(value):
        monkeypatch.setattr(uk.Scale, "factor", value)
        monkeypatch.setattr(uk.Scale, "dpi", int(round(96 * value)))
        return value

    return _set


class TestGeometry:
    """Die Form muss vollständig in ihre Bounding Box passen."""

    @pytest.mark.parametrize("squircle", [False, True])
    def test_fills_box_edges(self, squircle):
        img = uk.render_surface(80, 40, 10, FILL, squircle=squircle)
        assert img.size == (80, 40)
        # Kantenmitten müssen die Box berühren
        assert alpha_at(img, 40, 0) > 200, "obere Kante erreicht die Box nicht"
        assert alpha_at(img, 40, 39) > 200
        assert alpha_at(img, 0, 20) > 200
        assert alpha_at(img, 79, 20) > 200

    @pytest.mark.parametrize("squircle", [False, True])
    def test_corners_are_rounded_away(self, squircle):
        img = uk.render_surface(80, 40, 12, FILL, squircle=squircle)
        for x, y in ((0, 0), (79, 0), (0, 39), (79, 39)):
            assert alpha_at(img, x, y) < 60, f"Ecke {x},{y} ist nicht gerundet"

    @pytest.mark.parametrize("squircle", [False, True])
    def test_nothing_spills_outside(self, squircle):
        """Kein Pixel darf ausserhalb liegen — das prüft die Boxgrösse selbst."""
        img = uk.render_surface(60, 30, 8, FILL, squircle=squircle)
        assert img.size == (60, 30)
        assert opaque_pixels(img) < 60 * 30, "Form füllt die Box vollständig aus"

    def test_center_has_exact_fill_colour(self):
        img = uk.render_surface(60, 30, 8, FILL, gradient=0.0, dither=False)
        assert rgb_at(img, 30, 15) == (0x0A, 0x6F, 0xE8)
        assert alpha_at(img, 30, 15) == 255

    def test_squircle_corner_is_fuller_than_arc(self):
        """Die Superellipse schneidet weniger von der Ecke ab als ein Kreisbogen."""
        arc = opaque_pixels(uk.render_surface(64, 64, 20, FILL, squircle=False))
        squircle = opaque_pixels(uk.render_surface(64, 64, 20, FILL, squircle=True))
        assert squircle > arc, f"Squircle {squircle} nicht füllender als Bogen {arc}"

    def test_radius_is_clamped(self):
        """Ein zu grosser Radius darf die Form nicht zerstören."""
        img = uk.render_surface(20, 20, 99, FILL, squircle=True)
        assert img.size == (20, 20)
        assert alpha_at(img, 10, 10) == 255

    def test_zero_radius_is_a_rectangle(self):
        img = uk.render_surface(20, 20, 0, FILL, squircle=True)
        assert alpha_at(img, 0, 0) > 200


class TestEdgeHighlight:
    def test_top_is_brighter_than_body(self):
        plain = uk.render_surface(60, 30, 6, FILL, dither=False)
        lit = uk.render_surface(60, 30, 6, FILL, edge="#FFFFFF", dither=False)
        assert sum(rgb_at(lit, 30, 0)) > sum(rgb_at(plain, 30, 0))

    def test_bottom_is_untouched(self):
        plain = uk.render_surface(60, 30, 6, FILL, dither=False)
        lit = uk.render_surface(60, 30, 6, FILL, edge="#FFFFFF", dither=False)
        assert rgb_at(lit, 30, 29) == rgb_at(plain, 30, 29)

    def test_does_not_leak_past_the_rounding(self):
        """Die Kante darf die Ecke nicht ausserhalb der Form aufhellen."""
        lit = uk.render_surface(60, 30, 10, FILL, edge="#FFFFFF")
        assert alpha_at(lit, 0, 0) < 60

    def test_body_below_the_rim_keeps_fill(self):
        lit = uk.render_surface(60, 30, 6, FILL, edge="#FFFFFF", dither=False)
        assert rgb_at(lit, 30, 20) == (0x0A, 0x6F, 0xE8)


class TestGradient:
    def test_top_brighter_than_bottom(self):
        img = uk.render_surface(60, 40, 6, FILL, gradient=0.05, dither=False)
        assert sum(rgb_at(img, 30, 2)) > sum(rgb_at(img, 30, 38))

    def test_stays_subtle(self):
        """Wenige Prozent — kein sichtbarer Verlauf."""
        img = uk.render_surface(60, 40, 6, FILL, gradient=0.03, dither=False)
        top, bottom = rgb_at(img, 30, 2), rgb_at(img, 30, 38)
        assert max(t - b for t, b in zip(top, bottom)) < 24

    def test_without_gradient_column_is_flat(self):
        img = uk.render_surface(60, 40, 6, FILL, gradient=0.0, dither=False)
        assert rgb_at(img, 30, 5) == rgb_at(img, 30, 35)

    def test_dither_keeps_mean_colour(self):
        """Rauschen darf die mittlere Farbe nicht verschieben."""
        def mean(image):
            pixels = [p for p in image.getdata() if p[3] == 255]
            return [sum(p[i] for p in pixels) / len(pixels) for i in range(3)]

        plain = mean(uk.render_surface(80, 40, 6, FILL, gradient=0.04, dither=False))
        noisy = mean(uk.render_surface(80, 40, 6, FILL, gradient=0.04, dither=True))
        assert all(abs(a - b) < 2.0 for a, b in zip(plain, noisy))


class TestShadow:
    def test_shadow_stays_inside_the_box(self):
        img = uk.render_surface(24, 24, 12, "#FFFFFF", shadow=(1.5, 0.5, 0.35))
        assert img.size == (24, 24)
        assert alpha_at(img, 12, 12) == 255

    def test_shadow_leaves_a_soft_rim(self):
        """Unter der Form liegt ein teildeckender Saum statt einer harten Kante."""
        shaded = uk.render_surface(24, 24, 12, "#FFFFFF", shadow=(1.5, 0.8, 0.5))
        column = [alpha_at(shaded, 12, y) for y in range(24)]
        assert any(0 < value < 200 for value in column[-6:]), column[-6:]

    def test_shadow_follows_the_offset(self):
        """Der Versatz nach unten muss unten mehr Schatten erzeugen als oben."""
        shaded = uk.render_surface(24, 24, 12, "#FFFFFF", shadow=(1.5, 0.8, 0.5))
        column = [alpha_at(shaded, 12, y) for y in range(24)]
        assert sum(column[-4:]) > sum(column[:4])

    def test_without_shadow_the_shape_fills_the_box(self):
        plain = uk.render_surface(24, 24, 12, "#FFFFFF")
        assert alpha_at(plain, 12, 23) > 200


class TestDpi:
    """Proportionen müssen über alle Skalierungen identisch bleiben."""

    @pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
    def test_coverage_ratio_is_stable(self, scale, factor):
        factor(scale)
        width, height, radius = uk.px(60), uk.px(30), uk.px(8)
        img = uk.render_surface(width, height, radius, FILL, squircle=True)
        ratio = opaque_pixels(img) / (width * height)
        assert 0.90 < ratio < 0.995, f"Deckung bei {scale}x: {ratio:.3f}"

    @pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
    def test_size_follows_px(self, scale, factor):
        factor(scale)
        img = uk.render_surface(uk.px(40), uk.px(20), uk.px(4), FILL)
        assert img.size == (uk.px(40), uk.px(20))

    def test_ratio_matches_between_scales(self, factor):
        ratios = []
        for scale in (1.0, 1.5, 2.0):
            factor(scale)
            width, height = uk.px(60), uk.px(30)
            img = uk.render_surface(width, height, uk.px(8), FILL, squircle=True)
            ratios.append(opaque_pixels(img) / (width * height))
        assert max(ratios) - min(ratios) < 0.02, f"Proportionen driften: {ratios}"


class TestCache:
    def test_same_call_is_cached(self, root):
        uk._img_cache.clear()
        first = uk.surface(40, 20, 6, FILL)
        second = uk.surface(40, 20, 6, FILL)
        assert first is second
        assert len(uk._img_cache) == 1

    def test_every_parameter_changes_the_key(self, root):
        uk._img_cache.clear()
        uk.surface(40, 20, 6, FILL)
        uk.surface(40, 20, 6, FILL, squircle=True)
        uk.surface(40, 20, 6, FILL, gradient=0.03)
        uk.surface(40, 20, 6, FILL, edge="#FFFFFF")
        uk.surface(40, 20, 6, "#30D158")
        assert len(uk._img_cache) == 5

    def test_limit_is_never_exceeded(self, root):
        uk._img_cache.clear()
        for width in range(10, 10 + uk.CACHE_LIMIT + 60):
            uk.surface(width, 20, 6, FILL)
        assert len(uk._img_cache) == uk.CACHE_LIMIT

    def test_oldest_entry_is_dropped_first(self, root):
        uk._img_cache.clear()
        uk.surface(11, 20, 6, FILL)
        for width in range(100, 100 + uk.CACHE_LIMIT):
            uk.surface(width, 20, 6, FILL)
        keys = [key[0] for key in uk._img_cache]
        assert 11 not in keys

    def test_recent_use_survives(self, root):
        uk._img_cache.clear()
        uk.surface(11, 20, 6, FILL)
        for width in range(100, 100 + uk.CACHE_LIMIT - 1):
            uk.surface(width, 20, 6, FILL)
            uk.surface(11, 20, 6, FILL)      # bleibt in Benutzung
        assert any(key[0] == 11 for key in uk._img_cache)
