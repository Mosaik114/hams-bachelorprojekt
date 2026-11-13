"""Das Hauptfenster von Hams und seine Zustaende."""

import contextlib
import subprocess
import sys
import threading
import time
import tkinter as tk

import numpy as np
import sounddevice as sd

from hams import branding
from hams import models as whisper_models
from hams import ollama as ollama_api
from hams import ui_kit as uk
from hams.backend import WhisperModel
from hams.config import APP_DIR, CFG, CONFIG_PATH, logger
from hams.i18n import Text, t
from hams.instance import _acquire_single_instance_mutex, _release_single_instance_mutex, restart_command
from hams.main_window import MainWindowMixin
from hams.recording import RecordingMixin
from hams.settings_window import SettingsWindow
from hams.setup_window import SetupWindow
from hams.transcription import TranscriptionMixin
from hams.tray import TRAY_PALETTE, TrayMixin, _notify_cursor_dot
from hams.ui_dispatch import UiDispatcher
from hams.ui_kit import Theme
from hams.update_notice import UpdateNoticeMixin
from hams.whisper_model import WhisperModelMixin
from hams.window_position import window_position

if sys.platform == "win32":
    import winsound

    import keyboard

    from hams.system_hotkeys import SystemHotkeys
else:
    # Gleiche Namen, Linux dahinter: der Ablauf unten bleibt fuer beide Systeme derselbe.
    from hams import linux
    from hams.linux import keyboard, winsound
    from hams.portal_hotkeys import PortalSystemHotkeys as SystemHotkeys


class FloatingTranscriberApp(
    MainWindowMixin, TrayMixin, WhisperModelMixin, RecordingMixin, TranscriptionMixin, UpdateNoticeMixin
):
    """Main application class for Hams.

    Manages the Tkinter UI, system tray, audio recording, transcription,
    and LLM cleanup/prompt generation.
    """

    WINDOW_WIDTH = 360  # logisch; Platz fuer beide Kurzbefehle als Tasten
    WINDOW_MIN_HEIGHT = 190  # logisch; die echte Hoehe kommt aus dem Layout

    #: Gives background threads a way onto the Tk thread (see `_ui_call`). Set in `__init__`.
    _ui_dispatcher: UiDispatcher | None = None

    def __init__(self, root: tk.Tk, *, setup: bool = False) -> None:
        self.root = root
        self._ui_dispatcher = UiDispatcher(root)
        self._init_window()
        self._init_model_state()
        self._init_runtime_state()

        self._build_ui()
        self._fit_window()
        self._apply_state("model_loading")
        if setup and CFG.model_size not in whisper_models.installed_whisper_models():
            # faster-whisper luede das fehlende Modell sonst wortlos herunter. Der
            # Assistent laedt es mit Fortschritt; bis dahin sagt der Status, worauf es wartet.
            self._model_deferred = True
            self._apply_state("setup_pending")
        else:
            self._load_model_async()
        self._warmup_ollama_async()
        self._register_hotkeys()

        logger.info("Fensterecken: %s", uk.round_window_corners(self.root))
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)
        self._setup_tray()
        self._start_update_check()
        if sys.platform != "win32":
            linux.on_show_request(lambda: self._ui_call(self._show_window))
        self.root.withdraw()
        logger.info("Hams started")

    def _init_window(self) -> None:
        """Randloses Fenster, Skalierung und Schriften, Lage aus der Einstellung."""
        self.root.title(branding.PRODUCT_NAME)
        # Reihenfolge zählt: erst Skalierung (setzt tk scaling), dann Fonts.
        uk.init_scale(self.root)
        uk.init_fonts(self.root)
        logger.info("Systemanimationen: %s", "an" if uk.init_animations() else "aus")
        logger.info(
            "UI scale: %d dpi, Faktor %.2f, tk scaling %.3f",
            uk.Scale.dpi,
            uk.Scale.factor,
            float(self.root.tk.call("tk", "scaling")),
        )
        uk.frame_window(self.root)  # Windows randlos im Mosaik-Design, Linux mit KDE-Titelleiste
        self._window_size = (uk.px(self.WINDOW_WIDTH), uk.px(self.WINDOW_MIN_HEIGHT))
        self._window_pos = window_position(CFG.window_x, CFG.window_y, *self._window_size, uk.work_areas())
        self._position_job: str | None = None
        self.root.geometry(f"+{self._window_pos[0]}+{self._window_pos[1]}")
        self.root.resizable(False, False)
        self.root.configure(bg=Theme.WINDOW_BG)
        self._apply_window_icon()
        self.settings_win: SettingsWindow | None = None
        self.setup_win: SetupWindow | None = None
        #: True, solange der Start das Modell fuer den Einrichtungsassistenten zurueckhaelt.
        self._model_deferred = False
        # Wer sonst noch das Mikrofon offen hat und es waehrend einer Aufnahme
        # freigeben muss. Klare Lebenszyklus-Schnittstelle statt Zugriff auf
        # private Methoden des Einstellungsfensters.
        self._mic_consumers: list = []

    def _init_model_state(self) -> None:
        """Aufnahme, Modell, Download und Ollama – alles, was zwischen den Fäden geteilt wird."""
        self.is_recording = False
        self.frames: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None

        self.model: WhisperModel | None = None
        #: Welches Modell tatsaechlich geladen ist. CFG.model_size ist dagegen
        #: die *Auswahl* — beides faellt nach einem Wechsel auseinander.
        self.active_model_size: str | None = None
        self.model_ready = threading.Event()
        self.model_error: str | None = None
        #: Kurzer Schutz um self.model und die Begleitfelder.
        self._model_lock = threading.Lock()
        #: Serialisiert Transkription gegen Modellwechsel. Ein Wechsel wartet
        #: damit auf einen laufenden Lauf, statt ein zweites Modell parallel in
        #: den Speicher zu legen.
        self._transcribe_lock = threading.Lock()
        #: Steigt bei jedem Wechselwunsch. Ein spaet zurueckkehrender aelterer
        #: Ladevorgang erkennt daran, dass er nicht mehr gemeint ist.
        self._model_generation = 0
        #: Das Modell, das gerade geladen wird (erster Start, Wiederholung oder Wechsel), sonst None.
        self._switching_to: str | None = None
        #: idle, loading, switching, ready oder failed - siehe `WhisperModelMixin`.
        self._model_phase = "idle"
        #: Der Download gehoert der Anwendung, nicht dem Einstellungsfenster:
        #: er laeuft weiter, wenn das Fenster zwischendurch geschlossen wird.
        #: Letzte Auskunft des Ollama-Dienstes. Sie gehoert der Anwendung, damit
        #: ein neu geoeffnetes Einstellungsfenster nicht faelschlich "nicht
        #: erreichbar" behauptet, bis die erste eigene Abfrage zurueck ist.
        self.ollama_state = ollama_api.OllamaState(False)
        self.ollama_checked = False
        self.downloader = whisper_models.ModelDownloader()
        # Muss vor dem ersten Modellzugriff stehen; der Schalter gilt fuer den
        # ganzen Prozess, also auch fuer das Nachladen beim Start.
        whisper_models.disable_xet_transfer()
        self.download_done = 0
        self.download_total = 0
        #: Der Hub meldet die Gesamtgroesse erst nach und nach; ohne diese
        #: Sperre liefe die Prozentanzeige zwischendurch rueckwaerts.
        self.download_percent = 0
        self._active_compute_type: str | None = None
        self._active_device: str | None = None

    def _init_runtime_state(self) -> None:
        """Zustand der Oberfläche, Zeitgeber, Verlauf und Kurzbefehle."""
        self.drag_start_x = 0
        self.drag_start_y = 0
        self._state = "model_loading"
        self._done_job: str | None = None
        self._elapsed_job: str | None = None

        self._recording_start: float = 0.0
        self._max_duration_timer: threading.Timer | None = None

        self._rms_level: float = 0.0
        self._level_tick_job: str | None = None

        self._history: list[tuple[str, str, str]] = []  # (display-time, preview, full-text)
        self._history_lock = threading.Lock()

        self._window_visible = False
        self._prompt_mode = False
        self._shutting_down = False
        self._system_hotkeys = SystemHotkeys()
        if sys.platform != "win32":
            # Das Geraet frueh anlegen: KWin braucht einen Moment, bis es es kennt.
            threading.Thread(target=linux.KEYBOARD.open, daemon=True, name="uinput").start()

    # ------------------------------------------------------------------ Mikrofon-Teilhabe

    def register_mic_consumer(self, consumer) -> None:
        """Meldet einen Mitbenutzer des Mikrofons an.

        Aufnahme und Pegelmonitor duerfen dasselbe Geraet nie gleichzeitig
        oeffnen; der Konsument wird deshalb vor der Aufnahme angehalten und
        danach wieder freigegeben.
        """
        if consumer not in self._mic_consumers:
            self._mic_consumers.append(consumer)

    def unregister_mic_consumer(self, consumer) -> None:
        if consumer in self._mic_consumers:
            self._mic_consumers.remove(consumer)

    def _suspend_mic_consumers(self) -> None:
        for consumer in list(self._mic_consumers):
            try:
                consumer.suspend_microphone()
            except Exception:
                logger.exception("Mikrofon-Konsument liess sich nicht anhalten")

    def _resume_mic_consumers(self) -> None:
        for consumer in list(self._mic_consumers):
            try:
                consumer.resume_microphone()
            except Exception:
                logger.exception("Mikrofon-Konsument liess sich nicht fortsetzen")

    # ------------------------------------------------------------------ dpi

    def _on_root_configure(self, event: tk.Event) -> None:
        """Erkennt einen Monitorwechsel mit anderer Skalierung.

        Tk kennt `WM_DPICHANGED` nicht, deshalb wird das DPI beim ohnehin
        anfallenden Configure-Ereignis verglichen — kein eigener Timer.
        """
        if event.widget is not self.root:
            return
        dpi = uk.window_dpi(self.root)
        if dpi == uk.Scale.dpi:
            return
        logger.info("DPI-Wechsel: %d -> %d", uk.Scale.dpi, dpi)
        uk.Scale.apply(self.root, dpi)
        self._fit_window()
        uk.round_window_corners(self.root)

    # ------------------------------------------------------------------ ui helpers

    def _ui_call(self, fn) -> None:
        """Runs fn on the Tk thread. The one way for background threads to reach the UI.

        Safe from any thread (tray, hotkeys, downloads, model load, update check): it never
        blocks the caller and drops the call quietly once the app is shutting down.
        """
        dispatcher = self._ui_dispatcher
        if dispatcher is not None:
            dispatcher.call(fn)
            return
        # Ohne __init__ gebaut (Tests mit Attrappen-Root): direkt einreihen.
        with contextlib.suppress(tk.TclError, RuntimeError):
            self.root.after(0, fn)

    # ------------------------------------------------------------------ hotkeys

    def _register_hotkeys(self) -> None:
        """Erst ueber `RegisterHotKey` (uebersteht Standby und lange Pausen),
        nur was dort scheitert, ueber den Hook der `keyboard`-Bibliothek."""
        eintraege = (
            (CFG.hotkey_transcript, self.toggle_recording, "transcript"),
            (CFG.hotkey_prompt, self.toggle_recording_generate, "prompt"),
        )
        gescheitert = self._system_hotkeys.register(
            [(combo, lambda h=handler: self._ui_call(h)) for combo, handler, _ in eintraege]
        )
        for combo, handler, label in eintraege:
            if combo not in gescheitert:
                logger.info("Hotkey registered: %s (%s, System)", combo, label)
                continue
            try:
                keyboard.add_hotkey(combo, lambda h=handler: self._ui_call(h))
                logger.info("Hotkey registered: %s (%s)", combo, label)
            except (ValueError, KeyError) as exc:
                logger.error("Hotkey '%s' invalid (%s): %s", combo, label, exc)
                self._ui_call(
                    lambda c=combo, key=label: self._apply_state(
                        "error",
                        t("error.shortcut_unavailable"),
                        t("error.shortcut_invalid", combo=c, name=t(f"shortcut.{key}"), path=CONFIG_PATH),
                    )
                )
            except Exception:
                logger.exception("Hotkey '%s' registration failed", combo)
                self._ui_call(
                    lambda c=combo: self._apply_state(
                        "error", t("error.shortcut_unavailable"), t("error.shortcut_taken", combo=c)
                    )
                )

    def suspend_hotkeys(self) -> None:
        """Gibt beide Kurzbefehle frei, etwa solange die Einstellungen eine neue Kombination aufnehmen.

        Sonst loeste die alte Kombination beim Aufnehmen ein Diktat aus, und sie belegte sich selbst.
        """
        self._system_hotkeys.stop()
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            logger.exception("Hotkey unhook failed")

    def reregister_hotkeys(self) -> None:
        """Meldet die Kurzbefehle aus CFG neu an und zeigt sie im Hauptfenster - ohne Neustart."""
        self.suspend_hotkeys()
        if self._state == "error" and self.status_label.cget("text") == t("error.shortcut_unavailable"):
            # Die alte Meldung galt der alten Kombination; scheitert die neue, meldet sich die Anmeldung erneut.
            self._apply_state("ready" if self.model_ready.is_set() else "model_loading")
        self._register_hotkeys()
        for key, _title, combo in self.shortcuts():
            keys = self.shortcut_keys.get(key)
            if keys is not None:
                keys.set_combo(combo)

    def shortcut_available(self, combo: str) -> bool:
        """Ob kein anderes Programm `combo` belegt. Nur wo das System es pruefen kann, sonst True."""
        probe = getattr(self._system_hotkeys, "available", None)
        if probe is None:
            return True
        try:
            return bool(probe(combo))
        except Exception:
            logger.exception("Hotkey probe failed: %s", combo)
            return True

    def _open_settings(self, icon=None, item=None) -> None:
        def _do() -> None:
            if self.settings_win is not None and self.settings_win.alive():
                self.settings_win.focus()
                return
            self.settings_win = SettingsWindow(self)

        self._ui_call(_do)  # auch aus dem Tray-Thread

    def _open_setup(self, icon=None, item=None) -> None:
        """Der Einrichtungsassistent: beim ersten Start, aus den Einstellungen und aus dem Tray."""

        def _do() -> None:
            if self.setup_win is not None and self.setup_win.alive():
                self.setup_win.focus()
                return
            self.setup_win = SetupWindow(self)

        self._ui_call(_do)  # auch aus dem Tray-Thread

    def on_setup_closed(self, completed: bool) -> None:
        """Der Assistent ist zu. Hielt der Start das Modell zurueck, wird es jetzt geladen.

        Hat der Assistent schon einen Wechsel oder Download angestossen, kuemmert sich der darum. Closed
        early (×) with the speech model still missing, nothing is downloaded behind the user's back: the
        main window says so and its button opens the assistant again.
        """
        self.setup_win = None
        logger.info("Setup closed (%s)", "completed" if completed else "not completed")
        if not self._model_deferred:
            return
        if self.model_ready.is_set() or self.is_switching_model or self.downloading_model is not None:
            self._model_deferred = False
            return
        if not completed and self.model_needs_download(CFG.model_size):
            logger.info("Setup closed early, the speech model waits for it")
            self._apply_state("setup_pending")
            return
        self._model_deferred = False
        self._apply_state("model_loading")
        self._load_model_async()

    def restart_app(self) -> str:
        """Ersetzt den laufenden Prozess durch einen frischen. "" bei Erfolg.

        Der eigentliche Grund, warum es diesen Knopf gibt: Windows kann einen
        Low-Level-Tastaturhook jederzeit entfernen, ohne dass die Anwendung
        etwas davon merkt. Der Lauschthread der `keyboard`-Bibliothek dreht
        danach weiter seine `GetMessage`-Schleife, das Tray-Symbol lebt, das
        Fenster reagiert — nur die Kurzbefehle kommen nie wieder an. Ein
        erneutes `add_hotkey()` hilft dagegen nachweislich nicht: die
        Bibliothek merkt sich in `listening`, dass sie schon lauscht, und setzt
        keinen neuen Hook. Nur ein neuer Prozess setzt ihn neu.

        Reihenfolge mit Bedacht: erst das Schloss freigeben, dann starten, dann
        selbst gehen. Schlaegt das Starten fehl, wird das Schloss wieder
        genommen und alles bleibt, wie es war — lieber eine Fehlermeldung als
        ein Rechner ohne laufendes Hams.
        """
        if self.is_recording:
            return t("restart.recording")
        if self.downloading_model is not None:
            return t("restart.downloading")
        if self._pending_dictations():
            return t("restart.transcribing")

        self._flush_position()
        if not _release_single_instance_mutex():
            return t("restart.unavailable")

        if sys.platform != "win32" and linux.under_systemd():
            # Der Dienst startet neu, sobald sich Hams mit dem vereinbarten Code
            # beendet; ein selbst gestarteter Nachfolger fiele dem Dienststopp zum Opfer.
            logger.info("Neustart über den Dienst angefordert")
            linux.restart_requested = True
            self._shut_down()
            return ""

        befehl = restart_command()
        if not self._window_visible:
            befehl.append("--background")  # der Nachfolger zeigt sich so, wie der Vorgaenger gerade war
        if sys.platform == "win32":
            loesen = {"creationflags": (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)}
        else:
            loesen = {"start_new_session": True}
        # Vor dem Start des Nachfolgers: er meldet dieselben Kurzbefehle an, solange sie hier noch belegt waeren,
        # scheitert er daran.
        self.suspend_hotkeys()
        try:
            subprocess.Popen(befehl, cwd=str(APP_DIR.parent), close_fds=True, **loesen)
        except Exception:
            logger.exception("Neustart fehlgeschlagen: %s", befehl)
            _acquire_single_instance_mutex()  # Zustand zuruecknehmen
            self._register_hotkeys()
            return t("restart.failed")

        logger.info("Neustart angefordert: %s", befehl)
        self._shut_down()
        return ""

    def _quit_app(self, icon=None, item=None) -> None:
        """Tray "Quit": pystray calls this on its own thread, the work belongs to the Tk thread."""
        logger.info("Quit requested")
        self._ui_call(self._quit_on_ui_thread)

    #: So lange wartet Beenden hoechstens auf Diktate, die noch transkribiert und eingefuegt werden (s).
    QUIT_DRAIN_SECONDS = 5.0
    QUIT_POLL_MS = 100
    _quit_deadline: float | None = None

    def _pending_dictations(self) -> int:
        """Finished recordings that are still being transcribed or wait in the queue for it."""
        with self._pipeline_lock:
            return len(self._pipeline_jobs or ()) + (1 if self._pipeline_job_running else 0)

    def _quit_on_ui_thread(self) -> None:
        if self._shutting_down:
            return
        pending = self._pending_dictations()
        if pending:
            now = time.monotonic()
            if self._quit_deadline is None:
                self._quit_deadline = now + self.QUIT_DRAIN_SECONDS
                logger.info("Quit waits for %d dictation(s) still being transcribed", pending)
                self.suspend_hotkeys()  # beim Beenden beginnt kein neues Diktat mehr
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
        """Gemeinsames Ende von Beenden und Neustart: freigeben, Tray-Symbol weg, Fenster zu."""
        self._shutting_down = True
        self._release_resources()
        try:
            self.tray_icon.stop()
        except Exception:
            logger.exception("Tray stop failed")
        self.root.after(0, self.root.destroy)
        if self._ui_dispatcher is not None:
            self._ui_dispatcher.close()  # spaete Rueckrufe aus Hintergrundthreads fallen weg

    def _release_resources(self) -> None:
        """Stream, Hotkeys und Modell freigeben."""
        self._cancel_max_duration_timer()
        self._stop_elapsed_ticker()
        self._cancel_done_reset()
        if self.stream is not None:
            try:
                self.stream.abort()
                self.stream.close()
            except Exception:
                logger.exception("Stream cleanup failed")
            self.stream = None
        self.suspend_hotkeys()
        self.downloader.cancel()  # unvollstaendige Dateien bleiben, sie setzen fort
        self._retire_model()  # auch ein noch laufender Ladevorgang wird verworfen

    # ------------------------------------------------------------------ Zustände

    #: Zustand -> (Statuspunkt, Textschluessel, Buttonrolle, Buttonschluessel, bedienbar)
    STATE_KEYS = {
        # Bedienbar, obwohl das Modell noch laedt: die Aufnahme braucht es
        # nicht (siehe `start_recording`), nur die Auswertung danach. Die
        # Statuszeile nennt den Zustand, der Knopf die moegliche Aktion.
        "model_loading": ("idle", "status.model_loading", "primary", "button.start_recording", True),
        # Kein Sprachmodell und der Assistent wurde vorher geschlossen: der Knopf oeffnet ihn wieder.
        "setup_pending": ("idle", "status.setup_pending", "primary", "button.open_setup", True),
        "ready": ("ready", None, "primary", "button.start_recording", True),
        # Waehrend der Aufnahme ist Stoppen die einzige sinnvolle Handlung —
        # deshalb eine gefuellte Flaeche in der Farbe des Zustands: rot fuer
        # Diktat, lila fuer den Prompt-Modus. Das weisse Stop-Symbol davor
        # sagt es zusaetzlich ohne Farbe.
        "recording": ("recording", "status.recording", "recording", "button.stop", True),
        "recording_prompt": ("ai", "status.recording_prompt", "ai", "button.stop", True),
        # Waehrend der Verarbeitung nennt der Button weiter die Aktion und ist
        # gesperrt; was gerade laeuft, sagt allein die Statuszeile.
        "transcribing": ("processing", "status.transcribing", "secondary", "button.start_recording", False),
        "cleaning": ("processing", "status.cleaning", "secondary", "button.start_recording", False),
        "generating": ("ai", "status.generating", "secondary", "button.start_recording", False),
        "done": ("ready", "status.done", "primary", "button.start_recording", True),
        "error": ("error", None, "secondary", "button.retry", True),
        "no_signal": ("warning", "status.no_signal", "primary", "button.start_recording", True),
    }

    #: Dieselbe Tabelle mit den Texten der aktiven Sprache (beim Zugriff uebersetzt).
    STATE_TABLE = Text(
        lambda: {
            state: (dot, t(text) if text else None, role, t(button), enabled)
            for state, (dot, text, role, button, enabled) in FloatingTranscriberApp.STATE_KEYS.items()
        }
    )

    RECORDING_STATES = ("recording", "recording_prompt")
    #: So lange steht "Eingefügt", bevor das Fenster wieder "Bereit" zeigt (ms).
    DONE_MS = 3000
    HINT_DONE_MS = 8000

    def _apply_state(
        self, state: str, detail: str | None = None, hint: str | None = None, *, calm: bool = False
    ) -> None:
        """Die einzige Stelle, an der sich die Oberfläche des Hauptfensters ändert.

        Der Button zeigt die *Aktion*, die Statuszeile den *Zustand* — nie
        beides dasselbe.

        Args:
            state: Schlüssel aus `STATE_TABLE`.
            detail: Ersetzt den Standardtext der Statuszeile.
            hint: Fußzeilentext; ohne Angabe stehen dort die Kurzbefehle.
            calm: Der Hinweis ist eine Auskunft, kein Fehler (gedeckte Schrift statt Fehlerrot).
        """
        dot, text, role, button_text, enabled = self.STATE_TABLE[state]
        self._state = state

        self.status_dot.set_status(dot)
        if detail is not None:
            label = detail
        elif text is not None:
            label = text
        elif state == "ready":
            model_id = self.active_model_size or CFG.model_size  # das geladene, nicht nur das gewaehlte
            info = whisper_models.find(model_id)
            label = t("status.ready", model=info.label if info else model_id)
        else:
            label = ""
        self.status_label.config(text=label)

        self.main_button.set_role(role)
        self.main_button.config(text=button_text)
        self.main_button.set_enabled(enabled)
        # Ohne eigene Farbe: das Symbol nimmt die Schriftfarbe der Flaeche an.
        self.main_button.set_icon("stop" if state in self.RECORDING_STATES else None)

        if state not in self.RECORDING_STATES:
            self.elapsed_label.config(text="")
            self.level_meter.set_level(0.0)

        self._show_footer_hint(hint, calm=calm)
        self._set_tray_icon(self._tray_colour(state))
        _notify_cursor_dot(state)
        self._cancel_done_reset()
        if state == "done":
            # Ein Hinweis will gelesen werden; ohne ihn genuegt ein kurzer Blick.
            self._done_job = self.root.after(self.HINT_DONE_MS if hint else self.DONE_MS, self._back_to_ready)

    @staticmethod
    def _tray_colour(state: str) -> str:
        """Zustandsfarbe des Tray-Symbols — aus `TRAY_PALETTE`, nicht aus `Theme`.

        Die Bedeutung ist dieselbe wie im Fenster (rot Aufnahme, lila KI), die
        Toene sind es bewusst nicht: das Symbol sitzt auf der Windows-Leiste
        und folgt deren Theme, nicht dem von Hams.
        """
        if state in ("recording",):
            return TRAY_PALETTE["RECORDING"]
        if state in ("recording_prompt", "generating"):
            return TRAY_PALETTE["AI"]
        if state == "error":
            return TRAY_PALETTE["ERROR"]
        return TRAY_PALETTE["ACCENT_TINT"]

    def _cancel_done_reset(self) -> None:
        if getattr(self, "_done_job", None) is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.root.after_cancel(self._done_job)
            self._done_job = None

    #: A model message held back by a recording until its dictation was pasted (see `_deliver_model_notice`).
    _notice_after_pipeline = None

    def _back_to_ready(self) -> None:
        """Der Erfolgszustand fällt von selbst zurück, statt stehen zu bleiben."""
        self._done_job = None
        if not self.is_recording and self.model_ready.is_set():
            notice, self._notice_after_pipeline = self._notice_after_pipeline, None
            if notice is not None:
                notice()
            else:
                self._apply_state("ready")

    def _on_main_action(self) -> None:
        """Der Button führt aus, was sein Text verspricht."""
        if self._state == "error":
            self._retry()
        elif self._state == "setup_pending":
            self._open_setup()
        else:
            self.toggle_recording()

    def _retry(self) -> None:
        """Wiederholen: ueber den einen Besitzer des Modells, nie als zweiter, paralleler Ladevorgang."""
        if self.model_error or not self.model_ready.is_set():
            self.reload_model()
        else:
            self._apply_state("ready")

    # ------------------------------------------------------------------ sound feedback

    def _beep(self, freq: int, duration: int) -> None:
        if not CFG.sound_feedback:
            return
        threading.Thread(target=winsound.Beep, args=(freq, duration), daemon=True, name="beep").start()
