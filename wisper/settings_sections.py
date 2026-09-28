"""Die Gruppen des Einstellungsfensters: Aufnahme, Transkription, KI, Kurzbefehle, Allgemein.

Ausgelagert aus settings_window.py (Q7), das Lebenszyklus, Ziehen und Schalter behält. Konstanten wie
CONTROL_WIDTH stehen weiter an der Klasse SettingsWindow.
"""

import tkinter as tk

from wisper import ui_kit as uk
from wisper.config import CFG
from wisper.ui_kit import Theme


class SectionsMixin:
    """Baut die Gruppen in den Scrollbereich (braucht die Attribute von SettingsWindow)."""

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
            fill="x", padx=(uk.px(text_x), uk.px(right)), pady=(uk.px(top), uk.px(uk.Space.GROUP_GAP))
        )
        group = uk.GroupSurface(master, bg_under=Theme.WINDOW_BG)
        group.pack(fill="x", padx=(0, uk.px(right)))
        return group

    def _build_recording(self, master: tk.Misc) -> None:
        group = self._group(master, "Aufnahme", first=True)

        # Geraetenamen sind lang; das Feld steht deshalb in voller Breite unter
        # Titel und Status.
        self.row_mic = uk.SettingRow(group.body, "Mikrofon", variant="status", stacked=True)
        self.row_mic.pack(fill="x")
        self.mic_select = uk.Select(
            group.body,
            bg_under=Theme.GROUP_SURFACE,
            on_change=self._select_device,
            placeholder="Kein Mikrofon gefunden",
        )
        self.row_mic.set_control(self.mic_select)

        self.row_level = uk.SettingRow(group.body, "Eingangspegel", variant="simple", hairline=False)
        self.row_level.pack(fill="x")
        self.meter = uk.SignalMeter(group.body, bg_under=Theme.GROUP_SURFACE)
        uk.keep_logical_width(self.meter, self.CONTROL_WIDTH)
        self.row_level.set_control(self.meter)

        self._refresh_devices()

    def _build_transcription(self, master: tk.Misc) -> None:
        group = self._group(master, "Transkription")

        self.row_model = uk.SettingRow(
            group.body, "Whisper-Modell", variant="description", description=self.MODEL_HINT, stacked=True
        )
        self.row_model.pack(fill="x")
        self.model_select = uk.Select(group.body, bg_under=Theme.GROUP_SURFACE, on_change=self._select_model)
        self.row_model.set_control(self.model_select)
        # Waehrend eines Downloads tritt dieses Kreuz an die Stelle des
        # Auswahlfeldes; es wird erst dann eingehaengt. Bewusst nur ein Icon:
        # eine beschriftete Schaltflaeche nimmt der Fortschrittszeile die
        # Breite, und die Zeile ist die eigentliche Rueckmeldung.
        self.cancel_button = uk.IconButton(
            group.body, "close", self._cancel_download, bg_under=Theme.GROUP_SURFACE, tooltip="Download abbrechen"
        )
        self.cancel_button.set_hover_colour(Theme.ERROR)
        self._refresh_models()

        # Eine eigene Zeile fuer den Bestand auf der Platte. Bewusst nicht als
        # Papierkorb neben dem Auswahlfeld: das zeigt immer das *eingestellte*
        # Modell, und genau das darf man nicht loeschen. Eine Loeschung braucht
        # deshalb eine eigene Auswahl — und die erscheint nur, wenn es etwas zu
        # loeschen gibt.
        self.row_storage = uk.SettingRow(group.body, "Gespeicherte Modelle", variant="description", description="—")
        self.row_storage.pack(fill="x")
        self.select_delete = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE, on_change=self._choose_delete, placeholder=self.DELETE_PLACEHOLDER
        )
        uk.keep_logical_width(self.select_delete, self.CONTROL_WIDTH)
        self.row_storage.set_control(self.select_delete)

        # Die Rueckfrage tritt an die Stelle des Auswahlfeldes — derselbe
        # Kniff wie beim Download-Abbruch. Zwei Schaltflaechen nebeneinander
        # brauchen einen Rahmen, weil eine Zeile nur ein Control aufnimmt.
        self._delete_box = tk.Frame(group.body, bg=Theme.GROUP_SURFACE)
        self._delete_box.focus_ring = True  # die Knoepfe darin tragen ihn
        self.delete_button = uk.SecondaryButton(
            self._delete_box,
            "Löschen",
            self._confirm_delete,
            bg_under=Theme.GROUP_SURFACE,
            role="recording",
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(self.delete_button, self.DELETE_WIDTH)
        self.delete_button.pack(side="left")
        self.delete_cancel = uk.IconButton(
            self._delete_box, "close", self._abort_delete, bg_under=Theme.GROUP_SURFACE, tooltip="Löschen abbrechen"
        )
        self.delete_cancel.pack(side="left", padx=(uk.px(uk.Space.XS), 0))
        self._refresh_storage()

        self.row_hardware = uk.SettingRow(group.body, "Hardware", variant="display", value="—", hairline=False)
        self.row_hardware.pack(fill="x")

    def _build_ai(self, master: tk.Misc) -> None:
        group = self._group(master, "KI")

        self.row_cleanup = uk.SettingRow(
            group.body,
            "Text bereinigen",
            variant="description",
            description="Glättet das Transkript nach jeder Aufnahme",
            interactive=True,
            command=self._toggle_cleanup,
        )
        self.row_cleanup.pack(fill="x")
        self.switch_cleanup = uk.Switch(
            group.body, bg_under=Theme.GROUP_SURFACE, value=CFG.ollama_cleanup_enabled, command=self._on_cleanup
        )
        self.row_cleanup.set_control(self.switch_cleanup)

        # Zwei getrennte Auswahlfelder, kein gemeinsames "KI-Modell": Cleanup
        # laeuft oft mit einem kleinen schnellen Modell, der Prompt-Modus mit
        # einem groesseren. Gleiches Bedienelement, getrennte Werte.
        self.row_cleanup_model = uk.SettingRow(group.body, "Modell zum Bereinigen", variant="status", stacked=True)
        self.row_cleanup_model.pack(fill="x")
        self.select_cleanup_model = uk.Select(
            group.body,
            bg_under=Theme.GROUP_SURFACE,
            value=CFG.ollama_model_cleanup,
            on_change=self._select_cleanup_model,
            placeholder="Kein Modell",
        )
        self.row_cleanup_model.set_control(self.select_cleanup_model)

        self.row_prompt_model = uk.SettingRow(
            group.body,
            "Modell für Prompts",
            variant="description",
            description="Beantwortet gesprochene Prompts",
            hairline=False,
            stacked=True,
        )
        self.row_prompt_model.pack(fill="x")
        self.select_prompt_model = uk.Select(
            group.body,
            bg_under=Theme.GROUP_SURFACE,
            value=CFG.ollama_model_prompt,
            on_change=self._select_prompt_model,
            placeholder="Kein Modell",
        )
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
            group.body, "Neustart", variant="description", description=self.RESTART_HINT, hairline=False
        )
        self.row_restart.pack(fill="x")
        self.restart_button = uk.SecondaryButton(
            group.body, "Neu starten", self._restart_app, bg_under=Theme.GROUP_SURFACE, height=uk.Height.STANDARD
        )
        uk.keep_logical_width(self.restart_button, self.RESTART_WIDTH)
        self.row_restart.set_control(self.restart_button)

    def _build_general(self, master: tk.Misc) -> None:
        group = self._group(master, "Allgemein")

        self.row_sound = uk.SettingRow(
            group.body,
            "Signalton",
            variant="description",
            description="Kurzer Ton bei Start und Stopp",
            interactive=True,
            command=self._toggle_sound,
        )
        self.row_sound.pack(fill="x")
        self.switch_sound = uk.Switch(
            group.body, bg_under=Theme.GROUP_SURFACE, value=CFG.sound_feedback, command=self._on_sound
        )
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
        self.row_appearance = uk.SettingRow(group.body, "Darstellung", hairline=False)
        self.row_appearance.pack(fill="x")
        self.select_appearance = uk.Select(
            group.body,
            bg_under=Theme.GROUP_SURFACE,
            options=[
                uk.Option(Theme.LIGHT, self.APPEARANCE_LABELS[Theme.LIGHT]),
                uk.Option(Theme.DARK, self.APPEARANCE_LABELS[Theme.DARK]),
            ],
            value=Theme.mode,
            on_change=self._select_appearance,
        )
        uk.keep_logical_width(self.select_appearance, self.CONTROL_WIDTH)
        self.row_appearance.set_control(self.select_appearance)
