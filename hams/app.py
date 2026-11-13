"""The Hams application: the Tk shell around the core objects.

`FloatingTranscriberApp` is the main window (`MainWindowMixin`) and the tray (`TrayMixin`). It owns the core, which
has no Tk: the `Recorder` (microphone), the `ModelManager` (Whisper model, downloads), the `Pipeline` (transcription,
cleanup, delivery), one `OllamaClient` and the `UpdateNotice`. The core reports back through the app, which
implements `hams.status.StatusSink` and `hams.whisper_model.ModelEvents`; the windows (settings, setup assistant)
reach the core through the same objects.
"""

import contextlib
import subprocess
import sys
import threading
import time
import tkinter as tk

import numpy as np

from hams import branding, cursor_dot, transcription
from hams import models as whisper_models
from hams import ollama as ollama_api
from hams import ui_kit as uk
from hams.config import APP_DIR, CFG, CONFIG_PATH, logger
from hams.i18n import Text, t, tn
from hams.instance import _acquire_single_instance_mutex, _release_single_instance_mutex, restart_command
from hams.main_window import MainWindowMixin
from hams.recording import SILENCE_PEAK_THRESHOLD, MicrophoneUnavailable, Recorder, _display_level
from hams.settings_window import SettingsWindow
from hams.setup_window import SetupWindow
from hams.transcription import Pipeline
from hams.tray import TRAY_PALETTE, TrayMixin
from hams.ui_dispatch import UiDispatcher
from hams.ui_kit import Theme
from hams.update_notice import UpdateNotice
from hams.whisper_model import ModelManager
from hams.window_position import window_position

if sys.platform == "win32":
    import winsound

    import keyboard

    from hams.system_hotkeys import SystemHotkeys
else:
    # The same names with Linux behind them: the flow below stays the same on both systems.
    from hams import linux
    from hams.linux import keyboard, winsound
    from hams.portal_hotkeys import PortalSystemHotkeys as SystemHotkeys


class FloatingTranscriberApp(MainWindowMixin, TrayMixin):
    """The main window and tray of Hams, holding the recorder, the model, the pipeline and the update notice."""

    WINDOW_WIDTH = 360  # logical; room for both shortcuts as keys
    WINDOW_MIN_HEIGHT = 190  # logical; the real height comes from the layout

    def __init__(self, root: tk.Tk, *, setup: bool = False) -> None:
        self.root = root
        #: Gives background threads a way onto the Tk thread (see `ui_call`).
        self._ui_dispatcher = UiDispatcher(root)
        self._init_window()
        self._init_core()
        self._init_runtime_state()

        self._build_ui()
        self._fit_window()
        self.apply_state("model_loading")
        if setup and CFG.model_size not in whisper_models.installed_whisper_models():
            # faster-whisper would download the missing model silently. The assistant downloads it with progress;
            # until then the status line says what it is waiting for.
            self._model_deferred = True
            self.apply_state("setup_pending")
        else:
            self._load_model_async()
        self.pipeline.warm_up_ollama_async()
        transcription.prune_transcripts_async()
        self._register_hotkeys()

        logger.info("Window corners: %s", uk.round_window_corners(self.root))
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)
        self._setup_tray()
        self.updates.start()
        if sys.platform != "win32":
            linux.on_show_request(lambda: self.ui_call(self._show_window))
        self.root.withdraw()
        logger.info("Hams started")

    def _init_window(self) -> None:
        """Borderless window, scaling and fonts, position from the settings."""
        self.root.title(branding.PRODUCT_NAME)
        # Order matters: scaling first (sets tk scaling), then the fonts.
        uk.init_scale(self.root)
        uk.init_fonts(self.root)
        logger.info("System animations: %s", "on" if uk.init_animations() else "off")
        logger.info(
            "UI scale: %d dpi, factor %.2f, tk scaling %.3f",
            uk.Scale.dpi,
            uk.Scale.factor,
            float(self.root.tk.call("tk", "scaling")),
        )
        uk.frame_window(self.root)  # Windows borderless in the Mosaik design, Linux with the KDE title bar
        self._window_size = (uk.px(self.WINDOW_WIDTH), uk.px(self.WINDOW_MIN_HEIGHT))
        self._window_pos = window_position(CFG.window_x, CFG.window_y, *self._window_size, uk.work_areas())
        self._position_job: str | None = None
        self.root.geometry(f"+{self._window_pos[0]}+{self._window_pos[1]}")
        self.root.resizable(False, False)
        self.root.configure(bg=Theme.WINDOW_BG)
        self._apply_window_icon()
        self.settings_win: SettingsWindow | None = None
        self.setup_win: SetupWindow | None = None
        #: True while the start holds the model back for the setup assistant.
        self._model_deferred = False

    def _init_core(self) -> None:
        """The objects without Tk; they report back through this app."""
        self.recorder = Recorder()
        self.models = ModelManager(self.ui_call, self)
        #: The one client for the Ollama service: cleanup, *Ask AI*, warm-up, the settings' checks and pulls.
        self.ollama = ollama_api.OllamaClient.for_url(lambda: CFG.ollama_url)
        self.pipeline = Pipeline(self.models, self, self.ollama, on_delivered=self._add_to_history)
        self.updates = UpdateNotice(self.ui_call, self._on_update_found, self._refresh_tray)
        #: The service's last answer. It belongs to the app, so a settings window opened again does not claim
        #: "unreachable" until its own first check is back.
        self.ollama_state = ollama_api.OllamaState(False)
        self.ollama_checked = False

    def _init_runtime_state(self) -> None:
        """Interface state, timers, history and shortcuts."""
        self.drag_start_x = 0
        self.drag_start_y = 0
        self._state = "model_loading"
        self._done_job: str | None = None
        self._elapsed_job: str | None = None
        self._level_tick_job: str | None = None
        self._history: list[tuple[str, str, str]] = []  # (display time, preview, full text)
        self._history_lock = threading.Lock()
        self._window_visible = False
        self._prompt_mode = False
        self._shutting_down = False
        self._quit_deadline: float | None = None
        #: A model message that came in during a recording, shown after it (see `_model_notice`).
        self._pending_model_notice = None
        #: A model message held back by a recording until its dictation was pasted (see `_deliver_model_notice`).
        self._notice_after_pipeline = None
        #: Set by `_setup_tray`; the first states are shown before the tray exists.
        self.tray_icon = None
        self._system_hotkeys = SystemHotkeys()
        if sys.platform != "win32":
            # Create the device early: KWin needs a moment until it knows it.
            threading.Thread(target=linux.KEYBOARD.open, daemon=True, name="uinput").start()

    # ------------------------------------------------------------------ StatusSink

    @property
    def is_recording(self) -> bool:
        return self.recorder.is_recording

    def ui_call(self, fn) -> None:
        """Runs fn on the Tk thread. The one way for background threads to reach the UI.

        Safe from any thread (tray, hotkeys, downloads, model load, update check): it never blocks the caller and
        drops the call quietly once the app is shutting down.
        """
        self._ui_dispatcher.call(fn)

    def show_answer_length(self, characters: int) -> None:
        self.elapsed_label.config(text=tn("status.characters", characters))

    # ------------------------------------------------------------------ dpi

    def _on_root_configure(self, event: tk.Event) -> None:
        """Notices a move to a monitor with another scaling.

        Tk does not know `WM_DPICHANGED`, so the DPI is compared on the Configure event that comes anyway - no
        timer of its own.
        """
        if event.widget is not self.root:
            return
        dpi = uk.window_dpi(self.root)
        if dpi == uk.Scale.dpi:
            return
        logger.info("DPI change: %d -> %d", uk.Scale.dpi, dpi)
        uk.Scale.apply(self.root, dpi)
        self._fit_window()
        uk.round_window_corners(self.root)

    # ------------------------------------------------------------------ hotkeys

    def _register_hotkeys(self) -> None:
        """Through `RegisterHotKey` first (survives standby and long pauses); only what fails there goes through
        the hook of the `keyboard` library."""
        entries = (
            (CFG.hotkey_transcript, self.toggle_recording, "transcript"),
            (CFG.hotkey_prompt, self.toggle_recording_generate, "prompt"),
        )
        failed = self._system_hotkeys.register(
            [(combo, lambda h=handler: self.ui_call(h)) for combo, handler, _ in entries]
        )
        for combo, handler, label in entries:
            if combo not in failed:
                logger.info("Hotkey registered: %s (%s, System)", combo, label)
                continue
            try:
                keyboard.add_hotkey(combo, lambda h=handler: self.ui_call(h))
                logger.info("Hotkey registered: %s (%s)", combo, label)
            except (ValueError, KeyError) as exc:
                logger.error("Hotkey '%s' invalid (%s): %s", combo, label, exc)
                self.ui_call(
                    lambda c=combo, key=label: self.apply_state(
                        "error",
                        t("error.shortcut_unavailable"),
                        t("error.shortcut_invalid", combo=c, name=t(f"shortcut.{key}"), path=CONFIG_PATH),
                    )
                )
            except Exception:  # noqa: BLE001 - reported in the window
                logger.exception("Hotkey '%s' registration failed", combo)
                self.ui_call(
                    lambda c=combo: self.apply_state(
                        "error", t("error.shortcut_unavailable"), t("error.shortcut_taken", combo=c)
                    )
                )

    def suspend_hotkeys(self) -> None:
        """Releases both shortcuts, for example while the settings record a new combination.

        Otherwise the old combination would start a dictation while recording it, and would block itself.
        """
        self._system_hotkeys.stop()
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:  # noqa: BLE001 - nothing left to release
            logger.exception("Hotkey unhook failed")

    def reregister_hotkeys(self) -> None:
        """Registers the shortcuts from CFG again and shows them in the main window - without a restart."""
        self.suspend_hotkeys()
        if self._state == "error" and self.status_label.cget("text") == t("error.shortcut_unavailable"):
            # The old message was about the old combination; if the new one fails, the registration says so again.
            self.apply_state("ready" if self.models.model_ready.is_set() else "model_loading")
        self._register_hotkeys()
        for key, _title, combo in self.shortcuts():
            keys = self.shortcut_keys.get(key)
            if keys is not None:
                keys.set_combo(combo)

    def shortcut_available(self, combo: str) -> bool:
        """Whether no other program holds `combo`. Only where the system can check it, else True."""
        probe = getattr(self._system_hotkeys, "available", None)  # the Linux portal cannot ask
        if probe is None:
            return True
        try:
            return bool(probe(combo))
        except Exception:  # noqa: BLE001 - when in doubt, let the user try
            logger.exception("Hotkey probe failed: %s", combo)
            return True

    # ------------------------------------------------------------------ windows

    def _open_settings(self, icon=None, item=None) -> None:
        def _do() -> None:
            win = self.settings_win
            if win is not None and win.alive():
                win.focus()
                return
            if win is None or not win.reusable():
                win = self.settings_win = SettingsWindow(self, show=False)
            win.show()

        self.ui_call(_do)  # also from the tray thread

    #: How long after the start the settings window is built ahead, hidden (`prebuild_settings`).
    PREBUILD_DELAY_MS = 3000

    def prebuild_settings(self) -> None:
        """Builds the settings window hidden, so that opening it later only has to show it (about 20 ms
        instead of 300). Called once in a quiet moment after the start; a recording in progress waits."""
        if self.settings_win is not None or self._shutting_down:
            return
        if self.is_recording:
            self.root.after(self.PREBUILD_DELAY_MS, self.prebuild_settings)
            return
        self.settings_win = SettingsWindow(self, show=False)
        self.settings_win.lay_out_hidden()
        logger.info("Settings window built ahead")

    def _open_setup(self, icon=None, item=None) -> None:
        """The setup assistant: at the first start, from the settings and from the tray."""

        def _do() -> None:
            if self.setup_win is not None and self.setup_win.alive():
                self.setup_win.focus()
                return
            self.setup_win = SetupWindow(self)

        self.ui_call(_do)  # also from the tray thread

    def on_setup_closed(self, completed: bool) -> None:
        """The assistant is closed. If the start held the model back, it is loaded now.

        If the assistant already started a switch or download, that takes care of it. Closed early (×) with the
        speech model still missing, nothing is downloaded behind the user's back: the main window says so and its
        button opens the assistant again.
        """
        self.setup_win = None
        logger.info("Setup closed (%s)", "completed" if completed else "not completed")
        if not self._model_deferred:
            return
        models = self.models
        if models.model_ready.is_set() or models.is_switching or models.downloading is not None:
            self._model_deferred = False
            return
        if not completed and models.needs_download(CFG.model_size):
            logger.info("Setup closed early, the speech model waits for it")
            self.apply_state("setup_pending")
            return
        self._model_deferred = False
        self.apply_state("model_loading")
        self._load_model_async()

    def _download_views(self) -> list:
        """The open windows that show a download: settings and setup assistant."""
        return [win for win in (self.settings_win, self.setup_win) if win is not None and win.alive()]

    # ------------------------------------------------------------------ restart and quit

    def restart_app(self) -> str:
        """Replaces the running process by a fresh one. "" on success, else the reason.

        Why this button exists: Windows can remove a low-level keyboard hook at any time without the application
        noticing. The listening thread of the `keyboard` library keeps turning its `GetMessage` loop, the tray icon
        lives, the window reacts - only the shortcuts never arrive again. Calling `add_hotkey()` again does not
        help: the library remembers in `listening` that it listens and sets no new hook (pinned in
        tests/test_hotkey_recovery.py). Only a new process sets it again.

        The order is deliberate: release the lock, start, then go. If the start fails, the lock is taken back and
        everything stays as it was - better an error message than a computer without a running Hams.
        """
        if self.is_recording:
            return t("restart.recording")
        if self.models.downloading is not None:
            return t("restart.downloading")
        if self.pipeline.pending():
            return t("restart.transcribing")

        self._flush_position()
        if not _release_single_instance_mutex():
            return t("restart.unavailable")

        if sys.platform != "win32" and linux.under_systemd():
            # The service restarts once Hams exits with the agreed code; a successor started here would die with
            # the service stop.
            logger.info("Restart requested through the service")
            linux.restart_requested = True
            self._shut_down()
            return ""

        command = restart_command()
        if not self._window_visible:
            command.append("--background")  # the successor shows itself the way its predecessor was
        if sys.platform == "win32":
            detach = {"creationflags": (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)}
        else:
            detach = {"start_new_session": True}
        # Before the successor starts: it registers the same shortcuts and would fail while they are still held here.
        self.suspend_hotkeys()
        try:
            subprocess.Popen(command, cwd=str(APP_DIR.parent), close_fds=True, **detach)
        except Exception:  # noqa: BLE001 - reported, everything is taken back
            logger.exception("Restart failed: %s", command)
            _acquire_single_instance_mutex()
            self._register_hotkeys()
            return t("restart.failed")

        logger.info("Restart requested: %s", command)
        self._shut_down()
        return ""

    def _quit_app(self, icon=None, item=None) -> None:
        """Tray "Quit": pystray calls this on its own thread, the work belongs to the Tk thread."""
        logger.info("Quit requested")
        self.ui_call(self._quit_on_ui_thread)

    #: At most this long Quit waits for dictations still being transcribed and pasted (s).
    QUIT_DRAIN_SECONDS = 5.0
    QUIT_POLL_MS = 100

    def _quit_on_ui_thread(self) -> None:
        if self._shutting_down:
            return
        pending = self.pipeline.pending()
        if pending:
            now = time.monotonic()
            if self._quit_deadline is None:
                self._quit_deadline = now + self.QUIT_DRAIN_SECONDS
                logger.info("Quit waits for %d dictation(s) still being transcribed", pending)
                self.suspend_hotkeys()  # no new dictation starts while quitting
            if now < self._quit_deadline:
                self.root.after(self.QUIT_POLL_MS, self._quit_on_ui_thread)
                return
            logger.warning("Quit: %d dictation(s) not finished in time were dropped", pending)
        self._flush_position()
        self._shutting_down = True
        with contextlib.suppress(Exception):
            winsound.Beep(440, 200)
        self._shut_down()

    def _shut_down(self) -> None:
        """The common end of Quit and Restart: release, tray icon away, window closed."""
        self._shutting_down = True
        self._release_resources()
        try:
            self.tray_icon.stop()
        except Exception:  # noqa: BLE001 - closing anyway
            logger.exception("Tray stop failed")
        self.root.after(0, self.root.destroy)
        self._ui_dispatcher.close()  # late callbacks from background threads are dropped

    def _release_resources(self) -> None:
        """Stream, hotkeys and model."""
        self._stop_elapsed_ticker()
        self._cancel_done_reset()
        self.recorder.close()
        self.suspend_hotkeys()
        self.models.close()  # a load still running is discarded too; an unfinished download resumes next time

    # ------------------------------------------------------------------ states

    #: State -> (status dot, text key, button role, button key, enabled)
    STATE_KEYS = {
        # Enabled although the model is still loading: recording does not need it (see `Recorder`), only the
        # transcription afterwards. The status line names the state, the button the possible action.
        "model_loading": ("idle", "status.model_loading", "primary", "button.start_recording", True),
        # No speech model and the assistant was closed before: the button opens it again.
        "setup_pending": ("idle", "status.setup_pending", "primary", "button.open_setup", True),
        "ready": ("ready", None, "primary", "button.start_recording", True),
        # While recording, stopping is the only sensible action - hence a filled surface in the colour of the
        # state: red for dictation, purple for the prompt mode. The white stop symbol says it without colour.
        "recording": ("recording", "status.recording", "recording", "button.stop", True),
        "recording_prompt": ("ai", "status.recording_prompt", "ai", "button.stop", True),
        # While processing, the button keeps naming the action and is disabled; the status line says what runs.
        "transcribing": ("processing", "status.transcribing", "secondary", "button.start_recording", False),
        "cleaning": ("processing", "status.cleaning", "secondary", "button.start_recording", False),
        "generating": ("ai", "status.generating", "secondary", "button.start_recording", False),
        "done": ("ready", "status.done", "primary", "button.start_recording", True),
        "error": ("error", None, "secondary", "button.retry", True),
        "no_signal": ("warning", "status.no_signal", "primary", "button.start_recording", True),
    }

    #: The same table with the texts of the active language (translated on access).
    STATE_TABLE = Text(
        lambda: {
            state: (dot, t(text) if text else None, role, t(button), enabled)
            for state, (dot, text, role, button, enabled) in FloatingTranscriberApp.STATE_KEYS.items()
        }
    )

    RECORDING_STATES = ("recording", "recording_prompt")
    #: How long "Pasted" stays before the window shows "Ready" again (ms).
    DONE_MS = 3000
    HINT_DONE_MS = 8000

    def apply_state(
        self, state: str, detail: str | None = None, hint: str | None = None, *, calm: bool = False
    ) -> None:
        """The one place where the main window's interface changes.

        The button shows the *action*, the status line the *state* - never both the same.

        Args:
            state: a key of `STATE_TABLE`.
            detail: replaces the default text of the status line.
            hint: footer text; without one the footer shows the shortcuts.
            calm: the hint is information, not an error (muted text instead of error red).
        """
        dot, text, role, button_text, enabled = self.STATE_TABLE[state]
        self._state = state

        self.status_dot.set_status(dot)
        if detail is not None:
            label = detail
        elif text is not None:
            label = text
        elif state == "ready":
            model_id = self.models.active_model_size or CFG.model_size  # the loaded one, not only the chosen one
            info = whisper_models.find(model_id)
            label = t("status.ready", model=info.label if info else model_id)
        else:
            label = ""
        self.status_label.config(text=label)

        self.main_button.set_role(role)
        self.main_button.config(text=button_text)
        self.main_button.set_enabled(enabled)
        # No colour of its own: the symbol takes the text colour of the surface.
        self.main_button.set_icon("stop" if state in self.RECORDING_STATES else None)

        if state not in self.RECORDING_STATES:
            self.elapsed_label.config(text="")
            self.level_meter.set_level(0.0)

        self._show_footer_hint(hint, calm=calm)
        self._set_tray_icon(self._tray_colour(state))
        cursor_dot.notify(state)
        self._cancel_done_reset()
        if state == "done":
            # A hint wants to be read; without one a short glance is enough.
            self._done_job = self.root.after(self.HINT_DONE_MS if hint else self.DONE_MS, self._back_to_ready)

    @staticmethod
    def _tray_colour(state: str) -> str:
        """The tray icon's state colour - from `TRAY_PALETTE`, not from `Theme`.

        The meaning is the same as in the window (red recording, purple AI), the tones deliberately are not: the
        icon sits on the Windows taskbar and follows its theme, not Hams'.
        """
        if state in ("recording",):
            return TRAY_PALETTE["RECORDING"]
        if state in ("recording_prompt", "generating"):
            return TRAY_PALETTE["AI"]
        if state == "error":
            return TRAY_PALETTE["ERROR"]
        return TRAY_PALETTE["ACCENT_TINT"]

    def _cancel_done_reset(self) -> None:
        if self._done_job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.root.after_cancel(self._done_job)
            self._done_job = None

    def _back_to_ready(self) -> None:
        """The success state falls back by itself instead of staying."""
        self._done_job = None
        if not self.is_recording and self.models.model_ready.is_set():
            notice, self._notice_after_pipeline = self._notice_after_pipeline, None
            if notice is not None:
                notice()
            else:
                self.apply_state("ready")

    def _on_main_action(self) -> None:
        """The button does what its text promises."""
        if self._state == "error":
            self._retry()
        elif self._state == "setup_pending":
            self._open_setup()
        else:
            self.toggle_recording()

    def _retry(self) -> None:
        """Try again: through the one owner of the model, never as a second, parallel load."""
        if self.models.model_error or not self.models.model_ready.is_set():
            self.reload_model()
        else:
            self.apply_state("ready")

    def _beep(self, freq: int, duration: int) -> None:
        if not CFG.sound_feedback:
            return
        threading.Thread(target=winsound.Beep, args=(freq, duration), daemon=True, name="beep").start()

    # ------------------------------------------------------------------ recording

    def toggle_recording(self) -> None:
        """The dictation hotkey: start, or stop and transcribe. Stopping always works, whatever the model does."""
        self._toggle(prompt_mode=False)

    def toggle_recording_generate(self) -> None:
        """The *Ask AI* hotkey: the same, but the AI answers the dictated prompt instead of cleaning it up."""
        self._toggle(prompt_mode=True)

    def _toggle(self, *, prompt_mode: bool) -> None:
        if self.is_recording:
            self.stop_recording_and_transcribe()
            return
        if not self._may_record():
            return
        self._prompt_mode = prompt_mode
        self.start_recording()

    def _may_record(self) -> bool:
        """Whether a recording may start now.

        Not only once the model is loaded: recording never touches it, and the model is needed only afterwards
        (the pipeline waits for it). Measured: warm the model is there after about 2.2 s, cold much later, and the
        average dictation lasts 11.95 s - whoever dictates right after logging on has long finished speaking by
        the time the model is needed.

        Refused is only what cannot go well later either: a model that could not be loaded at all, or none on its
        way. The spoken words would be lost, which is worse than a message beforehand.
        """
        if self.models.failed:
            self._on_model_error()
            return False
        if self._state == "setup_pending":
            self.apply_state("setup_pending")
            return False
        return True

    def start_recording(self) -> None:
        """Opens the microphone and shows the recording; it stops by itself after `max_recording_seconds`."""
        try:
            self.recorder.start(max_seconds=CFG.max_recording_seconds, on_limit=self._on_max_duration_reached)
        except MicrophoneUnavailable:
            logger.exception("Microphone could not be opened")
            device = CFG.mic_device_raw
            self.apply_state("error", t("error.mic_unavailable"), t("error.mic_not_responding", device=device))
            return
        except Exception:  # noqa: BLE001 - reported in the window
            logger.exception("Unexpected error on start_recording")
            self.apply_state("error", t("error.recording_not_started"), t("hint.see_log"))
            return
        self.apply_state("recording_prompt" if self._prompt_mode else "recording")
        self._beep(880, 80)
        self._tick_elapsed()
        self._level_tick()
        self._update_tray_tooltip()
        logger.info("Recording started (prompt_mode=%s)", self._prompt_mode)

    def _on_max_duration_reached(self) -> None:
        logger.warning("Max recording duration (%ss) reached - auto-stopping", CFG.max_recording_seconds)
        self.ui_call(self._auto_stop_recording)

    def _auto_stop_recording(self) -> None:
        if self.is_recording:
            self.stop_recording_and_transcribe()

    def stop_recording_and_transcribe(self) -> None:
        """Stops the recording and queues it for the pipeline - unless nothing usable came in."""
        duration = self.recorder.elapsed
        audio = self.recorder.stop()
        # From here on, an older transcription still running no longer changes the main window.
        run_id = self.pipeline.next_run()
        self._stop_elapsed_ticker()
        self._stop_level_ticker()
        notice, self._pending_model_notice = self._pending_model_notice, None
        self._update_tray_tooltip()
        self._beep(220, 120)
        self.apply_state("transcribing")
        logger.info("Recording stopped after %.1fs", duration)
        prompt_mode, self._prompt_mode = self._prompt_mode, False

        if audio is None:
            self.apply_state("no_signal", t("status.no_recording"), t("hint.no_audio"))
            self._deliver_model_notice(notice, queued=False)
            return
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        # The level in the log makes a muted microphone visible at once.
        logger.info("Recording captured: %.1fs, peak=%.4f", duration, peak)
        if peak < SILENCE_PEAK_THRESHOLD:
            logger.warning("No microphone signal (peak=%.4f) - microphone muted or the wrong device?", peak)
            self.apply_state("no_signal", None, t("hint.mic_silent", device=CFG.mic_device_raw))
            self._deliver_model_notice(notice, queued=False)
            return
        # Mode and run id travel with the recording: the next one may start before this one is through.
        self.pipeline.enqueue(audio, prompt_mode, run_id)
        self._deliver_model_notice(notice, queued=True)

    def _deliver_model_notice(self, notice, *, queued: bool) -> None:
        """A model message held back during the recording (see `_model_notice`).

        Without a queued dictation it is shown right away. With one, a missing model is reported by that dictation
        itself; any other message (a fallback) follows once the dictation is through.
        """
        if notice is None:
            return
        if not queued:
            notice()
        elif not self.models.failed:
            self._notice_after_pipeline = notice

    def _tick_elapsed(self) -> None:
        if not self.is_recording:
            return
        seconds = int(self.recorder.elapsed)
        self.elapsed_label.config(text=f"{seconds // 60:d}:{seconds % 60:02d}")
        self._elapsed_job = self.root.after(500, self._tick_elapsed)

    def _stop_elapsed_ticker(self) -> None:
        if self._elapsed_job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.root.after_cancel(self._elapsed_job)
            self._elapsed_job = None

    def _level_tick(self) -> None:
        if not self.is_recording:
            return
        level = _display_level(self.recorder.level)
        self.level_meter.set_level(level)
        cursor_dot.notify(self._state, level)
        self._level_tick_job = self.root.after(50, self._level_tick)

    def _stop_level_ticker(self) -> None:
        if self._level_tick_job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.root.after_cancel(self._level_tick_job)
            self._level_tick_job = None
        self.level_meter.set_level(0.0)

    # ------------------------------------------------------------------ model (ModelEvents and the UI around it)

    def _load_model_async(self) -> None:
        """Loads the configured model (`CFG.model_size`): at start, on "Try again" and after the assistant."""
        problem = self.models.load_or_download(CFG.model_size)
        if problem:
            self._on_model_download_failed(CFG.model_size, problem)
        else:
            self._show_model_download()

    def reload_model(self) -> bool:
        """The "Try again" button: load the configured model again unless a load is under way.

        Returns:
            False if a load is already running; that one reports its own result.
        """
        self.apply_state("model_loading")
        if self.models.is_switching:
            return False
        self.models.model_error = None
        self._load_model_async()
        return True

    def switch_model(self, model_id: str, *, force: bool = False) -> bool:
        """Starts a switch. False if it is not possible now.

        `force` loads the active model again - on the GPU, say, after the setup assistant fetched the CUDA
        libraries. A load under way (the first one at start included) is superseded by this request.
        """
        models = self.models
        if model_id == models.active_model_size and models.model_ready.is_set() and not force:
            return False
        if self.is_recording:
            state = "recording_prompt" if self._prompt_mode else "recording"
            self.apply_state(state, None, t("hint.no_switch_while_recording"))
            return False
        if model_id == models.switching_to and not force:
            return True  # already on its way
        models.request(model_id, persist=True)
        info = whisper_models.find(model_id)
        self.apply_state("model_loading", t("status.model_loading_named", model=info.label if info else model_id))
        return True

    def _show_model_download(self) -> None:
        """The main window's status line during a download the app is waiting for: model and percentage."""
        model_id = self.models.load_after_download
        if model_id is None or self._state != "model_loading":
            return  # a recording or an error comes first; the next progress shows up again
        info = whisper_models.find(model_id)
        label = info.label if info else model_id
        self.status_label.config(text=t("status.model_downloading", model=label, percent=self.models.download_percent))

    def _on_model_download_failed(self, model_id: str, error: str) -> None:
        """The download of the model the app waits for failed or was cancelled. "Try again" restarts it."""
        info = whisper_models.find(model_id)
        self.models.model_error = error
        if error == whisper_models.CANCELLED:
            error = t("status.model_download_cancelled")
        title = t("error.model_download_failed", model=info.label if info else model_id)
        self._model_notice(lambda: self.apply_state("error", title, error))

    def _model_notice(self, show) -> None:
        """Shows a model message now, or after the recording that is running: never over the recording.

        Stopping must keep working whatever the model does meanwhile, and the main window keeps saying
        *Recording* until then (see `_deliver_model_notice`).
        """
        if self.is_recording:
            self._pending_model_notice = show
            return
        show()

    def model_loaded(self, model_id: str, ok: bool, requested: str) -> None:
        """ModelEvents, on the Tk thread. A newer load started meanwhile reports itself."""
        fallback = ok and requested != model_id
        if not self.models.is_switching:
            if fallback:
                self._model_notice(lambda: self._on_model_fallback(requested, model_id))
            elif ok:
                logger.info("Model ready for UI (%s/%s)", self.models.active_device, self.models.active_compute_type)
                self._pending_model_notice = None  # an older error notice no longer applies
                if not self.is_recording:
                    self.apply_state("ready")
                self._update_tray_tooltip()
            else:
                self._model_notice(self._on_model_error)
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.on_model_changed()

    def _on_model_fallback(self, requested: str, active: str) -> None:
        """The requested model couldn't be loaded; the previous one is still in use."""
        logger.warning("Model %s could not be loaded, still using %s", requested, active)
        wanted, current = whisper_models.find(requested), whisper_models.find(active)
        self.apply_state(
            "ready",
            t("status.model_fallback", model=wanted.label if wanted else requested),
            t("hint.model_fallback", active=current.label if current else active),
        )
        self._update_tray_tooltip()

    def _on_model_error(self) -> None:
        self.apply_state(
            "error", t("error.model_load_failed"), t("error.model_load_failed_detail", model=CFG.model_size)
        )

    def download_progress(self) -> None:
        """ModelEvents, on the Tk thread."""
        models = self.models
        self._show_model_download()
        for view in self._download_views():
            view.on_download_progress(models.download_done, models.download_total, models.download_percent)

    def download_finished(self, model_id: str, error: str) -> None:
        """ModelEvents, on the Tk thread: on success the model is used right away.

        The switch belongs here and not to the settings window: whoever downloads a model wants to use it, also when
        the window was closed meanwhile.
        """
        models = self.models
        awaited = models.take_awaited_download(model_id)
        if error:
            switching = False
            if awaited:
                self._on_model_download_failed(model_id, error)
        elif awaited:
            # No model is loaded yet, so there is nothing a recording could lose: load right away. A recording that
            # is running waits for it; the main window keeps showing the recording.
            switching = True
            models.request(model_id, persist=False)
            if not self.is_recording:
                info = whisper_models.find(model_id)
                label = info.label if info else model_id
                self.apply_state("model_loading", t("status.model_loading_named", model=label))
        elif models.download_superseded:
            logger.info("Download of %s finished after a newer model request, not switching", model_id)
            switching = False
        else:
            switching = self.switch_model(model_id)
        for view in self._download_views():
            view.on_download_finished(model_id, error, switching)

    # ------------------------------------------------------------------ update notice

    def _on_update_found(self, release, first: bool) -> None:
        self._refresh_tray()
        if first:
            self._notify_update(release)
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.show_update(release)

    def _notify_update(self, release) -> None:
        """A calm system message; a tray without them (Linux/AppIndicator) leaves the menu and the settings."""
        icon = self.tray_icon
        if icon is None or not getattr(icon, "HAS_NOTIFICATION", False):  # pystray backends differ
            return
        try:
            icon.notify(
                t("update.notify_body"),
                t("update.notify_title", product=branding.PRODUCT_NAME, version=release.version),
            )
        except Exception:  # noqa: BLE001 - only a message
            logger.exception("Update notification failed")
