"""Entry script of the frozen Hams.exe (PyInstaller). All logic lives in hams.cli."""

import sys

from hams.cli import run

if __name__ == "__main__":
    sys.exit(run())
