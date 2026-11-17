<p align="center">
  <img src="hams/assets/branding/hams_mark_256.png" alt="Hams logo" width="104">
</p>

<h1 align="center">Hams Dictate</h1>

<p align="center">
  <b>Local, privacy-first voice input for Windows and Linux.</b><br>
  Press a hotkey, speak, and clean text appears wherever your cursor is: transcribed by Whisper on your GPU,
  polished by a local LLM, never sent to the cloud.
</p>

<p align="center">
  <a href="https://github.com/Mosaik114/hams-bachelorprojekt/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Mosaik114/hams-bachelorprojekt/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="https://github.com/Mosaik114/hams-bachelorprojekt/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/Mosaik114/hams-bachelorprojekt?label=release&color=0B7D72"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="Platforms: Windows and Linux" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-informational">
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

<p align="center">
  <img src="docs/images/demo.gif" width="880" alt="Hams in action: the cursor sits in an email draft; while Hams records a spoken sentence with filler words, a small level pill sits beside the pointer; then Hams transcribes it and pastes the cleaned-up sentence at the cursor">
  <br>
  <sub>Text and timing from a real dictation, idle pauses trimmed: filler words in, a clean sentence out.</sub>
</p>

<p align="center">
  <b><a href="https://github.com/Mosaik114/hams-bachelorprojekt/releases/latest">Download Hams for Windows</a></b>
  (installer, no Python needed) · <a href="docs/user-guide.md#linux">Linux</a> ·
  <a href="docs/user-guide.md">User guide</a>
</p>

> **Use it**
> 1. Click where the text should go, press **`Ctrl+Shift+Space`**, speak, press it again: the text is pasted there.
> 2. Press **`Ctrl+Shift+Alt+Space`** instead to *Ask AI*: your spoken question goes to a local LLM, the answer is pasted.
> 3. Everything else is behind the tray icon: settings, setup assistant, help, the last five transcripts.

## Highlights

- **Two modes, two global hotkeys.** *Dictation* transcribes, cleans up and pastes; *Ask AI* pastes the answer of a
  local LLM.
- **Fully local.** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on the GPU (or CPU) and
  [Ollama](https://ollama.com) for the cleanup. No account, no telemetry; logs record text lengths, never the text.
- **Ten dictation languages and a glossary.** German, English, French, Spanish, Italian, Dutch, Polish, Portuguese,
  Turkish, Arabic or *Automatic*; names and jargon keep their spelling. The glossary is edited right in the app.
- **Seven interface languages.** German, English, Russian, Spanish, French, Arabic (right to left) and Turkish, or
  *Automatic*, which follows Windows.
- **One-click updates** (Windows). *Update now* downloads the new setup, checks it against the release's SHA-256
  file, installs it silently and starts Hams again, never in the middle of a dictation.
- **Level at the pointer** (Windows). While you dictate, a small level pill follows the mouse, so you see that Hams is
  listening without looking for its window.
- **Fails soft.** If Ollama is slow or offline, the raw transcript is pasted anyway; without a suitable GPU,
  Whisper runs on the CPU.

<p align="center"><img src="docs/images/level-at-pointer.png" width="420" alt="Level at the pointer: a small pill with level bars above the mouse pointer while dictating into a text field"><br><sub>The level pill beside the pointer while dictating.</sub></p>

## For reviewers

If you have five minutes, this is what the project shows:

| | Where to look |
|---|---|
| **Measured result:** median response time 3.09 s → 0.72 s, each change measured on its own | [Evaluation](docs/evaluation.md#headline-results), [where the time went](docs/evaluation.md#where-the-time-went) |
| **Testing:** ≈2,000 test functions, many driving the real Tk app; line + branch coverage gates (93 % Windows, 88 % Linux) | [Quality gates](docs/architecture.md#quality-gates), [CI runs](https://github.com/Mosaik114/hams-bachelorprojekt/actions/workflows/ci.yml) |
| **Architecture:** threaded pipeline, no `torch`, graceful fallbacks, decisions with the rejected alternative | [Design decisions](docs/architecture.md#design-decisions) |
| **Working with AI agents:** I set scope, architecture and measurement rules; Claude Code wrote most of the code | [How it was built](#how-it-was-built), [review log](docs/reviews.md) |
| **Shipping:** installer built in CI with signed build provenance, silent-install smoke test, user docs | [Releases](https://github.com/Mosaik114/hams-bachelorprojekt/releases), [user guide](docs/user-guide.md) |

Built by **Mazgin A. Ahmad**, business informatics student at Kiel University (CAU) ·
[GitHub profile](https://github.com/Mosaik114)

## Install

**Windows 10/11:** download `Hams-Setup-<version>.exe` from the
[latest release](https://github.com/Mosaik114/hams-bachelorprojekt/releases/latest) and run it. No Python, git or
admin rights needed. An NVIDIA GPU is recommended, not required; plan for about 4 GB of disk with the GPU model
([requirements](docs/hardware.md#requirements)). The installer is not code-signed, so SmartScreen may warn:
**More info → Run anyway**.

**Verify the download:** every release has `Hams-Setup-<version>.exe.sha256` next to the installer (compare with
`Get-FileHash Hams-Setup-<version>.exe`; *Update now* in the app does exactly that and refuses anything that does
not match), and every installer carries a build provenance attestation from CI:
`gh attestation verify Hams-Setup-<version>.exe --repo Mosaik114/hams-bachelorprojekt`.

On first start, a setup assistant picks the languages, checks the graphics card, downloads the speech model, tests
the microphone and looks for Ollama.

<p align="center"><img src="docs/images/setup-model.png" width="360" alt="Setup assistant, step 3 of 6: the Turbo speech model is downloaded and ready"><br><sub>The setup assistant downloads the speech model that suits your hardware.</sub></p>

**Optional, for cleanup and *Ask AI*:** install [Ollama](https://ollama.com/download) and run the setup assistant
again (*Settings → General → Setup assistant*); it downloads the default model (or run `ollama pull qwen3.5:9b`).
Without Ollama, Hams pastes the raw transcript.

**Linux** (KDE Plasma on Wayland): run `linux/install.sh` from a clone, or follow the
[user guide](docs/user-guide.md#linux).

**Something wrong?** See [troubleshooting](docs/troubleshooting.md); *Settings → About & help → Diagnostic report*
prepares a [bug report](https://github.com/Mosaik114/hams-bachelorprojekt/issues/new/choose).

## How it works

```mermaid
flowchart LR
  H[Global hotkey] --> R[Recording<br/>16 kHz mono]
  R -- audio --> W[faster-whisper<br/>turbo · CUDA float16 · VAD]
  G[(Glossary)] -. hotwords .-> W
  W -- raw text --> O[LLM cleanup<br/>Ollama on 127.0.0.1]
  O --> F[Output guard +<br/>glossary fixes]
  O -. timeout or offline: raw text .-> F
  F --> T[Clipboard, then Ctrl+V<br/>in the target app]
```

Recording, transcription and cleanup run off the UI thread. If the LLM answer changes digits, leaks the prompt or
drops most words, an output guard pastes the raw transcript instead. Details: [architecture](docs/architecture.md).

<p align="center"><img src="docs/images/settings-ai.png" width="360" alt="Settings, AI tab: text cleanup on, Ollama running, Qwen 3.5 9B as cleanup and Ask AI model"><br><sub>Local AI cleanup with the Ollama model of your choice.</sub></p>

## The thesis: is local fast enough?

*Hams* (Arabic **همس**, "whisper") started as my Bachelor project at Kiel University (CAU) and is the dictation tool
I use every day. The question: **can speech recognition plus LLM post-processing run fully locally, system-wide,
and fast enough for daily use?** It can. Measured on an RTX 5070 with Whisper `turbo` and a 7B cleanup model:

| Metric | Before | After |
|---|---:|---:|
| Response time, real dictations (median)¹ | 3.09 s | **0.72 s** |
| Ollama cleanup call | 2.941 s | **0.839 s** |
| Hotkey ready after start | ~3 s | **0.38 s** |
| Memory after import | 505.9 MB | **74.8 MB** |

<sub>¹ Logged real use before and after (n = 172 → 678), not a controlled run. Every row is rated for comparability
in the [evaluation](docs/evaluation.md#headline-results), together with word error rates and cleanup quality.</sub>

**The bottleneck wasn't the model.** About two thirds of each cleanup call was a refused IPv6 connection:
`localhost` resolved to `::1` first, Ollama only listened on IPv4, and Windows waited about 2 s before falling back.
Addressing `127.0.0.1` directly fixed it; a smaller model would have saved just 0.38 s.

## How it was built

Hams was built AI-native with [Claude Code](https://claude.com/claude-code). I owned the scope, the architecture
decisions, the measurement rules and the review; the agent wrote most of the code and ran most of the measurements
under those rules ([who ran what](docs/evaluation.md#who-ran-the-measurements)). The safeguards are the ones a team
would use: small reviewed changes, tests and CI as the gate, measured numbers instead of impressions. Three
structured review rounds by AI agents followed; I triaged every finding ([review log](docs/reviews.md)).

Besides `127.0.0.1` over a smaller model, two decisions against an alternative I turned down:

- **No `torch`:** cuBLAS/cuDNN come from the NVIDIA wheels, saving 1.6 s per start and 4.3 GB on disk
  ([architecture](docs/architecture.md#design-decisions)).
- **A guard that pastes the raw text** instead of repairing a bad LLM answer, with cleanup at temperature 0
  ([evaluation](docs/evaluation.md#llm-cleanup-quality)).

## Documentation

- [User guide](docs/user-guide.md): install, daily use, settings, glossary, Linux, privacy
- [Troubleshooting](docs/troubleshooting.md): hotkeys, pasting, SmartScreen, GPU, Ollama, microphone
- [Hardware](docs/hardware.md): requirements and measured speed per Whisper model
- [Architecture](docs/architecture.md) and [evaluation](docs/evaluation.md): design, measurements, limitations
- [Development](docs/development.md): run from source, tests, build the installer, project history
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md) ·
  [Code of conduct](.github/CODE_OF_CONDUCT.md)

**Roadmap:** evaluate WER and cleanup on real (non-synthetic) speech, track VRAM per process, and ship a Linux
package so Linux users don't need git.

<details><summary>More screenshots</summary>
<p align="center"><img src="docs/images/main-window.png" width="400" alt="Main window: ready, with the Turbo model loaded and both shortcuts"><br><sub>The main window: status, record button and both shortcuts.</sub></p>
</details>

## Acknowledgements and license

Built on [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2),
[Ollama](https://ollama.com), [sounddevice](https://python-sounddevice.readthedocs.io) and
[pystray](https://github.com/moses-palmer/pystray). MIT License, see [LICENSE](LICENSE).
