"""Tests der Einstellungslogik ohne Fenster.

Geräteauswahl, Mikrofonstatus und die Cleanup-Abhängigkeit sind reine Logik und
lassen sich ohne die vollständige Anwendung prüfen. Der Lebenszyklus zwischen
Aufnahme und Pegelmonitor braucht dagegen echte Audio-Streams und wurde als
Integrationslauf geprüft.
"""

import pytest

from wisper.main import FloatingTranscriberApp as App
from wisper.main import SettingsWindow as SW


class _Stub:
    """Minimalobjekt mit den Feldern, die die geprüften Methoden anfassen."""

    def __init__(self, devices, selected, suspended=False, recording=False):
        self._devices = devices
        self._device_error = ""
        self._suspended = suspended
        self._selected = selected

    # von refresh_device_state benutzt
    refresh_device_state = SW.refresh_device_state
    device_options = SW.device_options


@pytest.fixture
def config(monkeypatch):
    def _set(device):
        monkeypatch.setattr("wisper.main.CFG.mic_device_raw", device)

    return _set


class TestMicConsumerApi:
    def test_registration_is_idempotent(self):
        app = App.__new__(App)
        app._mic_consumers = []
        consumer = object()
        App.register_mic_consumer(app, consumer)
        App.register_mic_consumer(app, consumer)
        assert app._mic_consumers == [consumer]

    def test_unregister_is_safe_when_absent(self):
        app = App.__new__(App)
        app._mic_consumers = []
        App.unregister_mic_consumer(app, object())      # darf nicht werfen
        assert app._mic_consumers == []

    def test_suspend_reaches_every_consumer(self):
        calls = []

        class _Consumer:
            def suspend_microphone(self):
                calls.append("suspend")

            def resume_microphone(self):
                calls.append("resume")

        app = App.__new__(App)
        app._mic_consumers = [_Consumer(), _Consumer()]
        App._suspend_mic_consumers(app)
        App._resume_mic_consumers(app)
        assert calls == ["suspend", "suspend", "resume", "resume"]

    def test_a_broken_consumer_does_not_stop_the_others(self):
        calls = []

        class _Broken:
            def suspend_microphone(self):
                raise RuntimeError("kaputt")

        class _Good:
            def suspend_microphone(self):
                calls.append("ok")

        app = App.__new__(App)
        app._mic_consumers = [_Broken(), _Good()]
        App._suspend_mic_consumers(app)      # darf nicht durchschlagen
        assert calls == ["ok"]


class TestDeviceState:
    def test_no_input_device_is_reported(self, config, monkeypatch):
        monkeypatch.setattr("wisper.main._list_input_devices", lambda: [])
        config("Shure MV7+")
        stub = _Stub([], "Shure MV7+")
        stub.refresh_device_state()
        assert stub._device_error == "Kein Mikrofon gefunden"

    def test_missing_selected_device_is_reported(self, config, monkeypatch):
        monkeypatch.setattr("wisper.main._list_input_devices",
                            lambda: [("Webcam", "Webcam (USB)")])
        config("Shure MV7+")
        stub = _Stub([], "Shure MV7+")
        stub.refresh_device_state()
        assert "nicht verbunden" in stub._device_error

    def test_substring_match_counts_as_present(self, config, monkeypatch):
        """Die Config speichert ein Namensfragment, keinen exakten Gerätenamen."""
        monkeypatch.setattr("wisper.main._list_input_devices",
                            lambda: [("Mikrofon (Shure MV7+)", "3- Mikrofon (Shure MV7+)")])
        config("Shure MV7+")
        stub = _Stub([], "Shure MV7+")
        stub.refresh_device_state()
        assert stub._device_error == ""

    def test_default_device_never_errors(self, config, monkeypatch):
        monkeypatch.setattr("wisper.main._list_input_devices",
                            lambda: [("Webcam", "Webcam (USB)")])
        config("default")
        stub = _Stub([], "default")
        stub.refresh_device_state()
        assert stub._device_error == ""

    def test_options_always_offer_the_system_default(self, monkeypatch):
        monkeypatch.setattr("wisper.main._list_input_devices",
                            lambda: [("Webcam", "Webcam (USB)")])
        options = _Stub([], None).device_options()
        assert options[0] == ("System-Standard", None)
        assert ("Webcam", "Webcam") in options

    def test_options_are_pairs_of_label_and_value(self, monkeypatch):
        monkeypatch.setattr("wisper.main._list_input_devices",
                            lambda: [("A", "A full"), ("B", "B full")])
        for label, value in _Stub([], None).device_options():
            assert isinstance(label, str)
            assert value is None or isinstance(value, str)


class TestMicStatus:
    """Status als Punkt *und* Text — nie allein über die Farbe."""

    class _Row:
        def __init__(self):
            self.status = None
            self.text = None

        def set_status(self, status, text):
            self.status, self.text = status, text

    def _run(self, level, *, error="", stream=object(), recording=False):
        window = SW.__new__(SW)
        window.row_mic = self._Row()
        window._device_error = error
        window._mon_stream = stream
        window.app = type("A", (), {"is_recording": recording})()
        SW._update_mic_status(window, level)
        return window.row_mic

    def test_good_signal(self):
        row = self._run(0.5)
        assert row.status == "ready" and row.text

    def test_quiet_signal(self):
        row = self._run(0.05)
        assert row.status == "warning" and "Leise" in row.text

    def test_no_signal(self):
        row = self._run(0.001)
        assert row.status == "error" and "Kein Signal" in row.text

    def test_device_error_wins(self):
        row = self._run(0.5, error="„Yeti“ ist nicht verbunden")
        assert row.status == "error" and "Yeti" in row.text

    def test_unavailable_device(self):
        row = self._run(0.5, stream=None)
        assert row.status == "warning" and "nicht verfügbar" in row.text

    def test_every_state_has_text(self):
        for level in (0.0, 0.02, 0.5):
            assert self._run(level).text


class TestCleanupDependency:
    class _Row:
        def __init__(self):
            self.enabled = True
            self.description = ""

        def set_enabled(self, value):
            self.enabled = value

        def set_description(self, text):
            self.description = text

    def _run(self, cleanup_on, ollama_online, monkeypatch):
        monkeypatch.setattr("wisper.main.CFG.ollama_cleanup_enabled", cleanup_on)
        window = SW.__new__(SW)
        window._alive = True
        window.win = type("W", (), {"winfo_exists": lambda self: True})()
        window._ollama_online = ollama_online
        window.row_cleanup = self._Row()
        window.row_cleanup_model = self._Row()
        window.row_prompt_model = self._Row()
        SW._sync_cleanup_dependency(window)
        return window

    def test_cleanup_on_and_ollama_up(self, monkeypatch):
        window = self._run(True, True, monkeypatch)
        assert window.row_cleanup_model.enabled
        assert "nicht erreichbar" not in window.row_cleanup.description

    def test_cleanup_on_but_ollama_down_is_visible(self, monkeypatch):
        """Der Widerspruch darf nicht erst nach der nächsten Aufnahme auffallen."""
        window = self._run(True, False, monkeypatch)
        assert not window.row_cleanup_model.enabled
        assert "nicht erreichbar" in window.row_cleanup.description

    def test_cleanup_off_disables_the_model_row(self, monkeypatch):
        window = self._run(False, True, monkeypatch)
        assert not window.row_cleanup_model.enabled

    def test_description_returns_to_normal(self, monkeypatch):
        window = self._run(True, False, monkeypatch)
        assert "nicht erreichbar" in window.row_cleanup.description
        window = self._run(True, True, monkeypatch)
        assert "Bereinigt" in window.row_cleanup.description

    def test_cleanup_off_leaves_the_prompt_model_alone(self, monkeypatch):
        """Prompt-Modus und Cleanup sind unabhängig voneinander."""
        window = self._run(False, True, monkeypatch)
        assert window.row_prompt_model.enabled
        assert "Prompt" in window.row_prompt_model.description

    def test_offline_disables_the_prompt_model(self, monkeypatch):
        window = self._run(True, False, monkeypatch)
        assert not window.row_prompt_model.enabled
        assert "nicht erreichbar" in window.row_prompt_model.description


class TestIntervals:
    def test_ollama_is_polled_sparingly(self):
        assert 2000 <= SW.OLLAMA_INTERVAL_MS <= 30000

    def test_window_is_larger_than_before(self):
        assert (SW.WIDTH, SW.HEIGHT) == (400, 620)
