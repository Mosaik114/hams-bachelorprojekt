"""Microphone choice with live level: the `LevelMonitor` shared by the settings and the setup assistant, and the
settings group "Recording" that holds it."""

from __future__ import annotations

import contextlib
import threading
import tkinter as tk
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from hams import ui_kit as uk
from hams.audio_devices import list_input_devices, resolve_mic_device
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import t
from hams.recording import CHANNELS, SAMPLE_RATE, reinitialise_portaudio
from hams.settings_sections import CONTROL_WIDTH, Section, group
from hams.ui_kit import Theme

#: Thread class for the device scan; the tests run it inline.
ScanThread = threading.Thread


class LevelMonitor:
    """Microphone select, status row and level meter, with the stream that feeds them.

    `host` is the window part it lives in: it has `app`, `win` and `alive()`. The monitor runs between
    `start()` and `stop()` (the settings window while shown, the setup assistant on its microphone page) and
    registers with the recorder as a microphone consumer meanwhile: a recording needs the device, so it suspends
    the monitor and resumes it afterwards. `on_level` sees every measured level (the assistant notes whether the
    microphone was heard).
    """

    #: How often the meter is redrawn, in ms: about 60 times a second, smooth to the eye. One tick costs about
    #: 0.4 ms on the Tk thread, and it only runs while the window is visible.
    TICK_MS = 16

    def __init__(
        self,
        host,
        body: tk.Misc,
        *,
        level_hairline: bool,
        on_level: Callable[[float], None] | None = None,
    ) -> None:
        self.host = host
        self.app = host.app
        self.on_level = on_level
        #: The open level stream, or None.
        self.stream: sd.InputStream | None = None
        self.rms = 0.0
        #: True between `start()` and `stop()`.
        self.active = False
        #: True while a recording holds the device.
        self.suspended = False
        #: True while the device list is being re-read in the background; the stream waits for it.
        self.scanning = False
        self.devices: list = []
        self.device_error = ""
        self._tick_job: str | None = None

        # Device names are long, so the select sits under its title at full width.
        self.row_mic = uk.SettingRow(body, t("settings.mic.title"), variant="status", stacked=True)
        self.row_mic.pack(fill="x")
        self.mic_select = uk.Select(
            body, bg_under=Theme.GROUP_SURFACE, on_change=self.select_device, placeholder=t("settings.mic.none")
        )
        self.row_mic.set_control(self.mic_select)
        self.row_level = uk.SettingRow(body, t("settings.level.title"), hairline=level_hairline)
        self.row_level.pack(fill="x")
        self.meter = uk.SignalMeter(body, bg_under=Theme.GROUP_SURFACE)
        uk.keep_logical_width(self.meter, CONTROL_WIDTH)
        self.row_level.set_control(self.meter)

    # ---------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Re-reads the devices, then opens the stream and starts the meter."""
        self.active = True
        self.suspended = self.app.is_recording
        self.app.recorder.register_consumer(self)
        self.refresh_devices()
        self.tick()

    def stop(self) -> None:
        self.active = False
        if self._tick_job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.host.win.after_cancel(self._tick_job)
            self._tick_job = None
        self.stop_stream()
        self.app.recorder.unregister_consumer(self)

    # ---------------------------------------------------------- devices

    def device_options(self) -> list:
        """(display name, value) per input device - logic without a widget."""
        options = [(t("settings.mic.system_default"), None)]
        options += [(pretty, pretty) for pretty, _full in self.devices]
        return options

    def refresh_device_state(self, devices: list | None = None) -> None:
        """Derives the state from the device list; without `devices` the list is queried again."""
        self.devices = list_input_devices() if devices is None else devices
        selected = CFG.mic_device_raw
        if not self.devices:
            self.device_error = t("settings.mic.none")
        elif isinstance(selected, str) and selected.strip().lower() not in ("", "default"):
            # The config stores a substring of the device name, not an exact name - exactly how
            # resolve_mic_device() searches as well.
            needle = selected.strip().lower()
            found = any(needle in pretty.lower() or needle in full.lower() for pretty, full in self.devices)
            self.device_error = "" if found else t("settings.mic.disconnected", device=selected)
        else:
            self.device_error = ""

    def refresh_devices(self) -> None:
        """Re-reads the input devices in the background (audit finding H18) and then fills the select.

        PortAudio is re-initialised first, so a microphone plugged in after the start is listed. That closes every
        open stream, so the level meter stops for the scan and starts again afterwards.
        """
        if self.scanning:
            return
        self.scanning = True
        self.stop_stream()
        ScanThread(target=self._scan_devices, daemon=True, name="mic-scan").start()

    def _audio_busy(self) -> bool:
        """Whether a stream is open that a re-initialisation of PortAudio would cut off."""
        recorder = self.app.recorder
        if recorder.busy():
            return True
        # Every consumer is a level monitor (settings, setup assistant).
        return any(getattr(consumer, "stream", None) is not None for consumer in recorder.consumers)

    def _scan_devices(self) -> None:
        """Thread entry: re-initialise, list, hand the result to the Tk thread."""
        try:
            reinitialise_portaudio(self._audio_busy)
            devices = list_input_devices()
        except Exception:  # noqa: BLE001 - boundary of the thread
            logger.exception("Microphone scan failed")
            devices = []
        self.app.ui_call(lambda: self.show_devices(devices))

    def show_devices(self, devices: list) -> None:
        """On the Tk thread: the scanned list into the select, then the level meter again."""
        self.scanning = False
        if not (self.active and self.host.alive()):
            return
        self.refresh_device_state(devices)
        options = [uk.Option(value, label) for label, value in self.device_options()]
        self.mic_select.set_options(options)
        self.mic_select.set_value(self.selected_device_value())
        self.mic_select.set_enabled(bool(self.devices))
        self.start_stream()
        if self.device_error:
            # At once, not with the next level tick: until then the row would still say "device unavailable",
            # and on a fast machine a test (or a glance) sees that instead of the real reason.
            self.row_mic.set_status("error", self.device_error)

    def selected_device_value(self):
        """The config value as option value - a substring mapped onto the display name."""
        raw = CFG.mic_device_raw
        if not (isinstance(raw, str) and raw.strip() and raw.strip().lower() != "default"):
            return None
        needle = raw.strip().lower()
        for pretty, full in self.devices:
            if needle in pretty.lower() or needle in full.lower():
                return pretty
        return raw

    def select_device(self, pretty: str | None) -> None:
        if pretty is None:
            CFG.mic_device_raw = "default"
            CFG.mic_device = None
            _persist_config_value("audio", "device", "default")
        else:
            CFG.mic_device_raw = pretty
            CFG.mic_device = resolve_mic_device(pretty)
            _persist_config_value("audio", "device", pretty)
        logger.info("Microphone chosen: %r -> %r", CFG.mic_device_raw, CFG.mic_device)
        self.refresh_device_state(self.devices)
        self.restart_stream()

    # ---------------------------------------------------------- stream

    def start_stream(self) -> None:
        if not self.active or self.suspended or self.scanning or self.app.is_recording or self.stream is not None:
            return
        if not self.devices and self.device_error:
            return  # no input device - do not even try
        try:
            device = resolve_mic_device(CFG.mic_device_raw)

            def callback(indata: np.ndarray, _frames: int, _time, _status) -> None:
                self.rms = float(np.mean(indata.astype(np.float32) ** 2))

            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, device=device, dtype="float32", callback=callback
            )
            self.stream.start()
        except Exception as exc:
            logger.warning("Level monitor stream failed: %s", exc)
            self.stream = None

    def stop_stream(self) -> None:
        if self.stream is not None:
            with contextlib.suppress(Exception):  # stream already closed or the device gone
                self.stream.stop()
                self.stream.close()
            self.stream = None
        self.rms = 0.0

    def restart_stream(self) -> None:
        self.stop_stream()
        self.start_stream()

    # ---------------------------------------------------------- sharing the microphone with the recorder

    def suspend_microphone(self) -> None:
        """The recording needs the device - pause the monitor."""
        self.suspended = True
        self.stop_stream()

    def resume_microphone(self) -> None:
        """Recording over - the monitor may come back if it still runs."""
        self.suspended = False
        if self.active and self.host.alive() and not self.app.is_recording:
            self.start_stream()

    # ---------------------------------------------------------- meter

    def tick(self) -> None:
        self._tick_job = None
        if not (self.active and self.host.alive()):
            return
        if self.app.is_recording:
            rms = self.app.recorder.level
        else:
            if self.stream is None and not self.suspended:
                self.start_stream()
            rms = self.rms
        level = min(1.0, (rms**0.5) * 1.5)
        self.meter.set_level(level)
        self.update_status(level)
        if self.on_level is not None:
            self.on_level(level)
        self._tick_job = self.host.win.after(self.TICK_MS, self.tick)

    def update_status(self, level: float) -> None:
        """Microphone state as a dot *and* text - never by colour alone."""
        if self.device_error:
            self.row_mic.set_status("error", self.device_error)
        elif self.stream is None and not self.app.is_recording:
            self.row_mic.set_status("warning", t("settings.mic.unavailable"))
        elif level < 0.015:
            self.row_mic.set_status("error", t("settings.mic.no_signal"))
        elif level < 0.08:
            self.row_mic.set_status("warning", t("settings.mic.low"))
        else:
            self.row_mic.set_status("ready", t("settings.mic.good"))


class RecordingSection(Section):
    """The group "Recording": microphone, its state and the live level."""

    def __init__(self, window, master: tk.Misc) -> None:
        super().__init__(window)
        self.group = group(master, t("settings.section.recording"), first=True)
        self.monitor = LevelMonitor(self, self.group.body, level_hairline=False)

    def on_show(self) -> None:
        self.monitor.start()

    def on_hide(self) -> None:
        super().on_hide()
        self.monitor.stop()
