"""One-click update (hams/update_install.py): checksum, download, refusals, the silent setup. No real network.

The whole chain against a real Inno Setup build (wait for the quitting Hams, install, restart entry) was run by
hand on a renamed test build of packaging/hams.iss; see the 1.3.4 section of CHANGELOG.md.
"""

import hashlib
import io
import subprocess
import sys
import urllib.error

import pytest

from hams import app_paths, update_install, updates
from hams.update_install import Download, UpdateError
from hams.updates import Release

SETUP = b"MZ fake setup " * 5000
DIGEST = hashlib.sha256(SETUP).hexdigest()
BASE = updates.DOWNLOAD_PREFIX + "v9.0.0/"
NAME = "Hams-Setup-9.0.0.exe"


class Response(io.BytesIO):
    def __init__(self, body: bytes, length: bool = True):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))} if length else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def release(**overrides) -> Release:
    values = {"setup_url": BASE + NAME, "checksum_url": BASE + NAME + ".sha256"}
    values.update(overrides)
    return Release("9.0.0", updates.RELEASES_PAGE + "/tag/v9.0.0", **values)


def server(files: dict, requests: list | None = None):
    """An opener that answers from `files` (url -> bytes or an exception)."""

    def opener(request, timeout):
        if requests is not None:
            requests.append(request.full_url)
        answer = files.get(request.full_url)
        if answer is None:
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)
        if isinstance(answer, Exception):
            raise answer
        return Response(answer)

    return opener


def good_files(checksum: str | None = None) -> dict:
    return {BASE + NAME: SETUP, BASE + NAME + ".sha256": (checksum or f"{DIGEST}  {NAME}\n").encode()}


class TestChecksumFile:
    @pytest.mark.parametrize(
        "text",
        [f"{DIGEST}  {NAME}\n", f"{DIGEST} *{NAME}", DIGEST, DIGEST.upper() + "\n", f"junk\n{DIGEST}  {NAME}\n"],
    )
    def test_formats(self, text):
        assert update_install.parse_checksum(text, NAME) == DIGEST

    @pytest.mark.parametrize("text", ["", "not a hash", f"{DIGEST}  Other-Setup.exe", DIGEST[:-1]])
    def test_nothing_usable(self, text):
        assert update_install.parse_checksum(text, NAME) == ""


class TestDownload:
    def test_verified_setup_lands_in_the_folder(self, tmp_path):
        progress, requests = [], []
        (tmp_path / "Hams-Setup-8.0.0.exe").write_bytes(b"old")
        path = Download(
            release(), folder=tmp_path, opener=server(good_files(), requests), progress=lambda *p: progress.append(p)
        ).run()
        assert path == tmp_path / NAME and path.read_bytes() == SETUP
        assert sorted(p.name for p in tmp_path.iterdir()) == [NAME], "older downloads go, no .part is left"
        assert requests[0].endswith(".sha256"), "the checksum comes first: without it nothing is downloaded"
        assert progress[0] == (0, len(SETUP)) and progress[-1] == (len(SETUP), len(SETUP))

    def test_unknown_length_still_downloads(self, tmp_path):
        files = good_files()

        def opener(request, timeout):
            return Response(files[request.full_url], length=False)

        assert Download(release(), folder=tmp_path, opener=opener).run().read_bytes() == SETUP

    def test_a_wrong_checksum_installs_nothing(self, tmp_path, caplog):
        with pytest.raises(UpdateError) as error:
            Download(release(), folder=tmp_path, opener=server(good_files("0" * 64))).run()
        assert error.value.reason == update_install.CHECKSUM
        assert list(tmp_path.iterdir()) == []
        assert "checksum mismatch" in caplog.text

    @pytest.mark.parametrize("checksum_url", [BASE + NAME + ".sha256", ""])
    def test_a_missing_checksum_downloads_nothing(self, tmp_path, checksum_url):
        requests = []
        files = {BASE + NAME: SETUP}
        with pytest.raises(UpdateError) as error:
            Download(release(checksum_url=checksum_url), folder=tmp_path, opener=server(files, requests)).run()
        assert error.value.reason == update_install.NO_CHECKSUM
        assert BASE + NAME not in requests

    @pytest.mark.parametrize("text", [b"no hash here", b"x" * 5000])
    def test_an_unusable_checksum_file(self, tmp_path, text):
        files = {BASE + NAME: SETUP, BASE + NAME + ".sha256": text}
        with pytest.raises(UpdateError) as error:
            Download(release(), folder=tmp_path, opener=server(files)).run()
        assert error.value.reason == update_install.NO_CHECKSUM

    @pytest.mark.parametrize(
        "overrides",
        [
            {"setup_url": "https://evil.example/Hams-Setup-9.0.0.exe"},
            {"checksum_url": "https://evil.example/Hams-Setup-9.0.0.exe.sha256"},
            {"setup_url": "http://github.com/Mosaik114/hams-bachelorprojekt/releases/download/v9/" + NAME},
            {"setup_url": BASE + "../../../other/" + NAME},
            {"setup_url": BASE + NAME + "?redirect=evil"},
        ],
    )
    def test_foreign_addresses_are_refused(self, tmp_path, overrides):
        requests = []
        with pytest.raises(UpdateError) as error:
            Download(release(**overrides), folder=tmp_path, opener=server(good_files(), requests)).run()
        assert error.value.reason == update_install.FOREIGN_URL
        assert requests == [], "nothing is fetched from anywhere else"

    def test_a_release_without_setup(self, tmp_path):
        for overrides in ({"setup_url": "", "checksum_url": ""}, {"setup_url": ""}):
            with pytest.raises(UpdateError) as error:
                Download(release(**overrides), folder=tmp_path, opener=server(good_files())).run()
            assert error.value.reason == update_install.NO_SETUP

    @pytest.mark.parametrize("problem", [urllib.error.URLError("offline"), ConnectionResetError(), TimeoutError()])
    def test_download_errors(self, tmp_path, problem):
        files = good_files()
        files[BASE + NAME] = problem
        with pytest.raises(UpdateError) as error:
            Download(release(), folder=tmp_path, opener=server(files)).run()
        assert error.value.reason == update_install.DOWNLOAD
        assert list(tmp_path.iterdir()) == []

    def test_a_broken_answer_is_a_download_error(self, tmp_path):
        files = good_files()

        def opener(request, timeout):
            if request.full_url.endswith(".exe"):
                raise ValueError("broken")
            return Response(files[request.full_url])

        with pytest.raises(UpdateError) as error:
            Download(release(), folder=tmp_path, opener=opener).run()
        assert error.value.reason == update_install.DOWNLOAD

    def test_no_permission_or_no_space(self, tmp_path):
        files = good_files()
        files[BASE + NAME] = PermissionError("denied")
        with pytest.raises(UpdateError) as error:
            Download(release(), folder=tmp_path, opener=server(files)).run()
        assert error.value.reason == update_install.DISK
        full = OSError(28, "No space left on device")
        files[BASE + NAME] = full
        with pytest.raises(UpdateError) as error:
            Download(release(), folder=tmp_path, opener=server(files)).run()
        assert error.value.reason == update_install.DISK

    def test_an_unwritable_folder(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        with pytest.raises(UpdateError) as error:
            Download(release(), folder=blocker / "updates", opener=server(good_files())).run()
        assert error.value.reason == update_install.DISK

    def test_cancel_stops_and_keeps_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(update_install, "CHUNK", 1000)
        download = Download(release(), folder=tmp_path, opener=server(good_files()))
        download._progress = lambda done, total: download.cancel() if done > 3000 else None
        with pytest.raises(UpdateError) as error:
            download.run()
        assert error.value.reason == update_install.CANCELLED
        assert list(tmp_path.iterdir()) == []

    def test_cancel_after_the_last_chunk(self, tmp_path):
        download = Download(release(), folder=tmp_path, opener=server(good_files()))
        download._progress = lambda done, total: download.cancel() if done == total and done else None
        with pytest.raises(UpdateError) as error:
            download.run()
        assert error.value.reason == update_install.CANCELLED

    def test_defaults(self):
        download = Download(release())
        assert download.folder == app_paths.local_data_dir() / "updates"
        assert download.target.name == NAME


class TestSetup:
    def test_silent_with_restart(self, tmp_path):
        command = update_install.setup_command(tmp_path / NAME, background=True)
        assert command[0] == str(tmp_path / NAME)
        assert command[1:5] == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RESTARTHAMS=background"]
        assert command[5] == f"/LOG={app_paths.default_log_dir() / 'update-setup.log'}"
        assert "/RESTARTHAMS=window" in update_install.setup_command(tmp_path / NAME, background=False)

    @pytest.mark.skipif(sys.platform != "win32", reason="the creation flags are Win32")
    def test_launch_is_detached(self, tmp_path):
        started = []
        update_install.launch_setup(
            tmp_path / NAME, background=False, popen=lambda cmd, **kw: started.append((cmd, kw))
        )
        command, options = started[0]
        assert command[0] == str(tmp_path / NAME)
        assert options["close_fds"] is True
        expected = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        assert options.get("creationflags", expected) == expected

    def test_a_setup_that_does_not_start(self, tmp_path, caplog):
        def broken(command, **options):
            raise OSError("blocked")

        with pytest.raises(UpdateError) as error:
            update_install.launch_setup(tmp_path / NAME, background=True, popen=broken)
        assert error.value.reason == update_install.LAUNCH

    @pytest.mark.parametrize(
        ("platform", "frozen", "possible"),
        [("win32", True, True), ("win32", False, False), ("linux", True, False)],
    )
    def test_only_the_installed_windows_app(self, platform, frozen, possible):
        assert update_install.can_install(platform, frozen) is possible

    def test_can_install_reads_the_running_app(self, monkeypatch):
        monkeypatch.setattr(app_paths, "is_frozen", lambda: False)
        assert update_install.can_install() is False
