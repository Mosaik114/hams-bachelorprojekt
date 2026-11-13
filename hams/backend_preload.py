"""faster-whisper ohne torch laden – und die CUDA-Bibliotheken bereitstellen, die torch sonst mitbrachte.

Ausgelagert aus models.py (Q7). Warum:


Gemessen mit `python -X importtime`: `from faster_whisper import
WhisperModel` brauchte 1602 ms, davon 943 ms torch und 651 ms
transformers. Beide stehen in keiner Abhaengigkeitsliste dieses Projekts
und werden von keiner Zeile hier aufgerufen — sie liegen nur im selben
Interpreter, und `ctranslate2/__init__.py` importiert `converters`
bedingungslos mit. `converters.transformers` und `specs.model_spec`
versuchen dann torch zu laden.

Beide Stellen schuetzen den Versuch mit `try: import torch / except
ImportError: pass` — "torch ist nicht installiert" ist fuer ctranslate2
ein vorgesehener Zustand, kein Fehlerfall. Genau diesen Zustand stellt
dieser Aufruf fuer die Dauer des Imports her.

Gemessen ohne die beiden: 160 ms statt 1602 ms.

Aber: torch ist nicht ueberfluessig. Sein Ordner `lib` ist auf einer
typischen Windows-Installation die einzige Quelle von cublas64_12.dll und
cudnn64_9.dll, und ctranslate2 braucht beide fuer CUDA. Bisher kamen sie
als Nebenwirkung des Imports in den DLL-Suchpfad. Faellt der Import weg,
ohne dass jemand die Bibliotheken bereitstellt, meldet die Transkription
auf der GPU

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

und Hams faellt auf die CPU zurueck. Deshalb wird der Ordner vorher
angemeldet — das kostet 0,3 ms und laedt kein Python-Modul.

Die Sperre gilt nur waehrend des Imports und wird danach wieder
abgeraeumt; wer torch spaeter braucht, bekommt es. Schlaegt der Import
trotzdem fehl, wird er ohne Sperre wiederholt — dann kostet der Start
wieder die 1,4 s, aber die Anwendung startet.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("hams")


#: Wo cuBLAS und cuDNN liegen koennen, in dieser Reihenfolge durchsucht.
#: Erst die eigenstaendigen NVIDIA-Pakete, dann torch. Die schlanke
#: Installation soll gewinnen, wenn beides da ist.
CUDA_LIBRARY_PACKAGES = (
    ("nvidia.cublas", "bin"),
    ("nvidia.cudnn", "bin"),
    ("torch", "lib"),
)


def _paket_verzeichnis(paket: str, unterordner: str) -> Path | None:
    """Wo ein Paket liegt — ohne es zu importieren.

    `find_spec` liest nur die Metadaten. Fuer torch macht das den Unterschied
    zwischen 0,3 ms und 943 ms.
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
    """Meldet die Verzeichnisse mit cuBLAS und cuDNN als DLL-Pfade an.

    ctranslate2 braucht beide fuer CUDA, bringt sie aber nicht mit. Auf einer
    gewachsenen Windows-Installation liefert sie meist torch — gemessen sind
    das 752 MB cuBLAS und 961 MB cuDNN in einem 4337 MB grossen Paket, dessen
    Python-Teil hier niemand aufruft. Es gibt sie auch einzeln, als
    `nvidia-cublas-cu12` und `nvidia-cudnn-cu12`; die werden zuerst gesucht.

    Bisher kamen die Bibliotheken als *Nebenwirkung* des torch-Imports in den
    Suchpfad. Diese Abhaengigkeit war unsichtbar und hat beim Wegfall des
    Imports prompt die GPU lahmgelegt:

        RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

    Jetzt steht sie ausdruecklich da und kostet 0,3 ms statt 943 ms.

    Returns:
        Die angemeldeten Verzeichnisse. Leer heisst: keines gefunden. Dann
        bleibt es beim bisherigen Verhalten — CUDA scheitert und
        `_build_model` faellt auf die naechste Stufe der Geraeteliste zurueck.
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


class _AbgeschalteteImporte:
    """Meta-Path-Finder, der genau die genannten Wurzelpakete abweist.

    `find_spec` statt der alten `find_module`/`load_module`: die sind seit
    Python 3.12 entfernt, und das Projekt laeuft ab 3.10.
    """

    __slots__ = ("_namen",)

    def __init__(self, *namen: str) -> None:
        self._namen = frozenset(namen)

    def find_spec(self, fullname, path=None, target=None):
        # Faellt die Antwort implizit auf None, heisst das "nicht zustaendig"
        # und der naechste Finder in sys.meta_path ist dran.
        if fullname.partition(".")[0] in self._namen:
            raise ModuleNotFoundError(f"No module named {fullname!r}")


def preload_backend_without_torch() -> bool:
    """Laedt faster-whisper, ohne torch und transformers mitzuziehen (Begruendung und Messung: Modulkopf).

    Returns:
        True, wenn das Backend ohne torch geladen wurde.
    """
    if "faster_whisper" in sys.modules:
        return "torch" not in sys.modules

    add_cuda_libraries()

    sperre = _AbgeschalteteImporte("torch", "transformers")
    sys.meta_path.insert(0, sperre)
    try:
        importlib.import_module("faster_whisper")
        return True
    except Exception:
        logger.warning("faster-whisper liess sich ohne torch nicht laden — zweiter Versuch ohne Sperre", exc_info=True)
    finally:
        with contextlib.suppress(ValueError):  # hat jemand anders entfernt
            sys.meta_path.remove(sperre)

    # Aufraeumen, damit der zweite Anlauf nicht auf halb geladenen Modulen
    # aufsetzt: was die Sperre abgewiesen hat, steht sonst als kaputter
    # Eintrag in sys.modules.
    for name in [n for n in sys.modules if n.partition(".")[0] in ("ctranslate2", "faster_whisper")]:
        del sys.modules[name]
    importlib.import_module("faster_whisper")
    return False
