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
  ollama pull qwen2.5:7b       # Cleanup- und Prompt-Modell (Standard)
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

Die Voreinstellungen liegen in [wisper/config.default.toml](wisper/config.default.toml). Eigene Einstellungen speichert Hams in `%APPDATA%\Wisper\config.toml`; diese Datei wird beim ersten Ändern in den Einstellungen angelegt. Die wichtigsten Hebel:

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

- **Modell lädt nicht** → GPU-Speicher prüfen, ggf. `MODEL_SIZE` auf `medium` oder `small` stellen.
- **Ollama-Fehler** → Ollama-Service läuft? `ollama list` zeigt die installierten Modelle.
- **Hotkey funktioniert nicht** → Andere Anwendung blockiert ihn möglicherweise. `HOTKEY_*` in `main.py` anpassen.
- **Kein Einfügen** → Zielanwendung muss `Ctrl+V` akzeptieren; Transkript ist trotzdem im Clipboard und unter `transcripts/`.

## Entwicklung

### Tests ausführen

```powershell
pip install pytest
pytest tests/ -v
```

### Code linten

```powershell
pip install ruff
ruff check wisper/
```

### Projekt-Struktur

```
wisper/
├── __init__.py
├── main.py              # Hauptanwendung
├── setup_autostart.py   # Windows Autostart
├── config.default.toml  # Voreinstellungen
├── config_validation.py # Config-Validierung
└── prompts/
    └── cleanup.txt      # Cleanup-Prompt
tests/
├── conftest.py
├── test_config_validation.py
├── test_config_load.py
└── test_cleanup.py
```

## Lizenz

MIT License — see [LICENSE](LICENSE)
