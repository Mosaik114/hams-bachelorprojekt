"""The app's update notice: the check in the background, the tray message, the menu entry.

The logic (when to ask, what to compare, what to remember) is in `updates.py`. This is only how the notice appears:
once per version as a tray message, until dismissed as an entry in the tray menu and as a row under Settings →
General. A click opens the release page in the browser; nothing is ever downloaded.
"""

from __future__ import annotations

import threading
import webbrowser
from collections.abc import Callable

from hams import branding, updates
from hams.config import CFG, logger
from hams.i18n import t


class UpdateNotice:
    """The newer release Hams points to, if any.

    `ui_call` brings a result onto the Tk thread; `on_found(release, first)` shows it there (`first`: the first time
    for this version, worth a tray message); `on_change()` rebuilds the tray menu after a dismissal.
    """

    def __init__(
        self,
        ui_call: Callable[[Callable[[], object]], None],
        on_found: Callable[[updates.Release, bool], None],
        on_change: Callable[[], None],
    ) -> None:
        self._ui_call = ui_call
        self._on_found = on_found
        self._on_change = on_change
        self._checker: updates.UpdateChecker | None = None
        #: The version the notice points to, or None.
        self.available: updates.Release | None = None

    def start(self) -> None:
        """Asks GitHub in the background (once a day at most, see `updates.UpdateChecker`), unless switched off."""
        if not CFG.update_check:
            logger.info("Update check is off")
            return
        self._checker = updates.UpdateChecker()
        threading.Thread(target=self._worker, name="update-check", daemon=True).start()

    def _worker(self) -> None:
        """In the background: `run` handles network errors itself, anything else only goes to the log."""
        try:
            release = self._checker.run()
        except Exception:  # noqa: BLE001 - boundary of the thread
            logger.exception("Update check failed")
            return
        if release is not None:
            self._ui_call(lambda: self._found(release))

    def _found(self, release: updates.Release) -> None:
        self.available = release
        self._on_found(release, self._checker.first_notice(release.version))

    def open_release_page(self, icon=None, item=None) -> None:
        """Tray entry and settings row: the release page in the browser (pystray passes icon and item)."""
        release = self.available
        url = release.url if release is not None else updates.RELEASES_PAGE
        try:
            webbrowser.open(updates.release_url(url))
        except Exception:  # noqa: BLE001 - only a browser that did not open
            logger.exception("Could not open the release page")

    def dismiss(self) -> None:
        """Hides the notice for this version; a later version shows up again."""
        release, self.available = self.available, None
        if release is not None and self._checker is not None:
            self._checker.dismiss(release.version)
            logger.info("Update notice dismissed: %s", release.version)
        self._on_change()

    def menu_items(self, menu_item) -> list:
        """The tray entry while a notice is pending (`menu_item` is `pystray.MenuItem`)."""
        release = self.available
        if release is None:
            return []
        label = t("tray.update", product=branding.PRODUCT_NAME, version=release.version)
        return [menu_item(label, self.open_release_page)]
