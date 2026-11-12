# Evaluation

This summarises the measurements from the Bachelor thesis *"Hams – Entwicklung und messbasierte Optimierung eines
lokalen KI-gestützten Spracheingabesystems für Windows"* (Kiel University, Department of Computer Science,
September 2026). The thesis covers the Windows version only.

**Research questions**

1. How can a local pipeline of speech recognition and LLM cleanup be built?
2. What response times and resource usage result on a typical developer machine?
3. Which improvements can targeted, individually measured changes achieve?

## Setup

| | |
|---|---|
| Hardware | AMD Ryzen 7 9800X3D, 31 GB RAM, NVIDIA GeForce RTX 5070 (12 GB), Windows 11 |
| Runtime | Python 3.11.9, faster-whisper ≥ 1.1 |
| Whisper | `turbo`, CUDA `float16`, `beam_size = 1`, Silero VAD, German, `condition_on_previous_text = false`; batched (batch 8) above 30 s |
| Cleanup LLM | `qwen2.5:7b` (Q4_K_M) via Ollama, `keep_alive = 30m`, warmed up at start, 30 s timeout |

All measurements in this document were taken with `qwen2.5:7b`. The current default cleanup and prompt model is
`qwen3.5:9b` (thinking mode off); it was not part of the thesis measurements.

**Metrics**

- *Response time:* from the log marker `Recording stopped` to `Saved transcript`, which comes after the clipboard copy and the simulated
  Ctrl+V. It does not prove that the text arrived in the target field.
- *WER:* word-level Levenshtein distance divided by the number of reference words, after lower-casing, NFC
  normalisation and punctuation removal. Number words and digits are not normalised ("vierzehn" vs "14" counts as an
  error), so the absolute WER is inflated. Only differences on identical audio are meaningful.

## Headline results

Comparability: **d** = directly comparable, **e** = limited, **n** = not directly comparable.

| Metric | Before | After | |
|---|---:|---:|:-:|
| End-to-end, real dictations, `qwen2.5:7b` with cleanup | p50 3.91 s (n = 43) | p50 **0.86 s** (n = 574) | e |
| End-to-end, real dictations, all | p50 3.09 s (n = 172) | p50 **0.72 s** (n = 678) | e |
| Ollama cleanup round trip | 2.941 s (n = 5) | **0.839 s** (n = 5) | d |
| Ollama status query `probe()` | 4.023 s (n = 5) | **0.016 s** (n = 5) | d |
| Whisper stage, real dictations | p50 0.26 s | p50 0.22 s | e |
| Import `wisper.main` (today `hams.main`) | 1.803 s (n = 5) | **0.298 s** (n = 5) | d |
| Hotkey ready after start | ≈ 3.0 s | **0.38 s** (n = 3) | n |
| Working set after import | 505.9 MB | **74.8 MB** | d |
| `site-packages` size | 6620 MB | 2280 MB | n |
| Whisper model load (log) | p50 2.25 s (n = 21) | p50 4.24 s (n = 26) | e |

Whisper model loading got *slower* after the change. A plausible cause is that the CUDA libraries now load at
model-load time instead of import time, but this was not investigated.

### Response time by pipeline stage (real use)

Real-use logs from a single user. *Before*: 1–8 Sep 2026 (172 dictations). *After*: 8–23 Sep 2026 (678 dictations).
The median recording length was 11.95 s in both windows.

| Stage | Before p50 / p90 | After p50 / p90 |
|---|---:|---:|
| Whisper (captured → transcribed) | 0.26 s / 0.73 s | 0.22 s / 0.64 s |
| Cleanup + paste | 2.75 s / 7.80 s | 0.43 s / 4.03 s |
| **Total** | **3.09 s / 8.46 s** | **0.72 s / 4.41 s** |
| Total, with cleanup | 3.85 s / 8.63 s | 0.86 s / 4.73 s |
| Total, without cleanup | 0.26 s / 0.68 s | 0.24 s / 0.51 s |
| Whisper real-time factor (median) | 0.0216 | 0.0193 |

## Where the time went

Breaking a single cleanup call into Ollama's own timings showed that most of the latency was outside the model
(n = 5 each, median):

| Measurement | Wall clock | Inside the model | Outside |
|---|---:|---:|---:|
| cleanup via `localhost` (before) | 2.941 s | 0.893 s | **2.048 s** |
| cleanup via `127.0.0.1` (after) | 0.839 s | 0.815 s | 0.024 s |
| `ministral-3:8b`, before the fix | 2.75 s | 0.66 s | 2.09 s |
| `qwen2.5:3b`, before the fix | 2.37 s | 0.31 s | 2.06 s |

A socket test confirmed the cause. Connecting to `::1` on a port where nothing listens took about 2,030 ms before
`ConnectionRefused`; connecting to `127.0.0.1:11434` took 0.2 ms. Switching to a smaller model would have saved only
0.38 s.

## Recognition quality (WER)

Corpus: 20 German sentences (219 words) produced by Windows text-to-speech, each clean and with white noise at
20/10/5 dB SNR, giving 80 samples. Reproduce it with `python tools/wer_benchmark.py`, which uses a fixed noise seed.

**`float16` vs `int8_float16`** (`turbo`, RTX 5070):

| Config | clean | 20 dB | 10 dB | 5 dB | Overall | p50 latency |
|---|---:|---:|---:|---:|---:|---:|
| `float16` | 12.33 % | 12.33 % | 12.33 % | 14.61 % | 12.90 % | 0.237 s |
| `int8_float16` | 12.33 % | 12.33 % | 12.79 % | 14.16 % | 12.90 % | 0.218 s |

Same overall WER, 8 % faster, 76 of 80 outputs identical character for character. The project stayed on `float16`
as a precaution.

**Batched vs sequential transcription** (one recording per length, time = median of 3 runs):

| Audio | Sequential | Batched (8) | Gain |
|---|---|---|---|
| 6.2 s | 0.217 s, WER 0.00 % | 0.220 s, WER 0.00 % | none |
| 23.7 s | 0.388 s, WER 19.57 % | 0.386 s, WER 19.57 % | none |
| 47.5 s | 0.815 s, WER 13.10 % | 0.606 s, WER 10.71 % | 25.6 % faster, −2.38 pp |
| 83.8 s | 1.388 s, WER 15.79 % | 0.909 s, WER 14.47 % | 34.5 % faster, −1.32 pp |
| 121.3 s | 2.21 s, WER 13.70 % | 1.22 s, WER 10.96 % | 44.9 % faster, −2.74 pp |

On long audio, sequential decoding dropped words and hallucinated a closing "vielen Dank". This led to the
`BATCHED_MIN_SECONDS = 30` threshold.

## Limitations

- All measurements come from a single machine, and many values rest on few repetitions (n = 3–5).
- WER was measured on synthetic speech (one TTS voice, no room acoustics), so it does not carry over to real
  dictation.
- The before/after log windows differ in length and model mix, and the "with cleanup" split is a heuristic.
- Not measured: WER with vs. without LLM cleanup, beam-size and model-size comparisons, whether the cleanup keeps
  the content intact, VRAM per process, and cold start after the changes.
- Ollama, CUDA and CTranslate2 versions were not recorded.

Most measurements were run by an AI coding agent (Claude Code) under measurement rules written by the author. The
thesis discloses this.
