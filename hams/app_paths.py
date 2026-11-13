"""Where Hams writes at run time: logs, transcripts and the optional GPU libraries.

None of it may live next to the code. Installed with the Windows installer, the
program folder belongs to the installer: an update replaces it and the
uninstaller deletes it. Run from a source checkout, files written into the
package end up between the tracked sources.

So everything Hams produces goes to the per-user data folder of the platform:

- Windows: ``%LOCALAPPDATA%\\Hams`` (machine-local, not roaming: the GPU
  libraries alone are about 1.9 GB)
- elsewhere: ``$XDG_DATA_HOME/hams``, by default ``~/.local/share/hams``

``HAMS_DATA_DIR`` overrides the folder. The test suite relies on it, just as
it relies on ``HAMS_CONFIG`` and ``HAMS_LOG_DIR``: it must never touch the
real folders.

The settings themselves stay where they are (``%APPDATA%\\Hams``, see
``config.py``). This module only imports the standard library so that the
backend preload can use it before anything else is loaded.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Folder name below the platform's data directory.
DATA_DIR_NAME = "Hams" if sys.platform == "win32" else "hams"


def is_frozen() -> bool:
    """True when running as the packaged ``Hams.exe`` (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def local_data_dir() -> Path:
    """The per-user folder for everything Hams writes besides its settings."""
    override = os.environ.get("HAMS_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / DATA_DIR_NAME


def default_log_dir() -> Path:
    """Where the log goes unless ``HAMS_LOG_DIR`` says otherwise."""
    return local_data_dir() / "logs"


def transcripts_dir() -> Path:
    """Where pasted texts are kept as files when `[output] save_transcripts` is on."""
    return local_data_dir() / "transcripts"


def cuda_dir() -> Path:
    """Where the downloaded cuBLAS/cuDNN libraries live (see ``gpu_support``)."""
    return local_data_dir() / "cuda"
