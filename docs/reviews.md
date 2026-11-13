# Review log

Between v1.0.0 and v1.1.2, Hams went through three rounds of independent review. Each review was a Claude Code agent
started with a fresh context (no memory of how the code was written) and the same fixed brief, from three
perspectives:

- **End user:** install it, use it, try to break it, judge the wording and the docs.
- **Engineering:** read the code, CI and tests; look for races, error handling and maintainability problems.
- **Hiring manager:** judge the repository as evidence of the author's skills: claims, honesty, process.

Each reviewer gave a score from 1 to 100 and a concrete path to 97. I triaged every finding: fix it, reject it with
a reason, or defer it. The agent then implemented the fixes, and CI was the gate.

| Round | End user | Engineering | Hiring | Average | Released as |
|---|---:|---:|---:|---:|---|
| 1 | 84 | 80 | 74 | 79.3 | [1.1.0](../CHANGELOG.md#110--2026-11-13) |
| 2 | 84 | 83 | 84 | 83.7 | [1.1.1](../CHANGELOG.md#111--2026-11-13) |
| 3 | 84 | 87 | 86 | 85.7 | [1.1.2](../CHANGELOG.md#112--2026-11-13) |

The tables below are reconstructed from the fix commits and the changelog; the review texts themselves are not in
the repository. Severity is my triage rating by user impact (high: loses or breaks a dictation or the app).

## Round 1 → 1.1.0

| Finding | Severity | Verdict | Commit |
|---|---|---|---|
| A config value of the wrong type stopped Hams from starting; `check = "false"` counted as true | high | fixed | `6620165` |
| A section header with a comment made saving duplicate a table, which reset all settings | high | fixed | `6620165` |
| Switching models during a load could leave the wrong model loaded, with the UI showing another | high | fixed | `2da6b83` |
| Worker threads called Tk directly (`root.after` blocks; 2 s freeze on re-registering hotkeys) | medium | fixed | `2da6b83` |
| GPU install errors (full disk, no write access) ended in tracebacks | medium | fixed | `2da6b83` |
| Starting Hams again did nothing visible; a missing Ollama model and the glossary needed a terminal or editor | medium | fixed | `9171362` |
| README ordered for developers; sizes differed between docs | low | fixed | `9171362` |
| `linux/setup-system.sh` in German and installing the NVIDIA driver unasked | medium | fixed | `9171362` |
| The installer lacked the English glossary template | low | fixed | `9171362` |

The fixes were merged in `db2b0bb` and `2263842`, together with the cleanup evaluation and the change it led to:
temperature 0 plus an output guard (`3fc84b7`).

**Release slip.** The `v1.1.0` tag was pushed while CI on that commit was red: two tests failed on Linux only. The
installer was attached to the release by hand. The tests were fixed right after (`55208e3`), and from 1.1.1 on a
release can only come from CI: the release job waits for every check and attests the installer's build provenance
(`784f94e`).

## Round 2 → 1.1.1

| Finding | Severity | Verdict | Commit |
|---|---|---|---|
| A dictation started during the previous cleanup could lose its *Ask AI* mode | high | fixed | `883e0c0` |
| Ollama dropping the connection mid-answer lost the dictation | high | fixed | `883e0c0` |
| A glossary value with a backslash broke every dictation; a value containing its key was applied twice | high | fixed | `883e0c0` |
| A failed model load fell back silently and saved the fallback over the user's choice | medium | fixed | `883e0c0` |
| Closing the setup assistant early started a silent 1.6 GB download | medium | fixed | `883e0c0` |
| Two transcripts in the same second overwrote each other; an interrupted GPU download failed with a traceback | low | fixed | `883e0c0` |
| No help inside the app; a bug report needed a terminal | medium | fixed | `a378f97` |
| Linux install was many manual steps; uninstall left the speech models | medium | fixed | `a378f97` |
| README too long, spoke of "twelve" optimisations the repo doesn't document, AI involvement unclear | medium | fixed | `a378f97` |
| Network connections not listed anywhere | low | fixed | `a378f97` |
| Dependabot could not update the hash-locked files; releases not gated by CI | medium | fixed | `784f94e` |
| Coverage gates counted lines only | low | fixed | `784f94e`, `b305532` |

Merged in `c5f3b73`, released in `7ed6cfa`.

## Round 3 → 1.1.2

| Finding | Severity | Verdict | Commit |
|---|---|---|---|
| Starting Hams from the Start menu showed nothing (window hidden, tray icon behind `^`) | high | fixed | `ebdf14e` |
| A glossary saved as ANSI or UTF-16 broke every dictation; one with a BOM was ignored | high | fixed | `ebdf14e` |
| A settings file in another encoding made *Quit*, *Hide* and *Restart* do nothing | high | fixed | `ebdf14e` |
| The stop hotkey was refused when the model failed to load during a recording | high | fixed | `ebdf14e` |
| A late model download overrode a model chosen meanwhile | medium | fixed | `ebdf14e` |
| *Restart* and *Quit* could drop a dictation still being transcribed | medium | fixed | `ebdf14e` |
| *Ask AI* pasted an empty text on an empty or broken answer | medium | fixed | `ebdf14e` |
| Level meter and recording could open the microphone at the same time | low | fixed | `ebdf14e` |
| Python 3.10 claimed, but the locked dependencies need 3.11 | low | fixed | `ebdf14e` |
| README did not mark the 3.09 s → 0.72 s comparison as limited, or explain the reviews | low | fixed | `ebdf14e` |

The installer smoke test still expected the old autostart entry; CI caught it on `main` and `33ffa59` fixed it before
the release (`0acf5b5`).

## Not done, and why

These came up across the rounds and were deliberately left open.

| Finding | Verdict | Reason |
|---|---|---|
| Rename the remaining German identifiers and comments | deferred | Large churn across most modules for little user value; the config code was renamed in `6620165` |
| Replace tests that check source text (`inspect.getsource`, `tests/source_code.py`) with behaviour tests | deferred | They still run in CI; replacing them is a refactor of its own |
| Code-sign the installer | deferred | Certificates cost money every year and need approval; SmartScreen handling is documented in [troubleshooting](troubleshooting.md) |
| Evaluate WER and cleanup on real speech | deferred | Needs hand-transcribed dictations of my own; on the [roadmap](../README.md#roadmap) |
| A stricter German cleanup prompt against "execute instead of clean" | rejected | Measured: worse German WER and new failure modes ([evaluation](evaluation.md#llm-cleanup-quality)) |
| Single-instance mutex under `Local\` instead of `Global\` | rejected | One instance per machine on purpose: the Whisper model sits on the one GPU, and older versions check the `Global\` names (comment in `hams/instance.py`) |
| Offer the English-only `.en` Whisper models | rejected | Every offered model speaks all languages, so switching the dictation language never means switching or downloading a model (`hams/models.py`) |
