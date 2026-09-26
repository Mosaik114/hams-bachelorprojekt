# Änderungen

Alle wichtigen Änderungen an Hams. Aufbau nach
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach
[Semantic Versioning](https://semver.org/lang/de/).

## [Unveröffentlicht]

### Neu
- Feste Paketversionen mit Prüfsumme: `requirements.txt` und `requirements-dev.txt`, erzeugt mit `pip-compile --generate-hashes`
- CI prüft die Pakete mit `pip-audit` auf bekannte Sicherheitslücken
- Tests für die laufende Anwendung (Start, Diktat, Einstellungsfenster); über 1500 Tests, Abdeckung 91 %
- CI verlangt mindestens 85 % Testabdeckung
- Prüfung der Einstellungen (`wisper/config_validation.py`) mit Warnungen im Protokoll

### Geändert
- `main.py` und `ui_kit.py` in Module zerlegt (`app`, `recording`, `transcription`, `whisper_model`, `settings_*`, `ui_kit/…`)
- Pakete mit bekannten Lücken angehoben (u. a. Pillow 12.3, pygments 2.20)
- Bereinigungs-Prompt strenger gegen Überkorrektur
- Standardmodell für Bereinigung und Prompt: `ministral-3:8b`
- Protokoll: Datei ab DEBUG, Konsole ab WARNING

### Sicherheit
- Diktate gehen unverschlüsselt (`http://`) nur an den eigenen Rechner. Eine Ollama-Adresse auf einem anderen Rechner muss `https://` sein, sonst bleibt die Standardadresse und das Protokoll warnt.
- Im Protokoll stehen nur Textlängen, nie der diktierte Text.

## [0.1.0] – erste Version

### Neu
- Schwebendes Fenster, immer im Vordergrund
- Tray-Symbol mit Statusfarben
- Globale Kurzbefehle: Strg+Shift+Leer für Diktat, Strg+Shift+Alt+Leer für Prompt
- Aufnahme mit Pegelanzeige
- Faster-Whisper mit CUDA
- Ollama für Bereinigung und Prompt-Antworten
- Einfügen über die Zwischenablage
- Verlauf der Diktate im Tray-Menü
- Autostart-Skript
- Dokumentierte `config.toml`
- Signaltöne bei Start und Stopp
