"""Print the CHANGELOG.md section of one version, for the body of its GitHub release.

Usage: python .github/scripts/release_notes.py <version> [changelog] > notes.md

<version> may carry a leading "v" (the tag name). Exits with an error if the section is missing or empty, so a
release is never published without notes. Standard library only: CI runs it before installing anything.
"""

import re
import sys
from pathlib import Path


def section(text: str, version: str) -> str:
    """Return the body between "## [<version>]" and the next "## " heading, stripped."""
    heading = re.compile(rf"^## \[{re.escape(version)}\]", re.MULTILINE)
    match = heading.search(text)
    if match is None:
        raise SystemExit(f"CHANGELOG has no section for {version}")
    newline = text.find("\n", match.end())
    start = len(text) if newline == -1 else newline + 1
    following = re.compile(r"^## ", re.MULTILINE).search(text, start)
    body = text[start : following.start() if following else len(text)].strip()
    if not body:
        raise SystemExit(f"CHANGELOG section for {version} is empty")
    return body


def main(argv: list[str]) -> None:
    if len(argv) not in (2, 3):
        raise SystemExit(__doc__)
    version = argv[1].removeprefix("v")
    changelog = Path(argv[2] if len(argv) == 3 else "CHANGELOG.md")
    notes = section(changelog.read_text(encoding="utf-8"), version) + "\n"
    sys.stdout.buffer.write(notes.encode("utf-8"))  # UTF-8 whatever the console code page


if __name__ == "__main__":
    main(sys.argv)
