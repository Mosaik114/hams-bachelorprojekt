# Contributing

Thanks for taking an interest. Bug reports, ideas and pull requests are welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- Identifiers and docs are English. The UI is German today; keep new UI strings next to the existing ones.
- Never log dictated text, only its length.

## Development setup

Python 3.10 or newer. With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv venv .venv --python 3.11
uv pip install -r requirements-dev.txt   # exactly the locked versions CI uses (uv picks up .venv)
uv pip install --no-deps -e .
```

Or with plain pip:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux: source .venv/bin/activate
pip install --require-hashes -r requirements-dev.txt
pip install --no-deps -e .
```

Add the `[cuda]` extra (`pip install -e ".[cuda]"`) to transcribe on an NVIDIA GPU. On Linux, run
`sudo bash linux/setup-system.sh` once for the system packages and the `/dev/uinput` permission.

## Tests

```bash
pytest tests --cov=hams
```

About 1,600 tests; the coverage gate is 85 %. Many tests open real Tk windows, so run them headless:

- **Linux:** `xvfb-run -a pytest tests` (as in CI).
- **Windows:** run the suite on a separate, invisible desktop (for example via `CreateDesktop`), or accept that
  windows briefly flash on screen. Windows CI uses `--cov-config=coverage-windows.ini`, which leaves out the
  Linux-only modules.

The suite never touches your real configuration: `tests/conftest.py` points `HAMS_CONFIG` and `HAMS_LOG_DIR` at
temporary folders before `hams.config` is imported. Keep it that way for new tests, and never let a test read or
write `%APPDATA%\Hams`, `~/.config/hams` or the old `Wisper` folders.

Some tests read source code with `inspect.getsource`; update their search strings when you refactor.

## Lint

```bash
ruff check . && ruff format --check .
```

Rules are in `ruff.toml`; the maximum cyclomatic complexity per function is 10.

## Dependencies

`requirements.txt` and `requirements-dev.txt` are generated with hashes from `pyproject.toml`. After changing a
dependency, regenerate both with the `uv pip compile` command at the top of each file and commit them together.

## Commits and pull requests

- One logical change per commit. Subject line in English, imperative, without a trailing period, optionally with
  an area prefix: `Linux: bring the window to the front via SIGUSR1`, `CI: pin ruff`, `Docs: …`.
- Add or adjust tests for behaviour you change.
- Describe user-visible changes in `CHANGELOG.md` under an *Unreleased* heading.
- Every pull request must pass the same checks as `main`: ruff, tests on Windows and Linux with the coverage gate,
  `pip-audit`, and the package build.

## Reporting security issues

Please don't open a public issue. See [SECURITY.md](SECURITY.md).
