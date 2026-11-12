"""Das Whisper-Modell der laufenden App: laden, wechseln, herunterladen."""

import contextlib
import gc
import threading
import tkinter as tk

from hams import models as whisper_models
from hams.backend import WhisperModel
from hams.config import CFG, _persist_config_value, logger


class WhisperModelMixin:
    """Whisper-Modell laden, wechseln und herunterladen."""

    # ------------------------------------------------------------------ model loading

    def _load_model_async(self) -> None:
        threading.Thread(target=self._load_model, daemon=True, name="model-load").start()

    # ------------------------------------------------------------------ Modellwechsel

    @property
    def is_switching_model(self) -> bool:
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
        for device in CFG.model_device_priority:
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

    def switch_model(self, model_id: str) -> bool:
        """Startet einen Wechsel. Gibt False zurueck, wenn er gerade nicht geht."""
        if model_id == self.active_model_size and self.model_ready.is_set():
            return False
        if self.is_recording:
            self._apply_state(
                "recording_prompt" if self._prompt_mode else "recording",
                None,
                "Das Modell lässt sich während einer Aufnahme nicht wechseln.",
            )
            return False
        if self.is_switching_model:
            return False

        self._model_generation += 1
        generation = self._model_generation
        self._switching_to = model_id
        label = whisper_models.find(model_id)
        self._apply_state("model_loading", f"{label.label if label else model_id} wird geladen")
        self._start_switch_thread(model_id, generation)
        return True

    def _start_switch_thread(self, model_id: str, generation: int) -> None:
        """Eigene Methode, damit Tests den Wechsel synchron ausfuehren koennen."""
        threading.Thread(
            target=self._switch_worker, args=(model_id, generation), daemon=True, name="model-switch"
        ).start()

    def _switch_worker(self, model_id: str, generation: int) -> None:
        """Wechsel im Hintergrund — wartet auf eine laufende Transkription.

        Reihenfolge bewusst: erst freigeben, dann laden. Andernfalls laegen
        kurzzeitig zwei Modelle im VRAM; CTranslate2 gibt seinen Speicher erst
        nach dem Dereferenzieren frei. Der Preis ist, dass bei einem Fehlschlag
        kein Modell aktiv waere — deshalb der Rueckfall unten.
        """
        previous = self.active_model_size
        with self._transcribe_lock:  # laufenden Lauf abwarten
            if generation != self._model_generation:
                return  # inzwischen ueberholt
            with self._model_lock:
                self.model = None
                self.model_ready.clear()
            gc.collect()

            model, device, info = self._build_model(model_id)
            if model is None and previous and previous != model_id:
                logger.warning("Rueckfall auf %s nach Fehler bei %s", previous, model_id)
                model, device, info = self._build_model(previous)
                if model is not None:
                    model_id = previous

            if generation != self._model_generation:
                # Ein neuerer Wechsel hat uebernommen — dieses Ergebnis verwerfen.
                del model
                gc.collect()
                return

            with self._model_lock:
                if model is not None:
                    self.model = model
                    self._active_device = device
                    self._active_compute_type = info
                    self.active_model_size = model_id
                    self.model_error = None
                    self.model_ready.set()
                else:
                    self.model_error = info

        self._switching_to = None
        self.root.after(0, lambda: self._after_switch(model_id, model is not None))

    def _after_switch(self, model_id: str, ok: bool) -> None:
        if ok:
            CFG.model_size = model_id
            _persist_config_value("model", "size", model_id)
            self._apply_state("ready")
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
            return "Unbekanntes Modell"
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
        if self._shutting_down:
            return
        with contextlib.suppress(tk.TclError, RuntimeError):  # Fenster ist weg
            self.root.after(0, callback)

    def _push_download_progress(self) -> None:
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.on_download_progress(self.download_done, self.download_total, self.download_percent)

    def _after_download(self, model_id: str, error: str) -> None:
        """Nach dem Download: bei Erfolg direkt aktivieren.

        Der Wechsel gehoert hierher und nicht ins Einstellungsfenster — wer ein
        Modell laedt, will es benutzen, auch wenn er das Fenster inzwischen
        geschlossen hat.
        """
        if not error:
            self.download_percent = 100
        switching = self.switch_model(model_id) if not error else False
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.on_download_finished(model_id, error, switching)

    def _load_model(self) -> None:
        model, device, info = self._build_model(CFG.model_size)
        if model is None:
            self.model_error = info
            logger.error("All model-load attempts failed: %s", info)
            self.root.after(0, self._on_model_error)
            return
        with self._model_lock:
            self.model = model
            self._active_device = device
            self._active_compute_type = info
            self.active_model_size = CFG.model_size
            self.model_ready.set()
        self.root.after(0, lambda: self._on_model_loaded(device, info))

    def _on_model_loaded(self, device: str, compute_type: str) -> None:
        logger.info("Model ready for UI (%s/%s)", device, compute_type)
        self._apply_state("ready")
        self._update_tray_tooltip()

    def _on_model_error(self) -> None:
        self._apply_state(
            "error",
            "Modell konnte nicht geladen werden",
            f"„{CFG.model_size}“ ließ sich weder auf GPU noch auf CPU laden. Einzelheiten stehen im Log.",
        )
