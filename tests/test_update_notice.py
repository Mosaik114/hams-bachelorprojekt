"""How the app shows a newer release (hams/update_notice.py): tray, settings, browser. No real network."""

import time
from types import SimpleNamespace

import kit as uk_kit
import pystray
import pytest
from test_settings_window_live import disk, open_settings  # noqa: F401 - Fixtures

from hams import app as app_mod
from hams import update_install, update_notice, updates
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
        #: What "Check for updates now" answers.
        self.now = (updates.AVAILABLE, RELEASE)

    def check_now(self):
        if self.error is not None:
            raise self.error
        return self.now

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

    def __init__(self, target, args=(), **_options):
        self.target, self.args = target, args

    def start(self):
        InlineThread.started.append(self.target)
        self.target(*self.args)


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
        assert menu_texts(live_app)[0] == "Jetzt aktualisieren: Hams 9.0.0"

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
        general = open_settings().general
        assert general.row_updates._title_text == "Über Updates informieren"
        before = CFG.update_check
        general.switch_updates.toggle()
        assert CFG.update_check is (not before)
        assert ("updates", "check", not before) in hams.saved

    def test_without_an_update_the_row_offers_a_check(self, open_settings):  # noqa: F811
        general = open_settings().general
        assert general.row_update._title_text == "Updates"
        assert general.row_update.sub_text == f"Installiert ist Hams {updates.app_version()}"
        assert shown(general) == ["Jetzt nach Updates suchen"]

    def test_a_pending_update_shows_its_actions_and_can_be_dismissed(
        self,
        open_settings,  # noqa: F811
        live_app,
        checker,
        installable,
    ):
        live_app.updates.available = RELEASE
        general = open_settings().general
        current = updates.app_version()
        assert general.row_update._title_text == "Update verfügbar"
        assert general.row_update.sub_text == f"Hams 9.0.0 ist erschienen · installiert ist {current}"
        assert shown(general) == ["close", "Jetzt aktualisieren", "Ansehen"]
        general._dismiss_update()
        assert general.row_update._title_text == "Updates"
        assert checker.dismissed == ["9.0.0"]

    def test_without_one_click_updates_only_view(self, open_settings, live_app, checker, monkeypatch):  # noqa: F811
        monkeypatch.setattr(update_notice.UpdateNotice, "can_install", staticmethod(lambda: False))
        live_app.updates.available = RELEASE
        general = open_settings().general
        assert shown(general) == ["close", "Ansehen"]
        assert general.row_update.sub_text.endswith("nur in der installierten Windows-App")

    def test_an_update_found_while_the_window_is_open(self, open_settings, live_app, checker):  # noqa: F811
        CFG.update_check = True
        general = open_settings().general
        start(live_app)
        assert general.row_update.sub_text.startswith("Hams 9.0.0 ist erschienen")


def shown(general) -> list[str]:
    """The update row's controls from the right edge inwards: button texts, "close" for the round crosses."""
    names = []
    for widget in general.update_controls():
        assert widget.winfo_manager() == "pack"
        names.append(widget.cget("text") if isinstance(widget, uk_kit.SecondaryButton) else "close")
    return names


@pytest.fixture
def installable(monkeypatch):
    monkeypatch.setattr(update_notice.UpdateNotice, "can_install", staticmethod(lambda: True))


class FakeDownload:
    """update_install.Download without network: `result` is a path, an UpdateError reason or an exception."""

    result: object = None
    made: list = []

    def __init__(self, release, *, progress):
        self.release, self.progress = release, progress
        self.cancelled = False
        FakeDownload.made.append(self)

    def cancel(self):
        self.cancelled = True

    def run(self):
        self.progress(0, 100)
        self.progress(40, 100)
        if isinstance(FakeDownload.result, str):
            raise update_install.UpdateError(FakeDownload.result)
        if isinstance(FakeDownload.result, Exception):
            raise FakeDownload.result
        return FakeDownload.result


@pytest.fixture
def downloads(live_app, checker, installable, monkeypatch, tmp_path):
    """The install flow without network and without starting anything; the list collects setups and the quit."""
    FakeDownload.made, FakeDownload.result = [], tmp_path / "Hams-Setup-9.0.0.exe"
    launched = []
    monkeypatch.setattr(update_notice.update_install, "Download", FakeDownload)
    monkeypatch.setattr(
        app_mod.update_install, "launch_setup", lambda setup, background: launched.append((setup, background))
    )
    monkeypatch.setattr(live_app, "_shut_down", lambda: launched.append("quit"))
    live_app.updates.available = RELEASE
    return launched


def no_thread(**_options):
    return SimpleNamespace(start=lambda: None)


class TestCheckNow:
    @pytest.mark.parametrize(
        ("answer", "phase", "text"),
        [
            ((updates.LATEST, None), update_notice.LATEST, "Du hast die neueste Version · Hams "),
            ((updates.FAILED, None), update_notice.CHECK_FAILED, "GitHub ist gerade nicht erreichbar"),
            ((updates.AVAILABLE, RELEASE), update_notice.IDLE, "Hams 9.0.0 ist erschienen"),
        ],
    )
    def test_results_show_in_the_row(self, open_settings, live_app, checker, answer, phase, text):  # noqa: F811
        checker.now = answer
        general = open_settings().general
        general.update_check_button._invoke()
        live_app.root.update()
        assert live_app.updates.phase == phase
        assert general.row_update.sub_text.startswith(text)

    def test_while_checking(self, open_settings, live_app, checker, monkeypatch):  # noqa: F811
        monkeypatch.setattr(update_notice.threading, "Thread", no_thread)
        general = open_settings().general
        live_app.updates.check_now()
        assert general.row_update.sub_text == "Wird gesucht …"
        assert not general.update_check_button.enabled
        live_app.updates.check_now()  # a second click while one runs changes nothing
        assert live_app.updates.phase == update_notice.CHECKING

    def test_an_error_in_the_check_counts_as_failed(self, live_app, checker, caplog):
        checker.error = RuntimeError("kaputt")
        live_app.updates.check_now()
        live_app.root.update()
        assert live_app.updates.phase == update_notice.CHECK_FAILED
        assert "Update check failed" in caplog.text

    def test_the_daily_result_waits_while_an_update_runs(self, live_app, checker):
        live_app.updates.phase = update_notice.DOWNLOADING
        live_app.updates._found(Release("9.9.9", PAGE))
        assert live_app.updates.available is None


class TestInstall:
    def test_download_then_setup_then_quit(self, open_settings, live_app, downloads):  # noqa: F811
        general = open_settings().general
        phases = []
        follow = live_app.updates._on_change

        def record():
            phases.append(live_app.updates.phase)
            follow()

        live_app.updates._on_change = record
        general.update_install_button._invoke()
        live_app.root.update()
        assert update_notice.DOWNLOADING in phases
        assert live_app.updates.progress == (40, 100)
        assert downloads == [(FakeDownload.result, True), "quit"]
        assert live_app.updates.phase == update_notice.INSTALLING
        assert general.row_update.sub_text == "Wird installiert · Hams startet gleich neu"

    def test_the_row_shows_the_download(self, open_settings, live_app, downloads):  # noqa: F811
        general = open_settings().general
        live_app.updates.phase, live_app.updates.progress = update_notice.DOWNLOADING, (34_000_000, 75_000_000)
        general.show_update()
        assert general.row_update.sub_text.startswith("Wird heruntergeladen · 45 % · ")
        assert shown(general)[0] == "close"
        assert general.update_progress.level == pytest.approx(34 / 75, abs=0.01)
        live_app.updates.progress = (5_000, 0)
        general.show_update()
        assert general.row_update.sub_text.startswith("Wird heruntergeladen · 0 % · ")

    def test_waits_for_a_running_dictation(self, live_app, downloads, monkeypatch):
        monkeypatch.setattr(type(live_app), "is_recording", property(lambda app: True))
        monkeypatch.setattr(live_app, "UPDATE_WAIT_MS", 1)
        live_app.updates.install()
        live_app.root.update()
        assert live_app.updates.phase == update_notice.WAITING
        assert downloads == []
        monkeypatch.setattr(type(live_app), "is_recording", property(lambda app: False))
        for _ in range(200):
            live_app.root.update()
            if downloads:
                break
            time.sleep(0.005)
        assert downloads[-1] == "quit"

    def test_cancel_while_waiting(self, live_app, downloads, monkeypatch):
        monkeypatch.setattr(type(live_app), "is_recording", property(lambda app: True))
        monkeypatch.setattr(live_app, "UPDATE_WAIT_MS", 1)
        live_app.updates.install()
        live_app.root.update()
        live_app.updates.cancel()
        assert live_app.updates.phase == update_notice.FAILED
        assert live_app.updates.error == update_install.CANCELLED
        monkeypatch.setattr(type(live_app), "is_recording", property(lambda app: False))
        for _ in range(20):
            live_app.root.update()
        assert downloads == [], "a cancelled update never starts the setup"

    def test_cancel_during_the_download(self, live_app, downloads, monkeypatch):
        monkeypatch.setattr(update_notice.threading, "Thread", no_thread)
        live_app.updates.install()
        live_app.root.update()
        live_app.updates.cancel()
        assert FakeDownload.made[-1].cancelled

    @pytest.mark.parametrize(
        ("reason", "text"),
        [
            (update_install.CHECKSUM, "Die Prüfsumme stimmt nicht · nichts installiert"),
            (update_install.NO_CHECKSUM, "Zum Update fehlt die Prüfsumme · nichts installiert"),
            (update_install.FOREIGN_URL, "Die Download-Adresse gehört nicht zu Hams · nichts installiert"),
            (update_install.DOWNLOAD, "Download fehlgeschlagen · erneut versuchen"),
            (update_install.CANCELLED, "Update abgebrochen"),
        ],
    )
    def test_refusals_are_said_and_nothing_starts(self, open_settings, live_app, downloads, reason, text):  # noqa: F811
        general = open_settings().general
        FakeDownload.result = reason
        live_app.updates.install()
        live_app.root.update()
        assert live_app.updates.phase == update_notice.FAILED
        assert general.row_update.sub_text == text
        assert downloads == []
        assert "Jetzt aktualisieren" in shown(general), "the same button tries again"

    def test_an_unexpected_error_is_a_download_error(self, live_app, downloads):
        FakeDownload.result = RuntimeError("kaputt")
        live_app.updates.install()
        live_app.root.update()
        assert live_app.updates.error == update_install.DOWNLOAD

    def test_a_setup_that_does_not_start(self, live_app, downloads, monkeypatch):
        def broken(setup, background):
            raise update_install.UpdateError(update_install.LAUNCH)

        monkeypatch.setattr(app_mod.update_install, "launch_setup", broken)
        live_app.updates.install()
        live_app.root.update()
        assert live_app.updates.error == update_install.LAUNCH
        assert "quit" not in downloads

    def test_without_one_click_the_entry_opens_the_page(self, live_app, checker, monkeypatch):
        opened = []
        monkeypatch.setattr(update_notice.UpdateNotice, "can_install", staticmethod(lambda: False))
        monkeypatch.setattr(update_notice.webbrowser, "open", opened.append)
        live_app.updates.available = RELEASE
        live_app.updates.install()
        live_app.root.update()
        assert opened == [PAGE]
        assert live_app.updates.phase == update_notice.IDLE

    def test_nothing_to_install(self, live_app, downloads):
        live_app.updates.available = None
        live_app.updates.install()
        live_app.root.update()
        assert FakeDownload.made == []

    def test_late_results_of_an_old_download_are_ignored(self, live_app, downloads):
        stale = SimpleNamespace()
        live_app.updates._stopped(stale, update_install.DOWNLOAD)
        live_app.updates._downloaded(stale, "x")
        assert live_app.updates.phase == update_notice.IDLE
        live_app.updates._show_progress(1, 2)
        assert live_app.updates.progress == (0, 0)

    def test_tray_entry_updates_from_the_general_page(self, open_settings, live_app, downloads):  # noqa: F811
        items = live_app._build_tray_menu().items
        entry = next(item for item in items if item is not pystray.Menu.SEPARATOR and item.text.startswith("Jetzt"))
        entry(None)
        for _ in range(5):
            live_app.root.update()
        win = live_app.settings_win
        assert win.page == win.sections.index(win.general)
        assert downloads[-1] == "quit"

    def test_dismiss_is_ignored_while_an_update_runs(self, live_app, checker):
        live_app.updates.available = RELEASE
        live_app.updates.phase = update_notice.DOWNLOADING
        live_app.updates.dismiss()
        assert live_app.updates.available == RELEASE
