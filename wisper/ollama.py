"""Auskunft über den lokalen Ollama-Dienst: installierte und warme Modelle.

Bewusst nur Lesezugriffe. Das Erzeugen von Text bleibt in `main.py` — hier
steht ausschließlich, was Wisper über den Dienst *weiß*, damit die Oberfläche
nichts erfinden muss.

Wisper lädt keine Ollama-Modelle nach: `/api/pull` bliebe minutenlang ohne
verlässliche Größenangabe hängen, und Ollama bringt dafür ein eigenes Werkzeug
mit. Die Auswahl zeigt deshalb nur, was wirklich installiert ist.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from wisper.models import format_bytes

logger = logging.getLogger("wisper")

#: Kurz halten: der Aufruf hängt an der periodischen Prüfung des
#: Einstellungsfensters und darf den Hintergrundthread nicht blockieren.
TIMEOUT = 2.0

#: Hinweiszeile im Popover. Kein auswählbarer Eintrag — nur eine Auskunft.
PULL_HINT = "Weitere Modelle per ollama pull"


class OllamaModel:
    """Ein installiertes Modell, so wie `/api/tags` es beschreibt."""

    __slots__ = ("name", "size_bytes", "parameter_size", "family")

    def __init__(self, name: str, size_bytes: int = 0,
                 parameter_size: str = "", family: str = "") -> None:
        self.name = name
        self.size_bytes = size_bytes
        self.parameter_size = parameter_size      #: z. B. "8.9B", oft leer
        self.family = family

    @property
    def meta(self) -> str:
        """Zweite Zeile im Popover — nur, was die API tatsächlich liefert.

        Aus dem Namen etwas abzuleiten wäre geraten: `qwen2.5:3b` hat 3,1 Mrd.
        Parameter, `ministral-3:8b` deren 8,9. Fehlt eine Angabe, fehlt sie.
        """
        parts = []
        if self.parameter_size:
            parts.append(self.parameter_size.replace(".", ","))
        if self.size_bytes > 0:
            parts.append(format_bytes(self.size_bytes))
        return " · ".join(parts)

    def __repr__(self) -> str:      # pragma: no cover - nur fürs Log
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

    def __repr__(self) -> str:      # pragma: no cover - nur fürs Log
        return (f"OllamaState(online={self.online}, models={len(self.models)}, "
                f"warm={sorted(self.warm)})")


def base_url(generate_url: str) -> str:
    """Dienstadresse aus der konfigurierten `/api/generate`-Adresse.

    Die Konfiguration nennt seit jeher den Generate-Endpunkt; Tags und Ps
    liegen daneben. Reine Textumformung, damit es genau eine Stelle gibt.
    """
    url = (generate_url or "").strip()
    head = url.rsplit("/api/", 1)[0]
    return (head or url).rstrip("/")


def _get_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
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
        found.append(OllamaModel(
            str(name),
            int(size) if isinstance(size, (int, float)) and size > 0 else 0,
            str(details.get("parameter_size") or ""),
            str(details.get("family") or ""),
        ))
    return tuple(found)


def _describe_error(exc: BaseException) -> str:
    """Ein Satz für die Oberfläche, kein Stacktrace."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"Ollama antwortet mit Fehler {exc.code}"
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "Ollama antwortet unverständlich"
    return "Ollama nicht erreichbar"


def list_models(base: str, timeout: float = TIMEOUT) -> tuple[tuple[OllamaModel, ...], str]:
    """(Modelle, Fehlertext). Leerer Fehlertext heißt: Dienst hat geantwortet."""
    try:
        payload = _get_json(f"{base}/api/tags", timeout)
    except Exception as exc:      # noqa: BLE001 - in Klartext übersetzen
        return (), _describe_error(exc)
    if not isinstance(payload, dict):
        return (), "Ollama antwortet unverständlich"
    return _parse_models(payload), ""


def running_models(base: str, timeout: float = TIMEOUT) -> frozenset[str]:
    """Namen der Modelle im Speicher.

    `/api/ps` gibt es erst ab neueren Ollama-Versionen. Fehlt der Endpunkt oder
    scheitert er, bleibt die Menge leer — die Auswahl muss trotzdem gehen.
    """
    try:
        payload = _get_json(f"{base}/api/ps", timeout)
    except Exception:      # noqa: BLE001 - kein Grund, die Auswahl zu blockieren
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

    Genau ein Ort, an dem Wisper den Dienst befragt — die Statusanzeige und die
    beiden Auswahlfelder leben von derselben Antwort.
    """
    models, error = list_models(base, timeout)
    if error:
        return OllamaState(False, (), (), error)
    return OllamaState(True, models, running_models(base, timeout), "")
