"""Katalog der anwählbaren Whisper-Modelle und Erkennung des Installationsstands.

faster-whisper kennt neunzehn Bezeichner, aber die meisten sind für dieses
Produkt sinnlos: alle `.en`-Varianten und sämtliche `distil-*`-Modelle sind
englischsprachig, Wisper transkribiert Deutsch; `large` ist nur ein Alias auf
`large-v3`, `large-v1` und `large-v2` sind davon überholt. Übrig bleiben sechs.

Wer einen anderen Wert braucht, trägt ihn weiterhin in `config.toml` ein — die
Anwendung reicht den String unverändert an faster-whisper durch.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("wisper")


class WhisperModelInfo:
    """Ein Eintrag des kuratierten Katalogs."""

    __slots__ = ("model_id", "label", "summary", "size_label", "size_bytes", "repo_id")

    def __init__(self, model_id: str, label: str, summary: str,
                 size_label: str, size_bytes: int, repo_id: str) -> None:
        self.model_id = model_id
        self.label = label
        self.summary = summary          #: kurze Einordnung, Produkttext
        self.size_label = size_label    #: für die Anzeige
        self.size_bytes = size_bytes    #: grobe Schätzung, für die Platzprüfung
        self.repo_id = repo_id          #: Hugging-Face-Repository, für den Cache

    @property
    def meta(self) -> str:
        """Die zweite Zeile im Selector: Einordnung und Größe."""
        return f"{self.summary} · {self.size_label}"


#: Dezimal gerechnet: so weist der Hub die Dateigrössen aus, und nur so passen
#: Beschriftung und Fortschrittsanzeige zusammen.
_GB = 1000 ** 3
_MB = 1000 ** 2

#: Reihenfolge = Anzeigereihenfolge. Die Einordnungen sind Produkttexte und
#: bewusst zurückhaltend — wir haben keine eigenen Messwerte für Genauigkeit.
#: Die Grössen sind am Hub gemessen, über genau die Dateien in ALLOW_PATTERNS.
CATALOG: tuple[WhisperModelInfo, ...] = (
    WhisperModelInfo("turbo", "Turbo", "Schnell · sehr gut", "1,6 GB",
                     1_621_665_983, "mobiuslabsgmbh/faster-whisper-large-v3-turbo"),
    WhisperModelInfo("large-v3", "Large v3", "Höchste Genauigkeit", "3,1 GB",
                     3_090_835_702, "Systran/faster-whisper-large-v3"),
    WhisperModelInfo("medium", "Medium", "Ausgewogen", "1,5 GB",
                     1_530_571_735, "Systran/faster-whisper-medium"),
    WhisperModelInfo("small", "Small", "Schnell", "486 MB",
                     486_212_372, "Systran/faster-whisper-small"),
    WhisperModelInfo("base", "Base", "Sehr schnell", "148 MB",
                     147_882_941, "Systran/faster-whisper-base"),
    WhisperModelInfo("tiny", "Tiny", "Nur für Tests", "78 MB",
                     78_203_619, "Systran/faster-whisper-tiny"),
)

_BY_ID = {info.model_id: info for info in CATALOG}


def catalog_ids() -> tuple[str, ...]:
    return tuple(info.model_id for info in CATALOG)


def find(model_id: str) -> WhisperModelInfo | None:
    """Katalogeintrag zu einer ID, oder None bei einem eigenen Config-Wert."""
    return _BY_ID.get(model_id)


def installed_whisper_models() -> set[str]:
    """IDs der Modelle, die lokal im Hugging-Face-Cache liegen.

    Bewusst konservativ: Lässt sich der Cache nicht lesen, gilt kein Modell als
    installiert. Ein unbekannter Zustand darf nicht als „vorhanden“ erfunden
    werden — sonst böte die Oberfläche einen Wechsel an, der dann doch minutenlang
    lädt.
    """
    try:
        from huggingface_hub import scan_cache_dir

        cache = scan_cache_dir()
    except Exception:      # Cache fehlt, ist beschädigt oder die API änderte sich
        logger.warning("Hugging-Face-Cache nicht lesbar — kein Modell gilt als installiert",
                       exc_info=True)
        return set()

    by_repo = {info.repo_id: info.model_id for info in CATALOG}
    found: set[str] = set()
    for repo in getattr(cache, "repos", ()):
        model_id = by_repo.get(getattr(repo, "repo_id", ""))
        if model_id is None:
            continue          # fremdes Repository — geht uns nichts an
        # `model.bin` ist die Datei, an der faster-whisper haengt. Ein
        # abgebrochener Download hinterlaesst durchaus schon config.json und
        # tokenizer.json — als installiert darf das nicht gelten, sonst boete
        # die Oberflaeche einen Wechsel an, der beim Laden scheitert.
        for revision in getattr(repo, "revisions", ()):
            names = {getattr(file, "file_name", "")
                     for file in getattr(revision, "files", ())}
            if MODEL_FILE in names:
                found.add(model_id)
                break
    return found


#: Die Datei, ohne die faster-whisper ein Modell nicht laden kann.
MODEL_FILE = "model.bin"


class CachedModel:
    """Ein Katalog-Modell, das im Cache liegt — vollstaendig oder als Rest."""

    __slots__ = ("model_id", "label", "size_bytes", "complete", "revisions")

    def __init__(self, model_id: str, label: str, size_bytes: int,
                 complete: bool, revisions: tuple[str, ...]) -> None:
        self.model_id = model_id
        self.label = label
        self.size_bytes = size_bytes
        #: True, wenn `model.bin` vorhanden ist. False heisst: Rest eines
        #: abgebrochenen Downloads — belegt Platz, laesst sich aber nicht laden.
        self.complete = complete
        self.revisions = revisions

    @property
    def meta(self) -> str:
        """Zweite Zeile im Auswahlfeld: Groesse, und ob es vollstaendig ist."""
        groesse = format_bytes(self.size_bytes)
        return groesse if self.complete else f"{groesse} · unvollständig"


def cached_whisper_models() -> tuple[CachedModel, ...]:
    """Was vom Katalog wirklich auf der Platte liegt, in Katalogreihenfolge.

    Anders als `installed_whisper_models()` zaehlt hier auch mit, was ein
    abgebrochener Download hinterlassen hat. Genau das will man loeschen
    koennen: es belegt Platz, ist aber zu nichts zu gebrauchen.

    Bei einem unlesbaren Cache kommt eine leere Liste zurueck — dieselbe
    vorsichtige Haltung wie bei der Installationserkennung: nichts erfinden.
    """
    try:
        from huggingface_hub import scan_cache_dir

        cache = scan_cache_dir()
    except Exception:
        logger.warning("Hugging-Face-Cache nicht lesbar — kein Bestand ermittelbar",
                       exc_info=True)
        return ()

    nach_repo = {info.repo_id: info for info in CATALOG}
    gefunden: dict[str, CachedModel] = {}
    for repo in getattr(cache, "repos", ()):
        info = nach_repo.get(getattr(repo, "repo_id", ""))
        if info is None:
            continue          # fremdes Repository — geht uns nichts an
        revisionen = tuple(getattr(revision, "commit_hash", "")
                           for revision in getattr(repo, "revisions", ())
                           if getattr(revision, "commit_hash", ""))
        if not revisionen:
            continue
        vollstaendig = any(
            MODEL_FILE in {getattr(datei, "file_name", "")
                           for datei in getattr(revision, "files", ())}
            for revision in getattr(repo, "revisions", ())
        )
        gefunden[info.model_id] = CachedModel(
            info.model_id, info.label, int(getattr(repo, "size_on_disk", 0) or 0),
            vollstaendig, revisionen)
    return tuple(gefunden[info.model_id] for info in CATALOG
                 if info.model_id in gefunden)


def delete_whisper_model(model_id: str) -> tuple[bool, int, str]:
    """Loescht ein Modell aus dem Cache. Gibt (Erfolg, freie Bytes, Meldung).

    Ueber `scan_cache_dir().delete_revisions()` statt `shutil.rmtree` auf dem
    Ordner: der Cache teilt Blobs zwischen Revisionen, und die offizielle
    Loeschstrategie raeumt Blobs, Snapshots und Referenzen zusammen ab. Ein
    Ordner von Hand entfernt liesse den Cache in einem Zustand zurueck, den
    `scan_cache_dir()` beim naechsten Mal bemaengelt.

    Der Aufrufer entscheidet, *ob* geloescht werden darf — diese Funktion
    prueft nur, ob es etwas zu loeschen gibt. Was gerade geladen ist, weiss
    sie nicht.
    """
    info = find(model_id)
    label = info.label if info else model_id
    bestand = {eintrag.model_id: eintrag for eintrag in cached_whisper_models()}
    eintrag = bestand.get(model_id)
    if eintrag is None:
        return False, 0, f"{label} liegt nicht im Speicher"

    try:
        from huggingface_hub import scan_cache_dir

        strategie = scan_cache_dir().delete_revisions(*eintrag.revisions)
        erwartet = int(getattr(strategie, "expected_freed_size", 0) or 0)
        strategie.execute()
    except PermissionError:
        # Windows gibt eine geladene Modelldatei nicht her. Das ist der einzige
        # Fehler, der eine verstaendliche eigene Meldung verdient.
        logger.warning("Modell %s ist in Benutzung, nicht geloescht", model_id)
        return False, 0, f"{label} wird gerade benutzt"
    except Exception:
        logger.exception("Modell %s konnte nicht geloescht werden", model_id)
        return False, 0, f"{label} konnte nicht gelöscht werden"

    frei = erwartet or eintrag.size_bytes
    logger.info("Modell %s geloescht, %s frei", model_id, format_bytes(frei))
    # Ohne belegten Platz keine Zahl: ein abgebrochener Download hinterlaesst
    # oft nur leere Ordner, und „0 B frei" waere eine Meldung ueber nichts.
    if frei <= 0:
        return True, 0, f"{label} gelöscht"
    return True, frei, f"{label} gelöscht · {format_bytes(frei)} frei"


def cache_root() -> Path | None:
    """Verzeichnis des Modell-Caches, für Platzprüfung und Log."""
    try:
        from huggingface_hub import constants

        return Path(constants.HF_HUB_CACHE)
    except Exception:      # pragma: no cover - nur bei kaputter Installation
        return None


def free_space_bytes() -> int | None:
    """Freier Platz auf dem Laufwerk des Caches; None, wenn nicht ermittelbar."""
    root = cache_root()
    if root is None:
        return None
    probe = root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:      # pragma: no cover
        return None


# --------------------------------------------------------------------- Download

#: Genau die Dateien, die faster-whisper spaeter sucht. Ohne diese Auswahl
#: laedt der Hub das ganze Repository — bei Large v3 mehrere Gigabyte, die
#: nie jemand benutzt.
ALLOW_PATTERNS = ("config.json", "preprocessor_config.json", "model.bin",
                  "tokenizer.json", "vocabulary.*")

#: Fehlertext, an dem die Oberflaeche einen Abbruch von einem Fehler trennt.
CANCELLED = "abgebrochen"


#: Wo cuBLAS und cuDNN liegen koennen, in dieser Reihenfolge durchsucht.
#: Erst die eigenstaendigen NVIDIA-Pakete, dann torch. Die schlanke
#: Installation soll gewinnen, wenn beides da ist.
CUDA_LIBRARY_PACKAGES = (
    ("nvidia.cublas", "bin"),
    ("nvidia.cudnn", "bin"),
    ("torch", "lib"),
)


def _paket_verzeichnis(paket: str, unterordner: str) -> Optional[Path]:
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
    if wurzel is None:                       # Namensraum-Paket wie "nvidia"
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
    angemeldet: list[str] = []
    for paket, unterordner in CUDA_LIBRARY_PACKAGES:
        ordner = _paket_verzeichnis(paket, unterordner)
        if ordner is None:
            continue
        try:
            os.add_dll_directory(str(ordner))
        except OSError:                      # pragma: no cover - Pfad verschwunden
            logger.warning("CUDA-Bibliotheken unter %s nicht anmeldbar", ordner)
            continue
        angemeldet.append(str(ordner))
    if not angemeldet:
        logger.warning("Keine CUDA-Bibliotheken gefunden — Transkription "
                       "laeuft auf der CPU. Fehlt nvidia-cublas-cu12 / "
                       "nvidia-cudnn-cu12?")
    return tuple(angemeldet)


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
    """Laedt faster-whisper, ohne torch und transformers mitzuziehen.

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

    und Wisper faellt auf die CPU zurueck. Deshalb wird der Ordner vorher
    angemeldet — das kostet 0,3 ms und laedt kein Python-Modul.

    Die Sperre gilt nur waehrend des Imports und wird danach wieder
    abgeraeumt; wer torch spaeter braucht, bekommt es. Schlaegt der Import
    trotzdem fehl, wird er ohne Sperre wiederholt — dann kostet der Start
    wieder die 1,4 s, aber die Anwendung startet.

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
        logger.warning("faster-whisper liess sich ohne torch nicht laden — "
                       "zweiter Versuch ohne Sperre", exc_info=True)
    finally:
        try:
            sys.meta_path.remove(sperre)
        except ValueError:      # pragma: no cover - hat jemand anders entfernt
            pass

    # Aufraeumen, damit der zweite Anlauf nicht auf halb geladenen Modulen
    # aufsetzt: was die Sperre abgewiesen hat, steht sonst als kaputter
    # Eintrag in sys.modules.
    for name in [n for n in sys.modules
                 if n.partition(".")[0] in ("ctranslate2", "faster_whisper")]:
        del sys.modules[name]
    importlib.import_module("faster_whisper")
    return False


def disable_xet_transfer() -> bool:
    """Schaltet den Xet-Transport ab. True, wenn der Schalter gesetzt werden konnte.

    Gemessen mit Small (0,5 GB): ueber Xet dauerte ein Abbruch 47 Sekunden — die
    Ausnahme aus dem Fortschritts-Rueckruf haelt den Rust-Downloader nicht an —
    und die angefangene Datei blieb leer, der zweite Anlauf begann wieder bei
    null. Ueber den klassischen Weg greift derselbe Abbruch nach 0,9 Sekunden,
    die geladenen 20 MB bleiben liegen und die Fortsetzung zaehlt sie mit. Ein
    Geschwindigkeitsvorteil von Xet war dabei nicht messbar (47 s gegen 43 s).

    `is_xet_available()` liest die Konstante bei jedem Aufruf neu — die
    Reihenfolge der Importe spielt deshalb keine Rolle.
    """
    try:
        from huggingface_hub import constants

        constants.HF_HUB_DISABLE_XET = True
        return True
    except Exception:      # pragma: no cover - nur bei kaputter Installation
        logger.warning("Xet liess sich nicht abschalten", exc_info=True)
        return False


class DownloadCancelled(Exception):
    """Der Nutzer hat den Download abgebrochen."""


class _ProgressBar:
    """Ersetzt tqdm in `snapshot_download` — fuer Fortschritt und Abbruch.

    Der Hub legt genau zwei Balken dieser Klasse an: einen fuer die Bytes (ohne
    Iterable, `unit="B"`), dessen `total` die einzelnen Dateidownloads von aussen
    hochzaehlen, und einen fuer die Dateizahl, der ueber `thread_map` das
    Ergebnis-Iterable umschliesst. Der zweite *muss* durchlaufen werden: sonst
    werden die Futures der Arbeitsthreads nie abgeholt, und ein Fehler beim
    Laden verschwindet stillschweigend.

    Der Byte-Balken ist zugleich die einzige Stelle, an der ein Abbruch greifen
    kann. Python-Threads lassen sich nicht von aussen beenden; hier wird
    waehrend des Ladens regelmaessig zurueckgefragt.
    """

    #: Werden vor jedem Lauf gesetzt. Es laeuft immer nur ein Download.
    cancel_flag = None
    report = None

    def __init__(self, iterable=None, *_args, **kwargs) -> None:
        self._guard = threading.Lock()
        self._total = 0
        self.iterable = iterable
        self.total = kwargs.get("total") or 0
        self.n = kwargs.get("initial") or 0
        self.desc = kwargs.get("desc", "")
        self.disable = False
        #: Nur der Byte-Balken kommt ohne Iterable — der andere zaehlt Dateien.
        self._counts_bytes = iterable is None

    @property
    def total(self) -> int:
        return self._total

    @total.setter
    def total(self, value) -> None:
        """Waechst nur. `bytes_progress.total += n` laeuft aus acht Threads.

        Der Hub liest, addiert und schreibt ohne Sperre; ein veralteter
        Schreibvorgang machte die Gesamtgroesse sonst wieder kleiner und die
        Anzeige sprang von "0/78 MB" auf "13/76 MB" zurueck.
        """
        with self._guard:
            self._total = max(self._total, int(value or 0))

    # ------------------------------------------------ tqdm-Schnittstelle

    def __iter__(self):
        for item in self.iterable if self.iterable is not None else ():
            yield item
            self.update(1)

    def update(self, n=1) -> None:
        with self._guard:
            self.n += n or 0
            done, total = self.n, self.total
        if not self._counts_bytes:
            return
        if _ProgressBar.cancel_flag is not None and _ProgressBar.cancel_flag.is_set():
            raise DownloadCancelled()
        if _ProgressBar.report is not None:
            _ProgressBar.report(int(done), int(total))

    def close(self) -> None:
        pass

    def refresh(self) -> None:
        pass

    def set_description(self, *_args, **_kwargs) -> None:
        pass

    def set_description_str(self, *_args, **_kwargs) -> None:
        pass

    def set_postfix(self, *_args, **_kwargs) -> None:
        pass

    def write(self, *_args, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # `thread_map` teilt seine Sperre ueber die Balkenklasse. Ohne diese beiden
    # Methoden bricht tqdm ab, bevor ueberhaupt eine Datei geladen wird.
    @classmethod
    def get_lock(cls):
        lock = getattr(cls, "_lock", None)
        if lock is None:
            lock = threading.RLock()
            cls._lock = lock
        return lock

    @classmethod
    def set_lock(cls, lock) -> None:
        cls._lock = lock


class ModelDownloader:
    """Laedt genau ein Modell zur Zeit in den Hugging-Face-Cache.

    Bewusst ein eigenstaendiger Dienst und kein Widget: der Download laeuft
    weiter, wenn das Einstellungsfenster geschlossen wird.
    """

    #: Puffer ueber der bekannten Modellgroesse fuer die Platzpruefung.
    SPACE_MARGIN = 1.2
    #: Nicht bei jedem Datenblock melden — ein paar Mal je Sekunde reicht.
    REPORT_INTERVAL = 0.25

    def __init__(self) -> None:
        self._thread = None
        self._cancel = None
        self.model_id: str | None = None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def check_space(self, info: WhisperModelInfo) -> str:
        """Leerer String, wenn genug Platz ist — sonst der Grund."""
        free = free_space_bytes()
        if free is None:
            return ""      # nicht ermittelbar: nicht im Weg stehen
        needed = int(info.size_bytes * self.SPACE_MARGIN)
        if free < needed:
            return (f"Zu wenig Speicherplatz — {format_bytes(needed)} nötig, "
                    f"{format_bytes(free)} frei")
        return ""

    def start(self, info: WhisperModelInfo, on_progress, on_finished) -> str:
        """Startet den Download. Leerer String heisst: laeuft.

        Args:
            on_progress: (geladen, gesamt) in Bytes, aus dem Download-Thread.
                `gesamt` waechst noch, solange der Hub Dateien anmeldet.
            on_finished: (model_id, fehlertext) — leerer Text heisst Erfolg,
                `CANCELLED` heisst Abbruch durch den Nutzer.

        Returns:
            Der Grund, warum nicht gestartet wurde — oder "".
        """
        if self.active:
            return "Es läuft bereits ein Download"
        problem = self.check_space(info)
        if problem:
            return problem

        self._cancel = threading.Event()
        self.model_id = info.model_id
        self._thread = threading.Thread(
            target=self._run, args=(info, on_progress, on_finished),
            daemon=True, name="model-download")
        self._thread.start()
        return ""

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.set()

    def _run(self, info: WhisperModelInfo, on_progress, on_finished) -> None:
        disable_xet_transfer()      # sonst greift der Abbruch erst nach Minuten
        last = [0.0]

        def report(done: int, total: int) -> None:
            now = time.monotonic()
            if now - last[0] >= self.REPORT_INTERVAL:
                last[0] = now
                on_progress(done, total)

        _ProgressBar.cancel_flag = self._cancel
        _ProgressBar.report = report

        error = ""
        try:
            from huggingface_hub import snapshot_download

            snapshot_download(info.repo_id, tqdm_class=_ProgressBar,
                              allow_patterns=list(ALLOW_PATTERNS))
        except Exception as exc:      # noqa: BLE001 - in Klartext uebersetzen
            if self._cancel is not None and self._cancel.is_set():
                # Der Abbruch kommt aus einem Arbeitsthread zurueck und ist
                # unterwegs schon einmal eingepackt worden — die Ursache steht
                # im Flag, nicht im Ausnahmetyp.
                error = CANCELLED
                logger.info("Download von %s abgebrochen", info.model_id)
            else:
                error = _describe_download_error(exc)
                logger.exception("Download von %s fehlgeschlagen", info.model_id)
        else:
            logger.info("Download von %s abgeschlossen", info.model_id)
        finally:
            _ProgressBar.cancel_flag = None
            _ProgressBar.report = None
            self.model_id = None
        on_finished(info.model_id, error)


def _describe_download_error(exc: BaseException) -> str:
    """Uebersetzt eine Ausnahme in einen Satz fuer die Oberflaeche.

    Der vollstaendige Stacktrace bleibt im Log; hier steht nur, was der Nutzer
    daraus machen kann.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    if isinstance(exc, PermissionError):
        return "Kein Schreibrecht im Modellordner"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return "Kein Speicherplatz mehr frei"
    if "no space" in text or "not enough space" in text or "errno 28" in text:
        return "Kein Speicherplatz mehr frei"
    for marker in ("connection", "timed out", "timeout", "network", "dns",
                   "resolve", "unreachable", "getaddrinfo", "offline",
                   "proxy", "ssl"):
        if marker in text:
            return "Keine Verbindung zum Modell-Server"
    if "404" in text or "not found" in text or "repositorynotfound" in text:
        return "Modell nicht gefunden"
    return "Download fehlgeschlagen"


def format_bytes(value: int) -> str:
    """Kompakte Groessenangabe fuer die Oberflaeche."""
    if value >= _GB:
        return f"{value / _GB:.1f} GB".replace(".", ",")
    if value >= _MB:
        return f"{value / _MB:.0f} MB"
    return f"{max(0, int(value))} B"


def format_progress(done: int, total: int) -> str:
    """Beide Zahlen in der Einheit der groesseren: "13/78 MB".

    Zwei volle Groessenangaben nebeneinander passen nicht in eine
    Einstellungszeile — und zwei verschiedene Einheiten liest ohnehin niemand.
    """
    done = max(0, min(int(done), int(total)))
    if total >= _GB:
        return f"{done / _GB:.1f}/{total / _GB:.1f} GB".replace(".", ",")
    if total >= _MB:
        return f"{done / _MB:.0f}/{total / _MB:.0f} MB"
    return f"{int(done)}/{int(total)} B"
