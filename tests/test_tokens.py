"""Tests für die Design-Tokens: Kontrast, Raster, Radien, Höhen, Schriftrollen."""

import re
from pathlib import Path

import pytest

from wisper.ui_kit import Height, Radius, Space, Theme, _FONT_ROLES

PROJECT = Path(__file__).parent.parent


def luminance(hex_color: str) -> float:
    """Relative Helligkeit nach WCAG 2.1."""
    value = hex_color.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    light, dark = sorted([luminance(foreground), luminance(background)], reverse=True)
    return (light + 0.05) / (dark + 0.05)


#: Alle Pruefungen laufen ueber `Theme.palette(mode)` und tragen deshalb
#: Tokennamen statt Farbwerte. Kein Test stellt den globalen Modus um — die
#: Paletten sind reine Daten und lassen sich ohne Nebenwirkung befragen.
MODES = list(Theme.MODES)

# Text auf Fläche — WCAG AA für normalen Text verlangt 4,5:1
TEXT_ON_SURFACE = [
    ("TEXT_PRIMARY", "WINDOW_BG"),
    ("TEXT_PRIMARY", "GROUP_SURFACE"),
    ("TEXT_PRIMARY", "CONTROL_SURFACE"),
    ("TEXT_PRIMARY", "CONTROL_HOVER"),
    ("TEXT_PRIMARY", "POPOVER_SURFACE"),
    ("TEXT_SECONDARY", "WINDOW_BG"),
    ("TEXT_SECONDARY", "GROUP_SURFACE"),
    ("TEXT_SECONDARY", "CONTROL_SURFACE"),
    ("TEXT_SECONDARY", "CONTROL_HOVER"),
    ("TEXT_SECONDARY", "POPOVER_SURFACE"),
    ("TEXT_TERTIARY", "WINDOW_BG"),
    ("TEXT_TERTIARY", "GROUP_SURFACE"),
    ("ACCENT_TINT", "WINDOW_BG"),
    ("AI", "WINDOW_BG"),
    ("ERROR", "WINDOW_BG"),
    ("ERROR", "GROUP_SURFACE"),
    ("ON_ACCENT", "ACCENT_SURFACE"),
    ("ON_ACCENT", "ACCENT_HOVER"),
    ("ON_ACCENT", "ACCENT_PRESSED"),
    ("ON_SUCCESS", "SUCCESS"),
    # Auf gefuellten Flaechen steht `ON_FILL`, nicht `TEXT_PRIMARY`: im hellen
    # Modus waere der Primaertext nahezu schwarz und auf Rot oder Violett nicht
    # mehr zu lesen.
    ("ON_FILL", "ERROR_SURFACE"),
    ("ON_FILL", "RECORDING_SURFACE"),
    ("ON_FILL", "RECORDING_HOVER"),
    ("ON_FILL", "RECORDING_PRESSED"),
    ("ON_FILL", "AI_SURFACE"),
    ("ON_FILL", "AI_HOVER"),
    ("ON_FILL", "AI_PRESSED"),
]

# Grafische Elemente (Punkte, Balken) — 3:1 nach WCAG 1.4.11
GRAPHIC_ON_SURFACE = [
    ("SUCCESS", "WINDOW_BG"),
    ("WARNING", "WINDOW_BG"),
    ("ERROR", "WINDOW_BG"),
    ("RECORDING", "WINDOW_BG"),
    ("AI", "WINDOW_BG"),
    ("SUCCESS", "GROUP_SURFACE"),
    ("ERROR", "GROUP_SURFACE"),
    ("WARNING", "GROUP_SURFACE"),
    ("AI", "GROUP_SURFACE"),
    ("ACCENT_TINT", "GROUP_SURFACE"),
    ("CONTROL_OUTLINE", "GROUP_SURFACE"),
]


class TestPalettes:
    """Was beide Paletten gemeinsam haben muss, damit der Wechsel traegt."""

    def test_both_modes_carry_the_same_tokens(self):
        assert set(Theme.palette(Theme.LIGHT)) == set(Theme.palette(Theme.DARK))
        assert set(Theme.palette(Theme.LIGHT)) == set(Theme.token_names())

    def test_every_token_is_a_hex_colour(self):
        for mode in MODES:
            for name, value in Theme.palette(mode).items():
                assert re.fullmatch(r"#[0-9A-F]{6}", value), f"{mode}/{name}: {value}"

    def test_the_two_palettes_are_not_the_same(self):
        light, dark = Theme.palette(Theme.LIGHT), Theme.palette(Theme.DARK)
        gleich = {n for n in light if light[n] == dark[n]}
        # Ein paar Toene duerfen sich decken — die Akzentfuellung und die
        # Textfarben auf gefuellten Flaechen tragen in beiden Modi denselben
        # Wert. Der Rest muss sich unterscheiden, sonst waere es kein Theme.
        assert len(gleich) <= 6, sorted(gleich)

    def test_shared_values_form_the_same_groups_in_both_palettes(self):
        """Zwei Tokens mit gleichem Wert muessen ihn in beiden Modi teilen.

        Sonst waere `Theme.remap()` nicht eindeutig: ein Widget, dessen `bg`
        genau dieser Wert ist, liesse sich nicht mehr auf *ein* Token
        zurueckfuehren. Einzige zugelassene Ausnahme ist `ON_SUCCESS` — hell
        weiss wie die anderen Fuelltexte, dunkel ein sehr dunkles Gruen, und
        nie der Hintergrund eines Widgets.
        """
        def gruppen(palette):
            nach_wert = {}
            for name, value in palette.items():
                nach_wert.setdefault(value, set()).add(name)
            return {frozenset(g - {"ON_SUCCESS"}) for g in nach_wert.values()
                    if len(g - {"ON_SUCCESS"}) > 1}

        assert gruppen(Theme.palette(Theme.LIGHT)) == gruppen(Theme.palette(Theme.DARK))


class TestContrast:
    """Getrennte Matrix je Modus — das eine traegt das andere nicht.

    Apples helles Gruen `#30D158` kommt auf dem dunklen Fenstergrund auf
    8,60:1 und auf der hellen Gruppenflaeche auf 1,73:1. Ein gemeinsamer Test
    ueber „die“ Palette haette den hellen Modus nie geprueft.
    """

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("fg,bg", TEXT_ON_SURFACE, ids=lambda v: v)
    def test_text_meets_wcag_aa(self, mode, fg, bg):
        palette = Theme.palette(mode)
        ratio = contrast(palette[fg], palette[bg])
        assert ratio >= 4.5, f"{mode}: {fg} auf {bg} nur {ratio:.2f}:1"

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("fg,bg", GRAPHIC_ON_SURFACE, ids=lambda v: v)
    def test_graphics_meet_wcag_non_text(self, mode, fg, bg):
        palette = Theme.palette(mode)
        ratio = contrast(palette[fg], palette[bg])
        assert ratio >= 3.0, f"{mode}: {fg} auf {bg} nur {ratio:.2f}:1"

    @pytest.mark.parametrize("mode", MODES)
    def test_tertiary_text_stays_on_the_quiet_surfaces(self, mode):
        """Die Zusage lautet: Fenstergrund und Gruppenflaeche, sonst nichts.

        Dunkel reicht `TEXT_TERTIARY` auf der Controlflaeche nicht (3,94:1),
        hell knapp (4,68:1). Statt die Ausnahme festzuschreiben, prueft der
        Test die Zusage — und dass auf allen uebrigen Flaechen
        `TEXT_SECONDARY` traegt.
        """
        palette = Theme.palette(mode)
        for name in ("WINDOW_BG", "GROUP_SURFACE"):
            assert contrast(palette["TEXT_TERTIARY"], palette[name]) >= 4.5
        for name in ("CONTROL_SURFACE", "CONTROL_HOVER", "POPOVER_SURFACE"):
            assert contrast(palette["TEXT_SECONDARY"], palette[name]) >= 4.5

    @pytest.mark.parametrize("mode", MODES)
    def test_hover_never_reduces_contrast_below_aa(self, mode):
        """Hover verdunkelt; damit darf der Kontrast nur steigen."""
        palette = Theme.palette(mode)
        for text, base, hover in (("ON_ACCENT", "ACCENT_SURFACE", "ACCENT_HOVER"),
                                  ("ON_FILL", "RECORDING_SURFACE", "RECORDING_HOVER"),
                                  ("ON_FILL", "AI_SURFACE", "AI_HOVER")):
            assert (contrast(palette[text], palette[hover])
                    >= contrast(palette[text], palette[base])), f"{mode}: {base}"

    @pytest.mark.parametrize("mode", MODES)
    def test_hover_darkens_the_filled_surfaces(self, mode):
        palette = Theme.palette(mode)
        for base, hover, pressed in (("ACCENT_SURFACE", "ACCENT_HOVER", "ACCENT_PRESSED"),
                                     ("RECORDING_SURFACE", "RECORDING_HOVER",
                                      "RECORDING_PRESSED"),
                                     ("AI_SURFACE", "AI_HOVER", "AI_PRESSED")):
            werte = [luminance(palette[n]) for n in (base, hover, pressed)]
            assert werte == sorted(werte, reverse=True), f"{mode}: {base}"


class TestSurfaceLadder:
    """Die Ebenen treten Schritt fuer Schritt aus dem Fenstergrund heraus.

    Die *Richtung* haengt am Modus — dunkel nach oben, hell nach unten. Was in
    beiden Modi gilt, ist die Ordnung: jede Ebene liegt weiter vom Fenstergrund
    entfernt als die vorige, und die Schritte bleiben klein.
    """

    @staticmethod
    def _leiter(mode):
        palette = Theme.palette(mode)
        namen = ("WINDOW_BG", "GROUP_SURFACE", "CONTROL_SURFACE", "CONTROL_HOVER")
        return [luminance(palette[n]) for n in namen]

    @pytest.mark.parametrize("mode", MODES)
    def test_four_levels_step_away_from_the_window(self, mode):
        werte = self._leiter(mode)
        erwartet = sorted(werte) if mode == Theme.DARK else sorted(werte, reverse=True)
        assert werte == erwartet, f"{mode}: {werte}"

    @pytest.mark.parametrize("mode", MODES)
    def test_popover_sits_above_group(self, mode):
        """Die schwebende Ebene ist in beiden Modi die *hellere*.

        Dunkel hebt sie sich damit aus der Gruppe heraus, hell wird sie fast
        weiss — beides der uebliche Tiefenhinweis der jeweiligen Welt.
        """
        palette = Theme.palette(mode)
        assert luminance(palette["POPOVER_SURFACE"]) > luminance(palette["GROUP_SURFACE"])

    @pytest.mark.parametrize("mode", MODES)
    def test_hairline_is_visible_against_group(self, mode):
        palette = Theme.palette(mode)
        assert contrast(palette["HAIRLINE"], palette["GROUP_SURFACE"]) >= 1.2

    @pytest.mark.parametrize("mode", MODES)
    def test_edge_highlight_is_brighter_than_control(self, mode):
        palette = Theme.palette(mode)
        assert luminance(palette["EDGE_HIGHLIGHT"]) > luminance(palette["CONTROL_SURFACE"])

    @pytest.mark.parametrize("mode", MODES)
    def test_steps_stay_subtle(self, mode):
        """Apple-artig heißt kleine Schritte — kein harter Sprung zwischen Ebenen."""
        palette = Theme.palette(mode)
        assert contrast(palette["GROUP_SURFACE"], palette["WINDOW_BG"]) < 1.5
        assert contrast(palette["CONTROL_SURFACE"], palette["GROUP_SURFACE"]) < 1.5

    @pytest.mark.parametrize("mode", MODES)
    def test_every_step_is_actually_visible(self, mode):
        """Untergrenze, damit die Hierarchie nicht unbemerkt verschwindet."""
        palette = Theme.palette(mode)
        for a, b in (("GROUP_SURFACE", "WINDOW_BG"),
                     ("CONTROL_SURFACE", "GROUP_SURFACE"),
                     ("CONTROL_HOVER", "CONTROL_SURFACE")):
            ratio = contrast(palette[a], palette[b])
            assert ratio >= 1.10, f"{mode}: {a} zu {b} nur {ratio:.3f}:1"


class TestScales:
    def test_spacing_roles_sit_on_the_grid(self):
        roles = [Space.WINDOW, Space.SECTION_GAP, Space.GROUP_GAP,
                 Space.CONTROL_GAP, Space.ICON_GAP, Space.ROW_X]
        for value in roles:
            assert value % 2 == 0, f"{value} liegt nicht auf dem Raster"

    def test_spacing_scale_is_ascending(self):
        scale = [Space.XS, Space.SM, Space.MD, Space.LG, Space.XL, Space.XXL]
        assert scale == sorted(scale)
        assert len(set(scale)) == len(scale)

    def test_radius_family_is_related(self):
        assert Radius.WINDOW == Radius.POPOVER
        assert Radius.CONTROL == Radius.BUTTON
        assert Radius.SEGMENT < Radius.CONTROL < Radius.GROUP < Radius.WINDOW

    def test_nested_radius_rule(self):
        """Innen = außen − Abstand: eine Gruppe muss in ein Fenster passen."""
        assert Radius.GROUP <= Radius.WINDOW
        assert Radius.CONTROL <= Radius.GROUP

    def test_pill_radius_is_half_height(self):
        assert Radius.pill(24) == 12
        assert Radius.pill(1) == 1

    def test_only_three_control_heights(self):
        heights = {Height.PRIMARY, Height.STANDARD, Height.COMPACT}
        assert len(heights) == 3
        assert Height.COMPACT < Height.STANDARD < Height.PRIMARY


class TestFontRoles:
    REQUIRED = {
        "window_title", "section", "row_title", "description",
        "status", "button", "shortcut", "meta",
    }

    def test_all_required_roles_exist(self):
        assert self.REQUIRED <= set(_FONT_ROLES)

    def test_only_titles_and_buttons_are_bold(self):
        """Apple-artig: wenig Fettung."""
        bold = {r for r, (_f, _s, w) in _FONT_ROLES.items() if w == "bold"}
        assert bold == {"window_title", "section", "button"}

    def test_small_optical_size_for_small_text(self):
        """Segoe UI Variable Small ist für 8–12 pt gezeichnet."""
        for role in ("description", "meta", "section"):
            assert _FONT_ROLES[role][0] == "small"

    def test_sizes_are_plausible(self):
        for role, (_family, points, _weight) in _FONT_ROLES.items():
            assert 8 <= points <= 20, f"{role}: {points} pt"


class TestNoRawValues:
    """Nach dem Aufräumen darf außerhalb des Token-Blocks keine Rohfarbe stehen."""

    def test_main_has_no_hex_colors(self):
        source = (PROJECT / "wisper" / "main.py").read_text(encoding="utf-8")
        found = re.findall(r'"#[0-9A-Fa-f]{3,8}"', source)
        assert not found, f"Rohfarben in main.py: {found}"

    def test_ui_kit_hex_colors_only_inside_theme(self):
        source = (PROJECT / "wisper" / "ui_kit.py").read_text(encoding="utf-8")
        after_theme = source.split("class Space:", 1)[1]
        found = re.findall(r'"#[0-9A-Fa-f]{3,8}"', after_theme)
        assert not found, f"Rohfarben außerhalb der Token-Klasse: {found}"

    def test_old_palette_is_gone(self):
        source = (PROJECT / "wisper" / "main.py").read_text(encoding="utf-8")
        assert "COLOR_" not in source
