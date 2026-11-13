"""Das Whisper-Modell der laufenden App: laden, wechseln, herunterladen."""

import gc
import threading

from hams import models as whisper_models
from hams.backend import WhisperModel
from hams.backend_preload import cuda_libraries_found
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import t


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


class WhisperModelMixin:
    """Whisper-Modell laden, wechseln und herunterladen."""

    # ------------------------------------------------------------------ model lifecycle
    #
    # One owner for every load: the first one at start, a retry after an error,
    # a switch from the settings, the wizard or a finished download. All of them
    # go through `_request_model`, which hands out a new generation. The workers
    # load one after the other under `_transcribe_lock` (never two models in
    # memory, never in the middle of a transcription), and only the newest
    # generation may commit its result. An older one that finishes late is
    # discarded and freed instead of overwriting the newer choice.
    #
    # Phases (`model_phase`): idle -> loading (no model yet) or switching (one was
    # loaded before) -> ready or failed. A new request while busy supersedes the
    # running one instead of being refused or starting a second, parallel load.

    def _load_model_async(self) -> None:
        """Loads the configured model (`CFG.model_size`): at start and after the wizard."""
        self._request_model(CFG.model_size, persist=False)

    def reload_model(self) -> bool:
        """The "Try again" button: load the configured model again unless a load is under way.

        Returns:
            False if a load is already running; that one reports its own result.
        """
        self._apply_state("model_loading")
        if self.is_switching_model:
            return False
        self.model_error = None
        self._load_model_async()
        return True

    @property
    def model_phase(self) -> str:
        """idle, loading, switching, ready or failed."""
        return self._model_phase

    @property
    def is_switching_model(self) -> bool:
        """Whether any load is under way, the first one included."""
        return self._switching_to is not None

    def current_model(self) -> WhisperModel | None:
        """Stabile Referenz auf das aktive Modell.

        Ein Transkriptionslauf holt sie sich genau einmal und arbeitet danach
        nur noch damit — so kann ein Wechsel ihm das Modell nicht unter den
        Haenden wegtauschen.
        """
        with self._model_lock:
            return self.model

    def _build_model(self, model_id: str) -> tuple:
        """Laedt ein Modell ueber die konfigurierte Geraete-Matrix.

        Returns:
            (Modell, Geraet, Compute-Typ) oder (None, None, Fehlertext).
        """
        errors: list[str] = []
        for device in usable_devices(CFG.model_device_priority):
            for compute_type in CFG.model_compute_priority.get(device, ()):
                try:
                    logger.info("Loading Whisper model %s on %s/%s", model_id, device, compute_type)
                    model = WhisperModel(model_id, device=device, compute_type=compute_type)
                    logger.info("Model ready (%s/%s)", device, compute_type)
                    return model, device, compute_type
                except Exception as exc:
                    errors.append(f"{device}/{compute_type}: {exc}")
                    logger.warning("Model load failed: %s", errors[-1])
        return None, None, " | ".join(errors)

    def switch_model(self, model_id: str, *, force: bool = False) -> bool:
        """Startet einen Wechsel. Gibt False zurueck, wenn er gerade nicht geht.

        `force` laedt auch das schon aktive Modell neu - etwa auf der GPU, nachdem der
        Einrichtungsassistent die CUDA-Bibliotheken geholt hat. Laeuft gerade ein anderer
        Ladevorgang (auch der erste beim Start), loest dieser Wunsch ihn ab.
        """
        if model_id == self.active_model_size and self.model_ready.is_set() and not force:
            return False
        if self.is_recording:
            self._apply_state(
                "recording_prompt" if self._prompt_mode else "recording",
                None,
                t("hint.no_switch_while_recording"),
            )
            return False
        if model_id == self._switching_to and not force:
            return True  # schon unterwegs
        self._request_model(model_id, persist=True)
        label = whisper_models.find(model_id)
        self._apply_state("model_loading", t("status.model_loading_named", model=label.label if label else model_id))
        return True

    def _request_model(self, model_id: str, *, persist: bool) -> int:
        """Die einzige Stelle, die einen Ladevorgang beginnt. Gibt dessen Generation zurueck."""
        with self._model_lock:
            self._model_generation += 1
            generation = self._model_generation
            self._switching_to = model_id
            self._model_phase = "switching" if self.active_model_size else "loading"
        logger.info("Model request %d: %s (%s)", generation, model_id, self._model_phase)
        self._start_switch_thread(model_id, generation, persist)
        return generation

    def _retire_model(self) -> None:
        """Beim Beenden: laufende Ladevorgaenge entwerten und das Modell freigeben."""
        with self._model_lock:
            self._model_generation += 1
            self._switching_to = None
            self.model = None
            self.model_ready.clear()
            self._model_phase = "idle"

    def _start_switch_thread(self, model_id: str, generation: int, persist: bool = True) -> None:
        """Eigene Methode, damit Tests den Ladevorgang synchron ausfuehren koennen."""
        threading.Thread(
            target=self._switch_worker, args=(model_id, generation, persist), daemon=True, name="model-load"
        ).start()

    def _is_current(self, generation: int) -> bool:
        return generation == self._model_generation and not getattr(self, "_shutting_down", False)

    def _switch_worker(self, model_id: str, generation: int, persist: bool = True) -> None:
        """Laedt im Hintergrund - nach einer laufenden Transkription und einem aelteren Ladevorgang.

        Reihenfolge bewusst: erst freigeben, dann laden. Andernfalls laegen
        kurzzeitig zwei Modelle im VRAM; CTranslate2 gibt seinen Speicher erst
        nach dem Dereferenzieren frei. Der Preis ist, dass bei einem Fehlschlag
        kein Modell aktiv waere - deshalb der Rueckfall auf das vorige.
        """
        try:
            with self._transcribe_lock:
                outcome = self._load_for(model_id, generation)
        except Exception as exc:  # Grenze des Threads: nie still sterben
            logger.exception("Model load crashed")
            outcome = self._commit(generation, model_id, None, None, f"{type(exc).__name__}: {exc}")
        if outcome is None:
            logger.info("Model request %d superseded, result discarded", generation)
            return
        loaded_id, ok = outcome
        self._post(lambda: self._after_switch(loaded_id, ok, persist=persist))

    def _load_for(self, model_id: str, generation: int) -> tuple[str, bool] | None:
        """Unter `_transcribe_lock`: laden und, falls noch gemeint, uebernehmen. None = ueberholt."""
        if not self._is_current(generation):
            return None
        previous = self.active_model_size
        with self._model_lock:
            self.model = None
            self.model_ready.clear()
        gc.collect()

        model, device, info = self._build_model(model_id)
        if model is None and previous and previous != model_id and self._is_current(generation):
            logger.warning("Rueckfall auf %s nach Fehler bei %s", previous, model_id)
            model, device, info = self._build_model(previous)
            if model is not None:
                model_id = previous
        return self._commit(generation, model_id, model, device, info)

    def _commit(self, generation: int, model_id: str, model, device, info) -> tuple[str, bool] | None:
        """Uebernimmt das Ergebnis, wenn es noch das neueste ist; sonst wird es verworfen und freigegeben."""
        with self._model_lock:
            current = self._is_current(generation)
            if current:
                self._switching_to = None
                self._model_phase = "ready" if model is not None else "failed"
                if model is not None:
                    self.model = model
                    self._active_device = device
                    self._active_compute_type = info
                    self.active_model_size = model_id
                    self.model_error = None
                    self.model_ready.set()
                else:
                    self.model_error = info
        if not current:
            del model  # das verworfene Modell gibt seinen Speicher frei
            gc.collect()
            return None
        if model is None:
            logger.error("All model-load attempts failed: %s", info)
        return model_id, model is not None

    def _after_switch(self, model_id: str, ok: bool, *, persist: bool = True) -> None:
        """Auf dem Tk-Thread. Ein inzwischen begonnener neuerer Ladevorgang meldet sich selbst."""
        if ok and persist:
            CFG.model_size = model_id
            _persist_config_value("model", "size", model_id)
        if not self.is_switching_model:
            if ok:
                logger.info("Model ready for UI (%s/%s)", self._active_device, self._active_compute_type)
                self._apply_state("ready")
                self._update_tray_tooltip()
            else:
                self._on_model_error()
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.on_model_changed()

    # ------------------------------------------------------------------ Modell-Download

    @property
    def downloading_model(self) -> str | None:
        """Welches Modell gerade geladen wird — oder None."""
        return self.downloader.model_id if self.downloader.active else None

    def start_download(self, model_id: str) -> str:
        """Startet den Download eines Katalogmodells.

        Returns:
            Leerer String, wenn er laeuft — sonst der Grund im Klartext.
        """
        info = whisper_models.find(model_id)
        if info is None:
            return t("download.unknown_model")
        self.download_done = 0
        self.download_total = info.size_bytes
        self.download_percent = 0
        problem = self.downloader.start(info, self._on_download_progress, self._on_download_finished)
        if problem:
            logger.info("Download von %s nicht gestartet: %s", model_id, problem)
        else:
            logger.info("Download von %s gestartet (%s)", model_id, info.size_label)
        return problem

    def cancel_download(self) -> None:
        if self.downloader.active:
            logger.info("Abbruch fuer %s angefordert", self.downloader.model_id)
            self.downloader.cancel()

    def _on_download_progress(self, done: int, total: int) -> None:
        """Laeuft im Download-Thread: nur rechnen, nichts zeichnen."""
        self.download_done = done
        self.download_total = total or self.download_total
        if self.download_total > 0:
            share = int(done * 100 / self.download_total)
            # Bei 99 deckeln: 100 % steht erst, wenn wirklich alles liegt.
            self.download_percent = max(self.download_percent, min(99, share))
        self._post(self._push_download_progress)

    def _on_download_finished(self, model_id: str, error: str) -> None:
        """Laeuft im Download-Thread."""
        self._post(lambda: self._after_download(model_id, error))

    def _post(self, callback) -> None:
        """Reicht einen Rueckruf in den Tk-Thread — auch beim Beenden gefahrlos."""
        if getattr(self, "_shutting_down", False):
            return
        self._ui_call(callback)

    def _download_views(self) -> list:
        """Die offenen Fenster, die einen Download anzeigen: Einstellungen und Einrichtung."""
        return [win for win in (self.settings_win, getattr(self, "setup_win", None)) if win is not None and win.alive()]

    def _push_download_progress(self) -> None:
        for view in self._download_views():
            view.on_download_progress(self.download_done, self.download_total, self.download_percent)

    def _after_download(self, model_id: str, error: str) -> None:
        """Nach dem Download: bei Erfolg direkt aktivieren.

        Der Wechsel gehoert hierher und nicht ins Einstellungsfenster — wer ein
        Modell laedt, will es benutzen, auch wenn er das Fenster inzwischen
        geschlossen hat.
        """
        if not error:
            self.download_percent = 100
        switching = self.switch_model(model_id) if not error else False
        for view in self._download_views():
            view.on_download_finished(model_id, error, switching)

    def _on_model_error(self) -> None:
        self._apply_state(
            "error",
            t("error.model_load_failed"),
            t("error.model_load_failed_detail", model=CFG.model_size),
        )
