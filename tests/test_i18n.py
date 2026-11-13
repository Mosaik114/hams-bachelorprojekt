"""The i18n layer: catalogs, language choice, lookup and the guard against strings that bypass the catalog."""

import ast
import ctypes
import re
import string
import sys
from types import SimpleNamespace

import pytest
from source_code import PACKAGE_DIR, module_files

from hams import i18n
from hams.messages_de import MESSAGES as DE
from hams.messages_en import MESSAGES as EN


def placeholders(text: str) -> set[str]:
    """Names of the `str.format` fields in a message."""
    return {name for _literal, name, _spec, _conv in string.Formatter().parse(text) if name is not None}


# ---------------------------------------------------------------- catalogs


class TestCatalogs:
    def test_both_catalogs_have_exactly_the_same_keys(self):
        assert set(DE) - set(EN) == set(), "only in German"
        assert set(EN) - set(DE) == set(), "only in English"

    @pytest.mark.parametrize("key", sorted(EN))
    def test_placeholders_match(self, key):
        assert placeholders(DE[key]) == placeholders(EN[key]), key

    @pytest.mark.parametrize("key", sorted(EN))
    def test_no_message_is_empty(self, key):
        assert DE[key].strip() and EN[key].strip()

    def test_plural_keys_come_in_pairs(self):
        stems = {key.rsplit(".", 1)[0] for key in EN if key.endswith((".one", ".other"))}
        assert stems
        for stem in stems:
            assert f"{stem}.one" in EN and f"{stem}.other" in EN, stem

    def test_english_catalog_contains_no_german(self):
        """Language names are the only exception: they are shown in their own language."""
        endonyms = {"language.de", "language.en"}
        offenders = [f"{key}: {text}" for key, text in EN.items() if key not in endonyms and looks_german(text)]
        assert not offenders, "\n".join(offenders)

    def test_every_whisper_model_has_a_summary(self):
        from hams import models

        for info in models.CATALOG:
            assert f"model.summary.{info.model_id}" in EN


# ---------------------------------------------------------------- lookup


class TestLookup:
    def test_follows_the_active_language(self):
        i18n.set_language("en")
        assert i18n.t("tray.quit") == "Quit"
        i18n.set_language("de")
        assert i18n.t("tray.quit") == "Beenden"

    def test_fills_placeholders(self, english):
        assert i18n.t("status.ready", model="Turbo") == "Ready · Turbo"

    def test_unknown_key_returns_the_key(self):
        assert i18n.t("no.such.key") == "no.such.key"

    def test_missing_translation_falls_back_to_english(self, monkeypatch):
        monkeypatch.setitem(i18n.CATALOGS, "de", {})
        assert i18n.t_for("de", "tray.quit") == "Quit"

    def test_unknown_language_uses_english(self):
        assert i18n.t_for("fr", "tray.quit") == "Quit"

    @pytest.mark.parametrize(("count", "text"), [(0, "0 models · 1 GB"), (1, "1 model · 1 GB"), (2, "2 models · 1 GB")])
    def test_plural(self, english, count, text):
        assert i18n.tn("settings.storage.summary", count, size="1 GB") == text

    def test_german_plural(self):
        assert i18n.tn("settings.storage.summary", 1, size="1 GB") == "1 Modell · 1 GB"
        assert i18n.tn("settings.storage.summary", 3, size="1 GB") == "3 Modelle · 1 GB"

    def test_decimal_separator(self):
        assert i18n.decimal(1.62) == "1,6"
        i18n.set_language("en")
        assert i18n.decimal(1.62) == "1.6"

    def test_language_is_resolved_lazily(self, monkeypatch):
        monkeypatch.setattr(i18n, "_active", None)
        monkeypatch.setattr(i18n, "detect_system_language", lambda *a: "en")
        assert i18n.language() == "en"

    def test_text_descriptor_translates_on_access(self):
        class Holder:
            HINT = i18n.Text("settings.model.hint")
            TABLE = i18n.Text(lambda: {"x": i18n.t("tray.quit")})

        assert Holder.HINT == "Tempo und Genauigkeit"
        assert Holder().TABLE == {"x": "Beenden"}
        i18n.set_language("en")
        assert Holder.HINT == "Speed and accuracy"
        assert Holder.TABLE == {"x": "Quit"}


# ---------------------------------------------------------------- language resolution


class TestResolution:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("auto", "auto"), ("de", "de"), ("EN", "en"), (" de ", "de"), ("fr", "auto"), ("", "auto"), (None, "auto")],
    )
    def test_normalise_setting(self, value, expected):
        assert i18n.normalise_setting(value) == expected

    def test_explicit_setting_wins_over_the_system(self):
        env = {"LANG": "de_DE.UTF-8"}
        assert i18n.resolve_language("en", platform="linux", environ=env) == "en"
        assert i18n.resolve_language("de", platform="linux", environ={"LANG": "en_US.UTF-8"}) == "de"

    @pytest.mark.parametrize(
        ("environ", "expected"),
        [
            ({"LANG": "de_DE.UTF-8"}, "de"),
            ({"LANG": "de_AT.UTF-8"}, "de"),
            ({"LANG": "en_GB.UTF-8"}, "en"),
            ({"LANG": "fr_FR.UTF-8"}, "en"),
            ({"LANG": "C.UTF-8"}, "en"),
            ({}, "en"),
            ({"LC_ALL": "de_DE.UTF-8", "LANG": "en_US.UTF-8"}, "de"),
            ({"LC_ALL": "en_US.UTF-8", "LANG": "de_DE.UTF-8"}, "en"),
            ({"LC_MESSAGES": "de_CH.UTF-8", "LANG": "en_US.UTF-8"}, "de"),
            ({"LC_ALL": "", "LANG": "de_DE.UTF-8"}, "de"),
        ],
    )
    def test_auto_on_linux_reads_the_locale(self, environ, expected):
        assert i18n.resolve_language("auto", platform="linux", environ=environ) == expected

    @pytest.mark.parametrize(
        ("langid", "expected"),
        [(0x0407, "de"), (0x0C07, "de"), (0x0807, "de"), (0x0409, "en"), (0x040C, "en")],
    )
    def test_auto_on_windows_reads_the_ui_language(self, monkeypatch, langid, expected):
        kernel32 = SimpleNamespace(GetUserDefaultUILanguage=lambda: langid)
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)
        assert i18n.resolve_language("auto", platform="win32") == expected

    def test_windows_without_an_answer_falls_back_to_english(self, monkeypatch):
        def broken():
            raise OSError("no")

        monkeypatch.setattr(
            ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(GetUserDefaultUILanguage=broken)), raising=False
        )
        assert i18n.resolve_language("auto", platform="win32") == "en"

    def test_set_language_returns_the_concrete_language(self, monkeypatch):
        monkeypatch.setattr(i18n, "detect_system_language", lambda *a: "de")
        assert i18n.set_language("auto") == "de"
        assert i18n.set_language("en") == "en"
        assert i18n.language() == "en"


# ---------------------------------------------------------------- configuration


class TestConfig:
    def test_default_template_names_the_option(self):
        text = (PACKAGE_DIR / "config.default.toml").read_text(encoding="utf-8")
        assert re.search(r'^language = "auto"$', text, re.MULTILINE)

    def test_default_is_auto(self):
        from hams.config import Config

        assert Config().ui_language == "auto"

    @pytest.mark.parametrize(
        ("value", "expected"), [("de", "de"), ("en", "en"), ("Auto", "auto"), ("klingonisch", "auto")]
    )
    def test_loading(self, value, expected):
        from hams.config import Config, _apply_toml

        cfg = Config()
        _apply_toml(cfg, {"ui": {"language": value}})
        assert cfg.ui_language == expected

    def test_unknown_value_is_reported(self, caplog):
        from hams.config import Config, _apply_toml

        with caplog.at_level("WARNING", logger="hams"):
            _apply_toml(Config(), {"ui": {"language": "klingonisch"}})
        assert "ui.language" in caplog.text

    def test_missing_key_keeps_the_default(self):
        from hams.config import Config, _apply_toml

        cfg = Config()
        _apply_toml(cfg, {"ui": {}})
        assert cfg.ui_language == "auto"


# ---------------------------------------------------------------- strings that bypass the catalog

#: Umlauts and ß, or frequent German words that hardly occur in English product text.
_GERMAN = re.compile(
    r"[äöüÄÖÜß]|\b(und|oder|nicht|nichts|kein|keine|keinen|wird|werden|ist|sind|bitte|bereit|läuft|Fehler|Aufnahme"
    r"|Einstellungen|Modell|Modelle|Mikrofon|Neustart|Sprache|Zeichen|Gerät|erneut|abgebrochen|gelöscht|geladen"
    r"|wurde|Eintrag|Befehl|Dienst|Tastatur|zuletzt|Diktieren|Beenden|Verlauf|Hell|Dunkel|Allgemein|Kurzbefehl"
    r"|Kurzbefehle|Löschen|Speicher|Signal|Pegel|Zwischenablage)\b"
)


def looks_german(text: str) -> bool:
    return bool(_GERMAN.search(text))


#: Literals that are German on purpose. Each entry: (module relative to hams/, start of the string).
ALLOWLIST = {
    # Instructions for German *dictation*, not for the interface (all dictation languages live in one module).
    ("dictation_language.py", "Bereinige den folgenden transkribierten deutschen Text."),
    ("dictation_language.py", "Antworte in normalem Klartext."),
    ("dictation_language.py", "Ein deutscher Text."),
    ("dictation_language.py", "Diese Wörter sind richtig geschrieben"),
    # The Whisper frame sentences of the other dictation languages (umlauts and the like trip the German check).
    ("dictation_language.py", "Un texte en français."),
    ("dictation_language.py", "Un texto en español."),
    ("dictation_language.py", "Tekst po polsku."),
    ("dictation_language.py", "Um texto em português."),
    # Patterns the cleanup guard matches against dictated text and model answers; never shown.
    ("cleanup_guard.py", "\\A[ \\t]*(?P<line>"),
    ("cleanup_guard.py", "äh"),
    ("cleanup_guard.py", "äähm"),
    ("cleanup_guard.py", "öhm"),
    # Internal result codes, only written to the log.
    ("ui_kit/scale.py", "keine"),
    ("ui_kit/window.py", "keine"),
}

_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}

#: Calls and keywords whose string arguments end up on screen (or on the console).
_UI_KEYWORDS = {"text", "tooltip", "placeholder", "description", "label", "meta", "trailing", "hint", "detail", "group"}
_UI_POSITIONS = {
    "MenuItem": (0,),
    "print": (0,),
    "MessageBoxW": (1,),
    "notify": (1,),
    "set_description": (0,),
    "set_status": (1,),
    "set_value": (0,),
    "_apply_state": (1, 2),
    "SettingRow": (1,),
    "SecondaryButton": (1,),
    "SectionHeader": (1,),
    "Option": (1,),
    "title": (0,),
}
_WORD = re.compile(r"[^\W\d_]{2,}")


def _ignored_nodes(tree: ast.AST) -> set:
    """Docstrings and everything inside a logging call (log messages may stay as they are)."""
    ignored = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            ignored.add(id(node.value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_METHODS:
            ignored.update(id(inner) for inner in ast.walk(node))
    return ignored


def _strings(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node
    elif isinstance(node, ast.JoinedStr):
        yield from (part for part in node.values if isinstance(part, ast.Constant))


def _call_name(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def _ui_arguments(call: ast.Call):
    """String arguments of a call that reach the screen. `print(..., file=...)` is diagnostics, not UI."""
    name = _call_name(call)
    if name == "print" and any(keyword.arg == "file" for keyword in call.keywords):
        return
    for index in _UI_POSITIONS.get(name, ()):
        if index < len(call.args):
            yield from _strings(call.args[index])
    for keyword in call.keywords:
        if keyword.arg in _UI_KEYWORDS:
            yield from _strings(keyword.value)


def _source_files():
    return [path for path in module_files() if not path.name.startswith("messages_")]


def _relative(path) -> str:
    return path.relative_to(PACKAGE_DIR).as_posix()


def _allowed(module: str, text: str) -> bool:
    return any(module == allowed and text.startswith(start) for allowed, start in ALLOWLIST)


def german_literals() -> list:
    """Every non-docstring, non-log string literal outside the catalogs that looks German."""
    found = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        ignored = _ignored_nodes(tree)
        module = _relative(path)
        for node in ast.walk(tree):
            if id(node) in ignored or not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if looks_german(node.value) and not _allowed(module, node.value):
                found.append(f"{module}:{node.lineno}: {node.value[:80]!r}")
    return found


def literal_ui_texts() -> list:
    """Word-bearing string literals passed straight to widgets, the tray menu or the console."""
    found = []
    for path in _source_files():
        module = _relative(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            for literal in _ui_arguments(node):
                if _WORD.search(literal.value) and not _allowed(module, literal.value):
                    found.append(f"{module}:{literal.lineno}: {_call_name(node)}({literal.value!r})")
    return found


class TestNoStringBypassesTheCatalog:
    def test_no_german_literal_outside_the_catalogs(self):
        offenders = german_literals()
        assert not offenders, "Move these into hams/messages_*.py:\n" + "\n".join(offenders)

    def test_no_literal_text_is_handed_to_the_interface(self):
        offenders = literal_ui_texts()
        assert not offenders, "Use i18n.t() for these:\n" + "\n".join(offenders)

    def test_the_scanner_finds_a_planted_literal(self, tmp_path, monkeypatch):
        """Guard the guard: both checks must notice a string that skips the catalog."""
        planted = tmp_path / "planted.py"
        planted.write_text('row.set_description("Wird geladen")\nlabel(text="Hello there")\n', encoding="utf-8")
        monkeypatch.setattr(sys.modules[__name__], "_source_files", lambda: [planted])
        monkeypatch.setattr(sys.modules[__name__], "_relative", lambda path: path.name)
        assert len(german_literals()) == 1
        assert len(literal_ui_texts()) == 2

    def test_docstrings_and_log_messages_are_ignored(self, tmp_path, monkeypatch):
        planted = tmp_path / "planted.py"
        planted.write_text('"""Modul für Tests."""\nlogger.info("Aufnahme läuft: %s", x)\n', encoding="utf-8")
        monkeypatch.setattr(sys.modules[__name__], "_source_files", lambda: [planted])
        monkeypatch.setattr(sys.modules[__name__], "_relative", lambda path: path.name)
        assert german_literals() == []

    @pytest.mark.parametrize(("module", "start"), sorted(ALLOWLIST))
    def test_allowlist_entries_still_exist(self, module, start):
        """A stale allowlist entry would silently permit a future German string."""
        assert start in (PACKAGE_DIR / module).read_text(encoding="utf-8")
