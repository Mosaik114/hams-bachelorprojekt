"""Katalog der anwählbaren Whisper-Modelle und Erkennung des Installationsstands.

faster-whisper kennt neunzehn Bezeichner; angeboten werden nur die sechs
mehrsprachigen. Die Diktiersprache ist einstellbar (`dictation_language`) und
lässt sich ohne Neustart wechseln — das geht nur, weil jedes Modell im Katalog
alle Sprachen kann. Die `.en`-Varianten und sämtliche `distil-*`-Modelle
können nur Englisch; mit ihnen würde aus einem Sprachwechsel ein Modellwechsel
samt Download, und bei Turbo und Large v3 bringen sie für Englisch kaum etwas.
`large` ist nur ein Alias auf `large-v3`, `large-v1` und `large-v2` sind davon
überholt.

Wer einen anderen Wert braucht, trägt ihn weiterhin in `config.toml` ein — die
Anwendung reicht den String unverändert an faster-whisper durch.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from hams.hub_progress import _ProgressBar
from hams.i18n import decimal, t

logger = logging.getLogger("hams")


class WhisperModelInfo:
    """Ein Eintrag des kuratierten Katalogs."""

    __slots__ = ("model_id", "label", "size_bytes", "repo_id")

    def __init__(self, model_id: str, label: str, size_bytes: int, repo_id: str) -> None:
        self.model_id = model_id
        self.label = label
        self.size_bytes = size_bytes  #: am Hub gemessen, für Anzeige und Platzprüfung
        self.repo_id = repo_id  #: Hugging-Face-Repository, für den Cache

    @property
    def summary(self) -> str:
        """Kurze Einordnung, Produkttext — in der Sprache der Oberfläche."""
        return t(f"model.summary.{self.model_id}")

    @property
    def size_label(self) -> str:
        """Größe für die Anzeige, aus denselben Bytes wie die Fortschrittsanzeige."""
        return format_bytes(self.size_bytes)

    @property
    def meta(self) -> str:
        """Die zweite Zeile im Selector: Einordnung und Größe."""
        return f"{self.summary} · {self.size_label}"


#: Dezimal gerechnet: so weist der Hub die Dateigrössen aus, und nur so passen
#: Beschriftung und Fortschrittsanzeige zusammen.
_GB = 1000**3
_MB = 1000**2

#: Reihenfolge = Anzeigereihenfolge. Die Einordnungen sind Produkttexte und
#: bewusst zurückhaltend — wir haben keine eigenen Messwerte für Genauigkeit.
#: Die Grössen sind am Hub gemessen, über genau die Dateien in ALLOW_PATTERNS.
CATALOG: tuple[WhisperModelInfo, ...] = (
    WhisperModelInfo("turbo", "Turbo", 1_621_665_983, "mobiuslabsgmbh/faster-whisper-large-v3-turbo"),
    WhisperModelInfo("large-v3", "Large v3", 3_090_835_702, "Systran/faster-whisper-large-v3"),
    WhisperModelInfo("medium", "Medium", 1_530_571_735, "Systran/faster-whisper-medium"),
    WhisperModelInfo("small", "Small", 486_212_372, "Systran/faster-whisper-small"),
    WhisperModelInfo("base", "Base", 147_882_941, "Systran/faster-whisper-base"),
    WhisperModelInfo("tiny", "Tiny", 78_203_619, "Systran/faster-whisper-tiny"),
)

_BY_ID = {info.model_id: info for info in CATALOG}


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
    except Exception:  # Cache fehlt, ist beschädigt oder die API änderte sich
        logger.warning("Hugging Face cache unreadable - no model counts as installed", exc_info=True)
        return set()

    by_repo = {info.repo_id: info.model_id for info in CATALOG}
    found: set[str] = set()
    for repo in getattr(cache, "repos", ()):
        model_id = by_repo.get(getattr(repo, "repo_id", ""))
        if model_id is None:
            continue  # fremdes Repository — geht uns nichts an
        # `model.bin` ist die Datei, an der faster-whisper haengt. Ein
        # abgebrochener Download hinterlaesst durchaus schon config.json und
        # tokenizer.json — als installiert darf das nicht gelten, sonst boete
        # die Oberflaeche einen Wechsel an, der beim Laden scheitert.
        for revision in getattr(repo, "revisions", ()):
            names = {getattr(file, "file_name", "") for file in getattr(revision, "files", ())}
            if MODEL_FILE in names:
                found.add(model_id)
                break
    return found


#: Die Datei, ohne die faster-whisper ein Modell nicht laden kann.
MODEL_FILE = "model.bin"


class IncompleteDownload(Exception):
    """The download ended without the model file: as a failure, never as "installed"."""


class CachedModel:
    """Ein Katalog-Modell, das im Cache liegt — vollstaendig oder als Rest."""

    __slots__ = ("model_id", "label", "size_bytes", "complete", "revisions")

    def __init__(self, model_id: str, label: str, size_bytes: int, complete: bool, revisions: tuple[str, ...]) -> None:
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
        size_text = format_bytes(self.size_bytes)
        return size_text if self.complete else t("model.incomplete", size=size_text)


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
        logger.warning("Hugging Face cache unreadable - no cached models known", exc_info=True)
        return ()

    catalog_by_repo = {info.repo_id: info for info in CATALOG}
    found_models: dict[str, CachedModel] = {}
    for repo in getattr(cache, "repos", ()):
        info = catalog_by_repo.get(getattr(repo, "repo_id", ""))
        if info is None:
            continue  # fremdes Repository — geht uns nichts an
        revision_hashes = tuple(
            getattr(revision, "commit_hash", "")
            for revision in getattr(repo, "revisions", ())
            if getattr(revision, "commit_hash", "")
        )
        if not revision_hashes:
            continue
        is_complete = any(
            MODEL_FILE in {getattr(item, "file_name", "") for item in getattr(revision, "files", ())}
            for revision in getattr(repo, "revisions", ())
        )
        found_models[info.model_id] = CachedModel(
            info.model_id, info.label, int(getattr(repo, "size_on_disk", 0) or 0), is_complete, revision_hashes
        )
    return tuple(found_models[info.model_id] for info in CATALOG if info.model_id in found_models)


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
    cached = {entry.model_id: entry for entry in cached_whisper_models()}
    entry = cached.get(model_id)
    if entry is None:
        return False, 0, t("model.delete.missing", model=label)

    try:
        from huggingface_hub import scan_cache_dir

        strategy = scan_cache_dir().delete_revisions(*entry.revisions)
        expected = int(getattr(strategy, "expected_freed_size", 0) or 0)
        strategy.execute()
    except PermissionError:
        # Windows gibt eine geladene Modelldatei nicht her. Das ist der einzige
        # Fehler, der eine verstaendliche eigene Meldung verdient.
        logger.warning("Model %s is in use, not deleted", model_id)
        return False, 0, t("model.delete.in_use", model=label)
    except Exception:
        logger.exception("Model %s could not be deleted", model_id)
        return False, 0, t("model.delete.failed", model=label)

    freed = expected or entry.size_bytes
    logger.info("Model %s deleted, %s freed", model_id, format_bytes(freed))
    # Ohne belegten Platz keine Zahl: ein abgebrochener Download hinterlaesst
    # oft nur leere Ordner, und „0 B frei" waere eine Meldung ueber nichts.
    if freed <= 0:
        return True, 0, t("model.delete.done", model=label)
    return True, freed, t("model.delete.done_freed", model=label, size=format_bytes(freed))


def cache_root() -> Path | None:
    """Verzeichnis des Modell-Caches, für Platzprüfung und Log."""
    try:
        from huggingface_hub import constants

        return Path(constants.HF_HUB_CACHE)
    except Exception:  # pragma: no cover - nur bei kaputter Installation
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
    except OSError:  # pragma: no cover
        return None


# --------------------------------------------------------------------- Download

#: Genau die Dateien, die faster-whisper spaeter sucht. Ohne diese Auswahl
#: laedt der Hub das ganze Repository — bei Large v3 mehrere Gigabyte, die
#: nie jemand benutzt.
ALLOW_PATTERNS = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*")

#: Kennung (kein Anzeigetext), an der die Oberflaeche einen Abbruch von einem Fehler trennt.
CANCELLED = "cancelled"


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
    except Exception:  # pragma: no cover - nur bei kaputter Installation
        logger.warning("Xet could not be switched off", exc_info=True)
        return False


#: Whether the hub checks HTTPS against the system's certificate store; None until `use_system_certificates` ran.
_SYSTEM_CERTIFICATES: bool | None = None


def use_system_certificates() -> bool:
    """Lets the hub check HTTPS against the system's certificate store (Windows: the one the browser uses).

    httpx brings its own list (certifi). Antivirus programs that look into HTTPS (Avast, Kaspersky, ESET) and
    company networks sign the connection with a certificate of their own, which they add to the Windows store
    only: the browser and Hams' update check (urllib) trusted it, the model download did not, and failed with
    an SSL error although the internet worked. `truststore` asks the operating system instead.

    Called once before the first model access; later calls return the first answer. Without `truststore` (or
    with a hub that has no client factory) the hub keeps certifi, as before.

    Returns:
        True if the system store is used.
    """
    global _SYSTEM_CERTIFICATES
    if _SYSTEM_CERTIFICATES is not None:
        return _SYSTEM_CERTIFICATES
    try:
        import ssl

        import httpx
        import truststore
        from huggingface_hub import set_client_factory
        from huggingface_hub.utils import _http as hub_http

        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # The hub's own request hook (user agent, request id): without it the client would differ in more than
        # the certificates. Missing in another hub version: keep certifi rather than a half-equal client.
        hook = hub_http.hf_request_event_hook

        def client_factory() -> httpx.Client:
            # As `huggingface_hub.utils._http.default_client_factory`, plus `verify`. No timeout there either: the
            # hub sets its own per request, and a model file takes minutes.
            return httpx.Client(
                event_hooks={"request": [hook]},
                follow_redirects=True,
                timeout=None,  # noqa: S113 - as the hub's own client, see above
                verify=context,
            )

        set_client_factory(client_factory)
    except Exception:  # noqa: BLE001 - certifi stays: as safe as before, just not as forgiving
        logger.warning("System certificate store not used for model downloads", exc_info=True)
        _SYSTEM_CERTIFICATES = False
    else:
        logger.info("Model downloads check HTTPS against the system certificate store")
        _SYSTEM_CERTIFICATES = True
    return _SYSTEM_CERTIFICATES


class ModelDownloader:
    """Laedt genau ein Modell zur Zeit in den Hugging-Face-Cache.

    Bewusst ein eigenstaendiger Dienst und kein Widget: der Download laeuft
    weiter, wenn das Einstellungsfenster geschlossen wird.
    """

    #: Puffer ueber der bekannten Modellgroesse fuer die Platzpruefung.
    SPACE_MARGIN = 1.2
    #: Nicht bei jedem Datenblock melden — ein paar Mal je Sekunde reicht.
    REPORT_INTERVAL = 0.25
    #: Seconds before each automatic new attempt after trouble that often passes by itself (connection, a busy or
    #: broken server). Two attempts: a short hiccup and a slightly longer one; after that the user decides.
    RETRY_DELAYS: tuple[float, ...] = (5, 20)
    #: The countdown ticks once a second; tests make it faster.
    WAIT_TICK = 1.0

    def __init__(self) -> None:
        self._thread = None
        self._cancel = None
        self.model_id: str | None = None
        #: Models whose last attempt failed in a way a leftover piece can cause: the next start clears it first.
        self._start_clean: set[str] = set()

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def check_space(self, info: WhisperModelInfo) -> str:
        """Leerer String, wenn genug Platz ist — sonst der Grund."""
        free = free_space_bytes()
        if free is None:
            return ""  # nicht ermittelbar: nicht im Weg stehen
        needed = int(info.size_bytes * self.SPACE_MARGIN)
        if free < needed:
            return t("download.no_space", needed=format_bytes(needed), free=format_bytes(free))
        return ""

    def start(self, info: WhisperModelInfo, on_progress, on_finished, on_wait=None) -> str:
        """Startet den Download. Leerer String heisst: laeuft.

        Args:
            on_progress: (geladen, gesamt) in Bytes, aus dem Download-Thread.
                `gesamt` waechst noch, solange der Hub Dateien anmeldet.
            on_finished: (model_id, fehlertext) — leerer Text heisst Erfolg,
                `CANCELLED` heisst Abbruch durch den Nutzer.
            on_wait: (Sekunden) vor einem automatischen neuen Versuch, jede
                Sekunde einmal; 0, sobald es weitergeht. Aus dem Download-Thread.

        Returns:
            Der Grund, warum nicht gestartet wurde — oder "".
        """
        if self.active:
            return t("download.already_running")
        problem = self.check_space(info)
        if problem:
            return problem

        self._cancel = threading.Event()
        self.model_id = info.model_id
        self._thread = threading.Thread(
            target=self._run, args=(info, on_progress, on_finished, on_wait), daemon=True, name="model-download"
        )
        self._thread.start()
        return ""

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.set()

    def _run(self, info: WhisperModelInfo, on_progress, on_finished, on_wait=None) -> None:
        disable_xet_transfer()  # sonst greift der Abbruch erst nach Minuten
        use_system_certificates()
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
            if info.model_id in self._start_clean:
                # "Try again" after a failure a leftover piece can cause: begin from nothing.
                self._start_clean.discard(info.model_id)
                remove_partial_downloads(info)
            error = self._download_healing(info, on_wait)
        finally:
            _ProgressBar.cancel_flag = None
            _ProgressBar.report = None
            self.model_id = None
        on_finished(info.model_id, error)

    def _download_healing(self, info: WhisperModelInfo, on_wait=None) -> str:
        """Downloads, and repairs on its own what it can before the user hears of it.

        * A failure a broken leftover can cause: the leftover goes and the download starts once more from the
          beginning. Without this, a leftover `.incomplete` file larger than the real one made every attempt fail
          the same way (the hub resumes it and the server answers 416), and "Try again" could never help.
        * Trouble that often passes by itself (connection, a busy or broken server): after `RETRY_DELAYS` seconds,
          with a countdown through `on_wait`, it tries again. The leftover stays: the next attempt resumes it.

        Only when that did not help does the window show a sentence.

        Returns:
            "" on success, `CANCELLED`, or the sentence for the window.
        """
        repaired = False
        delays = list(self.RETRY_DELAYS)
        attempt = 0
        while True:
            attempt += 1
            try:
                self._fetch(info)
            except IncompleteDownload:
                logger.warning("Download of %s finished without %s", info.model_id, MODEL_FILE)
                return t("download.incomplete")
            except Exception as exc:  # noqa: BLE001 - in Klartext uebersetzen
                if self._cancelled():
                    # Der Abbruch kommt aus einem Arbeitsthread zurueck und ist
                    # unterwegs schon einmal eingepackt worden — die Ursache steht
                    # im Flag, nicht im Ausnahmetyp.
                    logger.info("Download of %s cancelled", info.model_id)
                    return CANCELLED
                logger.exception("Download of %s failed (attempt %d)", info.model_id, attempt)
                if leftover_can_cause(exc):
                    if not repaired and remove_partial_downloads(info):
                        repaired = True
                        logger.info(
                            "Download of %s: broken leftover removed, starting again from the beginning", info.model_id
                        )
                        continue
                    self._start_clean.add(info.model_id)
                    return describe_download_error(exc)
                if passes_by_itself(exc) and delays:
                    if not self._wait(delays.pop(0), on_wait):
                        logger.info("Download of %s cancelled while waiting for a new attempt", info.model_id)
                        return CANCELLED
                    logger.info("Download of %s: trying again", info.model_id)
                    continue
                return describe_download_error(exc)
            else:
                logger.info("Download of %s finished", info.model_id)
                return ""

    def _cancelled(self) -> bool:
        return self._cancel is not None and self._cancel.is_set()

    def _wait(self, seconds: float, on_wait) -> bool:
        """Counts down to a new attempt, once a tick. False if the user cancelled meanwhile.

        Every tick reports the seconds left (`on_wait`), the last one 0: the window then shows progress again.
        """
        remaining = max(0, round(seconds))
        try:
            while remaining > 0:
                if on_wait is not None:
                    on_wait(remaining)
                if self._cancel is not None and self._cancel.wait(self.WAIT_TICK):
                    return False
                if self._cancel is None:
                    time.sleep(self.WAIT_TICK)
                remaining -= 1
            return not self._cancelled()
        finally:
            if on_wait is not None:
                on_wait(0)

    @staticmethod
    def _fetch(info: WhisperModelInfo) -> None:
        from huggingface_hub import snapshot_download

        folder = snapshot_download(info.repo_id, tqdm_class=_ProgressBar, allow_patterns=list(ALLOW_PATTERNS))
        if not (Path(folder) / MODEL_FILE).is_file():
            # Erfolg heisst "installiert": dieselbe Probe wie `installed_whisper_models`.
            raise IncompleteDownload(f"{MODEL_FILE} missing in {info.repo_id}")


def repo_folder(info: WhisperModelInfo) -> Path | None:
    """The model's folder in the Hugging Face cache (``models--owner--name``), or None without a cache root."""
    root = cache_root()
    if root is None:
        return None
    return root / ("models--" + info.repo_id.replace("/", "--"))


def remove_partial_downloads(info: WhisperModelInfo) -> int:
    """Deletes the unfinished pieces (``blobs/*.incomplete``) of one model. Returns how many were removed.

    Only these files: finished files and the other models stay. A piece another process still holds is left
    alone (logged); the hub then reports its own error.
    """
    folder = repo_folder(info)
    if folder is None:
        return 0
    removed = 0
    for piece in (folder / "blobs").glob("*.incomplete"):
        try:
            size = piece.stat().st_size
            piece.unlink()
        except OSError as exc:
            logger.warning("Leftover %s of %s could not be removed: %s", piece.name, info.model_id, exc)
            continue
        removed += 1
        logger.info("Removed leftover %s of %s (%s)", piece.name, info.model_id, format_bytes(size))
    return removed


def describe_download_error(exc: BaseException) -> str:
    """Uebersetzt eine Ausnahme in einen Satz fuer die Oberflaeche.

    Der vollstaendige Stacktrace bleibt im Log; hier steht nur, was der Nutzer
    daraus machen kann.
    """
    kind = error_kind(exc)
    if kind == "disk_full":
        return t("download.error.disk_full")  # says it all; a number would not help anybody
    # Everything else carries a short number for the support: the HTTP status, else the type of the innermost
    # exception (`ConnectError`, `SSLCertVerificationError`, `ProxyError`), which tells more than the wrapper.
    status = http_status(exc)
    code = str(status) if status is not None else type(_exception_chain(exc)[-1]).__name__
    return t("download.error.with_code", message=t(f"download.error.{kind}"), code=code)


def split_code(text: str) -> tuple[str, str]:
    """A download sentence and its support number apart: ("…", "416"), or (text, "") without one.

    The window shows the number on its own, quieter than the sentence; it is for the support, not for the user.
    Relies on the form of ``download.error.with_code`` ("{message} ({code})"), the same in every language.
    """
    import re

    # Arabic on Windows: `t()` embeds every stretch in direction marks, the number included.
    marks = "[‎‏‪-‮⁦-⁩]*"
    match = re.fullmatch(rf"(.*\S)\s+{marks}\(([A-Za-z0-9_.]+)\){marks}", text or "", flags=re.DOTALL)
    if match is None:
        return text, ""
    return match.group(1), match.group(2)


#: Words in an exception that mean: the connection itself is the problem.
_NETWORK_MARKERS = (
    "connection",
    "timed out",
    "timeout",
    "network",
    "dns",
    "resolve",
    "unreachable",
    "getaddrinfo",
    "offline",
    "ssl",
)

#: Words that mean: there is a connection, but something in between does not let the download through - an
#: antivirus that looks into HTTPS, a company proxy, a network filter. Checked before `_NETWORK_MARKERS`.
_BLOCKED_MARKERS = (
    "certificate",
    "cert_verify",
    "self signed",
    "self-signed",
    "local issuer",
    "proxy",
)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """The exception and those it was raised from (`__cause__`, else `__context__`), outermost first.

    The hub wraps the real cause: an `SSLCertVerificationError` arrives as a `LocalEntryNotFoundError` that only
    says "check your connection".
    """
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not seen for seen in chain) and len(chain) < 12:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def http_status(exc: BaseException) -> int | None:
    """The HTTP status behind a hub error, if there is one (from a response, else from a message, outermost first)."""
    import re

    for item in _exception_chain(exc):
        response = getattr(item, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int) and 100 <= status <= 599:
            return status
        match = re.search(r"(?<![\d.])([45]\d\d)(?:\s+(?:client|server)\s+error|\s+[A-Z][a-z])", str(item))
        if match:
            return int(match.group(1))
        match = re.search(r"'([45]\d\d) ", str(item))
        if match:
            return int(match.group(1))
    return None


def error_kind(exc: BaseException) -> str:
    """One word for what went wrong.

    permission, disk_full, blocked (a protection program or the network stops it), network, server_busy, server,
    not_found or generic.
    """
    chain = _exception_chain(exc)
    text = " ".join(f"{type(item).__name__} {item}" for item in chain).lower()
    local = _local_kind(chain, text)
    if local:
        return local
    status = http_status(exc)
    if status is not None:
        return _status_kind(status, text)
    return _text_kind(text)


def _local_kind(chain: list[BaseException], text: str) -> str:
    """Trouble on this computer: missing write rights or a full disk; "" if neither."""
    if any(isinstance(item, PermissionError) for item in chain):
        return "permission"
    if any(isinstance(item, OSError) and getattr(item, "errno", None) == 28 for item in chain):
        return "disk_full"
    if "no space" in text or "not enough space" in text or "errno 28" in text:
        return "disk_full"
    return ""


def _status_kind(status: int, text: str) -> str:
    """The server answered: what its status says."""
    if status == 429:
        return "server_busy"
    if status >= 500:
        return "server"
    if status == 407:
        return "blocked"  # Proxy Authentication Required
    if status == 404 or "repositorynotfound" in text:
        return "not_found"
    return "generic"


def _text_kind(text: str) -> str:
    """No answer from the server: what the exceptions say."""
    if "repositorynotfound" in text:
        return "not_found"
    if any(marker in text for marker in _BLOCKED_MARKERS):
        return "blocked"
    # Before "entry not found": offline, the hub reports a missing local entry and names the connection.
    if any(marker in text for marker in _NETWORK_MARKERS):
        return "network"
    if "entrynotfound" in text or "404" in text or "not found" in text:
        return "not_found"
    return "generic"


def leftover_can_cause(exc: BaseException) -> bool:
    """Whether a broken unfinished piece in the cache may be behind this failure.

    Not for network trouble (the piece is what lets the next attempt resume), a blocked connection, a full disk,
    missing write rights, a busy or broken server, or a model that does not exist: deleting the piece would not
    change any of them.
    """
    return error_kind(exc) == "generic"


def passes_by_itself(exc: BaseException) -> bool:
    """Whether the same download may well work in a few seconds: a dropped connection, a busy or broken server.

    Not a blocked connection (an antivirus or proxy does not change its mind in 20 seconds), and nothing on this
    computer.
    """
    return error_kind(exc) in ("network", "server_busy", "server")


#: Former name, kept for callers outside the package.
_describe_download_error = describe_download_error


def format_bytes(value: int) -> str:
    """Kompakte Groessenangabe fuer die Oberflaeche."""
    if value >= _GB:
        return f"{decimal(value / _GB)} GB"
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
        return f"{decimal(done / _GB)}/{decimal(total / _GB)} GB"
    if total >= _MB:
        return f"{done / _MB:.0f}/{total / _MB:.0f} MB"
    return f"{int(done)}/{int(total)} B"
