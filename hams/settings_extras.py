"""The settings group "AI": text cleanup with Ollama, the two models, their state and pulling a missing model.

The pull runs without a terminal: it uses the same task as the setup assistant (`setup_wizard.run_task` with
`ollama.pull`). Ollama is asked every few seconds while the window is shown, always from a background thread.
"""

from __future__ import annotations

import threading
import tkinter as tk

from hams import ollama as ollama_api
from hams import setup_wizard
from hams import ui_kit as uk
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import t
from hams.settings_sections import Section, group
from hams.setup_wizard import Task, TaskView, task_view
from hams.ui_kit import Theme

#: States in which the pull row stays, even if the model is not (or no longer) missing.
_STICKY = (setup_wizard.RUNNING, setup_wizard.FAILED, setup_wizard.CANCELLED)


def pull_row_needed(state, model: str, task: Task) -> bool:
    """Whether the row for pulling the cleanup model is visible - logic without a widget."""
    if task.state in _STICKY:
        return True
    return bool(model) and state.online and model not in state.names


def pull_view(task: Task, model: str) -> TaskView:
    return task_view(
        task,
        idle=t("settings.ollama.pull_idle", model=ollama_api.display_name(model)),
        done=t("settings.ollama.pull_done", model=ollama_api.display_name(model)),
    )


def ollama_options(state, current: str) -> list:
    """Select options from one answer of the service - logic without a widget.

    Only installed models. Ollama knows no catalogue of remote models through this API, so none is invented.
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
        # The configured model is missing. It stays visible and stays stored: silently choosing another one
        # would be a decision that belongs to the user.
        if state.online:
            meta = t("settings.ollama.not_installed")
        elif state.error:
            meta = state.error
        else:
            meta = ""  # never asked yet - claim nothing
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


class AiSection(Section):
    """Cleanup switch, cleanup model with the state of Ollama, pull row and prompt model."""

    #: How often Ollama is asked while the window is shown - sparing, but not only once.
    OLLAMA_INTERVAL_MS = 5000

    def __init__(self, window, master: tk.Misc) -> None:
        super().__init__(window)
        #: The last answer of the service, taken over from the app. The signature beside it decides whether the
        #: selects need to be rebuilt at all.
        self.ollama_state = self.app.ollama_state
        self._signature = self.ollama_state.signature()
        self.ollama_online = self.ollama_state.online
        self.pull_task = Task()
        self._pull_model = CFG.ollama_model_cleanup

        self.group = group(master, t("settings.section.ai"))
        body = self.group.body
        self.row_cleanup = uk.SettingRow(
            body,
            t("settings.cleanup.title"),
            variant="description",
            description=t("settings.cleanup.hint"),
            interactive=True,
            command=lambda: self.switch_cleanup.toggle(),
        )
        self.row_cleanup.pack(fill="x")
        self.switch_cleanup = uk.Switch(
            body, bg_under=Theme.GROUP_SURFACE, value=CFG.ollama_cleanup_enabled, command=self._on_cleanup
        )
        self.row_cleanup.set_control(self.switch_cleanup)

        # Two separate selects, no shared "AI model": cleanup often runs with a small fast model, the prompt mode
        # with a larger one. Same control, separate values.
        self.row_cleanup_model = uk.SettingRow(body, t("settings.cleanup_model.title"), variant="status", stacked=True)
        self.row_cleanup_model.pack(fill="x")
        self.select_cleanup_model = uk.Select(
            body,
            bg_under=Theme.GROUP_SURFACE,
            value=CFG.ollama_model_cleanup,
            on_change=self.select_cleanup_model_name,
            placeholder=t("settings.ollama.no_model"),
        )
        self.row_cleanup_model.set_control(self.select_cleanup_model)
        # If the configured model is missing, this row pulls it with a button - without a terminal.
        from hams.setup_window import TaskRow

        self.pull_row = TaskRow(
            body,
            t("settings.ollama.pull_title"),
            start=self.start_model_pull,
            cancel=self.cancel_model_pull,
            hairline=True,
        )

        self.row_prompt_model = uk.SettingRow(
            body,
            t("settings.prompt_model.title"),
            variant="description",
            description=t("settings.prompt_model.hint"),
            hairline=False,
            stacked=True,
        )
        self.row_prompt_model.pack(fill="x")
        self.select_prompt_model = uk.Select(
            body,
            bg_under=Theme.GROUP_SURFACE,
            value=CFG.ollama_model_prompt,
            on_change=self.select_prompt_model_name,
            placeholder=t("settings.ollama.no_model"),
        )
        self.row_prompt_model.set_control(self.select_prompt_model)
        self._fill(self.ollama_state)

    def sync(self) -> None:
        """The app's last answer, the switch from the config, the rows that depend on both."""
        self.switch_cleanup.set(CFG.ollama_cleanup_enabled, animate=False)
        self.ollama_online = self.app.ollama_state.online
        self._fill(self.app.ollama_state)

    def _fill(self, state) -> None:
        self.ollama_state = state
        self._signature = state.signature()
        self.refresh_models()
        self.refresh_pull_row(force=True)
        self.show_status()
        if self.app.ollama_checked:
            self.sync_cleanup_dependency(force=True)
        else:
            # Nobody has answered yet: the plain hints, nothing derived from an answer that does not exist.
            self.row_cleanup_model.set_enabled(True)
            self.row_prompt_model.set_enabled(True)
            self.row_cleanup.set_description(t("settings.cleanup.hint"))
            self.row_prompt_model.set_description(t("settings.prompt_model.hint"))

    def on_show(self) -> None:
        self.schedule_check()

    def on_hide(self) -> None:
        super().on_hide()
        self.cancel_model_pull()  # no download that nobody sees any more

    # ---------------------------------------------------------- cleanup switch

    def _on_cleanup(self, value: bool) -> None:
        CFG.ollama_cleanup_enabled = value
        _persist_config_value("ollama", "cleanup_enabled", value)
        self.sync_cleanup_dependency()
        self.show_status()

    def sync_cleanup_dependency(self, *, force: bool = False) -> None:
        """Cleanup on and Ollama off - that must show at once.

        Otherwise the window would show an active switch beside a service that does not answer, and the
        contradiction would only show after the next recording.
        """
        if not (force or self.alive()):
            return
        self.row_cleanup_model.set_enabled(CFG.ollama_cleanup_enabled and self.ollama_online)
        # The prompt mode does not depend on the cleanup switch. Disabling both together would be convenient and
        # wrong: they are independent.
        self.row_prompt_model.set_enabled(self.ollama_online)
        self.refresh_pull_row(force=force)
        self.row_prompt_model.set_description(
            t("settings.prompt_model.ready") if self.ollama_online else t("ollama.unreachable")
        )
        if CFG.ollama_cleanup_enabled and not self.ollama_online:
            self.row_cleanup.set_description(t("settings.cleanup.offline"))
        else:
            self.row_cleanup.set_description(t("settings.cleanup.active"))

    # ---------------------------------------------------------- asking Ollama

    def schedule_check(self) -> None:
        """Asks every few seconds - only checking at opening would leave the display wrong if Ollama starts later."""
        if not self.alive():
            return
        threading.Thread(target=self.check_ollama, daemon=True, name="ollama-check").start()
        self.after("ollama", self.OLLAMA_INTERVAL_MS, self.schedule_check)

    def check_ollama(self) -> None:
        """Runs in the background thread - only ask here, draw nothing."""
        state = ollama_api.probe_or_offline(self.app.ollama.probe)
        self.app.ui_call(lambda: self.apply_state(state))

    def show_status(self) -> None:
        """Status row from the known state - without asking.

        As long as nobody answered in this session, it says "checking". Claiming an outage that nobody measured
        would simply be wrong.
        """
        if not self.app.ollama_checked:
            self.row_cleanup_model.set_status("idle", t("settings.ollama.checking"))
            return
        state = self.ollama_state
        if not state.online and not CFG.ollama_cleanup_enabled:
            # Without cleanup a missing Ollama is no error, only information.
            self.row_cleanup_model.set_status("idle", t("settings.ollama.optional"))
            return
        self.row_cleanup_model.set_status(
            "ready" if state.online else "error",
            t("settings.ollama.online") if state.online else state.error or t("ollama.unreachable"),
        )

    def apply_state(self, state) -> None:
        """Takes over an answer - on the Tk thread."""
        if not self.alive():
            return
        self.app.ollama_state = state
        self.app.ollama_checked = True
        self.ollama_online = state.online
        if state.signature() != self._signature:
            popover = uk.Popover.current
            if popover is not None and popover.trigger in (self.select_cleanup_model, self.select_prompt_model):
                self.show_status()
                return  # do not swap an open select under the user's fingers
            self.ollama_state = state
            self._signature = state.signature()
            self.refresh_models()
        self.show_status()
        self.sync_cleanup_dependency()

    # ---------------------------------------------------------- Ollama models

    def refresh_models(self) -> None:
        state = self.ollama_state
        self.select_cleanup_model.set_options(ollama_options(state, CFG.ollama_model_cleanup))
        self.select_cleanup_model.set_value(CFG.ollama_model_cleanup)
        self.select_prompt_model.set_options(ollama_options(state, CFG.ollama_model_prompt))
        self.select_prompt_model.set_value(CFG.ollama_model_prompt)

    def select_cleanup_model_name(self, name: str) -> None:
        """Writes only the cleanup key."""
        if not name or name == CFG.ollama_model_cleanup:
            return
        CFG.ollama_model_cleanup = name
        _persist_config_value("ollama", "model_transcript_cleanup", name)
        logger.info("Cleanup model: %s", name)
        self.select_cleanup_model.set_value(name)

    def select_prompt_model_name(self, name: str) -> None:
        """Writes only the prompt key."""
        if not name or name == CFG.ollama_model_prompt:
            return
        CFG.ollama_model_prompt = name
        _persist_config_value("ollama", "model_prompt_generate", name)
        logger.info("Prompt model: %s", name)
        self.select_prompt_model.set_value(name)

    # ---------------------------------------------------------- pulling the cleanup model

    def refresh_pull_row(self, *, force: bool = False) -> None:
        """Shows or hides the row below the cleanup model."""
        if not (force or self.alive()):
            return
        row = self.pull_row
        if not self.pull_task.running:
            self._pull_model = CFG.ollama_model_cleanup
        if not pull_row_needed(self.ollama_state, self._pull_model, self.pull_task):
            row.row.pack_forget()
            row.bar_box.pack_forget()
            return
        row.row.pack(fill="x", after=self.row_cleanup_model)
        row.show(pull_view(self.pull_task, self._pull_model))

    def start_model_pull(self) -> None:
        if self.pull_task.running or not self._pull_model:
            return
        name = self._pull_model
        logger.info("Settings: pulling Ollama model %s", name)

        def work(progress, cancelled) -> None:
            self.app.ollama.pull(name, progress, cancelled)

        setup_wizard.run_task(self.pull_task, work, self._post_pull, setup_wizard.describe_error)
        self.refresh_pull_row()

    def _post_pull(self, callback) -> None:
        """From the background thread to the Tk thread; a hidden window swallows the rest."""

        def run() -> None:
            callback()
            if not self.alive():
                return
            if self.pull_task.state == setup_wizard.DONE:
                # Ask Ollama again at once: the model is in the list now.
                threading.Thread(target=self.check_ollama, daemon=True, name="ollama-check").start()
            self.refresh_pull_row()

        # Never `self.win.after` from this thread: Tk is not thread-safe. The app's dispatcher passes it on.
        self.app.ui_call(run)

    def cancel_model_pull(self) -> None:
        if self.pull_task.running:
            self.pull_task.cancel()
