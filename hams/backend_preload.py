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


def _paket_verzeichnis(paket: str, unterordner: str) -> Path | None:
    """Wo ein Paket liegt — ohne es zu importieren.

    `find_spec` liest nur die Metadaten, es laeuft kein Code des Pakets.
    """
    try:
        spec = importlib.util.find_spec(paket)
    except (ImportError, ValueError, ModuleNotFoundError):
        return None
    if spec is None:
        return None
    wurzel = spec.origin
    if wurzel is None:  # Namensraum-Paket wie "nvidia"
        orte = list(getattr(spec, "submodule_search_locations", []) or [])
        if not orte:
            return None
        ordner = Path(orte[0]) / unterordner
    else:
        ordner = Path(wurzel).parent / unterordner
    return ordner if ordner.is_dir() else None


def add_cuda_libraries() -> tuple[str, ...]:
    """Meldet die Verzeichnisse mit cuBLAS und cuDNN als DLL-Pfade an (siehe Modulkopf).

    Returns:
        Die angemeldeten Verzeichnisse. Leer heisst: keines gefunden. Dann
        scheitert CUDA, und `_build_model` faellt auf die naechste Stufe der
        Geraeteliste zurueck.
    """
    if sys.platform != "win32":
        return _preload_cuda_libraries_linux()
    angemeldet: list[str] = []
    for paket, unterordner in CUDA_LIBRARY_PACKAGES:
        ordner = _paket_verzeichnis(paket, unterordner)
        if ordner is not None and _register_dll_directory(ordner):
            angemeldet.append(str(ordner))
    heruntergeladen = _downloaded_cuda_dir()
    if heruntergeladen is not None and _register_dll_directory(heruntergeladen):
        angemeldet.append(str(heruntergeladen))
    if not angemeldet:
        logger.warning(
            "Keine CUDA-Bibliotheken gefunden — Transkription "
            "laeuft auf der CPU. Fehlt nvidia-cublas-cu12 / "
            "nvidia-cudnn-cu12 (installiert: Hams.exe --install-gpu-support)?"
        )
    _preload_cuda_libraries_windows()
    return tuple(angemeldet)


def _register_dll_directory(ordner: Path) -> bool:
    """`os.add_dll_directory` ohne Absturz: ein verschwundener oder gesperrter Ordner kostet nur die GPU."""
    try:
        os.add_dll_directory(str(ordner))
    except OSError:
        logger.warning("CUDA-Bibliotheken unter %s nicht anmeldbar", ordner, exc_info=True)
        return False
    return True


def _downloaded_cuda_dir() -> Path | None:
    """Die vom installierten Programm nachgeladenen Bibliotheken (`gpu_support`), falls vollstaendig."""
    from hams import gpu_support

    return gpu_support.installed_dir()


def _windows_library_dir(paket: str) -> Path | None:
    """Erst das pip-Paket (Quellinstallation), dann der Download des installierten Programms."""
    return _paket_verzeichnis(paket, "bin") or _downloaded_cuda_dir()


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

    for paket, dateien in CUDA_LIBRARIES_WINDOWS:
        ordner = _windows_library_dir(paket)
        if ordner is None:
            continue
        for datei in dateien:
            try:
                ctypes.WinDLL(str(ordner / datei))
            except OSError:
                logger.warning("CUDA-Bibliothek %s nicht ladbar", ordner / datei)


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

    geladen: list[str] = []
    for paket, dateien in CUDA_LIBRARIES_LINUX:
        ordner = _paket_verzeichnis(paket, "lib")
        if ordner is None:
            continue
        for datei in dateien:
            try:
                ctypes.CDLL(str(ordner / datei), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                logger.warning("CUDA-Bibliothek %s nicht ladbar", ordner / datei)
            else:
                if str(ordner) not in geladen:
                    geladen.append(str(ordner))
    if not geladen:
        logger.warning(
            "Keine CUDA-Bibliotheken gefunden — Transkription "
            "laeuft auf der CPU. Fehlt nvidia-cublas-cu12 / "
            "nvidia-cudnn-cu12?"
        )
    return tuple(geladen)
