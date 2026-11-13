"""Code in hams/ speaks English: identifiers and log messages (CLAUDE.md, audit finding H17).

The interface texts live in the catalogs (`messages_de.py`, `messages_en.py`, checked by test_i18n.py); German
comments may stay. What is checked here is what a reader of the code or of the log meets: names of modules'
functions, classes, arguments, variables and attributes, and the messages written to the log.

A project-wide AST rule, like the catalog rule in test_i18n.py: no single function is pinned by its source.
"""

import ast
import re
import sys

import pytest
from source_code import PACKAGE_DIR, module_files

#: German words that turned up in identifiers. Only words that are not also English ("links", "kind", "rest", "die",
#: "was", "alt" are), so an English name can never trip it.
_WORDS = """
    abliefern ablauf aktiv aktuell anfrage anfragen angemeldet anmelden anzeige aufnahme aufruf aufrufe aufzeichnung
    ausloesen befehl bestand bild datei dateien dauer eingestellt einstellung einstellungen
    eintraege eintrag erfolg ergebnis
    erlaubt erstellt erwartet fehler fenster fertig frei gebatchte gebunden gedrueckt gefunden geladen gemerkt gesamt
    gescheitert gesehen groesse grund heruntergeladen hinweis inhalt kinder knopf kurzbefehl kurzbefehle laden liste
    loeschbar loesen meldung modell nachbearbeiten namen optionen ordner orte paket pfad protokoll punkte quelle
    reihenfolge revisionen rumpf schalter schleife schluessel speichern sprache strategie teil unterordner
    verzeichnis vollstaendig warten wert werte wurzel zeichen zeichnen zeiger zeile ziel zustand
"""
GERMAN_WORDS = frozenset(_WORDS.split())

#: Deliberate exceptions: (module relative to hams/, identifier). Keep it tiny and say why.
ALLOWLIST: set[tuple[str, str]] = set()

#: A log message that looks German: umlauts or a German word that English never uses.
_GERMAN_LOG = re.compile(
    r"[äöüÄÖÜß]|\b(nicht|liess|liessen|konnte|wurde|wird|und|oder|keine?|gefunden|geladen|geloescht|gewählt|"
    r"angelegt|fehlgeschlagen|abgelehnt|abgebrochen|unter|nach|beim)\b"
)

_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


def words(name: str) -> list[str]:
    """`_paket_verzeichnis` -> ["paket", "verzeichnis"]; `GebatchteAufzeichnung` -> ["gebatchte", "aufzeichnung"]."""
    return [part.lower() for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", name)]


def defined_names(tree: ast.AST):
    """(name, line) of everything the module itself names: defs, classes, arguments, assignments, attributes set."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node.name, node.lineno
        elif isinstance(node, ast.arg):
            yield node.arg, node.lineno
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node.id, node.lineno
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            yield node.attr, node.lineno
        elif isinstance(node, ast.alias) and node.asname:
            yield node.asname, getattr(node, "lineno", 0)


def _relative(path) -> str:
    return path.relative_to(PACKAGE_DIR).as_posix()


def _source_files():
    return module_files()


def german_identifiers() -> list[str]:
    found = []
    for path in _source_files():
        module = _relative(path)
        for name, line in defined_names(ast.parse(path.read_text(encoding="utf-8"))):
            if (module, name) in ALLOWLIST:
                continue
            german = [word for word in words(name) if word in GERMAN_WORDS]
            if german:
                found.append(f"{module}:{line}: {name} ({', '.join(german)})")
    return found


def german_log_messages() -> list[str]:
    found = []
    for path in _source_files():
        module = _relative(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
                continue
            message = node.args[0]
            if node.func.attr not in _LOG_METHODS or not isinstance(message, ast.Constant):
                continue
            if isinstance(message.value, str) and _GERMAN_LOG.search(message.value):
                found.append(f"{module}:{node.lineno}: {message.value[:80]!r}")
    return found


class TestEnglishCode:
    def test_there_is_something_to_check(self):
        assert sum(1 for path in _source_files() for _ in defined_names(ast.parse(path.read_text("utf-8")))) > 1000

    def test_no_german_identifier(self):
        offenders = german_identifiers()
        assert not offenders, "Rename these to English:\n" + "\n".join(offenders)

    def test_no_german_log_message(self):
        offenders = german_log_messages()
        assert not offenders, "The log is English:\n" + "\n".join(offenders)

    def test_allowlist_entries_still_exist(self):
        """A stale entry would silently permit a German name later. (Empty today: nothing needs an exception.)"""
        for module, name in sorted(ALLOWLIST):
            tree = ast.parse((PACKAGE_DIR / module).read_text(encoding="utf-8"))
            assert name in {found for found, _line in defined_names(tree)}, (module, name)


class TestTheScanner:
    """Guard the guard: both checks notice what they are meant to notice, and only that."""

    @pytest.fixture
    def planted(self, tmp_path, monkeypatch):
        def plant(source: str):
            path = tmp_path / "planted.py"
            path.write_text(source, encoding="utf-8")
            monkeypatch.setattr(sys.modules[__name__], "_source_files", lambda: [path])
            monkeypatch.setattr(sys.modules[__name__], "_relative", lambda p: p.name)

        return plant

    def test_german_names_are_found_wherever_they_are_defined(self, planted):
        planted(
            "class GebatchteAufzeichnung:\n"
            "    def _abliefern(self, datei, *, ziel=None):\n"
            "        self.meldung = groesse = 1\n"
            "import os as paket\n"
        )
        names = [entry.split(": ")[1].split(" (")[0] for entry in german_identifiers()]
        assert sorted(names) == sorted(
            ["GebatchteAufzeichnung", "_abliefern", "datei", "ziel", "meldung", "groesse", "paket"]
        )

    def test_english_names_that_look_alike_pass(self, planted):
        planted("def links(kind, rest, die, alt):\n    was = also = hell = 0\n")
        assert german_identifiers() == []

    def test_german_log_messages_are_found(self, planted):
        planted('logger.info("Modell %s geloescht")\nlogger.warning("Glossar nicht lesbar")\nlogger.info("Model %s")\n')
        assert len(german_log_messages()) == 2
