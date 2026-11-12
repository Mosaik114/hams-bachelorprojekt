# CLAUDE.md – Hams

Context for AI coding assistants working in this repository.

Press a hotkey, speak, and the corrected text appears at the cursor. faster-whisper transcribes, Ollama cleans up,
and the text is pasted via the clipboard and Ctrl+V. On Linux, a `uinput` keyboard does the pasting. The UI is
Tkinter with its own design system (`wisper/ui_kit`).

- **Entry point:** `wisper/main.py` → `wisper/app.py` (`FloatingTranscriberApp`, composed of the mixins
  `main_window`, `tray`, `whisper_model`, `recording`, `transcription`). Settings: `wisper/config.py` (+
  `config_toml.py`), window `settings_window.py` with its groups in `settings_sections.py`.
- **Tests:** `pytest tests` (about 1,600). Tk tests open real windows. Run them headless: under `xvfb-run` on Linux,
  or on a separate desktop on Windows.
- **Lint:** `ruff check . && ruff format --check .` (rules in `ruff.toml`, max complexity 10).
- **Never restart the running app unasked.** The author dictates with it.
- Identifiers stay in English; comments and UI strings may be German.
- Some tests read source code with `inspect.getsource` (order of the output paths). Update those search strings when
  refactoring.
- Keep the Windows parts (`start_wisper.vbs`, `system_hotkeys.py`, `activation.py`) working. Platform switches use
  `sys.platform`.
- Never log dictated text, only its length.
