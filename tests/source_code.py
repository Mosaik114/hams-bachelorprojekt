"""Quelltext von Hams fuer Pruefungen, die eine Regel am Code selbst festhalten.

Regeln, die fuer das ganze Programm gelten, pruefen alle Module; wer eine
einzelne Funktion meint, holt sich deren Quelltext mit `inspect.getsource` -
so haengt kein Test daran, in welcher Datei eine Funktion gerade liegt.
"""
from pathlib import Path

WISPER = Path(__file__).resolve().parent.parent / "wisper"


def module_files(package: Path = WISPER) -> list:
    return sorted(path for path in package.rglob("*.py") if "__pycache__" not in path.parts)


def all_source(package: Path = WISPER) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in module_files(package))


def ui_kit_source() -> str:
    """Das ganze UI-Kit - es ist auf mehrere Module verteilt."""
    return all_source(WISPER / "ui_kit")
