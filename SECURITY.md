# Security policy

## Supported versions

Only the latest release on `main` receives fixes.

## Reporting a vulnerability

Please report security issues privately through
[GitHub's private vulnerability reporting](https://github.com/Mosaik114/hams-bachelorprojekt/security/advisories/new)
instead of opening a public issue. You can expect a first answer within a week.

Please include the Hams version, your operating system, and steps to reproduce. Do not attach logs or recordings
that contain personal data.

## Design notes relevant to security

- **Local by default.** Audio is transcribed on the machine with faster-whisper. The only network traffic Hams
  produces is to Ollama and, once per model, the Whisper model download from Hugging Face.
- **No plain HTTP off the machine.** An Ollama URL with `http://` is only accepted for the local machine
  (`127.0.0.1`, `::1`, `localhost`); a remote endpoint must use HTTPS (`config_validation.py`).
- **Dictated text is never logged.** The log records text lengths and timings only.
- **No elevation on Windows.** Hams runs with the user's normal rights; autostart is a per-user `HKCU\…\Run` value
  and the single-instance lock is a named mutex.
- **Linux paste needs `/dev/uinput`.** `linux/setup-system.sh` grants the logged-in user access to `/dev/uinput`
  via a udev rule. Any program of that user can then create input devices; `--entfernen` removes the rule again.
- **Settings are written atomically** (temporary file + `os.replace`), so a crash cannot leave a half-written
  configuration.
- **Supply chain.** Dependencies are locked with hashes (`requirements.txt`, `requirements-dev.txt`) and checked
  with `pip-audit` in CI. GitHub Actions are pinned to commit SHAs, Dependabot proposes updates, and every push is
  scanned for committed secrets with gitleaks.
