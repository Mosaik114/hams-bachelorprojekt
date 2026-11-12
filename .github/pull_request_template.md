## What and why

<!-- What does this change, and which problem does it solve? Link the issue: Fixes #123 -->

## How it was tested

<!-- Platforms you ran it on, new or changed tests, manual checks -->

## Checklist

- [ ] `ruff check . && ruff format --check .` passes
- [ ] `pytest tests` passes (headless: `xvfb-run` on Linux, separate desktop on Windows)
- [ ] Tests added or updated for changed behaviour
- [ ] User-visible changes noted in `CHANGELOG.md` under *Unreleased*
- [ ] No dictated text in logs, and no personal data (paths, names, recordings) in the diff
