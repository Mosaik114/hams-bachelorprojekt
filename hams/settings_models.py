"""The settings group "Transcription": Whisper model (choose, download, delete), dictation language, hardware.

What lies in the model cache is read in a background thread (`scan_inventory`): the first scan after a while can
take half a second on a cold disk, and the Tk thread must never wait for it. Until the answer arrives, the rows
show the last known state.
"""

from __future__ import annotations

import threading
import tkinter as tk

from hams import dictation_language
from hams import models as whisper_models
from hams import ui_kit as uk
from hams.config import CFG, logger
from hams.i18n import Text, t, tn
from hams.settings_sections import CONTROL_WIDTH, Section, choose_dictation_language, dictation_options, group
from hams.ui_kit import Theme


def catalog_options(installed, *, meta=lambda info: info.meta, group=None) -> list:
    """The Whisper catalog as select options; a model that is not installed shows its download size.

    Shared by the settings and the setup assistant; `meta` and `group` (both per model) are what differs.
    """
    return [
        uk.Option(
            info.model_id,
            info.label,
            meta=meta(info),
            group=group(info) if group else "",
            trailing="" if info.model_id in installed else f"↓ {info.size_label}",
        )
        for info in whisper_models.CATALOG
    ]


def storage_text(cached: list) -> str:
    """One line that says how much lies on the disk.

    Count and size instead of a list of names: "Turbo, Large v3, Medium, Tiny" ran out of the row as soon as more
    than two models were there. Which ones they are is in the select above and in the delete list.
    """
    if not cached:
        return t("settings.storage.empty")
    total_bytes = sum(entry.size_bytes for entry in cached)
    return tn("settings.storage.summary", len(cached), size=whisper_models.format_bytes(total_bytes))


def confirm_text(entry) -> str:
    """The question before deleting - depending on whether something usable goes.

    An aborted download often leaves 0 B on the disk; "frees 0 B" would be a meaningless number. Then what
    goes counts, not how much.
    """
    if not entry.complete:
        return t("settings.storage.confirm_partial", model=entry.label)
    return t("settings.storage.confirm", model=entry.label, size=whisper_models.format_bytes(entry.size_bytes))


def download_text(label: str, done: int, total: int, percent: int) -> str:
    """What carries the wait: model, share, absolute amount - in one line."""
    if total > 0:
        progress = whisper_models.format_progress(done, total)
        return t("settings.download.progress", model=label, percent=percent, progress=progress)
    return t("settings.download.loaded", model=label, size=whisper_models.format_bytes(done))


def dictation_hint(setting: str) -> str:
    """Description of the dictation language: with "auto" the notice that every recording is detected anew."""
    if setting == dictation_language.AUTO:
        return t("settings.dictation.auto_hint")
    return t("settings.dictation.hint")


class TranscriptionSection(Section):
    """Whisper model with download and deletion, dictation language and the hardware in use."""

    MODEL_HINT = Text("settings.model.hint")
    DELETE_PLACEHOLDER = Text("settings.storage.delete_placeholder")
    DELETE_WIDTH = 88  # logical; "Löschen" plus air
    #: How long the success message stays before the row shows the inventory again.
    DELETE_NOTE_MS = 4000
    #: While the model loads, the hardware row asks again at this interval.
    HARDWARE_POLL_MS = 800

    def __init__(self, window, master: tk.Misc) -> None:
        super().__init__(window)
        self.installed: set = set()
        self.cached: list = []
        #: Model whose download this window is showing.
        self.downloading = ""
        #: Model for which the delete question is open.
        self.delete_target = ""
        self._scanning = False

        self.group = group(master, t("settings.section.transcription"))
        body = self.group.body
        self.row_model = uk.SettingRow(
            body, t("settings.model.title"), variant="description", description=self.MODEL_HINT, stacked=True
        )
        self.row_model.pack(fill="x")
        self.model_select = uk.Select(body, bg_under=Theme.GROUP_SURFACE, on_change=self.select_model)
        self.row_model.set_control(self.model_select)
        # During a download this cross takes the place of the select; it is only hooked in then. Deliberately just
        # an icon: a labelled button takes width from the progress line, and that line is the actual feedback.
        self.cancel_button = uk.IconButton(
            body,
            "close",
            self.cancel_download,
            bg_under=Theme.GROUP_SURFACE,
            tooltip=t("settings.download.cancel_tooltip"),
        )
        self.cancel_button.set_hover_colour(Theme.ERROR)
        self._build_dictation_language(body)
        self._build_storage(body)
        self.row_hardware = uk.SettingRow(
            body, t("settings.hardware.title"), variant="display", value="—", hairline=False
        )
        self.row_hardware.pack(fill="x")
        self.scan_inventory()

    def _build_dictation_language(self, body: tk.Misc) -> None:
        """The dictation language. Applies from the next recording, without a restart: the model is multilingual,
        only the language passed to Whisper and the instructions to Ollama change."""
        self.row_dictation = uk.SettingRow(
            body,
            t("settings.dictation.title"),
            variant="description",
            description=dictation_hint(CFG.transcription_language),
        )
        self.row_dictation.pack(fill="x")
        self.select_dictation = uk.Select(
            body,
            bg_under=Theme.GROUP_SURFACE,
            options=dictation_options(),
            value=CFG.transcription_language,
            on_change=self.select_dictation_language,
        )
        uk.keep_logical_width(self.select_dictation, CONTROL_WIDTH)
        self.row_dictation.set_control(self.select_dictation)

    def _build_storage(self, body: tk.Misc) -> None:
        # A row of its own for what lies on the disk. Deliberately not a bin beside the select: that always shows
        # the *configured* model, and exactly that must not be deleted. A deletion therefore needs a choice of its
        # own - and it only appears when there is something to delete.
        self.row_storage = uk.SettingRow(body, t("settings.storage.title"), variant="description", description="—")
        self.row_storage.pack(fill="x")
        self.select_delete = uk.Select(
            body, bg_under=Theme.GROUP_SURFACE, on_change=self.choose_delete, placeholder=self.DELETE_PLACEHOLDER
        )
        uk.keep_logical_width(self.select_delete, CONTROL_WIDTH)
        self.row_storage.set_control(self.select_delete)
        # The question takes the place of the select - the same trick as the download cancel. Two buttons side by
        # side need a frame, because a row takes only one control.
        self.delete_box = tk.Frame(body, bg=Theme.GROUP_SURFACE)
        self.delete_box.focus_ring = True  # the buttons inside carry it
        self.delete_button = uk.SecondaryButton(
            self.delete_box,
            t("settings.storage.delete"),
            self.confirm_delete,
            bg_under=Theme.GROUP_SURFACE,
            role="recording",
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(self.delete_button, self.DELETE_WIDTH)
        self.delete_button.pack(side="left")
        self.delete_cancel = uk.IconButton(
            self.delete_box,
            "close",
            self.abort_delete,
            bg_under=Theme.GROUP_SURFACE,
            tooltip=t("settings.storage.delete_cancel_tooltip"),
        )
        self.delete_cancel.pack(side="left", padx=(uk.px(uk.Space.XS), 0))

    # ---------------------------------------------------------- show / hide

    def sync(self) -> None:
        self.select_dictation.set_value(CFG.transcription_language)
        self.row_dictation.set_description(dictation_hint(CFG.transcription_language))
        self.downloading = ""
        self.delete_target = ""
        self.cancel_button.set_enabled(True)
        self.delete_button.set_enabled(True)
        self.delete_cancel.set_enabled(True)
        self.row_model.set_control(self.model_select)
        self.model_select.set_enabled(True)
        self._show_inventory()
        self.scan_inventory()

    def on_show(self) -> None:
        self.attach_download()
        self.refresh_hardware()

    # ---------------------------------------------------------- inventory

    def scan_inventory(self) -> None:
        """Reads the model cache in the background; `apply_inventory` shows the result."""
        if self._scanning:
            return
        self._scanning = True
        threading.Thread(target=self._scan_worker, daemon=True, name="model-scan").start()

    def _scan_worker(self) -> None:
        try:
            cached = list(whisper_models.cached_whisper_models())
            installed = whisper_models.installed_whisper_models()
        except Exception:  # noqa: BLE001 - boundary of the thread; both functions already log their own errors
            logger.exception("Model cache scan failed")
            cached, installed = [], set()
        self.app.ui_call(lambda: self.apply_inventory(installed, cached))

    def apply_inventory(self, installed: set, cached: list) -> None:
        """On the Tk thread: the scan result into the model select and the storage row."""
        self._scanning = False
        self.installed, self.cached = set(installed), list(cached)
        self._show_inventory()

    def _show_inventory(self) -> None:
        if self.downloading:
            self.model_select.set_options(self.model_options())
            self.model_select.set_value(CFG.model_size)
        else:
            self.refresh_models()
        if not self.delete_target and not self.pending("storage_note"):
            self.refresh_storage()

    # ---------------------------------------------------------- Whisper models

    def model_options(self) -> list:
        """The catalogue as select options, separated into installed and available.

        A custom value from the config stands there as "custom" - it is not silently reset to Turbo.
        """
        installed = self.installed

        def model_group(info) -> str:
            installed_now = info.model_id in installed
            return t("settings.model.group_installed" if installed_now else "settings.model.group_available")

        options = catalog_options(installed, group=model_group)
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

    def refresh_models(self) -> None:
        self.model_select.set_options(self.model_options())
        self.model_select.set_value(CFG.model_size)
        self.update_model_hint()

    def on_model_changed(self) -> None:
        """Called by the app after a switch.

        The model's lifetime deliberately does not depend on the settings window: the switch goes on even if the
        window is closed meanwhile.
        """
        if not self.alive():
            return
        self.model_select.set_enabled(True)
        self.refresh_hardware()
        self.refresh_models()
        self.scan_inventory()

    def update_model_hint(self) -> None:
        """Tells "active" apart from "chosen for the next start"."""
        if self.downloading:
            return  # the progress has priority over the hint
        chosen = CFG.model_size
        active = self.app.models.active_model_size
        failed = self.app.models.fallback
        if failed:
            info = whisper_models.find(failed)
            self.row_model.set_description(t("settings.model.load_failed", model=info.label if info else failed))
        elif chosen not in self.installed and whisper_models.find(chosen):
            self.row_model.set_description(t("settings.model.needs_download"))
        elif active is not None and chosen != active:
            self.row_model.set_description(t("settings.model.next_start"))
        else:
            self.row_model.set_description(self.model_hint(chosen))

    @classmethod
    def model_hint(cls, model_id: str) -> str:
        """Where the model stands - no longer in the field, but below it."""
        info = whisper_models.find(model_id)
        return info.meta if info is not None else cls.MODEL_HINT

    def select_model(self, model_id: str) -> None:
        """Takes the choice - but only if the model is really there.

        A model that is not installed is deliberately *not* stored: the next start would otherwise download
        several gigabytes without a word, because faster-whisper silently downloads missing models.
        """
        if model_id == CFG.model_size:
            self.update_model_hint()
            return
        if model_id not in self.installed:
            self.begin_download(model_id)
            return
        logger.info("Whisper model chosen: %s", model_id)
        self.model_select.set_value(model_id)
        if self.app.switch_model(model_id):
            self.model_select.set_enabled(False)
            self.row_model.set_description(t("status.model_loading"))
        else:
            self.model_select.set_value(CFG.model_size)
            self.row_model.set_description(t("settings.model.locked_while_recording"))

    def select_dictation_language(self, setting) -> None:
        """Stores the dictation language; it applies from the next recording, without a restart."""
        setting = choose_dictation_language(setting)
        if setting is not None:
            self.row_dictation.set_description(dictation_hint(setting))

    def refresh_hardware(self) -> None:
        if not self.alive():
            return
        models = self.app.models
        if models.model_error:
            self.row_hardware.set_value(t("settings.hardware.error"))
        elif models.model_ready.is_set():
            device = models.active_device or "?"
            label = "GPU" if device == "cuda" else device.upper()
            self.row_hardware.set_value(t("settings.hardware.auto", device=label))
        else:
            self.row_hardware.set_value(t("status.model_loading"))
            self.after("hardware", self.HARDWARE_POLL_MS, self.refresh_hardware)

    # ------------------------------------------------------- deleting models

    def deletable_models(self) -> list:
        """What may be deleted - with the reason for what may not.

        Three rules, each with a reason:

        * The **loaded** model stays. Windows does not give up an open model file; the attempt ended in an error.
        * The **configured** model stays while it is complete. Otherwise the config would point at something that
          is gone, and faster-whisper silently downloads what is missing at the next start - exactly the silent
          gigabyte download the model choice avoids.
        * **Remains of aborted downloads** may always be removed. They take space and cannot be loaded anyway.
        """
        active_model = self.app.models.active_model_size
        configured = CFG.model_size
        allowed = []
        for entry in self.cached:
            if entry.model_id == active_model:
                continue
            if entry.complete and entry.model_id == configured:
                continue
            allowed.append(entry)
        return allowed

    def refresh_storage(self) -> None:
        """Rebuilds the inventory row and the delete choice."""
        self.delete_target = ""
        deletable = self.deletable_models()
        self.row_storage.set_control(self.select_delete if deletable else None)
        self.select_delete.set_options([uk.Option(entry.model_id, entry.label, meta=entry.meta) for entry in deletable])
        self.select_delete.set_value(None)
        self.select_delete.set_enabled(bool(deletable) and not self.downloading)
        self.row_storage.set_description(storage_text(self.cached))

    def choose_delete(self, model_id: str) -> None:
        """The choice becomes a question - deleting only comes after it."""
        entry = next((e for e in self.deletable_models() if e.model_id == model_id), None)
        if entry is None:
            self.refresh_storage()
            return
        self.delete_target = model_id
        self.row_storage.set_control(self.delete_box)
        self.row_storage.set_description(confirm_text(entry))

    def abort_delete(self) -> None:
        self.refresh_storage()

    def confirm_delete(self) -> None:
        """Deletes in the background - on large models that takes noticeably long."""
        model_id = self.delete_target
        if not model_id:
            return
        self.delete_target = ""
        self.delete_button.set_enabled(False)
        self.delete_cancel.set_enabled(False)
        self.row_storage.set_description(t("settings.storage.deleting"))
        threading.Thread(target=self._delete_worker, args=(model_id,), daemon=True, name="hams-delete").start()

    def _delete_worker(self, model_id: str) -> None:
        try:
            ok, _free, message = whisper_models.delete_whisper_model(model_id)
            cached = list(whisper_models.cached_whisper_models())
            installed = whisper_models.installed_whisper_models()
        except Exception:  # noqa: BLE001 - boundary of the thread
            logger.exception("Deleting the model failed: %s", model_id)
            info = whisper_models.find(model_id)
            ok, message = False, t("model.delete.failed", model=info.label if info else model_id)
            cached, installed = self.cached, self.installed
        self.app.ui_call(lambda: self.delete_done(ok, message, installed, cached))

    def delete_done(self, ok: bool, message: str, installed: set, cached: list) -> None:
        if not self.alive():
            return
        self.delete_button.set_enabled(True)
        self.delete_cancel.set_enabled(True)
        self.installed, self.cached = set(installed), list(cached)
        self.refresh_storage()
        self.refresh_models()  # the inventory has changed
        self.row_storage.set_description(message)
        logger.info("Deleting model: %s", message)
        # The message stands briefly, then the inventory takes over again. A sentence that stays for good could
        # no longer be placed at the next glance into the window.
        self.after("storage_note", self.DELETE_NOTE_MS, self._clear_storage_note)

    def _clear_storage_note(self) -> None:
        if self.alive() and not self.delete_target:
            self.row_storage.set_description(storage_text(self.cached))

    # ---------------------------------------------------------- model download

    def begin_download(self, model_id: str) -> None:
        """Starts the download from the select.

        The select itself stays at the previous model: it is stored only once the new one is really there and
        loaded.
        """
        info = whisper_models.find(model_id)
        if info is None:
            return
        self.model_select.set_value(CFG.model_size)
        problem = self.app.models.start_download(model_id)
        if problem:
            self.row_model.set_description(problem)
            return
        self.downloading = model_id
        self.row_model.set_control(self.cancel_button)
        self.row_model.set_description(download_text(info.label, 0, 0, 0))
        self.refresh_storage()

    def cancel_download(self) -> None:
        self.row_model.set_description(t("settings.download.cancelling"))
        self.cancel_button.set_enabled(False)
        self.app.models.cancel_download()

    def attach_download(self) -> None:
        """Shows a download that is older than this showing of the window."""
        running = self.app.models.downloading
        if running is None:
            return
        self.downloading = running
        self.row_model.set_control(self.cancel_button)
        models = self.app.models
        self.on_download_progress(models.download_done, models.download_total, models.download_percent)

    def on_download_progress(self, done: int, total: int, percent: int) -> None:
        if not self.alive() or not self.downloading:
            return
        info = whisper_models.find(self.downloading)
        label = info.label if info else self.downloading
        self.row_model.set_description(download_text(label, done, total, percent))

    def on_download_finished(self, model_id: str, error: str, switching: bool) -> None:
        """Clears the download display again.

        On success the select stays locked: the switch goes on and reports back through `on_model_changed`.
        """
        if not self.alive():
            return
        self.downloading = ""
        self.cancel_button.set_enabled(True)
        self.row_model.set_control(self.model_select)
        if not error:
            # The new model is in the cache now; until the scan confirms it, count it as installed.
            self.installed.add(model_id)
        self.refresh_storage()
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
            self.update_model_hint()
        self.scan_inventory()
