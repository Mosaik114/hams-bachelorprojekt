# CLAUDE.md – Hams

Context for AI coding assistants working in this repository.

Press a hotkey, speak, and the corrected text appears at the cursor. faster-whisper transcribes, Ollama cleans up,
and the text is pasted via the clipboard and Ctrl+V. On Linux, a `uinput` keyboard does the pasting. The UI is
Tkinter with its own design system (`hams/ui_kit`). The kit is frozen: build from its existing controls, add no
new ones; `hams.ui_kit` exports only what the app uses (tests take internals from `tests/kit.py`).

- **Entry point:** `hams/main.py` → `hams/bootstrap.py` (log, user file, settings, theme) → `hams/app.py`
  (`FloatingTranscriberApp`: main window and tray, the two remaining mixins). The app owns plain objects without Tk:
  `Recorder` (`recording.py`), `ModelManager` (`whisper_model.py`), `Pipeline` (`transcription.py`), `OllamaClient`
  (`ollama.py`, the only place that talks HTTP to Ollama) and `UpdateNotice`. They report through
  `hams.status.StatusSink` and `whisper_model.ModelEvents`, which the app implements; windows reach them as
  `app.models`, `app.recorder`, `app.ollama`, `app.updates`. See `docs/architecture.md`.
- **Settings:** `hams/config.py` (+ `config_toml.py`) only defines things; importing it reads no file and queries no
  device. `bootstrap.load()`/`start()` fills the one `CFG` object in place. Window `settings_window.py` with its
  groups in `settings_sections.py`.
- **Tests:** `pytest tests` (≈1,800 test functions, ≈3,350 test cases including parametrisation). Tk tests open
  real windows. Run them headless: under `xvfb-run` on Linux, or on a separate desktop on Windows. Build core
  objects with the fakes in `tests/live_app.py` (`make_pipeline`, `ready_models`, `RecordingSink`, `FakeOllama`);
  never construct the app with `__new__` or add class defaults to production code for tests.
- **Lint:** `ruff check . && ruff format --check .` (rules in `ruff.toml`, max complexity 10).
- **Never restart the running app unasked.** The author dictates with it.
- Identifiers and log messages are English (enforced by `tests/test_english_identifiers.py`); comments may be German. UI strings go into both catalogs `hams/messages_en.py` and
  `hams/messages_de.py` and are looked up with `hams.i18n.t()`; the tests run with the German UI (`conftest.py`).
- Tests check behaviour, never source text: build the real objects with the fakes from `tests/live_app.py` and
  observe what they do. The only exceptions are project-wide AST rules (strings through the catalogs, colours through
  the theme, English identifiers; helper `tests/source_code.py`) and `test_hotkey_recovery.py`, which pins a property
  of the third-party `keyboard` library.
- Keep the Windows parts (`scripts/start_hams.vbs`, `system_hotkeys.py`, `activation.py`) working. Platform
  switches use `sys.platform`.
- User data lives in `%APPDATA%\Hams` / `~/.config/hams`; tests redirect it with `HAMS_CONFIG` and `HAMS_LOG_DIR`
  (see `tests/conftest.py`) and must never touch the real folders, including the old `Wisper` one.
- Never log dictated text, only its length.
