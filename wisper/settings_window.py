"""Das Einstellungsfenster: Aufbau und Schalter."""

import tkinter as tk
from typing import TYPE_CHECKING, Optional


import sounddevice as sd

from wisper import branding
from wisper import ui_kit as uk
from wisper.ui_kit import Theme
if TYPE_CHECKING:
    from wisper.app import FloatingTranscriberApp
from wisper.config import CFG, _persist_config_value, logger
from wisper.settings_models import ModelSectionMixin
from wisper.settings_status import StatusSectionMixin


class SettingsWindow(ModelSectionMixin, StatusSectionMixin):
    """Randloses Einstellungsfenster im Mosaik-Design.

    Zeigt Mikrofon-Auswahl mit Live-Pegel, Status-Lampen (Modell/Ollama/Mikro)
    und Schnell-Schalter. Öffnet einen eigenen Monitor-Stream, solange es offen
    ist und nicht aufgenommen wird, damit man den Pegel *vor* dem Sprechen sieht.
    """

    WIDTH = 440                 # logisch
    HEIGHT = 680                # logisch
    OLLAMA_INTERVAL_MS = 5000   # sparsam, aber nicht nur einmal
    #: Breite der Controls, die *neben* dem Text stehen (Pegel, Darstellung,
    #: Loeschauswahl). Auswahlfelder mit langen Werten stehen gestapelt darunter.
    CONTROL_WIDTH = 160         # logisch
    MODEL_HINT = "Tempo und Genauigkeit"
    APPEARANCE_LABELS = {Theme.LIGHT: "Hell", Theme.DARK: "Dunkel"}
    #: Die Beschreibung bricht um, sie darf also ein ganzer Satz sein.
    RESTART_HINT = "Wenn Kurzbefehle nicht reagieren"
    RESTART_WIDTH = 108         # logisch; „Neu starten" plus Luft
    DELETE_PLACEHOLDER = "Löschen …"
    DELETE_WIDTH = 88           # logisch; „Löschen" plus Luft
    #: So lange bleibt die Erfolgsmeldung stehen, bevor die Zeile wieder den
    #: Bestand zeigt.
    DELETE_NOTE_MS = 4000

    def __init__(self, app: "FloatingTranscriberApp") -> None:
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title(f"{branding.PRODUCT_NAME} — Einstellungen")
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=Theme.WINDOW_BG)
        try:
            px, py = app.root.winfo_rootx(), app.root.winfo_rooty()
        except Exception:
            px, py = 80, 80
        self._logical_size = (self.WIDTH, self.HEIGHT)
        self.win.geometry(
            f"{uk.px(self.WIDTH)}x{uk.px(self.HEIGHT)}"
            f"+{px + uk.px(24)}+{py + uk.px(24)}"
        )
        self.win.bind("<Escape>", lambda _e: self.close())

        self._mon_stream: Optional[sd.InputStream] = None
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
        self._ollama_job: Optional[str] = None
        self._installed_models: set = set()
        #: Modell, dessen Download dieses Fenster gerade zeigt.
        self._downloading = ""
        #: Modell, fuer das die Loeschruckfrage gerade offen steht.
        self._delete_target = ""
        self._storage_note_job: Optional[str] = None
        self._tick_job: Optional[str] = None
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
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)

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
        except tk.TclError:      # pragma: no cover - Fenster verschwindet gerade
            pass

    def close(self) -> None:
        self._alive = False
        uk.Scale.unregister(self._on_scale_change)
        if self._ollama_job is not None:
            try:
                self.win.after_cancel(self._ollama_job)
            except (ValueError, tk.TclError):
                pass
            self._ollama_job = None
        if self._tick_job is not None:
            try:
                self.win.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
        if self._storage_note_job is not None:
            try:
                self.win.after_cancel(self._storage_note_job)
            except Exception:
                pass
            self._storage_note_job = None
        self._stop_monitor()
        self.app.unregister_mic_consumer(self)
        try:
            self.win.destroy()
        except Exception:
            pass
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
        head.pack(fill="x", padx=uk.px(uk.Space.WINDOW),
                  pady=(uk.px(uk.Space.WINDOW), 0))

        title = tk.Label(head, text="Einstellungen", bg=Theme.WINDOW_BG,
                         fg=Theme.TEXT_PRIMARY, font=uk.font("window_title"), anchor="w")
        title.pack(side="left")
        for widget in (head, title):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

        self._close_btn = uk.IconButton(head, "close", self.close,
                                        bg_under=Theme.WINDOW_BG,
                                        tooltip="Einstellungen schließen")
        self._close_btn.set_hover_colour(Theme.ERROR)
        self._close_btn.pack(side="right")

        # Trennlinie erscheint erst, wenn Inhalt darunter wegscrollt.
        self._head_line = tk.Frame(self.win, bg=Theme.WINDOW_BG, height=max(1, uk.px(1)))
        self._head_line.pack(fill="x", pady=(uk.px(uk.Space.LG), 0))

        self.scroll = uk.ScrollArea(self.win, bg_under=Theme.WINDOW_BG,
                                    on_scroll=self._on_scroll)
        self.scroll.pack(fill="both", expand=True,
                         padx=(uk.px(uk.Space.WINDOW), 0),
                         pady=(0, uk.px(uk.Space.WINDOW)))
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

    def _group(self, master: tk.Misc, title: str, *, first: bool = False) -> uk.GroupSurface:
        """Abschnittsüberschrift plus zugehörige Gruppenfläche.

        Die Überschrift steht bündig mit dem Zeilentext darunter (Gruppeneinzug
        plus Zeilenabstand), nicht mit der Gruppenkante. Rechts endet die Gruppe
        um die Scroll-Rinne früher, damit beide Fensterränder gleich breit sind.
        """
        text_x = uk.Space.GROUP_INSET + uk.Space.ROW_X
        right = uk.Space.WINDOW - uk.ScrollArea.gutter_width()
        top = uk.Space.MD if first else uk.Space.SECTION_GAP
        uk.SectionHeader(master, title, bg_under=Theme.WINDOW_BG).pack(
            fill="x", padx=(uk.px(text_x), uk.px(right)),
            pady=(uk.px(top), uk.px(uk.Space.GROUP_GAP)))
        group = uk.GroupSurface(master, bg_under=Theme.WINDOW_BG)
        group.pack(fill="x", padx=(0, uk.px(right)))
        return group

    def _build_recording(self, master: tk.Misc) -> None:
        group = self._group(master, "Aufnahme", first=True)

        # Geraetenamen sind lang; das Feld steht deshalb in voller Breite unter
        # Titel und Status.
        self.row_mic = uk.SettingRow(group.body, "Mikrofon", variant="status",
                                     stacked=True)
        self.row_mic.pack(fill="x")
        self.mic_select = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE,
            on_change=self._select_device, placeholder="Kein Mikrofon gefunden")
        self.row_mic.set_control(self.mic_select)

        self.row_level = uk.SettingRow(group.body, "Eingangspegel",
                                       variant="simple", hairline=False)
        self.row_level.pack(fill="x")
        self.meter = uk.SignalMeter(group.body, bg_under=Theme.GROUP_SURFACE)
        uk.keep_logical_width(self.meter, self.CONTROL_WIDTH)
        self.row_level.set_control(self.meter)

        self._refresh_devices()

    def _build_transcription(self, master: tk.Misc) -> None:
        group = self._group(master, "Transkription")

        self.row_model = uk.SettingRow(
            group.body, "Whisper-Modell", variant="description",
            description=self.MODEL_HINT, stacked=True)
        self.row_model.pack(fill="x")
        self.model_select = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE,
            on_change=self._select_model)
        self.row_model.set_control(self.model_select)
        # Waehrend eines Downloads tritt dieses Kreuz an die Stelle des
        # Auswahlfeldes; es wird erst dann eingehaengt. Bewusst nur ein Icon:
        # eine beschriftete Schaltflaeche nimmt der Fortschrittszeile die
        # Breite, und die Zeile ist die eigentliche Rueckmeldung.
        self.cancel_button = uk.IconButton(
            group.body, "close", self._cancel_download,
            bg_under=Theme.GROUP_SURFACE, tooltip="Download abbrechen")
        self.cancel_button.set_hover_colour(Theme.ERROR)
        self._refresh_models()

        # Eine eigene Zeile fuer den Bestand auf der Platte. Bewusst nicht als
        # Papierkorb neben dem Auswahlfeld: das zeigt immer das *eingestellte*
        # Modell, und genau das darf man nicht loeschen. Eine Loeschung braucht
        # deshalb eine eigene Auswahl — und die erscheint nur, wenn es etwas zu
        # loeschen gibt.
        self.row_storage = uk.SettingRow(
            group.body, "Gespeicherte Modelle", variant="description",
            description="—")
        self.row_storage.pack(fill="x")
        self.select_delete = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE,
            on_change=self._choose_delete, placeholder=self.DELETE_PLACEHOLDER)
        uk.keep_logical_width(self.select_delete, self.CONTROL_WIDTH)
        self.row_storage.set_control(self.select_delete)

        # Die Rueckfrage tritt an die Stelle des Auswahlfeldes — derselbe
        # Kniff wie beim Download-Abbruch. Zwei Schaltflaechen nebeneinander
        # brauchen einen Rahmen, weil eine Zeile nur ein Control aufnimmt.
        self._delete_box = tk.Frame(group.body, bg=Theme.GROUP_SURFACE)
        self._delete_box.focus_ring = True      # die Knoepfe darin tragen ihn
        self.delete_button = uk.SecondaryButton(
            self._delete_box, "Löschen", self._confirm_delete,
            bg_under=Theme.GROUP_SURFACE, role="recording",
            height=uk.Height.STANDARD)
        uk.keep_logical_width(self.delete_button, self.DELETE_WIDTH)
        self.delete_button.pack(side="left")
        self.delete_cancel = uk.IconButton(
            self._delete_box, "close", self._abort_delete,
            bg_under=Theme.GROUP_SURFACE, tooltip="Löschen abbrechen")
        self.delete_cancel.pack(side="left", padx=(uk.px(uk.Space.XS), 0))
        self._refresh_storage()

        self.row_hardware = uk.SettingRow(group.body, "Hardware", variant="display",
                                          value="—", hairline=False)
        self.row_hardware.pack(fill="x")

    def _build_ai(self, master: tk.Misc) -> None:
        group = self._group(master, "KI")

        self.row_cleanup = uk.SettingRow(
            group.body, "Text bereinigen", variant="description",
            description="Glättet das Transkript nach jeder Aufnahme",
            interactive=True, command=self._toggle_cleanup)
        self.row_cleanup.pack(fill="x")
        self.switch_cleanup = uk.Switch(group.body, bg_under=Theme.GROUP_SURFACE,
                                        value=CFG.ollama_cleanup_enabled,
                                        command=self._on_cleanup)
        self.row_cleanup.set_control(self.switch_cleanup)

        # Zwei getrennte Auswahlfelder, kein gemeinsames "KI-Modell": Cleanup
        # laeuft oft mit einem kleinen schnellen Modell, der Prompt-Modus mit
        # einem groesseren. Gleiches Bedienelement, getrennte Werte.
        self.row_cleanup_model = uk.SettingRow(
            group.body, "Modell zum Bereinigen", variant="status", stacked=True)
        self.row_cleanup_model.pack(fill="x")
        self.select_cleanup_model = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE,
            value=CFG.ollama_model_cleanup, on_change=self._select_cleanup_model,
            placeholder="Kein Modell")
        self.row_cleanup_model.set_control(self.select_cleanup_model)

        self.row_prompt_model = uk.SettingRow(
            group.body, "Modell für Prompts", variant="description",
            description="Beantwortet gesprochene Prompts",
            hairline=False, stacked=True)
        self.row_prompt_model.pack(fill="x")
        self.select_prompt_model = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE,
            value=CFG.ollama_model_prompt, on_change=self._select_prompt_model,
            placeholder="Kein Modell")
        self.row_prompt_model.set_control(self.select_prompt_model)
        self._refresh_ollama_models()
        self._show_ollama_status()

    def _build_shortcuts(self, master: tk.Misc) -> None:
        """Beide Kurzbefehle als Tasten — und der Neustart, falls sie ausfallen.

        Vorher standen beide Kombinationen als roher Konfigurationswert
        untereinander in einer Zeile („ctrl+shift+space“). Ohne Beschriftung
        war nicht zu erkennen, welche wofuer ist.
        """
        group = self._group(master, "Kurzbefehle")

        self.shortcut_keys = {}
        for key, title, combo in self.app.shortcuts():
            row = uk.SettingRow(group.body, title)
            row.pack(fill="x")
            keys = uk.KeyCombo(group.body, bg_under=Theme.GROUP_SURFACE, combo=combo)
            row.set_control(keys)
            self.shortcut_keys[key] = keys

        # Steht bewusst direkt unter den Kurzbefehlen: das ist der Fall, in dem
        # man ihn braucht. Windows kann den Tastaturhook entfernen, ohne dass
        # die Anwendung etwas davon mitbekommt — Fenster und Tray-Symbol laufen
        # dann weiter, nur die Tastenkombinationen kommen nicht mehr an. Von
        # innen laesst sich der Hook nicht neu setzen (siehe `restart_app`),
        # ein frischer Prozess dagegen schon.
        self.row_restart = uk.SettingRow(
            group.body, "Neustart", variant="description",
            description=self.RESTART_HINT, hairline=False)
        self.row_restart.pack(fill="x")
        self.restart_button = uk.SecondaryButton(
            group.body, "Neu starten", self._restart_app,
            bg_under=Theme.GROUP_SURFACE, height=uk.Height.STANDARD)
        uk.keep_logical_width(self.restart_button, self.RESTART_WIDTH)
        self.row_restart.set_control(self.restart_button)

    def _build_general(self, master: tk.Misc) -> None:
        group = self._group(master, "Allgemein")

        self.row_sound = uk.SettingRow(
            group.body, "Signalton", variant="description",
            description="Kurzer Ton bei Start und Stopp",
            interactive=True, command=self._toggle_sound)
        self.row_sound.pack(fill="x")
        self.switch_sound = uk.Switch(group.body, bg_under=Theme.GROUP_SURFACE,
                                      value=CFG.sound_feedback,
                                      command=self._on_sound)
        self.row_sound.set_control(self.switch_sound)

        # Zwei Entscheidungen an dieser Zeile:
        #
        # Ein Auswahlfeld, kein Schalter. „Dunkel an/aus" hiesse, Hell sei die
        # Abwesenheit von Dunkel; „Darstellung -> Hell/Dunkel" benennt beide
        # Modi gleichrangig — und laesst spaeter Platz fuer ein drittes
        # „System", ohne die Zeile umzubauen.
        #
        # Einzeilig, ohne Unterzeile: „Hell“ und „Dunkel“ sind kurz, das Feld
        # darf neben dem Titel stehen und sagt selbst, worum es geht.
        self.row_appearance = uk.SettingRow(
            group.body, "Darstellung", hairline=False)
        self.row_appearance.pack(fill="x")
        self.select_appearance = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE,
            options=[uk.Option(Theme.LIGHT, self.APPEARANCE_LABELS[Theme.LIGHT]),
                     uk.Option(Theme.DARK, self.APPEARANCE_LABELS[Theme.DARK])],
            value=Theme.mode, on_change=self._select_appearance)
        uk.keep_logical_width(self.select_appearance, self.CONTROL_WIDTH)
        self.row_appearance.set_control(self.select_appearance)

    # ---------------------------------------------------------- Kopfzeilenlinie

    def _on_scroll(self, offset: int) -> None:
        """Die Linie erscheint erst, wenn etwas darunter liegt."""
        self._head_line.configure(
            bg=Theme.HAIRLINE if offset > 0 else Theme.WINDOW_BG)

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
        self.row_cleanup_model.set_enabled(
            CFG.ollama_cleanup_enabled and self._ollama_online)
        # Der Prompt-Modus haengt nicht am Cleanup-Schalter. Beide gemeinsam
        # abzuschalten waere bequem und falsch: sie sind unabhaengig.
        self.row_prompt_model.set_enabled(self._ollama_online)
        self.row_prompt_model.set_description(
            "Für den Prompt-Modus" if self._ollama_online
            else "Ollama nicht erreichbar")
        if CFG.ollama_cleanup_enabled and not self._ollama_online:
            self.row_cleanup.set_description(
                "Ollama ist nicht erreichbar — Rohtext wird eingefügt")
        else:
            self.row_cleanup.set_description(
                "Bereinigt das Transkript automatisch")

    def _restart_app(self) -> None:
        """Startet Hams neu — oder sagt, warum gerade nicht.

        Kein Rueckfragedialog: der Knopf sitzt hinter einer eigenen Zeile mit
        klarer Beschriftung, und die beiden Faelle, in denen ein Neustart
        wirklich weh taete (laufende Aufnahme, laufender Download), lehnt
        `restart_app` von sich aus ab.
        """
        self.restart_button.set_enabled(False)
        self.row_restart.set_description("Wird neu gestartet …")
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

    def _on_sound(self, value: bool) -> None:
        CFG.sound_feedback = value
        _persist_config_value("ui", "sound_feedback", value)
