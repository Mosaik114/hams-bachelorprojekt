"""Settings: paths, the values from the TOML files, writing a single value back.

Importing this module defines things and does nothing else: no log file, no file read or written, no device query,
no colour scheme. `CFG` starts with the built-in defaults; `hams.bootstrap` sets up the log and calls `reload()`,
which reads the template and the user file into it (never raising: wrong types and ranges are reported and skipped,
an unreadable user file is backed up as `config.toml.broken-<time>`, left alone and noted in `BROKEN_CONFIG`).
`ensure_user_config()` creates or migrates the user file; `bootstrap.start` calls it once.
"""

import contextlib
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tomlkit.exceptions import TOMLKitError

from hams import app_paths, branding, dictation_language, i18n
from hams import models as whisper_models
from hams.config_toml import (
    TomlEncodingError,
    _set_toml_value,
    load_toml_file,
    read_toml_text,
    verify_toml_value,
)
from hams.config_validation import (
    MAX_CLEANUP_TEMPERATURE,
    MAX_KEEP_DAYS,
    MAX_OLLAMA_TIMEOUT,
    MAX_PASTE_TIMEOUT,
    MAX_RECORDING_SECONDS,
    SectionReader,
    clean_coordinate,
    ollama_url_problem,
)

logger = logging.getLogger("hams")

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


#: The colour schemes (`[ui] appearance`), the same names as `ui_kit.Theme.MODES`. Without an entry Hams is light:
#: deliberately not the Windows theme, the user chooses.
APPEARANCES = ("light", "dark")
DEFAULT_APPEARANCE = "light"


def normalise_appearance(value: Any) -> str:
    """A known colour scheme; anything else (an old or mistyped file) is light, never a colourless window."""
    text = str(value).strip().lower() if value is not None else ""
    return text if text in APPEARANCES else DEFAULT_APPEARANCE


@dataclass
class Config:
    mic_device: str | int | None = None
    # Roher Config-Wert (z.B. "Shure MV7+"), damit das Gerät pro Aufnahme frisch
    # aufgelöst werden kann — Indizes verschieben sich beim Aus-/Einstecken.
    mic_device_raw: Any = None
    max_recording_seconds: int = 300

    model_size: str = "turbo"
    #: Devices tried in this order; `["cpu"]` keeps the GPU free. Compute types per device: whisper_model.py.
    model_device_priority: tuple[str, ...] = ("cuda", "cpu")

    #: Diktiersprache, siehe `dictation_language.SETTINGS`. Leer heisst: kein Eintrag in Vorlage oder
    #: Nutzerdatei — `_load_config` setzt dann die Sprache der Oberflaeche ein. Bestehende Dateien
    #: tragen "de" und behalten es.
    transcription_language: str = ""
    transcription_vad_filter: bool = True

    hotkey_transcript: str = "ctrl+shift+space"
    hotkey_prompt: str = "ctrl+shift+alt+space"

    #: Bewusst die Zahlenadresse statt `localhost`: siehe `ipv4_loopback`.
    ollama_url: str = "http://127.0.0.1:11434/api/generate"
    ollama_model_cleanup: str = "qwen3.5:9b"
    ollama_model_prompt: str = "qwen3.5:9b"
    ollama_timeout_cleanup: int = 30
    ollama_timeout_prompt: int = 60
    #: Sampling-Temperatur der Bereinigung. 0 = deterministisch (gemessen besser, docs/evaluation.md).
    ollama_cleanup_temperature: float = 0.0
    ollama_cleanup_enabled: bool = True
    ollama_keep_alive: str = "30m"
    #: Zuletzt bekannte Lage des Hauptfensters in physischen Bildschirmpixeln.
    #: None heisst: noch nie gemerkt, es gilt der Rueckfall.
    window_x: int | None = None
    window_y: int | None = None
    #: Ob der einmalige Hinweis "laeuft im Tray weiter" schon kam (schreibt Hams selbst, `[ui] tray_hint_shown`).
    tray_hint_shown: bool = False
    cleanup_prompt_template: str = DEFAULT_CLEANUP_PROMPT

    clipboard_paste_timeout: float = 0.3
    #: Every pasted text as a file in `OUTPUT_DIR` (`[output] save_transcripts`). Off in new configurations; a
    #: file from before the option keeps saving (see `LEGACY_OUTPUT`).
    save_transcripts: bool = False
    #: Saved transcripts older than this many days are deleted at start and after each save; 0 keeps them forever.
    transcripts_keep_days: int = 30
    sound_feedback: bool = True
    #: Darstellungsmodus, "light" oder "dark". Ohne Eintrag in der Nutzerdatei
    #: gilt hell — bewusst nicht das Windows-Systemtheme, der Nutzer waehlt
    #: selbst. Ein unbekannter Wert faellt in `normalise_appearance()` auf hell
    #: zurueck, damit eine alte oder vertippte Datei nie ein farbloses Fenster
    #: ergibt.
    appearance: str = DEFAULT_APPEARANCE
    #: Sprache der Oberflaeche: "auto" (Systemsprache), "de" oder "en". Gilt
    #: ab dem Start; `main` aktiviert sie ueber `i18n.set_language`.
    ui_language: str = i18n.DEFAULT_SETTING
    #: Einmal am Tag beim Start bei GitHub nach einer neuen Version fragen (siehe updates.py).
    update_check: bool = True
    #: Optional: report recording state and level to an external overlay (`[integrations] cursor_dot`, see
    #: cursor_dot.py). Off: nothing is sent.
    integrations_cursor_dot: bool = False


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

    Gesetzt wird mit tomlkit (config_toml.py). Fehlt der Schlüssel, wird er am Ende seines Abschnitts ergänzt; fehlt der
    Abschnitt, wird er am Dateiende angelegt. Kommentare, Leerzeilen,
    Reihenfolge und Zeilenenden bleiben erhalten.

    Geschrieben wird atomar über eine temporäre Datei — ein Abbruch mitten im
    Schreiben darf die Konfiguration nie halbiert zurücklassen.
    """
    if not CONFIG_PATH.exists():
        ensure_user_config()
    try:
        text = read_toml_text(CONFIG_PATH)
    except OSError:
        logger.warning("Config not written: %s unreadable", CONFIG_PATH)
        return False
    except TomlEncodingError as exc:
        # Nie in eine Datei schreiben, die nicht UTF-8 ist: das Beenden und Verstecken haengt daran.
        logger.error("Config not saved (%s.%s): %s", section, key, exc)
        return False
    try:
        new_text = _set_toml_value(text, section, key, value)
    except (ValueError, TypeError, KeyError, TOMLKitError) as exc:
        logger.error("Config not saved (%s.%s): %s - %s left unchanged", section, key, exc, CONFIG_PATH)
        return False
    problem = verify_toml_value(new_text, section, key, value)
    if problem:
        # Die alte Datei bleibt, wie sie ist: lieber diese eine Aenderung
        # verlieren als eine Datei, die beim naechsten Start alles verwirft.
        logger.error("Config not saved (%s.%s): %s - %s left unchanged", section, key, problem, CONFIG_PATH)
        return False
    return _atomic_write(new_text)


def _atomic_write(text: str) -> bool:
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
        logger.exception("Config not written")
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        return False


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
        logger.error("User data could not be copied from %s to %s", old_dir, new_dir, exc_info=True)
        shutil.rmtree(staging, ignore_errors=True)
        return False
    logger.info("User data copied from %s to %s (the old folder stays unchanged)", old_dir, new_dir)
    return True


#: Ergebnisse von `ensure_user_config`.
CONFIG_EXISTING = "existing"
CONFIG_MIGRATED = "migrated"
CONFIG_CREATED = "created"
CONFIG_FAILED = "failed"


def ensure_user_config() -> str:
    """Sorgt dafür, dass eine persönliche Konfiguration existiert.

    Genau einmal: existiert die Nutzerdatei bereits, wird nichts kopiert. Eine
    später geänderte Vorlage darf persönliche Werte nie wieder überschreiben.

    Gibt zurück, was geschehen ist: `CONFIG_EXISTING`, `CONFIG_MIGRATED`,
    `CONFIG_CREATED` oder `CONFIG_FAILED`.
    """
    if migrate_legacy_user_dir(CONFIG_PATH.parent, LEGACY_USER_DIR) and CONFIG_PATH.exists():
        return CONFIG_MIGRATED
    if CONFIG_PATH.exists():
        return CONFIG_EXISTING
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
            "User configuration %s could not be created - settings cannot be saved in this session",
            CONFIG_PATH,
            exc_info=True,
        )
        return CONFIG_FAILED
    if source is LEGACY_CONFIG_PATH:
        logger.info("Configuration taken over from %s to %s", LEGACY_CONFIG_PATH, CONFIG_PATH)
        return CONFIG_MIGRATED
    logger.info("New configuration created at %s", CONFIG_PATH)
    return CONFIG_CREATED


@dataclass(frozen=True)
class BrokenConfig:
    """Die Nutzerdatei liess sich beim letzten Laden nicht lesen."""

    path: Path
    #: Die Sicherungskopie, oder None, wenn auch das Kopieren scheiterte.
    backup: Path | None
    error: str


#: Gesetzt, wenn `_load_config` die Nutzerdatei nicht lesen konnte; `main` zeigt dann einen Hinweis.
#: Ueber `config.BROKEN_CONFIG` lesen, nicht importieren: jedes Laden setzt den Wert neu.
BROKEN_CONFIG: BrokenConfig | None = None


def _backup_broken_config(path: Path) -> Path | None:
    """Legt `config.toml.broken-<Zeitstempel>` neben die unlesbare Datei.

    Die Datei selbst bleibt unangetastet: Hams ueberschreibt sie nie, ein
    Speichern wird abgelehnt, solange sie kaputt ist (siehe
    `_persist_config_value`). Die Kopie ist fuer den Fall, dass der Nutzer
    sie beim Reparieren verschlimmert. Liegt schon eine inhaltsgleiche Kopie
    da, wird keine weitere angelegt – sonst kaeme bei jedem Start eine dazu.
    """
    try:
        content = path.read_bytes()
        for existing in sorted(path.parent.glob(path.name + ".broken-*")):
            if existing.read_bytes() == content:
                return existing
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for attempt in range(100):
            suffix = f"-{attempt}" if attempt else ""
            backup = path.with_name(f"{path.name}.broken-{stamp}{suffix}")
            try:
                with backup.open("xb") as handle:
                    handle.write(content)
            except FileExistsError:
                continue
            return backup
    except OSError:
        logger.error("Could not keep a copy of the unreadable config %s", path, exc_info=True)
    return None


def _note_broken_user_config(path: Path, exc: Exception) -> None:
    global BROKEN_CONFIG
    backup = _backup_broken_config(path)
    BROKEN_CONFIG = BrokenConfig(path, backup, str(exc))
    logger.error(
        "USER CONFIG UNREADABLE: %s (%s). All settings fall back to the defaults for this session; "
        "the file is left unchanged and nothing will be saved into it until it is fixed. Copy: %s",
        path,
        exc,
        backup,
    )


def _read_toml(path: Path) -> dict:
    """Eine TOML-Datei als Wörterbuch. Fehlt oder bricht sie, bleibt es leer."""
    if not path.exists():
        return {}
    try:
        return load_toml_file(path)
    except (OSError, ValueError) as exc:  # TOMLDecodeError und TomlEncodingError sind ValueErrors
        print(
            f"[{branding.PRODUCT_NAME}] could not load {path.name} ({exc}) — using defaults.",
            file=sys.stderr,
        )
        if path == CONFIG_PATH:
            _note_broken_user_config(path, exc)
        else:
            logger.error("Could not read %s (%s)", path, exc)
        return {}


def _load_config() -> Config:
    """Voreinstellungen aus dem Code, dann aus der Vorlage, dann vom Nutzer.

    Drei Ebenen in dieser Reihenfolge. Deshalb muss eine ältere Nutzerdatei
    keinen später hinzugekommenen Schlüssel kennen: was dort fehlt, kommt aus
    der Vorlage, und was auch dort fehlt, steht im Datenmodell.

    Wirft nie: das Laden laeuft beim Import von `hams.config`, und eine
    Ausnahme dort hiesse, dass die fensterlose Anwendung gar nicht startet.
    """
    global BROKEN_CONFIG
    BROKEN_CONFIG = None
    try:
        return _load_layers()
    except Exception:
        logger.exception("Config could not be loaded - using the built-in defaults")
        cfg = Config()
        with contextlib.suppress(Exception):
            cfg.transcription_language = dictation_language.default_setting(cfg.ui_language)
        return cfg


def _load_layers() -> Config:
    cfg = Config()
    for path in (DEFAULT_CONFIG_PATH, CONFIG_PATH):
        data = _read_toml(path)
        if data:
            _apply_toml(cfg, data)
            _note_retired_keys(data, path)
        if path == CONFIG_PATH and data:
            _keep_legacy_output(cfg, data)

    if not cfg.transcription_language:
        cfg.transcription_language = dictation_language.default_setting(cfg.ui_language)
    return cfg


#: Keys older versions read and that are now fixed in the code, with the value the code uses. Every user file
#: started as a copy of the template, so most still carry them: such a key is ignored, and only a value that
#: differs from the fixed one is reported (once per start), because only then does the file ask for something
#: Hams no longer does.
RETIRED_KEYS: dict[tuple[str, str], Any] = {
    # Whisper takes 16 kHz mono and nothing converted other rates: 48 kHz stereo came out as gibberish.
    ("audio", "sample_rate"): 16_000,
    ("audio", "channels"): 1,
    # Whisper and Ollama internals nobody needs to change; the values are measured (docs/evaluation.md) and now
    # live next to the code that uses them (whisper_model.COMPUTE_PRIORITY, transcription.WHISPER_OPTIONS).
    ("model", "compute_priority"): {"cuda": ["float16", "int8_float16", "int8"], "cpu": ["int8", "float32"]},
    ("transcription", "word_timestamps"): False,
    ("transcription", "beam_size"): 1,
    ("transcription", "condition_on_previous_text"): False,
    ("transcription", "without_timestamps"): True,
    ("ollama", "stream_prompt"): True,
    ("ollama", "stream"): True,
    ("ollama", "warmup_on_start"): True,
}


def _note_retired_keys(data: dict, path: Path) -> None:
    """One warning for retired keys in `data` whose value differs from the one the code uses."""
    differing = []
    for (section, key), fixed in RETIRED_KEYS.items():
        table = data.get(section) if isinstance(data, dict) else None
        if isinstance(table, dict) and key in table and table[key] != fixed:
            differing.append(f"{section}.{key} = {table[key]!r} (fixed: {fixed!r})")
    if differing:
        logger.warning("Config %s: no longer settings, ignored: %s", path.name, "; ".join(differing))


#: `[output]` keys a user file from before the transcript option lacks, with the behaviour it had then: every
#: text was saved and kept forever. New files are copies of the template and carry both keys (saving off).
LEGACY_OUTPUT = {"save_transcripts": True, "keep_days": 0}


def _keep_legacy_output(cfg: Config, data: dict) -> None:
    """An existing user file without the `[output]` keys keeps saving transcripts as before."""
    output = data.get("output")
    present = output if isinstance(output, dict) else {}
    if "save_transcripts" not in present:
        cfg.save_transcripts = LEGACY_OUTPUT["save_transcripts"]
    if "keep_days" not in present:
        cfg.transcripts_keep_days = LEGACY_OUTPUT["keep_days"]


def _warn_config(message: str) -> None:
    logger.warning("Config: %s", message)


def _apply_toml(cfg: Config, data: dict) -> None:
    """Legt eine gelesene Datei über die bisherigen Werte.

    Jeder Wert wird typgeprueft gelesen (`SectionReader`): ein falscher Typ
    oder ein Wert ausserhalb des Bereichs wird gemeldet und uebergangen, nie
    geraten und nie mit einer Ausnahme quittiert.
    """
    if not isinstance(data, dict):
        _warn_config(f"the file should contain tables, got {type(data).__name__} - ignored")
        return
    for section, apply in _SECTION_APPLIERS:
        apply(cfg, SectionReader(data, section, _warn_config))


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


def _clean_device(audio: SectionReader) -> Any:
    """`audio.device`: None, ein Geraeteindex oder ein Namensteil. Alles andere gilt als Standardgeraet."""
    raw = audio.raw("device")
    if raw is None or (isinstance(raw, (int, str)) and not isinstance(raw, bool)):
        return raw
    return audio.reject("device", '"default", a device number or part of its name', raw, None)


def _apply_audio(cfg: Config, audio: SectionReader) -> None:
    cfg.mic_device_raw = _clean_device(audio)
    cfg.max_recording_seconds = audio.integer(
        "max_recording_seconds", cfg.max_recording_seconds, 1, MAX_RECORDING_SECONDS
    )


def _text_list(value: Any) -> tuple[str, ...] | None:
    """Eine nicht leere Liste aus Texten als Tupel, sonst None."""
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return tuple(value)
    return None


def _apply_model(cfg: Config, model: SectionReader) -> None:
    if "size" in model:
        cfg.model_size = model.text("size", cfg.model_size)
        if not whisper_models.find(cfg.model_size):
            # Allowed on purpose (another faster-whisper size or a model folder), but a typo would only show up
            # as a failed load.
            logger.warning("Config: model.size %r is not one of Hams' models - it is loaded as given", cfg.model_size)
    if "device_priority" in model:
        devices = _text_list(model.raw("device_priority"))
        if devices is None:
            devices = model.reject("device_priority", "a list of texts", model.raw("device_priority"), None)
        cfg.model_device_priority = devices or cfg.model_device_priority


def _apply_transcription(cfg: Config, transcription: SectionReader) -> None:
    if "language" in transcription:
        cfg.transcription_language = _clean_dictation_language(
            transcription.raw("language"), cfg.transcription_language
        )
    cfg.transcription_vad_filter = transcription.boolean("vad_filter", cfg.transcription_vad_filter)


def _apply_hotkeys(cfg: Config, hotkeys: SectionReader) -> None:
    cfg.hotkey_transcript = hotkeys.text("transcript", cfg.hotkey_transcript)
    cfg.hotkey_prompt = hotkeys.text("prompt", cfg.hotkey_prompt)


def _apply_ollama(cfg: Config, ollama: SectionReader) -> None:
    url = ipv4_loopback(ollama.text("url", cfg.ollama_url))
    problem = ollama_url_problem(url)
    if problem:
        # Nur melden reicht nicht: die Adresse bekaeme trotzdem jedes Diktat.
        logger.warning("Config: %s - using %s instead", problem, cfg.ollama_url)
    else:
        cfg.ollama_url = url
    cfg.ollama_model_cleanup = ollama.text("model_transcript_cleanup", cfg.ollama_model_cleanup)
    cfg.ollama_model_prompt = ollama.text("model_prompt_generate", cfg.ollama_model_prompt)
    cfg.ollama_timeout_cleanup = ollama.integer("timeout_cleanup", cfg.ollama_timeout_cleanup, 1, MAX_OLLAMA_TIMEOUT)
    cfg.ollama_timeout_prompt = ollama.integer("timeout_prompt", cfg.ollama_timeout_prompt, 1, MAX_OLLAMA_TIMEOUT)
    cfg.ollama_cleanup_temperature = ollama.number(
        "cleanup_temperature", cfg.ollama_cleanup_temperature, 0.0, MAX_CLEANUP_TEMPERATURE
    )
    cfg.ollama_cleanup_enabled = ollama.boolean("cleanup_enabled", cfg.ollama_cleanup_enabled)
    cfg.ollama_keep_alive = _clean_keep_alive(ollama, cfg.ollama_keep_alive)
    _load_cleanup_prompt(cfg, ollama.text("cleanup_prompt_file", ""))


def _clean_keep_alive(ollama: SectionReader, current: str) -> str:
    """Ollama versteht "30m" ebenso wie eine Zahl in Sekunden (-1 = unbegrenzt)."""
    value = ollama.raw("keep_alive", current)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ollama.text("keep_alive", current)


def _load_cleanup_prompt(cfg: Config, prompt_file_rel: str) -> None:
    if not prompt_file_rel.strip():
        return
    prompt_path = (APP_DIR / prompt_file_rel).resolve()
    try:
        cfg.cleanup_prompt_template = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(
            f"[{branding.PRODUCT_NAME}] could not load the cleanup prompt ({exc}) — using the default.",
            file=sys.stderr,
        )


def _apply_clipboard(cfg: Config, clipboard: SectionReader) -> None:
    cfg.clipboard_paste_timeout = clipboard.number("paste_timeout", cfg.clipboard_paste_timeout, 0.0, MAX_PASTE_TIMEOUT)


def _apply_output(cfg: Config, output: SectionReader) -> None:
    cfg.save_transcripts = output.boolean("save_transcripts", cfg.save_transcripts)
    cfg.transcripts_keep_days = output.integer("keep_days", cfg.transcripts_keep_days, 0, MAX_KEEP_DAYS)


def _apply_ui(cfg: Config, ui: SectionReader) -> None:
    cfg.sound_feedback = ui.boolean("sound_feedback", cfg.sound_feedback)
    if "window_x" in ui:
        cfg.window_x = clean_coordinate(ui.raw("window_x"))
    if "window_y" in ui:
        cfg.window_y = clean_coordinate(ui.raw("window_y"))
    cfg.tray_hint_shown = ui.boolean("tray_hint_shown", cfg.tray_hint_shown)
    # `normalise` statt `str(...)`: der Schluessel fehlt in jeder bestehenden
    # Nutzerdatei, und ein unbekannter Wert soll hell ergeben, keinen Fehler.
    cfg.appearance = normalise_appearance(ui.raw("appearance", cfg.appearance))
    cfg.ui_language = _clean_language(ui.raw("language", cfg.ui_language))


def _apply_updates(cfg: Config, updates: SectionReader) -> None:
    # Datenschutz: "false" in Anfuehrungszeichen schaltet ab, und ein unklarer Wert
    # schaltet ebenfalls ab - im Zweifel fragt Hams nicht bei GitHub nach.
    cfg.update_check = updates.boolean("check", cfg.update_check, invalid=False)


def _apply_integrations(cfg: Config, integrations: SectionReader) -> None:
    cfg.integrations_cursor_dot = integrations.boolean("cursor_dot", cfg.integrations_cursor_dot, invalid=False)


#: Abschnitt und Leser, in der Reihenfolge der Vorlage.
_SECTION_APPLIERS = (
    ("audio", _apply_audio),
    ("model", _apply_model),
    ("transcription", _apply_transcription),
    ("hotkeys", _apply_hotkeys),
    ("ollama", _apply_ollama),
    ("clipboard", _apply_clipboard),
    ("output", _apply_output),
    ("ui", _apply_ui),
    ("updates", _apply_updates),
    ("integrations", _apply_integrations),
)


#: Defaults only: `reload()` (called by `bootstrap`) fills in the template and the user file. Every module imports
#: this one object, so it is updated in place, never replaced.
CFG = Config()


def reload() -> Config:
    """Reads the template and the user file into `CFG`. Never raises (see `_load_config`)."""
    vars(CFG).update(vars(_load_config()))
    return CFG
