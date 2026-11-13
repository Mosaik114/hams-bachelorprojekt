"""`python -m hams [--self-test | --install-gpu-support | --settings]`, see cli.py."""

import sys

from hams.cli import run

if __name__ == "__main__":
    sys.exit(run())
