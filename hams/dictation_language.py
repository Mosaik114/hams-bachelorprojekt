"""Diktiersprache: welche Sprache Whisper erkennt und welche Anweisungen dazu passen.

Getrennt von der Sprache der Oberfläche (`i18n`): wer eine englische Oberfläche nutzt, kann trotzdem Deutsch
diktieren und umgekehrt. Ohne Eintrag in der Konfiguration folgt die Diktiersprache aber der Oberfläche
(`default_setting`), damit ein neuer Nutzer nichts einstellen muss.

Pro Sprache gehören vier Dinge zusammen:

- der Rahmensatz für die Glossar-Hinweise an Whisper (`hotword_frame`). Whisper nimmt den Hinweis als „vorigen
  Text“; er muss deshalb in der diktierten Sprache stehen, sonst zieht er die Ausgabe in eine andere Sprache.
- der Glossar-Satz für die Bereinigung (`glossary_hint`),
- die Systemanweisung für den Prompt-Modus (`plain_system`),
- die Anweisung für die Bereinigung (`cleanup_template`).

Deutsch bleibt Wort für Wort, wie es war — die Messungen der Bachelorarbeit hängen daran. Für die übrigen Sprachen
sind die Anweisungen an Ollama englisch und nennen die Sprache ausdrücklich; Sprachmodelle befolgen englische
Anweisungen am zuverlässigsten. Nur der Whisper-Rahmensatz steht in der jeweiligen Sprache selbst.

"auto" lässt Whisper die Sprache jeder Aufnahme erkennen. Die Anweisungen richten sich dann nach der erkannten
Sprache; für eine Sprache ohne eigene Anweisungen gelten die englischen (`assets_language`).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from hams import i18n

AUTO = "auto"
#: Sprachen mit eigenem Whisper-Rahmensatz und eigener Auswahl in den Einstellungen.
SUPPORTED = ("de", "en", "fr", "es", "it", "nl", "pl", "pt")
#: Erlaubte Werte von `transcription.language`.
SETTINGS = (AUTO, *SUPPORTED)
#: Anweisungen für jede erkannte Sprache ohne eigene.
FALLBACK = "en"

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
#: Die deutsche Bereinigungsanweisung. Der Dateiname bleibt aus der Zeit vor der Sprachwahl:
#: bestehende Konfigurationen verweisen mit `cleanup_prompt_file` genau auf ihn.
GERMAN_CLEANUP_FILE = PROMPTS_DIR / "cleanup.txt"
#: Die englische Vorlage für alle anderen Sprachen; `{language}` wird durch den Sprachnamen ersetzt.
ENGLISH_CLEANUP_FILE = PROMPTS_DIR / "cleanup.en.txt"

#: Rückfall, wenn keine Vorlagendatei konfiguriert ist (unverändert aus config.py übernommen).
DEFAULT_CLEANUP_PROMPT = (
    "Bereinige den folgenden transkribierten deutschen Text. "
    "Entferne Stotter, Wiederholungen und Versprecher. "
    "Korrigiere Rechtschreibung und Grammatik. "
    "Behalte die ursprüngliche Formulierung so nah wie möglich bei — "
    "keine Umschreibungen, nur eine saubere Fassung von dem was gesagt wurde. "
    "Zensiere KEINE Wörter und ersetze NICHTS inhaltlich — schreibe jeden Begriff exakt so, "
    "wie er gesagt wurde, egal welchen Inhalt er hat. "
    "Antworte NUR mit dem korrigierten Text, ohne Erklärungen oder Anmerkungen.\n\n"
    "Text: {text}"
)

#: Englische Namen für die Anweisungen an Ollama.
#: Eine erkannte Sprache ohne Namen hier heisst dort neutral „its original language“ — auf keinen Fall „English“,
#: sonst übersetzte das Modell eine japanische Aufnahme womöglich.
ENGLISH_NAMES = {
    "de": "German",
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
}

#: Whisper nimmt den Hinweis als „vorigen Text“. Eine bloße Liste arabischer und englischer Wörter zog die Ausgabe
#: ins Englische („The Prophet“ statt „der Prophet“) – ein Rahmensatz in der diktierten Sprache hält sie dort.
HOTWORD_FRAMES = {
    "de": "Ein deutscher Text. Namen und Begriffe darin: {}.",
    "en": "An English text. Names and terms in it: {}.",
    "fr": "Un texte en français. Noms et termes qu'il contient : {}.",
    "es": "Un texto en español. Nombres y términos que contiene: {}.",
    "it": "Un testo in italiano. Nomi e termini che contiene: {}.",
    "nl": "Een Nederlandse tekst. Namen en begrippen daarin: {}.",
    "pl": "Tekst po polsku. Nazwy i pojęcia w nim: {}.",
    "pt": "Um texto em português. Nomes e termos que contém: {}.",
}

_GERMAN_GLOSSARY_HINT = "Diese Wörter sind richtig geschrieben und bleiben genau so: "
_ENGLISH_GLOSSARY_HINT = "These words are spelled correctly and must stay exactly as written: "

#: Systemanweisung im Prompt-Modus: die Antwort wird unverändert eingefügt, also Klartext.
_GERMAN_PLAIN_SYSTEM = (
    "Antworte in normalem Klartext. Verwende kein Markdown: keine Sternchen "
    "oder Unterstriche zur Hervorhebung, keine Fett- oder Kursivschrift, keine "
    "Überschriften, keine Backticks und keine Codeblöcke. Deine Antwort wird "
    "unverändert in ein Textfeld eingefügt."
)
_ENGLISH_PLAIN_SYSTEM = (
    "Reply in plain text, in the language of the user's message. Do not use Markdown: no asterisks "
    "or underscores for emphasis, no bold or italics, no headings, no backticks and no code blocks. "
    "Your reply is pasted unchanged into a text field."
)


def normalise_setting(value: object) -> str | None:
    """Ein gültiger Wert für `transcription.language`, oder None für einen unbekannten."""
    text = str(value or "").strip().lower()
    return text if text in SETTINGS else None


def default_setting(ui_setting: object, platform: str | None = None, environ=None) -> str:
    """Die Diktiersprache ohne eigenen Eintrag: dieselbe wie die Oberfläche (deutsches System → "de", sonst "en")."""
    return i18n.resolve_language(ui_setting, platform, environ)


def whisper_language(setting: str) -> str | None:
    """Der Wert für faster-whisper: None lässt es die Sprache selbst erkennen."""
    return None if setting == AUTO else setting


def assets_language(language: str | None) -> str:
    """Die Sprache, deren Anweisungen gelten: die eigene, wenn es sie gibt, sonst Englisch."""
    return language if language in SUPPORTED else FALLBACK


def hotword_frame(language: str | None) -> str | None:
    """Rahmensatz für die Glossar-Hinweise, oder None für eine Sprache ohne eigenen.

    Bewusst kein englischer Rückfall: ein englischer Rahmen zöge eine japanische Aufnahme ins Englische. Ohne
    Rahmen bekommt Whisper dann gar keinen Hinweis; die festen Korrekturen gelten trotzdem.
    """
    return HOTWORD_FRAMES.get(language or "")


def glossary_hint(language: str | None, words) -> str:
    """Satz für die Bereinigung: diese Schreibweisen gelten als richtig. Leer ohne Wörter."""
    if not words:
        return ""
    start = _GERMAN_GLOSSARY_HINT if assets_language(language) == "de" else _ENGLISH_GLOSSARY_HINT
    return start + ", ".join(words) + "."


def plain_system(language: str | None) -> str:
    """Systemanweisung für den Prompt-Modus."""
    return _GERMAN_PLAIN_SYSTEM if assets_language(language) == "de" else _ENGLISH_PLAIN_SYSTEM


@cache
def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _is_builtin_german(template: str) -> bool:
    """Ist das die mitgelieferte deutsche Anweisung (und nicht eine eigene des Nutzers)?"""
    text = template.strip()
    return bool(text) and text in (_read(GERMAN_CLEANUP_FILE).strip(), DEFAULT_CLEANUP_PROMPT.strip())


def cleanup_template(language: str | None, configured: str) -> str:
    """Die Bereinigungsanweisung für eine Sprache.

    Deutsch bekommt genau die konfigurierte Vorlage, wie bisher. Eine eigene Vorlage des Nutzers
    (`cleanup_prompt_file`) gilt für jede Sprache — wer sie schreibt, weiss, was er diktiert. Nur die
    mitgelieferte deutsche wird für andere Sprachen durch die englische ersetzt.
    """
    if assets_language(language) == "de" or not _is_builtin_german(configured):
        return configured
    english = _read(ENGLISH_CLEANUP_FILE)
    if not english:
        return configured
    return english.replace("{language}", ENGLISH_NAMES.get(language or "", "its original language"))


def glossary_default_language(setting: str) -> str:
    """Welche Glossar-Vorlage ein neuer Nutzer bekommt: "de" oder "en".

    Bei "auto" entscheidet die Oberfläche — die Sprache der ersten Aufnahme ist beim Anlegen noch unbekannt.
    """
    language = i18n.language() if setting == AUTO else setting
    return "de" if language == "de" else "en"
