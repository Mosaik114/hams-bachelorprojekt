"""Tests für Zustandsmodell, Fokus, Fokusring und StatusDot."""

import tkinter as tk

import pytest

from conftest import fokus_erzwingen

from wisper import ui_kit as uk
from wisper.ui_kit import ControlStyle, FocusMixin, State, StateMixin, StatusDot, Theme


class Probe(tk.Label, StateMixin, FocusMixin):
    """Minimal-Control zum Prüfen der Mixins."""

    def __init__(self, master, role="secondary", **kwargs):
        super().__init__(master, text="Probe", **kwargs)
        self.init_states(role)
        self.redraws = 0
        self.activations = 0
        self.init_focus(self._fire)

    def _fire(self):
        self.activations += 1

    def on_state_change(self):
        self.redraws += 1


@pytest.fixture
def probes(root):
    """Drei Controls in einem *sichtbaren* Fenster.

    Tastaturfokus laesst sich nur an einem gemappten Fenster pruefen: ein
    verstecktes Toplevel vergibt unter Tk keinen Fokus.
    """
    window = tk.Toplevel(root)
    window.geometry("220x140+80+80")
    window.title("Fokus-Test")
    made = [Probe(window) for _ in range(3)]
    for widget in made:
        widget.pack()
    window.deiconify()
    window.lift()
    fokus_erzwingen(window, root)
    yield made
    window.destroy()
    root.update()


class TestPriority:
    """Genau ein Zustand bestimmt die Fläche — und zwar der ranghöchste."""

    @pytest.mark.parametrize(
        "active,expected",
        [
            (set(), State.DEFAULT),
            ({State.HOVER}, State.HOVER),
            ({State.HOVER, State.PRESSED}, State.PRESSED),
            ({State.HOVER, State.SELECTED}, State.HOVER),
            ({State.SELECTED}, State.SELECTED),
            ({State.HOVER, State.PRESSED, State.DISABLED}, State.DISABLED),
            ({State.LOADING, State.HOVER}, State.LOADING),
            ({State.ERROR, State.LOADING, State.PRESSED}, State.ERROR),
            ({State.DISABLED, State.ERROR}, State.DISABLED),
            ({State.FOCUSED}, State.DEFAULT),
        ],
    )
    def test_surface_state(self, active, expected):
        assert State.surface_state(active) == expected

    def test_pressed_beats_hover(self):
        assert State.surface_state({State.HOVER, State.PRESSED}) == State.PRESSED

    def test_disabled_never_looks_like_hover(self):
        fill_disabled, _ = ControlStyle.resolve("primary", {State.DISABLED, State.HOVER})
        fill_hover, _ = ControlStyle.resolve("primary", {State.HOVER})
        assert fill_disabled != fill_hover

    def test_focus_is_orthogonal(self):
        """Fokus ändert die Fläche nicht, ist aber sichtbar."""
        with_focus = ControlStyle.resolve("primary", {State.HOVER, State.FOCUSED})
        without = ControlStyle.resolve("primary", {State.HOVER})
        assert with_focus == without
        assert ControlStyle.ring_colour({State.HOVER, State.FOCUSED}) is not None

    def test_disabled_hides_the_ring(self):
        assert ControlStyle.ring_colour({State.FOCUSED, State.DISABLED}) is None

    def test_interactivity(self):
        assert State.is_interactive(set())
        assert not State.is_interactive({State.DISABLED})
        assert not State.is_interactive({State.LOADING})


class TestControlStyle:
    @pytest.mark.parametrize("role", ["primary", "secondary", "plain", "recording", "ai"])
    @pytest.mark.parametrize("state", [State.DEFAULT, State.HOVER, State.PRESSED,
                                       State.SELECTED, State.DISABLED, State.LOADING,
                                       State.ERROR])
    def test_every_role_answers_every_state(self, role, state):
        fill, text = ControlStyle.resolve(role, {state})
        assert isinstance(text, str) and text.startswith("#")
        assert fill is None or fill.startswith("#")

    def test_unknown_role_fails_loudly(self):
        with pytest.raises(KeyError):
            ControlStyle.resolve("fancy", set())

    def test_plain_has_no_fill_at_rest(self):
        fill, _ = ControlStyle.resolve("plain", set())
        assert fill is None

    def test_disabled_text_is_muted_everywhere(self):
        for role in ("primary", "secondary", "recording", "ai"):
            _fill, text = ControlStyle.resolve(role, {State.DISABLED})
            assert text == Theme.TEXT_TERTIARY

    def test_ring_uses_the_accent_tint(self):
        assert ControlStyle.ring_colour({State.FOCUSED}) == Theme.ACCENT_TINT
        assert ControlStyle.focus_ring() == Theme.ACCENT_TINT


class TestStateMixin:
    def test_set_and_clear(self, probes):
        widget = probes[0]
        widget.set_state(State.HOVER)
        assert widget.has_state(State.HOVER)
        widget.set_state(State.HOVER, False)
        assert not widget.has_state(State.HOVER)

    def test_redraw_only_on_real_change(self, probes):
        widget = probes[0]
        widget.set_state(State.HOVER)
        before = widget.redraws
        widget.set_state(State.HOVER)          # schon aktiv
        assert widget.redraws == before

    def test_disabling_clears_interaction_states(self, probes):
        widget = probes[0]
        widget.set_state(State.HOVER)
        widget.set_state(State.PRESSED)
        widget.set_state(State.DISABLED)
        assert not widget.has_state(State.HOVER)
        assert not widget.has_state(State.PRESSED)
        assert not widget.enabled

    def test_unknown_state_fails_loudly(self, probes):
        with pytest.raises(KeyError):
            probes[0].set_state("blinking")


class TestFocus:
    def test_widgets_accept_focus(self, probes):
        assert all(str(w.cget("takefocus")) == "1" for w in probes)

    def test_focus_sets_the_state(self, probes, root):
        probes[0].focus_set()
        root.update()
        assert probes[0].has_state(State.FOCUSED)

    def test_focus_moves_away_cleanly(self, probes, root):
        probes[0].focus_set()
        root.update()
        probes[1].focus_set()
        root.update()
        assert not probes[0].has_state(State.FOCUSED)
        assert probes[1].has_state(State.FOCUSED)

    def test_tab_chain_forward(self, probes, root):
        probes[0].focus_set()
        root.update()
        assert probes[0].tk_focusNext() is probes[1]
        assert probes[1].tk_focusNext() is probes[2]

    def test_tab_chain_backward(self, probes, root):
        assert probes[2].tk_focusPrev() is probes[1]
        assert probes[1].tk_focusPrev() is probes[0]

    @pytest.mark.parametrize("key", ["<Return>", "<space>"])
    def test_enter_and_space_activate(self, probes, root, key):
        widget = probes[0]
        widget.focus_set()
        root.update()
        before = widget.activations
        widget.event_generate(key)
        root.update()
        assert widget.activations == before + 1

    def test_only_the_focused_widget_activates(self, probes, root):
        probes[0].focus_set()
        root.update()
        probes[0].event_generate("<space>")
        root.update()
        assert probes[0].activations == 1
        assert probes[1].activations == 0

    @pytest.mark.parametrize("key", ["<Return>", "<space>"])
    def test_disabled_ignores_keys(self, probes, root, key):
        widget = probes[0]
        widget.set_state(State.DISABLED)
        widget.event_generate(key)
        root.update()
        assert widget.activations == 0

    def test_loading_ignores_keys(self, probes, root):
        widget = probes[0]
        widget.set_state(State.LOADING)
        widget.event_generate("<space>")
        root.update()
        assert widget.activations == 0

    def test_activation_key_is_consumed(self, probes, root):
        """`break` verhindert, dass Tk die Taste weiterreicht."""
        seen = []
        probes[0].winfo_toplevel().bind("<space>", lambda _e: seen.append(1), add="+")
        probes[0].focus_set()
        root.update()
        probes[0].event_generate("<space>")
        root.update()
        assert seen == []

    def test_focus_returns_to_opener(self, probes, root):
        probes[2].focus_set()
        root.update()
        probes[0].focus_set()
        root.update()
        uk.return_focus_to(probes[2])
        root.update()
        assert probes[2].has_state(State.FOCUSED)

    def test_return_focus_survives_destroyed_widget(self, root):
        holder = tk.Frame(root)
        gone = Probe(holder)
        holder.destroy()
        uk.return_focus_to(gone)      # darf nicht werfen

    def test_return_focus_accepts_none(self):
        uk.return_focus_to(None)


class TestFocusRing:
    def test_ring_is_visible(self):
        plain = uk.render_surface(40, 24, 8, Theme.ACCENT_SURFACE,
                                  ring_width=2, ring_gap=2)
        ringed = uk.render_surface(40, 24, 8, Theme.ACCENT_SURFACE,
                                   ring=Theme.ACCENT_TINT, ring_width=2, ring_gap=2)
        ink = lambda img: sum(p[3] for p in img.getdata())
        assert ink(ringed) > ink(plain) * 1.05

    def test_shape_does_not_jump_when_focused(self):
        """Der Ringplatz ist immer reserviert — die Fläche bleibt gleich gross."""
        plain = uk.render_surface(40, 24, 8, Theme.ACCENT_SURFACE,
                                  ring_width=2, ring_gap=2)
        ringed = uk.render_surface(40, 24, 8, Theme.ACCENT_SURFACE,
                                   ring=Theme.ACCENT_TINT, ring_width=2, ring_gap=2)
        assert plain.size == ringed.size
        # Die Füllung selbst liegt an derselben Stelle
        assert plain.getpixel((20, 12))[:3] == ringed.getpixel((20, 12))[:3]

    def test_ring_sits_outside_the_fill(self):
        img = uk.render_surface(40, 24, 8, Theme.ACCENT_SURFACE,
                                ring=Theme.ACCENT_TINT, ring_width=2, ring_gap=2)
        tint = tuple(int(Theme.ACCENT_TINT[i:i + 2], 16) for i in (1, 3, 5))
        top_edge = [img.getpixel((x, 0))[:3] for x in range(15, 25)]
        assert any(abs(p[0] - tint[0]) + abs(p[2] - tint[2]) < 40 for p in top_edge)

    @pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
    def test_ring_scales(self, scale, monkeypatch):
        monkeypatch.setattr(uk.Scale, "factor", scale)
        monkeypatch.setattr(uk.Scale, "dpi", int(96 * scale))
        img = uk.render_surface(uk.px(40), uk.px(24), uk.px(8), Theme.ACCENT_SURFACE,
                                ring=Theme.ACCENT_TINT,
                                ring_width=uk.px(2), ring_gap=uk.px(2))
        assert img.size == (uk.px(40), uk.px(24))


class TestStatusDot:
    def test_all_states_have_a_colour(self):
        assert set(StatusDot.COLOURS) == {
            "idle", "ready", "recording", "processing", "ai", "warning", "error"
        }

    def test_semantic_colours_match_the_tokens(self):
        assert StatusDot.colour("recording") == Theme.RECORDING
        assert StatusDot.colour("ready") == Theme.SUCCESS
        assert StatusDot.colour("error") == Theme.ERROR
        assert StatusDot.colour("ai") == Theme.AI

    def test_renders_and_switches(self, root):
        dot = StatusDot(root, bg_under=Theme.WINDOW_BG)
        assert dot.status == "idle"
        dot.set_status("recording")
        assert dot.status == "recording"
        dot.destroy()

    def test_unknown_status_fails_loudly(self, root):
        dot = StatusDot(root, bg_under=Theme.WINDOW_BG)
        with pytest.raises(KeyError):
            dot.set_status("blinkend")
        dot.destroy()

    @pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
    def test_size_follows_dpi(self, root, monkeypatch, scale):
        monkeypatch.setattr(uk.Scale, "factor", scale)
        monkeypatch.setattr(uk.Scale, "dpi", int(96 * scale))
        dot = StatusDot(root, bg_under=Theme.WINDOW_BG)
        root.update_idletasks()
        assert int(dot.cget("width")) == uk.px(StatusDot.SIZE_MAIN)
        dot.destroy()

    def test_two_sizes_available(self, root):
        main = StatusDot(root, bg_under=Theme.WINDOW_BG, size=StatusDot.SIZE_MAIN)
        row = StatusDot(root, bg_under=Theme.WINDOW_BG, size=StatusDot.SIZE_ROW)
        assert int(main.cget("width")) >= int(row.cget("width"))
        main.destroy()
        row.destroy()
