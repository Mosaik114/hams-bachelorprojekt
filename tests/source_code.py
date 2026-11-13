"""The Python files of the `hams` package, for the few rules that scan the whole code base.

Only project-wide conventions are checked this way (strings go through the catalogs, colours through the theme,
identifiers are English); single functions are tested by their behaviour, never by their source text.
"""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "hams"


def module_files(package: Path = PACKAGE_DIR) -> list:
    return sorted(path for path in package.rglob("*.py") if "__pycache__" not in path.parts)
