"""Der Einrichtungsassistent als Fenster: zeichnet, was `SetupController` entscheidet.

Sechs Seiten (Willkommen, Grafikkarte, Sprachmodell, Mikrofon, Textbereinigung,
Kurzbefehle), gebaut aus denselben Bauteilen wie das Einstellungsfenster. Jede Seite
wird beim Betreten neu aufgebaut; ihr Zustand lebt im Controller, nicht in Widgets.

Hintergrundarbeit (GPU-Download, Ollama-Abfrage und -Download) meldet sich über
`win.after` auf dem Tk-Thread zurück. Der Download des Whisper-Modells gehört der
Anwendung (`ModelManager.start_download`), genau wie aus den Einstellungen; das
Fenster zeigt ihn nur an.

Mikrofonauswahl und Live-Pegel kommen aus `LevelMonitor`, demselben Baustein wie in den Einstellungen.
"""

from __future__ import annotations

import contextlib
import sys
import tkinter as tk
from typing import TYPE_CHECKING

from hams import branding, i18n, setup_wizard
from hams import models as whisper_models
from hams import ollama as ollama_api
from hams import ui_kit as uk
from hams.config import CFG, CONFIG_PATH, logger
from hams.i18n import t
from hams.settings_models import catalog_options
from hams.settings_sections import choose_dictation_language, choose_ui_language, dictation_options, language_options
from hams.settings_status import LevelMonitor
from hams.setup_wizard import Choices, SetupController, Task, task_view
from hams.ui_kit import Theme

if TYPE_CHECKING:
    from hams.app import FloatingTranscriberApp

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"


class TaskRow:
    """Eine Zeile für einen Download: Statuspunkt mit Text, Knopf oder Abbruch, Balken darunter."""

    BUTTON_WIDTH = 132  # logisch; „Erneut versuchen“ plus Luft

    def __init__(self, body: tk.Misc, title: str, *, start, cancel, hairline: bool = False) -> None:
        self.row = uk.SettingRow(body, title, variant="status", hairline=hairline)
        self.row.pack(fill="x")
        self.button = uk.SecondaryButton(
            body, t("setup.download"), start, bg_under=Theme.GROUP_SURFACE, height=uk.Height.STANDARD
        )
        uk.keep_logical_width(self.button, self.BUTTON_WIDTH)
        self.cancel_button = uk.IconButton(
            body, "close", cancel, bg_under=Theme.GROUP_SURFACE, tooltip=t("setup.cancel_tooltip")
        )
        self.cancel_button.set_hover_colour(Theme.ERROR)
        self.bar_box = tk.Frame(body, bg=Theme.GROUP_SURFACE)
        self.bar = uk.ProgressBar(self.bar_box, bg_under=Theme.GROUP_SURFACE)
        self.bar.pack(fill="x", padx=uk.px(uk.Space.ROW_X), pady=(0, uk.px(uk.Space.ROW_Y)))

    def show(self, view: setup_wizard.TaskView) -> None:
        self.row.set_status(view.status, view.text)
        if view.action == "cancel":
            self.row.set_control(self.cancel_button)
        elif view.action is None:
            self.row.set_control(None)
        else:
            self.button.config(text=t("setup.retry" if view.action == "retry" else "setup.download"))
            self.row.set_control(self.button)
        if view.fraction is None:
            self.bar_box.pack_forget()
        else:
            self.bar_box.pack(fill="x", after=self.row)
            self.bar.set_fraction(view.fraction)


class SetupWindow:
    """Randloses Fenster im Mosaik-Design, eine Seite zur Zeit."""

    WIDTH = 460  # logisch
    HEIGHT = 680  # logisch, wie das Einstellungsfenster
    BUTTON_WIDTH = 104  # logisch; „Überspringen“ plus Luft
    CONTROL_WIDTH = 160  # logisch, wie in den Einstellungen
    STATUS_INTERVAL_MS = 500

    def __init__(self, app: FloatingTranscriberApp, services: setup_wizard.SetupServices | None = None) -> None:
        self.app = app
        self.controller = SetupController(services or setup_wizard.default_services(), self._initial_choices())
        self.marker = setup_wizard.marker_path(CONFIG_PATH)
        if setup_wizard.read_marker(self.marker) is None:
            # Ein Abbruch beim ersten Start soll den Assistenten beim nächsten Start wieder zeigen.
            setup_wizard.write_marker(self.marker, False)
        self.model_task = Task()
        self.completed = False
        #: Microphone select and level on the microphone page; runs only while that page is shown.
        self.monitor: LevelMonitor | None = None
        self._status_job: str | None = None
        self._alive = True
        self._drag = (0, 0)
        #: Rows of the pages, built when their page is shown.
        self.gpu_row = None
        self.model_row = None
        self.cleanup_box = None
        self._cleanup_shown = None
        self.pull_row = None
        self.status_row = None

        self.win = tk.Toplevel(app.root)
        uk.frame_window(self.win)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.configure(bg=Theme.WINDOW_BG)
        self.win.geometry(f"{uk.px(self.WIDTH)}x{uk.px(self.HEIGHT)}+{uk.px(120)}+{uk.px(80)}")
        self.controller.detect_hardware()
        self._attach_download()
        self._build()
        uk.Scale.register(self._on_scale_change)
        self.win.update_idletasks()
        uk.round_window_corners(self.win)
        self.focus()

    @staticmethod
    def _initial_choices() -> Choices:
        return Choices(
            ui_language=CFG.ui_language,
            dictation_language=CFG.transcription_language,
            model=CFG.model_size,
            cleanup_model=CFG.ollama_model_cleanup,
            cleanup=CFG.ollama_cleanup_enabled,
        )

    def _attach_download(self) -> None:
        """Zeigt einen Modell-Download, der schon läuft (etwa aus den Einstellungen)."""
        running = self.app.models.downloading
        if running is None:
            return
        self.controller.choices.model = running
        self.controller.model_suggested = True
        self.model_task.begin()
        self.model_task.advance(self.app.models.download_done, self.app.models.download_total)

    # ---------------------------------------------------------- lifecycle

    def alive(self) -> bool:
        try:
            return self._alive and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def focus(self) -> None:
        uk.bring_to_front(self.win)
        self.win.focus_force()

    def retint(self) -> int:
        return uk.apply_theme_tree(self.win)

    def _on_scale_change(self, _factor: float) -> None:
        if not self.alive():
            return
        with contextlib.suppress(tk.TclError):
            self.win.geometry(f"{uk.px(self.WIDTH)}x{uk.px(self.HEIGHT)}")
            uk.round_window_corners(self.win)

    def close(self, *, confirmed: bool = False) -> None:
        """Schließt das Fenster. Ein laufender GPU- oder Ollama-Download bricht ab, der des Modells läuft weiter.

        While the GPU libraries or an Ollama model are downloading, × asks first: the download stops (and
        resumes next time), which nobody should trigger by accident.
        """
        if not self._alive:
            return
        if not confirmed and self._download_running():
            self._ask_close()
            return
        self._alive = False
        self.controller.gpu_task.cancel()
        self.controller.pull_task.cancel()
        uk.Scale.unregister(self._on_scale_change)
        self._cancel_jobs()
        self._leave_microphone()
        with contextlib.suppress(tk.TclError):
            self.win.destroy()
        self.app.on_setup_closed(self.completed)

    def _download_running(self) -> bool:
        return self.controller.gpu_task.running or self.controller.pull_task.running

    def _ask_close(self) -> None:
        """Swaps the footer for the question: close anyway (the download resumes later) or keep open."""
        if self._close_box is not None:
            return
        for button in (self.back_button, self.next_button, self.skip_button):
            button.pack_forget()
        box = tk.Frame(self.footer, bg=Theme.WINDOW_BG)
        box.pack(fill="x")
        tk.Label(
            box,
            text=t("setup.close_confirm"),
            bg=Theme.WINDOW_BG,
            fg=Theme.TEXT_PRIMARY,
            font=uk.font("description"),
            wraplength=self._text_width(),
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(0, uk.px(uk.Space.MD)))
        self.close_anyway_button = uk.SecondaryButton(
            box, t("setup.close_anyway"), lambda: self.close(confirmed=True), bg_under=Theme.WINDOW_BG
        )
        self.close_anyway_button.pack(side="left")
        self.keep_open_button = uk.SecondaryButton(
            box, t("setup.keep_open"), self._keep_open, bg_under=Theme.WINDOW_BG, role="primary"
        )
        self.keep_open_button.pack(side="right")
        self._close_box = box

    def _keep_open(self) -> None:
        """Back from the question to the normal footer."""
        if self._close_box is None:
            return
        self._close_box.destroy()
        self._close_box = None
        self.back_button.pack(side="left")
        self.next_button.pack(side="right", padx=(uk.px(uk.Space.MD), 0))
        self._update_footer()

    def _cancel_jobs(self) -> None:
        for name in ("_status_job",):
            job = getattr(self, name)
            if job is not None:
                with contextlib.suppress(ValueError, tk.TclError):
                    self.win.after_cancel(job)
                setattr(self, name, None)

    def post(self, callback) -> None:
        """Bringt einen Rückruf aus einem Hintergrundthread auf den Tk-Thread und zeichnet danach neu."""

        def run() -> None:
            if self.alive():
                callback()
                self._after_task_change()

        self.app.ui_call(run)  # wartet nie auf den Tk-Thread; nach dem Schliessen faellt es weg

    # ---------------------------------------------------------- drag

    def _start_drag(self, event: tk.Event) -> None:
        self._drag = (event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:
        x = self.win.winfo_x() + event.x - self._drag[0]
        y = self.win.winfo_y() + event.y - self._drag[1]
        self.win.geometry(f"+{x}+{y}")

    # ---------------------------------------------------------- frame

    def _build(self) -> None:
        """Kopf, Seite, Fußzeile. Wird nach einem Sprachwechsel komplett neu gebaut."""
        self._close_box = None
        self.win.title(t("setup.window_title", product=branding.PRODUCT_NAME))
        self._build_head()
        self.footer = tk.Frame(self.win, bg=Theme.WINDOW_BG)
        self.footer.pack(side="bottom", fill="x", padx=uk.px(uk.Space.WINDOW), pady=uk.px(uk.Space.WINDOW))
        self.back_button = self._footer_button(t("setup.back"), self.go_back, "secondary", "left")
        self.next_button = self._footer_button(t("setup.next"), self.go_next, "primary", "right")
        self.skip_button = self._footer_button(t("setup.skip"), self.skip, "secondary", "right")
        self.scroll = uk.ScrollArea(self.win, bg_under=Theme.WINDOW_BG)
        self.scroll.pack(fill="both", expand=True, padx=(uk.px(uk.Space.WINDOW), 0))
        self.scroll.bind_keys(self.win)
        self.show_page()

    def _build_head(self) -> None:
        head = tk.Frame(self.win, bg=Theme.WINDOW_BG)
        head.pack(fill="x", padx=uk.px(uk.Space.WINDOW), pady=(uk.px(uk.Space.WINDOW), 0))
        title = tk.Label(
            head,
            text=t("setup.title", product=branding.PRODUCT_NAME),
            bg=Theme.WINDOW_BG,
            fg=Theme.TEXT_PRIMARY,
            font=uk.font("window_title"),
            anchor="w",
        )
        title.pack(side="left")
        for widget in (head, title) if not uk.system_frame() else ():
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)
        self.close_button = uk.IconButton(
            head, "close", self.close, bg_under=Theme.WINDOW_BG, tooltip=t("setup.close_tooltip")
        )
        self.close_button.set_hover_colour(Theme.ERROR)
        if not uk.system_frame():
            self.close_button.pack(side="right")

        steps = tk.Frame(self.win, bg=Theme.WINDOW_BG)
        steps.pack(fill="x", padx=uk.px(uk.Space.WINDOW), pady=(uk.px(uk.Space.MD), uk.px(uk.Space.LG)))
        self.step_label = tk.Label(
            steps, bg=Theme.WINDOW_BG, fg=Theme.TEXT_TERTIARY, font=uk.font("description"), anchor="w"
        )
        self.step_label.pack(side="left")
        self.step_bar = uk.ProgressBar(steps, bg_under=Theme.WINDOW_BG, height=4)
        uk.keep_logical_width(self.step_bar, 120)
        self.step_bar.pack(side="right")

    def _footer_button(self, text: str, command, role: str, side: str) -> uk.SecondaryButton:
        button = uk.SecondaryButton(
            self.footer, text, command, bg_under=Theme.WINDOW_BG, role=role, height=uk.Height.PRIMARY
        )
        uk.keep_logical_width(button, self.BUTTON_WIDTH)
        button.pack(side=side, padx=(uk.px(uk.Space.MD), 0) if side == "right" else 0)
        return button

    # ---------------------------------------------------------- navigation

    def go_next(self) -> None:
        if not self.controller.can_next(self._model_ready()):
            return
        if self.controller.page == "model":
            self._activate_model()
        if self.controller.is_last:
            self.finish()
            return
        self.controller.next()
        self.show_page()

    def go_back(self) -> None:
        if self.controller.can_back():
            self.controller.back()
            self.show_page()

    def skip(self) -> None:
        if self.controller.can_skip():
            self.controller.skip()
            self.show_page()

    def finish(self) -> None:
        written = self.controller.finish(self.marker)
        for section, key, value in written:
            if (section, key) == ("ollama", "cleanup_enabled"):
                CFG.ollama_cleanup_enabled = value
        self.completed = True
        self.close(confirmed=True)
        self.app._show_window()

    def show_page(self) -> None:
        """Baut die aktuelle Seite neu auf und stellt die Fußzeile ein."""
        self._leave_microphone()
        self._cancel_jobs()
        for child in self.scroll.body.winfo_children():
            child.destroy()
        self.scroll.scroll_to(0)
        page = self.controller.page
        getattr(self, f"_page_{page}")(self.scroll.body)
        self._update_steps()
        self._update_footer()
        logger.info("Setup page: %s", page)

    def _update_steps(self) -> None:
        controller = self.controller
        self.step_label.config(text=t("setup.step", current=controller.index + 1, count=controller.count))
        self.step_bar.set_fraction((controller.index + 1) / controller.count)

    def _update_footer(self) -> None:
        if self._close_box is not None:
            return  # the close question holds the footer until it is answered
        controller = self.controller
        self.back_button.set_enabled(controller.can_back())
        self.next_button.config(text=t("setup.finish" if controller.is_last else "setup.next"))
        self.next_button.set_enabled(controller.can_next(self._model_ready()))
        if controller.page in setup_wizard.SKIPPABLE:
            # innen neben „Weiter“: rechts gepackt steht das zuerst Gepackte ganz aussen
            self.skip_button.pack(side="right", padx=(uk.px(uk.Space.MD), 0), after=self.next_button)
            self.skip_button.set_enabled(controller.can_skip())
        else:
            self.skip_button.pack_forget()

    def _after_task_change(self) -> None:
        """Nach jeder Meldung aus dem Hintergrund: Seite auffrischen, ohne sie neu zu bauen."""
        refresh = getattr(self, f"_refresh_{self.controller.page}", None)
        if refresh is not None:
            refresh()
        self._update_footer()

    # ---------------------------------------------------------- building blocks

    def _text_width(self) -> int:
        return uk.px(self.WIDTH - 2 * uk.Space.WINDOW - uk.ScrollArea.gutter_width() - 8)

    def _heading(self, master: tk.Misc, text: str) -> tk.Label:
        label = tk.Label(
            master, text=text, bg=Theme.WINDOW_BG, fg=Theme.TEXT_PRIMARY, font=uk.font("section"), anchor="w"
        )
        label.pack(fill="x", pady=(0, uk.px(uk.Space.MD)))
        return label

    def _paragraph(self, master: tk.Misc, text: str, *, tertiary: bool = False) -> tk.Label:
        label = tk.Label(
            master,
            text=text,
            bg=Theme.WINDOW_BG,
            fg=Theme.TEXT_TERTIARY if tertiary else Theme.TEXT_SECONDARY,
            font=uk.font("description" if tertiary else "row_title"),
            anchor="w",
            justify="left",
            wraplength=self._text_width(),
        )
        label.pack(fill="x", pady=(0, uk.px(uk.Space.LG)))
        return label

    def _group(self, master: tk.Misc) -> uk.GroupSurface:
        group = uk.GroupSurface(master, bg_under=Theme.WINDOW_BG)
        group.pack(fill="x", padx=(0, uk.px(uk.Space.WINDOW - uk.ScrollArea.gutter_width())), pady=(0, uk.px(16)))
        return group

    def _select_row(self, group, title: str, description: str, options, value, on_change, *, hairline=True):
        row = uk.SettingRow(group.body, title, variant="description", description=description, hairline=hairline)
        row.pack(fill="x")
        select = uk.Select(group.body, bg_under=Theme.GROUP_SURFACE, options=options, value=value, on_change=on_change)
        uk.keep_logical_width(select, self.CONTROL_WIDTH)
        row.set_control(select)
        return row, select

    # ---------------------------------------------------------- page: welcome

    def _page_welcome(self, body: tk.Misc) -> None:
        self._heading(body, t("setup.welcome.title"))
        self._paragraph(body, t("setup.welcome.body", product=branding.PRODUCT_NAME))
        group = self._group(body)
        choices = self.controller.choices
        self._select_row(
            group,
            t("settings.language.title"),
            t("settings.language.hint"),
            language_options(),
            choices.ui_language,
            self._select_ui_language,
        )
        self._select_row(
            group,
            t("settings.dictation.title"),
            t("settings.dictation.hint"),
            dictation_options(),
            choices.dictation_language,
            self._select_dictation_language,
            hairline=False,
        )

    def _select_ui_language(self, setting) -> None:
        """Gilt im Assistenten sofort: er wird in der neuen Sprache neu aufgebaut."""
        setting = choose_ui_language(setting)
        if setting is None:
            return
        self.controller.choices.ui_language = setting
        i18n.set_language(setting)
        for sequence in self.SCROLL_KEYS:
            self.win.unbind(sequence)
        for child in self.win.winfo_children():
            child.destroy()
        self._build()
        self.app._refresh_tray()

    #: Die Tasten, die `ScrollArea.bind_keys` ans Fenster hängt; vor einem Neuaufbau zu lösen.
    SCROLL_KEYS = ("<Up>", "<Down>", "<Prior>", "<Next>", "<Home>", "<End>")

    def _select_dictation_language(self, setting) -> None:
        setting = choose_dictation_language(setting)
        if setting is not None:
            self.controller.choices.dictation_language = setting

    # ---------------------------------------------------------- page: hardware

    def _page_hardware(self, body: tk.Misc) -> None:
        self._heading(body, t("setup.hardware.title"))
        hardware = self.controller.hardware
        size = whisper_models.format_bytes(self.controller.services.gpu_download_bytes)
        self.hardware_text = self._paragraph(
            body, t(f"setup.hardware.{hardware}", product=branding.PRODUCT_NAME, size=size)
        )
        self.gpu_row = None
        if hardware == setup_wizard.GPU_DOWNLOAD or self.controller.gpu_task.state != setup_wizard.IDLE:
            group = self._group(body)
            self.gpu_row = TaskRow(
                group.body, t("setup.gpu.row"), start=self._start_gpu_download, cancel=self.controller.gpu_task.cancel
            )
            self._refresh_hardware()

    def _refresh_hardware(self) -> None:
        if self.gpu_row is None:
            return
        size = whisper_models.format_bytes(self.controller.services.gpu_download_bytes)
        hardware = self.controller.hardware
        self.hardware_text.config(text=t(f"setup.hardware.{hardware}", product=branding.PRODUCT_NAME, size=size))
        view = task_view(self.controller.gpu_task, idle=t("setup.gpu.offer", size=size), done=t("setup.gpu.done"))
        self.gpu_row.show(view)

    def _start_gpu_download(self) -> None:
        controller = self.controller
        controller.start_gpu_download(self._post_gpu)
        self._after_task_change()

    def _post_gpu(self, callback) -> None:
        """Wie `post`, merkt aber den Erfolg: danach gilt die GPU als bereit."""

        def run() -> None:
            callback()
            if self.controller.gpu_task.state == setup_wizard.DONE:
                self.controller.after_gpu_download()

        self.post(run)

    # ---------------------------------------------------------- page: model

    def _page_model(self, body: tk.Misc) -> None:
        self._heading(body, t("setup.model.title"))
        gpu = self.controller.hardware == setup_wizard.GPU_READY
        self._paragraph(body, t("setup.model.gpu" if gpu else "setup.model.cpu"))
        group = self._group(body)
        row = uk.SettingRow(group.body, t("setup.model.choose"), stacked=True)
        row.pack(fill="x")
        self.model_select = uk.Select(
            group.body,
            bg_under=Theme.GROUP_SURFACE,
            options=self._model_options(),
            value=self.controller.choices.model,
            on_change=self._choose_model,
        )
        row.set_control(self.model_select)
        self.model_row = TaskRow(
            group.body, t("setup.model.row"), start=self._start_model_download, cancel=self.app.models.cancel_download
        )
        self._refresh_model()

    def _model_options(self) -> list:
        recommended = setup_wizard.recommended_model(self.controller.hardware)

        def meta(info) -> str:
            return f"{t('setup.model.recommended')} · {info.meta}" if info.model_id == recommended else info.meta

        return catalog_options(self.controller.installed, meta=meta)

    def _choose_model(self, model_id) -> None:
        if self.model_task.running or model_id == self.controller.choices.model:
            return
        self.controller.choices.model = model_id
        self.model_select.set_value(model_id)
        self.model_task = Task()
        self._refresh_model()
        self._update_footer()

    def _model_ready(self) -> bool:
        return self.model_task.running or self.controller.model_installed()

    def _refresh_model(self) -> None:
        if self.model_row is None or not self.model_row.row.winfo_exists():
            return
        info = whisper_models.find(self.controller.choices.model)
        size = info.size_label if info else ""
        if self.controller.model_installed() and not self.model_task.running:
            self.model_task.succeed()
        view = task_view(self.model_task, idle=t("setup.model.missing", size=size), done=t("setup.model.installed"))
        self.model_row.show(view)
        self.model_select.set_enabled(not self.model_task.running)

    def _start_model_download(self) -> None:
        problem = self.app.models.start_download(self.controller.choices.model)
        if problem:
            self.model_task.fail(problem)
        else:
            self.model_task.begin()
        self._after_task_change()

    def on_download_progress(self, done: int, total: int, _percent: int) -> None:
        """Von der Anwendung, auf dem Tk-Thread."""
        if self.alive():
            self.model_task.advance(done, total)
            self._after_task_change()

    def on_download_finished(self, model_id: str, error: str, _switching: bool) -> None:
        """Von der Anwendung, auf dem Tk-Thread. Bei Erfolg lädt die Anwendung das Modell schon."""
        if not self.alive() or model_id != self.controller.choices.model:
            return
        if error == whisper_models.CANCELLED:
            self.model_task.cancelled()
        elif error:
            self.model_task.fail(error)
        else:
            self.controller.refresh_installed()  # sonst bliebe „Weiter“ gesperrt
            self.model_task.succeed()
        self._after_task_change()

    def _activate_model(self) -> None:
        """Beim Verlassen der Modellseite: das gewählte, installierte Modell laden.

        Läuft der Download noch, lädt die Anwendung es selbst, sobald er fertig ist. Rechnete das
        geladene Modell bisher auf der CPU und ist die GPU inzwischen bereit, wird es neu geladen.
        """
        app = self.app
        model = self.controller.choices.model
        if self.model_task.running or not self.controller.model_installed():
            return
        to_gpu = self.controller.hardware == setup_wizard.GPU_READY and app.models.active_device == "cpu"
        if model != app.models.active_model_size or not app.models.model_ready.is_set() or to_gpu:
            app.switch_model(model, force=to_gpu)

    # ---------------------------------------------------------- page: microphone

    def _page_microphone(self, body: tk.Misc) -> None:
        self._heading(body, t("setup.mic.title"))
        self._paragraph(body, t("setup.mic.body", product=branding.PRODUCT_NAME))
        group = self._group(body)
        self.monitor = LevelMonitor(self, group.body, level_hairline=True, on_level=self._note_level)
        self.row_check = uk.SettingRow(group.body, t("setup.mic.check"), variant="status", hairline=False)
        self.row_check.pack(fill="x")
        self._show_mic_check()
        self.monitor.start()

    def _leave_microphone(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None

    def _note_level(self, level: float) -> None:
        was_heard = self.controller.mic_heard
        if self.controller.note_level(level) and not was_heard:
            self._show_mic_check()

    def _show_mic_check(self) -> None:
        if self.controller.mic_heard:
            self.row_check.set_status("ready", t("setup.mic.heard"))
        else:
            self.row_check.set_status("idle", t("setup.mic.waiting"))

    # ---------------------------------------------------------- page: cleanup

    def _page_cleanup(self, body: tk.Misc) -> None:
        self._heading(body, t("setup.cleanup.title"))
        self._paragraph(body, t("setup.cleanup.body", product=branding.PRODUCT_NAME))
        self.cleanup_box = tk.Frame(body, bg=Theme.WINDOW_BG)
        self.cleanup_box.pack(fill="x")
        self._cleanup_shown = None
        self.pull_row = None
        if self.controller.ollama is None:
            self.check_ollama()
        else:
            self._refresh_cleanup()

    def check_ollama(self) -> None:
        """Fragt Ollama im Hintergrund; bis zur Antwort steht dort „wird geprüft“."""
        self.controller.ollama = None
        self._refresh_cleanup()
        setup_wizard.TaskThread(target=self._probe_worker, daemon=True, name="hams-setup-ollama").start()

    def _probe_worker(self) -> None:
        """Thread entry: whatever happens, the page leaves "checking"."""
        state = ollama_api.probe_or_offline(self.controller.services.probe_ollama)
        self.post(lambda: self.controller.set_ollama_state(state))

    def _cleanup_layout(self) -> tuple:
        controller = self.controller
        return (controller.ollama is None, controller.ollama_online, controller.cleanup_model_installed())

    def _refresh_cleanup(self) -> None:
        box = self.cleanup_box
        if box is None or not box.winfo_exists():
            return
        layout = self._cleanup_layout()
        if layout == self._cleanup_shown and self.pull_row is not None:
            self._show_pull()
            return
        self._cleanup_shown = layout
        self.pull_row = None
        for child in box.winfo_children():
            child.destroy()
        group = self._group(box)
        service = uk.SettingRow(group.body, t("setup.cleanup.service"), variant="status")
        service.pack(fill="x")
        controller = self.controller
        if controller.ollama is None:
            service.set_status("idle", t("setup.cleanup.checking"))
        elif not controller.ollama_online:
            self._show_offline(box, group, service)
        else:
            service.set_status("ready", t("setup.cleanup.online"))
            self._show_cleanup_model(group)

    def _show_offline(self, box: tk.Misc, group, service) -> None:
        """Ohne Ollama: ruhig erklären, nichts als Fehler zeigen."""
        service.set_status("idle", t("setup.cleanup.offline"))
        service.set_control(
            self._row_button(
                group.body, t("setup.cleanup.get"), lambda: self.controller.services.open_url(OLLAMA_DOWNLOAD_URL)
            )
        )
        again = uk.SettingRow(group.body, t("setup.cleanup.after_install"), hairline=False)
        again.pack(fill="x")
        again.set_control(self._row_button(group.body, t("setup.cleanup.check_again"), self.check_ollama))
        self._paragraph(box, t("setup.cleanup.offline_body", product=branding.PRODUCT_NAME), tertiary=True)

    def _row_button(self, master: tk.Misc, text: str, command) -> uk.SecondaryButton:
        button = uk.SecondaryButton(master, text, command, bg_under=Theme.GROUP_SURFACE, height=uk.Height.STANDARD)
        uk.keep_logical_width(button, TaskRow.BUTTON_WIDTH)
        return button

    def _show_cleanup_model(self, group) -> None:
        controller = self.controller
        model = controller.choices.cleanup_model
        if controller.cleanup_model_installed():
            row = uk.SettingRow(
                group.body,
                t("settings.cleanup.title"),
                variant="description",
                description=t("setup.cleanup.model_ready", model=model),
                hairline=False,
            )
            row.pack(fill="x")
            switch = uk.Switch(
                group.body,
                bg_under=Theme.GROUP_SURFACE,
                value=controller.choices.cleanup,
                command=controller.set_cleanup,
            )
            row.set_control(switch)
            return
        self.pull_row = TaskRow(
            group.body, t("settings.cleanup_model.title"), start=self._start_pull, cancel=controller.pull_task.cancel
        )
        self._show_pull()

    def _show_pull(self) -> None:
        model = self.controller.choices.cleanup_model
        idle = t("setup.cleanup.model_missing", model=model)
        self.pull_row.show(
            task_view(self.controller.pull_task, idle=idle, done=t("setup.cleanup.model_ready", model=model))
        )

    def _start_pull(self) -> None:
        self.controller.start_pull(self._post_pull)
        self._after_task_change()

    def _post_pull(self, callback) -> None:
        def run() -> None:
            callback()
            if self.controller.pull_task.state == setup_wizard.DONE:
                setup_wizard.TaskThread(target=self._probe_worker, daemon=True, name="hams-setup-ollama").start()

        self.post(run)

    # ---------------------------------------------------------- page: finish

    def _page_finish(self, body: tk.Misc) -> None:
        self._heading(body, t("setup.finish.title"))
        self._paragraph(body, t("setup.finish.body"))
        group = self._group(body)
        for _key, title, combo in self.app.shortcuts():
            row = uk.SettingRow(group.body, title)
            row.pack(fill="x")
            row.set_control(uk.KeyCombo(group.body, bg_under=Theme.GROUP_SURFACE, combo=combo))
        self.status_row = uk.SettingRow(group.body, t("setup.finish.status"), variant="display", hairline=False)
        self.status_row.pack(fill="x")
        combo = "+".join(uk.key_labels(CFG.hotkey_transcript))
        self._paragraph(body, t("setup.finish.try", combo=combo))
        change = "setup.finish.change" if sys.platform == "win32" else "setup.finish.change_linux"
        self._paragraph(body, t(change), tertiary=True)
        self._poll_status()

    def _poll_status(self) -> None:
        """Zeigt die Statuszeile des Hauptfensters, damit der erste Versuch hier sichtbar wird."""
        self._status_job = None
        row = self.status_row
        if not self.alive() or row is None or not row.winfo_exists() or self.controller.page != "finish":
            return
        row.set_value(self.app.status_label.cget("text"))
        self._status_job = self.win.after(self.STATUS_INTERVAL_MS, self._poll_status)
