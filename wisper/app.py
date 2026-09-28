"""Das Hauptfenster von Hams und seine Zustaende."""

import contextlib
import subprocess
import sys
import threading
import tkinter as tk

import numpy as np
import sounddevice as sd

from wisper import branding
from wisper import models as whisper_models
from wisper import ollama as ollama_api
from wisper import ui_kit as uk
from wisper.backend import WhisperModel
from wisper.config import APP_DIR, CFG, CONFIG_PATH, logger
from wisper.instance import _acquire_single_instance_mutex, _release_single_instance_mutex, restart_command
from wisper.main_window import MainWindowMixin
from wisper.recording import RecordingMixin
from wisper.settings_window import SettingsWindow
from wisper.transcription import TranscriptionMixin
from wisper.tray import TRAY_PALETTE, TrayMixin, _notify_cursor_dot
from wisper.ui_kit import Theme
from wisper.whisper_model import WhisperModelMixin
from wisper.window_position import window_position

if sys.platform == "win32":
    import winsound

    import keyboard

    from wisper.system_hotkeys import SystemHotkeys
else:
    # Gleiche Namen, Linux dahinter: der Ablauf unten bleibt fuer beide Systeme derselbe.
    from wisper import linux
    from wisper.linux import keyboard, winsound
    from wisper.portal_hotkeys import PortalSystemHotkeys as SystemHotkeys


class FloatingTranscriberApp(MainWindowMixin, TrayMixin, WhisperModelMixin, RecordingMixin, TranscriptionMixin):
    """Main application class for Wisper.

    Manages the Tkinter UI, system tray, audio recording, transcription,
    and LLM cleanup/prompt generation.
    """

    WINDOW_WIDTH = 360  # logisch; Platz fuer beide Kurzbefehle als Tasten
    WINDOW_MIN_HEIGHT = 190  # logisch; die echte Hoehe kommt aus dem Layout

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._init_window()
        self._init_model_state()
        self._init_runtime_state()

        self._build_ui()
        self._fit_window()
        self._apply_state("model_loading")
        self._load_model_async()
        self._warmup_ollama_async()
        self._register_hotkeys()

        logger.info("Fensterecken: %s", uk.round_window_corners(self.root))
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)
        self._setup_tray()
        self.root.withdraw()
        logger.info("Wisper started")

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
        self.root.overrideredirect(True)  # randlos für das eigene Mosaik-Design
        self._window_size = (uk.px(self.WINDOW_WIDTH), uk.px(self.WINDOW_MIN_HEIGHT))
        self._window_pos = window_position(CFG.window_x, CFG.window_y, *self._window_size, uk.work_areas())
        self._position_job: str | None = None
        self.root.geometry(f"+{self._window_pos[0]}+{self._window_pos[1]}")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.configure(bg=Theme.WINDOW_BG)
        self._apply_window_icon()
        self.settings_win: SettingsWindow | None = None
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
        self._switching_to: str | None = None
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
        """Schedule fn on the Tk main thread. Zentral statt verstreute root.after-Calls."""
        self.root.after(0, fn)

    # ------------------------------------------------------------------ hotkeys

    def _register_hotkeys(self) -> None:
        """Erst ueber `RegisterHotKey` (uebersteht Standby und lange Pausen),
        nur was dort scheitert, ueber den Hook der `keyboard`-Bibliothek."""
        eintraege = (
            (CFG.hotkey_transcript, self.toggle_recording, "Transkript"),
            (CFG.hotkey_prompt, self.toggle_recording_generate, "Prompt"),
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
                    lambda c=combo, bez=label: self._apply_state(
                        "error",
                        "Kurzbefehl nicht verfügbar",
                        f"„{c}“ ({bez}) lässt sich nicht registrieren. Prüfe die Einstellung in {CONFIG_PATH}.",
                    )
                )
            except Exception:
                logger.exception("Hotkey '%s' registration failed", combo)
                self._ui_call(
                    lambda c=combo: self._apply_state(
                        "error",
                        "Kurzbefehl nicht verfügbar",
                        f"„{c}“ konnte nicht registriert werden. Vermutlich belegt ihn ein anderes Programm.",
                    )
                )

    def _open_settings(self, icon=None, item=None) -> None:
        def _do() -> None:
            if self.settings_win is not None and self.settings_win.alive():
                self.settings_win.focus()
                return
            self.settings_win = SettingsWindow(self)

        self.root.after(0, _do)

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
            return "Läuft gerade eine Aufnahme"
        if self.downloading_model is not None:
            return "Ein Download läuft gerade"

        self._flush_position()
        if not _release_single_instance_mutex():
            return "Neustart nicht möglich"

        if sys.platform != "win32" and linux.under_systemd():
            # Der Dienst startet neu, sobald sich Hams mit dem vereinbarten Code
            # beendet; ein selbst gestarteter Nachfolger fiele dem Dienststopp zum Opfer.
            logger.info("Neustart über den Dienst angefordert")
            linux.restart_requested = True
            self._shut_down()
            return ""

        befehl = restart_command()
        if sys.platform == "win32":
            loesen = {"creationflags": (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)}
        else:
            loesen = {"start_new_session": True}
        try:
            subprocess.Popen(befehl, cwd=str(APP_DIR.parent), close_fds=True, **loesen)
        except Exception:
            logger.exception("Neustart fehlgeschlagen: %s", befehl)
            _acquire_single_instance_mutex()  # Zustand zuruecknehmen
            return "Neustart fehlgeschlagen"

        logger.info("Neustart angefordert: %s", befehl)
        self._shut_down()
        return ""

    def _quit_app(self, icon=None, item=None) -> None:
        logger.info("Quit requested")
        self._flush_position()
        self._shutting_down = True
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
        self._system_hotkeys.stop()
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            logger.exception("Hotkey unhook failed")
        self.downloader.cancel()  # unvollstaendige Dateien bleiben, sie setzen fort
        if self.model is not None:
            self.model = None
            self.model_ready.clear()

    # ------------------------------------------------------------------ Zustände

    #: Zustand -> (Statuspunkt, Text, Buttonrolle, Buttontext, bedienbar)
    STATE_TABLE = {
        # Bedienbar, obwohl das Modell noch laedt: die Aufnahme braucht es
        # nicht (siehe `start_recording`), nur die Auswertung danach. Die
        # Statuszeile nennt den Zustand, der Knopf die moegliche Aktion.
        "model_loading": ("idle", "Modell wird geladen …", "primary", "Aufnahme starten", True),
        "ready": ("ready", None, "primary", "Aufnahme starten", True),
        # Waehrend der Aufnahme ist Stoppen die einzige sinnvolle Handlung —
        # deshalb eine gefuellte Flaeche in der Farbe des Zustands: rot fuer
        # Diktat, lila fuer den Prompt-Modus. Das weisse Stop-Symbol davor
        # sagt es zusaetzlich ohne Farbe.
        "recording": ("recording", "Aufnahme läuft", "recording", "Stoppen", True),
        "recording_prompt": ("ai", "Prompt-Modus · Aufnahme", "ai", "Stoppen", True),
        # Waehrend der Verarbeitung nennt der Button weiter die Aktion und ist
        # gesperrt; was gerade laeuft, sagt allein die Statuszeile.
        "transcribing": ("processing", "Transkribiere …", "secondary", "Aufnahme starten", False),
        "cleaning": ("processing", "Bereinige Text …", "secondary", "Aufnahme starten", False),
        "generating": ("ai", "Generiere Antwort …", "secondary", "Aufnahme starten", False),
        "done": ("ready", "Eingefügt", "primary", "Aufnahme starten", True),
        "error": ("error", None, "secondary", "Erneut versuchen", True),
        "no_signal": ("warning", "Kein Mikrofonsignal", "primary", "Aufnahme starten", True),
    }

    RECORDING_STATES = ("recording", "recording_prompt")

    def _apply_state(self, state: str, detail: str | None = None, hint: str | None = None) -> None:
        """Die einzige Stelle, an der sich die Oberfläche des Hauptfensters ändert.

        Der Button zeigt die *Aktion*, die Statuszeile den *Zustand* — nie
        beides dasselbe.

        Args:
            state: Schlüssel aus `STATE_TABLE`.
            detail: Ersetzt den Standardtext der Statuszeile.
            hint: Fußzeilentext; ohne Angabe stehen dort die Kurzbefehle.
        """
        dot, text, role, button_text, enabled = self.STATE_TABLE[state]
        self._state = state

        self.status_dot.set_status(dot)
        if detail is not None:
            label = detail
        elif text is not None:
            label = text
        elif state == "ready":
            info = whisper_models.find(CFG.model_size)
            label = f"Bereit · {info.label if info else CFG.model_size}"
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

        self._show_footer_hint(hint)
        self._set_tray_icon(self._tray_colour(state))
        _notify_cursor_dot(state)
        self._cancel_done_reset()
        if state == "done":
            self._done_job = self.root.after(3000, self._back_to_ready)

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

    def _back_to_ready(self) -> None:
        """Der Erfolgszustand fällt von selbst zurück, statt stehen zu bleiben."""
        self._done_job = None
        if not self.is_recording and self.model_ready.is_set():
            self._apply_state("ready")

    def _on_main_action(self) -> None:
        """Der Button führt aus, was sein Text verspricht."""
        if self._state == "error":
            self._retry()
        else:
            self.toggle_recording()

    def _retry(self) -> None:
        if self.model_error or not self.model_ready.is_set():
            self.model_error = None
            self._apply_state("model_loading")
            self._load_model_async()
        else:
            self._apply_state("ready")

    # ------------------------------------------------------------------ sound feedback

    def _beep(self, freq: int, duration: int) -> None:
        if not CFG.sound_feedback:
            return
        threading.Thread(target=winsound.Beep, args=(freq, duration), daemon=True, name="beep").start()
