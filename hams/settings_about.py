"""The settings group "Info & help": version, guide, troubleshooting, log folder, diagnostic report.

The diagnostic report is the self-test of ``Hams.exe --self-test`` (see `support`), without a terminal: it runs in
a background thread, the result comes back through the app's UI dispatcher, and the finished report opens in the
default editor.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path

from hams import branding, support, updates
from hams import ui_kit as uk
from hams.config import logger
from hams.i18n import t, tn
from hams.settings_sections import Section, group
from hams.ui_kit import Theme


def report_hint(path, failures: int, opened: bool) -> str:
    """Description after the report: the file name (the full path wraps), and whether a check failed."""
    name = Path(path).name
    if failures:
        return tn("settings.about.report.done_failures", failures, name=name)
    if opened:
        return t("settings.about.report.done", name=name)
    return t("settings.about.report.saved", name=name)


def open_report(path) -> bool:
    try:
        support.open_path(path)
    except OSError as exc:
        logger.warning("Diagnostic report could not be opened: %s", type(exc).__name__)
        return False
    return True


class AboutSection(Section):
    """Version, help links, log folder and the diagnostic report."""

    # The buttons measure themselves (text plus padding). Fixed widths from the thinner font made them wider
    # than their word, and the report row with two of them left its description four lines of room.

    def __init__(self, window, master: tk.Misc) -> None:
        super().__init__(window)
        self.report_running = False
        #: The last report written from this window; its "Open" button opens it again.
        self.report_path: Path | None = None
        self.group = group(master, t("settings.section.about"))
        body = self.group.body
        self.row_version = uk.SettingRow(
            body,
            t("settings.about.version"),
            variant="display",
            value=f"{branding.PRODUCT_NAME} {updates.app_version()}",
        )
        self.row_version.pack(fill="x")
        self.row_guide, self.guide_button = self._help_row(body, "guide", support.open_user_guide)
        self.row_troubleshooting, self.troubleshooting_button = self._help_row(
            body, "troubleshooting", support.open_troubleshooting
        )
        self.row_logs, self.logs_button = self._help_row(body, "logs", self.open_logs)
        self._build_report_row(body)

    def sync(self) -> None:
        """A fresh look, as if the window had just been built - a report still running keeps its line."""
        self.row_logs.set_description(t("settings.about.logs.hint"))
        if self.report_running:
            return
        self.report_path = None
        self.report_button.set_enabled(True)
        self.report_open_button.set_enabled(False)
        self.row_report.set_description(t("settings.about.report.hint"))

    def _help_row(self, body: tk.Misc, key: str, command):
        row = uk.SettingRow(
            body,
            t(f"settings.about.{key}.title"),
            variant="description",
            description=t(f"settings.about.{key}.hint"),
        )
        row.pack(fill="x")
        button = uk.SecondaryButton(
            body, t("settings.about.open"), command, bg_under=Theme.GROUP_SURFACE, height=uk.Height.STANDARD
        )
        row.set_control(button)
        return row, button

    def _build_report_row(self, body: tk.Misc) -> None:
        """Create, and once a report exists, Open next to it: the row names the file instead of a long path."""
        self.row_report = uk.SettingRow(
            body,
            t("settings.about.report.title"),
            variant="description",
            description=t("settings.about.report.hint"),
            hairline=False,
        )
        self.row_report.pack(fill="x")
        # The two buttons stand on a line of their own under the description: beside it they left the text a
        # narrow column of four lines ("Info & Hilfe" looked crowded).
        box = tk.Frame(body, bg=Theme.GROUP_SURFACE, highlightthickness=0, takefocus=0)
        self.report_open_button = uk.SecondaryButton(
            box,
            t("settings.about.open"),
            self.open_last_report,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        self.report_button = uk.SecondaryButton(
            box,
            t("settings.about.report.button"),
            self.create_report,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        self.report_button.pack(side=uk.mirror_side("right"))
        # From the start, greyed out until there is a report: a control that appears later would shift the line.
        self.report_open_button.set_enabled(False)
        self.report_open_button.pack(side=uk.mirror_side("right"), padx=uk.mirror_pad((0, uk.px(uk.Space.MD))))
        box.pack(fill="x", padx=uk.px(uk.Space.ROW_X), pady=(0, uk.px(uk.Space.ROW_Y)))
        self.report_box = box

    def open_last_report(self) -> None:
        if self.report_path is not None and not open_report(self.report_path):
            self.row_report.set_description(t("settings.about.report.saved", name=Path(self.report_path).name))

    def open_logs(self) -> None:
        try:
            support.open_log_folder()
        except OSError as exc:
            logger.warning("Log folder could not be opened: %s", type(exc).__name__)
            self.row_logs.set_description(t("settings.about.logs.failed", path=support.log_dir()))

    # ---------------------------------------------------------- diagnostic report

    def create_report(self) -> None:
        if self.report_running:
            return
        self.report_running = True
        self.report_button.set_enabled(False)
        self.row_report.set_description(t("settings.about.report.running"))
        threading.Thread(target=self._report_worker, daemon=True, name="diagnostic-report").start()

    def _report_worker(self) -> None:
        try:
            path, failures = support.write_report()
        except Exception as exc:  # noqa: BLE001 - every error ends as a message in the row
            logger.warning("Diagnostic report failed: %s", type(exc).__name__)
            self.app.ui_call(lambda: self.report_finished(None, 0))
            return
        self.app.ui_call(lambda: self.report_finished(path, failures))

    def report_finished(self, path, failures: int) -> None:
        """On the Tk thread: update the row and open the report."""
        self.report_running = False
        if not self.alive():
            if path is not None:
                open_report(path)
            return
        self.report_button.set_enabled(True)
        if path is None:
            self.row_report.set_description(t("settings.about.report.failed"))
            return
        opened = open_report(path)
        self.row_report.set_description(report_hint(path, failures, opened))
        self.report_path = path
        self.report_open_button.set_enabled(True)
