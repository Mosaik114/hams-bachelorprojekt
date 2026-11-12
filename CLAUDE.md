# CLAUDE.md – Hams

Context for AI coding assistants working in this repository.

Press a hotkey, speak, and the corrected text appears at the cursor. faster-whisper transcribes, Ollama cleans up,
and the text is pasted via the clipboard and Ctrl+V. On Linux, a `uinput` keyboard does the pasting. The UI is
Tkinter with its own design system (`hams/ui_kit`).

- **Entry point:** `hams/main.py` → `hams/app.py` (`FloatingTranscriberApp`, composed of the mixins
  `main_window`, `tray`, `whisper_model`, `recording`, `transcription`). Settings: `hams/config.py` (+
  `config_toml.py`), window `settings_window.py` with its groups in `settings_sections.py`.
- **Tests:** `pytest tests` (about 1,600). Tk tests open real windows. Run them headless: under `xvfb-run` on Linux,
  or on a separate desktop on Windows.
- **Lint:** `ruff check . && ruff format --check .` (rules in `ruff.toml`, max complexity 10).
- **Never restart the running app unasked.** The author dictates with it.
- Identifiers stay in English; comments and UI strings may be German.
- Some tests read source code with `inspect.getsource` (order of the output paths). Update those search strings when
  refactoring.
- Keep the Windows parts (`scripts/start_hams.vbs`, `system_hotkeys.py`, `activation.py`) working. Platform
  switches use `sys.platform`.
- User data lives in `%APPDATA%\Hams` / `~/.config/hams`; tests redirect it with `HAMS_CONFIG` and `HAMS_LOG_DIR`
  (see `tests/conftest.py`) and must never touch the real folders, including the old `Wisper` one.
- Never log dictated text, only its length.
