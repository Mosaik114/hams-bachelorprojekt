"""Tests der Einstellungslogik.

Geräteauswahl, Mikrofonstatus und die Cleanup-Abhängigkeit werden an den echten
Abschnitten des Einstellungsfensters geprüft (`LevelMonitor`, `AiSection`), mit
den Attrappen aus tests/live_app.py statt echter Geräte und ohne Ollama.
"""

import types

import pytest

from hams import audio_devices
from hams import settings_extras as extras
from hams.recording import Recorder
from hams.settings_extras import AiSection
from hams.settings_window import SettingsWindow as SW

#: Platzhalter für „ein Mikrofon-Strom ist offen“
_EIN_STROM = object()


@pytest.fixture
def monitor(live_app):
    """Der Pegelmonitor des echten, vorab versteckt gebauten Einstellungsfensters (kein Strom offen)."""
    live_app.prebuild_settings()
    return live_app.settings_win.recording.monitor


@pytest.fixture
def config(monkeypatch):
    def _set(device):
        monkeypatch.setattr("hams.config.CFG.mic_device_raw", device)

    return _set


class TestMicConsumerApi:
    def test_registration_is_idempotent(self):
        recorder = Recorder()
        consumer = object()
        recorder.register_consumer(consumer)
        recorder.register_consumer(consumer)
        assert recorder.consumers == [consumer]

    def test_unregister_is_safe_when_absent(self):
        recorder = Recorder()
        recorder.unregister_consumer(object())  # must not raise
        assert recorder.consumers == []

    def test_suspend_reaches_every_consumer(self):
        calls = []

        class _Consumer:
            def suspend_microphone(self):
                calls.append("suspend")

            def resume_microphone(self):
                calls.append("resume")

        recorder = Recorder()
        recorder.consumers = [_Consumer(), _Consumer()]
        recorder.suspend_consumers()
        recorder.resume_consumers()
        assert calls == ["suspend", "suspend", "resume", "resume"]

    def test_a_broken_consumer_does_not_stop_the_others(self):
        calls = []

        class _Broken:
            def suspend_microphone(self):
                raise RuntimeError("kaputt")

        class _Good:
            def suspend_microphone(self):
                calls.append("ok")

        recorder = Recorder()
        recorder.consumers = [_Broken(), _Good()]
        recorder.suspend_consumers()  # darf nicht durchschlagen
        assert calls == ["ok"]


class TestDeviceState:
    def test_no_input_device_is_reported(self, monitor, config, monkeypatch):
        monkeypatch.setattr("hams.settings_status.list_input_devices", lambda: [])
        config("Shure MV7+")
        monitor.refresh_device_state()
        assert monitor.device_error == "Kein Mikrofon gefunden"

    def test_missing_selected_device_is_reported(self, monitor, config, monkeypatch):
        monkeypatch.setattr("hams.settings_status.list_input_devices", lambda: [("Webcam", "Webcam (USB)")])
        config("Shure MV7+")
        monitor.refresh_device_state()
        assert "nicht verbunden" in monitor.device_error

    def test_substring_match_counts_as_present(self, monitor, config, monkeypatch):
        """Die Config speichert ein Namensfragment, keinen exakten Gerätenamen."""
        monkeypatch.setattr(
            "hams.settings_status.list_input_devices",
            lambda: [("Mikrofon (Shure MV7+)", "3- Mikrofon (Shure MV7+)")],
        )
        config("Shure MV7+")
        monitor.refresh_device_state()
        assert monitor.device_error == ""

    def test_default_device_never_errors(self, monitor, config, monkeypatch):
        monkeypatch.setattr("hams.settings_status.list_input_devices", lambda: [("Webcam", "Webcam (USB)")])
        config("default")
        monitor.refresh_device_state()
        assert monitor.device_error == ""

    def test_options_always_offer_the_system_default(self, monitor):
        monitor.refresh_device_state([("Webcam", "Webcam (USB)")])
        options = monitor.device_options()
        assert options[0] == ("System-Standard", None)
        assert ("Webcam", "Webcam") in options

    def test_options_are_pairs_of_label_and_value(self, monitor):
        monitor.refresh_device_state([("A", "A full"), ("B", "B full")])
        for label, value in monitor.device_options():
            assert isinstance(label, str)
            assert value is None or isinstance(value, str)


class _FakeSd:
    """sounddevice-Ersatz: dasselbe Mikro über mehrere Host-APIs."""

    def __init__(self, apis, devices):
        self._apis = [{"name": name} for name in apis]
        self._devices = devices
        self.default = types.SimpleNamespace(hostapi=0)

    def query_hostapis(self):
        return self._apis

    def query_devices(self):
        return self._devices


class TestInputDeviceList:
    def _dev(self, name, hostapi, inputs=1):
        return {"name": name, "hostapi": hostapi, "max_input_channels": inputs}

    def test_only_wasapi_devices_are_listed(self, monkeypatch):
        """Nur was Windows unter Eingabe zeigt — kein gekürzter MME-Name,
        kein in Windows deaktiviertes WDM-KS-Gerät."""
        fake = _FakeSd(
            ["MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS"],
            [
                self._dev("Microsoft Soundmapper - Input", 0),
                self._dev("Personal Mix (Elgato Virtual Au", 0),
                self._dev("Mikrofon (3- Shure MV7+)", 1),
                self._dev("Mikrofon (3- Shure MV7+)", 2),
                self._dev("Lautsprecher (Realtek(R) Audio)", 2, inputs=0),
                self._dev("Mikrofon (UGREEN Camera)", 3),
            ],
        )
        monkeypatch.setattr("hams.audio_devices.sd", fake)
        assert audio_devices.list_input_devices() == [("Mikrofon (3- Shure MV7+)", "Mikrofon (3- Shure MV7+)")]

    def test_without_wasapi_every_host_api_counts(self, monkeypatch):
        fake = _FakeSd(["MME"], [self._dev("3- Shure MV7+", 0)])
        monkeypatch.setattr("hams.audio_devices.sd", fake)
        assert audio_devices.list_input_devices() == [("Shure MV7+", "3- Shure MV7+")]


class TestMicStatus:
    """Status als Punkt *und* Text — nie allein über die Farbe."""

    @pytest.fixture
    def run(self, monitor):
        def _run(level, *, error="", stream=_EIN_STROM):
            monitor.device_error = error
            monitor.stream = stream
            monitor.update_status(level)
            row = monitor.row_mic
            return types.SimpleNamespace(status=row.status_dot._status, text=row.sub_text)

        yield _run
        monitor.stream = None  # der Platzhalter ist kein Strom, den jemand schließen könnte

    def test_good_signal(self, run):
        row = run(0.5)
        assert row.status == "ready" and row.text

    def test_quiet_signal(self, run):
        row = run(0.05)
        assert row.status == "warning" and "Leise" in row.text

    def test_no_signal(self, run):
        row = run(0.001)
        assert row.status == "error" and "Kein Signal" in row.text

    def test_device_error_wins(self, run):
        row = run(0.5, error="„Yeti“ ist nicht verbunden")
        assert row.status == "error" and "Yeti" in row.text

    def test_unavailable_device(self, run):
        row = run(0.5, stream=None)
        assert row.status == "warning" and "nicht verfügbar" in row.text

    def test_every_state_has_text(self, run):
        for level in (0.0, 0.02, 0.5):
            assert run(level).text


class _NoThread:
    """Ein Hintergrundfaden, der nie läuft: hier soll niemand Ollama fragen."""

    def __init__(self, **_options):
        pass

    def start(self) -> None:
        pass


class TestCleanupDependency:
    @pytest.fixture
    def ai(self, live_app, monkeypatch):
        """Der KI-Abschnitt des echten, gezeigten Fensters; Ollama wird dabei nicht gefragt."""
        monkeypatch.setattr(extras, "threading", types.SimpleNamespace(Thread=_NoThread))
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win.ai

    @pytest.fixture
    def run(self, ai, monkeypatch):
        def _run(cleanup_on, ollama_online):
            monkeypatch.setattr("hams.config.CFG.ollama_cleanup_enabled", cleanup_on)
            ai.ollama_online = ollama_online
            ai.sync_cleanup_dependency()
            return ai

        return _run

    def test_cleanup_on_and_ollama_up(self, run):
        ai = run(True, True)
        assert ai.row_cleanup_model._enabled
        assert "nicht erreichbar" not in ai.row_cleanup.sub_text

    def test_cleanup_on_but_ollama_down_is_visible(self, run):
        """Der Widerspruch darf nicht erst nach der nächsten Aufnahme auffallen."""
        ai = run(True, False)
        assert not ai.row_cleanup_model._enabled
        assert "nicht erreichbar" in ai.row_cleanup.sub_text

    def test_cleanup_off_disables_the_model_row(self, run):
        ai = run(False, True)
        assert not ai.row_cleanup_model._enabled

    def test_description_returns_to_normal(self, run):
        ai = run(True, False)
        assert "nicht erreichbar" in ai.row_cleanup.sub_text
        ai = run(True, True)
        assert "Bereinigt" in ai.row_cleanup.sub_text

    def test_cleanup_off_leaves_the_prompt_model_alone(self, run):
        """Prompt-Modus und Cleanup sind unabhängig voneinander."""
        ai = run(False, True)
        assert ai.row_prompt_model._enabled
        assert "KI fragen" in ai.row_prompt_model.sub_text

    def test_offline_disables_the_prompt_model(self, run):
        ai = run(True, False)
        assert not ai.row_prompt_model._enabled
        assert "nicht erreichbar" in ai.row_prompt_model.sub_text


class TestIntervals:
    def test_ollama_is_polled_sparingly(self):
        assert 2000 <= AiSection.OLLAMA_INTERVAL_MS <= 30000

    def test_window_is_larger_than_before(self):
        assert (SW.WIDTH, SW.HEIGHT) == (440, 680)
