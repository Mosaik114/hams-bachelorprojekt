import ctypes
import gc
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional, Union

# Projekt-Root in sys.path aufnehmen, damit auch der direkte Skript-Start
# (pythonw.exe wisper\main.py, z. B. aus dem Autostart-VBS) die Package-
# Imports unten findet - dort ist sys.path[0] sonst nur der wisper-Ordner.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# faster-whisper laedt sonst torch und transformers mit - 1,4 s pro Start fuer
# zwei Pakete, die dieses Projekt weder auffuehrt noch aufruft. Muss vor dem
# Import unten stehen, sonst ist er laengst gelaufen.
from wisper.models import preload_backend_without_torch

preload_backend_without_torch()

import winsound

import keyboard
import numpy as np
import pyperclip
import pystray
import sounddevice as sd
from faster_whisper import BatchedInferencePipeline, WhisperModel
from PIL import Image, ImageTk

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[import-not-found]

from wisper.config_validation import validate_config
from wisper import branding
from wisper import models as whisper_models
from wisper import ollama as ollama_api
from wisper import ui_kit as uk
from wisper.ui_kit import Theme


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "transcripts"


def _log_dir() -> Path:
    """Wohin das Log geht.

    `WISPER_LOG_DIR` hat Vorrang — dasselbe Muster wie `WISPER_CONFIG` und aus
    demselben Grund: die Testsuite darf die Aufzeichnung der Anwendung nicht
    beschreiben. Ein Testlauf legte hier sonst 218 Zeilen ab, zwischen denen
    die echten Eintraege des Nutzers stehen; wer spaeter einen Fehler sucht,
    liest dann Zeitstempel aus pytest-Temporaerverzeichnissen.
    """
    override = os.environ.get("WISPER_LOG_DIR")
    return Path(override) if override else APP_DIR / "logs"


LOG_DIR = _log_dir()

#: Mitgelieferte Vorlage. Enthält Voreinstellungen, aber keinen Nutzerzustand.
DEFAULT_CONFIG_PATH = APP_DIR / "config.default.toml"
#: Wo frühere Versionen ihre Konfiguration hatten — mitten im Projektordner.
#: Nur noch Quelle für die einmalige Übernahme.
LEGACY_CONFIG_PATH = APP_DIR / "config.toml"


def _user_config_path() -> Path:
    """Wo die persönliche Konfiguration liegt.

    `WISPER_CONFIG` hat Vorrang. Daran hängt die Testsuite: sie darf die echte
    Konfiguration des Nutzers weder lesen noch beschreiben. Sonst der übliche
    Windows-Ort für Anwendungsdaten.
    """
    override = os.environ.get("WISPER_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "Wisper" / "config.toml"
    return Path.home() / "AppData" / "Roaming" / "Wisper" / "config.toml"


CONFIG_PATH = _user_config_path()

MUTEX_NAME = "Global\\Wisper-SingleInstance-7a3f2c1b"
ERROR_ALREADY_EXISTS = 183

DEFAULT_CLEANUP_PROMPT = (
    "Bereinige den folgenden transkribierten deutschen Text. "
    "Entferne Stotter, Wiederholungen und Versprecher. "
    "Korrigiere Rechtschreibung und Grammatik. "
    "Behalte die ursprüngliche Formulierung so nah wie möglich bei — "
    "keine Umschreibungen, nur eine saubere Fassung von dem was gesagt wurde. "
    "Zensiere KEINE Wörter und ersetze NICHTS inhaltlich — schreibe jeden Begriff exakt so, wie er gesagt wurde, egal welchen Inhalt er hat. "
    "Antworte NUR mit dem korrigierten Text, ohne Erklärungen oder Anmerkungen.\n\n"
    "Text: {text}"
)


@dataclass
class Config:
    sample_rate: int = 16_000
    channels: int = 1
    mic_device: Union[str, int, None] = None
    # Roher Config-Wert (z.B. "Shure MV7+"), damit das Gerät pro Aufnahme frisch
    # aufgelöst werden kann — Indizes verschieben sich beim Aus-/Einstecken.
    mic_device_raw: Any = None
    max_recording_seconds: int = 300

    model_size: str = "turbo"
    model_device_priority: tuple[str, ...] = ("cuda", "cpu")
    model_compute_priority: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "cuda": ("float16", "int8_float16", "int8"),
            "cpu": ("int8", "float32"),
        }
    )

    transcription_language: str = "de"
    transcription_vad_filter: bool = True
    transcription_word_timestamps: bool = False
    transcription_beam_size: int = 1
    transcription_condition_on_previous_text: bool = False
    transcription_without_timestamps: bool = True

    hotkey_transcript: str = "ctrl+shift+space"
    hotkey_prompt: str = "ctrl+shift+alt+space"

    #: Bewusst die Zahlenadresse statt `localhost`: siehe `ipv4_loopback`.
    ollama_url: str = "http://127.0.0.1:11434/api/generate"
    ollama_model_cleanup: str = "qwen2.5:7b"
    ollama_model_prompt: str = "qwen2.5:7b"
    ollama_timeout_cleanup: int = 30
    ollama_timeout_prompt: int = 60
    ollama_stream_prompt: bool = True
    ollama_cleanup_enabled: bool = True
    ollama_keep_alive: str = "30m"
    ollama_warmup_on_start: bool = True
    #: Zuletzt bekannte Lage des Hauptfensters in physischen Bildschirmpixeln.
    #: None heisst: noch nie gemerkt, es gilt der Rueckfall.
    window_x: Optional[int] = None
    window_y: Optional[int] = None
    cleanup_prompt_template: str = DEFAULT_CLEANUP_PROMPT

    clipboard_paste_timeout: float = 0.3
    sound_feedback: bool = True
    #: Darstellungsmodus, "light" oder "dark". Ohne Eintrag in der Nutzerdatei
    #: gilt hell — bewusst nicht das Windows-Systemtheme, der Nutzer waehlt
    #: selbst. Ein unbekannter Wert faellt in `Theme.normalise()` auf hell
    #: zurueck, damit eine alte oder vertippte Datei nie ein farbloses Fenster
    #: ergibt.
    appearance: str = Theme.DEFAULT_MODE


def _resolve_mic_device(raw: Any) -> Union[str, int, None]:
    """Resolve microphone device from config value.

    Args:
        raw: Config value - can be None, int (device index), or string (device name).

    Returns:
        None for default device, int for device index, or string for device name.

    A name substring is resolved to a concrete input-device *index* here, because
    the same physical mic appears under several host APIs (MME/DirectSound/WASAPI)
    and passing the bare string to sounddevice raises "Multiple input devices found".
    Among matches we prefer the default host API, then the lowest device index.
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        needle = raw.strip().lower()
        if needle in ("", "default"):
            return None
        try:
            devices = sd.query_devices()
            default_hostapi = sd.default.hostapi
        except Exception:
            return raw  # fall back to letting sounddevice resolve the name
        matches = [
            idx
            for idx, dev in enumerate(devices)
            if dev.get("max_input_channels", 0) > 0
            and needle in str(dev.get("name", "")).lower()
        ]
        if not matches:
            return raw
        matches.sort(
            key=lambda idx: (devices[idx].get("hostapi") != default_hostapi, idx)
        )
        return matches[0]
    return None


# Peak unterhalb dieser Schwelle gilt als "kein Signal" (Mikro stumm/falsches
# Gerät). Sprache liegt typ. bei 0.05+, Grundrauschen bei ~0.0002 — 0.005 trennt
# beides sauber. Wird sowohl für die Normalisierung als auch die Kein-Signal-
# Erkennung genutzt, damit beide dieselbe Grenze verwenden.
SILENCE_PEAK_THRESHOLD = 0.005

#: Wie lange eine fertige Aufnahme auf das Modell wartet, bevor sie aufgibt.
#: Warm ist es nach ~2 s da, kalt nach ~20 s; die Schranke ist grosszuegig
#: bemessen, aber vorhanden, damit kein Thread unbegrenzt haengt.
MODEL_WAIT_SECONDS = 120.0

#: Ab dieser Aufnahmelaenge wird die gebatchte Auswertung benutzt.
#: Gemessen (turbo/float16, RTX 5070, gegen bekannten Referenztext):
#:    6 s: −1,6 % Zeit,  WER ±0,00 pp      24 s: +0,5 % Zeit,  WER ±0,00 pp
#:   12 s: ±0,0 % Zeit,  WER ±0,00 pp      48 s: +25,6 % Zeit, WER −2,38 pp
#:                                         84 s: +34,5 % Zeit, WER −1,32 pp
#: Unterhalb der Schwelle bringt es nichts, oberhalb wird es schneller *und*
#: genauer: der sequentielle Lauf verschluckt am Ende langer Aufnahmen Woerter
#: und haengt Floskeln an, die nie gesprochen wurden.
BATCHED_MIN_SECONDS = 30.0
#: Groesser bringt nichts mehr (8 -> 16 gemessen: +1,4 %), kostet aber VRAM.
BATCH_SIZE = 8


def _normalize_audio(audio: np.ndarray, target_peak: float = 0.35) -> np.ndarray:
    """Hebt leise Aufnahmen auf einen Zielpegel an, damit Whisper/VAD nicht an
    zu niedrigem Eingangspegel scheitern.

    Reine Stille (Peak unter der Rauschgrenze) wird NICHT verstärkt — sonst
    würde nur Grundrauschen hochgezogen. Bereits laute Aufnahmen bleiben
    unverändert; es wird nur angehoben, nie abgesenkt.
    """
    if audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak < SILENCE_PEAK_THRESHOLD or peak >= target_peak:
        return audio
    return (audio * (target_peak / peak)).astype(np.float32)


def _toml_scalar(value: Any) -> str:
    """Formatiert einen Python-Wert als TOML-Skalar."""
    if isinstance(value, bool):  # vor int prüfen — bool ist eine int-Unterklasse
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, (int, float)):
        return repr(value)
    return str(value)


def _trailing_comment(rest: str) -> str:
    """Gibt einen nachgestellten Kommentar samt führendem Weißraum zurück.

    Ein `#` innerhalb eines Strings zählt nicht als Kommentarbeginn.
    """
    in_string = False
    escaped = False
    for idx, char in enumerate(rest):
        if escaped:
            escaped = False
            continue
        if in_string and char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if char == "#" and not in_string:
            begin = idx
            while begin > 0 and rest[begin - 1] in " \t":
                begin -= 1
            return rest[begin:].rstrip("\r\n")
    return ""


#: Rueckfallabstand zur Ecke der Arbeitsflaeche, in logischen Pixeln.
POSITION_FALLBACK = 48
#: So viel vom Fenster muss auf einem Bildschirm liegen, damit es greifbar ist.
MIN_VISIBLE_W = 120
MIN_VISIBLE_H = 60
#: Nach dem Ziehen wird einmal geschrieben, nicht bei jedem Ereignis.
POSITION_SAVE_DELAY_MS = 700


#: Ein Codeblock, der die **ganze** Antwort umschliesst. Nur dieser wird
#: entfernt — Backticks mitten im Text gehoeren dem Nutzer.
_FENCE_RE = re.compile(r"\A```[^\n]*\n(?P<body>.*)\n```\Z", re.DOTALL)

#: Markdown-Hervorhebungen. Jede Regel verlangt, dass der Inhalt weder mit
#: Leerraum beginnt noch endet und dass links und rechts kein Wortzeichen
#: steht. Damit bleiben `2 * 3 = 6`, `*.txt`, `foo*bar` und `datei_name.txt`
#: unangetastet — dort fehlt jeweils genau eine dieser Bedingungen.
_EMPHASIS_RES = (
    re.compile(r"(?<![\w*])\*\*\*(?!\s)(?P<text>[^*\n]+?)(?<!\s)\*\*\*(?![\w*])"),
    re.compile(r"(?<![\w_])___(?!\s)(?P<text>[^_\n]+?)(?<!\s)___(?![\w_])"),
    re.compile(r"(?<![\w*])\*\*(?!\s)(?P<text>[^*\n]+?)(?<!\s)\*\*(?![\w*])"),
    re.compile(r"(?<![\w_])__(?!\s)(?P<text>[^_\n]+?)(?<!\s)__(?![\w_])"),
    re.compile(r"(?<![\w*])\*(?!\s)(?P<text>[^*\n]+?)(?<!\s)\*(?![\w*])"),
    re.compile(r"(?<![\w_])_(?!\s)(?P<text>[^_\n]+?)(?<!\s)_(?![\w_])"),
)


#: Wird dem Prompt-Modus als Systemanweisung mitgegeben. Nicht in den Prompt
#: des Nutzers hineingeschrieben: der bleibt Wort fuer Wort, wie diktiert.
#: Ollama nimmt dafuer ein eigenes Feld entgegen.
PROMPT_PLAIN_SYSTEM = (
    "Antworte in normalem Klartext. Verwende kein Markdown: keine Sternchen "
    "oder Unterstriche zur Hervorhebung, keine Fett- oder Kursivschrift, keine "
    "Überschriften, keine Backticks und keine Codeblöcke. Deine Antwort wird "
    "unverändert in ein Textfeld eingefügt."
)


def plain_text(value: str) -> str:
    """Nimmt Markdown-Hervorhebungen aus einer Cleanup-Antwort.

    Bewusst klein gehalten: keine Markdown-Bibliothek, keine allgemeine
    Umwandlung nach Klartext. Entfernt werden genau zwei Dinge — ein
    Codeblock, der die ganze Antwort umschliesst, und die vier ueblichen
    Hervorhebungsklammern `**`, `__`, `*`, `_`.

    Ein pauschales `text.replace("*", "")` waere hier falsch: es zerlegte
    `2 * 3 = 6`, `*.txt` und `foo*bar`. Deshalb muessen die Klammern wirklich
    als Klammern erkennbar sein.

    Gilt nur fuer den Cleanup. Der Prompt-Modus liefert weiterhin, was das
    Modell schreibt — dort ist Formatierung erwuenscht.
    """
    if not value:
        return value
    text = value.strip()
    fence = _FENCE_RE.match(text)
    if fence is not None:
        text = fence.group("body").strip()
    for pattern in _EMPHASIS_RES:
        previous = None
        while previous != text:      # geschachtelte Klammern aufloesen
            previous = text
            text = pattern.sub(r"\g<text>", text)
    return text


def ipv4_loopback(url: str) -> str:
    """Ersetzt den Hostnamen `localhost` durch `127.0.0.1` — und sonst nichts.

    Gemessen auf Windows 11: `localhost` loest zu `::1` *und* `127.0.0.1` auf,
    und `::1` steht vorn. Ollama lauscht in der Voreinstellung nur auf
    `127.0.0.1`. Der erste Verbindungsversuch geht damit ins Leere, und eine
    abgelehnte Loopback-Verbindung braucht auf diesem System 2,03 s, bis sie
    als abgelehnt gilt. Erst danach wird IPv4 probiert. Diese 2,03 s haengen
    an *jedem* Ollama-Aufruf: Cleanup, Prompt-Antwort, Warmlauf und die
    periodische Auskunft des Einstellungsfensters.

    Die Umschreibung ist bewusst eng. Angefasst wird ausschliesslich der
    Hostname `localhost`; `127.0.0.1` meint dieselbe Maschine, nur ohne den
    Umweg ueber eine Adresse, an der niemand zuhoert. Jeder andere Host bleibt
    unberuehrt — auch `[::1]`, denn wer das ausdruecklich hinschreibt, meint
    IPv6 und soll es bekommen. Das ist der Ausweg fuer eine Ollama-Instanz,
    die tatsaechlich nur auf IPv6 lauscht.
    """
    if not url:
        return url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:              # kaputte URL: unveraendert weiterreichen
        return url
    if (parts.hostname or "").lower() != "localhost":
        return url
    try:
        port = parts.port
    except ValueError:              # unbrauchbarer Port: nicht anfassen
        return url
    host = "127.0.0.1" if port is None else f"127.0.0.1:{port}"
    userinfo, at, _ = parts.netloc.rpartition("@")
    return urllib.parse.urlunsplit(
        parts._replace(netloc=f"{userinfo}{at}{host}")
    )


def _clean_coordinate(value: Any) -> Optional[int]:
    """Ganze Zahl oder None. Kaputte Werte werden verworfen, nie geraten.

    Negative Werte sind ausdruecklich gueltig: Windows setzt Monitore links
    und oberhalb des Hauptbildschirms, deren Koordinaten sind dann negativ.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = int(value)
    except (ValueError, OverflowError):      # NaN, Unendlich
        return None
    if abs(number) > 100_000:                # jenseits jedes realen Verbunds
        return None
    return number


def _visible_enough(x: int, y: int, width: int, height: int, areas) -> bool:
    """Liegt ein brauchbarer Teil des Fensters auf einem Bildschirm?

    Nicht nur "irgendwo im virtuellen Desktop": ein Fenster, von dem nur zehn
    Pixel am Rand herausschauen, ist nicht bedienbar. Zusaetzlich muss die
    Oberkante unterhalb des Arbeitsflaechenrandes liegen — dort sitzen
    Kopfzeile und Ziehflaeche, und was darueber liegt, bekommt niemand zu
    fassen.
    """
    for left, top, right, bottom in areas:
        if y < top:
            continue
        overlap_w = min(x + width, right) - max(x, left)
        overlap_h = min(y + height, bottom) - max(y, top)
        if overlap_w >= uk.px(MIN_VISIBLE_W) and overlap_h >= uk.px(MIN_VISIBLE_H):
            return True
    return False


def window_position(saved_x, saved_y, width: int, height: int, areas) -> tuple[int, int]:
    """Startlage des Hauptfensters: gemerkte Position oder Rueckfall.

    Gerechnet wird in **physischen** Bildschirmpixeln — derselben Einheit, die
    `geometry()` erwartet, die `winfo_x()` liefert und in der die
    Arbeitsflaechen gemeldet werden. Logisch zu speichern hiesse, bei jedem
    Start durch den Skalierungsfaktor zu teilen und wieder zu multiplizieren:
    das wanderte ueber mehrere Laeufe um die Rundungsfehler und landete nach
    einem DPI-Wechsel an einer anderen Stelle des Schirms.
    """
    left, top = (areas[0][0], areas[0][1]) if areas else (0, 0)
    fallback = (left + uk.px(POSITION_FALLBACK), top + uk.px(POSITION_FALLBACK))
    x, y = _clean_coordinate(saved_x), _clean_coordinate(saved_y)
    if x is None or y is None:
        return fallback
    if not _visible_enough(x, y, width, height, areas):
        logger.info("Gemerkte Fensterposition %s,%s liegt nicht mehr sichtbar", x, y)
        return fallback
    return x, y


def _persist_config_value(section: str, key: str, value: Any) -> bool:
    """Schreibt `key = value` in den Abschnitt `[section]` von config.toml.

    Abschnittsbewusst, weil derselbe Schlüsselname in mehreren Abschnitten
    vorkommen darf (`[audio] device` neben einem künftigen `[model] device`).
    Fehlt der Schlüssel, wird er am Ende seines Abschnitts ergänzt; fehlt der
    Abschnitt, wird er am Dateiende angelegt. Kommentare, Leerzeilen,
    Reihenfolge und Zeilenenden bleiben erhalten.

    Geschrieben wird atomar über eine temporäre Datei — ein Abbruch mitten im
    Schreiben darf die Konfiguration nie halbiert zurücklassen.
    """
    if not CONFIG_PATH.exists():
        ensure_user_config()
    try:
        with CONFIG_PATH.open("r", encoding="utf-8", newline="") as fh:
            text = fh.read()
    except OSError:
        logger.warning("Config schreiben fehlgeschlagen: %s nicht lesbar", CONFIG_PATH)
        return False

    lines = text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in text else "\n"

    header_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    key_re = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)(.*)$")

    current: Optional[str] = None
    section_start: Optional[int] = None
    section_end: Optional[int] = None
    target: Optional[int] = None

    for idx, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        header = header_re.match(bare)
        if header is not None:
            if current == section and section_end is None:
                section_end = idx
            current = header.group(1).strip()
            if current == section and section_start is None:
                section_start = idx
            continue
        if current == section and target is None and key_re.match(bare) is not None:
            target = idx
    if section_start is not None and section_end is None:
        section_end = len(lines)

    rhs = _toml_scalar(value)

    if target is not None:
        original = lines[target]
        bare = original.rstrip("\r\n")
        line_eol = original[len(bare):] or eol
        match = key_re.match(bare)
        assert match is not None  # oben bereits geprüft
        lines[target] = match.group(1) + rhs + _trailing_comment(match.group(2)) + line_eol
    elif section_start is not None:
        insert_at = section_end if section_end is not None else len(lines)
        # Hinter der letzten inhaltlichen Zeile des Abschnitts einfügen,
        # damit die Leerzeile vor dem nächsten Abschnitt erhalten bleibt.
        while insert_at > section_start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        if insert_at > 0 and not lines[insert_at - 1].endswith(("\n", "\r")):
            lines[insert_at - 1] += eol
        lines.insert(insert_at, f"{key} = {rhs}{eol}")
        logger.info("Config-Schlüssel '%s' in [%s] ergänzt", key, section)
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += eol
        if lines and lines[-1].strip():
            lines.append(eol)
        lines.append(f"[{section}]{eol}")
        lines.append(f"{key} = {rhs}{eol}")
        logger.info("Config-Abschnitt [%s] mit '%s' angelegt", section, key)

    tmp_path = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as fh:
            fh.write("".join(lines))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, CONFIG_PATH)
        return True
    except OSError:
        logger.exception("Config schreiben fehlgeschlagen")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _list_input_devices() -> list[tuple[str, str]]:
    """Eingabegeräte als (Anzeigename, Such-String) — pro physischem Mikro nur
    einmal (bevorzugt die Standard-Host-API)."""
    try:
        devices = sd.query_devices()
        default_hostapi = sd.default.hostapi
    except Exception:
        return []
    seen: dict[str, tuple[str, str, int]] = {}
    for dev in devices:
        if dev.get("max_input_channels", 0) <= 0:
            continue
        name = str(dev.get("name", "")).strip()
        if not name:
            continue
        # Anzeigenamen normalisieren (führendes "3- " etc. entfernen).
        pretty = re.sub(r"^\d+-\s*", "", name)
        prefer = 0 if dev.get("hostapi") == default_hostapi else 1
        prev = seen.get(pretty)
        if prev is None or prefer < prev[2]:
            seen[pretty] = (pretty, name, prefer)
    return [(p, full) for (p, full, _pref) in seen.values()]


def ensure_user_config() -> str:
    """Sorgt dafür, dass eine persönliche Konfiguration existiert.

    Genau einmal: existiert die Nutzerdatei bereits, wird nichts kopiert. Eine
    später geänderte Vorlage darf persönliche Werte nie wieder überschreiben.

    Gibt zurück, was geschehen ist — "vorhanden", "migriert", "angelegt" oder
    "fehlgeschlagen".
    """
    if CONFIG_PATH.exists():
        return "vorhanden"
    source = LEGACY_CONFIG_PATH if LEGACY_CONFIG_PATH.exists() else DEFAULT_CONFIG_PATH
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8") if source.exists() else ""
        with CONFIG_PATH.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    except OSError:
        # Bewusst kein Rückfall auf die Projektdatei: persönliche Werte gehören
        # nicht in den Programmordner, auch nicht im Notfall.
        logger.error("Nutzerkonfiguration %s liess sich nicht anlegen — "
                     "Einstellungen sind in dieser Sitzung nicht speicherbar",
                     CONFIG_PATH, exc_info=True)
        return "fehlgeschlagen"
    if source is LEGACY_CONFIG_PATH:
        logger.info("Konfiguration aus %s nach %s übernommen",
                    LEGACY_CONFIG_PATH, CONFIG_PATH)
        return "migriert"
    logger.info("Neue Konfiguration unter %s angelegt", CONFIG_PATH)
    return "angelegt"


def _read_toml(path: Path) -> dict:
    """Eine TOML-Datei als Wörterbuch. Fehlt oder bricht sie, bleibt es leer."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"[{branding.PRODUCT_NAME}] {path.name} konnte nicht geladen "
              f"werden ({exc}) — nutze Defaults.", file=sys.stderr)
        return {}


def _load_config() -> Config:
    """Voreinstellungen aus dem Code, dann aus der Vorlage, dann vom Nutzer.

    Drei Ebenen in dieser Reihenfolge. Deshalb muss eine ältere Nutzerdatei
    keinen später hinzugekommenen Schlüssel kennen: was dort fehlt, kommt aus
    der Vorlage, und was auch dort fehlt, steht im Datenmodell.
    """
    cfg = Config()
    for path in (DEFAULT_CONFIG_PATH, CONFIG_PATH):
        data = _read_toml(path)
        if data:
            _apply_toml(cfg, data)

    errors = validate_config(cfg)
    for error in errors:
        logger.warning("Config validation: %s", error)

    return cfg


def _apply_toml(cfg: Config, data: dict) -> None:
    """Legt eine gelesene Datei über die bisherigen Werte."""
    audio = data.get("audio", {})
    cfg.sample_rate = int(audio.get("sample_rate", cfg.sample_rate))
    cfg.channels = int(audio.get("channels", cfg.channels))
    cfg.mic_device_raw = audio.get("device")
    cfg.mic_device = _resolve_mic_device(cfg.mic_device_raw)
    cfg.max_recording_seconds = int(audio.get("max_recording_seconds", cfg.max_recording_seconds))

    model = data.get("model", {})
    cfg.model_size = str(model.get("size", cfg.model_size))
    device_priority = model.get("device_priority")
    if isinstance(device_priority, list) and device_priority:
        cfg.model_device_priority = tuple(str(d) for d in device_priority)
    compute_priority = model.get("compute_priority", {})
    if isinstance(compute_priority, dict) and compute_priority:
        cfg.model_compute_priority = {
            str(k): tuple(str(x) for x in v)
            for k, v in compute_priority.items()
            if isinstance(v, list) and v
        }

    transcription = data.get("transcription", {})
    cfg.transcription_language = str(transcription.get("language", cfg.transcription_language))
    cfg.transcription_vad_filter = bool(transcription.get("vad_filter", cfg.transcription_vad_filter))
    cfg.transcription_word_timestamps = bool(transcription.get("word_timestamps", cfg.transcription_word_timestamps))
    cfg.transcription_beam_size = int(transcription.get("beam_size", cfg.transcription_beam_size))
    cfg.transcription_condition_on_previous_text = bool(transcription.get("condition_on_previous_text", cfg.transcription_condition_on_previous_text))
    cfg.transcription_without_timestamps = bool(transcription.get("without_timestamps", cfg.transcription_without_timestamps))

    hotkeys = data.get("hotkeys", {})
    cfg.hotkey_transcript = str(hotkeys.get("transcript", cfg.hotkey_transcript))
    cfg.hotkey_prompt = str(hotkeys.get("prompt", cfg.hotkey_prompt))

    ollama = data.get("ollama", {})
    cfg.ollama_url = ipv4_loopback(str(ollama.get("url", cfg.ollama_url)))
    cfg.ollama_model_cleanup = str(ollama.get("model_transcript_cleanup", cfg.ollama_model_cleanup))
    cfg.ollama_model_prompt = str(ollama.get("model_prompt_generate", cfg.ollama_model_prompt))
    cfg.ollama_timeout_cleanup = int(ollama.get("timeout_cleanup", cfg.ollama_timeout_cleanup))
    cfg.ollama_timeout_prompt = int(ollama.get("timeout_prompt", cfg.ollama_timeout_prompt))
    cfg.ollama_stream_prompt = bool(ollama.get("stream_prompt", ollama.get("stream", cfg.ollama_stream_prompt)))
    cfg.ollama_cleanup_enabled = bool(ollama.get("cleanup_enabled", cfg.ollama_cleanup_enabled))
    cfg.ollama_keep_alive = str(ollama.get("keep_alive", cfg.ollama_keep_alive))
    cfg.ollama_warmup_on_start = bool(ollama.get("warmup_on_start", cfg.ollama_warmup_on_start))
    prompt_file_rel = ollama.get("cleanup_prompt_file")
    if isinstance(prompt_file_rel, str) and prompt_file_rel.strip():
        prompt_path = (APP_DIR / prompt_file_rel).resolve()
        try:
            cfg.cleanup_prompt_template = prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[{branding.PRODUCT_NAME}] Cleanup-Prompt konnte nicht geladen "
                  f"werden ({exc}) — nutze Default.", file=sys.stderr)

    clipboard = data.get("clipboard", {})
    cfg.clipboard_paste_timeout = float(clipboard.get("paste_timeout", cfg.clipboard_paste_timeout))

    ui = data.get("ui", {})
    cfg.sound_feedback = bool(ui.get("sound_feedback", cfg.sound_feedback))
    cfg.window_x = _clean_coordinate(ui.get("window_x"))
    if "window_x" in ui:
        cfg.window_x = _clean_coordinate(ui.get("window_x"))
    if "window_y" in ui:
        cfg.window_y = _clean_coordinate(ui.get("window_y"))
    # `normalise` statt `str(...)`: der Schluessel fehlt in jeder bestehenden
    # Nutzerdatei, und ein unbekannter Wert soll hell ergeben, keinen Fehler.
    cfg.appearance = Theme.normalise(ui.get("appearance", cfg.appearance))


def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("wisper")
    log.setLevel(logging.DEBUG)
    if log.handlers:
        return log
    file_handler = RotatingFileHandler(
        LOG_DIR / "wisper.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    )
    log.addHandler(file_handler)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(
        logging.Formatter("%(levelname)s: %(message)s")
    )
    log.addHandler(console_handler)
    return log


logger = _setup_logging()


# Das Log steht *vor* der Konfiguration, nicht danach: `_load_config()`
# meldet ungueltige Werte ueber `logger`. Stuende der Aufbau spaeter,
# schluege jede Nutzerdatei mit einem Wert ausserhalb der erlaubten
# Bereiche beim Import fehl — und zwar lautlos, weil die Anwendung aus
# dem Autostart ohne Konsole laeuft.
CFG = _load_config()

# Der Darstellungsmodus steht, bevor das erste Widget entsteht. Danach liest
# jede Zeichenroutine ueber `Theme.*` automatisch die richtige Palette.
Theme.apply(CFG.appearance)

#: Das Schloss, an dem die zweite Instanz scheitert. Modulweit, weil der
#: Neustart es freigeben muss, *bevor* der Nachfolger startet — sonst haelt
#: der scheidende Prozess es noch und der neue meldet „laeuft bereits".
_MUTEX_HANDLE: Optional[int] = None


def _acquire_single_instance_mutex() -> Optional[int]:
    """Return mutex handle on success, None if another instance is running.
    The returned handle must be kept alive for the lifetime of the process."""
    global _MUTEX_HANDLE
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    _MUTEX_HANDLE = handle
    return handle


def _release_single_instance_mutex() -> bool:
    """Gibt das Schloss frei. True, wenn danach keines mehr gehalten wird.

    Ein benanntes Mutex verschwindet, sobald der letzte Griff darauf zu ist —
    danach kann der Nachfolger es sofort selbst anlegen.
    """
    global _MUTEX_HANDLE
    if _MUTEX_HANDLE is None:
        return True
    try:
        ctypes.windll.kernel32.CloseHandle(_MUTEX_HANDLE)
    except Exception:
        logger.exception("Einzelinstanz-Schloss liess sich nicht freigeben")
        return False
    _MUTEX_HANDLE = None
    return True


def restart_command() -> list:
    """Der Befehl, mit dem sich Hams selbst noch einmal startet.

    `sys.executable` statt eines festen Pfades: gestartet ueber das VBS ist das
    `pythonw.exe` (ohne Konsolenfenster), aus der Konsole `python.exe`. Der
    Nachfolger soll genau so laufen wie der Vorgaenger. Das Skript wird
    ausdruecklich aufgeloest angegeben und nicht ueber `sys.argv` uebernommen —
    `sys.argv[0]` ist je nach Startweg relativ und zeigt nach einem
    Verzeichniswechsel ins Leere.
    """
    return [sys.executable, str(APP_DIR / "main.py")]


#: Farben des Tray-Symbols — bewusst **fest**, nicht aus dem aktiven
#: Darstellungsmodus. Das Hams-Fenster und die Windows-Taskleiste sind zwei
#: verschiedene Oberflaechen: wer Hams hell stellt, hat deshalb noch lange
#: keine helle Taskleiste. Ein Symbol, das seine Farbe nach dem App-Theme
#: richtet, waere auf der Leiste mal zu blass und mal zu dunkel. Genommen sind
#: die kraeftigen Toene, die sich auf beiden Leistenfarben behaupten.
TRAY_PALETTE = Theme.palette(Theme.DARK)


def _create_tray_icon_image(color: Optional[str] = None) -> Image.Image:
    """Das Tray-Symbol im aktuellen Zustand.

    Die Form ist immer die Hams-Bildmarke, die Farbe kommt aus dem Zustand.
    Hier stand frueher ein blauer Kreis mit weissem „W"; beides gehoerte zum
    alten Produktnamen und ist ersatzlos verschwunden.
    """
    return branding.tray_image(color or TRAY_PALETTE["ACCENT_TINT"])


class FloatingTranscriberApp:
    """Main application class for Wisper.

    Manages the Tkinter UI, system tray, audio recording, transcription,
    and LLM cleanup/prompt generation.
    """

    WINDOW_WIDTH = 340         # logisch
    WINDOW_MIN_HEIGHT = 190    # logisch; die echte Hoehe kommt aus dem Layout

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(branding.PRODUCT_NAME)
        # Reihenfolge zählt: erst Skalierung (setzt tk scaling), dann Fonts.
        uk.init_scale(self.root)
        uk.init_fonts(self.root)
        logger.info("Systemanimationen: %s",
                    "an" if uk.init_animations() else "aus")
        logger.info(
            "UI scale: %d dpi, Faktor %.2f, tk scaling %.3f",
            uk.Scale.dpi, uk.Scale.factor,
            float(self.root.tk.call("tk", "scaling")),
        )
        self.root.overrideredirect(True)  # randlos für das eigene Apple-Design
        self._window_size = (uk.px(self.WINDOW_WIDTH), uk.px(self.WINDOW_MIN_HEIGHT))
        self._window_pos = window_position(
            CFG.window_x, CFG.window_y, *self._window_size, uk.work_areas())
        self._position_job: Optional[str] = None
        self.root.geometry(f"+{self._window_pos[0]}+{self._window_pos[1]}")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.configure(bg=Theme.WINDOW_BG)
        self._apply_window_icon()
        self.settings_win: Optional["SettingsWindow"] = None
        # Wer sonst noch das Mikrofon offen hat und es waehrend einer Aufnahme
        # freigeben muss. Klare Lebenszyklus-Schnittstelle statt Zugriff auf
        # private Methoden des Einstellungsfensters.
        self._mic_consumers: list = []

        self.is_recording = False
        self.frames: list[np.ndarray] = []
        self.stream: Optional[sd.InputStream] = None

        self.model: Optional[WhisperModel] = None
        #: Welches Modell tatsaechlich geladen ist. CFG.model_size ist dagegen
        #: die *Auswahl* — beides faellt nach einem Wechsel auseinander.
        self.active_model_size: Optional[str] = None
        self.model_ready = threading.Event()
        self.model_error: Optional[str] = None
        #: Kurzer Schutz um self.model und die Begleitfelder.
        self._model_lock = threading.Lock()
        #: Serialisiert Transkription gegen Modellwechsel. Ein Wechsel wartet
        #: damit auf einen laufenden Lauf, statt ein zweites Modell parallel in
        #: den Speicher zu legen.
        self._transcribe_lock = threading.Lock()
        #: Steigt bei jedem Wechselwunsch. Ein spaet zurueckkehrender aelterer
        #: Ladevorgang erkennt daran, dass er nicht mehr gemeint ist.
        self._model_generation = 0
        self._switching_to: Optional[str] = None
        #: Der Download gehoert der Anwendung, nicht dem Einstellungsfenster:
        #: er laeuft weiter, wenn das Fenster zwischendurch geschlossen wird.
        #: Letzte Auskunft des Ollama-Dienstes. Sie gehoert der Anwendung, damit
        #: ein neu geoeffnetes Einstellungsfenster nicht faelschlich "nicht
        #: erreichbar" behauptet, bis die erste eigene Abfrage zurueck ist.
        self.ollama_state = ollama_api.OllamaState(False)
        self.ollama_checked = False
        self.downloader = whisper_models.ModelDownloader()
        # Muss vor dem ersten Modellzugriff stehen; der Schalter gilt fuer den
        # ganzen Prozess, also auch fuer das Nachladen beim Start.
        whisper_models.disable_xet_transfer()
        self.download_done = 0
        self.download_total = 0
        #: Der Hub meldet die Gesamtgroesse erst nach und nach; ohne diese
        #: Sperre liefe die Prozentanzeige zwischendurch rueckwaerts.
        self.download_percent = 0
        self._active_compute_type: Optional[str] = None
        self._active_device: Optional[str] = None

        self.drag_start_x = 0
        self.drag_start_y = 0
        self._state = "model_loading"
        self._done_job: Optional[str] = None
        self._elapsed_job: Optional[str] = None

        self._recording_start: float = 0.0
        self._max_duration_timer: Optional[threading.Timer] = None

        self._rms_level: float = 0.0
        self._level_tick_job: Optional[str] = None

        self._history: list[tuple[str, str, str]] = []  # (display-time, preview, full-text)
        self._history_lock = threading.Lock()

        self._window_visible = False
        self._prompt_mode = False
        self._shutting_down = False

        self._build_ui()
        self._fit_window()
        self._apply_state("model_loading")
        self._load_model_async()
        self._warmup_ollama_async()
        self._register_hotkeys()

        logger.info("Fensterecken: %s", uk.round_window_corners(self.root))
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)
        self._setup_tray()
        self.root.withdraw()
        logger.info("Wisper started")

    # ------------------------------------------------------------------ Mikrofon-Teilhabe

    def register_mic_consumer(self, consumer) -> None:
        """Meldet einen Mitbenutzer des Mikrofons an.

        Aufnahme und Pegelmonitor duerfen dasselbe Geraet nie gleichzeitig
        oeffnen; der Konsument wird deshalb vor der Aufnahme angehalten und
        danach wieder freigegeben.
        """
        if consumer not in self._mic_consumers:
            self._mic_consumers.append(consumer)

    def unregister_mic_consumer(self, consumer) -> None:
        if consumer in self._mic_consumers:
            self._mic_consumers.remove(consumer)

    def _suspend_mic_consumers(self) -> None:
        for consumer in list(self._mic_consumers):
            try:
                consumer.suspend_microphone()
            except Exception:
                logger.exception("Mikrofon-Konsument liess sich nicht anhalten")

    def _resume_mic_consumers(self) -> None:
        for consumer in list(self._mic_consumers):
            try:
                consumer.resume_microphone()
            except Exception:
                logger.exception("Mikrofon-Konsument liess sich nicht fortsetzen")

    # ------------------------------------------------------------------ dpi

    def _on_root_configure(self, event: tk.Event) -> None:
        """Erkennt einen Monitorwechsel mit anderer Skalierung.

        Tk kennt `WM_DPICHANGED` nicht, deshalb wird das DPI beim ohnehin
        anfallenden Configure-Ereignis verglichen — kein eigener Timer.
        """
        if event.widget is not self.root:
            return
        dpi = uk.window_dpi(self.root)
        if dpi == uk.Scale.dpi:
            return
        logger.info("DPI-Wechsel: %d -> %d", uk.Scale.dpi, dpi)
        uk.Scale.apply(self.root, dpi)
        self._fit_window()
        uk.round_window_corners(self.root)

    # ------------------------------------------------------------------ ui helpers

    def _ui_call(self, fn) -> None:
        """Schedule fn on the Tk main thread. Zentral statt verstreute root.after-Calls."""
        self.root.after(0, fn)

    # ------------------------------------------------------------------ hotkeys

    def _register_hotkeys(self) -> None:
        for combo, handler, label in (
            (CFG.hotkey_transcript, self.toggle_recording, "Transkript"),
            (CFG.hotkey_prompt, self.toggle_recording_generate, "Prompt"),
        ):
            try:
                keyboard.add_hotkey(combo, lambda h=handler: self._ui_call(h))
                logger.info("Hotkey registered: %s (%s)", combo, label)
            except (ValueError, KeyError) as exc:
                logger.error("Hotkey '%s' invalid (%s): %s", combo, label, exc)
                self._ui_call(lambda c=combo, bez=label: self._apply_state(
                    "error", "Kurzbefehl nicht verfügbar",
                    f"„{c}“ ({bez}) lässt sich nicht registrieren. "
                    f"Prüfe die Einstellung in {CONFIG_PATH}.",
                ))
            except Exception:
                logger.exception("Hotkey '%s' registration failed", combo)
                self._ui_call(lambda c=combo: self._apply_state(
                    "error", "Kurzbefehl nicht verfügbar",
                    f"„{c}“ konnte nicht registriert werden. "
                    f"Vermutlich belegt ihn ein anderes Programm.",
                ))

    # ------------------------------------------------------------------ tray

    def _setup_tray(self) -> None:
        image = _create_tray_icon_image()
        self.tray_icon = pystray.Icon(branding.PRODUCT_NAME, image,
                                      self._tray_tooltip(), self._build_tray_menu())
        threading.Thread(target=self.tray_icon.run, daemon=True, name="tray").start()

    def _apply_window_icon(self) -> None:
        """Setzt das Anwendungssymbol auf dem vorhandenen Tk-Weg.

        Beides, weil beides gebraucht wird: `iconbitmap(default=...)` reicht das
        ICO mit allen Groessen an Windows durch (Taskleiste, Alt+Tab), und
        `iconphoto` versorgt Tk-eigene Fenster, die kein ICO annehmen. Ein
        randloses Fenster zeigt selbst kein Symbol — deshalb keine zusaetzliche
        native Schicht dafuer.
        """
        icon = branding.icon_path()
        if icon is not None:
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:      # pragma: no cover - ICO nicht annehmbar
                logger.warning("Fenstersymbol %s abgelehnt", icon, exc_info=True)
        image = branding.app_icon_image(64) or _create_tray_icon_image()
        self._tk_icon = ImageTk.PhotoImage(image)
        self.root.iconphoto(True, self._tk_icon)

    def _tray_tooltip(self) -> str:
        base = branding.PRODUCT_NAME
        if not self.model_ready.is_set():
            return f"{base} — Modell lädt"
        if self.is_recording:
            mode = "Prompt-Modus" if self._prompt_mode else "Aufnahme"
            return f"{base} — {mode}"
        with self._history_lock:
            last = self._history[0] if self._history else None
        if last is not None:
            return f"{base} — zuletzt: {last[1]}"
        return f"{base} — bereit"

    def _update_tray_tooltip(self) -> None:
        if hasattr(self, "tray_icon"):
            self.tray_icon.title = self._tray_tooltip()

    def _history_action(self, index: int):
        """Rückruf für einen Verlaufseintrag — mit genau zwei Parametern.

        pystray liest die Zahl der Positionsparameter aus `co_argcount` und
        lässt nur 0, 1 oder 2 zu; alles andere quittiert es mit `ValueError`.
        Der frühere Schlüssel-Trick

            lambda _icon=None, _item=None, i=idx: ...

        zählt drei — Vorgabewerte ändern daran nichts. Das Tray-Menü liess sich
        deshalb nicht mehr aufbauen, sobald der Verlauf einen Eintrag hatte.
        Die geschlossene Variable hier hält den Index, ohne ihn in die Signatur
        zu schreiben.
        """
        def activate(_icon, _item) -> None:
            self._copy_history_entry(index)

        return activate

    def _build_tray_menu(self) -> pystray.Menu:
        with self._history_lock:
            history_snapshot = list(self._history)

        history_items: list = []
        for idx, entry in enumerate(history_snapshot):
            try:
                stamp, preview, _full = entry
                label = f"{stamp}  —  {preview}"
            except (TypeError, ValueError):
                # Ein unbrauchbarer Eintrag kostet seine Zeile, nicht das Menü.
                logger.warning("Verlaufseintrag %d unlesbar: %r", idx, entry)
                continue
            history_items.append(pystray.MenuItem(label, self._history_action(idx)))
        if not history_items:
            history_items.append(pystray.MenuItem("(leer)", None, enabled=False))

        return pystray.Menu(
            pystray.MenuItem("Zeigen / Verstecken", self._toggle_visibility, default=True),
            pystray.MenuItem("Einstellungen …", self._open_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Letzte Transkripte", pystray.Menu(*history_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self._quit_app),
        )

    def _refresh_tray(self) -> None:
        """Baut das Tray-Menü neu.

        Der Aufbau steht mit im Schutzbereich: er lief bisher davor und riss bei
        einem Fehler den ganzen Tk-Rückruf mit, statt sich im Log zu melden.
        """
        if not hasattr(self, "tray_icon"):
            return
        try:
            self.tray_icon.menu = self._build_tray_menu()
            self.tray_icon.update_menu()
        except Exception:
            logger.exception("Tray-Menü konnte nicht aufgebaut werden")
        self._update_tray_tooltip()

    def _copy_history_entry(self, index: int) -> None:
        with self._history_lock:
            if index >= len(self._history):
                return
            _stamp, _preview, full = self._history[index]
        try:
            pyperclip.copy(full)
            self._ui_call(lambda: self._apply_state("done", "Aus Verlauf kopiert"))
        except pyperclip.PyperclipException:
            logger.exception("Clipboard copy from history failed")
            self._ui_call(lambda: self._apply_state(
                "error", "Zwischenablage nicht verfügbar",
                "Ein anderes Programm blockiert sie gerade. Versuch es erneut.",
            ))

    def _add_to_history(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        preview = (text[:40] + "…") if len(text) > 40 else text
        preview = preview.replace("\n", " ").replace("\r", " ")
        with self._history_lock:
            self._history.insert(0, (stamp, preview, text))
            del self._history[5:]
        self._ui_call(self._refresh_tray)

    def _set_tray_icon(self, color: str) -> None:
        # Der erste Zustand wird gesetzt, bevor das Tray existiert.
        if hasattr(self, "tray_icon"):
            self.tray_icon.icon = _create_tray_icon_image(color)

    def _hide_window(self) -> None:
        self._window_visible = False
        self._flush_position()
        self.root.withdraw()

    def _show_window(self) -> None:
        self._window_visible = True
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        uk.round_window_corners(self.root)

    def _toggle_visibility(self, icon=None, item=None) -> None:
        if self._window_visible:
            self.root.after(0, self._hide_window)
        else:
            self.root.after(0, self._show_window)

    # ---------------------------------------------------------- Darstellung

    def set_appearance(self, mode: str) -> None:
        """Schaltet zwischen hell und dunkel um — sofort, ohne Neustart.

        Reihenfolge mit Bedacht: erst ein offenes Popover schliessen, dann die
        Palette wechseln, dann die Fenster einfaerben. Umgekehrt bliebe ein
        schwebendes Toplevel einen Wimpernschlag in der alten Palette stehen.

        Bewusst ohne Ueberblendung: eine Fensterueberblendung muesste jedes
        Bild neu rendern, und die Flaechen entstehen ueber PIL. Der Wechsel ist
        eine Entscheidung, keine Bewegung.
        """
        mode = Theme.normalise(mode)
        uk.Popover.dismiss_current()
        if not Theme.apply(mode):
            return
        CFG.appearance = mode
        painted = self._retint_windows()
        logger.info("Darstellung auf %s gewechselt (%s Widgets)", mode, painted)

    def _retint_windows(self) -> int:
        """Faerbt alle offenen Hams-Fenster neu ein. Gibt die Widgetzahl zurueck."""
        painted = uk.apply_theme_tree(self.root)
        if self.settings_win is not None and self.settings_win.alive():
            painted += self.settings_win.retint()
        return painted

    def _open_settings(self, icon=None, item=None) -> None:
        def _do() -> None:
            if self.settings_win is not None and self.settings_win.alive():
                self.settings_win.focus()
                return
            self.settings_win = SettingsWindow(self)
        self.root.after(0, _do)

    def restart_app(self) -> str:
        """Ersetzt den laufenden Prozess durch einen frischen. "" bei Erfolg.

        Der eigentliche Grund, warum es diesen Knopf gibt: Windows kann einen
        Low-Level-Tastaturhook jederzeit entfernen, ohne dass die Anwendung
        etwas davon merkt. Der Lauschthread der `keyboard`-Bibliothek dreht
        danach weiter seine `GetMessage`-Schleife, das Tray-Symbol lebt, das
        Fenster reagiert — nur die Kurzbefehle kommen nie wieder an. Ein
        erneutes `add_hotkey()` hilft dagegen nachweislich nicht: die
        Bibliothek merkt sich in `listening`, dass sie schon lauscht, und setzt
        keinen neuen Hook. Nur ein neuer Prozess setzt ihn neu.

        Reihenfolge mit Bedacht: erst das Schloss freigeben, dann starten, dann
        selbst gehen. Schlaegt das Starten fehl, wird das Schloss wieder
        genommen und alles bleibt, wie es war — lieber eine Fehlermeldung als
        ein Rechner ohne laufendes Hams.
        """
        if self.is_recording:
            return "Läuft gerade eine Aufnahme"
        if self.downloading_model is not None:
            return "Ein Download läuft gerade"

        self._flush_position()
        if not _release_single_instance_mutex():
            return "Neustart nicht möglich"

        befehl = restart_command()
        try:
            subprocess.Popen(
                befehl, cwd=str(APP_DIR.parent), close_fds=True,
                creationflags=(subprocess.DETACHED_PROCESS
                               | subprocess.CREATE_NEW_PROCESS_GROUP),
            )
        except Exception:
            logger.exception("Neustart fehlgeschlagen: %s", befehl)
            _acquire_single_instance_mutex()      # Zustand zuruecknehmen
            return "Neustart fehlgeschlagen"

        logger.info("Neustart angefordert: %s", befehl)
        self._shutting_down = True
        self._release_resources()
        try:
            self.tray_icon.stop()
        except Exception:
            logger.exception("Tray stop failed")
        self.root.after(0, self.root.destroy)
        return ""

    def _quit_app(self, icon=None, item=None) -> None:
        logger.info("Quit requested")
        self._flush_position()
        self._shutting_down = True
        winsound.Beep(440, 200)
        self._release_resources()
        try:
            self.tray_icon.stop()
        except Exception:
            logger.exception("Tray stop failed")
        self.root.after(0, self.root.destroy)

    def _release_resources(self) -> None:
        """Stream, Hotkeys und Modell freigeben."""
        self._cancel_max_duration_timer()
        self._stop_elapsed_ticker()
        self._cancel_done_reset()
        if self.stream is not None:
            try:
                self.stream.abort()
                self.stream.close()
            except Exception:
                logger.exception("Stream cleanup failed")
            self.stream = None
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            logger.exception("Hotkey unhook failed")
        self.downloader.cancel()      # unvollstaendige Dateien bleiben, sie setzen fort
        if self.model is not None:
            self.model = None
            self.model_ready.clear()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        """Fünf feste Zonen: Kopf, Status, Pegel, Aktion, Kurzbefehle.

        Jede Zone behält ihren Platz in jedem Zustand — deshalb bleibt die
        Fensterhöhe konstant und nichts springt.
        """
        card = tk.Frame(self.root, bg=Theme.WINDOW_BG)
        card.pack(fill="both", expand=True,
                  padx=uk.px(uk.Space.WINDOW), pady=uk.px(uk.Space.WINDOW))

        # --- 1. Kopfzeile: Wortmarke (ziehbar) + zwei Icon-Schaltflächen ---
        head = tk.Frame(card, bg=Theme.WINDOW_BG)
        head.pack(fill="x")

        title = tk.Label(head, text=branding.PRODUCT_NAME, bg=Theme.WINDOW_BG,
                         fg=Theme.TEXT_PRIMARY, font=uk.font("window_title"),
                         anchor="w")
        title.pack(side="left")
        for widget in (head, title):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

        # Reihenfolge der Erzeugung ist die Reihenfolge der Tabulatorkette; sie
        # soll der Leserichtung folgen (Zahnrad, dann Kreuz). Gepackt wird
        # umgekehrt, weil `side="right"` das zuerst gepackte ganz aussen setzt.
        self._gear_btn = uk.IconButton(head, "settings", self._open_settings,
                                       bg_under=Theme.WINDOW_BG,
                                       tooltip="Einstellungen")
        self._close_btn = uk.IconButton(head, "close", self._hide_window,
                                        bg_under=Theme.WINDOW_BG,
                                        tooltip="Fenster ausblenden")
        self._close_btn.set_hover_colour(Theme.ERROR)
        self._close_btn.pack(side="right")
        self._gear_btn.pack(side="right", padx=(0, uk.px(uk.Space.XS)))

        # --- 2. Statuszeile: die einzige Zustandsanzeige des Produkts ---
        status_row = tk.Frame(card, bg=Theme.WINDOW_BG)
        status_row.pack(fill="x", pady=(uk.px(uk.Space.XL), 0))

        self.status_dot = uk.StatusDot(status_row, bg_under=Theme.WINDOW_BG,
                                       size=uk.StatusDot.SIZE_MAIN)
        self.status_dot.pack(side="left", pady=uk.px(uk.Space.SM))
        self.status_label = tk.Label(status_row, text="", anchor="w",
                                     bg=Theme.WINDOW_BG, fg=Theme.TEXT_PRIMARY,
                                     font=uk.font("status"))
        self.status_label.pack(side="left", padx=(uk.px(uk.Space.MD), 0))
        self.elapsed_label = tk.Label(status_row, text="", anchor="e",
                                      bg=Theme.WINDOW_BG, fg=Theme.TEXT_SECONDARY,
                                      font=uk.font("shortcut"))
        self.elapsed_label.pack(side="right")

        # --- 3. Pegel: immer an derselben Stelle, im Ruhezustand dunkel ---
        self.level_meter = uk.SignalMeter(card, bg_under=Theme.WINDOW_BG)
        self.level_meter.pack(fill="x", pady=(uk.px(uk.Space.LG), 0))

        # --- 4. Hauptaktion ---
        self.main_button = uk.SecondaryButton(
            card, "Aufnahme starten", self._on_main_action,
            bg_under=Theme.WINDOW_BG, role="primary", height=uk.Height.PRIMARY,
        )
        self.main_button.pack(fill="x", pady=(uk.px(uk.Space.LG), 0))

        # --- 5. Fußzeile: Kurzbefehle, im Fehlerfall der Hinweistext ---
        self.footer_label = tk.Label(
            card, text=self._shortcut_hint(), anchor="w", justify="left",
            bg=Theme.WINDOW_BG, fg=Theme.TEXT_TERTIARY, font=uk.font("description"),
        )
        self.footer_label.pack(fill="x", pady=(uk.px(uk.Space.LG), 0))

    def _shortcut_hint(self) -> str:
        """Eine leise Zeile statt der früheren Vier-Label-Tabelle."""
        return f"{CFG.hotkey_transcript}  Transkript   ·   +Alt  KI"

    def _fit_window(self) -> None:
        """Fensterhöhe aus den fünf Zonen berechnen.

        Gerechnet statt gemessen: `winfo_reqheight()` meldet vor dem ersten
        Configure-Ereignis nur die Texthöhe der Buttons, weil deren Fläche erst
        dann gerendert wird. Die Rechnung ist dagegen sofort richtig — und weil
        jede Zone ihren Platz behält, ändert sich die Höhe danach nie wieder.
        """
        line = self._line_height("status")
        footer = self._line_height("description")
        ring = 2 * (uk.Space.XS + uk.Space.XS)      # Fokusring des Buttons

        logical = (
            uk.Space.WINDOW                          # Rand oben
            + uk.Height.STANDARD                     # Kopfzeile
            + uk.Space.XL + line + 2 * uk.Space.SM   # Statuszeile
            + uk.Space.LG + uk.SignalMeter.HEIGHT    # Pegel
            + uk.Space.LG + uk.Height.PRIMARY + ring  # Hauptaktion
            + uk.Space.LG + footer                   # Kurzbefehle
            + uk.Space.WINDOW                        # Rand unten
        )
        width = uk.px(self.WINDOW_WIDTH)
        height = uk.px(max(logical, self.WINDOW_MIN_HEIGHT))
        self._window_size = (width, height)
        self.root.geometry(f"{width}x{height}")

    def _line_height(self, role: str) -> int:
        """Zeilenhöhe einer Schriftrolle in *logischen* Pixeln."""
        try:
            pixels = uk.font(role).metrics("linespace")
        except AttributeError:                        # vor init_fonts()
            pixels = uk.px(14)
        return max(1, int(round(pixels / max(uk.Scale.factor, 0.1))))

    # ------------------------------------------------------------------ Zustände

    #: Zustand -> (Statuspunkt, Text, Buttonrolle, Buttontext, bedienbar)
    STATE_TABLE = {
        # Bedienbar, obwohl das Modell noch laedt: die Aufnahme braucht es
        # nicht (siehe `start_recording`), nur die Auswertung danach. Die
        # Statuszeile nennt den Zustand, der Knopf die moegliche Aktion.
        "model_loading": ("idle", "Modell wird geladen", "primary", "Aufnahme starten", True),
        "ready": ("ready", None, "primary", "Aufnahme starten", True),
        # Aufnahme bewusst ohne gefuellte Signalflaeche: die Semantik tragen der
        # Statuspunkt und das Stop-Symbol, nicht ein roter Vollflaechenbutton.
        "recording": ("recording", "Aufnahme läuft", "secondary", "Stoppen", True),
        "recording_prompt": ("ai", "Prompt-Modus · Aufnahme", "secondary", "Stoppen", True),
        # Waehrend der Verarbeitung nennt der Button weiter die Aktion und ist
        # gesperrt; was gerade laeuft, sagt allein die Statuszeile.
        "transcribing": ("processing", "Transkribiere …", "secondary", "Aufnahme starten", False),
        "cleaning": ("processing", "Bereinige Text …", "secondary", "Aufnahme starten", False),
        "generating": ("ai", "Generiere Antwort …", "secondary", "Aufnahme starten", False),
        "done": ("ready", "Eingefügt", "primary", "Aufnahme starten", True),
        "error": ("error", None, "secondary", "Erneut versuchen", True),
        "no_signal": ("warning", "Kein Mikrofonsignal", "primary", "Aufnahme starten", True),
    }

    RECORDING_STATES = ("recording", "recording_prompt")

    def _apply_state(self, state: str, detail: Optional[str] = None,
                     hint: Optional[str] = None) -> None:
        """Die einzige Stelle, an der sich die Oberfläche des Hauptfensters ändert.

        Der Button zeigt die *Aktion*, die Statuszeile den *Zustand* — nie
        beides dasselbe.

        Args:
            state: Schlüssel aus `STATE_TABLE`.
            detail: Ersetzt den Standardtext der Statuszeile.
            hint: Fußzeilentext; ohne Angabe stehen dort die Kurzbefehle.
        """
        dot, text, role, button_text, enabled = self.STATE_TABLE[state]
        self._state = state

        self.status_dot.set_status(dot)
        if detail is not None:
            label = detail
        elif text is not None:
            label = text
        elif state == "ready":
            label = f"Bereit · {CFG.model_size}"
        else:
            label = ""
        self.status_label.config(text=label)

        self.main_button.set_role(role)
        self.main_button.config(text=button_text)
        self.main_button.set_enabled(enabled)
        self.main_button.set_icon(
            "stop" if state in self.RECORDING_STATES else None,
            Theme.RECORDING if state == "recording" else Theme.AI,
        )

        if state not in self.RECORDING_STATES:
            self.elapsed_label.config(text="")
            self.level_meter.set_level(0.0)

        self.footer_label.config(
            text=hint if hint is not None else self._shortcut_hint(),
            fg=Theme.ERROR if hint is not None else Theme.TEXT_TERTIARY,
        )
        self._set_tray_icon(self._tray_colour(state))
        self._cancel_done_reset()
        if state == "done":
            self._done_job = self.root.after(3000, self._back_to_ready)

    @staticmethod
    def _tray_colour(state: str) -> str:
        """Zustandsfarbe des Tray-Symbols — aus `TRAY_PALETTE`, nicht aus `Theme`.

        Die Bedeutung ist dieselbe wie im Fenster (rot Aufnahme, lila KI), die
        Toene sind es bewusst nicht: das Symbol sitzt auf der Windows-Leiste
        und folgt deren Theme, nicht dem von Hams.
        """
        if state in ("recording",):
            return TRAY_PALETTE["RECORDING"]
        if state in ("recording_prompt", "generating"):
            return TRAY_PALETTE["AI"]
        if state == "error":
            return TRAY_PALETTE["ERROR"]
        return TRAY_PALETTE["ACCENT_TINT"]

    def _cancel_done_reset(self) -> None:
        if getattr(self, "_done_job", None) is not None:
            try:
                self.root.after_cancel(self._done_job)
            except (ValueError, tk.TclError):
                pass
            self._done_job = None

    def _back_to_ready(self) -> None:
        """Der Erfolgszustand fällt von selbst zurück, statt stehen zu bleiben."""
        self._done_job = None
        if not self.is_recording and self.model_ready.is_set():
            self._apply_state("ready")

    def _on_main_action(self) -> None:
        """Der Button führt aus, was sein Text verspricht."""
        if self._state == "error":
            self._retry()
        else:
            self.toggle_recording()

    def _retry(self) -> None:
        if self.model_error or not self.model_ready.is_set():
            self.model_error = None
            self._apply_state("model_loading")
            self._load_model_async()
        else:
            self._apply_state("ready")

    # ------------------------------------------------------------------ Aufnahmezeit

    def _start_elapsed_ticker(self) -> None:
        self._tick_elapsed()

    def _tick_elapsed(self) -> None:
        if not self.is_recording:
            return
        seconds = int(time.monotonic() - self._recording_start)
        self.elapsed_label.config(text=f"{seconds // 60:d}:{seconds % 60:02d}")
        self._elapsed_job = self.root.after(500, self._tick_elapsed)

    def _stop_elapsed_ticker(self) -> None:
        if getattr(self, "_elapsed_job", None) is not None:
            try:
                self.root.after_cancel(self._elapsed_job)
            except (ValueError, tk.TclError):
                pass
            self._elapsed_job = None

    # ------------------------------------------------------------------ sound feedback

    def _beep(self, freq: int, duration: int) -> None:
        if not CFG.sound_feedback:
            return
        threading.Thread(
            target=winsound.Beep, args=(freq, duration), daemon=True, name="beep"
        ).start()

    # ------------------------------------------------------------------ drag

    def _start_drag(self, event: tk.Event) -> None:
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def _on_drag(self, event: tk.Event) -> None:
        x = self.root.winfo_x() + event.x - self.drag_start_x
        y = self.root.winfo_y() + event.y - self.drag_start_y
        self.root.geometry(f"+{x}+{y}")
        self._remember_position(x, y)

    # ------------------------------------------------------------------ Fensterlage

    def _remember_position(self, x: int, y: int) -> None:
        """Merkt die Lage sofort und schreibt sie erst, wenn die Hand still steht.

        Beim Ziehen fallen hunderte Ereignisse an. Jedes davon in die
        Konfiguration zu schreiben hiesse, fuer ein paar Pixel hundertmal eine
        Datei neu anzulegen.
        """
        self._window_pos = (int(x), int(y))
        self._cancel_position_job()
        try:
            self._position_job = self.root.after(POSITION_SAVE_DELAY_MS,
                                                 self._save_position)
        except tk.TclError:      # pragma: no cover - Fenster verschwindet gerade
            self._position_job = None

    def _cancel_position_job(self) -> None:
        if self._position_job is None:
            return
        try:
            self.root.after_cancel(self._position_job)
        except (tk.TclError, ValueError):      # pragma: no cover
            pass
        self._position_job = None

    def _save_position(self) -> None:
        """Schreibt die gemerkte Lage — nur, wenn sie sich geaendert hat."""
        self._position_job = None
        x, y = self._window_pos
        if (x, y) == (CFG.window_x, CFG.window_y):
            return
        CFG.window_x, CFG.window_y = x, y
        _persist_config_value("ui", "window_x", x)
        _persist_config_value("ui", "window_y", y)
        logger.info("Fensterposition gemerkt: %d,%d", x, y)

    def _flush_position(self) -> None:
        """Beim Verstecken und beim Beenden auf jeden Fall sichern.

        Nachgemessen wird nur an einem sichtbaren Fenster. Ein verstecktes
        meldet keine verlaessliche Lage; beim Beenden nach dem Verstecken
        wuerde sonst ein Nullwert eine gute gemerkte Position ueberschreiben.
        """
        self._cancel_position_job()
        try:
            if self.root.winfo_exists() and self.root.winfo_ismapped():
                self._window_pos = (self.root.winfo_x(), self.root.winfo_y())
        except tk.TclError:      # pragma: no cover
            pass
        self._save_position()

    # ------------------------------------------------------------------ model loading

    def _load_model_async(self) -> None:
        threading.Thread(target=self._load_model, daemon=True, name="model-load").start()

    # ------------------------------------------------------------------ Modellwechsel

    @property
    def is_switching_model(self) -> bool:
        return self._switching_to is not None

    def current_model(self) -> Optional[WhisperModel]:
        """Stabile Referenz auf das aktive Modell.

        Ein Transkriptionslauf holt sie sich genau einmal und arbeitet danach
        nur noch damit — so kann ein Wechsel ihm das Modell nicht unter den
        Haenden wegtauschen.
        """
        with self._model_lock:
            return self.model

    def _build_model(self, model_id: str) -> tuple:
        """Laedt ein Modell ueber die konfigurierte Geraete-Matrix.

        Returns:
            (Modell, Geraet, Compute-Typ) oder (None, None, Fehlertext).
        """
        errors: list[str] = []
        for device in CFG.model_device_priority:
            for compute_type in CFG.model_compute_priority.get(device, ()):
                try:
                    logger.info("Loading Whisper model %s on %s/%s",
                                model_id, device, compute_type)
                    model = WhisperModel(model_id, device=device,
                                         compute_type=compute_type)
                    logger.info("Model ready (%s/%s)", device, compute_type)
                    return model, device, compute_type
                except Exception as exc:
                    errors.append(f"{device}/{compute_type}: {exc}")
                    logger.warning("Model load failed: %s", errors[-1])
        return None, None, " | ".join(errors)

    def switch_model(self, model_id: str) -> bool:
        """Startet einen Wechsel. Gibt False zurueck, wenn er gerade nicht geht."""
        if model_id == self.active_model_size and self.model_ready.is_set():
            return False
        if self.is_recording:
            self._apply_state(
                "recording_prompt" if self._prompt_mode else "recording",
                None, "Das Modell lässt sich während einer Aufnahme nicht wechseln.")
            return False
        if self.is_switching_model:
            return False

        self._model_generation += 1
        generation = self._model_generation
        self._switching_to = model_id
        label = whisper_models.find(model_id)
        self._apply_state("model_loading",
                          f"{label.label if label else model_id} wird geladen")
        self._start_switch_thread(model_id, generation)
        return True

    def _start_switch_thread(self, model_id: str, generation: int) -> None:
        """Eigene Methode, damit Tests den Wechsel synchron ausfuehren koennen."""
        threading.Thread(target=self._switch_worker, args=(model_id, generation),
                         daemon=True, name="model-switch").start()

    def _switch_worker(self, model_id: str, generation: int) -> None:
        """Wechsel im Hintergrund — wartet auf eine laufende Transkription.

        Reihenfolge bewusst: erst freigeben, dann laden. Andernfalls laegen
        kurzzeitig zwei Modelle im VRAM; CTranslate2 gibt seinen Speicher erst
        nach dem Dereferenzieren frei. Der Preis ist, dass bei einem Fehlschlag
        kein Modell aktiv waere — deshalb der Rueckfall unten.
        """
        previous = self.active_model_size
        with self._transcribe_lock:          # laufenden Lauf abwarten
            if generation != self._model_generation:
                return                       # inzwischen ueberholt
            with self._model_lock:
                self.model = None
                self.model_ready.clear()
            gc.collect()

            model, device, info = self._build_model(model_id)
            if model is None and previous and previous != model_id:
                logger.warning("Rueckfall auf %s nach Fehler bei %s", previous, model_id)
                model, device, info = self._build_model(previous)
                if model is not None:
                    model_id = previous

            if generation != self._model_generation:
                # Ein neuerer Wechsel hat uebernommen — dieses Ergebnis verwerfen.
                del model
                gc.collect()
                return

            with self._model_lock:
                if model is not None:
                    self.model = model
                    self._active_device = device
                    self._active_compute_type = info
                    self.active_model_size = model_id
                    self.model_error = None
                    self.model_ready.set()
                else:
                    self.model_error = info

        self._switching_to = None
        self.root.after(0, lambda: self._after_switch(model_id, model is not None))

    def _after_switch(self, model_id: str, ok: bool) -> None:
        if ok:
            CFG.model_size = model_id
            _persist_config_value("model", "size", model_id)
            self._apply_state("ready")
        else:
            self._on_model_error()
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.on_model_changed()

    # ------------------------------------------------------------------ Modell-Download

    @property
    def downloading_model(self) -> Optional[str]:
        """Welches Modell gerade geladen wird — oder None."""
        return self.downloader.model_id if self.downloader.active else None

    def start_download(self, model_id: str) -> str:
        """Startet den Download eines Katalogmodells.

        Returns:
            Leerer String, wenn er laeuft — sonst der Grund im Klartext.
        """
        info = whisper_models.find(model_id)
        if info is None:
            return "Unbekanntes Modell"
        self.download_done = 0
        self.download_total = info.size_bytes
        self.download_percent = 0
        problem = self.downloader.start(info, self._on_download_progress,
                                        self._on_download_finished)
        if problem:
            logger.info("Download von %s nicht gestartet: %s", model_id, problem)
        else:
            logger.info("Download von %s gestartet (%s)", model_id, info.size_label)
        return problem

    def cancel_download(self) -> None:
        if self.downloader.active:
            logger.info("Abbruch fuer %s angefordert", self.downloader.model_id)
            self.downloader.cancel()

    def _on_download_progress(self, done: int, total: int) -> None:
        """Laeuft im Download-Thread: nur rechnen, nichts zeichnen."""
        self.download_done = done
        self.download_total = total or self.download_total
        if self.download_total > 0:
            share = int(done * 100 / self.download_total)
            # Bei 99 deckeln: 100 % steht erst, wenn wirklich alles liegt.
            self.download_percent = max(self.download_percent, min(99, share))
        self._post(self._push_download_progress)

    def _on_download_finished(self, model_id: str, error: str) -> None:
        """Laeuft im Download-Thread."""
        self._post(lambda: self._after_download(model_id, error))

    def _post(self, callback) -> None:
        """Reicht einen Rueckruf in den Tk-Thread — auch beim Beenden gefahrlos."""
        if self._shutting_down:
            return
        try:
            self.root.after(0, callback)
        except (tk.TclError, RuntimeError):      # pragma: no cover - Fenster ist weg
            pass

    def _push_download_progress(self) -> None:
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.on_download_progress(
                self.download_done, self.download_total, self.download_percent)

    def _after_download(self, model_id: str, error: str) -> None:
        """Nach dem Download: bei Erfolg direkt aktivieren.

        Der Wechsel gehoert hierher und nicht ins Einstellungsfenster — wer ein
        Modell laedt, will es benutzen, auch wenn er das Fenster inzwischen
        geschlossen hat.
        """
        if not error:
            self.download_percent = 100
        switching = self.switch_model(model_id) if not error else False
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.on_download_finished(model_id, error, switching)

    def _load_model(self) -> None:
        model, device, info = self._build_model(CFG.model_size)
        if model is None:
            self.model_error = info
            logger.error("All model-load attempts failed: %s", info)
            self.root.after(0, self._on_model_error)
            return
        with self._model_lock:
            self.model = model
            self._active_device = device
            self._active_compute_type = info
            self.active_model_size = CFG.model_size
            self.model_ready.set()
        self.root.after(0, lambda: self._on_model_loaded(device, info))

    def _on_model_loaded(self, device: str, compute_type: str) -> None:
        logger.info("Model ready for UI (%s/%s)", device, compute_type)
        self._apply_state("ready")
        self._update_tray_tooltip()

    def _on_model_error(self) -> None:
        self._apply_state(
            "error", "Modell konnte nicht geladen werden",
            f"„{CFG.model_size}“ ließ sich weder auf GPU noch auf CPU laden. "
            f"Einzelheiten stehen im Log.",
        )

    # ------------------------------------------------------------------ recording

    def _may_record(self) -> bool:
        """Darf jetzt aufgenommen werden?

        Frueher hing das am geladenen Modell. Das war eine Sperre zu viel:
        `start_recording` fasst das Modell nirgends an — es oeffnet das
        Mikrofon und sammelt Puffer. Gebraucht wird es erst danach, in
        `_transcribe_audio`.

        Gemessen: bis das Modell steht, vergehen warm rund 2,2 s und kalt
        deutlich mehr. Die mittlere Aufnahmedauer betraegt 11,95 s. Wer gleich
        nach dem Anmelden diktiert, hat also laengst zu Ende gesprochen, bevor
        das Modell ueberhaupt gebraucht wird — es gibt keinen Grund, ihn
        vorher abzuweisen.

        Abgewiesen wird nur, was auch spaeter nicht gutgehen kann: ein Modell,
        das sich gar nicht laden liess. Dann waeren die gesprochenen Worte
        verloren, und das ist schlimmer als ein Hinweis vorab.
        """
        if self.model_error and not self.model_ready.is_set():
            self._on_model_error()
            return False
        return True

    def _await_model(self, deadline: float) -> bool:
        """Wartet auf ein einsatzbereites Modell. False heisst: kommt keines.

        Kein blosses `Event.wait()`: waehrend eines Modellwechsels ist das
        Ereignis kurzzeitig zurueckgesetzt und kann mit einem Fehler enden.
        Deshalb in kleinen Schritten warten und dazwischen nachsehen.
        """
        while time.monotonic() < deadline:
            if self.model_ready.wait(timeout=0.2):
                return True
            if self.model_error and not self.is_switching_model:
                return False
        return self.model_ready.is_set()

    def toggle_recording(self) -> None:
        """Toggle recording state for transcript mode.

        If not recording, starts recording. If recording, stops and transcribes.
        """
        if not self._may_record():
            return

        if not self.is_recording:
            self._prompt_mode = False
            self.start_recording()
        else:
            self.stop_recording_and_transcribe()

    def toggle_recording_generate(self) -> None:
        """Toggle recording state for prompt mode.

        Same as toggle_recording but uses the LLM to generate a response
        instead of just cleaning up the transcript.
        """
        if not self._may_record():
            return

        if not self.is_recording:
            self._prompt_mode = True
            self.start_recording()
        else:
            self.stop_recording_and_transcribe()

    def _schedule_max_duration_stop(self) -> None:
        self._cancel_max_duration_timer()
        self._max_duration_timer = threading.Timer(
            CFG.max_recording_seconds, self._on_max_duration_reached
        )
        self._max_duration_timer.daemon = True
        self._max_duration_timer.name = "max-duration"
        self._max_duration_timer.start()

    def _cancel_max_duration_timer(self) -> None:
        if self._max_duration_timer is not None:
            self._max_duration_timer.cancel()
            self._max_duration_timer = None

    def _on_max_duration_reached(self) -> None:
        logger.warning("Max recording duration (%ss) reached — auto-stopping", CFG.max_recording_seconds)
        self.root.after(0, self._auto_stop_recording)

    def _auto_stop_recording(self) -> None:
        if not self.is_recording:
            return
        self.stop_recording_and_transcribe()

    def _start_level_ticker(self) -> None:
        """Der Pegel bleibt sichtbar — nur der Takt startet und stoppt."""
        self._rms_level = 0.0
        self._level_tick()

    def _stop_level_ticker(self) -> None:
        if self._level_tick_job is not None:
            try:
                self.root.after_cancel(self._level_tick_job)
            except (ValueError, tk.TclError):
                pass
            self._level_tick_job = None
        self.level_meter.set_level(0.0)

    def _level_tick(self) -> None:
        if not self.is_recording:
            return
        # RMS in [0..1], wahrnehmungsorientiert skalieren (sqrt hebt leise Pegel an)
        level = min(1.0, float(np.sqrt(self._rms_level)) * 1.5)
        self.level_meter.set_level(level)
        self._level_tick_job = self.root.after(50, self._level_tick)

    def start_recording(self) -> None:
        """Start audio recording.

        Initializes the audio stream and begins capturing audio from the microphone.
        Recording will automatically stop after max_recording_seconds.
        """
        self.frames.clear()

        def callback(indata: np.ndarray, _frames: int, _time, status) -> None:
            if status:
                logger.warning("Audio stream status: %s", status)
            self.frames.append(indata.copy())
            # RMS für Level-Meter; kein Lock nötig (einzelner Float-Write).
            self._rms_level = float(np.mean(indata.astype(np.float32) ** 2))

        # Mitbenutzer des Mikrofons freigeben, damit das Gerät nicht doppelt
        # geöffnet wird.
        self._suspend_mic_consumers()

        # Gerät pro Aufnahme neu auflösen: Indizes verschieben sich beim
        # Aus-/Einstecken, sonst nimmt die App stumm vom falschen Gerät auf.
        device = _resolve_mic_device(CFG.mic_device_raw)
        if device != CFG.mic_device:
            logger.info("Mic device re-resolved: %r -> %r", CFG.mic_device, device)
            CFG.mic_device = device

        try:
            self.stream = sd.InputStream(
                samplerate=CFG.sample_rate,
                channels=CFG.channels,
                device=device,
                dtype="float32",
                callback=callback,
            )
            self.stream.start()
            self.is_recording = True
            self._recording_start = time.monotonic()
            self._schedule_max_duration_stop()
            self._apply_state("recording_prompt" if self._prompt_mode else "recording")
            self._beep(880, 80)
            self._start_elapsed_ticker()
            self._start_level_ticker()
            self._update_tray_tooltip()
            logger.info("Recording started (prompt_mode=%s)", self._prompt_mode)
        except sd.PortAudioError:
            logger.exception("PortAudio error on start_recording")
            self.is_recording = False
            self._apply_state(
                "error", "Mikrofon nicht verfügbar",
                f"„{CFG.mic_device_raw}“ antwortet nicht. "
                f"Wähle in den Einstellungen ein anderes Gerät.",
            )
        except Exception:
            logger.exception("Unexpected error on start_recording")
            self.is_recording = False
            self._apply_state(
                "error", "Aufnahme nicht gestartet",
                "Einzelheiten stehen im Log.",
            )

    def stop_recording_and_transcribe(self) -> None:
        """Stop recording and start transcription pipeline.

        Stops the audio stream, concatenates recorded frames, and starts
        the transcription thread.
        """
        self.is_recording = False
        self._cancel_max_duration_timer()
        self._stop_elapsed_ticker()
        self._stop_level_ticker()
        self._resume_mic_consumers()
        self._update_tray_tooltip()
        self._beep(220, 120)
        self._apply_state("transcribing")
        duration = time.monotonic() - self._recording_start if self._recording_start else 0
        logger.info("Recording stopped after %.1fs", duration)

        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except sd.PortAudioError:
            logger.exception("PortAudio error on stream.stop")
        finally:
            self.stream = None

        if not self.frames:
            self._apply_state("no_signal", "Keine Aufnahme erkannt",
                              "Es wurden keine Audiodaten empfangen.")
            self._prompt_mode = False
            return

        audio = np.concatenate(self.frames, axis=0).flatten()
        self.frames.clear()

        # Pegel protokollieren — macht "Mikro stumm" im Log sofort sichtbar.
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        logger.info("Recording captured: %.1fs, peak=%.4f", duration, peak)

        # Kein-Signal-Erkennung: klare Meldung statt stiller "Keine Sprache"-Leere.
        if peak < SILENCE_PEAK_THRESHOLD:
            logger.warning(
                "No microphone signal (peak=%.4f) — Mikro stumm oder falsches Gerät?",
                peak,
            )
            self._apply_state(
                "no_signal", None,
                f"„{CFG.mic_device_raw}“ liefert kein Signal. "
                f"Ist das Mikrofon stummgeschaltet?",
            )
            self._prompt_mode = False
            return

        threading.Thread(
            target=self._transcribe_audio,
            args=(audio,),
            daemon=True,
            name="transcribe",
        ).start()

    # ------------------------------------------------------------------ transcription

    def _ollama_generate(
        self,
        prompt: str,
        model: str,
        timeout: int,
        stream: bool = False,
        progress_label: Optional[str] = None,
        system: Optional[str] = None,
    ) -> str:
        """Sendet den Prompt an Ollama. stream=True ist nur für lange Antworten
        sinnvoll (Prompt-Modus); für kurze Cleanup-Antworten lohnt der Overhead
        nicht. keep_alive sorgt dafür, dass das Modell zwischen Aufrufen im
        VRAM bleibt und nicht 10–30s nachgeladen werden muss.

        `system` geht als eigenes Feld an Ollama. So lässt sich eine Regel für
        die Form der Antwort setzen, ohne den Prompt des Nutzers anzufassen."""
        body = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "keep_alive": CFG.ollama_keep_alive,
        }
        if system:
            body["system"] = system
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            CFG.ollama_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if not stream:
                result = json.loads(resp.read())
                return result["response"].strip()

            chunks: list[str] = []
            last_update = 0.0
            for raw_line in resp:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Ollama stream: non-JSON line ignored")
                    continue
                chunks.append(obj.get("response", ""))
                if obj.get("done"):
                    break
                if progress_label:
                    now = time.monotonic()
                    if now - last_update > 0.25:
                        last_update = now
                        total_len = sum(len(c) for c in chunks)
                        self.root.after(
                            0,
                            lambda n=total_len: self.elapsed_label.config(
                                text=f"{n} Zeichen"
                            ),
                        )
            return "".join(chunks).strip()

    def _generate_from_prompt(self, text: str) -> str:
        """Die KI-Antwort zum diktierten Prompt — ebenfalls als Klartext.

        Auch diese Antwort wird unverändert eingefügt; ein `**Oslo**` im
        Textfeld ist dort genauso unerwünscht wie im korrigierten Transkript.
        Zwei Ebenen wie beim Cleanup: eine Systemanweisung an Ollama und
        dieselbe Normalisierung danach. Der diktierte Prompt selbst bleibt
        unangetastet.

        Absätze und Zeilenumbrüche bleiben erhalten — im Prompt-Modus tragen
        sie Bedeutung, anders als eine Hervorhebung.
        """
        answer = self._ollama_generate(
            prompt=text,
            model=CFG.ollama_model_prompt,
            timeout=CFG.ollama_timeout_prompt,
            stream=CFG.ollama_stream_prompt,
            progress_label=f"Generiere ({CFG.ollama_model_prompt})",
            system=PROMPT_PLAIN_SYSTEM,
        )
        return plain_text(answer)

    def _cleanup_text(self, text: str) -> str:
        """Clean up transcript text using LLM.

        Removes stuttering, repetitions, and slips of the tongue.
        Corrects spelling and grammar while preserving original phrasing.

        Die Antwort geht durch `plain_text()`, bevor sie irgendwohin fliesst.
        Der Prompt verbietet Markdown, aber ein Modell haelt sich nicht immer
        daran — und was hier durchrutscht, landet als sichtbares `**` im
        Eingabefeld des Nutzers.
        """
        template = CFG.cleanup_prompt_template
        prompt = template.replace("{text}", text) if "{text}" in template else f"{template}\n\nText: {text}"
        answer = self._ollama_generate(
            prompt=prompt,
            model=CFG.ollama_model_cleanup,
            timeout=CFG.ollama_timeout_cleanup,
            stream=False,
        )
        return plain_text(answer)

    def _save_transcript_file(self, text: str) -> None:
        """Save transcript to a timestamped file in the transcripts directory."""
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_file = OUTPUT_DIR / f"{stamp}.txt"
            out_file.write_text(text, encoding="utf-8")
            logger.info("Saved transcript: %s (%d chars)", out_file.name, len(text))
        except OSError:
            logger.exception("Failed to save transcript file")

    def _warmup_ollama_async(self) -> None:
        if not CFG.ollama_warmup_on_start:
            return
        threading.Thread(
            target=self._warmup_ollama, daemon=True, name="ollama-warmup"
        ).start()

    def _warmup_ollama(self) -> None:
        """Lädt das Cleanup-Modell proaktiv in den VRAM, damit der erste
        echte Request nicht mit einem Kaltstart von 10–30s büßt."""
        if not CFG.ollama_cleanup_enabled:
            return
        model = CFG.ollama_model_cleanup
        try:
            payload = json.dumps({
                "model": model,
                "prompt": "",
                "stream": False,
                "keep_alive": CFG.ollama_keep_alive,
            }).encode()
            req = urllib.request.Request(
                CFG.ollama_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
            logger.info("Ollama warmed up: %s", model)
        except (urllib.error.URLError, TimeoutError) as exc:
            logger.warning("Ollama warmup failed for %s: %s", model, exc)

    def _copy_and_paste(self, text: str) -> None:
        """Copy text to clipboard and paste at cursor position.

        Waits up to clipboard_paste_timeout seconds for clipboard confirmation
        before sending Ctrl+V.
        """
        """Kopiert Text in die Zwischenablage und sendet Ctrl+V. Wartet nur
        kurz, bis der Inhalt bestätigt ist. Normalisiert \\r\\n → \\n, weil
        Windows beim Lesen Zeilenumbrüche umwandelt und sonst der Vergleich
        nie matcht."""
        pyperclip.copy(text)
        target = text.replace("\r\n", "\n")
        deadline = time.monotonic() + CFG.clipboard_paste_timeout
        while time.monotonic() < deadline:
            try:
                if pyperclip.paste().replace("\r\n", "\n") == target:
                    break
            except pyperclip.PyperclipException:
                logger.exception("Clipboard read failed")
                break
            time.sleep(0.01)
        keyboard.send("ctrl+v")

    def _run_transcription(self, model, audio: np.ndarray) -> Optional[str]:
        """Fuehrt Whisper mit einer *festen* Modellreferenz aus.

        Bewusst getrennt, damit der Aufrufer den Transkriptionslock nur um
        diesen Teil legen muss — Cleanup und Einfuegen brauchen ihn nicht und
        wuerden einen Modellwechsel unnoetig lange blockieren.
        """
        optionen = {
            "language": CFG.transcription_language,
            "vad_filter": CFG.transcription_vad_filter,
            "word_timestamps": CFG.transcription_word_timestamps,
            "beam_size": CFG.transcription_beam_size,
            "condition_on_previous_text": CFG.transcription_condition_on_previous_text,
            "without_timestamps": CFG.transcription_without_timestamps,
        }
        try:
            dauer = audio.size / max(CFG.sample_rate, 1)
            if dauer >= BATCHED_MIN_SECONDS:
                # Erlaubt ist das nur, weil zwischen den Segmenten ohnehin kein
                # Kontext fliesst — `condition_on_previous_text` ist aus. Sonst
                # waere die parallele Auswertung nicht dasselbe Ergebnis.
                segments, _ = BatchedInferencePipeline(model=model).transcribe(
                    _normalize_audio(audio), batch_size=BATCH_SIZE, **optionen)
            else:
                segments, _ = model.transcribe(_normalize_audio(audio), **optionen)
            return " ".join(segment.text.strip() for segment in segments).strip()
        except Exception:
            logger.exception("Whisper transcription failed")
            self.root.after(0, lambda: self._apply_state(
                "error", "Transkription fehlgeschlagen",
                "Einzelheiten stehen im Log."))
            self._prompt_mode = False
            return None

    def _transcribe_audio(self, audio: np.ndarray) -> None:
        """Transcription pipeline thread target.

        Runs Whisper transcription, optionally cleans up via LLM,
        saves to file, copies to clipboard, and pastes at cursor.
        """
        was_prompt_mode = self._prompt_mode

        # Die Aufnahme durfte ohne Modell beginnen; hier wird es faellig. In
        # aller Regel steht es laengst — der Nutzer hat waehrend des Ladens
        # gesprochen. Nur der erste Griff kurz nach dem Anmelden wartet hier
        # wirklich, und dann sagt die Statuszeile auch, worauf.
        if not self.model_ready.is_set():
            logger.info("Aufnahme wartet auf das Modell")
            self.root.after(0, lambda: self._apply_state(
                "transcribing", "Modell wird noch geladen …",
                "Die Aufnahme ist gesichert und wird gleich ausgewertet.",
            ))
            if not self._await_model(time.monotonic() + MODEL_WAIT_SECONDS):
                logger.error("Kein Modell fuer eine fertige Aufnahme verfuegbar")
                self.root.after(0, lambda: self._apply_state(
                    "error", "Modell nicht verfügbar",
                    "Die Aufnahme liess sich nicht auswerten.",
                ))
                self._prompt_mode = False
                return
            self.root.after(0, lambda: self._apply_state("transcribing"))

        # Genau einmal eine stabile Referenz holen und den Lauf damit zu Ende
        # bringen; der Lock haelt einen Modellwechsel so lange zurueck.
        with self._transcribe_lock:
            model = self.current_model()
            if model is None:
                self.root.after(0, lambda: self._apply_state(
                    "error", "Kein Modell geladen", "Versuch es erneut."))
                self._prompt_mode = False
                return
            text = self._run_transcription(model, audio)
        if text is None:
            return
        try:
            if not text:
                logger.info("Transcription returned empty text")
                self.root.after(0, lambda: self._apply_state(
                    "no_signal", "Keine Sprache erkannt",
                    "Die Aufnahme enthielt keine verständlichen Wörter.",
                ))
                return

            logger.info("Transcribed %d chars (prompt_mode=%s)", len(text), was_prompt_mode)

            if was_prompt_mode:
                self.root.after(0, lambda: self._apply_state("generating"))
                try:
                    text = self._generate_from_prompt(text)
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
                    logger.exception("Ollama prompt-generation failed")
                    self.root.after(0, lambda: self._apply_state(
                        "error", "KI-Antwort fehlgeschlagen",
                        "Ollama antwortet nicht. Läuft der Dienst?",
                    ))
                    return
            elif CFG.ollama_cleanup_enabled:
                self.root.after(0, lambda: self._apply_state("cleaning"))
                try:
                    text = self._cleanup_text(text)
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                    logger.warning("Ollama cleanup unavailable, using raw transcript: %s", exc)
                    self.root.after(0, lambda: self._apply_state(
                        "done", "Eingefügt · ohne Bereinigung"))
            # else: Cleanup deaktiviert — Roh-Transkript direkt nutzen.

            # Einfügen zuerst, Datei im Hintergrund speichern → kein Disk-I/O vor dem Paste.
            self._add_to_history(text)
            self._copy_and_paste(text)
            threading.Thread(
                target=self._save_transcript_file,
                args=(text,),
                daemon=True,
                name="save-transcript",
            ).start()
            self.root.after(0, lambda: self._apply_state("done"))
        except Exception:
            logger.exception("Transcription pipeline failed")
            self.root.after(0, lambda: self._apply_state(
                "error", "Transkription fehlgeschlagen",
                "Einzelheiten stehen im Log.",
            ))
        finally:
            self._prompt_mode = False


class SettingsWindow:
    """Randloses Einstellungsfenster im Wisper-Design.

    Zeigt Mikrofon-Auswahl mit Live-Pegel, Status-Lampen (Modell/Ollama/Mikro)
    und Schnell-Schalter. Öffnet einen eigenen Monitor-Stream, solange es offen
    ist und nicht aufgenommen wird, damit man den Pegel *vor* dem Sprechen sieht.
    """

    WIDTH = 400                 # logisch
    HEIGHT = 620                # logisch
    OLLAMA_INTERVAL_MS = 5000   # sparsam, aber nicht nur einmal
    CONTROL_WIDTH = 150         # logisch; sonst draengt das Control den Titel raus
    MODEL_WIDTH = 170           # logisch; der Modellname traegt eine Meta-Zeile
    MODEL_HINT = "Tempo und Genauigkeit"
    APPEARANCE_LABELS = {Theme.LIGHT: "Hell", Theme.DARK: "Dunkel"}
    #: Kurz halten: neben der Schaltflaeche bleiben rund 26 Zeichen, alles
    #: darueber schnitt die Zeile zu „Wenn Kurzbefehle nicht me…" ab.
    RESTART_HINT = "Wenn Kurzbefehle streiken"
    RESTART_WIDTH = 108         # logisch; „Neu starten" plus Luft
    DELETE_PLACEHOLDER = "Löschen …"
    DELETE_WIDTH = 88           # logisch; „Löschen" plus Luft
    #: So lange bleibt die Erfolgsmeldung stehen, bevor die Zeile wieder den
    #: Bestand zeigt.
    DELETE_NOTE_MS = 4000

    def __init__(self, app: "FloatingTranscriberApp") -> None:
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title(f"{branding.PRODUCT_NAME} — Einstellungen")
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=Theme.WINDOW_BG)
        try:
            px, py = app.root.winfo_rootx(), app.root.winfo_rooty()
        except Exception:
            px, py = 80, 80
        self._logical_size = (self.WIDTH, self.HEIGHT)
        self.win.geometry(
            f"{uk.px(self.WIDTH)}x{uk.px(self.HEIGHT)}"
            f"+{px + uk.px(24)}+{py + uk.px(24)}"
        )
        self.win.bind("<Escape>", lambda _e: self.close())

        self._mon_stream: Optional[sd.InputStream] = None
        self._mon_rms = 0.0
        self._suspended = False
        self._devices: list = []
        self._device_error = ""
        #: Letzte Antwort des Dienstes, uebernommen von der Anwendung. Die
        #: Signatur daneben entscheidet, ob die Auswahllisten überhaupt neu
        #: gebaut werden müssen.
        self._ollama_state = app.ollama_state
        self._ollama_signature = self._ollama_state.signature()
        self._ollama_online = self._ollama_state.online
        self._ollama_job: Optional[str] = None
        self._installed_models: set = set()
        #: Modell, dessen Download dieses Fenster gerade zeigt.
        self._downloading = ""
        #: Modell, fuer das die Loeschruckfrage gerade offen steht.
        self._delete_target = ""
        self._storage_note_job: Optional[str] = None
        self._tick_job: Optional[str] = None
        self._alive = True
        self._drag = (0, 0)

        self._build()
        self._attach_download()
        uk.Scale.register(self._on_scale_change)
        app.register_mic_consumer(self)
        self.win.update_idletasks()
        self.win.focus_force()
        uk.round_window_corners(self.win)
        self._start_monitor()
        self._tick()
        self._refresh_status()
        self._schedule_ollama_check()

    # ---------------------------------------------------------- lifecycle

    def alive(self) -> bool:
        try:
            return self._alive and bool(self.win.winfo_exists())
        except Exception:
            return False

    def focus(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)

    def retint(self) -> int:
        """Faerbt das Einstellungsfenster neu ein — ohne zu springen.

        Die Rollposition wird ausdruecklich gesichert und wieder gesetzt: das
        Neuzeichnen der Gruppenflaechen loest Configure-Ereignisse aus, und die
        koennen den Ausschnitt verschieben. Die Kopfzeilenlinie wird danach neu
        bewertet, weil sie nur bei gescrolltem Inhalt sichtbar ist.
        """
        offset = self.scroll.offset
        painted = uk.apply_theme_tree(self.win)
        self.scroll.scroll_to(offset)
        self._on_scroll(self.scroll.offset)
        return painted

    def _on_scale_change(self, _factor: float) -> None:
        """Fenstergrösse dem neuen DPI nachziehen.

        Die Controls darin skalieren selbst; das Fenster selbst hat niemand
        gefragt und bliebe sonst in der alten Pixelgrösse stehen.
        """
        if not self.alive():
            return
        width, height = self._logical_size
        try:
            self.win.geometry(f"{uk.px(width)}x{uk.px(height)}")
            uk.round_window_corners(self.win)
        except tk.TclError:      # pragma: no cover - Fenster verschwindet gerade
            pass

    def close(self) -> None:
        self._alive = False
        uk.Scale.unregister(self._on_scale_change)
        if self._ollama_job is not None:
            try:
                self.win.after_cancel(self._ollama_job)
            except (ValueError, tk.TclError):
                pass
            self._ollama_job = None
        if self._tick_job is not None:
            try:
                self.win.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
        if self._storage_note_job is not None:
            try:
                self.win.after_cancel(self._storage_note_job)
            except Exception:
                pass
            self._storage_note_job = None
        self._stop_monitor()
        self.app.unregister_mic_consumer(self)
        try:
            self.win.destroy()
        except Exception:
            pass
        self.app.settings_win = None

    # ---------------------------------------------------------- drag

    def _start_drag(self, e: tk.Event) -> None:
        self._drag = (e.x, e.y)

    def _on_drag(self, e: tk.Event) -> None:
        x = self.win.winfo_x() + e.x - self._drag[0]
        y = self.win.winfo_y() + e.y - self._drag[1]
        self.win.geometry(f"+{x}+{y}")

    # ---------------------------------------------------------- build

    def _build(self) -> None:
        """Kopfzeile, Scrollbereich, vier Gruppen.

        Die Gruppen liegen als eine Fläche mit Trennlinien vor, nicht als eine
        Karte je Einstellung — das ist der Unterschied, der das Fenster ruhig
        wirken lässt.
        """
        head = tk.Frame(self.win, bg=Theme.WINDOW_BG)
        head.pack(fill="x", padx=uk.px(uk.Space.WINDOW),
                  pady=(uk.px(uk.Space.WINDOW), 0))

        title = tk.Label(head, text="Einstellungen", bg=Theme.WINDOW_BG,
                         fg=Theme.TEXT_PRIMARY, font=uk.font("window_title"), anchor="w")
        title.pack(side="left")
        for widget in (head, title):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

        self._close_btn = uk.IconButton(head, "close", self.close,
                                        bg_under=Theme.WINDOW_BG,
                                        tooltip="Einstellungen schliessen")
        self._close_btn.set_hover_colour(Theme.ERROR)
        self._close_btn.pack(side="right")

        # Trennlinie erscheint erst, wenn Inhalt darunter wegscrollt.
        self._head_line = tk.Frame(self.win, bg=Theme.WINDOW_BG, height=max(1, uk.px(1)))
        self._head_line.pack(fill="x", pady=(uk.px(uk.Space.LG), 0))

        self.scroll = uk.ScrollArea(self.win, bg_under=Theme.WINDOW_BG,
                                    on_scroll=self._on_scroll)
        self.scroll.pack(fill="both", expand=True,
                         padx=(uk.px(uk.Space.WINDOW), 0),
                         pady=(0, uk.px(uk.Space.WINDOW)))
        self.scroll.bind_keys(self.win)

        body = self.scroll.body
        self._build_recording(body)
        self._build_transcription(body)
        self._build_ai(body)
        self._build_general(body)

        # Die Tabulatorkette folgt der Stapelreihenfolge, und die entspricht der
        # Reihenfolge der Erzeugung. Die Kopfzeile entsteht zuerst, das Kreuz
        # laege damit *vor* allen Einstellungen. `lift` schiebt die Kopfzeile ans
        # Ende der Kette; sichtbar bleibt sie oben, weil das die Packung regelt.
        head.lift()

    def _group(self, master: tk.Misc, title: str) -> uk.GroupSurface:
        """Abschnittsüberschrift plus zugehörige Gruppenfläche."""
        uk.SectionHeader(master, title, bg_under=Theme.WINDOW_BG).pack(
            fill="x", padx=(uk.px(uk.Space.XS), uk.px(uk.Space.WINDOW)),
            pady=(uk.px(uk.Space.SECTION_GAP), uk.px(uk.Space.MD)))
        group = uk.GroupSurface(master, bg_under=Theme.WINDOW_BG)
        group.pack(fill="x", padx=(0, uk.px(uk.Space.WINDOW)))
        return group

    def _build_recording(self, master: tk.Misc) -> None:
        group = self._group(master, "Aufnahme")

        self.row_mic = uk.SettingRow(group.body, "Mikrofon", variant="status")
        self.row_mic.pack(fill="x")
        self.mic_select = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE,
            on_change=self._select_device, placeholder="Kein Mikrofon gefunden")
        uk.keep_logical_width(self.mic_select, self.CONTROL_WIDTH)
        self.row_mic.set_control(self.mic_select)

        self.row_level = uk.SettingRow(group.body, "Eingangspegel",
                                       variant="simple", hairline=False)
        self.row_level.pack(fill="x")
        self.meter = uk.SignalMeter(group.body, bg_under=Theme.GROUP_SURFACE)
        uk.keep_logical_width(self.meter, self.CONTROL_WIDTH)
        self.row_level.set_control(self.meter)

        self._refresh_devices()

    def _build_transcription(self, master: tk.Misc) -> None:
        group = self._group(master, "Transkription")

        self.row_model = uk.SettingRow(
            group.body, "Whisper-Modell", variant="description",
            description=self.MODEL_HINT)
        self.row_model.pack(fill="x")
        self.model_select = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE, two_line=True,
            on_change=self._select_model)
        uk.keep_logical_width(self.model_select, self.MODEL_WIDTH)
        self.row_model.set_control(self.model_select)
        # Waehrend eines Downloads tritt dieses Kreuz an die Stelle des
        # Auswahlfeldes; es wird erst dann eingehaengt. Bewusst nur ein Icon:
        # eine beschriftete Schaltflaeche nimmt der Fortschrittszeile die
        # Breite, und die Zeile ist die eigentliche Rueckmeldung.
        self.cancel_button = uk.IconButton(
            group.body, "close", self._cancel_download,
            bg_under=Theme.GROUP_SURFACE, tooltip="Download abbrechen")
        self.cancel_button.set_hover_colour(Theme.ERROR)
        self._refresh_models()

        # Eine eigene Zeile fuer den Bestand auf der Platte. Bewusst nicht als
        # Papierkorb neben dem Auswahlfeld: das zeigt immer das *eingestellte*
        # Modell, und genau das darf man nicht loeschen. Eine Loeschung braucht
        # deshalb eine eigene Auswahl.
        self.row_storage = uk.SettingRow(
            group.body, "Modelle löschen", variant="description",
            description="—")
        self.row_storage.pack(fill="x")
        self.select_delete = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE, two_line=True,
            on_change=self._choose_delete, placeholder=self.DELETE_PLACEHOLDER)
        uk.keep_logical_width(self.select_delete, self.CONTROL_WIDTH)
        self.row_storage.set_control(self.select_delete)

        # Die Rueckfrage tritt an die Stelle des Auswahlfeldes — derselbe
        # Kniff wie beim Download-Abbruch. Zwei Schaltflaechen nebeneinander
        # brauchen einen Rahmen, weil eine Zeile nur ein Control aufnimmt.
        self._delete_box = tk.Frame(group.body, bg=Theme.GROUP_SURFACE)
        self.delete_button = uk.SecondaryButton(
            self._delete_box, "Löschen", self._confirm_delete,
            bg_under=Theme.GROUP_SURFACE, role="recording",
            height=uk.Height.STANDARD)
        uk.keep_logical_width(self.delete_button, self.DELETE_WIDTH)
        self.delete_button.pack(side="left")
        self.delete_cancel = uk.IconButton(
            self._delete_box, "close", self._abort_delete,
            bg_under=Theme.GROUP_SURFACE, tooltip="Löschen abbrechen")
        self.delete_cancel.pack(side="left", padx=(uk.px(uk.Space.XS), 0))
        self._refresh_storage()

        self.row_hardware = uk.SettingRow(group.body, "Hardware", variant="display",
                                          value="—", hairline=False)
        self.row_hardware.pack(fill="x")

    def _build_ai(self, master: tk.Misc) -> None:
        group = self._group(master, "KI")

        self.row_cleanup = uk.SettingRow(
            group.body, "Text-Cleanup", variant="description",
            description="Bereinigt das Transkript automatisch",
            interactive=True, command=self._toggle_cleanup)
        self.row_cleanup.pack(fill="x")
        self.switch_cleanup = uk.Switch(group.body, bg_under=Theme.GROUP_SURFACE,
                                        value=CFG.ollama_cleanup_enabled,
                                        command=self._on_cleanup)
        self.row_cleanup.set_control(self.switch_cleanup)

        # Zwei getrennte Auswahlfelder, kein gemeinsames "KI-Modell": Cleanup
        # laeuft oft mit einem kleinen schnellen Modell, der Prompt-Modus mit
        # einem groesseren. Gleiches Bedienelement, getrennte Werte.
        self.row_cleanup_model = uk.SettingRow(group.body, "Cleanup-Modell",
                                               variant="status")
        self.row_cleanup_model.pack(fill="x")
        self.select_cleanup_model = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE, two_line=True,
            value=CFG.ollama_model_cleanup, on_change=self._select_cleanup_model,
            placeholder="Kein Modell")
        uk.keep_logical_width(self.select_cleanup_model, self.MODEL_WIDTH)
        self.row_cleanup_model.set_control(self.select_cleanup_model)

        self.row_prompt_model = uk.SettingRow(
            group.body, "Prompt-Modell", variant="description",
            description="Für den Prompt-Modus", hairline=False)
        self.row_prompt_model.pack(fill="x")
        self.select_prompt_model = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE, two_line=True,
            value=CFG.ollama_model_prompt, on_change=self._select_prompt_model,
            placeholder="Kein Modell")
        uk.keep_logical_width(self.select_prompt_model, self.MODEL_WIDTH)
        self.row_prompt_model.set_control(self.select_prompt_model)
        self._refresh_ollama_models()
        self._show_ollama_status()

    def _build_general(self, master: tk.Misc) -> None:
        group = self._group(master, "Allgemein")

        self.row_sound = uk.SettingRow(
            group.body, "Sound-Feedback", variant="description",
            description="Kurzer Ton bei Start und Stopp",
            interactive=True, command=self._toggle_sound)
        self.row_sound.pack(fill="x")
        self.switch_sound = uk.Switch(group.body, bg_under=Theme.GROUP_SURFACE,
                                      value=CFG.sound_feedback,
                                      command=self._on_sound)
        self.row_sound.set_control(self.switch_sound)

        shortcuts = uk.SettingRow(
            group.body, "Kurzbefehle", variant="display",
            value="\n".join((CFG.hotkey_transcript, CFG.hotkey_prompt)))
        shortcuts.pack(fill="x")
        shortcuts.value_label.configure(font=uk.font("shortcut"))

        # Steht bewusst direkt unter den Kurzbefehlen: das ist der Fall, in dem
        # man ihn braucht. Windows kann den Tastaturhook entfernen, ohne dass
        # die Anwendung etwas davon mitbekommt — Fenster und Tray-Symbol laufen
        # dann weiter, nur die Tastenkombinationen kommen nicht mehr an. Von
        # innen laesst sich der Hook nicht neu setzen (siehe `restart_app`),
        # ein frischer Prozess dagegen schon.
        self.row_restart = uk.SettingRow(
            group.body, "Neustart", variant="description",
            description=self.RESTART_HINT)
        self.row_restart.pack(fill="x")
        self.restart_button = uk.SecondaryButton(
            group.body, "Neu starten", self._restart_app,
            bg_under=Theme.GROUP_SURFACE, height=uk.Height.STANDARD)
        uk.keep_logical_width(self.restart_button, self.RESTART_WIDTH)
        self.row_restart.set_control(self.restart_button)

        # Zwei Entscheidungen an dieser Zeile:
        #
        # Ein Auswahlfeld, kein Schalter. „Dunkel an/aus" hiesse, Hell sei die
        # Abwesenheit von Dunkel; „Darstellung -> Hell/Dunkel" benennt beide
        # Modi gleichrangig — und laesst spaeter Platz fuer ein drittes
        # „System", ohne die Zeile umzubauen.
        #
        # Einzeilig, ohne Unterzeile. Neben dem 150 px breiten Auswahlfeld
        # bleiben rund 150 px fuer den Text, und jede Erklaerung, die den Modus
        # benennt, wurde dort abgeschnitten — gemessen an „Helle oder dunkle
        # Obe...". Das Auswahlfeld sagt ohnehin selbst, worum es geht.
        self.row_appearance = uk.SettingRow(
            group.body, "Darstellung", hairline=False)
        self.row_appearance.pack(fill="x")
        self.select_appearance = uk.Select(
            group.body, bg_under=Theme.GROUP_SURFACE,
            options=[uk.Option(Theme.LIGHT, self.APPEARANCE_LABELS[Theme.LIGHT]),
                     uk.Option(Theme.DARK, self.APPEARANCE_LABELS[Theme.DARK])],
            value=Theme.mode, on_change=self._select_appearance)
        uk.keep_logical_width(self.select_appearance, self.CONTROL_WIDTH)
        self.row_appearance.set_control(self.select_appearance)

    # ---------------------------------------------------------- Kopfzeilenlinie

    def _on_scroll(self, offset: int) -> None:
        """Apple-Detail: die Linie erscheint erst, wenn etwas darunter liegt."""
        self._head_line.configure(
            bg=Theme.HAIRLINE if offset > 0 else Theme.WINDOW_BG)

    # ---------------------------------------------------------- Schalter

    def _toggle_cleanup(self) -> None:
        self.switch_cleanup.toggle()

    def _toggle_sound(self) -> None:
        self.switch_sound.toggle()

    # ---------------------------------------------------------- devices

    # ---------------------------------------------------------- Whisper-Modelle

    def model_options(self) -> list:
        """Katalog als Auswahlliste, getrennt nach installiert und ladbar.

        Ein eigener Wert aus der Konfiguration steht als "Benutzerdefiniert"
        dabei — er wird nicht stillschweigend auf Turbo zurueckgesetzt.
        """
        installed = self._installed_models
        options = []
        for info in whisper_models.CATALOG:
            is_installed = info.model_id in installed
            options.append(uk.Option(
                info.model_id, info.label, meta=info.meta,
                group="Installiert" if is_installed else "Verfügbar",
                trailing="" if is_installed else f"↓ {info.size_label}",
            ))
        current = CFG.model_size
        if whisper_models.find(current) is None:
            options.insert(0, uk.Option(
                current, f"Benutzerdefiniert · {current}",
                meta="Aus der Konfigurationsdatei", group="Installiert"))
        return options

    def _refresh_models(self) -> None:
        self._installed_models = whisper_models.installed_whisper_models()
        self.model_select.set_options(self.model_options())
        self.model_select.set_value(CFG.model_size)
        self._update_model_hint()

    def on_model_changed(self) -> None:
        """Wird von der Anwendung nach einem Wechsel gerufen.

        Die Modell-Lebensdauer haengt bewusst nicht am Einstellungsfenster: der
        Wechsel laeuft weiter, auch wenn es zwischendurch geschlossen wird.
        """
        if not self.alive():
            return
        self._installed_models = whisper_models.installed_whisper_models()
        self.model_select.set_options(self.model_options())
        self.model_select.set_value(CFG.model_size)
        self.model_select.set_enabled(True)
        self._update_model_hint()
        self._refresh_storage()

    def _update_model_hint(self) -> None:
        """Unterscheidet "aktiv" von "fuer den naechsten Start gewaehlt"."""
        if not self.alive() or self._downloading:
            return      # der Fortschritt hat Vorrang vor dem Hinweistext
        chosen = CFG.model_size
        active = self.app.active_model_size
        if chosen not in self._installed_models and whisper_models.find(chosen):
            self.row_model.set_description("Muss zuerst geladen werden")
        elif active is not None and chosen != active:
            self.row_model.set_description("Wird beim nächsten Start verwendet")
        else:
            self.row_model.set_description(self.MODEL_HINT)

    def _select_model(self, model_id: str) -> None:
        """Auswahl uebernehmen — aber nur, wenn das Modell wirklich da ist.

        Ein nicht installiertes Modell wird bewusst *nicht* gespeichert: der
        naechste Start wuerde sonst wortlos mehrere Gigabyte nachladen, weil
        faster-whisper fehlende Modelle stillschweigend herunterlaedt.
        """
        if model_id == CFG.model_size:
            self._update_model_hint()
            return
        if model_id not in self._installed_models:
            self._begin_download(model_id)
            return
        logger.info("Whisper-Modell gewählt: %s", model_id)
        self.model_select.set_value(model_id)
        if self.app.switch_model(model_id):
            self.model_select.set_enabled(False)
            self.row_model.set_description("Modell wird geladen …")
        else:
            self.model_select.set_value(CFG.model_size)
            self.row_model.set_description(
                "Während einer Aufnahme nicht wechselbar")

    # ------------------------------------------------------- Modelle loeschen

    def deletable_models(self) -> list:
        """Was geloescht werden darf — mit Begruendung, was nicht.

        Drei Regeln, und jede hat einen Grund:

        * Das **geladene** Modell bleibt. Windows gibt eine geoeffnete
          Modelldatei nicht her; der Versuch endete mit einem Fehler.
        * Das **eingestellte** Modell bleibt, solange es vollstaendig ist.
          Sonst zeigte die Konfiguration auf etwas, das nicht mehr da ist, und
          faster-whisper laedt Fehlendes beim naechsten Start wortlos nach —
          genau der stille Gigabyte-Download, den die Modellauswahl vermeidet.
        * **Reste abgebrochener Downloads** darf man immer wegraeumen. Sie
          belegen Platz und lassen sich ohnehin nicht laden.
        """
        aktiv = self.app.active_model_size
        eingestellt = CFG.model_size
        erlaubt = []
        for eintrag in whisper_models.cached_whisper_models():
            if eintrag.model_id == aktiv:
                continue
            if eintrag.complete and eintrag.model_id == eingestellt:
                continue
            erlaubt.append(eintrag)
        return erlaubt

    @staticmethod
    def storage_text(bestand: list) -> str:
        """Eine Zeile, die sagt, wie viel auf der Platte liegt.

        Anzahl und Groesse statt einer Namensliste: „Turbo, Large v3, Medium,
        Tiny" lief aus der Zeile heraus, sobald mehr als zwei Modelle lagen.
        Welche es sind, steht im Auswahlfeld darueber und in der Loeschliste.
        """
        if not bestand:
            return "Noch nichts gespeichert"
        gesamt = sum(eintrag.size_bytes for eintrag in bestand)
        wort = "Modell" if len(bestand) == 1 else "Modelle"
        return f"{len(bestand)} {wort} · {whisper_models.format_bytes(gesamt)}"

    def _refresh_storage(self) -> None:
        """Bestandszeile und Loeschauswahl neu aufbauen."""
        if not self.alive():
            return
        self._delete_target = ""
        bestand = list(whisper_models.cached_whisper_models())
        loeschbar = self.deletable_models()
        self.row_storage.set_control(self.select_delete)
        self.select_delete.set_options([
            uk.Option(eintrag.model_id, eintrag.label, meta=eintrag.meta)
            for eintrag in loeschbar])
        self.select_delete.set_value(None)
        self.select_delete.set_enabled(bool(loeschbar) and not self._downloading)
        self.row_storage.set_description(self.storage_text(bestand))

    def _choose_delete(self, model_id: str) -> None:
        """Aus der Auswahl wird eine Rueckfrage — geloescht wird erst danach."""
        eintrag = next((e for e in self.deletable_models()
                        if e.model_id == model_id), None)
        if eintrag is None:
            self._refresh_storage()
            return
        self._delete_target = model_id
        self.row_storage.set_control(self._delete_box)
        self.row_storage.set_description(self.confirm_text(eintrag))

    @staticmethod
    def confirm_text(eintrag) -> str:
        """Die Rueckfrage — je nachdem, ob etwas Brauchbares verschwindet.

        Bei einem abgebrochenen Download steht oft 0 B auf der Platte; „Gibt
        0 B frei" waere dort eine sinnlose Zahl. Dann zaehlt, *was* weggeht,
        nicht wie viel.
        """
        if not eintrag.complete:
            return f"Rest von {eintrag.label} entfernen?"
        return (f"{eintrag.label} löschen? "
                f"{whisper_models.format_bytes(eintrag.size_bytes)} frei")

    def _abort_delete(self) -> None:
        self._refresh_storage()

    def _confirm_delete(self) -> None:
        """Loescht im Hintergrund — auf grossen Modellen dauert das spuerbar."""
        model_id = self._delete_target
        if not model_id:
            return
        self._delete_target = ""
        self.delete_button.set_enabled(False)
        self.delete_cancel.set_enabled(False)
        self.row_storage.set_description("Wird gelöscht …")
        threading.Thread(target=self._delete_worker, args=(model_id,),
                         daemon=True, name="wisper-delete").start()

    def _delete_worker(self, model_id: str) -> None:
        erfolg, _frei, meldung = whisper_models.delete_whisper_model(model_id)
        try:
            self.win.after(0, lambda: self._delete_done(erfolg, meldung))
        except (tk.TclError, RuntimeError):      # Fenster inzwischen zu
            pass

    def _delete_done(self, erfolg: bool, meldung: str) -> None:
        if not self.alive():
            return
        self.delete_button.set_enabled(True)
        self.delete_cancel.set_enabled(True)
        self._refresh_storage()
        self._refresh_models()      # der Bestand hat sich geaendert
        self.row_storage.set_description(meldung)
        logger.info("Modell löschen: %s", meldung)
        # Die Meldung steht kurz, dann uebernimmt wieder der Bestand. Ein
        # dauerhaft stehender Satz waere nach dem naechsten Blick ins Fenster
        # nicht mehr einzuordnen.
        self._storage_note_job = self.win.after(
            self.DELETE_NOTE_MS, self._clear_storage_note)

    def _clear_storage_note(self) -> None:
        self._storage_note_job = None
        if self.alive() and not self._delete_target:
            self.row_storage.set_description(
                self.storage_text(list(whisper_models.cached_whisper_models())))

    # ---------------------------------------------------------- Modell-Download

    @staticmethod
    def download_text(label: str, done: int, total: int, percent: int) -> str:
        """Was beim Warten trägt: Modell, Anteil, absolute Menge — in einer Zeile."""
        if total > 0:
            return (f"{label} · {percent} % · "
                    f"{whisper_models.format_progress(done, total)}")
        return f"{label} · {whisper_models.format_bytes(done)} geladen"

    def _begin_download(self, model_id: str) -> None:
        """Startet den Download aus der Auswahl heraus.

        Die Auswahl selbst bleibt beim bisherigen Modell: gespeichert wird erst,
        wenn das neue wirklich liegt und geladen ist.
        """
        info = whisper_models.find(model_id)
        if info is None:
            return
        self.model_select.set_value(CFG.model_size)
        problem = self.app.start_download(model_id)
        if problem:
            self.row_model.set_description(problem)
            return
        self._downloading = model_id
        self.row_model.set_control(self.cancel_button)
        self.row_model.set_description(self.download_text(info.label, 0, 0, 0))
        self._refresh_storage()

    def _cancel_download(self) -> None:
        self.row_model.set_description("Wird abgebrochen …")
        self.cancel_button.set_enabled(False)
        self.app.cancel_download()

    def _attach_download(self) -> None:
        """Zeigt einen laufenden Download, der aelter ist als dieses Fenster."""
        running = self.app.downloading_model
        if running is None:
            return
        self._downloading = running
        self.row_model.set_control(self.cancel_button)
        self.on_download_progress(self.app.download_done, self.app.download_total,
                                  self.app.download_percent)

    def on_download_progress(self, done: int, total: int, percent: int) -> None:
        if not self.alive() or not self._downloading:
            return
        info = whisper_models.find(self._downloading)
        label = info.label if info else self._downloading
        self.row_model.set_description(self.download_text(label, done, total, percent))

    def on_download_finished(self, model_id: str, error: str, switching: bool) -> None:
        """Raeumt die Download-Anzeige wieder ab.

        Bei Erfolg bleibt das Auswahlfeld gesperrt: der Modellwechsel laeuft
        weiter und meldet sich ueber `on_model_changed` zurueck.
        """
        if not self.alive():
            return
        self._downloading = ""
        self.cancel_button.set_enabled(True)
        self.row_model.set_control(self.model_select)
        self._refresh_storage()
        self._installed_models = whisper_models.installed_whisper_models()
        self.model_select.set_options(self.model_options())
        self.model_select.set_value(CFG.model_size)
        info = whisper_models.find(model_id)
        label = info.label if info else model_id
        if error == whisper_models.CANCELLED:
            self.model_select.set_enabled(True)
            self.row_model.set_description(f"{label}: Download abgebrochen")
        elif error:
            self.model_select.set_enabled(True)
            self.row_model.set_description(f"{error} — erneut auswählen")
        elif switching:
            self.model_select.set_enabled(False)
            self.row_model.set_description(f"{label} wird geladen …")
        else:
            self.model_select.set_enabled(True)
            self._update_model_hint()

    def device_options(self) -> list:
        """(Anzeigename, Wert) je Eingabegerät — Logik ohne Widget.

        Phase 12 tauscht nur das Control aus; diese Liste bleibt unverändert.
        """
        options = [("System-Standard", None)]
        options += [(pretty, pretty) for pretty, _full in _list_input_devices()]
        return options

    def refresh_device_state(self) -> None:
        """Geräteliste neu einlesen und den Zustand daraus ableiten."""
        self._devices = _list_input_devices()
        selected = CFG.mic_device_raw
        if not self._devices:
            self._device_error = "Kein Mikrofon gefunden"
        elif isinstance(selected, str) and selected.strip().lower() not in ("", "default"):
            # Die Config speichert einen Teilstring des Geraetenamens, keinen
            # exakten Namen - genau so sucht auch _resolve_mic_device().
            needle = selected.strip().lower()
            found = any(needle in pretty.lower() or needle in full.lower()
                        for pretty, full in self._devices)
            self._device_error = "" if found else f"„{selected}“ ist nicht verbunden"
        else:
            self._device_error = ""

    def _current_device_label(self) -> str:
        raw = CFG.mic_device_raw
        if not (isinstance(raw, str) and raw.strip() and raw.strip().lower() != "default"):
            return "System-Standard"
        return re.sub(r"^\d+-\s*", "", raw)

    def _refresh_devices(self) -> None:
        """Geräteliste einlesen und ins Select geben — Logik bleibt getrennt."""
        self.refresh_device_state()
        options = [uk.Option(value, label) for label, value in self.device_options()]
        self.mic_select.set_options(options)
        self.mic_select.set_value(self._selected_device_value())
        self.mic_select.set_enabled(bool(self._devices))

    def _selected_device_value(self):
        """Der Config-Wert als Optionswert — Teilstring auf Anzeigename gemappt."""
        raw = CFG.mic_device_raw
        if not (isinstance(raw, str) and raw.strip()
                and raw.strip().lower() != "default"):
            return None
        needle = raw.strip().lower()
        for pretty, full in self._devices:
            if needle in pretty.lower() or needle in full.lower():
                return pretty
        return raw

    def _select_device(self, pretty: Optional[str]) -> None:
        if pretty is None:
            CFG.mic_device_raw = "default"
            CFG.mic_device = None
            _persist_config_value("audio", "device", "default")
        else:
            CFG.mic_device_raw = pretty
            CFG.mic_device = _resolve_mic_device(pretty)
            _persist_config_value("audio", "device", pretty)
        logger.info("Mikrofon gewählt: %r -> %r", CFG.mic_device_raw, CFG.mic_device)
        self.refresh_device_state()
        self._restart_monitor()

    # ---------------------------------------------------------- monitor stream

    def _start_monitor(self) -> None:
        if self._suspended or self.app.is_recording or self._mon_stream is not None:
            return
        if not self._devices and self._device_error:
            return      # kein Eingabegerät — gar nicht erst versuchen
        try:
            dev = _resolve_mic_device(CFG.mic_device_raw)

            def cb(indata: np.ndarray, _frames: int, _time, _status) -> None:
                self._mon_rms = float(np.mean(indata.astype(np.float32) ** 2))

            self._mon_stream = sd.InputStream(
                samplerate=CFG.sample_rate, channels=CFG.channels, device=dev,
                dtype="float32", callback=cb,
            )
            self._mon_stream.start()
        except Exception as exc:
            logger.warning("Monitor-Stream fehlgeschlagen: %s", exc)
            self._mon_stream = None

    def _stop_monitor(self) -> None:
        if self._mon_stream is not None:
            try:
                self._mon_stream.stop()
                self._mon_stream.close()
            except Exception:
                pass
            self._mon_stream = None
        self._mon_rms = 0.0

    def _restart_monitor(self) -> None:
        self._stop_monitor()
        self._start_monitor()

    # ---------------------------------------------------------- Mikrofon-Teilhabe

    def suspend_microphone(self) -> None:
        """Die Aufnahme braucht das Gerät — Monitor anhalten."""
        self._suspended = True
        self._stop_monitor()

    def resume_microphone(self) -> None:
        """Aufnahme vorbei — Monitor darf zurückkehren, sofern noch offen."""
        self._suspended = False
        if self.alive() and not self.app.is_recording:
            self._start_monitor()

    # ---------------------------------------------------------- ticks / status

    def _tick(self) -> None:
        if not self.alive():
            return
        if self.app.is_recording:
            rms = self.app._rms_level
        else:
            if self._mon_stream is None and not self._suspended:
                self._start_monitor()
            rms = self._mon_rms
        level = min(1.0, (rms ** 0.5) * 1.5)
        self.meter.set_level(level)
        self._update_mic_status(level)
        self._tick_job = self.win.after(60, self._tick)

    def _update_mic_status(self, level: float) -> None:
        """Mikrofonstatus als Punkt *und* Text — nie allein über die Farbe."""
        if self._device_error:
            self.row_mic.set_status("error", self._device_error)
        elif self._mon_stream is None and not self.app.is_recording:
            self.row_mic.set_status("warning", "Gerät nicht verfügbar")
        elif level < 0.015:
            self.row_mic.set_status("error", "Kein Signal")
        elif level < 0.08:
            self.row_mic.set_status("warning", "Leiser Pegel")
        else:
            self.row_mic.set_status("ready", "Gutes Signal")

    def _refresh_status(self) -> None:
        if not self.alive():
            return
        app = self.app
        if app.model_error:
            self.row_hardware.set_value("Fehler beim Laden")
        elif app.model_ready.is_set():
            device = app._active_device or "?"
            label = "GPU" if device == "cuda" else device.upper()
            self.row_hardware.set_value(f"Automatisch · {label}")
        else:
            self.row_hardware.set_value("Modell wird geladen …")
            self.win.after(800, self._refresh_status)

    def _schedule_ollama_check(self) -> None:
        """Alle paar Sekunden nachsehen — sparsam, aber nicht nur einmal.

        Bisher wurde genau beim Öffnen geprüft; startete Ollama danach, blieb
        die Anzeige dauerhaft falsch.
        """
        if not self.alive():
            return
        threading.Thread(target=self._check_ollama, daemon=True,
                         name="ollama-check").start()
        self._ollama_job = self.win.after(self.OLLAMA_INTERVAL_MS,
                                          self._schedule_ollama_check)

    def _check_ollama(self) -> None:
        """Laeuft im Hintergrundthread — hier nur fragen, nichts zeichnen."""
        state = ollama_api.probe(ollama_api.base_url(CFG.ollama_url))
        try:
            self.win.after(0, lambda: self._apply_ollama_state(state))
        except Exception:      # pragma: no cover - Fenster ist schon weg
            pass

    def _show_ollama_status(self) -> None:
        """Statuszeile aus dem bekannten Stand — ohne selbst zu fragen.

        Solange in dieser Sitzung noch nie geantwortet wurde, steht dort
        "wird geprüft". Einen Ausfall zu behaupten, den niemand gemessen hat,
        waere schlicht falsch.
        """
        if not self.app.ollama_checked:
            self.row_cleanup_model.set_status("idle", "Ollama wird geprüft …")
            return
        state = self._ollama_state
        self.row_cleanup_model.set_status(
            "ready" if state.online else "error",
            "Ollama erreichbar" if state.online
            else state.error or "Ollama nicht erreichbar")

    def _apply_ollama_state(self, state) -> None:
        """Uebernimmt eine Antwort — auf dem Tk-Thread."""
        if not self.alive():
            return
        self.app.ollama_state = state
        self.app.ollama_checked = True
        self._ollama_online = state.online
        if state.signature() != self._ollama_signature:
            popover = uk.Popover.current
            if popover is not None and popover.trigger in (self.select_cleanup_model,
                                                           self.select_prompt_model):
                self._show_ollama_status()
                return      # geoeffnete Auswahl nicht unter den Fingern austauschen
            self._ollama_state = state
            self._ollama_signature = state.signature()
            self._refresh_ollama_models()
        self._show_ollama_status()
        self._sync_cleanup_dependency()

    # ---------------------------------------------------------- Ollama-Modelle

    @staticmethod
    def ollama_options(state, current: str) -> list:
        """Auswahlliste aus einer Dienstantwort — Logik ohne Widget.

        Nur installierte Modelle. Ollama kennt ueber diese API keinen Katalog
        entfernter Modelle, also wird auch keiner erfunden.
        """
        options = [
            uk.Option(model.name, model.name, meta=model.meta,
                      group="Installierte Modelle",
                      trailing="im Speicher" if model.name in state.warm else "")
            for model in state.models
        ]
        if current and current not in state.names:
            # Das konfigurierte Modell fehlt. Es bleibt sichtbar und bleibt
            # gespeichert: stillschweigend ein anderes zu waehlen waere eine
            # Entscheidung, die dem Nutzer gehoert.
            if state.online:
                meta = "Nicht installiert"
            elif state.error:
                meta = state.error
            else:
                meta = ""      # noch nie gefragt — nichts behaupten
            options.insert(0, uk.Option(current, current, meta=meta,
                                        group="Installierte Modelle", enabled=False))
        if state.online and options:
            options.append(uk.Option("", ollama_api.PULL_HINT, enabled=False))
        return options

    def _refresh_ollama_models(self) -> None:
        if not self.alive():
            return
        state = self._ollama_state
        self.select_cleanup_model.set_options(
            self.ollama_options(state, CFG.ollama_model_cleanup))
        self.select_cleanup_model.set_value(CFG.ollama_model_cleanup)
        self.select_prompt_model.set_options(
            self.ollama_options(state, CFG.ollama_model_prompt))
        self.select_prompt_model.set_value(CFG.ollama_model_prompt)

    def _select_cleanup_model(self, name: str) -> None:
        """Schreibt ausschliesslich den Cleanup-Schluessel."""
        if not name or name == CFG.ollama_model_cleanup:
            return
        CFG.ollama_model_cleanup = name
        _persist_config_value("ollama", "model_transcript_cleanup", name)
        logger.info("Cleanup-Modell: %s", name)
        self.select_cleanup_model.set_value(name)

    def _select_prompt_model(self, name: str) -> None:
        """Schreibt ausschliesslich den Prompt-Schluessel."""
        if not name or name == CFG.ollama_model_prompt:
            return
        CFG.ollama_model_prompt = name
        _persist_config_value("ollama", "model_prompt_generate", name)
        logger.info("Prompt-Modell: %s", name)
        self.select_prompt_model.set_value(name)

    # ---------------------------------------------------------- toggles

    def _on_cleanup(self, value: bool) -> None:
        CFG.ollama_cleanup_enabled = value
        _persist_config_value("ollama", "cleanup_enabled", value)
        self._sync_cleanup_dependency()

    def _sync_cleanup_dependency(self) -> None:
        """Cleanup an, Ollama aus — das muss sofort sichtbar sein.

        Sonst zeigte das Fenster einen aktiven Schalter neben einem Dienst, der
        gar nicht antwortet, und der Widerspruch fiel erst nach der nächsten
        Aufnahme auf.
        """
        if not self.alive():
            return
        self.row_cleanup_model.set_enabled(
            CFG.ollama_cleanup_enabled and self._ollama_online)
        # Der Prompt-Modus haengt nicht am Cleanup-Schalter. Beide gemeinsam
        # abzuschalten waere bequem und falsch: sie sind unabhaengig.
        self.row_prompt_model.set_enabled(self._ollama_online)
        self.row_prompt_model.set_description(
            "Für den Prompt-Modus" if self._ollama_online
            else "Ollama nicht erreichbar")
        if CFG.ollama_cleanup_enabled and not self._ollama_online:
            self.row_cleanup.set_description(
                "Ollama ist nicht erreichbar — Rohtext wird eingefügt")
        else:
            self.row_cleanup.set_description(
                "Bereinigt das Transkript automatisch")

    def _restart_app(self) -> None:
        """Startet Hams neu — oder sagt, warum gerade nicht.

        Kein Rueckfragedialog: der Knopf sitzt hinter einer eigenen Zeile mit
        klarer Beschriftung, und die beiden Faelle, in denen ein Neustart
        wirklich weh taete (laufende Aufnahme, laufender Download), lehnt
        `restart_app` von sich aus ab.
        """
        self.restart_button.set_enabled(False)
        self.row_restart.set_description("Wird neu gestartet …")
        grund = self.app.restart_app()
        if grund:
            self.restart_button.set_enabled(True)
            self.row_restart.set_description(grund)
            logger.info("Neustart abgelehnt: %s", grund)

    def _select_appearance(self, mode) -> None:
        """Schreibt die Wahl und wendet sie sofort an.

        Erst speichern, dann umschalten: das Umfaerben beruehrt dieses Fenster
        selbst, und eine fehlgeschlagene Schreibaktion soll nicht mitten in
        einem halb neu gezeichneten Baum auffallen.
        """
        mode = Theme.normalise(mode)
        if mode == Theme.mode:
            return
        _persist_config_value("ui", "appearance", mode)
        self.app.set_appearance(mode)

    def _on_sound(self, value: bool) -> None:
        CFG.sound_feedback = value
        _persist_config_value("ui", "sound_feedback", value)


def main() -> None:
    global CFG
    state = ensure_user_config()
    logger.info("Nutzerkonfiguration %s (%s)", CONFIG_PATH, state)
    if state in ("migriert", "angelegt"):
        CFG = _load_config()      # die eben erst entstandene Datei mitnehmen

    mutex_handle = _acquire_single_instance_mutex()
    if mutex_handle is None:
        logger.warning("Another Wisper instance is already running — exiting")
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"{branding.PRODUCT_NAME} läuft bereits. "
                f"Überprüfe das System-Tray.",
                branding.PRODUCT_NAME,
                0x40,
            )
        except Exception:
            pass
        sys.exit(0)

    try:
        awareness = uk.enable_dpi_awareness()   # muss vor tk.Tk() stehen
        logger.info("DPI awareness: %s", awareness)
        root = tk.Tk()
        FloatingTranscriberApp(root)
        root.mainloop()
    finally:
        # Beim Neustart ist das Schloss schon freigegeben; dann ist hier nichts
        # mehr zu tun und der Nachfolger haelt es bereits.
        _release_single_instance_mutex()


if __name__ == "__main__":
    main()
