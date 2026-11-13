# Contributing

Thanks for taking an interest. Bug reports, ideas and pull requests are welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- Identifiers and docs are English. Every user-visible string lives in `hams/messages_en.py` and
  `hams/messages_de.py` (same keys, same placeholders) and is looked up with `hams.i18n.t()`; `tests/test_i18n.py`
  fails on literals that bypass the catalogs.
- Never log dictated text, only its length.

## Development setup

Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/) (recommended):

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

About 3,200 tests; coverage counts lines and branches together. The gate is 93 % on Windows
(`coverage-windows.ini`) and 88 % on Linux (`pyproject.toml`), where the Windows-only modules count but mostly
cannot run. Many tests open real Tk windows, so run them headless:

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

`requirements.txt`, `requirements-dev.txt` (both from `pyproject.toml`) and `packaging/requirements-build.txt`
(PyInstaller, constrained by `requirements.txt`) are locked with hashes by `uv pip compile`; the exact command is in
line 2 of each file. Never edit them by hand. After changing a dependency in `pyproject.toml`, re-lock all three
with uv 0.13.6 (the version CI pins) and commit them together:

```bash
bash .github/scripts/lock.sh             # keep the current pins, resolve what changed
bash .github/scripts/lock.sh --upgrade   # move every pin to the newest allowed version
```

The CI job *Lock files match pyproject* runs the first command and fails on any difference.

**Updates.** Python dependencies are not updated by Dependabot: it bumps one pin at a time and cannot re-resolve
these files, so a bump that brings a new dependency fails `pip install --require-hashes` (that is how the first
Dependabot pull requests broke). Instead, `.github/workflows/lock-refresh.yml` runs every Monday, re-locks all three
files with `--upgrade` and opens or updates **one** pull request from the branch `deps/lock-refresh`. Because
pull requests opened with `GITHUB_TOKEN` trigger no workflows, it starts CI on that branch through
`workflow_dispatch`; the results show on the pull request's head commit. Run it by hand from the Actions tab, with
*dry run* to only see the changes (runs from a branch other than `main` are always dry runs). Opening the pull
request needs *Settings → Actions → General → Allow GitHub Actions to create and approve pull requests*; without it
the run pushes the branch and fails with a link to open the pull request by hand. If a major upgrade breaks
something, cap it in `pyproject.toml` and run the workflow again. Dependabot still updates the GitHub Actions
(pinned by commit SHA), grouped into one weekly pull request.

## Commits and pull requests

- One logical change per commit. Subject line in English, imperative, without a trailing period, optionally with
  an area prefix: `Linux: bring the window to the front via SIGUSR1`, `CI: pin ruff`, `Docs: …`.
- Add or adjust tests for behaviour you change.
- Describe user-visible changes in `CHANGELOG.md` under an *Unreleased* heading.
- Every pull request must pass the same checks as `main`: ruff, the lock check, tests on Windows and Linux with the
  coverage gate, `pip-audit`, the package build and the Windows installer smoke test.

## Release checklist

A tag push is the only way a release gets its assets. Never create the release or upload the installer by hand:
CI builds it, smoke-tests it, signs its build provenance and publishes it only when every job is green.

1. Bump `version` in `pyproject.toml` and in the three templates in `packaging/winget/manifest/`
   (`tests/test_winget.py` checks they match).
2. In `CHANGELOG.md`, rename *Unreleased* to `## [x.y.z] – YYYY-MM-DD` and add a new empty *Unreleased* above it.
   The CI step *Release notes for the current version* prints the section that becomes the release notes and fails
   if it is missing or empty.
3. Merge to `main` and wait until CI on `main` is green (all jobs).
4. Tag that commit and push the tag: `git tag -a vx.y.z -m "Hams x.y.z" && git push origin vx.y.z`.
5. CI runs again on the tag. The installer job checks the tag against `pyproject.toml`; the *release* job then
   creates the release `Hams x.y.z` with the CHANGELOG section as notes (or, if it already exists, only attaches
   the file), uploads `Hams-Setup-x.y.z.exe` and records a build provenance attestation for it.
6. Check the download: `gh attestation verify Hams-Setup-x.y.z.exe --repo Mosaik114/hams-bachelorprojekt`.

If the tag run fails, do not attach anything by hand. Fix the cause on `main` and release the next patch version;
a published tag is not moved.

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
