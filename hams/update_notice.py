"""The app's updates: the check in the background, the tray message and entry, and "Update now".

The logic lives elsewhere: when to ask GitHub and what to remember in `updates.py`, download, checksum and the
silent setup in `update_install.py`. This is how it all appears and in which order it happens:

- the daily check (`start`), and "Check for updates now" (`check_now`) with its result for the settings row;
- a newer release: once per version a tray message, until dismissed an entry in the tray menu and a row under
  Settings → General;
- "Update now" (`install`, from that row or the tray entry): download in the background with progress, verify,
  then hand the setup to the app (`install` callback), which waits for a running dictation and quits for the setup.

`phase` says where that stands; every change calls `on_change()`, so the tray and the settings row follow it.
"""

from __future__ import annotations

import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

from hams import branding, update_install, updates
from hams.config import CFG, logger
from hams.i18n import t

#: Where an update stands (`UpdateNotice.phase`).
IDLE = "idle"
CHECKING = "checking"
LATEST = "latest"
CHECK_FAILED = "check_failed"
DOWNLOADING = "downloading"
WAITING = "waiting"
INSTALLING = "installing"
FAILED = "failed"
#: Phases in which a new check or a second download would get in the way.
BUSY = (CHECKING, DOWNLOADING, WAITING, INSTALLING)


class UpdateNotice:
    """The newer release Hams points to, if any, and the update to it.

    `ui_call` brings a result onto the Tk thread; `on_found(release, first)` shows a release found by the daily
    check there (`first`: the first time for this version, worth a tray message); `on_change()` follows every
    change of `available` and `phase`; `install(setup)` gets the verified setup file and runs it (the app).
    """

    def __init__(
        self,
        ui_call: Callable[[Callable[[], object]], None],
        on_found: Callable[[updates.Release, bool], None],
        on_change: Callable[[], None],
        install: Callable[[Path], None] | None = None,
    ) -> None:
        self._ui_call = ui_call
        self._on_found = on_found
        self._on_change = on_change
        self._install = install
        self._checker: updates.UpdateChecker | None = None
        self._download: update_install.Download | None = None
        self._percent = -1
        #: The version the notice points to, or None.
        self.available: updates.Release | None = None
        self.phase = IDLE
        #: Why the last update stopped (an `update_install` reason) while `phase` is FAILED.
        self.error = ""
        #: Bytes downloaded and expected (0 if unknown) while `phase` is DOWNLOADING.
        self.progress = (0, 0)

    @property
    def checker(self) -> updates.UpdateChecker:
        if self._checker is None:
            self._checker = updates.UpdateChecker()
        return self._checker

    @staticmethod
    def can_install() -> bool:
        return update_install.can_install()

    def _set_phase(self, phase: str, error: str = "") -> None:
        self.phase, self.error = phase, error
        self._on_change()

    # ---------------------------------------------------------- checking

    def start(self) -> None:
        """Asks GitHub in the background (once a day at most, see `updates.UpdateChecker`), unless switched off."""
        if not CFG.update_check:
            logger.info("Update check is off")
            return
        checker = self.checker
        threading.Thread(target=self._worker, args=(checker,), name="update-check", daemon=True).start()

    def _worker(self, checker: updates.UpdateChecker) -> None:
        """In the background: `run` handles network errors itself, anything else only goes to the log."""
        try:
            release = checker.run()
        except Exception:  # noqa: BLE001 - boundary of the thread
            logger.exception("Update check failed")
            return
        if release is not None:
            self._ui_call(lambda: self._found(release))

    def _found(self, release: updates.Release) -> None:
        if self.phase in BUSY:
            return  # an update under way keeps the release it started with
        self.available = release
        self._on_found(release, self.checker.first_notice(release.version))

    def check_now(self) -> None:
        """ "Check for updates now": asks GitHub at once, also with the daily check switched off."""
        if self.phase in BUSY:
            return
        checker = self.checker
        self._set_phase(CHECKING)
        threading.Thread(target=self._check_worker, args=(checker,), name="update-check-now", daemon=True).start()

    def _check_worker(self, checker: updates.UpdateChecker) -> None:
        try:
            result, release = checker.check_now()
        except Exception:  # noqa: BLE001 - boundary of the thread
            logger.exception("Update check failed")
            result, release = updates.FAILED, None
        self._ui_call(lambda: self._checked(result, release))

    def _checked(self, result: str, release: updates.Release | None) -> None:
        if result == updates.AVAILABLE and release is not None:
            self.available = release
            self._set_phase(IDLE)
        elif result == updates.LATEST:
            self.available = None
            self._set_phase(LATEST)
        else:
            self._set_phase(CHECK_FAILED)

    # ---------------------------------------------------------- installing

    def install(self, icon=None, item=None) -> None:
        """ "Update now" (settings row, tray entry; pystray passes icon and item). Safe from any thread."""
        self._ui_call(self._start_install)

    def _start_install(self) -> None:
        release = self.available
        if release is None or self.phase in BUSY:
            return
        if not self.can_install():
            self.open_release_page()
            return
        self._percent = -1
        self.progress = (0, 0)
        download = self._download = update_install.Download(release, progress=self._on_progress)
        self._set_phase(DOWNLOADING)
        logger.info("Update to %s started", release.version)
        threading.Thread(target=self._download_worker, args=(download,), name="update-download", daemon=True).start()

    def _download_worker(self, download: update_install.Download) -> None:
        try:
            setup = download.run()
        except update_install.UpdateError as exc:
            reason = exc.reason
            self._ui_call(lambda: self._stopped(download, reason))
            return
        except Exception:  # noqa: BLE001 - boundary of the thread
            logger.exception("Update download failed")
            self._ui_call(lambda: self._stopped(download, update_install.DOWNLOAD))
            return
        self._ui_call(lambda: self._downloaded(download, setup))

    def _on_progress(self, done: int, total: int) -> None:
        """From the download thread; the UI hears of whole percents only."""
        percent = int(done * 100 / total) if total else -1
        if percent == self._percent:
            return
        self._percent = percent
        self._ui_call(lambda: self._show_progress(done, total))

    def _show_progress(self, done: int, total: int) -> None:
        if self.phase == DOWNLOADING:
            self.progress = (done, total)
            self._on_change()

    def _stopped(self, download: update_install.Download, reason: str) -> None:
        if download is not self._download:
            return
        self._download = None
        logger.info("Update stopped: %s", reason)
        self._set_phase(FAILED, reason)

    def _downloaded(self, download: update_install.Download, setup: Path) -> None:
        if download is not self._download:
            return
        self._download = None
        self._set_phase(WAITING)
        if self._install is not None:
            self._install(setup)

    def cancel(self) -> None:
        """Stops a download, or an update still waiting for a dictation to finish."""
        if self._download is not None:
            self._download.cancel()
        elif self.phase == WAITING:
            self._set_phase(FAILED, update_install.CANCELLED)

    def installing(self) -> None:
        """The app starts the setup now and quits."""
        self._set_phase(INSTALLING)

    def failed(self, reason: str) -> None:
        self._set_phase(FAILED, reason)

    # ---------------------------------------------------------- the notice

    def open_release_page(self, icon=None, item=None) -> None:
        """ "View", and the tray entry where one-click updates are not possible: the release page in the browser."""
        release = self.available
        url = release.url if release is not None else updates.RELEASES_PAGE
        try:
            webbrowser.open(updates.release_url(url))
        except Exception:  # noqa: BLE001 - only a browser that did not open
            logger.exception("Could not open the release page")

    def dismiss(self) -> None:
        """Hides the notice for this version; a later version shows up again."""
        if self.phase in BUSY:
            return
        release, self.available = self.available, None
        if release is not None:
            self.checker.dismiss(release.version)
            logger.info("Update notice dismissed: %s", release.version)
        self._set_phase(IDLE)

    def menu_items(self, menu_item, install: Callable | None = None) -> list:
        """The tray entry while a notice is pending (`menu_item` is `pystray.MenuItem`).

        It updates (`install`, by default `self.install`) where one-click updates work, else opens the release page.
        """
        release = self.available
        if release is None:
            return []
        label = t("tray.update", product=branding.PRODUCT_NAME, version=release.version)
        action = (install or self.install) if self.can_install() else self.open_release_page
        return [menu_item(label, action)]
