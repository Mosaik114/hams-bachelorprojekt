"""Einstellungen: Ollama-Modell nachladen (KI) und Glossar bearbeiten (Allgemein).

Beides ohne Terminal: der Download nutzt dieselbe Aufgabe wie der Einrichtungsassistent
(`setup_wizard.run_task` mit `ollama.pull`), das Glossar öffnet der Standard-Editor.
"""

from __future__ import annotations

import threading

from hams import glossary, setup_wizard
from hams import ollama as ollama_api
from hams import ui_kit as uk
from hams.config import CFG, logger
from hams.i18n import t
from hams.setup_wizard import Task, TaskView, task_view
from hams.ui_kit import Theme

#: Zustände, in denen die Download-Zeile stehen bleibt, auch wenn das Modell (noch) nicht fehlt.
_STICKY = (setup_wizard.RUNNING, setup_wizard.FAILED, setup_wizard.CANCELLED)


def pull_row_needed(state, model: str, task: Task) -> bool:
    """Ob die Zeile zum Nachladen des Cleanup-Modells sichtbar ist — Logik ohne Widget."""
    if task.state in _STICKY:
        return True
    return bool(model) and state.online and model not in state.names


def pull_view(task: Task, model: str) -> TaskView:
    return task_view(
        task,
        idle=t("settings.ollama.pull_idle", model=ollama_api.display_name(model)),
        done=t("settings.ollama.pull_done", model=ollama_api.display_name(model)),
    )


class ExtrasSectionMixin:
    """Braucht `win`, `alive()`, `_ollama_state`, `_check_ollama()` von SettingsWindow."""

    GLOSSARY_WIDTH = 124  # logisch, wie „Erneut starten“

    # ---------------------------------------------------------- Ollama-Modell nachladen

    def _build_pull_row(self, group: uk.GroupSurface) -> None:
        from hams.setup_window import TaskRow

        self.pull_task = Task()
        self._pull_model = CFG.ollama_model_cleanup
        self.pull_row = TaskRow(
            group.body,
            t("settings.ollama.pull_title"),
            start=self._start_model_pull,
            cancel=self._cancel_model_pull,
            hairline=True,
        )
        self._refresh_pull_row()

    def _refresh_pull_row(self) -> None:
        """Zeigt oder versteckt die Zeile unter dem Cleanup-Modell."""
        row = getattr(self, "pull_row", None)
        if row is None or not self.alive():
            return
        if not self.pull_task.running:
            self._pull_model = CFG.ollama_model_cleanup
        if not pull_row_needed(self._ollama_state, self._pull_model, self.pull_task):
            row.row.pack_forget()
            row.bar_box.pack_forget()
            return
        row.row.pack(fill="x", after=self.row_cleanup_model)
        row.show(pull_view(self.pull_task, self._pull_model))

    def _start_model_pull(self) -> None:
        if self.pull_task.running or not self._pull_model:
            return
        name = self._pull_model
        logger.info("Settings: pulling Ollama model %s", name)

        def work(progress, cancelled) -> None:
            self.app.ollama.pull(name, progress, cancelled)

        setup_wizard.run_task(self.pull_task, work, self._post_pull, setup_wizard.describe_error)
        self._refresh_pull_row()

    def _post_pull(self, callback) -> None:
        """Aus dem Hintergrundthread auf den Tk-Thread; ein geschlossenes Fenster schluckt den Rest."""

        def run() -> None:
            callback()
            if not self.alive():
                return
            if self.pull_task.state == setup_wizard.DONE:
                # Ollama gleich neu fragen: das Modell steht jetzt in der Liste.
                threading.Thread(target=self._check_ollama, daemon=True, name="ollama-check").start()
            self._refresh_pull_row()

        # Nie `self.win.after` aus diesem Thread: Tk ist nicht threadsicher. Der Dispatcher der App reicht es weiter.
        self.app._ui_call(run)

    def _cancel_model_pull(self) -> None:
        if getattr(self, "pull_task", None) is not None and self.pull_task.running:
            self.pull_task.cancel()

    # ---------------------------------------------------------- Glossar

    def _build_glossary_row(self, group: uk.GroupSurface) -> None:
        self.row_glossary = uk.SettingRow(
            group.body, t("settings.glossary.title"), variant="description", description=t("settings.glossary.hint")
        )
        self.row_glossary.pack(fill="x")
        self.glossary_button = uk.SecondaryButton(
            group.body,
            t("settings.glossary.button"),
            self._edit_glossary,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(self.glossary_button, self.GLOSSARY_WIDTH)
        self.row_glossary.set_control(self.glossary_button)

    def _edit_glossary(self) -> None:
        try:
            glossary.open_in_editor()
        except OSError as exc:
            logger.warning("Glossary could not be opened: %s", type(exc).__name__)
            self.row_glossary.set_description(t("settings.glossary.failed", path=glossary.glossary_path()))
            return
        self.row_glossary.set_description(t("settings.glossary.opened"))
