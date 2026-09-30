"""Tests für die DPI-Skalierungsschicht des UI-Kits."""

import tkinter as tk

import pytest

from wisper import ui_kit as uk


@pytest.fixture
def factor(monkeypatch):
    """Setzt den Skalierungsfaktor gezielt, ohne echtes DPI zu brauchen."""

    def _set(value):
        monkeypatch.setattr(uk.Scale, "factor", value)
        monkeypatch.setattr(uk.Scale, "dpi", int(round(96 * value)))
        return value

    return _set


class TestPx:
    def test_zero_stays_zero(self, factor):
        factor(2.0)
        assert uk.px(0) == 0

    def test_identity_at_100_percent(self, factor):
        factor(1.0)
        for value in (1, 3, 8, 12, 18, 40, 332):
            assert uk.px(value) == value

    @pytest.mark.parametrize(
        "scale,value,expected",
        [
            (1.25, 40, 50),
            (1.5, 40, 60),
            (2.0, 40, 80),
            (1.5, 332, 498),
            (2.0, 274, 548),
            (1.25, 3, 4),
        ],
    )
    def test_scaled_values(self, factor, scale, value, expected):
        factor(scale)
        assert uk.px(value) == expected

    def test_small_values_never_vanish(self, factor):
        """Eine 1-px-Trennlinie darf beim Runden nicht verschwinden."""
        factor(0.5)
        assert uk.px(1) >= 1

    def test_monotonic(self, factor):
        factor(1.5)
        assert uk.px(8) <= uk.px(12) <= uk.px(16)


class TestScaleState:
    def test_defaults_to_96_dpi(self):
        assert uk.Scale.BASE_DPI == 96

    def test_factor_follows_dpi(self, factor):
        factor(1.5)
        assert uk.Scale.dpi == 144
        assert uk.px(100) == 150

    def test_apply_updates_factor_and_clears_cache(self, root, monkeypatch):
        monkeypatch.setattr(uk.Scale, "factor", 1.0)
        monkeypatch.setattr(uk.Scale, "dpi", 96)
        uk.init_fonts(root)
        uk.rounded_image(20, 10, 4, "#123456")
        assert uk._img_cache, "Cache sollte nach dem Rendern gefüllt sein"

        try:
            changed = uk.Scale.apply(root, 144)
            assert changed is True
            assert uk.Scale.factor == pytest.approx(1.5)
            assert uk.px(40) == 60
        finally:
            uk.Scale.apply(root, 96)

    def test_apply_is_noop_for_same_dpi(self, root):
        uk.init_fonts(root)
        uk.Scale.apply(root, uk.Scale.dpi)
        assert uk.Scale.apply(root, uk.Scale.dpi) is False

    def test_callbacks_receive_new_factor(self, root, monkeypatch):
        seen = []
        monkeypatch.setattr(uk.Scale, "_callbacks", [seen.append])
        before = uk.Scale.dpi
        try:
            uk.Scale.apply(root, before + 48)
            assert seen and seen[-1] == pytest.approx((before + 48) / 96)
        finally:
            uk.Scale.apply(root, before)

    def test_broken_callback_does_not_break_apply(self, root, monkeypatch):
        def boom(_factor):
            raise RuntimeError("defekter Rückruf")

        monkeypatch.setattr(uk.Scale, "_callbacks", [boom])
        before = uk.Scale.dpi
        try:
            assert uk.Scale.apply(root, before + 24) is True
        finally:
            uk.Scale.apply(root, before)


class TestImageCache:
    def test_cache_key_separates_factors(self, root, monkeypatch):
        uk.init_fonts(root)
        uk._img_cache.clear()

        monkeypatch.setattr(uk.Scale, "factor", 1.0)
        first = uk.rounded_image(30, 12, 4, "#0A84FF")
        monkeypatch.setattr(uk.Scale, "factor", 1.5)
        second = uk.rounded_image(30, 12, 4, "#0A84FF")

        assert first is not second, "Bild aus 100 % darf bei 150 % nicht wiederverwendet werden"
        assert len(uk._img_cache) == 2

    def test_same_factor_hits_cache(self, root):
        uk.init_fonts(root)
        uk._img_cache.clear()
        first = uk.rounded_image(24, 10, 3, "#30D158")
        second = uk.rounded_image(24, 10, 3, "#30D158")
        assert first is second


class TestFonts:
    """Schriften müssen mitwachsen, ohne doppelt skaliert zu werden."""

    def test_points_to_px_at_96_dpi(self, factor):
        factor(1.0)
        assert uk.points_to_px(10) == 13   # 10 pt * 96/72
        assert uk.points_to_px(16) == 21

    def test_points_to_px_scales_with_dpi(self, factor):
        factor(2.0)
        assert uk.points_to_px(10) == 27   # 10 pt * 192/72

    def test_roles_are_named_fonts(self, root):
        uk.init_fonts(root)
        assert uk.font("row_title").name == "WisperFont_row_title"
        assert uk.font("icon").name == "WisperFont_icon"

    def test_existing_widget_grows_with_dpi(self, root):
        """Der eigentliche Zweck benannter Fonts: bestehende Widgets wachsen mit."""
        uk.init_fonts(root)
        before_dpi = uk.Scale.dpi
        label = tk.Label(root, text="Aufnahme starten", font=uk.font("row_title"))
        root.update_idletasks()
        narrow = label.winfo_reqwidth()
        try:
            uk.Scale.apply(root, before_dpi * 2)
            root.update_idletasks()
            wide = label.winfo_reqwidth()
        finally:
            uk.Scale.apply(root, before_dpi)
            label.destroy()
        assert wide > narrow * 1.5, f"Text wuchs nicht mit: {narrow} -> {wide}"

    def test_font_size_is_pixel_based(self, root):
        """Negative Größe = Pixel; damit hängt sie nicht zusätzlich an tk scaling."""
        uk.init_fonts(root)
        assert uk.font("row_title").cget("size") < 0


class TestAwareness:
    def test_enable_returns_known_level(self):
        """Der Prozess ist bereits angemeldet; der Aufruf muss trotzdem antworten."""
        assert uk.enable_dpi_awareness() in {
            "per-monitor-v2",
            "per-monitor",
            "system",
            "keine",
        }

    def test_window_dpi_is_plausible(self, root):
        dpi = uk.window_dpi(root)
        assert 48 <= dpi <= 480
