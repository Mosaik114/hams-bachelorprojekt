"""Der Update-Hinweis der Anwendung: Pruefung im Hintergrund, Tray-Meldung, Menueeintrag.

Die Logik (wann fragen, was vergleichen, was merken) steht in `updates.py`. Hier geht es nur
darum, wie der Hinweis erscheint: einmal je Version als Tray-Meldung, bis zum Ausblenden als
Eintrag im Tray-Menue und als Zeile unter Einstellungen → Allgemein. Ein Klick oeffnet die
Release-Seite im Browser; heruntergeladen wird nie etwas.
"""

from __future__ import annotations

import threading
import webbrowser

from hams import branding, updates
from hams.config import CFG, logger
from hams.i18n import t


class UpdateNoticeMixin:
    """Braucht `_ui_call`, `_refresh_tray`, `tray_icon` und `settings_win` der Anwendung."""

    #: Die Version, auf die gerade hingewiesen wird, oder None.
    available_update: updates.Release | None = None

    def _start_update_check(self) -> None:
        if not CFG.update_check:
            logger.info("Update check is off")
            return
        self._update_checker = updates.UpdateChecker()
        threading.Thread(target=self._update_worker, name="update-check", daemon=True).start()

    def _update_worker(self) -> None:
        """Im Hintergrund: `run` faengt Netzfehler selbst ab, alles andere landet nur im Log."""
        try:
            release = self._update_checker.run()
        except Exception:
            logger.exception("Update check failed")
            return
        if release is not None:
            self._ui_call(lambda: self._on_update_found(release))

    def _on_update_found(self, release: updates.Release) -> None:
        self.available_update = release
        self._refresh_tray()
        if self._update_checker.first_notice(release.version):
            self._notify_update(release)
        settings = getattr(self, "settings_win", None)
        if settings is not None and settings.alive():
            settings.show_update(release)

    def _notify_update(self, release: updates.Release) -> None:
        """Eine ruhige Systemmeldung; kann das Tray keine (Linux/AppIndicator), bleiben Menue und Einstellungen."""
        icon = getattr(self, "tray_icon", None)
        if icon is None or not getattr(icon, "HAS_NOTIFICATION", False):
            return
        try:
            icon.notify(
                t("update.notify_body"),
                t("update.notify_title", product=branding.PRODUCT_NAME, version=release.version),
            )
        except Exception:
            logger.exception("Update notification failed")

    def open_release_page(self, icon=None, item=None) -> None:
        release = self.available_update
        url = release.url if release is not None else updates.RELEASES_PAGE
        try:
            webbrowser.open(updates.release_url(url))
        except Exception:
            logger.exception("Could not open the release page")

    def dismiss_update(self) -> None:
        """Blendet den Hinweis fuer diese Version aus; eine spaetere Version meldet sich wieder."""
        release, self.available_update = self.available_update, None
        if release is not None:
            self._update_checker.dismiss(release.version)
            logger.info("Update notice dismissed: %s", release.version)
        self._refresh_tray()

    def update_menu_items(self, menu_item) -> list:
        """Der Tray-Eintrag, solange ein Hinweis aussteht (`menu_item` ist `pystray.MenuItem`)."""
        release = self.available_update
        if release is None:
            return []
        label = t("tray.update", product=branding.PRODUCT_NAME, version=release.version)
        return [menu_item(label, self.open_release_page)]
