"""Einstellungen: die Gruppe „Info & Hilfe“ — Version, Anleitung, Fehlerbehebung, Log-Ordner, Diagnosebericht.

Der Diagnosebericht ist der Selbsttest von ``Hams.exe --self-test`` (siehe `support`),
ohne Terminal: er läuft in einem Hintergrundthread, das Ergebnis kommt über den
UI-Dispatcher der App zurück, und der fertige Bericht öffnet sich im Standard-Editor.
"""

from __future__ import annotations

import threading
import tkinter as tk

from hams import branding, support, updates
from hams import ui_kit as uk
from hams.config import logger
from hams.i18n import t
from hams.ui_kit import Theme


class AboutSectionMixin:
    """Braucht `app`, `alive()` und `_group()` von SettingsWindow."""

    HELP_WIDTH = 124  # logisch, wie „Erneut starten“

    def _build_about(self, master: tk.Misc) -> None:
        group = self._group(master, t("settings.section.about"))
        self.row_version = uk.SettingRow(
            group.body,
            t("settings.about.version"),
            variant="display",
            value=f"{branding.PRODUCT_NAME} {updates.app_version()}",
        )
        self.row_version.pack(fill="x")
        self.row_guide, self.guide_button = self._help_row(
            group, "guide", support.open_user_guide, t("settings.about.open")
        )
        self.row_troubleshooting, self.troubleshooting_button = self._help_row(
            group, "troubleshooting", support.open_troubleshooting, t("settings.about.open")
        )
        self.row_logs, self.logs_button = self._help_row(group, "logs", self._open_logs, t("settings.about.open"))
        self.row_report, self.report_button = self._help_row(
            group, "report", self._create_report, t("settings.about.report.button"), hairline=False
        )
        self._report_running = False

    def _help_row(self, group: uk.GroupSurface, key: str, command, label: str, *, hairline: bool = True):
        row = uk.SettingRow(
            group.body,
            t(f"settings.about.{key}.title"),
            variant="description",
            description=t(f"settings.about.{key}.hint"),
            hairline=hairline,
        )
        row.pack(fill="x")
        button = uk.SecondaryButton(group.body, label, command, bg_under=Theme.GROUP_SURFACE, height=uk.Height.STANDARD)
        uk.keep_logical_width(button, self.HELP_WIDTH)
        row.set_control(button)
        return row, button

    def _open_logs(self) -> None:
        try:
            support.open_log_folder()
        except OSError as exc:
            logger.warning("Log folder could not be opened: %s", type(exc).__name__)
            self.row_logs.set_description(t("settings.about.logs.failed", path=support.log_dir()))

    # ---------------------------------------------------------- Diagnosebericht

    def _create_report(self) -> None:
        if self._report_running:
            return
        self._report_running = True
        self.report_button.set_enabled(False)
        self.row_report.set_description(t("settings.about.report.running"))
        threading.Thread(target=self._report_worker, daemon=True, name="diagnostic-report").start()

    def _report_worker(self) -> None:
        try:
            path, failures = support.write_report()
        except Exception as exc:  # noqa: BLE001 - jeder Fehler endet als Meldung in der Zeile
            logger.warning("Diagnostic report failed: %s", type(exc).__name__)
            self.app._ui_call(lambda: self._report_finished(None, 0))
            return
        self.app._ui_call(lambda: self._report_finished(path, failures))

    def _report_finished(self, path, failures: int) -> None:
        """Auf dem Tk-Thread: Zeile aktualisieren und den Bericht öffnen."""
        self._report_running = False
        if not self.alive():
            if path is not None:
                self._open_report(path)
            return
        self.report_button.set_enabled(True)
        if path is None:
            self.row_report.set_description(t("settings.about.report.failed"))
            return
        opened = self._open_report(path)
        self.row_report.set_description(self.report_hint(path, failures, opened))

    @staticmethod
    def _open_report(path) -> bool:
        try:
            support.open_path(path)
        except OSError as exc:
            logger.warning("Diagnostic report could not be opened: %s", type(exc).__name__)
            return False
        return True

    @staticmethod
    def report_hint(path, failures: int, opened: bool) -> str:
        """Unterzeile nach dem Bericht: wo er liegt, und ob ein Check fehlschlug."""
        if failures:
            return t("settings.about.report.done_failures", count=failures, path=path)
        if opened:
            return t("settings.about.report.done", path=path)
        return t("settings.about.report.saved", path=path)
