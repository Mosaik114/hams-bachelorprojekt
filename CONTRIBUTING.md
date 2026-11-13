# Contributing

Thanks for taking an interest. Bug reports, ideas and pull requests are welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- Identifiers and docs are English. Every user-visible string lives in `hams/messages_en.py` and
  `hams/messages_de.py` (same keys, same placeholders) and is looked up with `hams.i18n.t()`; `tests/test_i18n.py`
  fails on literals that bypass the catalogs.
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

## Releasing to winget (manual)

`packaging/winget/manifest/` holds a manifest set for the package `Mosaik114.Hams` (schema 1.10: version,
installer, defaultLocale), matching the Inno Setup installer: `InstallerType: inno`, `Scope: user`, silent
switches, and the uninstall key `{AppId}_is1` as product code. The templates carry the version from
`pyproject.toml` and an all-zero placeholder hash. Nothing is submitted automatically; submitting needs a published
GitHub release and a pull request to [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs):

1. Tag and publish the release (CI attaches `Hams-Setup-<version>.exe`) and download that exact file. The hash
   must be the one of the published asset, not of a local rebuild.
2. Fill in version, URL and SHA-256:

   ```powershell
   .\packaging\winget\update-manifest.ps1 -Setup "$HOME\Downloads\Hams-Setup-1.1.0.exe" -Validate
   ```

   This writes `build\winget\manifests\m\Mosaik114\Hams\<version>\` (the winget-pkgs layout) and runs
   `winget validate` on it. The templates stay unchanged.
3. Optionally try it: `winget settings --enable LocalManifestFiles` (once, in an admin shell), then
   `winget install --manifest build\winget\manifests\m\Mosaik114\Hams\<version>`.
4. Fork microsoft/winget-pkgs, copy the folder to the same path there and open a pull request (or use
   `wingetcreate submit` with that folder). The winget bots run their own validation and install tests.

For later versions, repeat step 2 with the new installer; once the package exists, `wingetcreate update
Mosaik114.Hams` works as well. `tests/test_winget.py` keeps the templates in line with `pyproject.toml` and
`hams.iss`.

## Reporting security issues

Please don't open a public issue. See [SECURITY.md](SECURITY.md).
