"""The building blocks of the settings window: the `Section` base class, the shared helpers and the group
"General" (sound, transcripts, level at the pointer, appearance, language, updates, glossary, setup assistant).

Every group of the window is a `Section` object that owns its widgets and its state (audit finding 11: before,
six mixins shared undeclared attributes on one window object). A section builds its rows once; `sync()` fills
them from the current state each time the window is shown, `on_hide()` stops whatever the section runs (timers,
streams, a capture). The other sections live in settings_status.py (recording), settings_models.py
(transcription), settings_extras.py (AI), settings_shortcuts.py and settings_about.py.
"""

from __future__ import annotations

import contextlib
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING

from hams import (
    branding,
    dictation_language,
    i18n,
    support,
    transcription,
    update_install,
    update_notice,
    updates,
)
from hams import models as whisper_models
from hams import ui_kit as uk
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import Text, t
from hams.ui_kit import Theme

if TYPE_CHECKING:
    from hams.app import FloatingTranscriberApp
    from hams.settings_window import SettingsWindow

#: Width of the controls that stand *beside* their text (level, appearance, delete choice); selects with long
#: values stand stacked below their title. Logical pixels, like all widths here.
CONTROL_WIDTH = 160


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


def group(master: tk.Misc, title: str, *, first: bool = False) -> uk.GroupSurface:
    """Section header plus its group surface.

    The header is flush with the row text below it (group inset plus row padding), not with the group edge. On
    the right the group ends one scroll gutter earlier, so both window margins are equally wide.
    """
    text_x = uk.Space.GROUP_INSET + uk.Space.ROW_X
    right = uk.Space.WINDOW - uk.ScrollArea.gutter_width() - uk.ScrollArea.EDGE_GAP
    top = uk.Space.MD if first else uk.Space.SECTION_GAP
    # Right to left the header stands flush with the row text on the right.
    pad = (uk.px(right + text_x), uk.px(text_x)) if uk.Writing.rtl else (uk.px(text_x), uk.px(right))
    uk.SectionHeader(master, title, bg_under=Theme.WINDOW_BG).pack(
        fill="x", padx=pad, pady=(uk.px(top), uk.px(uk.Space.GROUP_GAP))
    )
    surface = uk.GroupSurface(master, bg_under=Theme.WINDOW_BG)
    surface.pack(fill="x", padx=(0, uk.px(right)))
    return surface


class Section:
    """One group of the settings window. Subclasses build their rows in `__init__`."""

    def __init__(self, window: SettingsWindow) -> None:
        self.window = window
        self.app: FloatingTranscriberApp = window.app
        #: Timer jobs of this section by name; `on_hide` cancels them all.
        self._jobs: dict[str, str] = {}

    @property
    def win(self) -> tk.Toplevel:
        return self.window.win

    def alive(self) -> bool:
        """Whether the window is shown; a hidden window keeps its widgets but ignores late results."""
        return self.window.alive()

    def sync(self) -> None:
        """Fills the rows from the current state. Runs before the window is shown, every time."""

    def on_show(self) -> None:
        """Starts what runs while the window is visible (after `sync`)."""

    def on_hide(self) -> None:
        """Stops what the section runs; the window is being hidden."""
        self.cancel_jobs()

    def after(self, name: str, delay_ms: int, callback) -> None:
        """A named timer; a second one with the same name replaces the first."""
        self.cancel_job(name)
        self._jobs[name] = self.win.after(delay_ms, lambda: self._run_job(name, callback))

    def _run_job(self, name: str, callback) -> None:
        self._jobs.pop(name, None)
        callback()

    def cancel_job(self, name: str) -> None:
        job = self._jobs.pop(name, None)
        if job is not None:
            with contextlib.suppress(ValueError, tk.TclError):
                self.win.after_cancel(job)

    def cancel_jobs(self) -> None:
        for name in list(self._jobs):
            self.cancel_job(name)

    def pending(self, name: str) -> bool:
        return name in self._jobs


class GeneralSection(Section):
    """Sound, transcripts, level at the pointer, appearance, interface language, updates, glossary, setup assistant."""

    APPEARANCE_LABELS = Text(
        lambda: {Theme.LIGHT: t("settings.appearance.light"), Theme.DARK: t("settings.appearance.dark")}
    )
    GLOSSARY_WIDTH = 124  # logical, like "Erneut starten"
    SETUP_WIDTH = 124  # logical; "Erneut starten" plus air

    def __init__(self, window: SettingsWindow, master: tk.Misc) -> None:
        super().__init__(window)
        self.group = group(master, t("settings.section.general"))
        body = self.group.body

        self.row_sound = uk.SettingRow(
            body,
            t("settings.sound.title"),
            variant="description",
            description=t("settings.sound.hint"),
            interactive=True,
            command=lambda: self.switch_sound.toggle(),
        )
        self.row_sound.pack(fill="x")
        self.switch_sound = uk.Switch(
            body, bg_under=Theme.GROUP_SURFACE, value=CFG.sound_feedback, command=self._on_sound
        )
        self.row_sound.set_control(self.switch_sound)
        self._build_transcripts_row(body)
        self._build_folder_row(body)
        self._build_cursor_level_row(body)

        # A select, not a switch: "dark on/off" would make light the absence of dark; "Appearance -> Light/Dark"
        # names both modes as equals and leaves room for a third "System". Single line, without a description:
        # "Hell" and "Dunkel" are short, the field may stand beside the title.
        self.row_appearance = uk.SettingRow(body, t("settings.appearance.title"))
        self.row_appearance.pack(fill="x")
        self.select_appearance = uk.Select(
            body,
            bg_under=Theme.GROUP_SURFACE,
            options=[
                uk.Option(Theme.LIGHT, self.APPEARANCE_LABELS[Theme.LIGHT]),
                uk.Option(Theme.DARK, self.APPEARANCE_LABELS[Theme.DARK]),
            ],
            value=Theme.mode,
            on_change=self.select_appearance_mode,
        )
        uk.keep_logical_width(self.select_appearance, self.select_appearance.fitting_width(CONTROL_WIDTH))
        self.row_appearance.set_control(self.select_appearance)

        # The language applies after a restart: rebuilding every open window would be a lot of work for a rare
        # choice. The description says so as soon as the choice differs from the running language.
        self.row_language = uk.SettingRow(
            body,
            t("settings.language.title"),
            variant="description",
            description=self.language_hint(CFG.ui_language),
        )
        self.row_language.pack(fill="x")
        self.select_language = uk.Select(
            body,
            bg_under=Theme.GROUP_SURFACE,
            options=language_options(),
            value=CFG.ui_language,
            on_change=self.select_ui_language,
        )
        uk.keep_logical_width(self.select_language, self.select_language.fitting_width(CONTROL_WIDTH))
        self.row_language.set_control(self.select_language)
        self._build_update_rows(body)
        self._build_glossary_row(body)
        self._build_setup_row(body)

    def sync(self) -> None:
        self.switch_sound.set(CFG.sound_feedback, animate=False)
        self.switch_transcripts.set(CFG.save_transcripts, animate=False)
        self.row_transcripts.set_description(self.transcripts_hint(CFG.transcripts_keep_days))
        self.row_folder.set_description(str(transcription.transcripts_folder()))
        self.switch_cursor_level.set(CFG.cursor_level, animate=False)
        self.select_appearance.set_value(Theme.mode)
        self.select_language.set_value(CFG.ui_language)
        self.row_language.set_description(self.language_hint(CFG.ui_language))
        self.switch_updates.set(CFG.update_check, animate=False)
        self.show_update()
        self.row_glossary.set_description(t("settings.glossary.hint"))

    # ---------------------------------------------------------- sound, transcripts

    def _on_sound(self, value: bool) -> None:
        CFG.sound_feedback = value
        _persist_config_value("ui", "sound_feedback", value)

    def _build_transcripts_row(self, body: tk.Misc) -> None:
        """Whether each pasted text is also kept as a file (`[output] save_transcripts`), and for how long."""
        self.row_transcripts = uk.SettingRow(
            body,
            t("settings.transcripts.title"),
            variant="description",
            description=self.transcripts_hint(CFG.transcripts_keep_days),
            interactive=True,
            command=lambda: self.switch_transcripts.toggle(),
        )
        self.row_transcripts.pack(fill="x")
        self.switch_transcripts = uk.Switch(
            body, bg_under=Theme.GROUP_SURFACE, value=CFG.save_transcripts, command=self._on_transcripts
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

    # ---------------------------------------------------------- transcript folder

    #: The folder dialog (Tk's, on Windows the system's folder picker); the tests put their own answer here.
    choose_folder = staticmethod(filedialog.askdirectory)

    def _build_folder_row(self, body: tk.Misc) -> None:
        """Where the transcripts go (`[output] transcripts_dir`): the path as the text, "Change" and "Open" below it."""
        self.row_folder = uk.SettingRow(
            body,
            t("settings.folder.title"),
            variant="description",
            description=str(transcription.transcripts_folder()),
            stacked=True,
        )
        self.row_folder.pack(fill="x")
        box = tk.Frame(body, bg=Theme.GROUP_SURFACE, highlightthickness=0, takefocus=0)
        self.folder_change_button = uk.SecondaryButton(
            box,
            t("settings.folder.change"),
            self.change_transcripts_folder,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        self.folder_open_button = uk.SecondaryButton(
            box,
            t("settings.folder.open"),
            self.open_transcripts_folder,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        self.folder_open_button.pack(side=uk.mirror_side("right"))
        self.folder_change_button.pack(side=uk.mirror_side("right"), padx=uk.mirror_pad((0, uk.px(uk.Space.MD))))
        self.row_folder.set_control(box)

    def change_transcripts_folder(self) -> None:
        """Asks for a folder; only one Hams can write to is stored. The data folder itself is stored as ""."""
        current = transcription.transcripts_folder()
        picked = self.choose_folder(
            parent=self.win, initialdir=str(current), title=t("settings.folder.dialog"), mustexist=False
        )
        if not picked:
            return
        folder = Path(picked)
        if not transcription.usable_transcripts_folder(folder):
            self.row_folder.set_description(t("settings.folder.invalid"))
            return
        value = "" if folder == transcription.OUTPUT_DIR else str(folder)
        CFG.transcripts_dir = value
        _persist_config_value("output", "transcripts_dir", value)
        logger.info("Transcript folder: %s", "default" if not value else "custom")
        self.row_folder.set_description(str(transcription.transcripts_folder()))

    def open_transcripts_folder(self) -> None:
        """Opens the folder in Explorer; creates it first, so there is something to open."""
        folder = transcription.transcripts_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            support.open_path(folder)
        except OSError as exc:
            logger.warning("Transcript folder could not be opened: %s", type(exc).__name__)
            self.row_folder.set_description(t("settings.folder.open_failed", path=folder))

    # ---------------------------------------------------------- level at the mouse pointer

    def _build_cursor_level_row(self, body: tk.Misc) -> None:
        """Whether the level animation beside the mouse pointer is shown while recording (`[ui] cursor_level`)."""
        self.row_cursor_level = uk.SettingRow(
            body,
            t("settings.cursor_level.title"),
            variant="description",
            # Linux has no overlay (see cursor_level.py); the row says so instead of promising one.
            description=t("settings.cursor_level.hint" if sys.platform == "win32" else "settings.cursor_level.linux"),
            interactive=True,
            command=lambda: self.switch_cursor_level.toggle(),
        )
        self.row_cursor_level.pack(fill="x")
        self.switch_cursor_level = uk.Switch(
            body, bg_under=Theme.GROUP_SURFACE, value=CFG.cursor_level, command=self._on_cursor_level
        )
        self.row_cursor_level.set_control(self.switch_cursor_level)

    def _on_cursor_level(self, value: bool) -> None:
        """Applies at once: during a recording the animation appears or disappears right away."""
        CFG.cursor_level = value
        _persist_config_value("ui", "cursor_level", value)
        self.app.cursor_level.refresh()

    # ---------------------------------------------------------- appearance, language

    def select_appearance_mode(self, mode) -> None:
        """Stores the choice and applies it at once.

        Store first, then switch: the recolouring touches this window itself, and a failed write should not
        surface in the middle of a half-repainted tree.
        """
        mode = Theme.normalise(mode)
        if mode == Theme.mode:
            return
        _persist_config_value("ui", "appearance", mode)
        self.app.set_appearance(mode)

    def select_ui_language(self, setting) -> None:
        """Stores the language; it applies from the next start (see `language_hint`)."""
        setting = choose_ui_language(setting)
        if setting is not None:
            self.row_language.set_description(self.language_hint(setting))

    @staticmethod
    def language_hint(setting: str) -> str:
        """Description of the language row: the restart notice while the choice does not apply yet."""
        if i18n.resolve_language(setting) != i18n.language():
            return t("settings.language.restart")
        return t("settings.language.hint")

    # ---------------------------------------------------------- updates

    def _build_update_rows(self, body: tk.Misc) -> None:
        """The switch for the daily check, and below it one row for everything else about updates.

        That row (`row_update`) follows the app's update notice (`app.updates`, see `update_notice.py`): it offers
        "Check for updates now" and says what came out; with a newer version it offers "View" and "Update now",
        then shows the download with its progress, and finally that Hams restarts. Its buttons stand on a line of
        their own under the text (a stacked row), so long labels never squeeze the description.
        """
        self.row_updates = uk.SettingRow(
            body,
            t("settings.updates.title"),
            variant="description",
            description=t("settings.updates.hint"),
            interactive=True,
            command=lambda: self.switch_updates.toggle(),
        )
        self.row_updates.pack(fill="x")
        self.switch_updates = uk.Switch(
            body, bg_under=Theme.GROUP_SURFACE, value=CFG.update_check, command=self._on_updates
        )
        self.row_updates.set_control(self.switch_updates)

        self.row_update = uk.SettingRow(
            body, t("settings.update.check_title"), variant="description", description="", stacked=True
        )
        self.row_update.pack(fill="x")
        box = self.update_box = tk.Frame(body, bg=Theme.GROUP_SURFACE, highlightthickness=0, takefocus=0)
        notice = self.app.updates

        def button(key: str, command) -> uk.SecondaryButton:
            return uk.SecondaryButton(box, t(key), command, bg_under=Theme.GROUP_SURFACE, height=uk.Height.STANDARD)

        def icon_button(tooltip: str, command) -> uk.IconButton:
            return uk.IconButton(box, "close", command, bg_under=Theme.GROUP_SURFACE, tooltip=t(tooltip))

        self.update_check_button = button("settings.update.check_button", notice.check_now)
        self.update_install_button = button("settings.update.install", notice.install)
        self.update_install_button.set_role("primary")
        self.update_view_button = button("settings.update.open", notice.open_release_page)
        self.update_dismiss_button = icon_button("settings.update.dismiss_tooltip", self._dismiss_update)
        self.update_cancel_button = icon_button("settings.update.cancel_tooltip", notice.cancel)
        self.update_progress = uk.ProgressBar(box, bg_under=Theme.GROUP_SURFACE)
        self.row_update.set_control(box)
        self.show_update()

    def _on_updates(self, value: bool) -> None:
        """Applies from the next start: then Hams asks again (or no longer)."""
        CFG.update_check = value
        _persist_config_value("updates", "check", value)

    def update_controls(self) -> list:
        """The controls the update row shows now, from the right edge inwards."""
        notice = self.app.updates
        phase = notice.phase
        if phase == update_notice.DOWNLOADING:
            return [self.update_cancel_button, self.update_progress]
        if phase == update_notice.WAITING:
            return [self.update_cancel_button]
        if phase == update_notice.INSTALLING:
            return []
        if notice.available is None:
            return [self.update_check_button]
        shown = [self.update_dismiss_button]
        if notice.can_install():
            shown.append(self.update_install_button)
        shown.append(self.update_view_button)
        return shown

    def show_update(self) -> None:
        """Title, text and buttons of the update row for the current state of `app.updates`."""
        notice = self.app.updates
        self.row_update.set_title(
            t("settings.update.check_title" if notice.available is None else "settings.update.title")
        )
        self.row_update.set_description(self.update_text())
        self.update_check_button.set_enabled(notice.phase != update_notice.CHECKING)
        if notice.phase == update_notice.DOWNLOADING:
            done, total = notice.progress
            self.update_progress.set_fraction(done / total if total else 0.0)
        shown = self.update_controls()
        for widget in self.update_box.pack_slaves():
            if widget not in shown:
                widget.pack_forget()
        gap = uk.px(uk.Space.MD)
        for widget in shown:
            side = uk.mirror_side("right")
            if widget is self.update_progress:
                widget.pack(side=side, fill="x", expand=True, padx=uk.mirror_pad((0, gap)))
            else:
                widget.pack(side=side, padx=uk.mirror_pad((0, gap if widget is not shown[0] else 0)))

    def update_text(self) -> str:
        """The update row's description."""
        notice = self.app.updates
        product, current = branding.PRODUCT_NAME, updates.app_version()
        phase = notice.phase
        if phase == update_notice.DOWNLOADING:
            done, total = notice.progress
            percent = int(done * 100 / total) if total else 0
            progress = whisper_models.format_progress(done, total) if total else whisper_models.format_bytes(done)
            return t("settings.update.downloading", percent=percent, progress=progress)
        if phase in (update_notice.WAITING, update_notice.INSTALLING):
            return t(f"settings.update.{phase}", product=product)
        if phase == update_notice.FAILED:
            if notice.error == update_install.CANCELLED:
                return t("settings.update.cancelled")
            return t(f"update.error.{notice.error}", product=product)
        if notice.available is not None:
            hint = self.update_hint(notice.available.version)
            return hint if notice.can_install() else hint + "\n" + t("settings.update.manual")
        if phase == update_notice.CHECKING:
            return t("settings.update.checking")
        if phase == update_notice.LATEST:
            return t("settings.update.latest", product=product, current=current)
        if phase == update_notice.CHECK_FAILED:
            return t("settings.update.check_failed")
        return t("settings.update.installed", product=product, current=current)

    @staticmethod
    def update_hint(version: str) -> str:
        return t("settings.update.hint", product=branding.PRODUCT_NAME, version=version, current=updates.app_version())

    def _dismiss_update(self) -> None:
        self.app.updates.dismiss()
        self.show_update()

    # ---------------------------------------------------------- glossary, setup

    def _build_glossary_row(self, body: tk.Misc) -> None:
        self.row_glossary = uk.SettingRow(
            body, t("settings.glossary.title"), variant="description", description=t("settings.glossary.hint")
        )
        self.row_glossary.pack(fill="x")
        self.glossary_button = uk.SecondaryButton(
            body,
            t("settings.glossary.button"),
            self.edit_glossary,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(self.glossary_button, self.GLOSSARY_WIDTH)
        self.row_glossary.set_control(self.glossary_button)

    def edit_glossary(self) -> None:
        """The glossary window: both lists, edited in place (the file stays the storage)."""
        self.app.open_glossary()

    def _build_setup_row(self, body: tk.Misc) -> None:
        """The setup assistant, once more: a new microphone, a GPU added, Ollama installed."""
        self.row_setup = uk.SettingRow(
            body,
            t("settings.setup.title"),
            variant="description",
            description=t("settings.setup.hint"),
            hairline=False,
        )
        self.row_setup.pack(fill="x")
        self.setup_button = uk.SecondaryButton(
            body,
            t("settings.setup.button"),
            self.app._open_setup,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(self.setup_button, self.SETUP_WIDTH)
        self.row_setup.set_control(self.setup_button)
