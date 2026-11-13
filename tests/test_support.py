"""Hilfe in der App: Doku-Links, Log-Ordner, Diagnosebericht (support.py, settings_about.py, Tray)."""

from datetime import datetime
from types import SimpleNamespace

import kit as uk
import pytest

from hams import settings_about, support, updates


class InlineThread:
    def __init__(self, target, **_options):
        self.target = target

    def start(self) -> None:
        self.target()


def ok_check():
    return "fine"


def broken_check():
    raise RuntimeError("kaputt")


class TestLinks:
    def test_the_links_point_at_the_docs_on_github(self):
        assert support.USER_GUIDE_URL.startswith("https://github.com/")
        assert support.USER_GUIDE_URL.endswith("docs/user-guide.md")
        assert support.TROUBLESHOOTING_URL.endswith("docs/troubleshooting.md")

    def test_open_url_hands_the_link_to_the_browser(self):
        opened = []
        assert support.open_url("https://example.org", opener=opened.append)
        assert opened == ["https://example.org"]

    def test_a_missing_browser_is_logged_not_raised(self):
        def fail(_url):
            raise OSError("no browser")

        assert not support.open_url("https://example.org", opener=fail)

    @pytest.mark.parametrize(
        ("function", "url"),
        [
            (support.open_user_guide, support.USER_GUIDE_URL),
            (support.open_troubleshooting, support.TROUBLESHOOTING_URL),
        ],
    )
    def test_the_shortcuts_take_the_tray_arguments(self, monkeypatch, function, url):
        opened = []
        monkeypatch.setattr(support.webbrowser, "open", opened.append)
        assert function("icon", "item")
        assert opened == [url]


class TestOpenPath:
    def test_windows_uses_the_given_launcher(self, tmp_path):
        launched = []
        support.open_path(tmp_path, platform="win32", launch=launched.append)
        assert launched == [str(tmp_path)]

    def test_linux_uses_xdg_open(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(support.subprocess, "Popen", lambda args, **_kw: calls.append(args))
        support.open_path(tmp_path, platform="linux")
        assert calls == [["xdg-open", str(tmp_path)]]

    def test_the_log_folder_is_created_and_opened(self, monkeypatch, tmp_path):
        folder = tmp_path / "logs"
        monkeypatch.setattr(support, "LOG_DIR", folder)
        launched = []
        assert support.open_log_folder(launch=launched.append) == folder
        assert folder.is_dir()
        assert launched == [str(folder)]


class TestReport:
    def test_the_report_lives_in_the_data_folder(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HAMS_DATA_DIR", str(tmp_path))
        assert support.report_path() == tmp_path / "hams-report.txt"

    def test_the_app_skips_the_tk_check(self):
        names = [name for name, _check in support.in_app_checks()]
        assert "tk" not in names
        assert "backend" in names

    def test_writes_the_checks_and_the_end_of_the_log(self, monkeypatch, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "hams.log").write_text("\n".join(f"line {i}" for i in range(400)), encoding="utf-8")
        monkeypatch.setattr(support, "LOG_DIR", logs)
        target = tmp_path / "out" / "report.txt"
        path, failures = support.write_report(
            target, checks=(("one", ok_check), ("two", broken_check)), now=datetime(2026, 11, 13, 9, 41)
        )
        text = target.read_text(encoding="utf-8")
        assert path == target
        assert failures == 1
        assert f"Hams {updates.app_version()} diagnostic report, 2026-11-13T09:41:00" in text
        assert "ok   one: fine" in text
        assert "FAIL two: RuntimeError: kaputt" in text
        assert f"--- last {support.LOG_TAIL_LINES} lines of hams.log ---" in text
        assert "line 399" in text
        assert "line 249\n" not in text

    def test_without_a_log_the_report_still_works(self, monkeypatch, tmp_path):
        monkeypatch.setattr(support, "LOG_DIR", tmp_path / "missing")
        target = tmp_path / "report.txt"
        _path, failures = support.write_report(target, checks=(("one", ok_check),))
        assert failures == 0
        assert "--- last 0 lines of hams.log ---" in target.read_text(encoding="utf-8")

    def test_the_default_runs_the_in_app_checks(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HAMS_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(support, "LOG_DIR", tmp_path)
        monkeypatch.setattr(support, "in_app_checks", lambda: (("one", ok_check),))
        path, failures = support.write_report()
        assert path == tmp_path / "hams-report.txt"
        assert failures == 0


class TestReportHint:
    def test_names_the_file(self):
        hint = settings_about.AboutSectionMixin.report_hint("C:/x/hams-report.txt", 0, True)
        assert hint == "Geöffnet · hams-report.txt", "the file name, not a full path that wraps"

    def test_says_so_when_checks_failed(self):
        assert "2 Prüfung" in settings_about.AboutSectionMixin.report_hint("r.txt", 2, True)

    def test_says_saved_when_no_editor_opened(self):
        assert settings_about.AboutSectionMixin.report_hint("r.txt", 0, False).startswith("Gespeichert")


class TestTray:
    def test_the_menu_has_a_help_entry_that_opens_the_user_guide(self, live_app, monkeypatch):
        opened = []
        monkeypatch.setattr(support.webbrowser, "open", opened.append)
        menu = live_app._build_tray_menu()
        help_item = next(item for item in menu.items if item.text == "Hilfe …")
        help_item(None)
        assert opened == [support.USER_GUIDE_URL]


@pytest.fixture
def settings(live_app, monkeypatch):
    monkeypatch.setattr(settings_about, "threading", SimpleNamespace(Thread=InlineThread))

    def _open():
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win

    return _open


class TestAboutGroup:
    def test_shows_the_version(self, settings):
        win = settings()
        assert win.row_version.value_label.cget("text") == f"Hams {updates.app_version()}"

    def test_the_buttons_open_the_docs(self, settings, monkeypatch):
        opened = []
        monkeypatch.setattr(support.webbrowser, "open", opened.append)
        win = settings()
        win.guide_button._invoke()
        win.troubleshooting_button._invoke()
        assert opened == [support.USER_GUIDE_URL, support.TROUBLESHOOTING_URL]

    def test_opens_the_log_folder(self, settings, monkeypatch):
        opened = []
        monkeypatch.setattr(support, "open_log_folder", lambda: opened.append(True))
        win = settings()
        win.logs_button._invoke()
        assert opened == [True]

    def test_names_the_log_folder_when_it_cannot_be_opened(self, settings, monkeypatch, tmp_path):
        def fail():
            raise OSError("kein Explorer")

        monkeypatch.setattr(support, "open_log_folder", fail)
        monkeypatch.setattr(support, "LOG_DIR", tmp_path)
        win = settings()
        win._open_logs()
        assert str(tmp_path) in win.row_logs._sub_text

    def test_creates_and_opens_the_report(self, settings, live_app, monkeypatch, tmp_path):
        report = tmp_path / "hams-report.txt"
        opened = []
        monkeypatch.setattr(support, "write_report", lambda: (report, 0))
        monkeypatch.setattr(support, "open_path", opened.append)
        win = settings()
        win.report_button._invoke()
        assert "wird erstellt" in win.row_report._sub_text
        live_app._ui_dispatcher.drain()
        live_app.root.update()
        assert opened == [report]
        assert win.row_report._sub_text == "Geöffnet · hams-report.txt"
        assert not win._report_running

    def test_a_second_click_while_running_does_nothing(self, settings, monkeypatch):
        calls = []
        monkeypatch.setattr(
            settings_about,
            "threading",
            SimpleNamespace(Thread=lambda **kw: calls.append(kw) or InlineThread(lambda: None)),
        )
        win = settings()
        win._create_report()
        win._create_report()
        assert len(calls) == 1

    def test_a_failed_report_says_so(self, settings, live_app, monkeypatch):
        def fail():
            raise OSError("voll")

        monkeypatch.setattr(support, "write_report", fail)
        win = settings()
        win._create_report()
        live_app._ui_dispatcher.drain()
        live_app.root.update()
        assert "nicht schreiben" in win.row_report._sub_text

    def test_an_editor_that_does_not_start_leaves_the_path(self, settings, monkeypatch, tmp_path):
        def fail(_path):
            raise OSError("kein Editor")

        monkeypatch.setattr(support, "open_path", fail)
        win = settings()
        win._report_finished(tmp_path / "r.txt", 0)
        assert win.row_report._sub_text.startswith("Gespeichert")

    def test_a_closed_window_still_opens_the_report(self, settings, monkeypatch, tmp_path):
        opened = []
        monkeypatch.setattr(support, "open_path", opened.append)
        win = settings()
        win.close()
        win._report_finished(tmp_path / "r.txt", 0)
        win._report_finished(None, 0)
        assert opened == [tmp_path / "r.txt"]


class TestReportRowDetails:
    """Third review: the row named a full path that wrapped over several lines, and "Prüfung(en)"."""

    def test_one_and_several_failed_checks_are_pluralised(self):
        hint = settings_about.AboutSectionMixin.report_hint
        assert hint("r.txt", 1, True) == "1 Prüfung fehlgeschlagen · gespeichert als r.txt"
        assert hint("r.txt", 3, True) == "3 Prüfungen fehlgeschlagen · gespeichert als r.txt"

    def test_open_is_greyed_out_until_a_report_exists_then_opens_it_again(self, settings, monkeypatch, tmp_path):
        opened = []
        monkeypatch.setattr(support, "open_path", opened.append)
        win = settings()
        assert win.report_open_button.has_state(uk.State.DISABLED)
        win._open_last_report()  # nothing yet: nothing happens
        assert opened == []
        report = tmp_path / "hams-report.txt"
        win._report_finished(report, 0)
        assert not win.report_open_button.has_state(uk.State.DISABLED)
        win.report_open_button._invoke()
        assert opened == [report, report]

    def test_open_says_saved_when_the_editor_does_not_start(self, settings, monkeypatch, tmp_path):
        def fail(_path):
            raise OSError("kein Editor")

        monkeypatch.setattr(support, "open_path", fail)
        win = settings()
        win._report_finished(tmp_path / "hams-report.txt", 0)
        win._open_last_report()
        assert win.row_report._sub_text == "Gespeichert als hams-report.txt"
