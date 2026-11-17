"""One-click update: download the release's setup file, check it, run it silently.

Only on the user's "Update now" (settings row or tray entry); the daily check in `updates.py` never downloads. The
steps, each of which refuses rather than guesses:

1. Both addresses must lie under this repository's release downloads (`updates.DOWNLOAD_PREFIX`). An answer that
   points anywhere else installs nothing (`FOREIGN_URL`).
2. The checksum file comes first. Without one, or without a SHA-256 for exactly this setup file in it, nothing is
   downloaded (`NO_CHECKSUM`). Every release publishes `Hams-Setup-<version>.exe.sha256` next to the setup
   (.github/workflows/ci.yml).
3. The setup file is streamed into the data folder (`app_paths.local_data_dir() / "updates"`) as a `.part` file,
   hashed on the way. Only if the hash matches does it get its real name (`CHECKSUM`); a mismatch deletes it.
4. `launch_setup` starts it detached with `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` plus `/RESTARTHAMS=...`.
   The setup waits until this Hams has quit (its single-instance mutex is gone, see packaging/hams.iss), replaces
   the files and starts Hams again; the app quits right after launching it (`app.install_update`).

Network, files and processes are passed in, so the tests run without GitHub (tests/test_update_install.py).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Callable
from pathlib import Path

from hams import app_paths, updates

logger = logging.getLogger("hams")

#: Reasons an update stops; each has a message `update.error.<reason>`.
NO_SETUP = "no_setup"
FOREIGN_URL = "foreign_url"
NO_CHECKSUM = "no_checksum"
CHECKSUM = "checksum"
DOWNLOAD = "download"
DISK = "disk"
LAUNCH = "launch"
#: Not an error: the user cancelled.
CANCELLED = "cancelled"

#: Silent install: no wizard, no message boxes, never a Windows restart. `/RESTARTHAMS` starts Hams afterwards.
SETUP_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
TIMEOUT_SECONDS = 30
CHUNK = 256 * 1024
#: A checksum file is one line; anything larger is not one.
MAX_CHECKSUM_BYTES = 4096
_SHA256 = re.compile(r"^([0-9a-fA-F]{64})(?:\s+\*?(\S.*))?$")


class UpdateError(Exception):
    """The update stopped; `reason` is one of the constants above."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def can_install(platform: str | None = None, frozen: bool | None = None) -> bool:
    """One-click updates need the installed Windows app: the setup replaces exactly that installation.

    A source checkout or Linux updates the way it was installed (git, pip, the Linux script).
    """
    platform = sys.platform if platform is None else platform
    frozen = app_paths.is_frozen() if frozen is None else frozen
    return platform == "win32" and frozen


def download_dir() -> Path:
    return app_paths.local_data_dir() / "updates"


def setup_log() -> Path:
    """Where the silent setup writes its log, next to Hams' own."""
    return app_paths.default_log_dir() / "update-setup.log"


def parse_checksum(text: str, file_name: str) -> str:
    """The SHA-256 (lower case) for `file_name` from a `sha256sum`-style file; "" if there is none for it.

    A line may be the bare hash, or the hash and a file name (`<hash>  <name>` or `<hash> *<name>`). A line with
    another file name does not count.
    """
    for line in text.splitlines():
        match = _SHA256.match(line.strip())
        if match is None:
            continue
        digest, name = match.groups()
        if name is None or Path(name.strip()).name == file_name:
            return digest.lower()
    return ""


def _request(url: str) -> urllib.request.Request:
    # Only addresses `updates.release_asset` accepted (https, this repository) get here.
    return urllib.request.Request(url, headers={"User-Agent": updates.USER_AGENT})  # noqa: S310


class Download:
    """Fetches and checks the setup file of one release. `cancel()` may come from any thread."""

    def __init__(
        self,
        release: updates.Release,
        *,
        folder: Path | None = None,
        opener: Callable = urllib.request.urlopen,
        progress: Callable[[int, int], None] = lambda done, total: None,
    ) -> None:
        self.release = release
        self.folder = folder or download_dir()
        self._opener = opener
        self._progress = progress
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def target(self) -> Path:
        return self.folder / updates.setup_name(self.release.version)

    def run(self) -> Path:
        """Downloads and verifies; returns the setup file. Raises `UpdateError` with the reason otherwise."""
        release = self.release
        if not release.setup_url and not release.checksum_url:
            raise UpdateError(NO_SETUP)
        if not updates.release_asset(release.setup_url):
            raise UpdateError(FOREIGN_URL if release.setup_url else NO_SETUP)
        if not updates.release_asset(release.checksum_url):
            raise UpdateError(FOREIGN_URL if release.checksum_url else NO_CHECKSUM)
        expected = self._expected_hash()
        self._clean_folder()
        part = self.target.with_name(self.target.name + ".part")
        try:
            digest = self._fetch(release.setup_url, part)
            if digest != expected:
                logger.warning("Update %s: checksum mismatch, setup deleted", release.version)
                raise UpdateError(CHECKSUM)
            part.replace(self.target)
        finally:
            with contextlib.suppress(OSError):
                part.unlink(missing_ok=True)
        logger.info("Update %s downloaded and verified (SHA-256)", release.version)
        return self.target

    def _expected_hash(self) -> str:
        try:
            with self._opener(_request(self.release.checksum_url), timeout=TIMEOUT_SECONDS) as response:
                text = response.read(MAX_CHECKSUM_BYTES + 1)
        except Exception as exc:  # noqa: BLE001 - offline, 404, timeout: no checksum, no install
            logger.info("Update checksum unavailable: %s", type(exc).__name__)
            raise UpdateError(NO_CHECKSUM) from exc
        if len(text) > MAX_CHECKSUM_BYTES:
            raise UpdateError(NO_CHECKSUM)
        digest = parse_checksum(text.decode("utf-8", "replace"), updates.setup_name(self.release.version))
        if not digest:
            raise UpdateError(NO_CHECKSUM)
        return digest

    def _clean_folder(self) -> None:
        """Earlier downloads go: only one setup file is ever kept."""
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            for old in self.folder.iterdir():
                if old.is_file():
                    old.unlink()
        except OSError as exc:
            raise UpdateError(DISK) from exc

    def _fetch(self, url: str, part: Path) -> str:
        """Streams `url` into `part`, reporting progress; returns its SHA-256."""
        sha = hashlib.sha256()
        try:
            with self._opener(_request(url), timeout=TIMEOUT_SECONDS) as response, part.open("wb") as out:
                total = int(response.headers.get("Content-Length") or 0) if getattr(response, "headers", None) else 0
                done = 0
                self._progress(done, total)
                while chunk := response.read(CHUNK):
                    if self._cancelled.is_set():
                        raise UpdateError(CANCELLED)
                    out.write(chunk)
                    sha.update(chunk)
                    done += len(chunk)
                    self._progress(done, total)
        except UpdateError:
            raise
        except OSError as exc:
            if isinstance(exc, PermissionError) or getattr(exc, "errno", None) == 28:  # ENOSPC
                raise UpdateError(DISK) from exc
            logger.info("Update download failed: %s", type(exc).__name__)
            raise UpdateError(DOWNLOAD) from exc
        except Exception as exc:  # noqa: BLE001 - HTTP errors, broken connections
            logger.info("Update download failed: %s", type(exc).__name__)
            raise UpdateError(DOWNLOAD) from exc
        if self._cancelled.is_set():
            raise UpdateError(CANCELLED)
        return sha.hexdigest()


def setup_command(setup: Path, *, background: bool) -> list[str]:
    """The silent setup; afterwards it starts Hams again, in the tray if it ran there (`--background`)."""
    restart = "background" if background else "window"
    return [str(setup), *SETUP_ARGS, f"/RESTARTHAMS={restart}", f"/LOG={setup_log()}"]


def launch_setup(setup: Path, *, background: bool, popen: Callable = subprocess.Popen) -> None:
    """Starts the setup detached, so it outlives this process. Raises `UpdateError(LAUNCH)` if it does not start."""
    command = setup_command(setup, background=background)
    flags = {}
    if sys.platform == "win32":
        flags["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        setup_log().parent.mkdir(parents=True, exist_ok=True)
        popen(command, close_fds=True, **flags)
    except OSError as exc:
        logger.exception("Update setup could not be started")
        raise UpdateError(LAUNCH) from exc
    logger.info("Update setup started: %s", setup.name)
