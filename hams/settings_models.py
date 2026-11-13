"""Einstellungen: Abschnitt Whisper-Modelle."""

import threading

from hams import models as whisper_models
from hams import ui_kit as uk
from hams.config import CFG, logger
from hams.i18n import t, tn


class ModelSectionMixin:
    """Einstellungen: Whisper-Modelle auswaehlen, laden und loeschen."""

    # ---------------------------------------------------------- devices

    # ---------------------------------------------------------- Whisper-Modelle

    def model_options(self) -> list:
        """Katalog als Auswahlliste, getrennt nach installiert und ladbar.

        Ein eigener Wert aus der Konfiguration steht als "Benutzerdefiniert"
        dabei — er wird nicht stillschweigend auf Turbo zurueckgesetzt.
        """
        installed = self._installed_models
        options = []
        for info in whisper_models.CATALOG:
            is_installed = info.model_id in installed
            options.append(
                uk.Option(
                    info.model_id,
                    info.label,
                    meta=info.meta,
                    group=t("settings.model.group_installed" if is_installed else "settings.model.group_available"),
                    trailing="" if is_installed else f"↓ {info.size_label}",
                )
            )
        current = CFG.model_size
        if whisper_models.find(current) is None:
            options.insert(
                0,
                uk.Option(
                    current,
                    t("settings.model.custom", model=current),
                    meta=t("settings.model.custom_meta"),
                    group=t("settings.model.group_installed"),
                ),
            )
        return options

    def _refresh_models(self) -> None:
        self._installed_models = whisper_models.installed_whisper_models()
        self.model_select.set_options(self.model_options())
        self.model_select.set_value(CFG.model_size)
        self._update_model_hint()

    def on_model_changed(self) -> None:
        """Wird von der Anwendung nach einem Wechsel gerufen.

        Die Modell-Lebensdauer haengt bewusst nicht am Einstellungsfenster: der
        Wechsel laeuft weiter, auch wenn es zwischendurch geschlossen wird.
        """
        if not self.alive():
            return
        self._installed_models = whisper_models.installed_whisper_models()
        self.model_select.set_options(self.model_options())
        self.model_select.set_value(CFG.model_size)
        self.model_select.set_enabled(True)
        self._update_model_hint()
        self._refresh_storage()

    def _update_model_hint(self) -> None:
        """Unterscheidet "aktiv" von "fuer den naechsten Start gewaehlt"."""
        if not self.alive() or self._downloading:
            return  # der Fortschritt hat Vorrang vor dem Hinweistext
        chosen = CFG.model_size
        active = self.app.active_model_size
        failed = getattr(self.app, "model_fallback", None)
        if failed:
            info = whisper_models.find(failed)
            self.row_model.set_description(t("settings.model.load_failed", model=info.label if info else failed))
        elif chosen not in self._installed_models and whisper_models.find(chosen):
            self.row_model.set_description(t("settings.model.needs_download"))
        elif active is not None and chosen != active:
            self.row_model.set_description(t("settings.model.next_start"))
        else:
            self.row_model.set_description(self.model_hint(chosen))

    @classmethod
    def model_hint(cls, model_id: str) -> str:
        """Einordnung des Modells — steht nicht mehr im Feld, sondern darunter."""
        info = whisper_models.find(model_id)
        return info.meta if info is not None else cls.MODEL_HINT

    def _select_model(self, model_id: str) -> None:
        """Auswahl uebernehmen — aber nur, wenn das Modell wirklich da ist.

        Ein nicht installiertes Modell wird bewusst *nicht* gespeichert: der
        naechste Start wuerde sonst wortlos mehrere Gigabyte nachladen, weil
        faster-whisper fehlende Modelle stillschweigend herunterlaedt.
        """
        if model_id == CFG.model_size:
            self._update_model_hint()
            return
        if model_id not in self._installed_models:
            self._begin_download(model_id)
            return
        logger.info("Whisper-Modell gewählt: %s", model_id)
        self.model_select.set_value(model_id)
        if self.app.switch_model(model_id):
            self.model_select.set_enabled(False)
            self.row_model.set_description(t("status.model_loading"))
        else:
            self.model_select.set_value(CFG.model_size)
            self.row_model.set_description(t("settings.model.locked_while_recording"))

    # ------------------------------------------------------- Modelle loeschen

    def deletable_models(self) -> list:
        """Was geloescht werden darf — mit Begruendung, was nicht.

        Drei Regeln, und jede hat einen Grund:

        * Das **geladene** Modell bleibt. Windows gibt eine geoeffnete
          Modelldatei nicht her; der Versuch endete mit einem Fehler.
        * Das **eingestellte** Modell bleibt, solange es vollstaendig ist.
          Sonst zeigte die Konfiguration auf etwas, das nicht mehr da ist, und
          faster-whisper laedt Fehlendes beim naechsten Start wortlos nach —
          genau der stille Gigabyte-Download, den die Modellauswahl vermeidet.
        * **Reste abgebrochener Downloads** darf man immer wegraeumen. Sie
          belegen Platz und lassen sich ohnehin nicht laden.
        """
        aktiv = self.app.active_model_size
        eingestellt = CFG.model_size
        erlaubt = []
        for eintrag in whisper_models.cached_whisper_models():
            if eintrag.model_id == aktiv:
                continue
            if eintrag.complete and eintrag.model_id == eingestellt:
                continue
            erlaubt.append(eintrag)
        return erlaubt

    @staticmethod
    def storage_text(bestand: list) -> str:
        """Eine Zeile, die sagt, wie viel auf der Platte liegt.

        Anzahl und Groesse statt einer Namensliste: „Turbo, Large v3, Medium,
        Tiny" lief aus der Zeile heraus, sobald mehr als zwei Modelle lagen.
        Welche es sind, steht im Auswahlfeld darueber und in der Loeschliste.
        """
        if not bestand:
            return t("settings.storage.empty")
        gesamt = sum(eintrag.size_bytes for eintrag in bestand)
        return tn("settings.storage.summary", len(bestand), size=whisper_models.format_bytes(gesamt))

    def _refresh_storage(self) -> None:
        """Bestandszeile und Loeschauswahl neu aufbauen."""
        if not self.alive():
            return
        self._delete_target = ""
        bestand = list(whisper_models.cached_whisper_models())
        loeschbar = self.deletable_models()
        self.row_storage.set_control(self.select_delete if loeschbar else None)
        self.select_delete.set_options(
            [uk.Option(eintrag.model_id, eintrag.label, meta=eintrag.meta) for eintrag in loeschbar]
        )
        self.select_delete.set_value(None)
        self.select_delete.set_enabled(bool(loeschbar) and not self._downloading)
        self.row_storage.set_description(self.storage_text(bestand))

    def _choose_delete(self, model_id: str) -> None:
        """Aus der Auswahl wird eine Rueckfrage — geloescht wird erst danach."""
        eintrag = next((e for e in self.deletable_models() if e.model_id == model_id), None)
        if eintrag is None:
            self._refresh_storage()
            return
        self._delete_target = model_id
        self.row_storage.set_control(self._delete_box)
        self.row_storage.set_description(self.confirm_text(eintrag))

    @staticmethod
    def confirm_text(eintrag) -> str:
        """Die Rueckfrage — je nachdem, ob etwas Brauchbares verschwindet.

        Bei einem abgebrochenen Download steht oft 0 B auf der Platte; „Gibt
        0 B frei" waere dort eine sinnlose Zahl. Dann zaehlt, *was* weggeht,
        nicht wie viel.
        """
        if not eintrag.complete:
            return t("settings.storage.confirm_partial", model=eintrag.label)
        return t("settings.storage.confirm", model=eintrag.label, size=whisper_models.format_bytes(eintrag.size_bytes))

    def _abort_delete(self) -> None:
        self._refresh_storage()

    def _confirm_delete(self) -> None:
        """Loescht im Hintergrund — auf grossen Modellen dauert das spuerbar."""
        model_id = self._delete_target
        if not model_id:
            return
        self._delete_target = ""
        self.delete_button.set_enabled(False)
        self.delete_cancel.set_enabled(False)
        self.row_storage.set_description(t("settings.storage.deleting"))
        threading.Thread(target=self._delete_worker, args=(model_id,), daemon=True, name="hams-delete").start()

    def _delete_worker(self, model_id: str) -> None:
        try:
            erfolg, _frei, meldung = whisper_models.delete_whisper_model(model_id)
        except Exception:  # noqa: BLE001 - Grenze des Threads
            logger.exception("Modell löschen fehlgeschlagen: %s", model_id)
            info = whisper_models.find(model_id)
            erfolg, meldung = False, t("model.delete.failed", model=info.label if info else model_id)
        self.app._ui_call(lambda: self._delete_done(erfolg, meldung))

    def _delete_done(self, erfolg: bool, meldung: str) -> None:
        if not self.alive():
            return
        self.delete_button.set_enabled(True)
        self.delete_cancel.set_enabled(True)
        self._refresh_storage()
        self._refresh_models()  # der Bestand hat sich geaendert
        self.row_storage.set_description(meldung)
        logger.info("Modell löschen: %s", meldung)
        # Die Meldung steht kurz, dann uebernimmt wieder der Bestand. Ein
        # dauerhaft stehender Satz waere nach dem naechsten Blick ins Fenster
        # nicht mehr einzuordnen.
        self._storage_note_job = self.win.after(self.DELETE_NOTE_MS, self._clear_storage_note)

    def _clear_storage_note(self) -> None:
        self._storage_note_job = None
        if self.alive() and not self._delete_target:
            self.row_storage.set_description(self.storage_text(list(whisper_models.cached_whisper_models())))

    # ---------------------------------------------------------- Modell-Download

    @staticmethod
    def download_text(label: str, done: int, total: int, percent: int) -> str:
        """Was beim Warten trägt: Modell, Anteil, absolute Menge — in einer Zeile."""
        if total > 0:
            progress = whisper_models.format_progress(done, total)
            return t("settings.download.progress", model=label, percent=percent, progress=progress)
        return t("settings.download.loaded", model=label, size=whisper_models.format_bytes(done))

    def _begin_download(self, model_id: str) -> None:
        """Startet den Download aus der Auswahl heraus.

        Die Auswahl selbst bleibt beim bisherigen Modell: gespeichert wird erst,
        wenn das neue wirklich liegt und geladen ist.
        """
        info = whisper_models.find(model_id)
        if info is None:
            return
        self.model_select.set_value(CFG.model_size)
        problem = self.app.start_download(model_id)
        if problem:
            self.row_model.set_description(problem)
            return
        self._downloading = model_id
        self.row_model.set_control(self.cancel_button)
        self.row_model.set_description(self.download_text(info.label, 0, 0, 0))
        self._refresh_storage()

    def _cancel_download(self) -> None:
        self.row_model.set_description(t("settings.download.cancelling"))
        self.cancel_button.set_enabled(False)
        self.app.cancel_download()

    def _attach_download(self) -> None:
        """Zeigt einen laufenden Download, der aelter ist als dieses Fenster."""
        running = self.app.downloading_model
        if running is None:
            return
        self._downloading = running
        self.row_model.set_control(self.cancel_button)
        self.on_download_progress(self.app.download_done, self.app.download_total, self.app.download_percent)

    def on_download_progress(self, done: int, total: int, percent: int) -> None:
        if not self.alive() or not self._downloading:
            return
        info = whisper_models.find(self._downloading)
        label = info.label if info else self._downloading
        self.row_model.set_description(self.download_text(label, done, total, percent))

    def on_download_finished(self, model_id: str, error: str, switching: bool) -> None:
        """Raeumt die Download-Anzeige wieder ab.

        Bei Erfolg bleibt das Auswahlfeld gesperrt: der Modellwechsel laeuft
        weiter und meldet sich ueber `on_model_changed` zurueck.
        """
        if not self.alive():
            return
        self._downloading = ""
        self.cancel_button.set_enabled(True)
        self.row_model.set_control(self.model_select)
        self._refresh_storage()
        self._installed_models = whisper_models.installed_whisper_models()
        self.model_select.set_options(self.model_options())
        self.model_select.set_value(CFG.model_size)
        info = whisper_models.find(model_id)
        label = info.label if info else model_id
        if error == whisper_models.CANCELLED:
            self.model_select.set_enabled(True)
            self.row_model.set_description(t("settings.download.cancelled", model=label))
        elif error:
            self.model_select.set_enabled(True)
            self.row_model.set_description(t("settings.download.failed", error=error))
        elif switching:
            self.model_select.set_enabled(False)
            self.row_model.set_description(t("status.model_loading_named", model=label))
        else:
            self.model_select.set_enabled(True)
            self._update_model_hint()
