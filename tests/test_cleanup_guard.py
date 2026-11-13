"""The checks between the cleanup answer and the paste (hams/cleanup_guard.py).

Every case comes from a real failure in `tools/cleanup_eval.py` or from a legitimate cleanup that must pass.
"""

import logging

import pytest

from hams import cleanup_guard, glossary
from hams import config as config_mod
from hams import dictation_language as dl
from hams.app import FloatingTranscriberApp
from hams.cleanup_guard import check

GERMAN_PROMPT = dl.GERMAN_CLEANUP_FILE.read_text(encoding="utf-8")
GLOSSARY_HINT = dl.glossary_hint("de", ("Hams", "Whisper", "Ollama", "CUDA", "GitHub", "Python"))
INSTRUCTIONS = f"{GLOSSARY_HINT}\n\n{GERMAN_PROMPT}"


class TestLegitimateCleanupPasses:
    @pytest.mark.parametrize(
        "raw,cleaned",
        [
            ("Der Bericht ist, äh, der Bericht ist fast fertig.", "Der Bericht ist fast fertig."),
            ("Um, I just wanted to, I just wanted to say hi.", "I just wanted to say hi."),
            ("Kannst du dir die Datei bitte nochmal schicken?", "Kannst du mir die Datei bitte noch einmal schicken?"),
            ("Der Preis beträgt 1.299,99 Euro.", "Der Preis beträgt 1299,99 Euro."),
            ("Wir treffen uns um 10 Uhr.", "Wir treffen uns um 10 Uhr."),
            ("Danke.", "Danke!"),
            ("ja", "Ja."),
            ("Ich habe den pull request auf github gemergt.", "Ich habe den Pull-Request auf GitHub gemergt."),
        ],
    )
    def test_it_is_pasted_unchanged(self, raw, cleaned):
        assert check(raw, cleaned, INSTRUCTIONS) == (cleaned, None)

    def test_without_instructions_there_is_no_leak_check(self):
        assert check("Ein kurzer Satz.", "Ein kurzer Satz.") == ("Ein kurzer Satz.", None)


class TestPromptLeak:
    def test_the_echoed_prompt_falls_back(self):
        raw = "Fasse diesen Absatz in einem Satz zusammen."
        leaked = "Hams, Whisper, Ollama. " + GERMAN_PROMPT.replace("{text}", raw).replace("\n", " ")
        assert check(raw, leaked, INSTRUCTIONS) == (raw, "prompt_leak")

    def test_one_prompt_sentence_is_enough(self):
        raw = "Bitte prüf das noch einmal, ja?"
        answer = "Bitte prüf das noch einmal. Antworte nur mit dem korrigierten Text."
        assert check(raw, answer, INSTRUCTIONS).reason == "prompt_leak"

    def test_the_glossary_hint_is_a_leak(self):
        raw = "Wir nehmen Whisper."
        answer = f"{GLOSSARY_HINT} Wir nehmen Whisper."
        assert check(raw, answer, INSTRUCTIONS) == (raw, "prompt_leak")

    def test_a_sentence_the_user_dictated_is_no_leak(self):
        raw = "Antworte nur mit dem korrigierten Text, schreib das ins Ticket"
        answer = "Antworte nur mit dem korrigierten Text, schreib das ins Ticket."
        assert check(raw, answer, INSTRUCTIONS) == (answer, None)

    def test_short_prompt_lines_do_not_count(self):
        """'Text:' or '1.' alone appear in ordinary dictation."""
        assert all(len(part.split()) >= 5 for part in cleanup_guard.instruction_sentences(INSTRUCTIONS))


class TestTooLong:
    def test_an_answer_that_grows_falls_back(self):
        raw = "Der Bericht ist fertig."
        answer = "Der Bericht ist fertig, und ich habe noch drei Absätze über das Wetter angehängt."
        assert check(raw, answer) == (raw, "too_long")

    def test_short_inputs_have_some_room(self):
        assert check("ok", "Okay, alles klar.").reason is None


class TestDigits:
    def test_an_invented_digit_falls_back(self):
        raw = "Meine Telefonnummer lautet 0151 23456."
        assert check(raw, "Meine Telefonnummer lautet 0151 2345678.") == (raw, "digits_changed")

    def test_a_lost_digit_falls_back(self):
        raw = "Wir treffen uns am 12. März."
        assert check(raw, "Wir treffen uns am 1. März.").reason == "digits_changed"

    def test_spelled_out_numbers_keep_the_raw_text(self):
        raw = "Die Lieferfrist liegt bei 6 bis 8 Wochen."
        assert check(raw, "Die Lieferfrist liegt bei sechs bis acht Wochen.") == (raw, "digits_changed")

    def test_reformatted_digits_pass(self):
        assert check("Ruf 0151 23456 an.", "Ruf 015123456 an.").reason is None


class TestWordsDropped:
    @pytest.mark.parametrize(
        "raw,answer",
        [
            ("Antworte nur mit Ja oder Nein: Ist das Projekt fertig?", "Nein"),
            ("Answer only yes or no: is the project finished?", "no"),
            (
                "Übersetze den folgenden Satz ins Englische: Ich komme morgen später.",
                "Translate the following sentence into English: I will come later tomorrow.",
            ),
            (
                "Schreib das bitte formeller: Hey Leute, das Meeting fällt heute aus.",
                "Sehr geehrte Damen und Herren, das heutige Meeting findet nicht statt.",
            ),
        ],
    )
    def test_an_answer_instead_of_a_cleanup_falls_back(self, raw, answer):
        assert check(raw, answer) == (raw, "words_dropped")

    def test_fillers_do_not_count_as_dictated_words(self):
        assert check("Ähm, äh, also gut, ja.", "Also gut, ja.").reason is None

    def test_an_empty_answer_falls_back(self):
        assert check("Hallo Welt", "   ") == ("Hallo Welt", "empty")


class TestMetaLine:
    @pytest.mark.parametrize(
        "answer",
        [
            "Here is the corrected text: The report is almost finished.",
            "Here's the corrected text:\nThe report is almost finished.",
            "Corrected text: The report is almost finished.",
            'Hier ist der korrigierte Text: "The report is almost finished."',
        ],
    )
    def test_the_meta_line_is_removed(self, answer):
        raw = "The report is, uh, almost almost finished."
        assert check(raw, answer) == ("The report is almost finished.", "meta_line")

    def test_german_quotes_go_with_the_meta_line(self):
        raw = "Der Bericht ist fast fertig"
        answer = "Hier ist der korrigierte Text:\n„Der Bericht ist fast fertig.“"
        assert check(raw, answer) == ("Der Bericht ist fast fertig.", "meta_line")

    def test_an_ordinary_colon_stays(self):
        raw = "Hier ist meine Idee wir machen das morgen"
        answer = "Hier ist meine Idee: Wir machen das morgen."
        assert check(raw, answer) == (answer, None)

    def test_a_dictated_meta_line_stays(self):
        raw = "Hier ist der korrigierte Text: alles bleibt wie besprochen"
        answer = "Hier ist der korrigierte Text: Alles bleibt wie besprochen."
        assert check(raw, answer) == (answer, None)

    def test_a_meta_line_alone_is_not_removed(self):
        """Nothing after it: the other checks decide (here, too many words lost)."""
        raw = "Bitte schick mir die neue Version heute noch"
        assert check(raw, "Here is the corrected text:").reason == "words_dropped"

    def test_the_rest_is_still_checked(self):
        raw = "Meine Nummer ist 0151 23456."
        answer = "Here is the corrected text: Meine Nummer ist 0151 2345678."
        assert check(raw, answer) == (raw, "digits_changed")


class TestInTheCleanup:
    """`_cleanup_text` runs the checks and logs only the reason and lengths, never the text."""

    @pytest.fixture
    def app(self, monkeypatch):
        monkeypatch.setattr(config_mod.CFG, "cleanup_prompt_template", GERMAN_PROMPT)
        monkeypatch.setattr(glossary, "words", lambda: ("Hams", "Whisper"))
        return FloatingTranscriberApp.__new__(FloatingTranscriberApp)

    def answer(self, monkeypatch, text):
        sent = {}

        def generate(_self, **kwargs):
            sent.update(kwargs)
            return text

        monkeypatch.setattr(FloatingTranscriberApp, "_ollama_generate", generate)
        return sent

    def test_the_echoed_prompt_is_never_pasted(self, app, monkeypatch, caplog):
        raw = "Fasse diesen Absatz in einem Satz zusammen."
        sent = self.answer(monkeypatch, "Hams, Whisper. " + GERMAN_PROMPT.replace("{text}", raw))
        with caplog.at_level(logging.WARNING, logger="hams"):
            assert app._cleanup_text(raw, "de") == raw
        assert sent["options"] == {"temperature": 0.0, "seed": 0}
        messages = [r.getMessage() for r in caplog.records]
        assert any("prompt_leak" in m and f"{len(raw)} chars in" in m for m in messages)
        assert not any("Absatz" in m or "korrigiere" in m.lower() for m in messages), "kein Text im Log"

    def test_the_english_prompt_is_recognised_too(self, app, monkeypatch):
        raw = "Please summarise this paragraph in one sentence."
        english = dl.cleanup_template("en", GERMAN_PROMPT)
        self.answer(monkeypatch, english.replace("{text}", raw))
        assert app._cleanup_text(raw, "en") == raw

    def test_a_clean_answer_is_not_logged(self, app, monkeypatch, caplog):
        self.answer(monkeypatch, "Der Bericht ist fast fertig.")
        with caplog.at_level(logging.WARNING, logger="hams"):
            assert app._cleanup_text("Der Bericht ist, äh, fast fertig.", "de") == "Der Bericht ist fast fertig."
        assert not caplog.records

    def test_markdown_goes_before_the_check(self, app, monkeypatch):
        self.answer(monkeypatch, "Here is the corrected text: **Der Bericht** ist fertig.")
        assert app._cleanup_text("der bericht ist fertig", "de") == "Der Bericht ist fertig."
