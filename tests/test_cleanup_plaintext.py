"""Tests der Klartext-Normalisierung nach dem Cleanup.

Die Regel ist eng: entfernt werden ein Codeblock um die ganze Antwort und die
üblichen Hervorhebungsklammern. Alles andere bleibt stehen — gerade Sternchen
und Unterstriche, die zum Inhalt gehören.
"""

import ast

import pytest

from wisper import main as wm
from wisper.main import plain_text


class TestBold:
    @pytest.mark.parametrize("value,expected", [
        ("**Wort**", "Wort"),
        ("Das ist **wichtig**.", "Das ist wichtig."),
        ("**mehrere Wörter am Stück**", "mehrere Wörter am Stück"),
        ("Am **Anfang** und am **Ende**.", "Am Anfang und am Ende."),
        ("__Text__", "Text"),
        ("Ein __hervorgehobener__ Teil.", "Ein hervorgehobener Teil."),
    ])
    def test_the_wrapper_disappears(self, value, expected):
        assert plain_text(value) == expected


class TestItalic:
    @pytest.mark.parametrize("value,expected", [
        ("*Wort*", "Wort"),
        ("Ein *wichtiger* Satz.", "Ein wichtiger Satz."),
        ("_Wort_", "Wort"),
        ("Ein _wichtiger_ Satz.", "Ein wichtiger Satz."),
        ("*mehrere Wörter*", "mehrere Wörter"),
    ])
    def test_the_wrapper_disappears(self, value, expected):
        assert plain_text(value) == expected


class TestCombined:
    @pytest.mark.parametrize("value,expected", [
        ("***dreifach***", "dreifach"),
        ("___dreifach___", "dreifach"),
        ("**fett** und *kursiv*", "fett und kursiv"),
    ])
    def test_both_at_once(self, value, expected):
        assert plain_text(value) == expected


class TestNotMarkdown:
    """Was wie Markdown aussieht, aber keines ist."""

    @pytest.mark.parametrize("value", [
        "2 * 3 = 6",
        "*.txt",
        "foo*bar",
        "datei_name.txt",
        "a__b__c",
        "5 * 4 * 3",
        "Suche nach *.log im Ordner",
        "snake_case_name",
        "Der Stern * steht allein.",
        "3*4=12",
        "C:\\pfad\\zur_datei.txt",
    ])
    def test_it_stays_exactly_as_it_is(self, value):
        assert plain_text(value) == value

    def test_inline_backticks_belong_to_the_user(self):
        text = "Er nutzt `ls -la` im Terminal."
        assert plain_text(text) == text

    def test_a_bare_dunder_is_a_known_limit(self):
        """`__init__` sieht genauso aus wie fettes `__Wort__`.

        Beides ist ohne Kontext nicht zu unterscheiden, und `__Wort__` zu
        entklammern ist die geforderte Regel. Der Eingang ist ein diktierter
        deutscher Satz — dort steht kein Python-Bezeichner. Die Zahl steht
        hier, damit die Entscheidung sichtbar bleibt.
        """
        assert plain_text("__init__") == "init"
        assert plain_text("Die Methode __init__ wird gerufen.") == (
            "Die Methode init wird gerufen.")


class TestCodeFence:
    def test_a_fence_around_everything_goes(self):
        assert plain_text("```text\nDies ist mein Satz.\n```") == "Dies ist mein Satz."

    def test_a_fence_without_a_language_goes(self):
        assert plain_text("```\nZwei Zeilen\nbleiben zwei.\n```") == (
            "Zwei Zeilen\nbleiben zwei.")

    def test_a_fence_in_the_middle_stays(self):
        text = "Davor.\n```\ncode\n```\nDanach."
        assert plain_text(text) == text

    def test_the_content_of_a_fence_is_not_further_touched(self):
        assert plain_text("```\n2 * 3 = 6\n```") == "2 * 3 = 6"


class TestShape:
    def test_several_lines_stay_several_lines(self):
        text = "Erste Zeile.\nZweite Zeile.\nDritte Zeile."
        assert plain_text(text) == text

    def test_emphasis_survives_across_lines(self):
        assert plain_text("**Erste**\n**Zweite**") == "Erste\nZweite"

    def test_surrounding_whitespace_goes(self):
        assert plain_text("  \n Ein Satz. \n ") == "Ein Satz."

    def test_an_empty_answer_stays_empty(self):
        assert plain_text("") == ""

    def test_a_plain_sentence_is_untouched(self):
        text = "Dies ist die letzte Abnahme von Hams."
        assert plain_text(text) == text

    def test_umlauts_and_punctuation_survive(self):
        text = "Grüße, Übung — „Anführungszeichen“ und 100 % davon."
        assert plain_text(text) == text


class TestPosition:
    """Die Normalisierung sitzt zwischen Ollama und allen Ausgabewegen."""

    def test_cleanup_normalises_its_answer(self, monkeypatch):
        app = wm.FloatingTranscriberApp.__new__(wm.FloatingTranscriberApp)
        monkeypatch.setattr(wm.FloatingTranscriberApp, "_ollama_generate",
                            lambda self, **kwargs: "Das ist **wichtig**.")
        monkeypatch.setattr(wm.CFG, "cleanup_prompt_template", "Korrigiere: {text}")
        assert app._cleanup_text("das ist wichtig") == "Das ist wichtig."

    def test_the_prompt_mode_is_plain_too(self, monkeypatch):
        """Auch die KI-Antwort wird unveraendert eingefuegt."""
        app = wm.FloatingTranscriberApp.__new__(wm.FloatingTranscriberApp)
        monkeypatch.setattr(wm.FloatingTranscriberApp, "_ollama_generate",
                            lambda self, **kwargs: "Die Antwort ist **42**.")
        assert app._generate_from_prompt("Frage") == "Die Antwort ist 42."

    def test_the_prompt_mode_keeps_its_paragraphs(self, monkeypatch):
        """Zeilenumbrueche tragen dort Bedeutung — Hervorhebungen nicht."""
        app = wm.FloatingTranscriberApp.__new__(wm.FloatingTranscriberApp)
        antwort = "Erstens: **Oslo**.\nZweitens: Bergen.\n\nSoweit dazu."
        monkeypatch.setattr(wm.FloatingTranscriberApp, "_ollama_generate",
                            lambda self, **kwargs: antwort)
        assert app._generate_from_prompt("Frage") == (
            "Erstens: Oslo.\nZweitens: Bergen.\n\nSoweit dazu.")

    def test_the_dictated_prompt_goes_out_untouched(self, monkeypatch):
        """Die Regel steht im Systemfeld, nicht im Prompt des Nutzers."""
        app = wm.FloatingTranscriberApp.__new__(wm.FloatingTranscriberApp)
        gesehen = {}
        monkeypatch.setattr(wm.FloatingTranscriberApp, "_ollama_generate",
                            lambda self, **kwargs: gesehen.update(kwargs) or "ok")
        app._generate_from_prompt("Nenne die Hauptstadt von Norwegen.")
        assert gesehen["prompt"] == "Nenne die Hauptstadt von Norwegen."
        assert "Klartext" in gesehen["system"]
        assert "Markdown" in gesehen["system"]

    def test_the_cleanup_does_not_send_a_system_instruction(self, monkeypatch):
        """Dort steht die Regel bereits im Prompt selbst."""
        app = wm.FloatingTranscriberApp.__new__(wm.FloatingTranscriberApp)
        gesehen = {}
        monkeypatch.setattr(wm.FloatingTranscriberApp, "_ollama_generate",
                            lambda self, **kwargs: gesehen.update(kwargs) or "ok")
        monkeypatch.setattr(wm.CFG, "cleanup_prompt_template", "Korrigiere: {text}")
        app._cleanup_text("text")
        assert "system" not in gesehen

    def test_all_output_paths_see_the_same_string(self):
        """Verlauf, Zwischenablage und Datei bekommen dasselbe `text`.

        Der Rumpf wird ueber den Syntaxbaum geholt, nicht ueber eine feste
        Zeichenzahl: ein Fenster fester Groesse rutscht aus der Funktion
        heraus, sobald jemand oben etwas einfuegt, und der Test scheitert
        dann an seiner eigenen Mechanik statt an der Regel.
        """
        source = (wm.APP_DIR / "main.py").read_text(encoding="utf-8")
        body = None
        for knoten in ast.walk(ast.parse(source)):
            if isinstance(knoten, ast.FunctionDef) and knoten.name == "_transcribe_audio":
                body = ast.get_source_segment(source, knoten)
                break
        assert body is not None, "_transcribe_audio nicht gefunden"
        order = [body.index("self._add_to_history(text)"),
                 body.index("self._copy_and_paste(text)"),
                 body.index("args=(text,)")]
        assert order == sorted(order), "die Reihenfolge der Ausgabewege hat sich geändert"
        assert body.index("self._cleanup_text(text)") < order[0]


class TestPrompt:
    """Der Prompt selbst — die erste Verteidigungslinie."""

    @pytest.fixture
    def prompt(self):
        return (wm.APP_DIR / "prompts" / "cleanup.txt").read_text(encoding="utf-8")

    def test_it_demands_plain_text(self, prompt):
        assert "Klartext" in prompt

    @pytest.mark.parametrize("word", ["Markdown", "Fettschrift", "Kursivschrift",
                                      "Sternchen", "Unterstriche", "Überschriften",
                                      "Backticks", "Codeblöcke"])
    def test_it_names_what_is_unwanted(self, prompt, word):
        assert word in prompt

    def test_the_placeholder_is_still_there(self, prompt):
        assert "{text}" in prompt

    def test_the_original_task_is_still_there(self, prompt):
        for word in ("Grammatikfehler", "Satzzeichen", "Stotterer"):
            assert word in prompt

    def test_no_broken_characters_remain(self, prompt):
        """Im alten Prompt stand mitten im Satz chinesischer Text."""
        assert all(ord(char) < 0x2E00 for char in prompt), "fremde Schriftzeichen"

    def test_it_is_the_file_the_configuration_points_at(self):
        assert "Klartext" in wm.CFG.cleanup_prompt_template
