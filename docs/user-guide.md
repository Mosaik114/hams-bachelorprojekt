# User guide

This guide is for people who want to *use* Hams on Windows. Developers who run it from source should start with
the [README](../README.md). If something doesn't work, see [troubleshooting.md](troubleshooting.md); for hardware
requirements and measured speeds, see [hardware.md](hardware.md).

Hams turns speech into text at your cursor. You press a hotkey, speak, press it again, and the text is pasted into
whatever window has focus. Speech recognition runs on your own computer with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper). An optional local LLM via [Ollama](https://ollama.com)
can tidy up the text or answer a spoken question.

## Install

1. Download `Hams-Setup-<version>.exe` from the
   [latest release](https://github.com/Mosaik114/hams-bachelorprojekt/releases/latest).
2. Run it. Hams installs for your Windows user only, without admin rights and without Python, into
   `%LOCALAPPDATA%\Programs\Hams` (about 257 MB). The installer adds a Start menu entry and offers two options:
   *Start Hams when I sign in to Windows* (on by default) and a desktop icon (off by default).
3. **Windows warning about an unknown publisher.** The installer and `Hams.exe` are not code-signed. Microsoft
   Defender SmartScreen may show *"Windows protected your PC"*. Click **More info → Run anyway**. If *Smart App
   Control* is switched on (Windows 11), it can block unsigned programs without offering that button; see
   [troubleshooting.md](troubleshooting.md#windows-blocks-the-installer-or-hamsexe). Only do this for a file you
   downloaded from the release page above.

Hams needs no other software for dictation. Ollama is optional (see [Optional: Ollama](#optional-ollama)).

## First start

The first start downloads the speech recognition model, so it needs an internet connection once. After that, Hams
works offline.

On first start, a short setup assistant walks you through these steps. Every step can be skipped, and you can run
it again later from the settings, the tray menu, or with `Hams.exe --setup`.

1. **Graphics card.** With an NVIDIA graphics card, Hams offers to download NVIDIA's CUDA libraries (cuBLAS, cuDNN
   and NVRTC from PyPI, a one-time download of about 1.4 GB, about 2 GB unpacked). Without them, or without an
   NVIDIA card, Hams runs on the CPU, which works but is slower. The download is checked against pinned SHA-256
   hashes, continues where it stopped after an interruption, and lands in `%LOCALAPPDATA%\Hams\cuda`. The same
   download is available from a terminal:

   ```powershell
   Start-Process -Wait -NoNewWindow "$env:LOCALAPPDATA\Programs\Hams\Hams.exe" -ArgumentList "--install-gpu-support"
   ```

   Restart Hams afterwards if you used the terminal.
2. **Speech model.** Hams suggests *Turbo* (1.6 GB) with a GPU and *Small* (0.5 GB) without one.
   [hardware.md](hardware.md) has measured speeds for every model.
3. **Microphone.** Pick a device and say a few words; a level bar shows whether Hams hears you.
4. **Text cleanup (optional).** If Ollama is running, Hams can download the default cleanup model through it
   (several GB). If not, cleanup stays off and *Get Ollama* opens the Ollama download page in your browser. See
   [Optional: Ollama](#optional-ollama).

The interface language follows Windows (English, or German on a German Windows) and the dictation language follows
the interface language. Change either one in the settings (see [Dictation languages](#dictation-languages)).

While the model loads, the Hams window says *Loading model …*. When it shows *Ready · Turbo* (or the model you
chose), you can dictate.

## Daily use

### Dictation

| Hotkey | Mode | What happens |
|---|---|---|
| `Ctrl+Shift+Space` | Dictation | record → transcribe → (optional) clean up with Ollama → paste |
| `Ctrl+Shift+Alt+Space` | Prompt | record → transcribe → send to Ollama as a question → paste the answer |

1. Click into the window where the text should go (an editor, a chat box, a browser field).
2. Press `Ctrl+Shift+Space`. A short beep plays and the Hams window shows *Recording* with a level meter.
3. Speak.
4. Press `Ctrl+Shift+Space` again. Hams transcribes, then pastes the text at the cursor.

The status line tells you what happened: *Pasted*, *Pasted · without cleanup* (Ollama was off or not reachable),
*No speech detected*, or *No microphone signal*. A recording stops by itself after 300 seconds
(`[audio] max_recording_seconds`). You can also start and stop with the button in the Hams window.

Hams pastes by putting the text on the clipboard and sending `Ctrl+V`. The text **stays on the clipboard**
afterwards, so you can paste it again elsewhere. If Windows clipboard history (`Win+V`) is on, it is kept there
too.

### Prompt mode

Prompt mode needs Ollama. Press `Ctrl+Shift+Alt+Space`, speak a request ("write a polite reply saying I'll be late
by ten minutes"), press the hotkey again. Hams sends the transcribed text to the *prompt model* in Ollama and
pastes the answer. Long answers take a few seconds; the window shows *Writing answer …* meanwhile. Without Ollama
you get *AI answer failed — Ollama isn't responding*.

### Window and tray icon

Hams shows a small floating window and an icon in the notification area (system tray). The window can be hidden;
Hams keeps running in the tray. Right-click the tray icon for:

- **Show or hide window**
- **Settings …**
- **Run setup …**: the setup assistant from the first start
- **Recent transcripts**: the last five texts of this session. Clicking one copies it to the clipboard again. This
  list lives in memory only and is empty after a restart.
- **Quit**

Every pasted text is also saved as a plain-text file in `%LOCALAPPDATA%\Hams\transcripts`, named after the date and
time (`2026-11-13_09-41-07.txt`). Delete these files whenever you like; Hams does not read them back.

Only one Hams runs at a time. Starting it again from the Start menu just reminds you that it is already running in
the tray; `Hams.exe --settings` opens the settings of the running instance instead.

## Settings

Open the settings from the gear icon in the Hams window or from the tray menu. Changes there are saved immediately
to your configuration file.

| Section | What you can change |
|---|---|
| Recording | microphone, live input level |
| Transcription | Whisper model (download, switch, delete downloaded models), dictation language, hardware in use |
| AI | text cleanup on/off, cleanup model, prompt model, Ollama status |
| Shortcuts | both hotkeys, each with *Change* (see [Changing the hotkeys](#changing-the-hotkeys)); *Restart* button for when hotkeys stop responding |
| General | sound on start/stop, light or dark appearance, interface language, *Check for updates*, *Run again* for the setup assistant |

Model switches and dictation language apply from the next recording. The interface language applies after a
restart (the settings offer a *Restart* button).

### Changing the hotkeys

In *Settings → Shortcuts*, press **Change** next to a hotkey, then press the new combination. Esc (or the ×)
cancels and keeps the old one. While Hams waits for the keys, both hotkeys are paused, so pressing the old
combination doesn't start a recording.

A new combination needs Ctrl, Alt or Win (Shift alone isn't enough); only function keys work on their own. Hams
refuses combinations Windows keeps for itself or that would break typing (for example Alt+Tab, Win+L, Ctrl+C,
Ctrl+V, F12), the combination the other Hams hotkey already uses, and combinations another program has registered
(*… is in use by another app*). An accepted combination works immediately, without a restart, and is saved to
`[hotkeys]` in `config.toml`. **Default** (shown while changing a hotkey that differs from its default) restores
`Ctrl+Shift+Space` for dictation or `Ctrl+Shift+Alt+Space` for prompt mode.

On Linux, KDE manages the hotkeys of the desktop portal: change them in *System Settings → Keyboard → Shortcuts*.

### The configuration file

Everything, including options that have no control in the settings window, lives in

```
%APPDATA%\Hams\config.toml
```

Hams creates it on first start. All available keys and their defaults are documented in
[`hams/config.default.toml`](../hams/config.default.toml); you only need to put the keys you want to change into
your own file. Quit Hams before editing the file by hand, and start it again afterwards. Examples:

```toml
[hotkeys]
transcript = "ctrl+alt+d"      # modifiers: ctrl, shift, alt, win; key: a letter, digit, F1–F24, space, …
prompt = "ctrl+alt+p"

[ollama]
cleanup_enabled = false        # paste the raw Whisper text (fastest)

[audio]
max_recording_seconds = 120
```

The settings window writes the same `[hotkeys]` keys (see [Changing the hotkeys](#changing-the-hotkeys)). To turn
off the update check (see [Privacy](#privacy)):

```toml
[updates]
check = false
```

## Dictation languages

The interface language (English or German) and the language you *speak* are separate settings.

Dictation languages: German, English, French, Spanish, Italian, Dutch, Polish, Portuguese, or **Automatic**, where
Whisper detects the language of each recording before transcribing it. Change it under *Settings → Transcription →
Dictation language*, or set `[transcription] language` to `"de"`, `"en"`, `"fr"`, `"es"`, `"it"`, `"nl"`, `"pl"`,
`"pt"` or `"auto"`.

- If you always speak the same language, pick it explicitly. *Automatic* can misjudge very short recordings
  (a word or two).
- The Whisper models are multilingual, so switching costs no download and no restart.
- Hams does not translate. The Ollama cleanup is told to keep the language you spoke.
- All accuracy and speed measurements in this project were made in German (thesis) or on short synthetic English
  and German samples ([hardware.md](hardware.md)). The other languages work but are not benchmarked.

## Glossary: names and special words

Whisper sometimes misspells names, products or jargon. The glossary fixes that. On first use Hams creates

```
%APPDATA%\Hams\glossar.toml
```

from a template (German or English, depending on your dictation language). Open it in any text editor. Changes
apply from the next recording; no restart needed.

```toml
# Words Whisper should know. Put the important ones first; keep the list short.
words = ["Hams", "Kubernetes", "Dr. Nguyen", "ACME Cloud"]

# Fixed corrections "heard = meant". Case on the left doesn't matter; only whole words are replaced.
[replace]
"Cuba Netties" = "Kubernetes"
"Acme Cloud" = "ACME Cloud"
```

- `words` are passed to Whisper as hints, so it recognises them more often. The Ollama cleanup is also told not to
  change these spellings. Whisper only reads roughly the first 200 tokens of hints, so a long list doesn't help.
- `[replace]` is applied to every transcript after recognition, for mishearings that always come out the same way.
- The German key names `woerter` and `ersetzen` work as well; both styles are accepted in either template. Use one
  style per file.

## Optional: Ollama

Without Ollama, Hams pastes exactly what Whisper recognised. That is the fastest option and often good enough.
With Ollama, Hams can:

- **clean up** each dictation (punctuation, filler words, obvious recognition errors), and
- answer spoken questions in **prompt mode**.

To set it up:

1. Install Ollama from [ollama.com/download](https://ollama.com/download).
2. Download a model, for example `ollama pull qwen3.5:9b` (the default Hams expects; it is several gigabytes and
   wants a GPU with enough free VRAM next to Whisper to be quick). Smaller models work too.
3. In *Settings → AI*, choose the cleanup model and the prompt model. The status line there says *Ollama is
   running* when Hams can reach it.

If Ollama is slow (more than 30 s for cleanup) or not running, Hams pastes the raw text instead and says so
(*Pasted · without cleanup*). Hams talks to Ollama at `http://127.0.0.1:11434`. A different address can be set with
`[ollama] url`; anything other than the local machine must use `https://`.

## Uninstall

Windows Settings → Apps → Installed apps → **Hams** → Uninstall.

The uninstaller removes the program folder (`%LOCALAPPDATA%\Programs\Hams`), the Start menu and desktop entries,
and the autostart entry. It then asks whether to delete your data as well:

| Folder | Contents | Deleted |
|---|---|---|
| `%APPDATA%\Hams` | `config.toml`, `glossar.toml`, `setup.json` (setup assistant state) | only if you answer *Yes* |
| `%LOCALAPPDATA%\Hams` | `logs\`, `transcripts\`, `cuda\` (GPU libraries, about 2 GB) | only if you answer *Yes* |
| `%USERPROFILE%\.cache\huggingface\hub` | downloaded Whisper models | **never** |

The Whisper models sit in the shared Hugging Face cache, which other programs may use too. To free that space,
delete the models in *Settings → Transcription → Downloaded models* **before** uninstalling, or delete the
`models--Systran--faster-whisper-*` and `models--mobiuslabsgmbh--faster-whisper-large-v3-turbo` folders in that
cache by hand. Ollama and its models are a separate program; uninstall it separately if you no longer need it.

A silent uninstall (`unins000.exe /SILENT`) keeps all user data.

If you upgraded from an old version that was still called *Wisper*, a folder `%APPDATA%\Wisper` may also exist.
Hams copied it once and never touches it again; you can delete it yourself.

## Privacy

Your audio and your text are processed on your computer. Hams has no account, no telemetry and no analytics. Its
only check-in is an update check that you can turn off (see below).

**What is stored locally**

- Transcripts: every pasted text, as a file in `%LOCALAPPDATA%\Hams\transcripts`, and the last five in memory for
  the tray menu.
- Logs: `%LOCALAPPDATA%\Hams\logs\hams.log` (rotated at 1 MB, three old files kept). The log contains timings,
  settings, model names and text *lengths*, never the dictated text.
- Recordings are not saved to disk. Audio exists in memory only until it is transcribed.
- The clipboard keeps the last pasted text (see [Dictation](#dictation)).

**Network connections Hams makes**

| When | Host | What is sent |
|---|---|---|
| Downloading a Whisper model (first start, or choosing a new one) | `huggingface.co`, which redirects the large file to its download CDN (hosts under `hf.co`) | standard HTTP requests for the public model files; no audio or text |
| Every time a model is loaded (each start, each switch) | `huggingface.co` | a metadata request asking whether the cached model is current. Without internet this fails silently and the cached copy is used |
| `--install-gpu-support` / GPU step of the setup assistant | `files.pythonhosted.org` (PyPI) | download of three pinned NVIDIA wheels (cuBLAS, cuDNN, NVRTC) |
| Cleanup step of the setup assistant, if you start the model download | your local Ollama, which then downloads the model from its own registry (`ollama.com`) | the model name |
| *Get Ollama* in the setup assistant | opens `https://ollama.com/download` in your web browser | nothing from Hams |
| Update check: at start, at most once every 24 hours, unless turned off | `api.github.com` (`/repos/Mosaik114/hams-bachelorprojekt/releases/latest`) | a plain HTTPS request without account or identifier (generic user agent `Hams-update-check`); the answer is the latest version number. Nothing is downloaded; offline or on errors nothing is shown |
| *View* on the update notice | opens the release page on `github.com` in your web browser | nothing from Hams |
| Text cleanup, prompt mode, warm-up at start, status check every 5 s while the settings window is open | `127.0.0.1:11434` (your local Ollama), or the `[ollama] url` you configured | the transcript and the instructions, only to that address |

Nothing else; Hams has no crash reporting. `api.github.com` is the one outbound host the update check adds.
When GitHub lists a newer release, Hams says so once as a tray notification and keeps a calm notice in the tray
menu and under *Settings → General* until you open it or dismiss it (×) for that version. It never downloads or
installs anything itself. The time of the last check and the dismissed version are kept in
`%LOCALAPPDATA%\Hams\update-check.json`. **To turn the check off**, switch off *Settings → General → Check for
updates*, or set `check = false` under `[updates]` in `config.toml`; Hams then makes no request to GitHub at all. If you point
`[ollama] url` at another machine, your transcripts go to that machine; Hams then requires HTTPS.

Ollama itself is a separate program with its own network behaviour (for example `ollama pull` downloads from
`ollama.com`); see Ollama's documentation.
