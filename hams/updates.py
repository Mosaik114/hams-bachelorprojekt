"""Update check: at most once a day (or when asked), ask GitHub whether a newer Hams was released.

The check asks one public endpoint (`RELEASES_API`, no account, no identifier
beyond what any HTTPS request carries) in a background thread with a short
timeout. Offline or any error stays silent and only goes to the log. `[updates]
check = false` in config.toml (or the switch in Settings → General) turns the
daily check off; "Check for updates now" still asks when clicked.

Nothing is downloaded on its own. The answer also names the release's setup file
and its `.sha256` file (`Release.setup_url`, `Release.checksum_url`); only the
user's "Update now" downloads them, see `update_install.py`.

The time of the last attempt, the newest version seen and the version whose
notice was dismissed live in a small JSON file in the data folder
(`app_paths.local_data_dir()`), so a restart doesn't ask GitHub again.

Everything that touches the outside (clock, network, file) is passed in, so
the tests run without a network.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib import metadata
from pathlib import Path

from hams import app_paths

logger = logging.getLogger("hams")

REPOSITORY = "Mosaik114/hams-bachelorprojekt"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
#: Fallback, and the only kind of page a notice may open (see `release_url`).
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
INTERVAL_SECONDS = 24 * 60 * 60
TIMEOUT_SECONDS = 5
STATE_FILE = "update-check.json"
#: Generic client name; nothing about the user or the machine.
USER_AGENT = "Hams-update-check"

#: The only place a setup file or its checksum may come from (see `release_asset`).
DOWNLOAD_PREFIX = f"https://github.com/{REPOSITORY}/releases/download/"

_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


@lru_cache(maxsize=1)
def app_version() -> str:
    """The running version. The frozen app bundles the package metadata (see packaging/hams.spec)."""
    try:
        return metadata.version("hams")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def parse_version(text: str) -> tuple[int, int, int, int] | None:
    """ "v1.2.3" -> (1, 2, 3, 1). The last field is 0 for a pre-release, which sorts before the release."""
    match = _VERSION.match(str(text or "").strip())
    if match is None:
        return None
    major, minor, patch, pre = match.groups()
    return int(major), int(minor), int(patch or 0), 0 if pre else 1


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


def clean_version(tag: str) -> str:
    """The tag without its leading "v", as it is shown."""
    return str(tag).strip().removeprefix("v")


def release_url(url: object) -> str:
    """The release page from the answer, but only if it belongs to this repository."""
    if isinstance(url, str) and url.startswith(RELEASES_PAGE + "/"):
        return url
    return RELEASES_PAGE


def setup_name(version: str) -> str:
    """The setup file a release carries, as the release workflow names it (packaging/hams.iss)."""
    return f"Hams-Setup-{version}.exe"


def checksum_name(version: str) -> str:
    """The checksum file next to it: `sha256sum` output for the setup file."""
    return setup_name(version) + ".sha256"


def release_asset(url: object) -> str:
    """`url` if it lies under this repository's release downloads, else "": nothing else is ever downloaded."""
    if isinstance(url, str) and url.startswith(DOWNLOAD_PREFIX) and ".." not in url and "?" not in url:
        return url
    return ""


@dataclass
class Release:
    version: str
    url: str
    #: Where the setup file and its checksum file are, if the release has them ("" otherwise).
    setup_url: str = ""
    checksum_url: str = ""


@dataclass
class UpdateState:
    """What `STATE_FILE` remembers between starts."""

    last_check: float = 0.0
    latest: str = ""
    url: str = ""
    dismissed: str = ""
    notified: str = ""
    setup_url: str = ""
    checksum_url: str = ""

    def due(self, now: float) -> bool:
        """At most once per interval; a clock that went backwards counts as due."""
        return now - self.last_check >= INTERVAL_SECONDS or now < self.last_check

    def pending(self, current: str) -> Release | None:
        """The release to point out: newer than `current` and not dismissed."""
        if not is_newer(self.latest, current) or self.latest == self.dismissed:
            return None
        return Release(self.latest, release_url(self.url), self.setup_url, self.checksum_url)

    def remember(self, release: Release) -> None:
        self.latest, self.url = release.version, release.url
        self.setup_url, self.checksum_url = release.setup_url, release.checksum_url


def state_path() -> Path:
    return app_paths.local_data_dir() / STATE_FILE


def load_state(path: Path) -> UpdateState:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return UpdateState()
    except (OSError, ValueError):
        logger.warning("Update check: unreadable %s, starting fresh", path.name)
        return UpdateState()
    if not isinstance(data, dict):
        return UpdateState()
    state = UpdateState()
    for name, default in asdict(state).items():
        value = data.get(name, default)
        if isinstance(value, type(default)) or (isinstance(default, float) and isinstance(value, int)):
            setattr(state, name, value)
    return state


def save_state(path: Path, state: UpdateState) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    except OSError:
        logger.warning("Update check: could not write %s", path.name)


def fetch_latest(opener: Callable = urllib.request.urlopen, timeout: float = TIMEOUT_SECONDS) -> Release | None:
    """Asks GitHub for the latest release. None on any problem (logged, never raised)."""
    request = urllib.request.Request(
        RELEASES_API, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    )
    try:
        with opener(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # offline, timeout, HTTP error, broken JSON: all the same to the user
        logger.info("Update check failed: %s", type(exc).__name__)
        return None
    tag = data.get("tag_name") if isinstance(data, dict) else None
    if not isinstance(tag, str) or parse_version(tag) is None:
        logger.info("Update check: no usable version in the answer")
        return None
    version = clean_version(tag)
    setup_url, checksum_url = _assets(data.get("assets"), version)
    return Release(version, release_url(data.get("html_url")), setup_url, checksum_url)


def _assets(assets: object, version: str) -> tuple[str, str]:
    """The download addresses of the setup file and its checksum ("" if missing); `update_install` checks them."""
    found = {}
    for asset in assets if isinstance(assets, list) else []:
        if isinstance(asset, dict) and isinstance(asset.get("name"), str):
            url = asset.get("browser_download_url")
            found[asset["name"]] = url if isinstance(url, str) else ""
    return found.get(setup_name(version), ""), found.get(checksum_name(version), "")


#: Results of `UpdateChecker.check_now`.
LATEST, AVAILABLE, FAILED = "latest", "available", "failed"


class UpdateChecker:
    """Decides whether to ask, asks, and remembers the answer."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        fetch: Callable[[], Release | None] = fetch_latest,
        clock: Callable[[], float] = time.time,
        current: str | None = None,
    ) -> None:
        self.path = path or state_path()
        self._fetch = fetch
        self._clock = clock
        self.current = current or app_version()

    def run(self) -> Release | None:
        """The release worth a notice, asking GitHub only when the last attempt is a day old."""
        state = load_state(self.path)
        now = self._clock()
        if state.due(now):
            self._ask(state, now)
        return state.pending(self.current)

    def check_now(self) -> tuple[str, Release | None]:
        """Asks GitHub right away, whatever the daily rhythm says ("Check for updates now").

        Returns `(result, release)`: `LATEST` (nothing newer), `AVAILABLE` with the newer release (also when its
        notice was dismissed before: whoever asks wants to know), or `FAILED` (offline, no usable answer).
        """
        state = load_state(self.path)
        release = self._ask(state, self._clock())
        if release is None:
            return FAILED, None
        if not is_newer(release.version, self.current):
            return LATEST, None
        state.dismissed = ""
        save_state(self.path, state)
        return AVAILABLE, state.pending(self.current)

    def _ask(self, state: UpdateState, now: float) -> Release | None:
        state.last_check = now  # also on failure: at most one automatic attempt a day
        release = self._fetch()
        if release is not None:
            state.remember(release)
            logger.info("Update check: latest release %s, running %s", release.version, self.current)
        save_state(self.path, state)
        return release

    def dismiss(self, version: str) -> None:
        state = load_state(self.path)
        state.dismissed = version
        save_state(self.path, state)

    def first_notice(self, version: str) -> bool:
        """True exactly once per version: whether the tray may announce it."""
        state = load_state(self.path)
        if state.notified == version:
            return False
        state.notified = version
        save_state(self.path, state)
        return True
