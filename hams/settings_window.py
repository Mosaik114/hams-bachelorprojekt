"""Das Einstellungsfenster: Aufbau und Schalter."""

import tkinter as tk
from typing import TYPE_CHECKING

import sounddevice as sd

from hams import branding, dictation_language, i18n
from hams import ui_kit as uk
from hams.ui_kit import Theme

if TYPE_CHECKING:
    from hams.app import FloatingTranscriberApp
import contextlib

from hams.config import CFG, _persist_config_value, logger
from hams.i18n import Text, t
from hams.settings_models import ModelSectionMixin
from hams.settings_sections import SectionsMixin
from hams.settings_status import StatusSectionMixin


class SettingsWindow(SectionsMixin, ModelSectionMixin, StatusSectionMixin):
    """Randloses Einstellungsfenster im Mosaik-Design.

    Zeigt Mikrofon-Auswahl mit Live-Pegel, Status-Lampen (Modell/Ollama/Mikro)
    und Schnell-Schalter. Öffnet einen eigenen Monitor-Stream, solange es offen
    ist und nicht aufgenommen wird, damit man den Pegel *vor* dem Sprechen sieht.
    """

    WIDTH = 440  # logisch
    HEIGHT = 680  # logisch
    OLLAMA_INTERVAL_MS = 5000  # sparsam, aber nicht nur einmal
    #: Breite der Controls, die *neben* dem Text stehen (Pegel, Darstellung,
    #: Loeschauswahl). Auswahlfelder mit langen Werten stehen gestapelt darunter.
    CONTROL_WIDTH = 160  # logisch
    MODEL_HINT = Text("settings.model.hint")
    APPEARANCE_LABELS = Text(
        lambda: {Theme.LIGHT: t("settings.appearance.light"), Theme.DARK: t("settings.appearance.dark")}
    )
    #: Die Beschreibung bricht um, sie darf also ein ganzer Satz sein.
    RESTART_HINT = Text("settings.restart.hint")
    RESTART_WIDTH = 108  # logisch; „Neu starten" plus Luft
    DELETE_PLACEHOLDER = Text("settings.storage.delete_placeholder")
    DELETE_WIDTH = 88  # logisch; „Löschen" plus Luft
    #: So lange bleibt die Erfolgsmeldung stehen, bevor die Zeile wieder den
    #: Bestand zeigt.
    DELETE_NOTE_MS = 4000

    def __init__(self, app: "FloatingTranscriberApp") -> None:
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title(t("settings.window_title", product=branding.PRODUCT_NAME))
        uk.frame_window(self.win)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.configure(bg=Theme.WINDOW_BG)
        try:
            px, py = app.root.winfo_rootx(), app.root.winfo_rooty()
        except Exception:
            px, py = 80, 80
        self._logical_size = (self.WIDTH, self.HEIGHT)
        self.win.geometry(f"{uk.px(self.WIDTH)}x{uk.px(self.HEIGHT)}+{px + uk.px(24)}+{py + uk.px(24)}")
        self.win.bind("<Escape>", lambda _e: self.close())

        self._mon_stream: sd.InputStream | None = None
        self._mon_rms = 0.0
        self._suspended = False
        self._devices: list = []
        self._device_error = ""
        #: Letzte Antwort des Dienstes, uebernommen von der Anwendung. Die
        #: Signatur daneben entscheidet, ob die Auswahllisten überhaupt neu
        #: gebaut werden müssen.
        self._ollama_state = app.ollama_state
        self._ollama_signature = self._ollama_state.signature()
        self._ollama_online = self._ollama_state.online
        self._ollama_job: str | None = None
        self._installed_models: set = set()
        #: Modell, dessen Download dieses Fenster gerade zeigt.
        self._downloading = ""
        #: Modell, fuer das die Loeschruckfrage gerade offen steht.
        self._delete_target = ""
        self._storage_note_job: str | None = None
        self._tick_job: str | None = None
        self._alive = True
        self._drag = (0, 0)

        self._build()
        self._attach_download()
        uk.Scale.register(self._on_scale_change)
        app.register_mic_consumer(self)
        self.win.update_idletasks()
        self.win.focus_force()
        uk.round_window_corners(self.win)
        self._start_monitor()
        self._tick()
        self._refresh_status()
        self._schedule_ollama_check()

    # ---------------------------------------------------------- lifecycle

    def alive(self) -> bool:
        try:
            return self._alive and bool(self.win.winfo_exists())
        except Exception:
            return False

    def focus(self) -> None:
        uk.bring_to_front(self.win)

    def retint(self) -> int:
        """Faerbt das Einstellungsfenster neu ein — ohne zu springen.

        Die Rollposition wird ausdruecklich gesichert und wieder gesetzt: das
        Neuzeichnen der Gruppenflaechen loest Configure-Ereignisse aus, und die
        koennen den Ausschnitt verschieben. Die Kopfzeilenlinie wird danach neu
        bewertet, weil sie nur bei gescrolltem Inhalt sichtbar ist.
        """
        offset = self.scroll.offset
        painted = uk.apply_theme_tree(self.win)
        self.scroll.scroll_to(offset)
        self._on_scroll(self.scroll.offset)
        return painted

    def _on_scale_change(self, _factor: float) -> None:
        """Fenstergrösse dem neuen DPI nachziehen.

        Die Controls darin skalieren selbst; das Fenster selbst hat niemand
        gefragt und bliebe sonst in der alten Pixelgrösse stehen.
        """
        if not self.alive():
            return
        width, height = self._logical_size
        try:
            self.win.geometry(f"{uk.px(width)}x{uk.px(height)}")
            uk.round_window_corners(self.win)
        except tk.TclError:  # pragma: no cover - Fenster verschwindet gerade
            pass

    def close(self) -> None:
        self._alive = False
        uk.Scale.unregister(self._on_scale_change)
        if self._ollama_job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.win.after_cancel(self._ollama_job)
            self._ollama_job = None
        if self._tick_job is not None:
            with contextlib.suppress(Exception):
                self.win.after_cancel(self._tick_job)
            self._tick_job = None
        if self._storage_note_job is not None:
            with contextlib.suppress(Exception):
                self.win.after_cancel(self._storage_note_job)
            self._storage_note_job = None
        self._stop_monitor()
        self.app.unregister_mic_consumer(self)
        with contextlib.suppress(Exception):
            self.win.destroy()
        self.app.settings_win = None

    # ---------------------------------------------------------- drag

    def _start_drag(self, e: tk.Event) -> None:
        self._drag = (e.x, e.y)

    def _on_drag(self, e: tk.Event) -> None:
        x = self.win.winfo_x() + e.x - self._drag[0]
        y = self.win.winfo_y() + e.y - self._drag[1]
        self.win.geometry(f"+{x}+{y}")

    # ---------------------------------------------------------- build

    def _build(self) -> None:
        """Kopfzeile, Scrollbereich, vier Gruppen.

        Die Gruppen liegen als eine Fläche mit Trennlinien vor, nicht als eine
        Karte je Einstellung — das ist der Unterschied, der das Fenster ruhig
        wirken lässt.
        """
        head = tk.Frame(self.win, bg=Theme.WINDOW_BG)
        head.pack(fill="x", padx=uk.px(uk.Space.WINDOW), pady=(uk.px(uk.Space.WINDOW), 0))

        title = tk.Label(
            head,
            text=t("settings.title"),
            bg=Theme.WINDOW_BG,
            fg=Theme.TEXT_PRIMARY,
            font=uk.font("window_title"),
            anchor="w",
        )
        title.pack(side="left")
        # Ziehen am Kopf nur ohne Systemrahmen; unter Linux zieht man an der KDE-Titelleiste.
        for widget in (head, title) if not uk.system_frame() else ():
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

        self._close_btn = uk.IconButton(
            head, "close", self.close, bg_under=Theme.WINDOW_BG, tooltip=t("settings.close_tooltip")
        )
        self._close_btn.set_hover_colour(Theme.ERROR)
        if not uk.system_frame():  # unter Linux hat die KDE-Titelleiste das Kreuz
            self._close_btn.pack(side="right")

        # Trennlinie erscheint erst, wenn Inhalt darunter wegscrollt.
        self._head_line = tk.Frame(self.win, bg=Theme.WINDOW_BG, height=max(1, uk.px(1)))
        self._head_line.pack(fill="x", pady=(uk.px(uk.Space.LG), 0))

        self.scroll = uk.ScrollArea(self.win, bg_under=Theme.WINDOW_BG, on_scroll=self._on_scroll)
        self.scroll.pack(fill="both", expand=True, padx=(uk.px(uk.Space.WINDOW), 0), pady=(0, uk.px(uk.Space.WINDOW)))
        self.scroll.bind_keys(self.win)

        body = self.scroll.body
        self._build_recording(body)
        self._build_transcription(body)
        self._build_ai(body)
        self._build_shortcuts(body)
        self._build_general(body)

        # Die Tabulatorkette folgt der Stapelreihenfolge, und die entspricht der
        # Reihenfolge der Erzeugung. Die Kopfzeile entsteht zuerst, das Kreuz
        # laege damit *vor* allen Einstellungen. `lift` schiebt die Kopfzeile ans
        # Ende der Kette; sichtbar bleibt sie oben, weil das die Packung regelt.
        head.lift()

    # ---------------------------------------------------------- Kopfzeilenlinie

    def _on_scroll(self, offset: int) -> None:
        """Die Linie erscheint erst, wenn etwas darunter liegt."""
        self._head_line.configure(bg=Theme.HAIRLINE if offset > 0 else Theme.WINDOW_BG)

    # ---------------------------------------------------------- Schalter

    def _toggle_cleanup(self) -> None:
        self.switch_cleanup.toggle()

    def _toggle_sound(self) -> None:
        self.switch_sound.toggle()

    # ---------------------------------------------------------- toggles

    def _on_cleanup(self, value: bool) -> None:
        CFG.ollama_cleanup_enabled = value
        _persist_config_value("ollama", "cleanup_enabled", value)
        self._sync_cleanup_dependency()

    def _sync_cleanup_dependency(self) -> None:
        """Cleanup an, Ollama aus — das muss sofort sichtbar sein.

        Sonst zeigte das Fenster einen aktiven Schalter neben einem Dienst, der
        gar nicht antwortet, und der Widerspruch fiel erst nach der nächsten
        Aufnahme auf.
        """
        if not self.alive():
            return
        self.row_cleanup_model.set_enabled(CFG.ollama_cleanup_enabled and self._ollama_online)
        # Der Prompt-Modus haengt nicht am Cleanup-Schalter. Beide gemeinsam
        # abzuschalten waere bequem und falsch: sie sind unabhaengig.
        self.row_prompt_model.set_enabled(self._ollama_online)
        self.row_prompt_model.set_description(
            t("settings.prompt_model.ready") if self._ollama_online else t("ollama.unreachable")
        )
        if CFG.ollama_cleanup_enabled and not self._ollama_online:
            self.row_cleanup.set_description(t("settings.cleanup.offline"))
        else:
            self.row_cleanup.set_description(t("settings.cleanup.active"))

    def _restart_app(self) -> None:
        """Startet Hams neu — oder sagt, warum gerade nicht.

        Kein Rueckfragedialog: der Knopf sitzt hinter einer eigenen Zeile mit
        klarer Beschriftung, und die beiden Faelle, in denen ein Neustart
        wirklich weh taete (laufende Aufnahme, laufender Download), lehnt
        `restart_app` von sich aus ab.
        """
        self.restart_button.set_enabled(False)
        self.row_restart.set_description(t("settings.restart.running"))
        grund = self.app.restart_app()
        if grund:
            self.restart_button.set_enabled(True)
            self.row_restart.set_description(grund)
            logger.info("Neustart abgelehnt: %s", grund)

    def _select_appearance(self, mode) -> None:
        """Schreibt die Wahl und wendet sie sofort an.

        Erst speichern, dann umschalten: das Umfaerben beruehrt dieses Fenster
        selbst, und eine fehlgeschlagene Schreibaktion soll nicht mitten in
        einem halb neu gezeichneten Baum auffallen.
        """
        mode = Theme.normalise(mode)
        if mode == Theme.mode:
            return
        _persist_config_value("ui", "appearance", mode)
        self.app.set_appearance(mode)

    def _select_language(self, setting) -> None:
        """Speichert die Sprache; sie gilt ab dem naechsten Start (siehe `language_hint`)."""
        setting = i18n.normalise_setting(setting)
        if setting == CFG.ui_language:
            return
        CFG.ui_language = setting
        _persist_config_value("ui", "language", setting)
        self.row_language.set_description(self.language_hint(setting))

    def _select_dictation_language(self, setting) -> None:
        """Speichert die Diktiersprache; sie gilt ab der naechsten Aufnahme, ohne Neustart."""
        setting = dictation_language.normalise_setting(setting)
        if setting is None or setting == CFG.transcription_language:
            return
        CFG.transcription_language = setting
        _persist_config_value("transcription", "language", setting)
        self.row_dictation.set_description(self.dictation_hint(setting))
        logger.info("Dictation language: %s", setting)

    def _on_sound(self, value: bool) -> None:
        CFG.sound_feedback = value
        _persist_config_value("ui", "sound_feedback", value)
