"""How the app shows a newer release (hams/update_notice.py): tray, settings, browser. No real network."""

from types import SimpleNamespace

import pystray
import pytest
from test_settings_window_live import disk, open_settings  # noqa: F401 - Fixtures

from hams import update_notice, updates
from hams.config import CFG
from hams.updates import Release

#: The real start of the check; the `hams` fixture replaces it on the class to stay offline.
REAL_START = update_notice.UpdateNotice.start
PAGE = updates.RELEASES_PAGE + "/tag/v9.0.0"
RELEASE = Release("9.0.0", PAGE)


class FakeChecker:
    def __init__(self, result=RELEASE, error=None):
        self.result, self.error = result, error
        self.dismissed, self.notified = [], set()

    def run(self):
        if self.error is not None:
            raise self.error
        return self.result

    def dismiss(self, version):
        self.dismissed.append(version)

    def first_notice(self, version):
        new = version not in self.notified
        self.notified.add(version)
        return new


class InlineThread:
    started: list = []

    def __init__(self, target, **_options):
        self.target = target

    def start(self):
        InlineThread.started.append(self.target)
        self.target()


class NotifyingTray:
    HAS_NOTIFICATION = True

    def __init__(self):
        self.notes, self.menu = [], None

    def notify(self, message, title=None):
        self.notes.append((title, message))

    def update_menu(self):
        pass


@pytest.fixture
def checker(live_app, monkeypatch):
    fake = FakeChecker()
    InlineThread.started = []
    monkeypatch.setattr(update_notice.updates, "UpdateChecker", lambda: fake)
    monkeypatch.setattr(update_notice.threading, "Thread", InlineThread)
    monkeypatch.setattr(live_app, "tray_icon", NotifyingTray())
    return fake


def start(app):
    REAL_START(app.updates)
    app.root.update()


def menu_texts(app):
    return [item.text for item in app._build_tray_menu().items if item is not pystray.Menu.SEPARATOR]


class TestCheck:
    def test_off_means_no_request_at_all(self, live_app, checker):
        CFG.update_check = False
        start(live_app)
        assert InlineThread.started == []
        assert live_app.updates.available is None

    def test_a_newer_release_is_announced_once(self, live_app, checker):
        CFG.update_check = True
        start(live_app)
        assert live_app.updates.available == RELEASE
        assert live_app.tray_icon.notes == [("Hams 9.0.0 ist verfügbar", live_app.tray_icon.notes[0][1])]
        start(live_app)
        assert len(live_app.tray_icon.notes) == 1, "the tray says it once per version"
        assert menu_texts(live_app)[0] == "Update verfügbar: Hams 9.0.0 …"

    def test_nothing_newer_changes_nothing(self, live_app, checker):
        CFG.update_check = True
        checker.result = None
        start(live_app)
        assert live_app.updates.available is None
        assert live_app.tray_icon.notes == []
        assert not menu_texts(live_app)[0].startswith("Update")

    def test_an_unexpected_error_is_only_logged(self, live_app, checker, caplog):
        CFG.update_check = True
        checker.error = RuntimeError("kaputt")
        start(live_app)
        assert live_app.updates.available is None
        assert "Update check failed" in caplog.text

    def test_a_tray_without_notifications_keeps_quiet(self, live_app, checker, monkeypatch):
        CFG.update_check = True
        monkeypatch.setattr(live_app, "tray_icon", SimpleNamespace(menu=None, update_menu=lambda: None))
        start(live_app)
        assert live_app.updates.available == RELEASE

    def test_a_failing_notification_is_logged(self, live_app, checker, caplog):
        def broken(*_args):
            raise OSError("kein Tray")

        live_app.tray_icon.notify = broken
        live_app.updates._checker = checker
        live_app._notify_update(RELEASE)
        assert "Update notification failed" in caplog.text


class TestActions:
    def test_view_opens_the_release_page_in_the_browser(self, live_app, monkeypatch):
        opened = []
        monkeypatch.setattr(update_notice.webbrowser, "open", opened.append)
        live_app.updates.available = RELEASE
        live_app.updates.open_release_page()
        live_app.updates.available = None
        live_app.updates.open_release_page()
        assert opened == [PAGE, updates.RELEASES_PAGE]

    def test_a_browser_failure_is_logged(self, live_app, monkeypatch, caplog):
        def broken(_url):
            raise OSError("kein Browser")

        monkeypatch.setattr(update_notice.webbrowser, "open", broken)
        live_app.updates.open_release_page()
        assert "Could not open the release page" in caplog.text

    def test_dismiss_hides_it_for_this_version(self, live_app, checker):
        live_app.updates._checker = checker
        live_app.updates.available = RELEASE
        live_app.updates.dismiss()
        assert checker.dismissed == ["9.0.0"]
        assert live_app.updates.available is None
        live_app.updates.dismiss()  # nichts mehr offen: harmlos
        assert checker.dismissed == ["9.0.0"]


class TestSettings:
    def test_the_switch_is_saved(self, open_settings, hams):  # noqa: F811
        win = open_settings()
        before = CFG.update_check
        win.switch_updates.toggle()
        assert CFG.update_check is (not before)
        assert ("updates", "check", not before) in hams.saved

    def test_no_notice_row_without_an_update(self, open_settings):  # noqa: F811
        assert open_settings().row_update is None

    def test_a_pending_update_shows_a_row_and_can_be_dismissed(self, open_settings, live_app, checker):  # noqa: F811
        live_app.updates.available = RELEASE
        live_app.updates._checker = checker
        win = open_settings()
        current = updates.app_version()
        assert win.row_update.sub_text == f"Hams 9.0.0 ist erschienen · installiert ist {current}"
        win._dismiss_update()
        assert win.row_update is None
        assert checker.dismissed == ["9.0.0"]

    def test_an_update_found_while_the_window_is_open(self, open_settings, live_app, checker):  # noqa: F811
        CFG.update_check = True
        win = open_settings()
        start(live_app)
        assert win.row_update is not None
        win.show_update(Release("9.1.0", PAGE))
        assert win.row_update.sub_text.startswith("Hams 9.1.0 ist erschienen")
