"""Command line of Hams: ``Hams.exe`` / ``hams`` / ``python -m hams``.

Without options it starts the app. The options are for the installer and for
support; none of them opens a window:

``--settings``              start (or ask the running instance) with the settings open
``--setup``                 start with the first-run setup wizard, even if it was completed
``--self-test``             check that the build is complete, exit 0 if it is
``--install-gpu-support``   download cuBLAS/cuDNN for NVIDIA GPUs (installed app)
``--report FILE``           also write the output of the two above to FILE

The packaged ``Hams.exe`` is a windowed program without a console, so
``sys.stdout`` is None there. The output then goes to the console of the
calling process if there is one, and to ``--report`` in any case.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="Hams", add_help=False)
    parser.add_argument("--settings", action="store_true")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--install-gpu-support", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def _parent_console():
    """The console of the calling process (Windows), or None."""
    if sys.platform != "win32":
        return None
    import ctypes

    attach_parent_process = -1
    if not ctypes.windll.kernel32.AttachConsole(attach_parent_process):
        return None
    try:
        return open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115 - closed in _Tee
    except OSError:
        return None


class _Tee(io.TextIOBase):
    """Writes to every target; closes the ones it opened."""

    def __init__(self, targets, owned) -> None:
        self._targets = [t for t in targets if t is not None]
        self._owned = owned

    def write(self, text: str) -> int:
        for target in self._targets:
            with contextlib.suppress(OSError, ValueError):
                target.write(text)
                target.flush()
        return len(text)

    def close(self) -> None:
        for target in self._owned:
            with contextlib.suppress(OSError):
                target.close()
        super().close()


def _output(report: Path | None) -> _Tee:
    owned = []
    console = sys.stdout
    if console is None:
        console = _parent_console()
        if console is not None:
            owned.append(console)
    targets = [console]
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        handle = report.open("w", encoding="utf-8")
        owned.append(handle)
        targets.append(handle)
    return _Tee(targets, owned)


def run(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args, _unknown = _parser().parse_known_args(sys.argv[1:] if argv is None else argv)
    if args.self_test or args.install_gpu_support:
        with contextlib.closing(_output(args.report)) as stream:
            if args.self_test:
                from hams import self_test

                return min(self_test.run(stream), 1)
            from hams import gpu_support

            return gpu_support.install_cli(stream)
    from hams.main import main

    main(show_settings=args.settings, show_setup=args.setup)
    return 0
