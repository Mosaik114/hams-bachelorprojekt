# Changelog

All notable changes to Hams. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **About & help in the settings:** the installed version, the user guide and troubleshooting pages, the log folder,
  and *Create* for a diagnostic report. The report runs the self-test in the background, adds the end of the log
  (never dictated text), saves `%LOCALAPPDATA%\Hams\hams-report.txt` and opens it, so a bug report needs no
  terminal. The tray menu has *Help …* for the user guide.
- **Uninstall can remove the speech models:** a second question, *No* by default and never asked in a silent
  uninstall, deletes only Hams' own Whisper model folders from the Hugging Face cache and shows their size first.
- `linux/install.sh` runs the Linux install steps of the user guide (system setup, `uv` after asking, `.venv`,
  Hams without development extras, menu entry and autostart after asking).
- Docs: a Linux section in troubleshooting, `docs/development.md` (from source, tests, installer build, code
  layout), the list of documented optimisations in `docs/evaluation.md`, and every network connection in
  `SECURITY.md`.

### Changed
- One name for the setup assistant everywhere: tray *Setup assistant …*, window title, settings and docs (German:
  *Einrichtungsassistent*).
- A Whisper model that fails to load now says where to look: *Couldn't load · see Troubleshooting*.
- British spelling in the interface (*recognisable*).
- README shortened to the essentials, with a *Use it* box for both hotkeys and a *How it was built* section; the
  developer material moved to `docs/development.md`. The README no longer speaks of "twelve" optimisations, a
  number the repository does not document.
- The PowerShell self-test commands quote the report path, so they also work when the user name contains a space.

## [1.1.0] – 2026-11-13

Hams becomes an app you can install without Python: a Windows installer, a setup assistant on the first start,
an English interface, several dictation languages and a measured, safer LLM cleanup.

### Added
- **Windows installer** `Hams-Setup-<version>.exe`: per-user, no admin rights, no Python. Start menu entry, optional
  autostart, an uninstaller that asks before deleting user data. Built with PyInstaller and Inno Setup
  (`packaging/`), installed, self-tested and uninstalled in CI, attached to `v*` releases. Not code-signed yet.
- **GPU support on demand:** the installed app downloads the pinned cuBLAS/cuDNN/NVRTC wheels from PyPI (1.37 GB,
  SHA-256 checked, resumable, one install at a time) and keeps only the DLLs in `%LOCALAPPDATA%\Hams\cuda`.
  Without them Hams runs on the CPU from the start.
- **Setup assistant** on the first start (again from Settings, the tray or `--setup`): languages, graphics card and
  GPU download, speech model download (Turbo on a ready GPU, Small on the CPU), microphone check, optional Ollama
  model, shortcuts. Errors are sentences with *Try again*.
- **English interface** with `[ui] language` (`auto` follows the system); all texts come from message catalogs.
- **Dictation languages:** German, English, French, Spanish, Italian, Dutch, Polish, Portuguese and automatic
  detection, each with its own Whisper and cleanup instructions. German is unchanged word for word.
- **Settings:** change the hotkeys by pressing them (reserved, taken and conflicting combinations are refused),
  edit the glossary, download a missing Ollama model, turn the update notice on or off.
- **Update notice:** at most once a day Hams asks `api.github.com` for the latest release and shows a dismissible
  hint. It never downloads anything; `[updates] check = false` turns it off.
- **Cleanup evaluation** `tools/cleanup_eval.py` and its results in `docs/evaluation.md`: WER before and after
  cleanup, content preservation and trap inputs, measured on the real product code paths.
- User guide, hardware page with measured GPU and CPU timings, troubleshooting, `--self-test --report`, winget
  manifest templates, `SECURITY.md`, `CONTRIBUTING.md`, issue and pull request templates.

### Changed
- **LLM cleanup runs at temperature 0 and its answer is checked before pasting.** Answers that repeat the
  instructions, change digits, grow far beyond the dictation or drop most dictated words fall back to the raw
  transcript. Measured on synthetic speech: WER after cleanup 13.32 → 12.12 %, outputs flagged for a changed meaning
  16 → 6 of 378, trap inputs passed 20 → 26 of 27, same latency. `[ollama] cleanup_temperature` sets it.
- **Ollama is optional and its absence is calm:** cleanup is switched off when Ollama isn't there, a missing
  service gives one neutral hint, and dictation never waits on it.
- Starting Hams again brings the running window to the front. The prompt mode is called *Ask AI*.
- **Renamed from the working title *Wisper*:** package `hams`, user data in `%APPDATA%\Hams` and
  `%LOCALAPPDATA%\Hams` (Linux `~/.config/hams`, `~/.local/share/hams`). An old `Wisper` folder is copied once and
  never deleted; old and new versions never run at the same time. Logs and transcripts left the package folder.
- Helper scripts moved to `scripts/` with English names; `linux/setup-system.sh` speaks English and installs the
  NVIDIA driver only with `--with-nvidia-driver`. README ordered for users; the configuration reference moved to the
  user guide. Dependencies updated (ctranslate2 4.8.5 among them, checked on the GPU).

### Fixed
- Loading the model at start while switching models (Retry, the setup assistant, Settings) could leave the wrong
  model loaded with the UI showing another one. All loads now go through one request path with a generation number.
- A config value of the wrong type (for example `timeout_cleanup = "30s"`) stopped Hams from starting, and
  `check = "false"` counted as true. Every value is now read with a type and range check and falls back to the
  default with a warning.
- A section header with a comment (`[hotkeys] # mine`) made saving append a duplicate table, after which all
  settings were reset. Saves are now checked with a TOML parser before writing; an unreadable file is kept, backed up
  and reported, never overwritten.
- Worker threads (hotkeys, tray, downloads, Ollama checks) no longer call Tk directly; a queue on the Tk thread
  takes their callbacks, which also removes a 2-second freeze when hotkeys were re-registered.
- GPU install errors (full disk, no write access) are reported as sentences instead of tracebacks; an Ollama answer
  of the wrong shape no longer leaves the assistant on "checking".
- The packaged app now includes the English glossary template.

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
