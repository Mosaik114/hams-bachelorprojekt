"""Recording from the microphone to the finished audio: the `Recorder` and the level helpers."""

import threading
import time

import numpy as np
import sounddevice as sd

from hams.audio_devices import resolve_mic_device
from hams.config import CFG, logger

#: Whisper takes 16 kHz mono. Recording in exactly that format means nothing has to be converted: other rates
#: or a second channel used to reach Whisper unconverted and came out as gibberish.
SAMPLE_RATE = 16_000
CHANNELS = 1

#: Held while PortAudio is re-initialised (`reinitialise_portaudio`) and while a recording opens its stream, so
#: neither happens in the middle of the other.
PORTAUDIO_LOCK = threading.Lock()


def reinitialise_portaudio(busy) -> bool:
    """Makes PortAudio read the device list again, so a microphone plugged in since the start shows up.

    PortAudio enumerates the devices once, when it is initialised; `query_devices` keeps returning that list.
    Terminating and initialising again closes every open stream, so it is skipped while `busy()` says one is
    open (recording, level meter). Call it off the Tk thread: on Windows it takes a noticeable moment.
    Returns whether it re-initialised.
    """
    with PORTAUDIO_LOCK:
        if busy():
            return False
        try:
            sd._terminate()
            sd._initialize()
        except (sd.PortAudioError, AttributeError, OSError):
            logger.warning("PortAudio could not be re-initialised; the device list may be out of date", exc_info=True)
            return False
    return True


# Peak unterhalb dieser Schwelle gilt als "kein Signal" (Mikro stumm/falsches
# Gerät). Sprache liegt typ. bei 0.05+, Grundrauschen bei ~0.0002 — 0.005 trennt
# beides sauber. Wird sowohl für die Normalisierung als auch die Kein-Signal-
# Erkennung genutzt, damit beide dieselbe Grenze verwenden.
SILENCE_PEAK_THRESHOLD = 0.005

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


class MicrophoneUnavailable(RuntimeError):
    """The configured microphone cannot be opened: unplugged, renamed, or held by another program."""


class Recorder:
    """The microphone: one input stream, the recorded blocks and the current level. No Tk, no model.

    Recording never needs the Whisper model: it opens the microphone and collects blocks, so it may start while the
    model is still loading (the transcription waits for it instead, see `ModelManager.wait_until_ready`).

    Other users of the microphone (the level meters of the settings and the setup assistant) register as consumers:
    they are suspended before the recording opens the device and resumed once it has closed it again, so the device
    is never open twice.
    """

    def __init__(self) -> None:
        self.stream = None
        self.frames: list[np.ndarray] = []
        self.is_recording = False
        #: Mean square of the newest block; written by PortAudio's thread (a single float, no lock needed).
        self.level = 0.0
        self.started_at = 0.0
        self.consumers: list = []
        self._limit_timer: threading.Timer | None = None

    # ------------------------------------------------------------------ microphone consumers

    def register_consumer(self, consumer) -> None:
        """`consumer` has `suspend_microphone()` and `resume_microphone()`."""
        if consumer not in self.consumers:
            self.consumers.append(consumer)

    def unregister_consumer(self, consumer) -> None:
        if consumer in self.consumers:
            self.consumers.remove(consumer)

    def suspend_consumers(self) -> None:
        for consumer in list(self.consumers):
            try:
                consumer.suspend_microphone()
            except Exception:  # noqa: BLE001 - one broken consumer must not stop the recording
                logger.exception("Microphone consumer could not be suspended")

    def resume_consumers(self) -> None:
        for consumer in list(self.consumers):
            try:
                consumer.resume_microphone()
            except Exception:  # noqa: BLE001 - one broken consumer must not stop the others
                logger.exception("Microphone consumer could not be resumed")

    def busy(self) -> bool:
        """Whether the recording holds the microphone."""
        return self.is_recording or self.stream is not None

    # ------------------------------------------------------------------ recording

    @property
    def elapsed(self) -> float:
        """Seconds since the recording started (0 without one)."""
        return time.monotonic() - self.started_at if self.started_at else 0.0

    def start(self, *, max_seconds: float, on_limit) -> None:
        """Opens the configured microphone and starts recording.

        The device is resolved again for every recording: indexes move when a microphone is plugged in or out, and
        the app would otherwise record silently from the wrong one. `on_limit` runs on a timer thread once
        `max_seconds` have passed.

        Raises:
            MicrophoneUnavailable: the microphone cannot be opened (PortAudio refused it, or sounddevice found no
                device with the configured name).
            Exception: anything else; in every case the stream is closed and the consumers are resumed.
        """
        self.frames.clear()
        self.suspend_consumers()
        device = resolve_mic_device(CFG.mic_device_raw)
        if device != CFG.mic_device:
            logger.info("Mic device re-resolved: %r -> %r", CFG.mic_device, device)
            CFG.mic_device = device
        try:
            with PORTAUDIO_LOCK:  # not while the device list is being re-read
                self.stream = sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=CHANNELS, device=device, dtype="float32", callback=self._on_block
                )
                self.stream.start()
                self.is_recording = True
        except (sd.PortAudioError, ValueError) as exc:
            self._abandon_start()
            raise MicrophoneUnavailable(str(exc)) from exc
        except Exception:
            self._abandon_start()
            raise
        self.level = 0.0
        self.started_at = time.monotonic()
        self._start_limit_timer(max_seconds, on_limit)

    def _on_block(self, indata: np.ndarray, _frames: int, _time, status) -> None:
        """PortAudio's callback, on its own thread."""
        if status:
            logger.warning("Audio stream status: %s", status)
        self.frames.append(indata.copy())
        self.level = float(np.mean(indata.astype(np.float32) ** 2))

    def _abandon_start(self) -> None:
        """A start that failed: close a stream that opened but didn't start, give the microphone back."""
        self.is_recording = False
        self._cancel_limit_timer()
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:  # noqa: BLE001 - the error of the start is reported already
                logger.exception("Stream cleanup after a failed start failed")
        self.resume_consumers()

    def stop(self) -> np.ndarray | None:
        """Ends the recording and gives the microphone back. Returns the mono audio, None if nothing came in."""
        self.is_recording = False
        self._cancel_limit_timer()
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except sd.PortAudioError:
            logger.exception("PortAudio error on stream.stop")
        finally:
            self.stream = None
            # Only now: the level meter opens the device the recording held until a moment ago.
            self.resume_consumers()
        self.level = 0.0
        if not self.frames:
            return None
        audio = np.concatenate(self.frames, axis=0).flatten()
        self.frames.clear()
        return audio

    def close(self) -> None:
        """At shutdown: abort a running stream without waiting for its buffers."""
        self._cancel_limit_timer()
        self.is_recording = False
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:  # noqa: BLE001 - shutting down anyway
                logger.exception("Stream cleanup failed")

    def _start_limit_timer(self, seconds: float, on_limit) -> None:
        self._cancel_limit_timer()
        self._limit_timer = threading.Timer(seconds, on_limit)
        self._limit_timer.daemon = True
        self._limit_timer.name = "max-duration"
        self._limit_timer.start()

    def _cancel_limit_timer(self) -> None:
        if self._limit_timer is not None:
            self._limit_timer.cancel()
            self._limit_timer = None
