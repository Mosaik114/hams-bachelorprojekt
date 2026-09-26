"""Quelltext von Hams fuer Pruefungen, die eine Regel am Code selbst festhalten.

Regeln, die fuer das ganze Programm gelten, pruefen alle Module; wer eine
einzelne Funktion meint, holt sich deren Quelltext mit `inspect.getsource` -
so haengt kein Test daran, in welcher Datei eine Funktion gerade liegt.
"""
from pathlib import Path

WISPER = Path(__file__).resolve().parent.parent / "wisper"


def module_files() -> list:
    return sorted(WISPER.glob("*.py"))


def all_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in module_files())
