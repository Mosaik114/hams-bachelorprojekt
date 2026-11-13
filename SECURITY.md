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

- **Local by default.** Audio is transcribed on the machine with faster-whisper. Audio never leaves the machine,
  and dictated text only goes to the Ollama address you configured. The complete list of connections is below.
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

## Network connections

Every outbound connection in the code (`hams/`), and what triggers it. There is no telemetry, crash reporting or
account.

| Endpoint | Code | When | What is sent |
|---|---|---|---|
| Ollama, `http://127.0.0.1:11434` by default (`/api/generate`, `/api/tags`, `/api/ps`, `/api/pull`); configurable with `[ollama] url`, plain HTTP only for `127.0.0.1`, `::1` or `localhost`, otherwise HTTPS | `transcription.py`, `ollama.py` | cleanup, *Ask AI*, warm-up at start, status check every 5 s while the settings window is open, model download you start | the transcript and the instructions (cleanup, *Ask AI*); a model name (download) |
| Hugging Face, `huggingface.co` and its download CDN | `models.py` (`snapshot_download`), `whisper_model.py` (faster-whisper's model loader) | downloading a Whisper model; a metadata request each time a model is loaded, which fails silently offline and the cached copy is used | standard requests for the public model files; no audio or text |
| PyPI, `files.pythonhosted.org` | `gpu_support.py` | the GPU step of the setup assistant or `Hams.exe --install-gpu-support` | download of three NVIDIA wheels (cuBLAS, cuDNN, NVRTC) at pinned URLs; each file is checked against a pinned SHA-256 before use |
| GitHub API, `api.github.com/repos/Mosaik114/hams-bachelorprojekt/releases/latest` | `updates.py` | at start, at most once every 24 hours; `[updates] check = false` or *Settings → General → Check for updates* turns it off | a plain HTTPS GET with the generic user agent `Hams-update-check`; nothing is downloaded |
| Browser links (opened in your default browser, not fetched by Hams) | `update_notice.py`, `setup_window.py`, `support.py` | *View* on an update notice (only a release page of this repository), *Get Ollama* (`ollama.com/download`), *User guide*, *Troubleshooting* and the tray's *Help …* (`github.com`) | nothing from Hams |

Local only, no network: the single-instance lock, `Hams.exe --settings` (a named Windows event), and on Linux the
`uinput` keyboard and an optional `AF_UNIX` status socket. Ollama itself is a separate program with its own network
behaviour (for example, `ollama pull` downloads from `ollama.com`).
