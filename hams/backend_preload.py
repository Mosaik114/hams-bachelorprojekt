"""Make cuBLAS and cuDNN reachable for CTranslate2 before faster-whisper is imported.

CTranslate2 needs both libraries for CUDA but does not ship them. Hams takes them from the `nvidia-cublas-cu12` /
`nvidia-cudnn-cu12` wheels (source install, `[cuda]` extra) or from the folder the installed app downloads
(`gpu_support`). Without them the GPU fails only at the first dictation with

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

so the folders are registered (and on Windows the DLLs preloaded) before the model is built. Registering costs
well under a millisecond and imports no Python module.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("hams")


#: Where cuBLAS and cuDNN can be found as pip packages (Windows: their `bin` folder).
CUDA_LIBRARY_PACKAGES = (
    ("nvidia.cublas", "bin"),
    ("nvidia.cudnn", "bin"),
)


def _package_dir(package: str, subfolder: str) -> Path | None:
    """Wo ein Paket liegt — ohne es zu importieren.

    `find_spec` liest nur die Metadaten, es laeuft kein Code des Pakets.
    """
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError, ModuleNotFoundError):
        return None
    if spec is None:
        return None
    root = spec.origin
    if root is None:  # Namensraum-Paket wie "nvidia"
        places = list(getattr(spec, "submodule_search_locations", []) or [])
        if not places:
            return None
        folder = Path(places[0]) / subfolder
    else:
        folder = Path(root).parent / subfolder
    return folder if folder.is_dir() else None


def add_cuda_libraries() -> tuple[str, ...]:
    """Meldet die Verzeichnisse mit cuBLAS und cuDNN als DLL-Pfade an (siehe Modulkopf).

    Returns:
        Die angemeldeten Verzeichnisse. Leer heisst: keines gefunden. Dann
        scheitert CUDA, und `_build_model` faellt auf die naechste Stufe der
        Geraeteliste zurueck.
    """
    if sys.platform != "win32":
        return _preload_cuda_libraries_linux()
    registered: list[str] = []
    for package, subfolder in CUDA_LIBRARY_PACKAGES:
        folder = _package_dir(package, subfolder)
        if folder is not None and _register_dll_directory(folder):
            registered.append(str(folder))
    downloaded = _downloaded_cuda_dir()
    if downloaded is not None and _register_dll_directory(downloaded):
        registered.append(str(downloaded))
    if not registered:
        logger.warning(
            "No CUDA libraries found - transcription runs on the CPU. Missing nvidia-cublas-cu12 / nvidia-cudnn-cu12 "
            "(install: Hams.exe --install-gpu-support)?"
        )
    _preload_cuda_libraries_windows()
    return tuple(registered)


def _register_dll_directory(folder: Path) -> bool:
    """`os.add_dll_directory` ohne Absturz: ein verschwundener oder gesperrter Ordner kostet nur die GPU."""
    try:
        os.add_dll_directory(str(folder))
    except OSError:
        logger.warning("CUDA libraries in %s could not be registered", folder, exc_info=True)
        return False
    return True


def _downloaded_cuda_dir() -> Path | None:
    """Die vom installierten Programm nachgeladenen Bibliotheken (`gpu_support`), falls vollstaendig."""
    from hams import gpu_support

    return gpu_support.installed_dir()


def _windows_library_dir(package: str) -> Path | None:
    """Erst das pip-Paket (Quellinstallation), dann der Download des installierten Programms."""
    return _package_dir(package, "bin") or _downloaded_cuda_dir()


def cuda_libraries_found() -> bool:
    """Ob cuBLAS fuer ctranslate2 erreichbar ist: Paket, Download oder PATH (CUDA-Toolkit).

    Ohne cuBLAS laedt ctranslate2 ein Modell zwar auf der GPU, scheitert aber
    erst beim ersten Diktat. Wer das vorher weiss, nimmt gleich die CPU.
    Ausserhalb von Windows immer True: dort entscheidet weiter der Ladeversuch.
    """
    if sys.platform != "win32":
        return True
    import ctypes.util

    return _windows_library_dir("nvidia.cublas") is not None or ctypes.util.find_library("cublas64_12") is not None


#: Unter Windows: Paket -> DLLs, in dieser Reihenfolge vorgeladen.
CUDA_LIBRARIES_WINDOWS = (
    ("nvidia.cublas", ("cublasLt64_12.dll", "cublas64_12.dll")),
    ("nvidia.cudnn", ("cudnn64_9.dll",)),
)


def _preload_cuda_libraries_windows() -> None:
    """Laedt cuBLAS und cuDNN aus den NVIDIA-Paketen vor.

    ctranslate2 laedt cuBLAS erst beim ersten Rechnen nach und sucht dabei
    ueber den PATH, nicht ueber die mit `add_dll_directory` angemeldeten
    Ordner. Steht eine DLL dieses Namens schon im Prozess, nimmt Windows sie.
    """
    import ctypes

    for package, files in CUDA_LIBRARIES_WINDOWS:
        folder = _windows_library_dir(package)
        if folder is None:
            continue
        for file in files:
            try:
                ctypes.WinDLL(str(folder / file))
            except OSError:
                logger.warning("CUDA library %s could not be loaded", folder / file)


#: Unter Linux: Paket -> Bibliotheken, in dieser Reihenfolge geladen.
CUDA_LIBRARIES_LINUX = (
    ("nvidia.cublas", ("libcublasLt.so.12", "libcublas.so.12")),
    ("nvidia.cudnn", ("libcudnn.so.9",)),
)


def _preload_cuda_libraries_linux() -> tuple[str, ...]:
    """Laedt cuBLAS und cuDNN aus den NVIDIA-Paketen vor.

    Linux kennt kein `add_dll_directory`. ctranslate2 sucht die Bibliotheken
    ueber ihren Namen; ist eine Bibliothek dieses Namens schon im Prozess,
    nimmt der Lader sie. cuDNN findet seine Teilbibliotheken selbst, weil sie
    neben ihm liegen.
    """
    import ctypes

    loaded: list[str] = []
    for package, files in CUDA_LIBRARIES_LINUX:
        folder = _package_dir(package, "lib")
        if folder is None:
            continue
        for file in files:
            try:
                ctypes.CDLL(str(folder / file), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                logger.warning("CUDA library %s could not be loaded", folder / file)
            else:
                if str(folder) not in loaded:
                    loaded.append(str(folder))
    if not loaded:
        logger.warning(
            "No CUDA libraries found - transcription runs on the CPU. Missing nvidia-cublas-cu12 / nvidia-cudnn-cu12?"
        )
    return tuple(loaded)
