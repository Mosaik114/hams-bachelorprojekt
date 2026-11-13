# Hardware requirements and speed

Hams runs on any 64-bit Windows 10 or 11 PC. How fast it feels depends mostly on one thing: whether Whisper can run
on an NVIDIA GPU. This page lists what Hams needs, shows measured timings for every Whisper model in the settings,
and recommends a model for typical machines.

## Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 or 11, 64-bit | Windows 11 |
| CPU | any 64-bit x86 CPU | 6 or more cores, 2020 or newer |
| RAM | 4 GB free for *Base*/*Small*; 8 GB for *Turbo* | 16 GB, more if Ollama runs on the same machine |
| GPU | none (CPU works) | NVIDIA GPU with 4 GB VRAM or more and a current driver |
| Disk | about 0.5 GB (app + *Base*) | about 4 GB (app + *Turbo* + GPU libraries) |
| Microphone | any input device Windows recognises | a headset or USB microphone close to your mouth |

Windows on ARM is not supported. AMD and Intel GPUs are not used (Whisper runs on the CPU there). Ollama is optional and has its
own requirements; a 7–9 B model wants roughly 6–8 GB of VRAM to be quick.

## Disk space

| Item | Size | Where |
|---|---:|---|
| Hams (installed) | ~257 MB | `%LOCALAPPDATA%\Programs\Hams` |
| GPU libraries (cuBLAS, cuDNN, NVRTC), optional | 1.37 GB download, about 2 GB unpacked | `%LOCALAPPDATA%\Hams\cuda` |
| Whisper *Tiny* | 78 MB | `%USERPROFILE%\.cache\huggingface\hub` |
| Whisper *Base* | 148 MB | same |
| Whisper *Small* | 486 MB | same |
| Whisper *Medium* | 1.53 GB | same |
| Whisper *Turbo* (default) | 1.62 GB | same |
| Whisper *Large v3* | 3.09 GB | same |

Model sizes are the exact download sizes from [`hams/models.py`](../hams/models.py). Hams checks for free space
(model size plus 20 %) before a download. On Windows without Developer Mode the Hugging Face cache cannot use
symbolic links and stores plain copies; that doesn't change the sizes above for a single download. Downloaded
models can be deleted in *Settings → Transcription → Downloaded models*.

## Measured speed

**Machine:** AMD Ryzen 7 9800X3D (8 cores), 31 GB RAM, NVIDIA GeForce RTX 5070 (12 GB, driver 621.01), Windows 11,
faster-whisper 1.2.1, CTranslate2 4.8.2. This is a fast desktop; treat the numbers as indicative, not as a promise
for your PC.

**Method** ([script](#reproducing-the-measurement)): two synthetic recordings made with the Windows speech
synthesizer (System.Speech): an English sentence pair of 12.1 s and a German one of 15.2 s, 16 kHz mono. Settings
as in Hams: `beam_size = 1`, VAD on, language set explicitly, no timestamps, no previous-text conditioning. Each
model was loaded in a fresh process, run once to warm up, then three times per language; the table shows the median.
GPU: `device="cuda"`, `compute_type="float16"`. CPU: `device="cpu"`, `compute_type="int8"`, with faster-whisper's
default of 4 CPU threads, the same as Hams.

**Real-time factor (RTF)** = processing time ÷ audio length. 0.10 means ten seconds of speech take one second.

| Model | GPU time EN / DE | GPU RTF | CPU time EN / DE | CPU RTF | Load time GPU / CPU | VRAM (GPU) | Process RAM GPU / CPU |
|---|---:|---:|---:|---:|---:|---:|---:|
| Tiny | 0.06 / 0.09 s | 0.005 | 0.13 / 0.15 s | 0.010 | 0.8 / 0.7 s | ~0.3 GB | 0.8 / 0.4 GB |
| Base | 0.08 / 0.12 s | 0.007 | 0.24 / 0.27 s | 0.019 | 0.8 / 0.8 s | ~0.4 GB | 0.8 / 0.5 GB |
| Small | 0.16 / 0.23 s | 0.014 | 0.81 / 0.96 s | 0.065 | 1.0 / 1.2 s | ~0.8 GB | 0.9 / 0.9 GB |
| Medium | 0.31 / 0.45 s | 0.028 | 2.32 / 2.76 s | 0.19 | 1.6 / 2.1 s | ~2.1 GB | 1.9 / 2.0 GB |
| **Turbo** | 0.17 / 0.21 s | 0.014 | 2.76 / 2.89 s | 0.21 | 1.8 / 2.3 s | ~2.3 GB | 2.0 / 2.1 GB |
| Large v3 | 0.47 / 0.69 s | 0.042 | 3.89 / 4.64 s | 0.31 | 2.6 / 3.5 s | ~4.0 GB | 3.5 / 3.6 GB |

RTF is the mean of the English and German values.

**Fewer CPU cores.** To approximate a laptop, the CPU run was repeated with only 2 threads:

| Model (CPU, int8, 2 threads) | Time EN / DE | RTF |
|---|---:|---:|
| Tiny | 0.18 / 0.19 s | 0.014 |
| Base | 0.35 / 0.40 s | 0.028 |
| Small | 1.15 / 1.33 s | 0.091 |
| Turbo | 4.30 / 4.47 s | 0.32 |

A real older laptop has slower cores than a current desktop limited to two threads, so expect it to be slower still,
possibly by a factor of two to four.

**What the numbers show**

- On the GPU, every model is faster than you can notice: even *Large v3* needs well under a second for 15 s of
  speech. *Turbo* is about as fast as *Small* there, because its decoder is much smaller than *Large v3*'s.
- On the CPU, *Turbo* and *Large v3* take 3–5 s for a 12–15 s dictation. That is usable but noticeable. *Small*
  stays around one second, *Base* and *Tiny* well below.
- Time grows roughly with the length of the recording. A one-minute dictation on the CPU with *Turbo* takes on the
  order of 10–15 s.
- This is Whisper only. Text cleanup with Ollama comes on top (0.8 s median on the thesis machine with a 7 B model
  on the GPU, see [evaluation.md](evaluation.md); much longer on a CPU).

**Accuracy.** Synthetic speech is too clean to rank the models: all of them transcribed the English sample without
an error, and the German differences were almost only spelling variants (*Team-Meeting* vs. *Teammeeting*,
*3 Uhr* vs. *drei Uhr*, *Donnerstag nach Mittag* vs. *Donnerstag Nachmittag*). The one real mistake was *Tiny* on the
GPU garbling *Budgetzahlen*. With real voices, accents, background noise and jargon, the larger models make
noticeably fewer errors; the thesis benchmark with noise is in [evaluation.md](evaluation.md). No accuracy numbers
are given here on purpose.

**Memory notes.** *VRAM* is the increase in used GPU memory after the model was loaded and used, read with
`nvidia-smi`. *Process RAM* is the peak working set of the benchmark process; the GPU runs include the CUDA
runtime, which is why *Tiny* uses more RAM on the GPU than on the CPU. Hams itself needs a little more for its window and
tray icon. If Ollama runs on the same GPU, its model needs VRAM as well; with 8 GB of VRAM, *Turbo* plus a 7–9 B
Ollama model can be tight, and Hams then falls back to `int8_float16`, `int8` or the CPU.

**Caveats.** One machine, one run of three repetitions, synthetic speech, short recordings. Another Hams instance was
idle on the same GPU during the GPU runs. The first transcription after loading a model is slower (warm-up), and the
first ever load includes the download.

## Recommendations

| Your computer | Model | Why |
|---|---|---|
| NVIDIA GPU with 4 GB VRAM or more | **Turbo** (default) | well under half a second per sentence, near *Large v3* accuracy |
| NVIDIA GPU with 8 GB or more, accuracy matters most | Large v3 | slower than *Turbo* but still under a second; uses about 4 GB VRAM |
| NVIDIA GPU with 2–4 GB VRAM | Small, or Turbo if nothing else uses the GPU | leaves room for the display and other apps |
| Modern desktop or laptop CPU (6+ cores, 2020 or newer), no NVIDIA GPU | **Small** | about one second per sentence; step up to *Turbo* if you prefer accuracy over speed and can wait 3–5 s |
| Older laptop, 4 cores or fewer, 8 GB RAM | **Base** | stays responsive; *Small* if your dictations are short |
| Very weak hardware or a quick test | Tiny | fastest, but makes visibly more mistakes |

Further tips for slow machines:

- Set the dictation language explicitly; *Automatic* runs an extra detection pass.
- Turn off the Ollama cleanup unless you have a GPU for it.
- Keep recordings short; time grows with length.

With an NVIDIA GPU, download the GPU libraries once (setup assistant, or `Hams.exe --install-gpu-support`); without
them, Hams runs on the CPU even if a GPU is present. See [troubleshooting.md](troubleshooting.md#the-gpu-is-not-used).

## Reproducing the measurement

1. Create two WAV files (16 kHz, 16-bit, mono) with any text of 10–15 s, for example with PowerShell's
   `System.Speech.Synthesis.SpeechSynthesizer` and `SetOutputToWaveFile` with a `SpeechAudioFormatInfo(16000, Sixteen,
   Mono)`.
2. In a Python environment with `pip install -e ".[cuda]"`, load each model with
   `WhisperModel(model, device="cuda", compute_type="float16")` or `device="cpu", compute_type="int8"`, call
   `hams.backend_preload.preload_backend_without_torch()` first so the CUDA libraries are found, and time
   `model.transcribe(audio, language=..., beam_size=1, vad_filter=True, condition_on_previous_text=False,
   without_timestamps=True)` after one warm-up run, consuming the returned segments.

For a larger, noise-controlled benchmark with WER, see `tools/wer_benchmark.py` and [evaluation.md](evaluation.md).
