"""Tests des Hauptfenster-Zustandsmodells.

Geprüft wird die Zustandstabelle als Datenstruktur — sie entscheidet, was der
Nutzer sieht. Der visuelle Durchlauf aller Zustände samt Höhenmessung liegt im
Skript `shot_states8.py`; er braucht die vollständige Anwendung mit Tray und
Modell-Ladethread und gehört deshalb nicht in die Testsuite.
"""

import pytest

from wisper.main import FloatingTranscriberApp as App
from wisper.ui_kit import ControlStyle, StatusDot

TABLE = App.STATE_TABLE


class TestStateTable:
    def test_covers_every_documented_state(self):
        assert set(TABLE) == {
            "model_loading", "ready", "recording", "recording_prompt",
            "transcribing", "cleaning", "generating", "done", "error", "no_signal",
        }

    @pytest.mark.parametrize("state", list(TABLE))
    def test_dot_is_a_known_status(self, state):
        dot = TABLE[state][0]
        assert dot in StatusDot.COLOURS

    @pytest.mark.parametrize("state", list(TABLE))
    def test_role_is_a_known_control_role(self, state):
        role = TABLE[state][2]
        assert role in ControlStyle.roles()

    @pytest.mark.parametrize("state", list(TABLE))
    def test_button_text_is_german_and_short(self, state):
        text = TABLE[state][3]
        assert text and len(text) <= 24
        for english in ("Start", "Stop recording", "Retry", "Loading"):
            assert english not in text


class TestSemantics:
    def test_recording_uses_no_filled_signal_surface(self):
        """Kein roter Vollflächenbutton — die Semantik trägt Punkt und Symbol."""
        for state in App.RECORDING_STATES:
            assert TABLE[state][2] == "secondary", f"{state} füllt die Fläche"

    def test_recording_states_show_the_right_dot(self):
        assert TABLE["recording"][0] == "recording"
        assert TABLE["recording_prompt"][0] == "ai"

    def test_only_ready_and_done_offer_the_accent_action(self):
        accent = {s for s, row in TABLE.items() if row[2] == "primary"}
        assert accent == {"model_loading", "ready", "done", "no_signal"}

    def test_processing_states_disable_the_button(self):
        for state in ("transcribing", "cleaning", "generating"):
            assert TABLE[state][4] is False, f"{state} bleibt bedienbar"

    def test_loading_the_model_does_not_block_recording(self):
        """Aufnehmen braucht kein Modell, nur Auswerten.

        `model_loading` galt frueher als Verarbeitungszustand und sperrte den
        Knopf. Das war eine Sperre zu viel: bis das Modell steht, vergehen
        warm 2,2 s, und wer gleich nach dem Anmelden diktiert, hat dann noch
        gar nicht angefangen zu sprechen. Die Aufnahme laeuft jetzt sofort,
        die Auswertung wartet.
        """
        assert TABLE["model_loading"][4] is True
        assert TABLE["model_loading"][3] == "Aufnahme starten"

    def test_actionable_states_enable_the_button(self):
        for state in ("model_loading", "ready", "recording", "recording_prompt",
                      "done", "error", "no_signal"):
            assert TABLE[state][4] is True

    def test_error_offers_a_retry(self):
        assert TABLE["error"][3] == "Erneut versuchen"

    def test_button_never_repeats_the_status_text(self):
        """Der Button zeigt die Aktion, die Statuszeile den Zustand."""
        for state, (_dot, status, _role, button, _enabled) in TABLE.items():
            if status is not None:
                assert status != button, f"{state} sagt zweimal dasselbe"

    def test_generating_uses_ai_semantics(self):
        assert TABLE["generating"][0] == "ai"


class TestGeometry:
    def test_window_width_is_the_planned_size(self):
        assert App.WINDOW_WIDTH == 340

    def test_minimum_height_is_compact(self):
        assert App.WINDOW_MIN_HEIGHT <= 220


class TestShortcutHint:
    def test_is_a_single_line(self):
        hint = App._shortcut_hint(None)
        assert "\n" not in hint

    def test_names_both_shortcuts(self):
        hint = App._shortcut_hint(None)
        assert "Transkript" in hint and "KI" in hint

    def test_uses_no_macos_symbols(self):
        """Wisper bleibt Windows-Software — keine ⌘/⌥/⇧-Zeichen."""
        hint = App._shortcut_hint(None)
        for symbol in ("⌘", "⌥", "⇧", "⌃"):
            assert symbol not in hint
