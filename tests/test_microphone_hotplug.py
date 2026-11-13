"""A microphone plugged in after the start shows up in the settings (audit finding H18).

PortAudio reads the device list once, when it is initialised, and `query_devices` keeps returning that list. The
settings and the setup assistant now re-initialise PortAudio before they list the devices, in a background thread,
and never while a stream is open: re-initialising closes every stream.
"""

import threading

from hams import recording as recording_mod
from hams import settings_status as status_mod
from hams.config import CFG


def _open_settings(live_app):
    live_app._open_settings()
    live_app.root.update()
    return live_app.settings_win


def _labels(select):
    return [option.label for option in select._option_list]


class TestHotplug:
    def test_a_new_microphone_appears_after_reopening_the_settings(self, live_app, hams):
        CFG.mic_device_raw = "default"
        win = _open_settings(live_app)
        assert "Yeti" not in _labels(win.mic_select)
        win.close()

        hams.devices.append(("Yeti", "Yeti (USB)"))
        win = _open_settings(live_app)
        assert "Yeti" in _labels(win.mic_select)
        assert hams.sd.reinits == 2, "each scan re-initialises PortAudio"

    def test_the_level_meter_resumes_after_the_scan(self, live_app, hams):
        win = _open_settings(live_app)
        assert win._scanning is False
        assert win._mon_stream is not None

    def test_no_reinitialisation_while_recording(self, live_app, hams):
        win = _open_settings(live_app)
        before = hams.sd.reinits
        live_app.toggle_recording()
        assert live_app.is_recording
        win._refresh_devices()
        live_app.root.update()
        assert hams.sd.reinits == before, "re-initialising would cut the recording off"
        assert win._scanning is False

    def test_the_monitor_stops_during_the_scan(self, live_app, hams, monkeypatch):
        win = _open_settings(live_app)
        seen = []
        monkeypatch.setattr(status_mod, "reinitialise_portaudio", lambda busy: seen.append(busy()) or True)
        win._refresh_devices()
        live_app.root.update()
        assert seen == [False], "the level meter of this window was closed before re-initialising"

    def test_the_scan_runs_off_the_tk_thread(self, live_app, hams, monkeypatch):
        win = _open_settings(live_app)
        threads = []

        def scan():
            threads.append(threading.current_thread() is threading.main_thread())
            return []

        monkeypatch.setattr(status_mod, "ScanThread", threading.Thread)
        monkeypatch.setattr(status_mod, "_list_input_devices", scan)
        win._refresh_devices()
        for thread in threading.enumerate():
            if thread.name == "mic-scan":
                thread.join(2)
        assert threads == [False]


class TestReinitialise:
    def test_it_is_skipped_while_busy(self, monkeypatch):
        calls = []
        fake = type("SD", (), {"_terminate": lambda: calls.append("t"), "_initialize": lambda: calls.append("i")})
        monkeypatch.setattr(recording_mod, "sd", fake)
        assert recording_mod.reinitialise_portaudio(lambda: True) is False
        assert calls == []
        assert recording_mod.reinitialise_portaudio(lambda: False) is True
        assert calls == ["t", "i"]

    def test_a_failure_is_only_logged(self, monkeypatch):
        import sounddevice as sd

        def broken():
            raise sd.PortAudioError("kaputt")

        fake = type("SD", (), {"_terminate": broken, "_initialize": broken, "PortAudioError": sd.PortAudioError})
        monkeypatch.setattr(recording_mod, "sd", fake)
        assert recording_mod.reinitialise_portaudio(lambda: False) is False
