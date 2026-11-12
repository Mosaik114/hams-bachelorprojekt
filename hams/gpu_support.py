"""Optional GPU support for the packaged app: download cuBLAS and cuDNN on demand.

ctranslate2 needs cuBLAS and cuDNN to run Whisper on an NVIDIA GPU but does not
ship them. A source install gets them from pip (`pip install -e ".[cuda]"`,
found by `backend_preload`). The Windows installer cannot carry them: they are
about 1.9 GB, close to GitHub's 2 GB limit per release file, and useless on
every machine without an NVIDIA card.

So the packaged app fetches them when asked to: the exact NVIDIA wheels from
PyPI (pinned below by URL, size and SHA-256), of which only the DLLs are kept,
in ``%LOCALAPPDATA%\\Hams\\cuda``. Without them Hams transcribes on the CPU.

Guarantees of `install`:

- **verified** - every wheel is checked against its pinned SHA-256, every
  extracted DLL against the hash in the wheel's own ``RECORD``.
- **resumable** - downloads go to ``*.part`` files and continue with an HTTP
  range request after a cancel or a dropped connection.
- **atomic** - the DLLs are unpacked into a temporary folder that replaces the
  target folder by a rename only when everything is in place. A half-finished
  install is never picked up by `installed_dir`.
- **cancellable** - the ``cancel`` callback is polled for every chunk.

Standard library only: `backend_preload` asks `installed_dir` before
faster-whisper is imported.
"""

from __future__ import annotations

import base64
import contextlib
import csv
import hashlib
import io
import json
import logging
import os
import shutil
import sys
import urllib.request
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from hams import app_paths

logger = logging.getLogger("hams")


@dataclass(frozen=True)
class Wheel:
    """One pinned wheel from PyPI."""

    name: str
    version: str
    url: str
    sha256: str
    size: int

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


#: The versions a source install with ``[cuda]`` resolves to as well; tested
#: with ctranslate2 4.8 (CUDA 12, cuDNN 9). cuBLAS needs NVRTC, so it comes too.
CUDA_WHEELS: tuple[Wheel, ...] = (
    Wheel(
        "nvidia-cublas-cu12",
        "12.9.2.10",
        "https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
        "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
        "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661",
        553_162_896,
    ),
    Wheel(
        "nvidia-cudnn-cu12",
        "9.27.0.42",
        "https://files.pythonhosted.org/packages/aa/38/f856579877f7c1c5066e61182e7de7bc27bf35a78c8d1b0fa592e6985bc4/"
        "nvidia_cudnn_cu12-9.27.0.42-py3-none-win_amd64.whl",
        "06e9b0026f3bad97d2b58666330fabec04fe1672f776661ecb0ce0029c27f142",
        743_068_852,
    ),
    Wheel(
        "nvidia-cuda-nvrtc-cu12",
        "12.9.86",
        "https://files.pythonhosted.org/packages/52/de/823919be3b9d0ccbf1f784035423c5f18f4267fb0123558d58b813c6ec86/"
        "nvidia_cuda_nvrtc_cu12-12.9.86-py3-none-win_amd64.whl",
        "72972ebdcf504d69462d3bcd67e7b81edd25d0fb85a2c46d3ea3517666636349",
        76_408_187,
    ),
)

#: In the wheels, but never loaded by ctranslate2 (BLAS drop-in for other programs).
SKIPPED_DLLS = frozenset({"nvblas64_12.dll"})

MANIFEST_NAME = "manifest.json"
CHUNK_SIZE = 1 << 20
TIMEOUT = 30.0
USER_AGENT = "Hams-gpu-support"

#: progress(stage, done, total) - stage is "download" or "extract", sizes in bytes.
ProgressCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]
Opener = Callable[..., object]


class GpuSupportError(RuntimeError):
    """Download or verification failed; nothing was installed."""


class InstallCancelled(GpuSupportError):
    """The cancel callback asked to stop. Partial downloads are kept for a resume."""


def bundle_id(wheels: Iterable[Wheel] = CUDA_WHEELS) -> str:
    """Names the exact set of wheels, so a changed pin triggers a fresh install."""
    return "+".join(f"{w.name}=={w.version}" for w in wheels)


def is_supported() -> bool:
    """The pinned wheels exist for 64-bit Windows only. Linux uses pip's ``[cuda]`` extra."""
    return sys.platform == "win32" and sys.maxsize > 2**32


def nvidia_gpu_present() -> bool:
    """Whether an NVIDIA driver is installed - the precondition for using the libraries.

    Asks for the driver's ``nvcuda.dll`` instead of importing ctranslate2: it
    is cheap and works before the backend is loaded.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        ctypes.WinDLL("nvcuda.dll")
    except OSError:
        return False
    return True


# ----------------------------------------------------------------- installed


def _read_manifest(folder: Path) -> dict | None:
    try:
        manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def installed_dir(target: Path | None = None, wheels: Iterable[Wheel] = CUDA_WHEELS) -> Path | None:
    """The folder with the downloaded DLLs, or None if they are missing or incomplete.

    Checks the manifest and every file's size (not the hash: reading 1.9 GB at
    every start would cost seconds; the hashes were checked when unpacking).
    """
    folder = target or app_paths.cuda_dir()
    manifest = _read_manifest(folder)
    if manifest is None or manifest.get("bundle") != bundle_id(wheels):
        return None
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        return None
    for name, size in files.items():
        path = folder / name
        try:
            if path.stat().st_size != size:
                return None
        except OSError:
            return None
    return folder


# ------------------------------------------------------------------ download


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_cancel(cancel: CancelCallback | None) -> None:
    if cancel is not None and cancel():
        raise InstallCancelled("GPU support download cancelled")


class _Progress:
    """Adds up the bytes of all wheels into one figure for the caller."""

    def __init__(self, callback: ProgressCallback | None, stage: str, total: int) -> None:
        self.callback = callback
        self.stage = stage
        self.total = total
        self.done = 0

    def advance(self, amount: int) -> None:
        self.done += amount
        if self.callback is not None:
            self.callback(self.stage, self.done, self.total)


def _open(opener: Opener, url: str, offset: int):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - pinned https URLs only
    if offset:
        request.add_header("Range", f"bytes={offset}-")
    return opener(request, timeout=TIMEOUT)


def _download(wheel: Wheel, folder: Path, opener: Opener, progress: _Progress, cancel: CancelCallback | None) -> Path:
    """Fetches one wheel, resuming a ``.part`` file if there is one, and checks its hash."""
    final = folder / wheel.filename
    if final.exists() and final.stat().st_size == wheel.size and _sha256_of(final) == wheel.sha256:
        progress.advance(wheel.size)
        return final
    part = folder / (wheel.filename + ".part")
    offset = part.stat().st_size if part.exists() else 0
    if offset > wheel.size:
        part.unlink()
        offset = 0
    if offset < wheel.size:
        offset = _fetch_into(wheel, part, offset, opener, progress, cancel)
    else:
        progress.advance(offset)
    if offset != wheel.size or _sha256_of(part) != wheel.sha256:
        part.unlink(missing_ok=True)
        raise GpuSupportError(f"{wheel.filename}: download does not match the pinned SHA-256")
    os.replace(part, final)
    return final


def _fetch_into(wheel: Wheel, part: Path, offset: int, opener: Opener, progress: _Progress, cancel) -> int:
    """Appends the rest of the file to ``part``. Returns the new size."""
    try:
        response = _open(opener, wheel.url, offset)
    except OSError as exc:
        raise GpuSupportError(f"{wheel.filename}: {exc}") from exc
    with contextlib.closing(response):
        if offset and getattr(response, "status", 200) != 206:
            offset = 0  # the server ignored the range: start over
        progress.advance(offset)
        with part.open("ab" if offset else "wb") as handle:
            while True:
                _check_cancel(cancel)
                try:
                    chunk = response.read(CHUNK_SIZE)
                except OSError as exc:
                    raise GpuSupportError(f"{wheel.filename}: {exc}") from exc
                if not chunk:
                    break
                handle.write(chunk)
                offset += len(chunk)
                progress.advance(len(chunk))
    return offset


# ------------------------------------------------------------------- extract


def _record_hashes(archive: zipfile.ZipFile) -> dict[str, str]:
    """``path -> sha256 (hex)`` from the wheel's RECORD file."""
    record = next((n for n in archive.namelist() if n.endswith(".dist-info/RECORD")), None)
    if record is None:
        raise GpuSupportError("wheel without RECORD")
    hashes: dict[str, str] = {}
    for row in csv.reader(io.TextIOWrapper(archive.open(record), encoding="utf-8")):
        if len(row) >= 2 and row[1].startswith("sha256="):
            raw = row[1].removeprefix("sha256=")
            padded = raw + "=" * (-len(raw) % 4)
            hashes[row[0]] = base64.urlsafe_b64decode(padded).hex()
    return hashes


def wanted_members(names: Iterable[str]) -> list[str]:
    """The DLLs ctranslate2 may load: ``nvidia/<lib>/bin/*.dll`` minus `SKIPPED_DLLS`."""
    wanted = []
    for name in names:
        path = PurePosixPath(name)
        if (
            len(path.parts) == 4
            and path.parts[0] == "nvidia"
            and path.parts[2] == "bin"
            and path.suffix.lower() == ".dll"
            and path.name.lower() not in SKIPPED_DLLS
        ):
            wanted.append(name)
    return wanted


def _extract_member(archive, member: str, expected: str | None, staging: Path, progress, cancel) -> tuple[str, int]:
    name = PurePosixPath(member).name
    digest = hashlib.sha256()
    size = 0
    with archive.open(member) as source, (staging / name).open("wb") as target:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            _check_cancel(cancel)
            digest.update(chunk)
            target.write(chunk)
            size += len(chunk)
            progress.advance(len(chunk))
    if expected is None or digest.hexdigest() != expected:
        raise GpuSupportError(f"{name}: hash does not match the wheel's RECORD")
    return name, size


def _extract(wheel_file: Path, staging: Path, progress: _Progress, cancel) -> dict[str, int]:
    files: dict[str, int] = {}
    with zipfile.ZipFile(wheel_file) as archive:
        hashes = _record_hashes(archive)
        for member in wanted_members(archive.namelist()):
            name, size = _extract_member(archive, member, hashes.get(member), staging, progress, cancel)
            files[name] = size
    return files


def _members_size(wheel_files: Iterable[Path]) -> int:
    total = 0
    for path in wheel_files:
        with zipfile.ZipFile(path) as archive:
            names = set(wanted_members(archive.namelist()))
            total += sum(info.file_size for info in archive.infolist() if info.filename in names)
    return total


def _replace_folder(staging: Path, target: Path) -> None:
    """Swaps the finished staging folder in; the old folder goes only after that."""
    old = target.with_name(target.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        os.replace(target, old)
    os.replace(staging, target)
    shutil.rmtree(old, ignore_errors=True)


# ------------------------------------------------------------------- install


def install(
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    *,
    target: Path | None = None,
    wheels: tuple[Wheel, ...] = CUDA_WHEELS,
    opener: Opener = urllib.request.urlopen,
) -> Path:
    """Downloads, verifies and unpacks the CUDA libraries. Returns the folder.

    Does nothing (but report progress) if the current set is already in place.

    Raises:
        InstallCancelled: ``cancel()`` returned True. Downloads so far are kept.
        GpuSupportError: network error or hash mismatch. Nothing was installed.
    """
    target = target or app_paths.cuda_dir()
    if installed_dir(target, wheels) is not None:
        return target
    downloads = target.with_name(target.name + ".download")
    downloads.mkdir(parents=True, exist_ok=True)
    fetched = _Progress(progress, "download", sum(w.size for w in wheels))
    wheel_files = [_download(w, downloads, opener, fetched, cancel) for w in wheels]

    staging = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        unpacked = _Progress(progress, "extract", _members_size(wheel_files))
        files: dict[str, int] = {}
        for wheel_file in wheel_files:
            files.update(_extract(wheel_file, staging, unpacked, cancel))
        manifest = {"bundle": bundle_id(wheels), "files": files}
        (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        _replace_folder(staging, target)
    except zipfile.BadZipFile as exc:
        raise GpuSupportError(f"broken wheel: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(downloads, ignore_errors=True)
    logger.info("GPU support installed in %s (%d files)", target, len(files))
    return target


def remove(target: Path | None = None) -> None:
    """Deletes the downloaded libraries (and any leftovers of an unfinished install)."""
    target = target or app_paths.cuda_dir()
    for folder in (target, target.with_name(target.name + ".download"), target.with_name(target.name + ".old")):
        shutil.rmtree(folder, ignore_errors=True)


# ----------------------------------------------------------------------- CLI


def _print_progress(stream) -> ProgressCallback:
    last = {"stage": "", "percent": -1}

    def report(stage: str, done: int, total: int) -> None:
        percent = done * 100 // total if total else 100
        if (stage, percent) != (last["stage"], last["percent"]):
            last.update(stage=stage, percent=percent)
            print(f"\r{stage}: {percent:3d} % ({done / 1e6:,.0f} of {total / 1e6:,.0f} MB)", end="", file=stream)

    return report


def install_cli(stream=None) -> int:
    """``Hams.exe --install-gpu-support``: the same as the first-run wizard will do, on the console."""
    stream = stream or sys.stdout
    if not is_supported():
        print("GPU support download is only available on 64-bit Windows.", file=stream)
        print('From source, install the libraries with: pip install -e ".[cuda]"', file=stream)
        return 2
    if not nvidia_gpu_present():
        print("No NVIDIA driver found - Hams will keep using the CPU.", file=stream)
        return 2
    total = sum(w.size for w in CUDA_WHEELS)
    print(f"Downloading GPU support (about {total / 1e9:.1f} GB) to {app_paths.cuda_dir()} ...", file=stream)
    try:
        folder = install(_print_progress(stream))
    except InstallCancelled:  # pragma: no cover - the CLI has no cancel button
        return 130
    except GpuSupportError as exc:
        print(f"\nFailed: {exc}", file=stream)
        logger.warning("GPU support install failed: %s", exc)
        return 1
    print(f"\nDone: {folder}. Restart Hams to use the GPU.", file=stream)
    return 0
