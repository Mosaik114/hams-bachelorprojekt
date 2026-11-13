"""linux/install.sh: the steps of the user guide, without development extras."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "linux" / "install.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")


def test_strict_mode_and_lf_line_endings():
    assert "set -euo pipefail" in TEXT
    assert "\r" not in SCRIPT.read_bytes().decode("utf-8")


def test_installs_like_the_user_guide_and_without_dev_extras():
    assert "uv venv --system-site-packages --python" in TEXT
    assert '-e ".[cuda]"' in TEXT
    assert "[dev" not in TEXT
    assert "requirements-dev" not in TEXT


def test_asks_before_installing_uv_and_autostart():
    install_uv = TEXT.index("curl -LsSf https://astral.sh/uv/install.sh | sh\n")
    assert TEXT.rfind('if ! ask "Install uv now', 0, install_uv) != -1
    assert 'if ask "Add Hams to the application menu' in TEXT


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="needs a POSIX bash")
def test_help_and_unknown_options():
    shown = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True, check=True)  # noqa: S603, S607
    assert "--skip-system" in shown.stdout
    assert "set -euo" not in shown.stdout
    wrong = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)  # noqa: S603, S607
    assert wrong.returncode == 2
