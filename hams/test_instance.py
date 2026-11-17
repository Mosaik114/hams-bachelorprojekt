"""A test instance of Hams: its own folders, lock and events, so a test never meets the user's Hams.

Set by ``HAMS_TEST_INSTANCE=<name>`` or ``--test-instance=<name>`` on the command line. The installer smoke test
(``packaging/smoke_test.ps1``) uses it with a setup built as a test instance (``/DTestInstance=<name>``, see
``packaging/hams.iss``). The test may run on a developer machine where the real Hams is installed and running.

What a test instance changes:

* settings: ``%APPDATA%\\Hams-test-<name>\\config.toml`` instead of ``%APPDATA%\\Hams``
* data, logs, transcripts and the Hugging Face cache: ``%LOCALAPPDATA%\\Hams-test-<name>``
* the single-instance lock and the activation events get ``-test-<name>`` appended

The command-line form exists for the restart through the Windows shell (`redirection_guard`): Explorer starts the
program with its own environment, so only an argument survives the restart.

Only the standard library: this runs before anything else is imported, the configuration included.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

#: The environment variable that names the test instance.
ENV = "HAMS_TEST_INSTANCE"
#: The command-line form, ``--test-instance=<name>``.
ARGUMENT = "--test-instance"

_VALID = re.compile(r"[A-Za-z0-9-]{1,40}")


def name() -> str:
    """The name of the test instance, or "" for the normal Hams. An invalid name counts as none."""
    value = os.environ.get(ENV, "")
    return value if _VALID.fullmatch(value) else ""


def suffix() -> str:
    """What names of locks and events get appended: "" for the normal Hams."""
    current = name()
    return f"-test-{current}" if current else ""


def argument() -> str:
    """The argument that hands the test instance on to a restarted Hams, or "" for the normal Hams."""
    current = name()
    return f"{ARGUMENT}={current}" if current else ""


def _from_argv(argv: list[str]) -> str:
    for index, arg in enumerate(argv):
        if arg.startswith(ARGUMENT + "="):
            return arg.split("=", 1)[1]
        if arg == ARGUMENT and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def apply(argv: list[str] | None = None) -> str:
    """Before any other import of Hams: turns ``--test-instance`` into the environment and points the folders there.

    Variables that are set already (the test suite sets ``HAMS_CONFIG`` and friends itself) are left alone.
    Returns the name of the test instance, or "".
    """
    given = _from_argv(list(sys.argv[1:] if argv is None else argv))
    if given and _VALID.fullmatch(given):
        os.environ[ENV] = given
    current = name()
    if not current:
        return ""
    folder = f"Hams-test-{current}"
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / folder
    roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / folder
    os.environ.setdefault("HAMS_DATA_DIR", str(local))
    os.environ.setdefault("HAMS_CONFIG", str(roaming / "config.toml"))
    os.environ.setdefault("HF_HUB_CACHE", str(local / "huggingface" / "hub"))
    return current
