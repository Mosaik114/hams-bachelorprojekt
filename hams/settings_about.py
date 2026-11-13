"""Einstellungen: die Gruppe „Info & Hilfe“ — Version, Anleitung, Fehlerbehebung, Log-Ordner, Diagnosebericht.

Der Diagnosebericht ist der Selbsttest von ``Hams.exe --self-test`` (siehe `support`),
ohne Terminal: er läuft in einem Hintergrundthread, das Ergebnis kommt über den
UI-Dispatcher der App zurück, und der fertige Bericht öffnet sich im Standard-Editor.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path

from hams import branding, support, updates
from hams import ui_kit as uk
from hams.config import logger
from hams.i18n import t, tn
from hams.ui_kit import Theme


class AboutSectionMixin:
    """Braucht `app`, `alive()` und `_group()` von SettingsWindow."""

    HELP_WIDTH = 124  # logisch, wie „Erneut starten“
    REPORT_OPEN_WIDTH = 88  # logisch, „Öffnen“ neben „Erstellen“

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
        self._build_report_row(group)
        self._report_running = False
        #: The last report written from this window; its "Open" button opens it again.
        self._report_path: Path | None = None

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

    def _build_report_row(self, group: uk.GroupSurface) -> None:
        """Create, and once a report exists, Open next to it: the row names the file instead of a long path."""
        self.row_report = uk.SettingRow(
            group.body,
            t("settings.about.report.title"),
            variant="description",
            description=t("settings.about.report.hint"),
            hairline=False,
        )
        self.row_report.pack(fill="x")
        box = tk.Frame(group.body, bg=Theme.GROUP_SURFACE, highlightthickness=0, takefocus=0)
        box.focus_ring = True  # die Knoepfe darin tragen ihn
        self.report_open_button = uk.SecondaryButton(
            box,
            t("settings.about.open"),
            self._open_last_report,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        self.report_button = uk.SecondaryButton(
            box,
            t("settings.about.report.button"),
            self._create_report,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(self.report_button, self.HELP_WIDTH)
        uk.keep_logical_width(self.report_open_button, self.REPORT_OPEN_WIDTH)
        self.report_button.pack(side="right")
        # From the start, greyed out until there is a report: a control that appears later changes the row's
        # width and makes the wrapped description reflow.
        self.report_open_button.set_enabled(False)
        self.report_open_button.pack(side="right", padx=(0, uk.px(uk.Space.XS)))
        self.row_report.set_control(box)

    def _open_last_report(self) -> None:
        if self._report_path is not None and not self._open_report(self._report_path):
            self.row_report.set_description(t("settings.about.report.saved", name=Path(self._report_path).name))

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
            self.app.ui_call(lambda: self._report_finished(None, 0))
            return
        self.app.ui_call(lambda: self._report_finished(path, failures))

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
        self._report_path = path
        self.report_open_button.set_enabled(True)

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
        """Unterzeile nach dem Bericht: der Dateiname (der volle Pfad bricht um), und ob ein Check fehlschlug."""
        name = Path(path).name
        if failures:
            return tn("settings.about.report.done_failures", failures, name=name)
        if opened:
            return t("settings.about.report.done", name=name)
        return t("settings.about.report.saved", name=name)
