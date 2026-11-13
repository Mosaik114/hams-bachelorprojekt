"""``Hams.exe --self-test``: proves that a build is complete, without a window or a microphone.

The installer's CI job runs it after a silent install. It catches what only
shows in the packaged app: a module PyInstaller did not collect, a missing
data file (prompts, branding, the VAD model of faster-whisper), a DLL that is
not found (ctranslate2, PortAudio, Tcl/Tk).

Each check prints one line, ``ok`` or ``FAIL``; the exit code is 0 only if
every check passed. Nothing is downloaded and no recording is made.
"""

from __future__ import annotations

import platform
import sys
import traceback
from collections.abc import Callable
from importlib import metadata


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "?"


def check_hams() -> str:
    from hams import app_paths

    mode = "installed" if app_paths.is_frozen() else "source"
    return f"hams {_version('hams')} ({mode}), Python {platform.python_version()}, {platform.platform()}"


def check_config() -> str:
    from hams import config

    return f"config {config.CONFIG_PATH}, log {config.LOG_DIR}, model {config.CFG.model_size}"


def check_package_data() -> str:
    from hams import branding, config

    files = [
        config.DEFAULT_CONFIG_PATH,
        config.APP_DIR / "glossar.default.toml",
        config.APP_DIR / "prompts" / "cleanup.txt",
        branding.ASSET_DIR / "hams.ico",
    ]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    return f"{len(files)} files"


def check_backend() -> str:
    import ctranslate2
    import faster_whisper

    from hams import backend  # noqa: F401 - the preload without torch runs here
    from hams.backend_preload import cuda_libraries_found

    try:
        gpus = ctranslate2.get_cuda_device_count()
    except Exception:  # pragma: no cover - depends on the driver
        gpus = 0
    torch = "loaded" if "torch" in sys.modules else "not loaded"
    return (
        f"faster-whisper {faster_whisper.__version__}, ctranslate2 {ctranslate2.__version__}, "
        f"CUDA devices {gpus}, CUDA libraries {'found' if cuda_libraries_found() else 'missing'}, torch {torch}"
    )


def check_vad() -> str:
    """The Silero VAD model is a data file of faster-whisper, run with onnxruntime."""
    import onnxruntime
    from faster_whisper.vad import get_vad_model

    get_vad_model()
    return f"onnxruntime {onnxruntime.__version__}"


def check_audio() -> str:
    import sounddevice

    return f"sounddevice {sounddevice.__version__}, {sounddevice.get_portaudio_version()[1]}"


def check_tk() -> str:
    """Creates a Tk root and withdraws it before the first idle loop, so it is never shown."""
    import tkinter as tk

    root = tk.Tk()
    try:
        root.withdraw()
        patchlevel = root.tk.call("info", "patchlevel")
    finally:
        root.destroy()
    return f"Tcl/Tk {patchlevel}"


def check_app_modules() -> str:
    import PIL
    import pystray  # noqa: F401

    import hams.main  # noqa: F401 - imports every module the app uses

    return f"Pillow {PIL.__version__}, pystray {_version('pystray')}"


def check_gpu_support() -> str:
    from hams import gpu_support

    folder = gpu_support.installed_dir()
    driver = "yes" if gpu_support.nvidia_gpu_present() else "no"
    return f"NVIDIA driver {driver}, downloaded libraries {folder or 'none'}"


CHECKS: tuple[tuple[str, Callable[[], str]], ...] = (
    ("hams", check_hams),
    ("config", check_config),
    ("package data", check_package_data),
    ("backend", check_backend),
    ("vad", check_vad),
    ("audio", check_audio),
    ("tk", check_tk),
    ("app modules", check_app_modules),
    ("gpu support", check_gpu_support),
)


def run(stream, checks=CHECKS) -> int:
    """Runs every check, prints one line each, returns the number of failures."""
    failures = 0
    for name, check in checks:
        try:
            detail = check()
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", file=stream)
            traceback.print_exc(file=stream)
        else:
            print(f"ok   {name}: {detail}", file=stream)
    print("self-test passed" if not failures else f"self-test failed ({failures})", file=stream)
    return failures
