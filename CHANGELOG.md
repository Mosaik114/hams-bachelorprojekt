# Changelog

All notable changes to Hams. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **Renamed from the working title *Wisper* to Hams everywhere.** The Python package is now `hams` (`python -m hams.main`,
  `hams/setup_autostart.py`); the `wisper` command alias is gone, use `hams`.
- **User data moved** to `%APPDATA%\Hams` (Windows) and `~/.config/hams` (Linux). On first start, an existing
  `Wisper` folder is copied there once; the old folder is never moved or deleted.
- The single-instance lock is now named after Hams. Hams also holds the old `Wisper` lock, so an older installation
  and Hams never run at the same time.
- The Windows autostart entry is now called `Hams`; setting it up replaces an old `Wisper` entry, `--remove` deletes
  both. The log file is now `hams.log`; the test overrides are `HAMS_CONFIG` and `HAMS_LOG_DIR`.
- Helper scripts moved and got English names: `scripts/start_hams.vbs`, `scripts/build_launcher.cmd`,
  `scripts/launch_hams.cs`, `linux/setup-system.sh`, `linux/show-hams.sh`, `tools/wer_benchmark.py`,
  `coverage-windows.ini`. Re-run `hams/setup_autostart.py` so autostart points to the new start script.
- **Logs and transcripts moved out of the package folder** to `%LOCALAPPDATA%\Hams` (Linux: `~/.local/share/hams`),
  overridable with `HAMS_DATA_DIR`; `HAMS_LOG_DIR` still wins for the log. Existing files in `hams/logs` and
  `hams/transcripts` are not moved. The `hams` command (now `hams.cli:run`) accepts the new options listed under Added.
- The documented default Ollama model is `qwen3.5:9b` everywhere; the evaluation states that it measured
  `qwen2.5:7b`.
- The template `config.default.toml` no longer sets `[transcription] language`, so a new configuration follows the
  interface language. `tools/wer_benchmark.py` always measures with `"de"` (its corpus is German).
- The German prompt texts moved to `hams/dictation_language.py`; `config.DEFAULT_CLEANUP_PROMPT`,
  `glossary.HOTWORD_FRAME` and `transcription.PROMPT_PLAIN_SYSTEM` still name the German versions.

### Added
- **English interface.** Every visible text (main window, settings, tray menu, notifications, error messages,
  keycaps) now comes from a message catalog in English and German. The new option `[ui] language` picks it:
  `"auto"` (default) follows the operating system's display language, German on a German system and English
  otherwise; `"en"` and `"de"` force a language. It can be changed under *General → Language* in the settings and
  applies after a restart. It is independent of the dictation language (`[transcription] language`).
- **Dictation language.** `[transcription] language` accepts `"de"`, `"en"`, `"fr"`, `"es"`, `"it"`, `"nl"`, `"pl"`,
  `"pt"` and `"auto"` (Whisper detects the language of each recording first). It can be changed under
  *Transcription → Dictation language* and applies from the next recording, without a restart. Each language gets
  its own Whisper hint sentence for the glossary; the Ollama instructions (cleanup, glossary note, prompt mode) are
  English for every language except German and name the language. German dictation is unchanged word for word.
  New configurations without the key follow the interface language; existing ones keep their value.
- English glossary template `glossar.default.en.toml` for new users who don't dictate German (no German spellings
  such as *Pull-Request*); the glossary also accepts the keys `words` and `replace`.
- **Windows installer** `Hams-Setup-<version>.exe`: per-user install without admin rights or Python, Start menu
  entry, optional autostart, uninstaller that removes the autostart entry and offers to delete the user data. Built with
  PyInstaller and Inno Setup (`packaging/`), installed and smoke-tested in CI, attached to releases on `v*` tags.
- **GPU support on demand** for the installed app: `Hams.exe --install-gpu-support` downloads the pinned cuBLAS/cuDNN
  wheels from PyPI (SHA-256 checked, resumable), keeps only the DLLs in `%LOCALAPPDATA%\Hams\cuda` and preloads them
  from there. Source installs keep using the `[cuda]` extra. Without the libraries Hams now goes straight to the CPU
  instead of failing at the first dictation.
- `--self-test` (also `python -m hams --self-test`): checks imports, package data, audio backend and Tk without a
  window or microphone.
- `SECURITY.md`, `CONTRIBUTING.md`, issue and pull request templates.

## [1.0.0] – 2026-11-12

First public release as a standalone repository. Earlier development took place inside a private monorepo; its
history for Hams is preserved here.

### Added
- **Glossary** (`glossary.py`, `glossar.default.toml`): words are passed to Whisper as hotwords, fixed mishearings
  are corrected after transcription, and the LLM cleanup is told to keep those spellings. Changes apply without a
  restart.
- **Settings launcher** for Windows (`launch_hams.cs`, built with `build_launcher.cmd`): opens the settings of the
  running instance, or starts Hams with the settings window.
- Linux: native KDE title bar, `SIGUSR1` brings the window to the front (`linux/hams-zeigen.sh`), a soft chime
  instead of a beep, and mouse-wheel scrolling in the settings.
- Reproducible WER and latency benchmark `tools/wer_messung.py`.
- CI on Windows and Linux (tests with an 85 % coverage gate, ruff, `pip-audit`, package build) and a gitleaks
  secret scan.
- English README, plus `docs/architecture.md` and `docs/evaluation.md` with the thesis measurements.

### Changed
- The level meter is logarithmic (dBFS). Speech from half a metre away was barely visible on the old linear scale.
- Windows: cuBLAS and cuDNN are preloaded from the NVIDIA wheels, so the first GPU transcription no longer fails
  without `torch`.
- `main()` was split into smaller functions (complexity limit 10).
- The distribution is now named `hams`, with a `hams` command; `wisper` stays as an alias.
- Dependencies were updated to versions without known vulnerabilities (certifi, filelock, fsspec, Pillow).

## [0.2.0] – 2026-09-28

### Added
- Linux support for Kubuntu / KDE Plasma on Wayland: hotkeys via the XDG Desktop Portal, a `uinput` virtual
  keyboard, a systemd user service.
- Hash-locked dependencies (`requirements.txt`, `requirements-dev.txt`), checked with `pip-audit`.
- Tests for the running application (start, dictation, settings window): more than 1,500 tests, 91 % coverage.
- Validation of the settings (`config_validation.py`), with warnings in the log.

### Changed
- `main.py` (≈3,400 lines) and `ui_kit.py` were split into modules (`app`, `recording`, `transcription`,
  `whisper_model`, `settings_*`, `ui_kit/…`); no function exceeds complexity 10.
- Hotkeys use `RegisterHotKey` instead of a keyboard hook and now survive standby.
- The cleanup prompt is stricter against over-correction.
- Logging goes to a file from DEBUG and to the console from WARNING.

### Security
- Dictations travel over plain HTTP only to the local machine; a remote Ollama URL must use HTTPS.
- The log records text lengths, never the dictated text.

## [0.1.0] – 2026-09-08

The version evaluated in the Bachelor thesis.

### Added
- Floating always-on-top window and a tray icon with status colours.
- Global hotkeys: Ctrl+Shift+Space for dictation, Ctrl+Shift+Alt+Space for prompt mode.
- Recording with a level meter and start/stop sounds.
- faster-whisper on CUDA, Ollama for cleanup and prompt answers.
- Paste via the clipboard, a dictation history in the tray menu, and autostart.
- A documented `config.toml`.
