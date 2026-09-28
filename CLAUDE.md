# CLAUDE.md – Hams (Diktat)

Taste drücken, sprechen, Text erscheint korrigiert an der Schreibmarke: Whisper (faster-whisper) transkribiert, Ollama
bereinigt, eine uinput-Tastatur fügt mit Strg+V ein. Tkinter-Fenster im Mosaik-Design (`wisper/ui_kit`).

- **Einstieg:** `wisper/main.py` → `wisper/app.py` (`FloatingTranscriberApp`, erbt die Mixins `main_window`, `tray`,
  `whisper_model`, `recording`, `transcription`). Einstellungen: `wisper/config.py` (+ `config_toml.py`), Fenster
  `settings_window.py` mit den Gruppen in `settings_sections.py`.
- **Tests:** `cd Hams && ../werkzeuge/unsichtbar.sh .venv/bin/python -m pytest -q -p no:cacheprovider tests`
  (Tk braucht eine Anzeige – nur in der Test-Sitzung, nie auf dem echten Bildschirm).
- **Nie ungefragt neu starten** – der Nutzer diktiert damit. Das laufende Hams nutzt neuen Code erst nach dem Neustart.
- **Namen bleiben englisch** (Windows-Herkunft); neue Kommentare dürfen deutsch sein.
- Tests lesen teils Quelltext mit `inspect.getsource` (Reihenfolge der Ausgabewege) – beim Umbau Suchtexte mitziehen.
- Windows-Teile (`start_wisper.vbs`, `system_hotkeys.py`) bleiben lauffähig; Linux-Weichen über `sys.platform`.
