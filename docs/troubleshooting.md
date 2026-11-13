# Troubleshooting and FAQ

Most problems leave a trace in the log. Start there:

```
%LOCALAPPDATA%\Hams\logs\hams.log
```

(*Settings → About & help → Log folder → Open*, or paste that path into the Explorer address bar; on Linux
`~/.local/share/hams/logs/hams.log`). The log records timings, settings, device and model names and
the *length* of each text, never the dictated text itself. Files rotate at 1 MB; `hams.log.1` to `hams.log.3` are
older.

- [The hotkey does nothing](#the-hotkey-does-nothing)
- [Text is recognised but not pasted](#text-is-recognised-but-not-pasted)
- [Windows blocks the installer or Hams.exe](#windows-blocks-the-installer-or-hamsexe)
- [The GPU is not used](#the-gpu-is-not-used)
- [Transcription is slow](#transcription-is-slow)
- [Ollama is not reachable](#ollama-is-not-reachable)
- [Wrong language or translated text](#wrong-language-or-translated-text)
- [The microphone is silent](#the-microphone-is-silent)
- [The model download fails](#the-model-download-fails)
- [The model doesn't load](#the-model-doesnt-load)
- [Linux](#linux)
- [Reporting a bug](#reporting-a-bug)

## The hotkey does nothing

**Is Hams running and ready?** Look for the icon in the notification area (it may be under the `^` overflow
arrow). The window must say *Ready*; while it says *Loading model …* a hotkey press only shows *Model is still
loading …*.

**Another program uses the same combination.** `Ctrl+Shift+Space` is also used by some editors and IDEs
(for example parameter hints in VS Code), by some input method editors (IMEs) for Chinese, Japanese or Korean, and
by some games and overlays. Hams registers its hotkeys with the Windows `RegisterHotKey` function. If another program
registered the combination first, that fails; Hams then falls back to a keyboard hook, which can make both programs
react, or, if that fails too, shows *Shortcut unavailable — Couldn't register "…". Another program is probably using
it.* The log contains `Hotkey registered: … (System)` when the normal registration worked.

Conversely, while Hams is running, the combination no longer reaches other applications.

To change the hotkeys, open *Settings → Shortcuts*, press **Change** and press the new combination; it works right
away and is saved to `config.toml` (details in the [user guide](user-guide.md#changing-the-hotkeys)). Hams checks
the combination first and says why it can't take it: reserved by Windows (for example `Alt+Tab`, `Win+L`,
`Ctrl+V`), already the other Hams hotkey, or *in use by another app*. Combinations with `win` are often reserved by
Windows. On Linux, change them in *System Settings → Keyboard → Shortcuts*.

You can still edit `[hotkeys]` in `%APPDATA%\Hams\config.toml` by hand (quit Hams first):

```toml
[hotkeys]
transcript = "ctrl+alt+d"
prompt = "ctrl+alt+p"
```

Modifiers are `ctrl`, `shift`, `alt` and `win`; the key is a letter, a digit, `F1` … `F24`, `space`, `enter`,
`tab`, `insert`, `delete`, `home`, `end`, `page up`, `page down`, `pause` or an arrow key. An invalid entry shows
*Can't register "…"* with the path of the file to fix.

**Hotkeys worked and then stopped** (for example after standby or a long session): open *Settings → Shortcuts* and
press **Restart**. That starts a fresh Hams process with fresh hotkey registrations. It refuses while a recording
or a model download is in progress.

## Text is recognised but not pasted

Hams copies the text to the clipboard and then sends `Ctrl+V` to whatever window has keyboard focus. If the status
says *Pasted* but nothing appeared:

- **Focus moved.** The text goes to the window that has focus *when transcription finishes*, not when you started
  speaking. Don't switch windows while it says *Transcribing …*.
- **The target window runs as administrator.** Windows does not let a normal program send keystrokes into a program
  that runs elevated (User Interface Privilege Isolation). Typical cases: an "Administrator" terminal, the Registry
  Editor, Task Manager, installers, some games and remote-desktop clients. The text is still on the clipboard; press
  `Ctrl+V` yourself. Running Hams itself as administrator is not recommended.
- **The field ignores `Ctrl+V`** (some terminals use `Ctrl+Shift+V`, some web forms block pasting). Paste manually
  from the clipboard, or pick the text from tray icon → *Recent transcripts*.
- **The window says *Copied, not pasted*.** Hams waits up to `[clipboard] paste_timeout` seconds (default `0.3`)
  for the clipboard to confirm the new text. Without that confirmation it does not send `Ctrl+V`: it would paste
  whatever was on the clipboard before. Press `Ctrl+V` yourself. If this happens often (clipboard managers and
  remote-desktop sessions can be slow), raise the value, for example to `1.0`.

The last five texts stay in tray icon → *Recent transcripts* until Hams quits. To also keep them as files, switch on
*Settings → General → Save transcripts*.

## Windows blocks the installer or Hams.exe

Hams is not code-signed. Code-signing certificates cost money every year, and this is a free open-source project.

- **SmartScreen** ("Windows protected your PC"): click **More info → Run anyway**.
- **Smart App Control** (Windows 11, *Windows Security → App & browser control*): when it is *On*, it blocks
  unsigned programs it doesn't know, and there is no "Run anyway" button. The only options are turning Smart App
  Control off (on some Windows versions it cannot be switched on again without resetting Windows, so think about
  that first) or running Hams from source (see [development.md](development.md#run-from-source)).
- **Antivirus false positives.** Programs packed with PyInstaller are sometimes flagged by heuristics. You can check
  the installer you downloaded against the release page, or build it yourself with `packaging\build.ps1`.

Only allow a file that you downloaded from the project's GitHub release page.

## The GPU is not used

Check what Hams uses: *Settings → Transcription → Hardware* shows *Automatic · GPU* or *Automatic · CPU*. The log
line `Model ready (cuda/float16)` or `Model ready (cpu/int8)` says the same.

1. **NVIDIA only.** Hams uses CUDA; AMD and Intel GPUs are not supported, Hams uses the CPU on those machines.
2. **Driver.** Install a current NVIDIA driver from nvidia.com or through the NVIDIA app. The bundled libraries are
   CUDA 12, which needs a reasonably recent driver; RTX 50-series cards need a driver from 2025 or newer.
3. **GPU libraries.** The installer does not include cuBLAS and cuDNN (1.37 GB download, about 2 GB unpacked). If the log says
   `CUDA libraries missing - skipping the GPU, using the next device`, download them once and restart Hams:

   ```powershell
   Start-Process -Wait -NoNewWindow "$env:LOCALAPPDATA\Programs\Hams\Hams.exe" -ArgumentList '--install-gpu-support'
   ```

   It prints `No NVIDIA driver found - Hams will keep using the CPU.` when the driver is missing, and `Done: … Restart
   Hams to use the GPU.` on success. An interrupted download resumes on the next attempt.
4. **Self-test.** *Settings → About & help → Diagnostic report → Create* runs it and opens the report. Without the
   settings window, run in PowerShell:

   ```powershell
   Start-Process -Wait "$env:LOCALAPPDATA\Programs\Hams\Hams.exe" -ArgumentList '--self-test','--report',"`"$env:TEMP\hams-report.txt`""
   notepad "$env:TEMP\hams-report.txt"
   ```

   The `backend` line should read `CUDA devices 1, CUDA libraries found`, and the `gpu support` line `NVIDIA driver
   yes, downloaded libraries <path>`. The self-test downloads nothing, records nothing and opens no window.
5. **Not enough VRAM.** If the GPU is busy (games, a large Ollama model), loading in `float16` can fail. Hams then
   tries `int8_float16`, `int8`, and finally the CPU. Each failed attempt is logged as `Model load failed: …`. A
   smaller Whisper model or a smaller Ollama model helps; see [hardware.md](hardware.md).

From source, the libraries come from `pip install -e ".[cuda]"` instead.

## Transcription is slow

On the CPU, the large models need several seconds per sentence. [hardware.md](hardware.md) has measured numbers.

- Pick a smaller model in *Settings → Transcription → Whisper model*: *Small* or *Base* on a CPU.
- Set the dictation language explicitly instead of *Automatic*; detection costs an extra pass.
- Turn off text cleanup (*Settings → AI*, or `[ollama] cleanup_enabled = false`) if Ollama runs on the CPU too.
  Cleanup can take longer than transcription.
- The very first dictation after a start can be slower while everything warms up.

## Ollama is not reachable

Ollama is optional. Without it, dictation still works and pastes the raw text (*Pasted · without cleanup*). Only
*Ask AI* (prompt mode) needs it.

- *Settings → AI* shows *Ollama is running* or *Ollama isn't reachable — raw text will be pasted*.
- Is Ollama started? It normally runs in the tray after installation. Test it in a browser:
  `http://127.0.0.1:11434` should answer *Ollama is running*.
- Is the selected model installed? The settings list installed models and mark the selected one *Not installed*
  if it is missing; *Settings → AI* then shows a *Download* button that fetches it through Ollama with a progress
  bar. In a terminal, `ollama list` shows the models and `ollama pull qwen3.5:9b` fetches the default.
- Installed Ollama after the first start? Run the setup assistant again (*Settings → General → Setup assistant →
  Run again*); it finds Ollama, downloads the model and switches cleanup on.
- Hams uses `127.0.0.1`, not `localhost`, on purpose (see [architecture.md](architecture.md#design-decisions)). If
  you run Ollama on a different port or machine, set `[ollama] url`, for example
  `url = "http://127.0.0.1:11500/api/generate"`. Other machines must be reached over `https://`.
- The first cleanup after starting Ollama can take 10–30 s while the model loads. Hams warms the model up at start
  (when cleanup is on) and keeps it loaded for 30 minutes (`keep_alive`). Cleanup gives up after 30 s and
  pastes the raw text.
- After Ollama could not be reached, Hams pastes raw text for the next 60 seconds without asking it again, so dictation
  doesn't keep waiting for a service that is down. Cleanup resumes by itself after that.
- If you don't want Ollama at all, switch cleanup off; Hams then doesn't contact it for dictation.

## Wrong language or translated text

- **Set the dictation language** in *Settings → Transcription → Dictation language*. With *Automatic*, Whisper
  guesses the language of each recording; for one or two words that guess can be wrong. The log shows
  `Detected language: xx (p=…)`.
- The dictation language is separate from the interface language. Changing the interface to English does not change
  what you dictate.
- If the text comes back translated, that is the LLM cleanup, not Whisper. Hams tells Ollama to keep the spoken
  language and pastes the raw text when an answer keeps less than half of the dictated words, but small models
  sometimes slip through. Try another cleanup model or turn cleanup off.
- Names and jargon spelled wrong: add them to the glossary ([user guide](user-guide.md#glossary-names-and-special-words)).

## The microphone is silent

Symptoms: *No microphone signal*, *No speech detected*, or *"…" isn't picking up any sound. Is the microphone muted?*

- Open *Settings → Recording* and watch the *Input level* while you speak. It should move and show *Good signal*.
- Choose the right device there instead of *System default*, especially with headsets, webcams and docking stations.
- Windows privacy: *Settings → Privacy & security → Microphone* must allow microphone access, **including** *Let
  desktop apps access your microphone*.
- Check the hardware mute switch and the input volume in *Settings → System → Sound → Input*.
- *"…" isn't responding. Choose another device in Settings.* means the device disappeared (unplugged, Bluetooth
  disconnected, or taken exclusively by another app). Pick it again or choose another one.

## The model download fails

- *Can't reach the model server*: Hams needs `huggingface.co` once per model. Check proxies and firewalls.
- *Not enough disk space — needs …*: the model goes to `%USERPROFILE%\.cache\huggingface\hub`, on the system
  drive. Free up space or choose a smaller model.
- A cancelled or broken download continues where it stopped when you select the model again. Leftovers show up under
  *Downloaded models* as *incomplete* and can be deleted there.

## The model doesn't load

The main window says *Couldn't load the model* with *"…" couldn't be loaded on the GPU or the CPU*, or, after
choosing another model, *Couldn't load …* with *Still using …* (Hams keeps the previous model). A dictation recorded
meanwhile ends with *Model unavailable — The recording couldn't be transcribed.*

- **A corrupt or incomplete download.** Open *Settings → Transcription → Downloaded models*, delete the model there
  (*Delete …*; an unfinished one shows *incomplete*), then select it again under *Whisper model* to download it
  afresh. *Try again* in the main window loads it once more.
- **Not enough graphics memory (VRAM).** Large models need several GB on the GPU, more while a game or another AI
  tool is running. Close those, or choose a smaller model; [hardware.md](hardware.md) lists the sizes. Hams then
  tries the CPU by itself, which is slower but works.
- **Not enough disk space.** Downloads stop with *Not enough disk space — needs …, … free* or *The disk is full*.
  Free up space on the system drive and select the model again.
- **Antivirus software.** A scanner that locks or quarantines model files while they are written leaves a broken
  download. Allow `%USERPROFILE%\.cache\huggingface\hub`, delete the model as above and download it again.

For the exact cause, create a diagnostic report (*Settings → About & help → Diagnostic report → Create*); it
contains the end of the log with the error from the GPU or CPU load.

## Linux

The log is `~/.local/share/hams/logs/hams.log` (or `$XDG_DATA_HOME/hams/logs`), the settings are in
`~/.config/hams`. Run the self-test from the repository folder:

```bash
.venv/bin/python -m hams --self-test --report ~/hams-report.txt
```

**Text is recognised but not pasted.** Wayland doesn't let programs send keystrokes to other windows, so Hams pastes
through its own virtual keyboard on `/dev/uinput`. Check that you can open it:

```bash
test -w /dev/uinput && echo "uinput: ok" || echo "uinput: no access"
```

*no access* means the system setup hasn't run or you haven't rebooted since: run `sudo bash linux/setup-system.sh`
and reboot. The log then shows `/dev/uinput: Permission denied`. The text is still on the clipboard; paste it with
`Ctrl+V` yourself.

**Nothing on the clipboard, or `wl-copy` not found.** On Wayland, Hams copies with `wl-copy` from the `wl-clipboard`
package.
`command -v wl-copy` must print a path; if it doesn't, run `sudo apt install wl-clipboard` (the system setup installs
it).

**The hotkey does nothing.** On Linux the hotkeys go through the XDG Desktop Portal (*GlobalShortcuts*), and KDE asks
once to confirm them.

- *KDE never asked:* Hams needs its application menu entry for that. Run
  `.venv/bin/python hams/setup_autostart.py`, quit Hams and start it again from the menu.
- *You declined, or want other keys:* open *System Settings → Keyboard → Shortcuts*, find Hams, and assign or
  change the two shortcuts there.
- *Another desktop:* the portal must support *GlobalShortcuts* (KDE Plasma 6 does; other desktops may not).

**No tray icon.** The tray needs `gir1.2-ayatanaappindicator3-0.1` (installed by the system setup). Use
`linux/show-hams.sh` to bring the window to the front.

## Reporting a bug

1. **Create a diagnostic report:** *Settings → About & help → Diagnostic report → Create*. Hams checks the
   installation, saves `%LOCALAPPDATA%\Hams\hams-report.txt` (Linux: `~/.local/share/hams/hams-report.txt`) and
   opens it. The report contains version numbers, paths, device names and the last lines of the log, never dictated
   text.

   If the settings don't open, run the self-test in PowerShell instead:

   ```powershell
   Start-Process -Wait "$env:LOCALAPPDATA\Programs\Hams\Hams.exe" -ArgumentList '--self-test','--report',"`"$env:TEMP\hams-report.txt`""
   notepad "$env:TEMP\hams-report.txt"
   ```

   The quotes inside the argument list are needed: `Start-Process` joins the arguments with spaces and does not
   quote them, so a user name with a space would split the path. From source: `python -m hams --self-test --report
   hams-report.txt`.
2. Open a [bug report](https://github.com/Mosaik114/hams-bachelorprojekt/issues/new/choose) and fill in the form:
   what happened, steps to reproduce, Hams version, Windows version, GPU and models.
3. Attach the report (and, if it matters, more of `%LOCALAPPDATA%\Hams\logs\hams.log`). Look through it before
   posting: it contains your user name in file paths and the names of your devices.

Never post dictated text, recordings or transcript files. Security problems go through
[SECURITY.md](../SECURITY.md), not public issues.
