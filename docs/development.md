# Development

How to run Hams from source, test it and build the Windows installer. Users of the installer don't need any of this;
see the [user guide](user-guide.md). Contribution rules (strings, tests, pull requests) are in
[CONTRIBUTING.md](../CONTRIBUTING.md); the design is described in [architecture.md](architecture.md).

## Run from source

Requirements: Windows 10/11 or Linux, Python 3.11+, an NVIDIA GPU for the default settings (the CPU works too, just
slower), and optionally [Ollama](https://ollama.com/download).

```powershell
git clone https://github.com/Mosaik114/hams-bachelorprojekt.git
cd hams-bachelorprojekt
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[cuda]"     # [cuda] adds cuBLAS + cuDNN without pulling in torch
ollama pull qwen3.5:9b       # default cleanup and Ask AI model; any Ollama model works
hams
```

On first start, the setup assistant downloads the speech model (Turbo, 1.6 GB, with a GPU). For exactly the versions
that CI tests, install with hashes: `pip install --require-hashes -r requirements.txt` and then
`pip install --no-deps -e .`.

On Linux, follow the [user guide](user-guide.md#linux) or run `linux/install.sh` from the checkout.

Autostart from source on Windows: `python hams/setup_autostart.py` (remove with `--remove`). It registers
`scripts/start_hams.vbs`, which starts Hams without a console window. `scripts/build_launcher.cmd` builds an
optional `Hams.exe` that opens the settings of the running instance.

## Tests and lint

```powershell
pip install --require-hashes -r requirements-dev.txt
pip install --no-deps -e .
pytest tests --cov=hams       # ≈1,600 test functions, ≈3,200 test cases; Tk tests open real windows
ruff check . ; ruff format --check .
```

Tk tests open real windows: run them under `xvfb-run` on Linux, or on a separate desktop on Windows (details in
[CONTRIBUTING.md](../CONTRIBUTING.md#tests)). CI rules: no function with a cyclomatic complexity above 10, and the
coverage gate fails the build below 93 % (Windows) or 88 % (Linux), line and branch coverage.

## Build the Windows installer

The installer is built from a clean environment without the `[cuda]` extra. The frozen app comes from PyInstaller
(onedir), and Inno Setup turns it into `build\installer\Hams-Setup-<version>.exe`:

```powershell
.\packaging\build.ps1 -InstallDependencies -Installer   # needs Inno Setup 6 for -Installer
Start-Process -Wait build\dist\Hams\Hams.exe -ArgumentList '--self-test','--report','build\self-test.txt'
```

CI builds the installer on every push. It installs the result silently, runs `--self-test`, uninstalls it, and checks
that nothing is left. On a `v*` tag, CI also attaches the installer to the GitHub release.

## Code layout

```
hams/
├── main.py              entry point: bootstrap, single instance, Tk root
├── bootstrap.py         what the start does once: log, user file, settings, theme
├── app.py               Tk shell (main window + tray) that owns the core objects below
├── status.py            StatusSink: how the core objects talk to the window
├── recording.py         Recorder: microphone → audio buffer, level meter (dBFS)
├── audio_devices.py     input device list and name resolution
├── transcription.py     Pipeline: Whisper, glossary, Ollama cleanup, paste
├── cleanup_guard.py     checks the cleanup answer, falls back to the raw text
├── whisper_model.py     ModelManager: load, switch and download models with fallback
├── backend_preload.py   make cuBLAS/cuDNN reachable before faster-whisper loads
├── gpu_support.py       on-demand CUDA download for the installed app
├── cli.py, self_test.py command line: --self-test, --install-gpu-support
├── support.py           help links, log folder, in-app diagnostic report
├── glossary.py          hotwords, fixed corrections, live reload
├── ollama.py            OllamaClient (every request to Ollama), status and model list
├── cursor_dot.py        optional: recording state to an external cursor overlay (off by default)
├── system_hotkeys.py    Windows hotkeys     │ portal_hotkeys.py  Linux hotkeys
├── linux.py             uinput paste, chime, signals, lock file
├── settings_*.py        settings window, sections, live status, model manager, about & help
├── config*.py           layered TOML config (no import side effects), validation, persistence
├── i18n.py              interface language; strings in messages_en.py / messages_de.py
├── dictation_language.py  dictation language: Whisper hints and Ollama instructions per language
└── ui_kit/              a small design system for tkinter (theme, DPI scaling, controls, icons)
scripts/                 Windows start script and settings launcher
packaging/               PyInstaller spec, Inno Setup script, build and smoke test
linux/                   install script, system setup and bring-to-front helper for Linux
tools/                   reproducible WER, latency and cleanup benchmarks (need a GPU and Windows TTS)
```

The codebase is about 10.5k lines of application code and 12k lines of tests.

## How the Linux port differs from Windows

| | Windows | Linux |
|---|---|---|
| Global hotkeys | `RegisterHotKey` | XDG Desktop Portal `GlobalShortcuts` (KDE asks once) |
| Paste | `keyboard` library | own virtual keyboard via `/dev/uinput` (Wayland blocks synthetic input otherwise) |
| Sound | `winsound` | soft synthesised chime via `sounddevice` |
| Config | `%APPDATA%\Hams\config.toml` | `~/.config/hams/config.toml` (or `$XDG_CONFIG_HOME/hams`) |
| Data, logs | `%LOCALAPPDATA%\Hams` | `~/.local/share/hams` (or `$XDG_DATA_HOME/hams`) |
| Autostart | registry `Run` key | `~/.config/autostart` + systemd user service |
| Bring to front | tray icon, or start Hams again | `linux/show-hams.sh` (bind it to a key or panel button), or start Hams again |
