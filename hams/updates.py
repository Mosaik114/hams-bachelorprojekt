"""Update notice: at most once a day, ask GitHub whether a newer Hams was released.

Only a notice: Hams never downloads or installs anything on its own. The check
asks one public endpoint (`RELEASES_API`, no account, no identifier beyond
what any HTTPS request carries) in a background thread with a short timeout.
Offline or any error stays silent and only goes to the log. `[updates] check =
false` in config.toml (or the switch in Settings → General) turns it off.

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


@dataclass
class Release:
    version: str
    url: str


@dataclass
class UpdateState:
    """What `STATE_FILE` remembers between starts."""

    last_check: float = 0.0
    latest: str = ""
    url: str = ""
    dismissed: str = ""
    notified: str = ""

    def due(self, now: float) -> bool:
        """At most once per interval; a clock that went backwards counts as due."""
        return now - self.last_check >= INTERVAL_SECONDS or now < self.last_check

    def pending(self, current: str) -> Release | None:
        """The release to point out: newer than `current` and not dismissed."""
        if not is_newer(self.latest, current) or self.latest == self.dismissed:
            return None
        return Release(self.latest, release_url(self.url))


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
    return Release(clean_version(tag), release_url(data.get("html_url")))


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
            state.last_check = now  # also on failure: at most one attempt a day
            release = self._fetch()
            if release is not None:
                state.latest, state.url = release.version, release.url
                logger.info("Update check: latest release %s, running %s", release.version, self.current)
            save_state(self.path, state)
        return state.pending(self.current)

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
