"""The local Ollama service: the one HTTP client Hams talks to it through, and what Hams knows about it.

`OllamaClient` is the only place that builds Ollama requests: text generation for the cleanup and *Ask AI*
(streamed, with a live character count), the warm-up at start, the installed and loaded models (`/api/tags`,
`/api/ps`) and `pull`, with which the setup assistant fetches the default cleanup model. Its size is only known once
a layer starts downloading, so the progress adds up what has been announced so far.

The model lists in the settings show only what is installed and never download anything.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from hams.i18n import t
from hams.models import format_bytes

logger = logging.getLogger("hams")

#: Kurz halten: der Aufruf hängt an der periodischen Prüfung des
#: Einstellungsfensters und darf den Hintergrundthread nicht blockieren.
TIMEOUT = 2.0


def pull_hint() -> str:
    """Hinweiszeile im Popover. Kein auswählbarer Eintrag — nur eine Auskunft."""
    return t("ollama.pull_hint")


#: Schreibweisen, die sich nicht aus dem Namen ergeben.
_WORD_NAMES = {"gpt": "GPT", "oss": "OSS", "deepseek": "DeepSeek", "qwq": "QwQ"}


def display_name(name: str) -> str:
    """Macht aus dem Ollama-Namen einen lesbaren: `qwen2.5:7b` → `Qwen 2.5 · 7B`.

    Nur für die Anzeige. Gespeichert und an Ollama geschickt wird weiter der
    rohe Name. Ein Namensraum (`hf.co/nutzer/modell`) entfällt, `latest` auch.
    """
    base, _, tag = name.strip().rpartition(":") if ":" in name else (name.strip(), "", "")
    base = base.rsplit("/", 1)[-1]
    words = []
    for part in re.split(r"[-_]", base):
        if not part:
            continue
        # Buchstaben und Versionsnummer trennen: "llama3.2" → "Llama 3.2".
        # Kurze Kürzel bleiben zusammen: "r1" → "R1".
        match = re.fullmatch(r"([a-zA-Z]{3,})(\d[\d.]*)", part)
        pieces = match.groups() if match else (part,)
        for piece in pieces:
            words.append(_WORD_NAMES.get(piece.lower(), piece[:1].upper() + piece[1:]))
    label = " ".join(words) or name
    tag = tag.strip()
    # Größenangabe vorn groß: "7b" → "7B", "4b-it-qat" → "4B-it-qat"
    tag = re.sub(r"^(\d+(?:\.\d+)?)([bm])(?=$|-)", lambda m: m[1] + m[2].upper(), tag, flags=re.IGNORECASE)
    if tag and tag.lower() != "latest":
        label = f"{label} · {tag}"
    return label


class OllamaModel:
    """Ein installiertes Modell, so wie `/api/tags` es beschreibt."""

    __slots__ = ("name", "size_bytes", "parameter_size", "family")

    def __init__(self, name: str, size_bytes: int = 0, parameter_size: str = "", family: str = "") -> None:
        self.name = name
        self.size_bytes = size_bytes
        self.parameter_size = parameter_size  #: z. B. "8.9B", oft leer
        self.family = family

    @property
    def meta(self) -> str:
        """Zweite Zeile im Popover — nur, was die API tatsächlich liefert.

        Aus dem Namen etwas abzuleiten wäre geraten: `qwen2.5:3b` hat 3,1 Mrd.
        Parameter, `ministral-3:8b` deren 8,9. Fehlt eine Angabe, fehlt sie.
        """
        parts = []
        if self.parameter_size:
            parts.append(self.parameter_size.replace(".", t("number.decimal_separator")))
        if self.size_bytes > 0:
            parts.append(format_bytes(self.size_bytes))
        return " · ".join(parts)

    def __repr__(self) -> str:  # pragma: no cover - nur fürs Log
        return f"OllamaModel({self.name!r})"


class OllamaState:
    """Momentaufnahme des Dienstes.

    `warm` ist eine Menge: Ollama kann mehrere Modelle gleichzeitig im Speicher
    halten, und `keep_alive` sorgt genau dafür.
    """

    __slots__ = ("online", "models", "warm", "error")

    def __init__(self, online: bool, models=(), warm=(), error: str = "") -> None:
        self.online = online
        self.models: tuple[OllamaModel, ...] = tuple(models)
        self.warm: frozenset[str] = frozenset(warm)
        self.error = error

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(model.name for model in self.models)

    def find(self, name: str) -> OllamaModel | None:
        for model in self.models:
            if model.name == name:
                return model
        return None

    def signature(self) -> tuple:
        """Alles, was die Oberfläche beeinflusst — und sonst nichts.

        Die periodische Prüfung läuft alle fünf Sekunden. Ohne diesen Vergleich
        würde jedes Mal die ganze Auswahlliste neu gebaut, ein offenes Popover
        fiele zu und der Tastaturfokus ginge verloren.
        """
        return (self.online, self.names, tuple(sorted(self.warm)), self.error)

    def __eq__(self, other) -> bool:
        return isinstance(other, OllamaState) and self.signature() == other.signature()

    def __repr__(self) -> str:  # pragma: no cover - nur fürs Log
        return f"OllamaState(online={self.online}, models={len(self.models)}, warm={sorted(self.warm)})"


def base_url(generate_url: str) -> str:
    """Dienstadresse aus der konfigurierten `/api/generate`-Adresse.

    Die Konfiguration nennt seit jeher den Generate-Endpunkt; Tags und Ps
    liegen daneben. Reine Textumformung, damit es genau eine Stelle gibt.
    """
    url = (generate_url or "").strip()
    head = url.rsplit("/api/", 1)[0]
    return (head or url).rstrip("/")


#: Everything a failing Ollama request can raise: no connection, a connection dropped mid-answer
#: (ConnectionResetError, RemoteDisconnected, IncompleteRead), a timeout, broken JSON or an answer of the
#: wrong shape. Each of them must end in the raw text, never in a lost dictation.
OLLAMA_ERRORS = (OSError, http.client.HTTPException, ValueError, KeyError, TypeError)


def is_unreachable(exc: BaseException) -> bool:
    """Whether `exc` means no Ollama answered at all, rather than a bad answer from a running one.

    Only this pauses the cleanup for a while; an HTTP error status comes from a reachable server.
    """
    return isinstance(exc, (OSError, http.client.HTTPException)) and not isinstance(exc, urllib.error.HTTPError)


class OllamaAnswerError(ValueError):
    """Ollama answered, but not with a usable text: an `error` field, a cut-off stream or an empty answer."""


#: A streamed answer reports its length at most this often (seconds between two reports).
PROGRESS_INTERVAL = 0.25


def _parse_models(payload: dict) -> tuple[OllamaModel, ...]:
    """Liest `/api/tags`. Unbekannte Felder werden übergangen, nicht geraten."""
    found = []
    for entry in _entries(payload):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("model") or ""
        if not name:
            continue
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        size = entry.get("size")
        found.append(
            OllamaModel(
                str(name),
                int(size) if isinstance(size, (int, float)) and size > 0 else 0,
                str(details.get("parameter_size") or ""),
                str(details.get("family") or ""),
            )
        )
    return tuple(found)


def _entries(payload) -> list:
    """The ``models`` list of an answer; anything of another shape counts as none."""
    entries = payload.get("models") if isinstance(payload, dict) else None
    return entries if isinstance(entries, list) else []


def _describe_error(exc: BaseException) -> str:
    """Ein Satz für die Oberfläche, kein Stacktrace."""
    if isinstance(exc, urllib.error.HTTPError):
        return t("ollama.http_error", code=exc.code)
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return t("ollama.bad_response")
    return t("ollama.unreachable")


# --------------------------------------------------------------------- client and pull

#: Zwischen zwei Statuszeilen von `/api/pull` vergeht selten mehr als eine
#: Sekunde; eine Minute Stille heißt, dass die Verbindung hängt.
PULL_TIMEOUT = 60.0


class PullError(RuntimeError):
    """Ollama hat das Modell nicht geladen; die Nachricht ist für die Oberfläche gedacht."""


class PullCancelled(PullError):
    """Der Nutzer hat abgebrochen. Ollama behält, was schon da ist, und setzt beim nächsten Mal fort."""


def probe_or_offline(probe) -> OllamaState:
    """`probe()`, or an offline state with a sentence if it fails in any way.

    For the background threads of the settings and the setup assistant, which must leave "checking" whatever
    happens.
    """
    try:
        return probe()
    except Exception:  # noqa: BLE001 - boundary of the callers' threads
        logger.exception("Ollama check failed")
        return OllamaState(False, (), (), t("ollama.bad_response"))


class OllamaClient:
    """The one HTTP client for the local Ollama service.

    `base` is the service address (`http://127.0.0.1:11434`), or a function returning it, so a client created at
    start follows the setting. `opener` replaces `urllib.request.urlopen` (tests, `tools/cleanup_eval.py`); without
    one, `urlopen` is looked up at every request.
    """

    def __init__(self, base: str | Callable[[], str], *, opener=None) -> None:
        self._base = base
        self._opener = opener

    @classmethod
    def for_url(cls, generate_url: str | Callable[[], str], *, opener=None) -> OllamaClient:
        """A client for the configured `/api/generate` address (`[ollama] url`)."""
        if callable(generate_url):
            return cls(lambda: base_url(generate_url()), opener=opener)
        return cls(base_url(generate_url), opener=opener)

    @property
    def base(self) -> str:
        return self._base() if callable(self._base) else self._base

    def _open(self, request: urllib.request.Request, timeout: float):
        opener = self._opener or urllib.request.urlopen
        return opener(request, timeout=timeout)

    def _post(self, path: str, body: dict, timeout: float):
        # The address comes from the settings; scheme and host are checked in config_validation.ollama_url_problem.
        request = urllib.request.Request(  # noqa: S310
            f"{self.base}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        return self._open(request, timeout)

    def _get_json(self, path: str, timeout: float):
        request = urllib.request.Request(f"{self.base}{path}", headers={"Accept": "application/json"})  # noqa: S310
        with self._open(request, timeout) as response:
            return json.loads(response.read())

    # ------------------------------------------------------------------ text

    def generate(
        self,
        prompt: str,
        model: str,
        *,
        timeout: float,
        keep_alive: str,
        system: str | None = None,
        options: dict | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> str:
        """The model's answer to `prompt`, stripped.

        With `on_progress` the answer is streamed and `on_progress(characters so far)` is called at most four times
        a second (long *Ask AI* answers); without it, one plain request (the short cleanup answers). `keep_alive`
        keeps the model in memory between dictations instead of reloading it for 10-30 s. `system` is sent as a
        field of its own, so a rule for the form of the answer never touches the user's prompt; `options` are
        Ollama's sampling options (without them the model's defaults apply).

        Raises:
            OllamaAnswerError: an `error` field or a stream cut off before `done`.
            One of `OLLAMA_ERRORS` for anything else that went wrong.
        """
        body = {
            "model": model,
            "prompt": prompt,
            "stream": on_progress is not None,
            "keep_alive": keep_alive,
            # Thinking models (Qwen 3.5, Granite 4.2) would think for seconds before answering; models without a
            # thinking mode ignore the field.
            "think": False,
        }
        if system:
            body["system"] = system
        if options:
            body["options"] = options
        with self._post("/api/generate", body, timeout) as response:
            if on_progress is None:
                return _plain_answer(json.loads(response.read()))
            return _read_stream(response, on_progress)

    def warm_up(self, model: str, keep_alive: str, timeout: float = 60.0) -> None:
        """Loads `model` into memory with an empty prompt, so the first dictation does not wait for a cold start."""
        body = {"model": model, "prompt": "", "stream": False, "keep_alive": keep_alive}
        with self._post("/api/generate", body, timeout) as response:
            response.read()

    # ------------------------------------------------------------------ models

    def list_models(self, timeout: float = TIMEOUT) -> tuple[tuple[OllamaModel, ...], str]:
        """(models, error text). An empty error text means the service answered."""
        try:
            payload = self._get_json("/api/tags", timeout)
        except Exception as exc:  # noqa: BLE001 - translated into a sentence
            return (), _describe_error(exc)
        if not isinstance(payload, dict):
            return (), t("ollama.bad_response")
        return _parse_models(payload), ""

    def running_models(self, timeout: float = TIMEOUT) -> frozenset[str]:
        """Names of the models in memory.

        `/api/ps` exists only in newer Ollama versions. If it is missing or fails, the set stays empty: the model
        choice must work anyway.
        """
        try:
            payload = self._get_json("/api/ps", timeout)
        except Exception:  # noqa: BLE001 - no reason to block the model choice
            logger.debug("Ollama /api/ps not available", exc_info=True)
            return frozenset()
        names = set()
        for entry in _entries(payload):
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("model")
                if name:
                    names.add(str(name))
        return frozenset(names)

    def probe(self, timeout: float = TIMEOUT) -> OllamaState:
        """One pass: reachable, installed models, models in memory.

        The status line and both model lists in the settings live off the same answer.
        """
        try:
            models, error = self.list_models(timeout)
            if error:
                return OllamaState(False, (), (), error)
            return OllamaState(True, models, self.running_models(timeout), "")
        except Exception:  # noqa: BLE001 - callers run this on worker threads and must always get a state
            logger.exception("Ollama probe failed")
            return OllamaState(False, (), (), t("ollama.bad_response"))

    def pull(self, name: str, progress, cancel, *, timeout: float = PULL_TIMEOUT) -> None:
        """Downloads a model through `/api/pull` and reports the progress.

        Args:
            progress: (done, total) in bytes over all layers announced so far.
            cancel: asked before every status line; True cancels.

        Raises:
            PullCancelled: `cancel()` was True.
            PullError: Ollama reports an error or cannot be reached.
        """
        try:
            response = self._post("/api/pull", {"model": name, "stream": True}, timeout)
        except Exception as exc:  # noqa: BLE001 - translated into a sentence
            raise PullError(_describe_error(exc)) from exc
        with response:
            _read_pull_stream(response, progress, cancel)


def _plain_answer(result) -> str:
    """The text of a non-streamed answer; an `error` field or a missing text is an error."""
    if isinstance(result, dict) and result.get("error"):
        raise OllamaAnswerError("Ollama reported an error")
    answer = result.get("response") if isinstance(result, dict) else None
    if not isinstance(answer, str):
        raise ValueError("Ollama answered without a text response")
    return answer.strip()


def _read_stream(lines, on_progress: Callable[[int], None]) -> str:
    """A streamed answer line by line, reporting its length at most every `PROGRESS_INTERVAL` seconds."""
    chunks: list[str] = []
    last_report = 0.0
    for raw_line in lines:
        obj = _stream_entry(raw_line)
        if obj is None:
            continue
        if obj.get("error"):
            # Only the kind is logged: the message can contain parts of the prompt.
            raise OllamaAnswerError("Ollama reported an error in the stream")
        piece = obj.get("response")
        if isinstance(piece, str):
            chunks.append(piece)
        if obj.get("done"):
            return "".join(chunks).strip()
        now = time.monotonic()
        if now - last_report > PROGRESS_INTERVAL:
            last_report = now
            on_progress(sum(len(chunk) for chunk in chunks))
    raise OllamaAnswerError("Ollama's answer stream ended before it was done")


def _stream_entry(raw_line) -> dict | None:
    line = raw_line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("Ollama stream: non-JSON line ignored")
        return None
    return obj if isinstance(obj, dict) else None


def _read_pull_stream(lines, progress, cancel) -> None:
    """Liest die Statuszeilen bis `success`. Fortschritt je Schicht, addiert über alle."""
    totals: dict[str, int] = {}
    completed: dict[str, int] = {}
    for raw in lines:
        if cancel():
            raise PullCancelled(t("setup.task.cancelled"))
        entry = _pull_entry(raw)
        if entry is None:
            continue
        if entry.get("error"):
            raise PullError(t("ollama.pull_failed", error=str(entry["error"])))
        digest = str(entry.get("digest") or "")
        if digest and entry.get("total"):
            totals[digest] = int(entry["total"])
            completed[digest] = int(entry.get("completed") or 0)
            progress(sum(completed.values()), sum(totals.values()))
        if entry.get("status") == "success":
            return
    raise PullError(t("ollama.pull_incomplete"))


def _pull_entry(raw) -> dict | None:
    line = raw.strip() if isinstance(raw, (bytes, str)) else b""
    if not line:
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        logger.debug("Ollama pull: unreadable status line ignored")
        return None
    return entry if isinstance(entry, dict) else None
