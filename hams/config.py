"""Konfiguration: Pfade, Einstellungen aus der TOML-Datei, gezieltes Zurueckschreiben und das Log."""

import logging
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import sounddevice as sd

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[import-not-found]

import contextlib

from hams import app_paths, branding, dictation_language, i18n
from hams.config_toml import _abschnitt_finden, _toml_scalar, _toml_setzen, _trailing_comment  # noqa: F401
from hams.config_validation import ollama_url_problem, validate_config
from hams.ui_kit import Theme
from hams.window_position import _clean_coordinate

APP_DIR = Path(__file__).resolve().parent
#: Not inside the package: installed, the program folder belongs to the installer (see app_paths).
OUTPUT_DIR = app_paths.transcripts_dir()


def _log_dir() -> Path:
    """Wohin das Log geht.

    `HAMS_LOG_DIR` hat Vorrang — dasselbe Muster wie `HAMS_CONFIG` und aus
    demselben Grund: die Testsuite darf die Aufzeichnung der Anwendung nicht
    beschreiben. Ein Testlauf legte hier sonst 218 Zeilen ab, zwischen denen
    die echten Eintraege des Nutzers stehen; wer spaeter einen Fehler sucht,
    liest dann Zeitstempel aus pytest-Temporaerverzeichnissen.

    Sonst `%LOCALAPPDATA%\\Hams\\logs` bzw. `~/.local/share/hams/logs` — nie der
    Programmordner, den der Installer verwaltet.
    """
    override = os.environ.get("HAMS_LOG_DIR")
    return Path(override) if override else app_paths.default_log_dir()


LOG_DIR = _log_dir()

#: Mitgelieferte Vorlage. Enthält Voreinstellungen, aber keinen Nutzerzustand.
DEFAULT_CONFIG_PATH = APP_DIR / "config.default.toml"
#: Wo frühere Versionen ihre Konfiguration hatten — mitten im Projektordner.
#: Nur noch Quelle für die einmalige Übernahme.
LEGACY_CONFIG_PATH = APP_DIR / "config.toml"


#: Ordnername der Nutzerdaten: `%APPDATA%\Hams` unter Windows, `~/.config/hams` sonst (XDG-Konvention: klein).
USER_DIR_NAME = "Hams" if sys.platform == "win32" else "hams"
#: Ordnername aus der Zeit, als das Programm noch „Wisper“ hiess. Nur noch Quelle der einmaligen Übernahme.
LEGACY_USER_DIR_NAME = "Wisper"


def _config_base() -> Path:
    """Der Ort, unter dem Programme ihre Nutzerdaten ablegen."""
    if sys.platform != "win32":
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    base = os.environ.get("APPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Roaming"


def _user_config_path() -> Path:
    """Wo die persönliche Konfiguration liegt.

    `HAMS_CONFIG` hat Vorrang. Daran hängt die Testsuite: sie darf die echte
    Konfiguration des Nutzers weder lesen noch beschreiben. Sonst der übliche
    Ort für Anwendungsdaten der Plattform.
    """
    override = os.environ.get("HAMS_CONFIG")
    if override:
        return Path(override)
    return _config_base() / USER_DIR_NAME / "config.toml"


def _legacy_user_dir() -> Path | None:
    """Der Nutzerordner aus der Wisper-Zeit, oder None, wenn `HAMS_CONFIG` umleitet.

    Mit Umleitung gibt es nichts zu übernehmen: die Testsuite darf den echten
    alten Ordner nie zu sehen bekommen.
    """
    if os.environ.get("HAMS_CONFIG"):
        return None
    return _config_base() / LEGACY_USER_DIR_NAME


CONFIG_PATH = _user_config_path()
LEGACY_USER_DIR = _legacy_user_dir()

#: Die deutsche Rückfall-Anweisung; sie steht jetzt bei den übrigen Sprachtexten.
DEFAULT_CLEANUP_PROMPT = dictation_language.DEFAULT_CLEANUP_PROMPT


@dataclass
class Config:
    sample_rate: int = 16_000
    channels: int = 1
    mic_device: str | int | None = None
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

    #: Diktiersprache, siehe `dictation_language.SETTINGS`. Leer heisst: kein Eintrag in Vorlage oder
    #: Nutzerdatei — `_load_config` setzt dann die Sprache der Oberflaeche ein. Bestehende Dateien
    #: tragen "de" und behalten es.
    transcription_language: str = ""
    transcription_vad_filter: bool = True
    transcription_word_timestamps: bool = False
    transcription_beam_size: int = 1
    transcription_condition_on_previous_text: bool = False
    transcription_without_timestamps: bool = True

    hotkey_transcript: str = "ctrl+shift+space"
    hotkey_prompt: str = "ctrl+shift+alt+space"

    #: Bewusst die Zahlenadresse statt `localhost`: siehe `ipv4_loopback`.
    ollama_url: str = "http://127.0.0.1:11434/api/generate"
    ollama_model_cleanup: str = "qwen3.5:9b"
    ollama_model_prompt: str = "qwen3.5:9b"
    ollama_timeout_cleanup: int = 30
    ollama_timeout_prompt: int = 60
    ollama_stream_prompt: bool = True
    ollama_cleanup_enabled: bool = True
    ollama_keep_alive: str = "30m"
    ollama_warmup_on_start: bool = True
    #: Zuletzt bekannte Lage des Hauptfensters in physischen Bildschirmpixeln.
    #: None heisst: noch nie gemerkt, es gilt der Rueckfall.
    window_x: int | None = None
    window_y: int | None = None
    cleanup_prompt_template: str = DEFAULT_CLEANUP_PROMPT

    clipboard_paste_timeout: float = 0.3
    sound_feedback: bool = True
    #: Darstellungsmodus, "light" oder "dark". Ohne Eintrag in der Nutzerdatei
    #: gilt hell — bewusst nicht das Windows-Systemtheme, der Nutzer waehlt
    #: selbst. Ein unbekannter Wert faellt in `Theme.normalise()` auf hell
    #: zurueck, damit eine alte oder vertippte Datei nie ein farbloses Fenster
    #: ergibt.
    appearance: str = Theme.DEFAULT_MODE
    #: Sprache der Oberflaeche: "auto" (Systemsprache), "de" oder "en". Gilt
    #: ab dem Start; `main` aktiviert sie ueber `i18n.set_language`.
    ui_language: str = i18n.DEFAULT_SETTING
    #: Einmal am Tag beim Start bei GitHub nach einer neuen Version fragen (siehe updates.py).
    update_check: bool = True


def _resolve_mic_device(raw: Any) -> str | int | None:
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
            if dev.get("max_input_channels", 0) > 0 and needle in str(dev.get("name", "")).lower()
        ]
        if not matches:
            return raw
        matches.sort(key=lambda idx: (devices[idx].get("hostapi") != default_hostapi, idx))
        return matches[0]
    return None


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
    except ValueError:  # kaputte URL: unveraendert weiterreichen
        return url
    if (parts.hostname or "").lower() != "localhost":
        return url
    try:
        port = parts.port
    except ValueError:  # unbrauchbarer Port: nicht anfassen
        return url
    host = "127.0.0.1" if port is None else f"127.0.0.1:{port}"
    userinfo, at, _ = parts.netloc.rpartition("@")
    return urllib.parse.urlunsplit(parts._replace(netloc=f"{userinfo}{at}{host}"))


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
    return _atomar_schreiben(_toml_setzen(text, section, key, value))


def _atomar_schreiben(text: str) -> bool:
    """Über eine temporäre Datei – ein Abbruch mitten im Schreiben lässt die Konfiguration nie halbiert zurück."""
    tmp_path = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, CONFIG_PATH)
        return True
    except OSError:
        logger.exception("Config schreiben fehlgeschlagen")
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        return False


def _list_input_devices() -> list[tuple[str, str]]:
    """Eingabegeräte als (Anzeigename, Such-String) — pro physischem Mikro nur
    einmal (bevorzugt die Standard-Host-API).

    Gibt es WASAPI, zählen nur dessen Geräte: das ist genau die Liste, die auch
    Windows unter Eingabe zeigt. MME kürzt Namen auf 31 Zeichen, und WDM-KS
    führt sogar Geräte auf, die in Windows deaktiviert sind (Kamera-Mikrofon,
    Stereomix) — beides ergab doppelte und unerwünschte Einträge.
    """
    try:
        devices = sd.query_devices()
        default_hostapi = sd.default.hostapi
        wasapi = next((i for i, api in enumerate(sd.query_hostapis()) if "WASAPI" in str(api.get("name", ""))), None)
    except Exception:
        return []
    seen: dict[str, tuple[str, str, int]] = {}
    for dev in devices:
        if dev.get("max_input_channels", 0) <= 0:
            continue
        if wasapi is not None and dev.get("hostapi") != wasapi:
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


def migrate_legacy_user_dir(new_dir: Path, old_dir: Path | None) -> bool:
    """Übernimmt den Nutzerordner aus der Wisper-Zeit, genau einmal.

    Nur wenn es den neuen Ordner noch nicht gibt, den alten aber schon. Der
    alte wird **kopiert**, nie verschoben oder gelöscht: eine noch laufende
    ältere Fassung liest weiter daraus, und ein Rückweg bleibt offen. Kopiert
    wird erst in einen Nachbarordner und dann umbenannt, damit ein Abbruch
    keinen halben neuen Ordner hinterlässt, der die Übernahme beim nächsten
    Start verhinderte.

    Gibt zurück, ob kopiert wurde.
    """
    if old_dir is None or new_dir.exists() or not old_dir.is_dir():
        return False
    staging = new_dir.with_name(new_dir.name + ".migrating")
    try:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(old_dir, staging)
        os.replace(staging, new_dir)
    except (OSError, shutil.Error):
        logger.error("Nutzerdaten aus %s liessen sich nicht nach %s kopieren", old_dir, new_dir, exc_info=True)
        shutil.rmtree(staging, ignore_errors=True)
        return False
    logger.info("Nutzerdaten aus %s nach %s kopiert (der alte Ordner bleibt unverändert)", old_dir, new_dir)
    return True


def ensure_user_config() -> str:
    """Sorgt dafür, dass eine persönliche Konfiguration existiert.

    Genau einmal: existiert die Nutzerdatei bereits, wird nichts kopiert. Eine
    später geänderte Vorlage darf persönliche Werte nie wieder überschreiben.

    Gibt zurück, was geschehen ist — "vorhanden", "migriert", "angelegt" oder
    "fehlgeschlagen".
    """
    if migrate_legacy_user_dir(CONFIG_PATH.parent, LEGACY_USER_DIR) and CONFIG_PATH.exists():
        return "migriert"
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
        logger.error(
            "Nutzerkonfiguration %s liess sich nicht anlegen — Einstellungen sind in dieser Sitzung nicht speicherbar",
            CONFIG_PATH,
            exc_info=True,
        )
        return "fehlgeschlagen"
    if source is LEGACY_CONFIG_PATH:
        logger.info("Konfiguration aus %s nach %s übernommen", LEGACY_CONFIG_PATH, CONFIG_PATH)
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
        print(
            f"[{branding.PRODUCT_NAME}] could not load {path.name} ({exc}) — using defaults.",
            file=sys.stderr,
        )
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

    if not cfg.transcription_language:
        cfg.transcription_language = dictation_language.default_setting(cfg.ui_language)

    errors = validate_config(cfg)
    for error in errors:
        logger.warning("Config validation: %s", error)

    return cfg


def _apply_toml(cfg: Config, data: dict) -> None:
    """Legt eine gelesene Datei über die bisherigen Werte."""
    _audio_anwenden(cfg, data.get("audio", {}))
    _modell_anwenden(cfg, data.get("model", {}))
    _transkription_anwenden(cfg, data.get("transcription", {}))
    hotkeys = data.get("hotkeys", {})
    cfg.hotkey_transcript = str(hotkeys.get("transcript", cfg.hotkey_transcript))
    cfg.hotkey_prompt = str(hotkeys.get("prompt", cfg.hotkey_prompt))
    _ollama_anwenden(cfg, data.get("ollama", {}))
    clipboard = data.get("clipboard", {})
    cfg.clipboard_paste_timeout = float(clipboard.get("paste_timeout", cfg.clipboard_paste_timeout))

    ui = data.get("ui", {})
    cfg.sound_feedback = bool(ui.get("sound_feedback", cfg.sound_feedback))
    if "window_x" in ui:
        cfg.window_x = _clean_coordinate(ui.get("window_x"))
    if "window_y" in ui:
        cfg.window_y = _clean_coordinate(ui.get("window_y"))
    # `normalise` statt `str(...)`: der Schluessel fehlt in jeder bestehenden
    # Nutzerdatei, und ein unbekannter Wert soll hell ergeben, keinen Fehler.
    cfg.appearance = Theme.normalise(ui.get("appearance", cfg.appearance))
    cfg.ui_language = _clean_language(ui.get("language", cfg.ui_language))
    cfg.update_check = bool(data.get("updates", {}).get("check", cfg.update_check))


def _clean_language(value: Any) -> str:
    """Ein gueltiger Wert fuer `ui.language`; ein unbekannter wird gemeldet und gilt als "auto"."""
    setting = i18n.normalise_setting(value)
    if setting != str(value).strip().lower():
        logger.warning("Config: ui.language should be one of %s, got %r - using %s", i18n.SETTINGS, value, setting)
    return setting


def _clean_dictation_language(value: Any, previous: str) -> str:
    """Ein gueltiger Wert fuer `transcription.language`; ein unbekannter wird gemeldet und uebergangen."""
    setting = dictation_language.normalise_setting(value)
    if setting is None:
        logger.warning(
            "Config: transcription.language should be one of %s, got %r - ignored", dictation_language.SETTINGS, value
        )
        return previous
    return setting


def _audio_anwenden(cfg: Config, audio: dict) -> None:
    cfg.sample_rate = int(audio.get("sample_rate", cfg.sample_rate))
    cfg.channels = int(audio.get("channels", cfg.channels))
    cfg.mic_device_raw = audio.get("device")
    cfg.mic_device = _resolve_mic_device(cfg.mic_device_raw)
    cfg.max_recording_seconds = int(audio.get("max_recording_seconds", cfg.max_recording_seconds))


def _modell_anwenden(cfg: Config, model: dict) -> None:
    cfg.model_size = str(model.get("size", cfg.model_size))
    device_priority = model.get("device_priority")
    if isinstance(device_priority, list) and device_priority:
        cfg.model_device_priority = tuple(str(d) for d in device_priority)
    compute_priority = model.get("compute_priority", {})
    if isinstance(compute_priority, dict) and compute_priority:
        cfg.model_compute_priority = {
            str(k): tuple(str(x) for x in v) for k, v in compute_priority.items() if isinstance(v, list) and v
        }


def _transkription_anwenden(cfg: Config, transcription: dict) -> None:
    if "language" in transcription:
        cfg.transcription_language = _clean_dictation_language(transcription["language"], cfg.transcription_language)
    cfg.transcription_vad_filter = bool(transcription.get("vad_filter", cfg.transcription_vad_filter))
    cfg.transcription_word_timestamps = bool(transcription.get("word_timestamps", cfg.transcription_word_timestamps))
    cfg.transcription_beam_size = int(transcription.get("beam_size", cfg.transcription_beam_size))
    cfg.transcription_condition_on_previous_text = bool(
        transcription.get("condition_on_previous_text", cfg.transcription_condition_on_previous_text)
    )
    cfg.transcription_without_timestamps = bool(
        transcription.get("without_timestamps", cfg.transcription_without_timestamps)
    )


def _ollama_anwenden(cfg: Config, ollama: dict) -> None:
    url = ipv4_loopback(str(ollama.get("url", cfg.ollama_url)))
    problem = ollama_url_problem(url)
    if problem:
        # Nur melden reicht nicht: die Adresse bekaeme trotzdem jedes Diktat.
        logger.warning("Config: %s - using %s instead", problem, cfg.ollama_url)
    else:
        cfg.ollama_url = url
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
            print(
                f"[{branding.PRODUCT_NAME}] could not load the cleanup prompt ({exc}) — using the default.",
                file=sys.stderr,
            )


def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("hams")
    log.setLevel(logging.DEBUG)
    if log.handlers:
        return log
    file_handler = RotatingFileHandler(
        LOG_DIR / "hams.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    log.addHandler(file_handler)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
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
