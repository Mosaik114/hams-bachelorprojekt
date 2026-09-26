# Wisper

Always-on-top Sprach-zu-Text Tool für Windows mit zwei Betriebsmodi:

- **Transkription** — Aufnahme wird bereinigt und direkt an der Cursor-Position eingefügt
- **Prompt-Modus** — Aufnahme wird als Prompt an ein lokales LLM geschickt, die Antwort landet an der Cursor-Position

Die App läuft im System-Tray und reagiert auf globale Hotkeys.

## Features

- `faster-whisper` mit CUDA (Standard: `turbo`-Modell, Deutsch)
- Zwei globale Hotkeys für Transkript-Bereinigung und Prompt-Generierung
- Integration mit lokalem [Ollama](https://ollama.com) für Text-Cleanup und Prompt-Antworten
- Tray-Icon mit Status-Farbe, Always-on-top Mini-Fenster, akustisches Feedback
- Automatisches Clipboard-Copy + `Ctrl+V`-Einfügen
- Transkripte werden zusätzlich unter `transcripts/<zeitstempel>.txt` gespeichert

## Voraussetzungen

- Windows 10/11
- Python 3.10+
- NVIDIA-GPU mit CUDA (für Standard-Einstellungen)
- [Ollama](https://ollama.com/download) lokal installiert mit den konfigurierten Modellen:
  ```powershell
  ollama pull ministral-3:8b   # Cleanup- und Prompt-Modell (Standard)
  ```
  Das Cleanup-Modell läuft nach jeder Transkription. Für maximale Geschwindigkeit
  kannst du ein kleineres lokales Modell konfigurieren; für Prompt-Antworten lohnt
  sich dagegen meist ein stärkeres Modell.
- Ollama muss auf `http://localhost:11434` erreichbar sein (Standard)

## 1) Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[cuda]"
```

Wer genau die geprüften Versionen will (so installiert auch die CI):

```powershell
pip install --require-hashes -r requirements.txt
pip install --no-deps -e .
```

`requirements.txt` legt jede Paketversion samt Prüfsumme fest; ein
verändertes Paket lässt pip dann gar nicht erst ein. Erzeugt wird sie mit
`pip-compile --generate-hashes pyproject.toml` (für die Entwicklung zusätzlich
`--extra dev -o requirements-dev.txt`).

Das `[cuda]`-Extra installiert `nvidia-cublas-cu12` und `nvidia-cudnn-cu12`.
CTranslate2 — der Motor hinter `faster-whisper` — braucht beide für die GPU,
bringt sie aber nicht mit. Ohne sie läuft Wisper auf der CPU weiter.

**Eine eigene Umgebung lohnt sich messbar.** Liegt `torch` im selben
Interpreter, zieht `ctranslate2` es beim Start mit; das kostet 1,5 s pro Start
und 4337 MB auf der Platte, von denen nur die 1712 MB CUDA-Bibliotheken
gebraucht werden. Gemessener Unterschied:

| | mit torch daneben | eigene Umgebung |
|---|---|---|
| `site-packages` | 6620 MB | **2280 MB** |
| Import beim Start | 1,80 s | **0,29 s** |
| Speicher nach dem Import | 506 MB | **75 MB** |

## 2) Starten

```powershell
wisper
```

Beim ersten Start lädt `faster-whisper` das Modell herunter — je nach Verbindung einige Minuten.

## 3) Benutzung

Die App startet unsichtbar im System-Tray. Steuerung erfolgt primär über Hotkeys:

| Hotkey | Modus | Ablauf |
|--------|-------|--------|
| `Ctrl+Shift+Space` | Transkription | Aufnahme → Whisper → Text-Cleanup via LLM → Clipboard + Einfügen |
| `Ctrl+Shift+Alt+Space` | Prompt | Aufnahme → Whisper → Prompt an LLM → Antwort in Clipboard + Einfügen |

Zweiter Druck des gleichen Hotkeys stoppt die Aufnahme und startet die Verarbeitung.

Alternativ: Klick aufs Tray-Icon → Fenster anzeigen → Button verwenden.

## Konfiguration

Alle Einstellungen liegen in [wisper/config.toml](wisper/config.toml). Die wichtigsten Hebel:

| Abschnitt | Schlüssel | Zweck |
|-----------|-----------|-------|
| `[model]` | `size` | Whisper-Modellgröße (`turbo`, `large-v3`, `medium`, …) |
| `[model]` | `device_priority` | Reihenfolge der Geräte (`cuda` → `cpu`) |
| `[transcription]` | `beam_size` | `1` = schnell (greedy), `5` = höhere Qualität |
| `[hotkeys]` | `transcript` / `prompt` | Globale Hotkeys |
| `[ollama]` | `model_transcript_cleanup` | LLM fürs Cleanup |
| `[ollama]` | `cleanup_enabled` | `false` → Rohtext einfügen, maximale Geschwindigkeit |
| `[ollama]` | `keep_alive` | Wie lange das Modell nach Gebrauch im VRAM bleibt |
| `[ollama]` | `warmup_on_start` | Modell beim App-Start vorladen |
| `[audio]` | `device` | Mikrofon: `"default"`, Index oder Namens-Substring |
| `[audio]` | `max_recording_seconds` | Auto-Stop nach N Sekunden |

Den Cleanup-Prompt selbst kannst du in [wisper/prompts/cleanup.txt](wisper/prompts/cleanup.txt) anpassen.

### Performance-Tipps

- **Maximal schnell**: `cleanup_enabled = false` → Rohtext landet direkt im Ziel, kein Ollama-Rundtrip.
- **Schnell mit Cleanup**: kleines lokales Ollama-Modell, `keep_alive = "30m"`, `warmup_on_start = true`.
- **Höchste Qualität**: `beam_size = 5`, `cleanup_enabled = true`, `model.size = "large-v3"`.

## Autostart (optional)

Einrichten:
```powershell
python setup_autostart.py
```

Entfernen:
```powershell
python setup_autostart.py --remove
```

Der Autostart registriert die App in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
und startet sie beim Login über `start_wisper.vbs` ohne Konsolenfenster.

## Troubleshooting

- **Modell lädt nicht** → GPU-Speicher prüfen, ggf. unter `[model]` in der Konfiguration `size` auf `medium` oder `small` stellen.
- **Ollama-Fehler** → Ollama-Service läuft? `ollama list` zeigt die installierten Modelle.
- **Hotkey funktioniert nicht** → Andere Anwendung blockiert ihn möglicherweise. Unter `[hotkeys]` in der Konfiguration eine andere Kombination eintragen.
- **Kein Einfügen** → Zielanwendung muss `Ctrl+V` akzeptieren; Transkript ist trotzdem im Clipboard und unter `transcripts/`.

## Entwicklung

### Tests ausführen

```powershell
pip install --require-hashes -r requirements-dev.txt
pip install --no-deps -e .
pytest tests/ --cov=wisper
```

### Code linten

```powershell
ruff check wisper tests
```

### CI/CD

Das Projekt nutzt GitHub Actions für automatische Tests bei jedem Push. Die Pipeline ist in `.github/workflows/ci.yml` definiert und verlangt mindestens 85 % Testabdeckung. Danach prüft `pip-audit` die festgelegten Pakete auf bekannte Sicherheitslücken.

### Projekt-Struktur

```
wisper/
├── main.py                # Startpunkt: Konfiguration, eine Instanz, Fenster
├── app.py                 # Hauptfenster und seine Zustände
├── recording.py           # Aufnahme vom Mikrofon bis zur Tonspur
├── transcription.py       # Whisper, Bereinigung mit Ollama, Einfügen
├── whisper_model.py       # Modell laden, wechseln, herunterladen
├── models.py              # Katalog der Whisper-Modelle, Installationsstand
├── ollama.py              # Auskunft über den lokalen Ollama-Dienst
├── settings_window.py     # Einstellungsfenster: Aufbau und Schalter
├── settings_status.py     #   … Mikrofon, Live-Pegel, Ollama
├── settings_models.py     #   … Whisper-Modelle, Download, Löschen
├── system_hotkeys.py      # Kurzbefehle über RegisterHotKey
├── tray.py                # Tray-Symbol, Meldung an die Mosaiks-Suche
├── window_position.py     # Fensterlage prüfen und zurückholen
├── instance.py            # Nur eine Instanz, Selbstneustart
├── config.py              # Pfade, TOML-Einstellungen, Log
├── config_validation.py   # Prüfung der Einstellungen
├── config.default.toml    # Vorlage der Konfiguration
├── backend.py, branding.py
├── setup_autostart.py     # Windows-Autostart
├── prompts/cleanup.txt    # Bereinigungs-Prompt
└── ui_kit/                # Mosaik-Design für tkinter
    ├── theme.py           #   Farben und Maße
    ├── scale.py           #   DPI und Schriftrollen
    ├── buttons.py, controls.py, popover.py, layout.py
    └── …                  #   Flächen, Icons, Zustände, Fensterecken
tests/
├── conftest.py            # Gemeinsame Fixtures
├── live_app.py            # Attrappen für die echte Anwendung
└── test_*.py
```

## Lizenz

MIT License — see [LICENSE](LICENSE)
