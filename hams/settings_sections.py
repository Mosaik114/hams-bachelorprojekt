"""Die Gruppen des Einstellungsfensters: Aufnahme, Transkription, KI, Allgemein (Kurzbefehle: settings_shortcuts.py).

Lebenszyklus, Ziehen und Schalter stehen in settings_window.py, ebenso Konstanten wie CONTROL_WIDTH an der
Klasse SettingsWindow.
"""

import tkinter as tk

from hams import branding, dictation_language, i18n, updates
from hams import ui_kit as uk
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import t
from hams.ui_kit import Theme


def choose_ui_language(setting) -> str | None:
    """Stores a new interface language; returns it, or None if it is the current one. Settings and setup."""
    setting = i18n.normalise_setting(setting)
    if setting == CFG.ui_language:
        return None
    CFG.ui_language = setting
    _persist_config_value("ui", "language", setting)
    return setting


def choose_dictation_language(setting) -> str | None:
    """Stores a new dictation language (valid from the next recording); None if unknown or unchanged."""
    setting = dictation_language.normalise_setting(setting)
    if setting is None or setting == CFG.transcription_language:
        return None
    CFG.transcription_language = setting
    _persist_config_value("transcription", "language", setting)
    logger.info("Dictation language: %s", setting)
    return setting


def language_options() -> list:
    return [uk.Option(value, t(f"language.{value}")) for value in i18n.SETTINGS]


def dictation_options() -> list:
    return [uk.Option(value, t(f"dictation.{value}")) for value in dictation_language.SETTINGS]


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
        group = self._group(master, t("settings.section.recording"), first=True)

        self._build_microphone_rows(group.body, level_hairline=False)

    def _build_transcription(self, master: tk.Misc) -> None:
        group = self._group(master, t("settings.section.transcription"))

        self.row_model = uk.SettingRow(
            group.body, t("settings.model.title"), variant="description", description=self.MODEL_HINT, stacked=True
        )
        self.row_model.pack(fill="x")
        self.model_select = uk.Select(group.body, bg_under=Theme.GROUP_SURFACE, on_change=self._select_model)
        self.row_model.set_control(self.model_select)
        # Waehrend eines Downloads tritt dieses Kreuz an die Stelle des
        # Auswahlfeldes; es wird erst dann eingehaengt. Bewusst nur ein Icon:
        # eine beschriftete Schaltflaeche nimmt der Fortschrittszeile die
        # Breite, und die Zeile ist die eigentliche Rueckmeldung.
        self.cancel_button = uk.IconButton(
            group.body,
            "close",
            self._cancel_download,
            bg_under=Theme.GROUP_SURFACE,
            tooltip=t("settings.download.cancel_tooltip"),
        )
        self.cancel_button.set_hover_colour(Theme.ERROR)
        self._refresh_models()

        self._build_dictation_language(group)

        # Eine eigene Zeile fuer den Bestand auf der Platte. Bewusst nicht als
        # Papierkorb neben dem Auswahlfeld: das zeigt immer das *eingestellte*
        # Modell, und genau das darf man nicht loeschen. Eine Loeschung braucht
        # deshalb eine eigene Auswahl — und die erscheint nur, wenn es etwas zu
        # loeschen gibt.
        self.row_storage = uk.SettingRow(
            group.body, t("settings.storage.title"), variant="description", description="—"
        )
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
            t("settings.storage.delete"),
            self._confirm_delete,
            bg_under=Theme.GROUP_SURFACE,
            role="recording",
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(self.delete_button, self.DELETE_WIDTH)
        self.delete_button.pack(side="left")
        self.delete_cancel = uk.IconButton(
            self._delete_box,
            "close",
            self._abort_delete,
            bg_under=Theme.GROUP_SURFACE,
            tooltip=t("settings.storage.delete_cancel_tooltip"),
        )
        self.delete_cancel.pack(side="left", padx=(uk.px(uk.Space.XS), 0))
        self._refresh_storage()

        self.row_hardware = uk.SettingRow(
            group.body, t("settings.hardware.title"), variant="display", value="—", hairline=False
        )
        self.row_hardware.pack(fill="x")

    def _build_dictation_language(self, group: uk.GroupSurface) -> None:
        """Die Diktiersprache. Gilt ab der naechsten Aufnahme, ohne Neustart: das Modell ist mehrsprachig,
        es aendern sich nur die Sprachangabe an Whisper und die Anweisungen an Ollama."""
        self.row_dictation = uk.SettingRow(
            group.body,
            t("settings.dictation.title"),
            variant="description",
            description=self.dictation_hint(CFG.transcription_language),
        )
        self.row_dictation.pack(fill="x")
        self.select_dictation = uk.Select(
            group.body,
            bg_under=Theme.GROUP_SURFACE,
            options=dictation_options(),
            value=CFG.transcription_language,
            on_change=self._select_dictation_language,
        )
        uk.keep_logical_width(self.select_dictation, self.CONTROL_WIDTH)
        self.row_dictation.set_control(self.select_dictation)

    @staticmethod
    def dictation_hint(setting: str) -> str:
        """Unterzeile der Diktiersprache: bei "auto" der Hinweis, dass jede Aufnahme neu erkannt wird."""
        if setting == dictation_language.AUTO:
            return t("settings.dictation.auto_hint")
        return t("settings.dictation.hint")

    def _build_ai(self, master: tk.Misc) -> None:
        group = self._group(master, t("settings.section.ai"))

        self.row_cleanup = uk.SettingRow(
            group.body,
            t("settings.cleanup.title"),
            variant="description",
            description=t("settings.cleanup.hint"),
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
        self.row_cleanup_model = uk.SettingRow(
            group.body, t("settings.cleanup_model.title"), variant="status", stacked=True
        )
        self.row_cleanup_model.pack(fill="x")
        self.select_cleanup_model = uk.Select(
            group.body,
            bg_under=Theme.GROUP_SURFACE,
            value=CFG.ollama_model_cleanup,
            on_change=self._select_cleanup_model,
            placeholder=t("settings.ollama.no_model"),
        )
        self.row_cleanup_model.set_control(self.select_cleanup_model)
        # Fehlt das eingestellte Modell, laedt es diese Zeile per Knopf nach — ohne Terminal.
        self._build_pull_row(group)

        self.row_prompt_model = uk.SettingRow(
            group.body,
            t("settings.prompt_model.title"),
            variant="description",
            description=t("settings.prompt_model.hint"),
            hairline=False,
            stacked=True,
        )
        self.row_prompt_model.pack(fill="x")
        self.select_prompt_model = uk.Select(
            group.body,
            bg_under=Theme.GROUP_SURFACE,
            value=CFG.ollama_model_prompt,
            on_change=self._select_prompt_model,
            placeholder=t("settings.ollama.no_model"),
        )
        self.row_prompt_model.set_control(self.select_prompt_model)
        self._refresh_ollama_models()
        self._show_ollama_status()

    def _build_general(self, master: tk.Misc) -> None:
        group = self._group(master, t("settings.section.general"))

        self.row_sound = uk.SettingRow(
            group.body,
            t("settings.sound.title"),
            variant="description",
            description=t("settings.sound.hint"),
            interactive=True,
            command=self._toggle_sound,
        )
        self.row_sound.pack(fill="x")
        self.switch_sound = uk.Switch(
            group.body, bg_under=Theme.GROUP_SURFACE, value=CFG.sound_feedback, command=self._on_sound
        )
        self.row_sound.set_control(self.switch_sound)
        self._build_transcripts_row(group)

        # Zwei Entscheidungen an dieser Zeile:
        #
        # Ein Auswahlfeld, kein Schalter. „Dunkel an/aus" hiesse, Hell sei die
        # Abwesenheit von Dunkel; „Darstellung -> Hell/Dunkel" benennt beide
        # Modi gleichrangig — und laesst spaeter Platz fuer ein drittes
        # „System", ohne die Zeile umzubauen.
        #
        # Einzeilig, ohne Unterzeile: „Hell“ und „Dunkel“ sind kurz, das Feld
        # darf neben dem Titel stehen und sagt selbst, worum es geht.
        self.row_appearance = uk.SettingRow(group.body, t("settings.appearance.title"))
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

        # Die Sprache gilt erst nach einem Neustart: jedes offene Fenster neu
        # aufzubauen waere viel Aufwand fuer eine seltene Wahl. Die Unterzeile
        # sagt das, sobald die Wahl von der laufenden Sprache abweicht.
        self.row_language = uk.SettingRow(
            group.body,
            t("settings.language.title"),
            variant="description",
            description=self.language_hint(CFG.ui_language),
        )
        self.row_language.pack(fill="x")
        self.select_language = uk.Select(
            group.body,
            bg_under=Theme.GROUP_SURFACE,
            options=language_options(),
            value=CFG.ui_language,
            on_change=self._select_language,
        )
        uk.keep_logical_width(self.select_language, self.CONTROL_WIDTH)
        self.row_language.set_control(self.select_language)
        self._build_update_rows(group)
        self._build_glossary_row(group)
        self._build_setup_row(group)

    def _build_transcripts_row(self, group: uk.GroupSurface) -> None:
        """Whether each pasted text is also kept as a file (`[output] save_transcripts`), and for how long."""
        self.row_transcripts = uk.SettingRow(
            group.body,
            t("settings.transcripts.title"),
            variant="description",
            description=self.transcripts_hint(CFG.transcripts_keep_days),
            interactive=True,
            command=lambda: self.switch_transcripts.toggle(),
        )
        self.row_transcripts.pack(fill="x")
        self.switch_transcripts = uk.Switch(
            group.body, bg_under=Theme.GROUP_SURFACE, value=CFG.save_transcripts, command=self._on_transcripts
        )
        self.row_transcripts.set_control(self.switch_transcripts)

    @staticmethod
    def transcripts_hint(keep_days: int) -> str:
        if keep_days > 0:
            return t("settings.transcripts.hint_days", days=keep_days)
        return t("settings.transcripts.hint_forever")

    def _on_transcripts(self, value: bool) -> None:
        CFG.save_transcripts = value
        _persist_config_value("output", "save_transcripts", value)

    def _build_update_rows(self, group: uk.GroupSurface) -> None:
        """Der Schalter fuer die taegliche Update-Abfrage; darueber, falls eine aussteht, der Hinweis."""
        self._general_group = group
        self.row_update: uk.SettingRow | None = None
        self.row_updates = uk.SettingRow(
            group.body,
            t("settings.updates.title"),
            variant="description",
            description=t("settings.updates.hint"),
            interactive=True,
            command=lambda: self.switch_updates.toggle(),
        )
        self.row_updates.pack(fill="x")
        self.switch_updates = uk.Switch(
            group.body, bg_under=Theme.GROUP_SURFACE, value=CFG.update_check, command=self._on_updates
        )
        self.row_updates.set_control(self.switch_updates)
        if self.app.available_update is not None:
            self.show_update(self.app.available_update)

    def _on_updates(self, value: bool) -> None:
        """Gilt ab dem naechsten Start: dann fragt Hams wieder (oder nicht mehr)."""
        CFG.update_check = value
        _persist_config_value("updates", "check", value)

    def show_update(self, release) -> None:
        """Ruhiger Hinweis auf eine neue Version: ansehen oder fuer diese Version ausblenden."""
        if self.row_update is not None:
            self.row_update.set_description(self.update_hint(release.version))
            return
        body = self._general_group.body
        self.row_update = uk.SettingRow(
            body, t("settings.update.title"), variant="description", description=self.update_hint(release.version)
        )
        self.row_update.pack(fill="x", before=self.row_updates)
        box = tk.Frame(body, bg=Theme.GROUP_SURFACE)
        box.focus_ring = True  # die Knoepfe darin tragen ihn
        view = uk.SecondaryButton(
            box,
            t("settings.update.open"),
            self.app.open_release_page,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(view, self.UPDATE_WIDTH)
        view.pack(side="left")
        dismiss = uk.IconButton(
            box,
            "close",
            self._dismiss_update,
            bg_under=Theme.GROUP_SURFACE,
            tooltip=t("settings.update.dismiss_tooltip"),
        )
        dismiss.pack(side="left", padx=(uk.px(uk.Space.XS), 0))
        self.row_update.set_control(box)

    @staticmethod
    def update_hint(version: str) -> str:
        return t("settings.update.hint", product=branding.PRODUCT_NAME, version=version, current=updates.app_version())

    def _dismiss_update(self) -> None:
        self.app.dismiss_update()
        if self.row_update is not None:
            self.row_update.destroy()
            self.row_update = None

    def _build_setup_row(self, group: uk.GroupSurface) -> None:
        """Der Einrichtungsassistent, noch einmal: neues Mikrofon, GPU nachgeruestet, Ollama installiert."""
        self.row_setup = uk.SettingRow(
            group.body,
            t("settings.setup.title"),
            variant="description",
            description=t("settings.setup.hint"),
            hairline=False,
        )
        self.row_setup.pack(fill="x")
        self.setup_button = uk.SecondaryButton(
            group.body,
            t("settings.setup.button"),
            self.app._open_setup,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(self.setup_button, self.SETUP_WIDTH)
        self.row_setup.set_control(self.setup_button)

    @staticmethod
    def language_hint(setting: str) -> str:
        """Unterzeile der Sprachzeile: Hinweis auf den Neustart, wenn die Wahl noch nicht gilt."""
        if i18n.resolve_language(setting) != i18n.language():
            return t("settings.language.restart")
        return t("settings.language.hint")
