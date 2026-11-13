"""Einstellungen: Mikrofon, Live-Pegel und Ollama."""

import contextlib
import threading

import numpy as np
import sounddevice as sd

from hams import ollama as ollama_api
from hams import ui_kit as uk
from hams.config import CFG, _list_input_devices, _persist_config_value, _resolve_mic_device, logger
from hams.i18n import t
from hams.recording import CHANNELS, SAMPLE_RATE, reinitialise_portaudio
from hams.ui_kit import Theme

#: Thread class for the device scan; the tests run it inline.
ScanThread = threading.Thread


class StatusSectionMixin:
    """Einstellungen: Mikrofon, Live-Pegel und Ollama-Status."""

    #: True while the device list is being re-read in the background; the level meter waits for it.
    _scanning = False

    def device_options(self) -> list:
        """(Anzeigename, Wert) je Eingabegerät — Logik ohne Widget."""
        options = [(t("settings.mic.system_default"), None)]
        options += [(pretty, pretty) for pretty, _full in self._devices]
        return options

    def refresh_device_state(self, devices: list | None = None) -> None:
        """Den Zustand aus der Geräteliste ableiten; ohne `devices` wird sie neu abgefragt."""
        self._devices = _list_input_devices() if devices is None else devices
        selected = CFG.mic_device_raw
        if not self._devices:
            self._device_error = t("settings.mic.none")
        elif isinstance(selected, str) and selected.strip().lower() not in ("", "default"):
            # Die Config speichert einen Teilstring des Geraetenamens, keinen
            # exakten Namen - genau so sucht auch _resolve_mic_device().
            needle = selected.strip().lower()
            found = any(needle in pretty.lower() or needle in full.lower() for pretty, full in self._devices)
            self._device_error = "" if found else t("settings.mic.disconnected", device=selected)
        else:
            self._device_error = ""

    def _build_microphone_rows(self, body, *, level_hairline: bool) -> None:
        """Microphone select with status and the level meter; the settings and the setup assistant share them.

        Device names are long, so the select sits under its title at full width.
        """
        self.row_mic = uk.SettingRow(body, t("settings.mic.title"), variant="status", stacked=True)
        self.row_mic.pack(fill="x")
        self.mic_select = uk.Select(
            body, bg_under=Theme.GROUP_SURFACE, on_change=self._select_device, placeholder=t("settings.mic.none")
        )
        self.row_mic.set_control(self.mic_select)
        self.row_level = uk.SettingRow(body, t("settings.level.title"), hairline=level_hairline)
        self.row_level.pack(fill="x")
        self.meter = uk.SignalMeter(body, bg_under=Theme.GROUP_SURFACE)
        uk.keep_logical_width(self.meter, self.CONTROL_WIDTH)
        self.row_level.set_control(self.meter)
        self._refresh_devices()

    def _refresh_devices(self) -> None:
        """Re-reads the input devices in the background (audit finding H18) and then fills the select.

        PortAudio is re-initialised first, so a microphone plugged in after the start is listed. That closes every
        open stream, so the level meter stops for the scan and starts again afterwards.
        """
        if self._scanning:
            return
        self._scanning = True
        self._stop_monitor()
        ScanThread(target=self._scan_devices, daemon=True, name="mic-scan").start()

    def _audio_busy(self) -> bool:
        """Whether a stream is open that a re-initialisation of PortAudio would cut off."""
        app = self.app
        if app.is_recording or getattr(app, "stream", None) is not None:
            return True
        return any(getattr(consumer, "_mon_stream", None) is not None for consumer in app._mic_consumers)

    def _scan_devices(self) -> None:
        """Thread entry: re-initialise, list, hand the result to the Tk thread."""
        try:
            reinitialise_portaudio(self._audio_busy)
            devices = _list_input_devices()
        except Exception:  # noqa: BLE001 - boundary of the thread
            logger.exception("Microphone scan failed")
            devices = []
        self.app._ui_call(lambda: self._show_devices(devices))

    def _show_devices(self, devices: list) -> None:
        """On the Tk thread: the scanned list into the select, then the level meter again."""
        self._scanning = False
        if not self.alive():
            return
        self.refresh_device_state(devices)
        options = [uk.Option(value, label) for label, value in self.device_options()]
        self.mic_select.set_options(options)
        self.mic_select.set_value(self._selected_device_value())
        self.mic_select.set_enabled(bool(self._devices))
        self._start_monitor()
        if self._device_error:
            # At once, not with the next level tick: until then the row would still say "device unavailable",
            # and on a fast machine a test (or a glance) sees that instead of the real reason.
            self.row_mic.set_status("error", self._device_error)

    def _selected_device_value(self):
        """Der Config-Wert als Optionswert — Teilstring auf Anzeigename gemappt."""
        raw = CFG.mic_device_raw
        if not (isinstance(raw, str) and raw.strip() and raw.strip().lower() != "default"):
            return None
        needle = raw.strip().lower()
        for pretty, full in self._devices:
            if needle in pretty.lower() or needle in full.lower():
                return pretty
        return raw

    def _select_device(self, pretty: str | None) -> None:
        if pretty is None:
            CFG.mic_device_raw = "default"
            CFG.mic_device = None
            _persist_config_value("audio", "device", "default")
        else:
            CFG.mic_device_raw = pretty
            CFG.mic_device = _resolve_mic_device(pretty)
            _persist_config_value("audio", "device", pretty)
        logger.info("Mikrofon gewählt: %r -> %r", CFG.mic_device_raw, CFG.mic_device)
        self.refresh_device_state(self._devices)
        self._restart_monitor()

    # ---------------------------------------------------------- monitor stream

    def _start_monitor(self) -> None:
        if self._suspended or self._scanning or self.app.is_recording or self._mon_stream is not None:
            return
        if not self._devices and self._device_error:
            return  # kein Eingabegerät — gar nicht erst versuchen
        try:
            dev = _resolve_mic_device(CFG.mic_device_raw)

            def cb(indata: np.ndarray, _frames: int, _time, _status) -> None:
                self._mon_rms = float(np.mean(indata.astype(np.float32) ** 2))

            self._mon_stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                device=dev,
                dtype="float32",
                callback=cb,
            )
            self._mon_stream.start()
        except Exception as exc:
            logger.warning("Monitor-Stream fehlgeschlagen: %s", exc)
            self._mon_stream = None

    def _stop_monitor(self) -> None:
        if self._mon_stream is not None:
            with contextlib.suppress(Exception):  # Strom ist schon zu oder das Gerät weg
                self._mon_stream.stop()
                self._mon_stream.close()
            self._mon_stream = None
        self._mon_rms = 0.0

    def _restart_monitor(self) -> None:
        self._stop_monitor()
        self._start_monitor()

    # ---------------------------------------------------------- Mikrofon-Teilhabe

    def suspend_microphone(self) -> None:
        """Die Aufnahme braucht das Gerät — Monitor anhalten."""
        self._suspended = True
        self._stop_monitor()

    def resume_microphone(self) -> None:
        """Aufnahme vorbei — Monitor darf zurückkehren, sofern noch offen."""
        self._suspended = False
        if self.alive() and not self.app.is_recording:
            self._start_monitor()

    # ---------------------------------------------------------- ticks / status

    def _tick(self) -> None:
        if not self.alive():
            return
        if self.app.is_recording:
            rms = self.app._rms_level
        else:
            if self._mon_stream is None and not self._suspended:
                self._start_monitor()
            rms = self._mon_rms
        level = min(1.0, (rms**0.5) * 1.5)
        self.meter.set_level(level)
        self._update_mic_status(level)
        self._tick_job = self.win.after(60, self._tick)

    def _update_mic_status(self, level: float) -> None:
        """Mikrofonstatus als Punkt *und* Text — nie allein über die Farbe."""
        if self._device_error:
            self.row_mic.set_status("error", self._device_error)
        elif self._mon_stream is None and not self.app.is_recording:
            self.row_mic.set_status("warning", t("settings.mic.unavailable"))
        elif level < 0.015:
            self.row_mic.set_status("error", t("settings.mic.no_signal"))
        elif level < 0.08:
            self.row_mic.set_status("warning", t("settings.mic.low"))
        else:
            self.row_mic.set_status("ready", t("settings.mic.good"))

    def _refresh_status(self) -> None:
        if not self.alive():
            return
        app = self.app
        if app.model_error:
            self.row_hardware.set_value(t("settings.hardware.error"))
        elif app.model_ready.is_set():
            device = app._active_device or "?"
            label = "GPU" if device == "cuda" else device.upper()
            self.row_hardware.set_value(t("settings.hardware.auto", device=label))
        else:
            self.row_hardware.set_value(t("status.model_loading"))
            self.win.after(800, self._refresh_status)

    def _schedule_ollama_check(self) -> None:
        """Alle paar Sekunden nachsehen — sparsam, aber nicht nur einmal.

        Nur beim Öffnen zu prüfen hiesse: startet Ollama danach, bleibt die
        Anzeige dauerhaft falsch.
        """
        if not self.alive():
            return
        threading.Thread(target=self._check_ollama, daemon=True, name="ollama-check").start()
        self._ollama_job = self.win.after(self.OLLAMA_INTERVAL_MS, self._schedule_ollama_check)

    def _check_ollama(self) -> None:
        """Laeuft im Hintergrundthread — hier nur fragen, nichts zeichnen."""
        state = ollama_api.probe_or_offline(lambda: ollama_api.probe(ollama_api.base_url(CFG.ollama_url)))
        self.app._ui_call(lambda: self._apply_ollama_state(state))

    def _show_ollama_status(self) -> None:
        """Statuszeile aus dem bekannten Stand — ohne selbst zu fragen.

        Solange in dieser Sitzung noch nie geantwortet wurde, steht dort
        "wird geprüft". Einen Ausfall zu behaupten, den niemand gemessen hat,
        waere schlicht falsch.
        """
        if not self.app.ollama_checked:
            self.row_cleanup_model.set_status("idle", t("settings.ollama.checking"))
            return
        state = self._ollama_state
        if not state.online and not CFG.ollama_cleanup_enabled:
            # Ohne Bereinigung ist ein fehlendes Ollama kein Fehler, nur eine Auskunft.
            self.row_cleanup_model.set_status("idle", t("settings.ollama.optional"))
            return
        self.row_cleanup_model.set_status(
            "ready" if state.online else "error",
            t("settings.ollama.online") if state.online else state.error or t("ollama.unreachable"),
        )

    def _apply_ollama_state(self, state) -> None:
        """Uebernimmt eine Antwort — auf dem Tk-Thread."""
        if not self.alive():
            return
        self.app.ollama_state = state
        self.app.ollama_checked = True
        self._ollama_online = state.online
        if state.signature() != self._ollama_signature:
            popover = uk.Popover.current
            if popover is not None and popover.trigger in (self.select_cleanup_model, self.select_prompt_model):
                self._show_ollama_status()
                return  # geoeffnete Auswahl nicht unter den Fingern austauschen
            self._ollama_state = state
            self._ollama_signature = state.signature()
            self._refresh_ollama_models()
        self._show_ollama_status()
        self._sync_cleanup_dependency()

    # ---------------------------------------------------------- Ollama-Modelle

    @staticmethod
    def ollama_options(state, current: str) -> list:
        """Auswahlliste aus einer Dienstantwort — Logik ohne Widget.

        Nur installierte Modelle. Ollama kennt ueber diese API keinen Katalog
        entfernter Modelle, also wird auch keiner erfunden.
        """
        options = [
            uk.Option(
                model.name,
                ollama_api.display_name(model.name),
                meta=model.meta,
                group=t("settings.ollama.group_installed"),
                trailing=t("settings.ollama.in_memory") if model.name in state.warm else "",
            )
            for model in state.models
        ]
        if current and current not in state.names:
            # Das konfigurierte Modell fehlt. Es bleibt sichtbar und bleibt
            # gespeichert: stillschweigend ein anderes zu waehlen waere eine
            # Entscheidung, die dem Nutzer gehoert.
            if state.online:
                meta = t("settings.ollama.not_installed")
            elif state.error:
                meta = state.error
            else:
                meta = ""  # noch nie gefragt — nichts behaupten
            options.insert(
                0,
                uk.Option(
                    current,
                    ollama_api.display_name(current),
                    meta=meta,
                    group=t("settings.ollama.group_installed"),
                    enabled=False,
                ),
            )
        if state.online and options:
            options.append(uk.Option("", ollama_api.pull_hint(), enabled=False))
        return options

    def _refresh_ollama_models(self) -> None:
        if not self.alive():
            return
        state = self._ollama_state
        self.select_cleanup_model.set_options(self.ollama_options(state, CFG.ollama_model_cleanup))
        self.select_cleanup_model.set_value(CFG.ollama_model_cleanup)
        self.select_prompt_model.set_options(self.ollama_options(state, CFG.ollama_model_prompt))
        self.select_prompt_model.set_value(CFG.ollama_model_prompt)

    def _select_cleanup_model(self, name: str) -> None:
        """Schreibt ausschliesslich den Cleanup-Schluessel."""
        if not name or name == CFG.ollama_model_cleanup:
            return
        CFG.ollama_model_cleanup = name
        _persist_config_value("ollama", "model_transcript_cleanup", name)
        logger.info("Cleanup-Modell: %s", name)
        self.select_cleanup_model.set_value(name)

    def _select_prompt_model(self, name: str) -> None:
        """Schreibt ausschliesslich den Prompt-Schluessel."""
        if not name or name == CFG.ollama_model_prompt:
            return
        CFG.ollama_model_prompt = name
        _persist_config_value("ollama", "model_prompt_generate", name)
        logger.info("Prompt-Modell: %s", name)
        self.select_prompt_model.set_value(name)
