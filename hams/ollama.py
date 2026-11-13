"""Auskunft über den lokalen Ollama-Dienst: installierte und warme Modelle.

Bewusst nur Lesezugriffe. Das Erzeugen von Text bleibt in `transcription.py` — hier
steht ausschließlich, was Hams über den Dienst *weiß*, damit die Oberfläche
nichts erfinden muss.

Die Auswahl in den Einstellungen zeigt nur, was wirklich installiert ist, und lädt
nichts nach. Einzige Ausnahme ist `pull`: der Einrichtungsassistent bietet an, das
voreingestellte Cleanup-Modell zu holen. Die Größe kennt `/api/pull` erst, wenn eine
Schicht zu laden beginnt; die Anzeige zählt deshalb mit, was bisher angemeldet ist.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

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


def _get_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    # Adresse aus der Einstellung, Schema/Host geprüft in config_validation.ollama_url_problem
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read())


def _parse_models(payload: dict) -> tuple[OllamaModel, ...]:
    """Liest `/api/tags`. Unbekannte Felder werden übergangen, nicht geraten."""
    found = []
    for entry in payload.get("models") or ():
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


def _describe_error(exc: BaseException) -> str:
    """Ein Satz für die Oberfläche, kein Stacktrace."""
    if isinstance(exc, urllib.error.HTTPError):
        return t("ollama.http_error", code=exc.code)
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return t("ollama.bad_response")
    return t("ollama.unreachable")


def list_models(base: str, timeout: float = TIMEOUT) -> tuple[tuple[OllamaModel, ...], str]:
    """(Modelle, Fehlertext). Leerer Fehlertext heißt: Dienst hat geantwortet."""
    try:
        payload = _get_json(f"{base}/api/tags", timeout)
    except Exception as exc:  # noqa: BLE001 - in Klartext übersetzen
        return (), _describe_error(exc)
    if not isinstance(payload, dict):
        return (), t("ollama.bad_response")
    return _parse_models(payload), ""


def running_models(base: str, timeout: float = TIMEOUT) -> frozenset[str]:
    """Namen der Modelle im Speicher.

    `/api/ps` gibt es erst ab neueren Ollama-Versionen. Fehlt der Endpunkt oder
    scheitert er, bleibt die Menge leer — die Auswahl muss trotzdem gehen.
    """
    try:
        payload = _get_json(f"{base}/api/ps", timeout)
    except Exception:  # noqa: BLE001 - kein Grund, die Auswahl zu blockieren
        logger.debug("Ollama /api/ps nicht verfügbar", exc_info=True)
        return frozenset()
    names = set()
    for entry in (payload or {}).get("models") or ():
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("model")
            if name:
                names.add(str(name))
    return frozenset(names)


def probe(base: str, timeout: float = TIMEOUT) -> OllamaState:
    """Ein Durchgang: erreichbar, installierte Modelle, warme Modelle.

    Genau ein Ort, an dem Hams den Dienst befragt — die Statusanzeige und die
    beiden Auswahlfelder leben von derselben Antwort.
    """
    models, error = list_models(base, timeout)
    if error:
        return OllamaState(False, (), (), error)
    return OllamaState(True, models, running_models(base, timeout), "")


# --------------------------------------------------------------------- pull

#: Zwischen zwei Statuszeilen von `/api/pull` vergeht selten mehr als eine
#: Sekunde; eine Minute Stille heißt, dass die Verbindung hängt.
PULL_TIMEOUT = 60.0


class PullError(RuntimeError):
    """Ollama hat das Modell nicht geladen; die Nachricht ist für die Oberfläche gedacht."""


class PullCancelled(PullError):
    """Der Nutzer hat abgebrochen. Ollama behält, was schon da ist, und setzt beim nächsten Mal fort."""


def pull(base: str, name: str, progress, cancel, *, opener=None, timeout: float = PULL_TIMEOUT) -> None:
    """Lädt ein Modell über `/api/pull` und meldet den Fortschritt.

    Args:
        progress: (geladen, gesamt) in Bytes über alle bisher angemeldeten Schichten.
        cancel: wird vor jeder Statuszeile gefragt; True bricht ab.
        opener: Ersatz für `urllib.request.urlopen` (Tests).

    Raises:
        PullCancelled: `cancel()` war True.
        PullError: Ollama meldet einen Fehler oder ist nicht erreichbar.
    """
    opener = opener or urllib.request.urlopen
    body = json.dumps({"model": name, "stream": True}).encode()
    # Adresse aus der Einstellung, Schema/Host geprüft in config_validation.ollama_url_problem
    request = urllib.request.Request(  # noqa: S310
        f"{base}/api/pull", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        response = opener(request, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - in Klartext übersetzen
        raise PullError(_describe_error(exc)) from exc
    with response:
        _read_pull_stream(response, progress, cancel)


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
