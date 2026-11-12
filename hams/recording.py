"""Aufnahme vom Mikrofon bis zur fertigen Tonspur."""

import contextlib
import threading
import time
import tkinter as tk

import numpy as np
import sounddevice as sd

from hams.config import CFG, _resolve_mic_device, logger
from hams.i18n import t
from hams.tray import _notify_cursor_dot

# Peak unterhalb dieser Schwelle gilt als "kein Signal" (Mikro stumm/falsches
# Gerät). Sprache liegt typ. bei 0.05+, Grundrauschen bei ~0.0002 — 0.005 trennt
# beides sauber. Wird sowohl für die Normalisierung als auch die Kein-Signal-
# Erkennung genutzt, damit beide dieselbe Grenze verwenden.
SILENCE_PEAK_THRESHOLD = 0.005

#: Wie lange eine fertige Aufnahme auf das Modell wartet, bevor sie aufgibt.
#: Warm ist es nach ~2 s da, kalt nach ~20 s; die Schranke ist grosszuegig
#: bemessen, aber vorhanden, damit kein Thread unbegrenzt haengt.
MODEL_WAIT_SECONDS = 120.0

#: Pegelanzeige in Dezibel statt linear: Sprache aus einem halben Meter Abstand liegt
#: bei etwa -50 dBFS (RMS 0,003) und war linear kaum sichtbar. Gemessen am MV7+
#: (29.09.2026): Grundrauschen bis -64 dBFS, Sprache nah am Mikro bis etwa -30 dBFS.
LEVEL_FLOOR_DB = -62.0
LEVEL_FULL_DB = -28.0


def _display_level(mean_square: float) -> float:
    """Anzeigepegel 0..1 aus dem mittleren Quadrat eines Blocks (logarithmisch wie das Gehör)."""
    if mean_square <= 0.0:
        return 0.0
    db = 10.0 * float(np.log10(mean_square))
    return max(0.0, min(1.0, (db - LEVEL_FLOOR_DB) / (LEVEL_FULL_DB - LEVEL_FLOOR_DB)))


def _normalize_audio(audio: np.ndarray, target_peak: float = 0.35) -> np.ndarray:
    """Hebt leise Aufnahmen auf einen Zielpegel an, damit Whisper/VAD nicht an
    zu niedrigem Eingangspegel scheitern.

    Reine Stille (Peak unter der Rauschgrenze) wird NICHT verstärkt — sonst
    würde nur Grundrauschen hochgezogen. Bereits laute Aufnahmen bleiben
    unverändert; es wird nur angehoben, nie abgesenkt.
    """
    if audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak < SILENCE_PEAK_THRESHOLD or peak >= target_peak:
        return audio
    return (audio * (target_peak / peak)).astype(np.float32)


class RecordingMixin:
    """Aufnahme: Start, Stopp, Pegel, Laufzeit und Hoechstdauer."""

    # ------------------------------------------------------------------ Aufnahmezeit

    def _start_elapsed_ticker(self) -> None:
        self._tick_elapsed()

    def _tick_elapsed(self) -> None:
        if not self.is_recording:
            return
        seconds = int(time.monotonic() - self._recording_start)
        self.elapsed_label.config(text=f"{seconds // 60:d}:{seconds % 60:02d}")
        self._elapsed_job = self.root.after(500, self._tick_elapsed)

    def _stop_elapsed_ticker(self) -> None:
        if getattr(self, "_elapsed_job", None) is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.root.after_cancel(self._elapsed_job)
            self._elapsed_job = None

    # ------------------------------------------------------------------ recording

    def _may_record(self) -> bool:
        """Darf jetzt aufgenommen werden?

        Frueher hing das am geladenen Modell. Das war eine Sperre zu viel:
        `start_recording` fasst das Modell nirgends an — es oeffnet das
        Mikrofon und sammelt Puffer. Gebraucht wird es erst danach, in
        `_transcribe_audio`.

        Gemessen: bis das Modell steht, vergehen warm rund 2,2 s und kalt
        deutlich mehr. Die mittlere Aufnahmedauer betraegt 11,95 s. Wer gleich
        nach dem Anmelden diktiert, hat also laengst zu Ende gesprochen, bevor
        das Modell ueberhaupt gebraucht wird — es gibt keinen Grund, ihn
        vorher abzuweisen.

        Abgewiesen wird nur, was auch spaeter nicht gutgehen kann: ein Modell,
        das sich gar nicht laden liess. Dann waeren die gesprochenen Worte
        verloren, und das ist schlimmer als ein Hinweis vorab.
        """
        if self.model_error and not self.model_ready.is_set():
            self._on_model_error()
            return False
        return True

    def _await_model(self, deadline: float) -> bool:
        """Wartet auf ein einsatzbereites Modell. False heisst: kommt keines.

        Kein blosses `Event.wait()`: waehrend eines Modellwechsels ist das
        Ereignis kurzzeitig zurueckgesetzt und kann mit einem Fehler enden.
        Deshalb in kleinen Schritten warten und dazwischen nachsehen.
        """
        while time.monotonic() < deadline:
            if self.model_ready.wait(timeout=0.2):
                return True
            if self.model_error and not self.is_switching_model:
                return False
        return self.model_ready.is_set()

    def toggle_recording(self) -> None:
        """Toggle recording state for transcript mode.

        If not recording, starts recording. If recording, stops and transcribes.
        """
        if not self._may_record():
            return

        if not self.is_recording:
            self._prompt_mode = False
            self.start_recording()
        else:
            self.stop_recording_and_transcribe()

    def toggle_recording_generate(self) -> None:
        """Toggle recording state for prompt mode.

        Same as toggle_recording but uses the LLM to generate a response
        instead of just cleaning up the transcript.
        """
        if not self._may_record():
            return

        if not self.is_recording:
            self._prompt_mode = True
            self.start_recording()
        else:
            self.stop_recording_and_transcribe()

    def _schedule_max_duration_stop(self) -> None:
        self._cancel_max_duration_timer()
        self._max_duration_timer = threading.Timer(CFG.max_recording_seconds, self._on_max_duration_reached)
        self._max_duration_timer.daemon = True
        self._max_duration_timer.name = "max-duration"
        self._max_duration_timer.start()

    def _cancel_max_duration_timer(self) -> None:
        if self._max_duration_timer is not None:
            self._max_duration_timer.cancel()
            self._max_duration_timer = None

    def _on_max_duration_reached(self) -> None:
        logger.warning("Max recording duration (%ss) reached — auto-stopping", CFG.max_recording_seconds)
        self.root.after(0, self._auto_stop_recording)

    def _auto_stop_recording(self) -> None:
        if not self.is_recording:
            return
        self.stop_recording_and_transcribe()

    def _start_level_ticker(self) -> None:
        """Der Pegel bleibt sichtbar — nur der Takt startet und stoppt."""
        self._rms_level = 0.0
        self._level_tick()

    def _stop_level_ticker(self) -> None:
        if self._level_tick_job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.root.after_cancel(self._level_tick_job)
            self._level_tick_job = None
        self.level_meter.set_level(0.0)

    def _level_tick(self) -> None:
        if not self.is_recording:
            return
        level = _display_level(self._rms_level)
        self.level_meter.set_level(level)
        _notify_cursor_dot(self._state, level)
        self._level_tick_job = self.root.after(50, self._level_tick)

    def start_recording(self) -> None:
        """Start audio recording.

        Initializes the audio stream and begins capturing audio from the microphone.
        Recording will automatically stop after max_recording_seconds.
        """
        self.frames.clear()

        def callback(indata: np.ndarray, _frames: int, _time, status) -> None:
            if status:
                logger.warning("Audio stream status: %s", status)
            self.frames.append(indata.copy())
            # RMS für Level-Meter; kein Lock nötig (einzelner Float-Write).
            self._rms_level = float(np.mean(indata.astype(np.float32) ** 2))

        # Mitbenutzer des Mikrofons freigeben, damit das Gerät nicht doppelt
        # geöffnet wird.
        self._suspend_mic_consumers()

        # Gerät pro Aufnahme neu auflösen: Indizes verschieben sich beim
        # Aus-/Einstecken, sonst nimmt die App stumm vom falschen Gerät auf.
        device = _resolve_mic_device(CFG.mic_device_raw)
        if device != CFG.mic_device:
            logger.info("Mic device re-resolved: %r -> %r", CFG.mic_device, device)
            CFG.mic_device = device

        try:
            self.stream = sd.InputStream(
                samplerate=CFG.sample_rate,
                channels=CFG.channels,
                device=device,
                dtype="float32",
                callback=callback,
            )
            self.stream.start()
            self.is_recording = True
            self._recording_start = time.monotonic()
            self._schedule_max_duration_stop()
            self._apply_state("recording_prompt" if self._prompt_mode else "recording")
            self._beep(880, 80)
            self._start_elapsed_ticker()
            self._start_level_ticker()
            self._update_tray_tooltip()
            logger.info("Recording started (prompt_mode=%s)", self._prompt_mode)
        except sd.PortAudioError:
            logger.exception("PortAudio error on start_recording")
            self.is_recording = False
            self._apply_state(
                "error",
                t("error.mic_unavailable"),
                t("error.mic_not_responding", device=CFG.mic_device_raw),
            )
        except Exception:
            logger.exception("Unexpected error on start_recording")
            self.is_recording = False
            self._apply_state(
                "error",
                t("error.recording_not_started"),
                t("hint.see_log"),
            )

    def stop_recording_and_transcribe(self) -> None:
        """Stop recording and start transcription pipeline.

        Stops the audio stream, concatenates recorded frames, and starts
        the transcription thread.
        """
        self.is_recording = False
        self._cancel_max_duration_timer()
        self._stop_elapsed_ticker()
        self._stop_level_ticker()
        self._resume_mic_consumers()
        self._update_tray_tooltip()
        self._beep(220, 120)
        self._apply_state("transcribing")
        duration = time.monotonic() - self._recording_start if self._recording_start else 0
        logger.info("Recording stopped after %.1fs", duration)

        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except sd.PortAudioError:
            logger.exception("PortAudio error on stream.stop")
        finally:
            self.stream = None

        if not self.frames:
            self._apply_state("no_signal", t("status.no_recording"), t("hint.no_audio"))
            self._prompt_mode = False
            return

        audio = np.concatenate(self.frames, axis=0).flatten()
        self.frames.clear()

        # Pegel protokollieren — macht "Mikro stumm" im Log sofort sichtbar.
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        logger.info("Recording captured: %.1fs, peak=%.4f", duration, peak)

        # Kein-Signal-Erkennung: klare Meldung statt stiller "Keine Sprache"-Leere.
        if peak < SILENCE_PEAK_THRESHOLD:
            logger.warning(
                "No microphone signal (peak=%.4f) — Mikro stumm oder falsches Gerät?",
                peak,
            )
            self._apply_state(
                "no_signal",
                None,
                t("hint.mic_silent", device=CFG.mic_device_raw),
            )
            self._prompt_mode = False
            return

        threading.Thread(
            target=self._transcribe_audio,
            args=(audio,),
            daemon=True,
            name="transcribe",
        ).start()
