# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Config validation module (`wisper/config_validation.py`)
- Logging validation warnings for invalid config values
- Console handler for ERROR-level logs (in addition to file handler)
- Unit tests with pytest (15 tests passing)
- Development section in README
- CHANGELOG.md

### Changed
- Logging level increased to DEBUG (file) / WARNING (console)
- Improved docstrings for main classes and methods
- Cleanup prompt rewritten with stricter instructions against over-correction
- Ollama defaults switched to `qwen2.5:7b` for cleanup and prompt generation

### Fixed
- Minor code quality improvements

## [0.1.0] - Initial Release

### Added
- Always-on-top floating window with dark theme
- System tray integration with status colors
- Global hotkeys (`Ctrl+Shift+Space` for transcript, `Ctrl+Shift+Alt+Space` for prompt)
- Audio recording with level meter
- Faster-Whisper integration with CUDA support
- Ollama integration for text cleanup and prompt generation
- Clipboard automation (copy + paste)
- Transcript history in tray menu
- Auto-start setup script
- Comprehensive config.toml documentation
- Acoustic feedback (start/stop beeps)
