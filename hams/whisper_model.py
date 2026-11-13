"""The Whisper model of the running app: load, switch, download (`ModelManager`)."""

import gc
import threading
import time
from typing import Protocol

from hams import models as whisper_models
from hams.backend import WhisperModel
from hams.backend_preload import cuda_libraries_found
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import t

#: Compute types per device, tried in this order: the first one the device supports wins. float16 is the fast
#: path on an NVIDIA GPU; int8 runs on cards without float16 and on any CPU.
COMPUTE_PRIORITY = {
    "cuda": ("float16", "int8_float16", "int8"),
    "cpu": ("int8", "float32"),
}


def usable_devices(priority) -> tuple[str, ...]:
    """The configured devices minus CUDA when cuBLAS is missing.

    ctranslate2 would load the model on the GPU anyway and only fail at the
    first dictation (``cublas64_12.dll is not found``). The installed app
    ships without the CUDA libraries until `gpu_support` fetched them.
    """
    if "cuda" in priority and not cuda_libraries_found():
        logger.info("CUDA libraries missing - skipping the GPU, using the next device")
        return tuple(device for device in priority if device != "cuda")
    return tuple(priority)


#: How long a finished recording waits for the model before it gives up. Warm it is there after ~2 s, cold after
#: ~20 s; the limit is generous, but it exists, so no thread hangs forever.
MODEL_WAIT_SECONDS = 120.0


class ModelEvents(Protocol):
    """What the model manager reports, always on the Tk thread (the app implements it)."""

    def model_loaded(self, model_id: str, ok: bool, requested: str) -> None:
        """A load ended: `model_id` is active (`ok`), or none could be loaded. `requested` was asked for."""

    def download_progress(self) -> None:
        """New numbers in `download_done`, `download_total` and `download_percent`."""

    def download_finished(self, model_id: str, error: str) -> None:
        """The download ended; `error` is "" on success or `models.CANCELLED`."""


class ModelManager:
    """The Whisper model of the running app: load, switch, download. No Tk.

    One owner for every load: the first one at start, a retry after an error, a switch from the settings, the
    wizard or a finished download. All of them go through `request`, which hands out a new generation. The workers
    load one after the other under `transcribe_lock` (never two models in memory, never in the middle of a
    transcription), and only the newest generation may commit its result. An older one that finishes late is
    discarded and freed instead of overwriting the newer choice.

    Phases (`phase`): idle -> loading (no model yet) or switching (one was loaded before) -> ready or failed. A new
    request while busy supersedes the running one instead of being refused or starting a second, parallel load.

    Results reach `events` on the Tk thread through `ui_call`; after `close()` nothing is reported any more.
    """

    def __init__(self, ui_call, events: ModelEvents) -> None:
        self._ui_call = ui_call
        self.events = events
        self.model: WhisperModel | None = None
        #: Which model is loaded. `CFG.model_size` is the *choice*; the two differ during and after a failed switch.
        self.active_model_size: str | None = None
        self.active_device: str | None = None
        self.active_compute_type: str | None = None
        self.model_ready = threading.Event()
        self.model_error: str | None = None
        #: The model the user asked for that could not be loaded while an older one stayed active, else None.
        self.fallback: str | None = None
        #: Short guard around `model` and its companions.
        self._lock = threading.Lock()
        #: Serialises transcription against a model switch: a switch waits for a running transcription instead of
        #: putting a second model into memory next to the first.
        self.transcribe_lock = threading.Lock()
        #: Rises with every request; a load that comes back late sees from it that it is no longer wanted.
        self._generation = 0
        #: The model being loaded (first start, retry or switch), else None.
        self._switching_to: str | None = None
        self._phase = "idle"
        self._closed = False
        # The download belongs to the app, not to a window: it goes on when the settings are closed meanwhile.
        self.downloader = whisper_models.ModelDownloader()
        # Before the first model access; the switch holds for the whole process.
        whisper_models.disable_xet_transfer()
        self.download_done = 0
        self.download_total = 0
        #: The hub reports the total size bit by bit; without this floor the percentage would run backwards.
        self.download_percent = 0
        #: A catalog model that is being downloaded because it has to be loaded next (start, retry), else None.
        self.load_after_download: str | None = None
        #: `_generation` when the running download started: a newer model request since then wins over it.
        self._download_generation = 0

    # ------------------------------------------------------------------ state

    @property
    def phase(self) -> str:
        """idle, loading, switching, ready or failed."""
        return self._phase

    @property
    def is_switching(self) -> bool:
        """Whether any load is under way, the first one included."""
        return self._switching_to is not None

    @property
    def switching_to(self) -> str | None:
        return self._switching_to

    @property
    def downloading(self) -> str | None:
        """The model being downloaded, or None."""
        return self.downloader.model_id if self.downloader.active else None

    @property
    def failed(self) -> bool:
        """No model, and the last attempt failed: a recording now could never be transcribed."""
        return bool(self.model_error) and not self.model_ready.is_set()

    @staticmethod
    def needs_download(model_id: str) -> bool:
        """Whether `model_id` is a catalog model that isn't in the local cache yet."""
        return whisper_models.find(model_id) is not None and model_id not in whisper_models.installed_whisper_models()

    def current_model(self) -> WhisperModel | None:
        """A stable reference to the active model.

        A transcription takes it exactly once and works with it to the end, so a switch cannot swap the model out
        from under it.
        """
        with self._lock:
            return self.model

    def wait_until_ready(self, deadline: float) -> bool:
        """Waits for a usable model until `deadline` (monotonic). False: none will come.

        Not a plain `Event.wait()`: during a switch the event is cleared for a moment and the switch can end in an
        error, so it waits in small steps and looks in between.
        """
        while time.monotonic() < deadline:
            if self.model_ready.wait(timeout=0.2):
                return True
            if self.model_error and not self.is_switching:
                return False
        return self.model_ready.is_set()

    # ------------------------------------------------------------------ loading

    def load_or_download(self, model_id: str) -> str:
        """Loads `model_id`, downloading it first (with progress) if it isn't in the cache.

        faster-whisper would otherwise fetch a missing model by itself, silently and without any progress - up to
        3 GB behind a "Loading model ..." that never moves. Returns "" when the load or download is under way, else
        the reason the download could not start (also kept in `model_error`).
        """
        if not self.needs_download(model_id):
            self.request(model_id, persist=False)
            return ""
        self.load_after_download = model_id
        if self.downloading != model_id:
            problem = self.start_download(model_id)
            if problem:
                self.load_after_download = None
                self.model_error = problem
                return problem
        return ""

    def request(self, model_id: str, *, persist: bool) -> int:
        """The one place that starts a load. Returns its generation."""
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._switching_to = model_id
            self._phase = "switching" if self.active_model_size else "loading"
        if self.load_after_download not in (None, model_id):
            # A download that a start or retry waited for no longer decides which model is loaded.
            logger.info("Model request %s supersedes the awaited download of %s", model_id, self.load_after_download)
            self.load_after_download = None
        logger.info("Model request %d: %s (%s)", generation, model_id, self._phase)
        self._start_switch_thread(model_id, generation, persist)
        return generation

    def close(self) -> None:
        """At shutdown: invalidate running loads, free the model, report nothing any more."""
        self._closed = True
        self.downloader.cancel()  # unfinished files stay, the next download resumes them
        with self._lock:
            self._generation += 1
            self._switching_to = None
            self.model = None
            self.model_ready.clear()
            self._phase = "idle"

    def _start_switch_thread(self, model_id: str, generation: int, persist: bool = True) -> None:
        """A method of its own, so tests can run the load synchronously."""
        threading.Thread(
            target=self._switch_worker, args=(model_id, generation, persist), daemon=True, name="model-load"
        ).start()

    def _is_current(self, generation: int) -> bool:
        return generation == self._generation and not self._closed

    def _build_model(self, model_id: str) -> tuple:
        """Loads a model through the configured devices and compute types.

        Returns:
            (model, device, compute type) or (None, None, error text).
        """
        errors: list[str] = []
        for device in usable_devices(CFG.model_device_priority):
            for compute_type in COMPUTE_PRIORITY.get(device, ()):
                try:
                    logger.info("Loading Whisper model %s on %s/%s", model_id, device, compute_type)
                    model = WhisperModel(model_id, device=device, compute_type=compute_type)
                    logger.info("Model ready (%s/%s)", device, compute_type)
                    return model, device, compute_type
                except Exception as exc:  # noqa: BLE001 - try the next device or compute type
                    errors.append(f"{device}/{compute_type}: {exc}")
                    logger.warning("Model load failed: %s", errors[-1])
        return None, None, " | ".join(errors)

    def _switch_worker(self, model_id: str, generation: int, persist: bool = True) -> None:
        """Loads in the background, after a running transcription and an older load.

        Free first, then load: otherwise two models would sit in VRAM for a moment; CTranslate2 frees its memory
        only once the model is dereferenced. The price is that a failure leaves no model active - hence the
        fallback to the previous one.
        """
        try:
            with self.transcribe_lock:
                outcome = self._load_for(model_id, generation)
        except Exception as exc:  # noqa: BLE001 - boundary of the thread: never die silently
            logger.exception("Model load crashed")
            outcome = self._commit(generation, model_id, None, None, f"{type(exc).__name__}: {exc}")
        if outcome is None:
            logger.info("Model request %d superseded, result discarded", generation)
            return
        loaded_id, ok = outcome
        self._post(lambda: self._finish_load(loaded_id, ok, persist=persist, requested=model_id))

    def _load_for(self, model_id: str, generation: int) -> tuple[str, bool] | None:
        """Under `transcribe_lock`: load and, if still wanted, commit. None = superseded."""
        if not self._is_current(generation):
            return None
        previous = self.active_model_size
        with self._lock:
            self.model = None
            self.model_ready.clear()
        gc.collect()

        model, device, info = self._build_model(model_id)
        if model is None and previous and previous != model_id and self._is_current(generation):
            logger.warning("Falling back to %s after %s failed", previous, model_id)
            model, device, info = self._build_model(previous)
            if model is not None:
                model_id = previous
        return self._commit(generation, model_id, model, device, info)

    def _commit(self, generation: int, model_id: str, model, device, info) -> tuple[str, bool] | None:
        """Takes the result if it is still the newest; otherwise it is discarded and freed."""
        with self._lock:
            current = self._is_current(generation)
            if current:
                self._switching_to = None
                self._phase = "ready" if model is not None else "failed"
                if model is not None:
                    self.model = model
                    self.active_device = device
                    self.active_compute_type = info
                    self.active_model_size = model_id
                    self.model_error = None
                    self.model_ready.set()
                else:
                    self.model_error = info
        if not current:
            del model  # the discarded model frees its memory
            gc.collect()
            return None
        if model is None:
            logger.error("All model-load attempts failed: %s", info)
        return model_id, model is not None

    def _finish_load(self, model_id: str, ok: bool, *, persist: bool, requested: str) -> None:
        """On the Tk thread: remember a fallback, save the choice, tell the app.

        If the requested model differs from the loaded `model_id`, the request failed and the previous model
        stayed: the fallback is never saved over the user's choice.
        """
        fallback = ok and requested != model_id
        if ok:
            self.fallback = requested if fallback else None
        if ok and persist and not fallback:
            CFG.model_size = model_id
            _persist_config_value("model", "size", model_id)
        self.events.model_loaded(model_id, ok, requested)

    # ------------------------------------------------------------------ download

    def start_download(self, model_id: str) -> str:
        """Starts the download of a catalog model. Returns "" when it runs, else the reason in plain words."""
        info = whisper_models.find(model_id)
        if info is None:
            return t("download.unknown_model")
        if self.downloader.active:
            # Before touching the numbers: they belong to the download that is running.
            return t("download.already_running")
        self.download_done = 0
        self.download_total = info.size_bytes
        self.download_percent = 0
        self._download_generation = self._generation
        problem = self.downloader.start(info, self._on_download_progress, self._on_download_finished)
        if problem:
            logger.info("Download of %s not started: %s", model_id, problem)
        else:
            logger.info("Download of %s started (%s)", model_id, info.size_label)
        return problem

    def cancel_download(self) -> None:
        if self.downloader.active:
            logger.info("Cancel requested for %s", self.downloader.model_id)
            self.downloader.cancel()

    def take_awaited_download(self, model_id: str) -> bool:
        """Whether the app was waiting for `model_id` to load it (start, retry); that wait is over now."""
        awaited = model_id == self.load_after_download
        if awaited:
            self.load_after_download = None
        return awaited

    @property
    def download_superseded(self) -> bool:
        """Another model was requested while the download ran: that later choice stays."""
        return self._generation != self._download_generation

    def _on_download_progress(self, done: int, total: int) -> None:
        """On the download thread: only arithmetic, nothing drawn."""
        self.download_done = done
        self.download_total = total or self.download_total
        if self.download_total > 0:
            share = int(done * 100 / self.download_total)
            # Capped at 99: 100 % is shown only once everything is there.
            self.download_percent = max(self.download_percent, min(99, share))
        self._post(self.events.download_progress)

    def _on_download_finished(self, model_id: str, error: str) -> None:
        """On the download thread."""
        if not error:
            self.download_percent = 100
        self._post(lambda: self.events.download_finished(model_id, error))

    def _post(self, callback) -> None:
        """Hands a callback to the Tk thread - harmless during shutdown."""
        if not self._closed:
            self._ui_call(callback)
