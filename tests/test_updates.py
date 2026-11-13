"""The daily update check (hams/updates.py): versions, the remembered state, the request. No real network."""

import io
import json
import urllib.error
from importlib import metadata

import pytest

from hams import app_paths, updates
from hams.updates import Release, UpdateChecker, UpdateState

DAY = updates.INTERVAL_SECONDS


class TestVersions:
    @pytest.mark.parametrize(
        ("text", "parsed"),
        [
            ("1.2.3", (1, 2, 3, 1)),
            ("v1.2.3", (1, 2, 3, 1)),
            (" v2.0 ", (2, 0, 0, 1)),
            ("1.2.3-rc.1", (1, 2, 3, 0)),
            ("1.2.3+build.7", (1, 2, 3, 1)),
            ("10.20.30", (10, 20, 30, 1)),
        ],
    )
    def test_parse(self, text, parsed):
        assert updates.parse_version(text) == parsed

    @pytest.mark.parametrize("text", ["", "latest", "1", "v1.x", "1.2.3.4", None])
    def test_unparsable(self, text):
        assert updates.parse_version(text) is None

    @pytest.mark.parametrize(
        ("candidate", "current", "newer"),
        [
            ("1.0.1", "1.0.0", True),
            ("1.10.0", "1.9.9", True),
            ("v2.0.0", "1.99.99", True),
            ("1.0.0", "1.0.0", False),
            ("0.9.0", "1.0.0", False),
            ("1.1.0-rc.1", "1.0.0", True),
            ("1.1.0-rc.1", "1.1.0", False),
            ("1.1.0", "1.1.0-rc.1", True),
            ("garbage", "1.0.0", False),
            ("1.0.1", "unknown", False),
        ],
    )
    def test_is_newer(self, candidate, current, newer):
        assert updates.is_newer(candidate, current) is newer

    def test_clean_version(self):
        assert updates.clean_version(" v1.2.0 ") == "1.2.0"

    def test_only_pages_of_this_repository_are_opened(self):
        own = updates.RELEASES_PAGE + "/tag/v1.2.0"
        assert updates.release_url(own) == own
        assert updates.release_url("https://example.com/hams/releases/tag/v9") == updates.RELEASES_PAGE
        assert updates.release_url(updates.RELEASES_PAGE + "evil") == updates.RELEASES_PAGE
        assert updates.release_url(None) == updates.RELEASES_PAGE


class TestAppVersion:
    def test_comes_from_the_package_metadata(self):
        updates.app_version.cache_clear()
        try:
            assert updates.app_version() == metadata.version("hams")
            assert updates.parse_version(updates.app_version()) is not None
        finally:
            updates.app_version.cache_clear()

    def test_without_metadata(self, monkeypatch):
        def missing(_name):
            raise metadata.PackageNotFoundError("hams")

        monkeypatch.setattr(updates.metadata, "version", missing)
        updates.app_version.cache_clear()
        try:
            assert updates.app_version() == "0.0.0"
        finally:
            updates.app_version.cache_clear()


class TestState:
    def test_lives_in_the_data_folder(self):
        assert updates.state_path() == app_paths.local_data_dir() / "update-check.json"

    def test_round_trip(self, tmp_path):
        path = tmp_path / "sub" / "state.json"
        state = UpdateState(last_check=5.0, latest="1.2.0", url="u", dismissed="1.1.0", notified="1.2.0")
        updates.save_state(path, state)
        assert updates.load_state(path) == state

    def test_missing_file_is_a_fresh_start(self, tmp_path):
        assert updates.load_state(tmp_path / "none.json") == UpdateState()

    @pytest.mark.parametrize("content", ["{broken", "[1, 2]", ""])
    def test_unreadable_file_is_a_fresh_start(self, tmp_path, content):
        path = tmp_path / "state.json"
        path.write_text(content, encoding="utf-8")
        assert updates.load_state(path) == UpdateState()

    def test_wrong_types_are_ignored_field_by_field(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"last_check": 7, "latest": 3, "dismissed": "1.0.1", "extra": 1}), encoding="utf-8")
        state = updates.load_state(path)
        assert state == UpdateState(last_check=7, latest="", dismissed="1.0.1")

    def test_a_failed_write_is_only_logged(self, tmp_path, caplog):
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        updates.save_state(blocker / "state.json", UpdateState())
        assert "could not write" in caplog.text

    @pytest.mark.parametrize(
        ("last", "now", "due"),
        [
            (0.0, float(DAY), True),
            (1000.0, 1000.0 + DAY - 1, False),
            (1000.0, 1000.0 + DAY, True),
            (5000.0, 10.0, True),
        ],
    )
    def test_due_at_most_once_a_day(self, last, now, due):
        assert UpdateState(last_check=last).due(now) is due

    def test_pending(self):
        state = UpdateState(latest="1.2.0", url=updates.RELEASES_PAGE + "/tag/v1.2.0")
        assert state.pending("1.1.0") == Release("1.2.0", updates.RELEASES_PAGE + "/tag/v1.2.0")
        assert state.pending("1.2.0") is None
        state.dismissed = "1.2.0"
        assert state.pending("1.1.0") is None
        assert UpdateState().pending("1.0.0") is None


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_returning(payload, requests=None):
    def opener(request, timeout):
        if requests is not None:
            requests.append((request, timeout))
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return FakeResponse(body)

    return opener


class TestFetch:
    def test_reads_tag_and_page(self):
        requests = []
        page = updates.RELEASES_PAGE + "/tag/v1.3.0"
        release = updates.fetch_latest(opener_returning({"tag_name": "v1.3.0", "html_url": page}, requests))
        assert release == Release("1.3.0", page)
        request, timeout = requests[0]
        assert request.full_url == "https://api.github.com/repos/Mosaik114/hams-bachelorprojekt/releases/latest"
        assert request.get_header("User-agent") == "Hams-update-check"
        assert timeout == updates.TIMEOUT_SECONDS

    def test_a_foreign_page_is_replaced(self):
        release = updates.fetch_latest(opener_returning({"tag_name": "1.3.0", "html_url": "https://evil.example"}))
        assert release.url == updates.RELEASES_PAGE

    @pytest.mark.parametrize(
        "error",
        [
            urllib.error.URLError("offline"),
            TimeoutError(),
            urllib.error.HTTPError(updates.RELEASES_API, 403, "rate limited", {}, None),
            ConnectionResetError(),
        ],
    )
    def test_network_problems_are_silent(self, error, caplog):
        def opener(request, timeout):
            raise error

        assert updates.fetch_latest(opener) is None
        assert "Update check failed" in caplog.text

    @pytest.mark.parametrize("payload", [b"not json", {"name": "x"}, {"tag_name": "nightly"}, [1], {"tag_name": 3}])
    def test_unusable_answers(self, payload):
        assert updates.fetch_latest(opener_returning(payload)) is None


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class TestChecker:
    @pytest.fixture
    def setup(self, tmp_path):
        calls = []
        answer = {"release": Release("1.2.0", updates.RELEASES_PAGE + "/tag/v1.2.0")}

        def fetch():
            calls.append(1)
            return answer["release"]

        clock = Clock()
        checker = UpdateChecker(tmp_path / "state.json", fetch=fetch, clock=clock, current="1.1.0")
        return checker, calls, answer, clock

    def test_asks_once_and_remembers(self, setup):
        checker, calls, _answer, clock = setup
        assert checker.run() == Release("1.2.0", updates.RELEASES_PAGE + "/tag/v1.2.0")
        clock.now += 60
        assert checker.run().version == "1.2.0", "from the remembered answer"
        assert len(calls) == 1
        clock.now += DAY
        checker.run()
        assert len(calls) == 2

    def test_nothing_newer(self, setup):
        checker, _calls, answer, _clock = setup
        answer["release"] = Release("1.1.0", updates.RELEASES_PAGE)
        assert checker.run() is None

    def test_a_failed_attempt_also_waits_a_day(self, setup):
        checker, calls, answer, clock = setup
        answer["release"] = None
        assert checker.run() is None
        clock.now += 3600
        checker.run()
        assert len(calls) == 1
        assert updates.load_state(checker.path).last_check == 1_000_000.0

    def test_dismissed_versions_stay_quiet_until_a_newer_one(self, setup):
        checker, _calls, answer, clock = setup
        checker.run()
        checker.dismiss("1.2.0")
        assert checker.run() is None
        answer["release"] = Release("1.3.0", updates.RELEASES_PAGE)
        clock.now += DAY
        assert checker.run().version == "1.3.0"

    def test_the_tray_announces_each_version_once(self, setup):
        checker = setup[0]
        assert checker.first_notice("1.2.0") is True
        assert checker.first_notice("1.2.0") is False
        assert checker.first_notice("1.3.0") is True

    def test_defaults(self):
        checker = UpdateChecker()
        assert checker.path == updates.state_path()
        assert checker.current == updates.app_version()
