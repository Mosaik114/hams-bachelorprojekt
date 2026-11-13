"""Dictation language: resolution, per-language assets, the auto-detect path and German staying exactly as it was."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import numpy as np
import pytest

from hams import config as config_mod
from hams import dictation_language as dl
from hams import glossary
from hams import transcription as transcription_mod
from hams.app import FloatingTranscriberApp
from hams.config import CFG, Config, _apply_toml, _load_config

AUDIO = np.full(16000, 0.2, dtype=np.float32)

# ---------------------------------------------------------------- German stays byte for byte

#: The German texts as they were before the language became configurable. The thesis measurements depend on them.
OLD_HOTWORD_FRAME = "Ein deutscher Text. Namen und Begriffe darin: {}."
OLD_GLOSSARY_HINT = "Diese Wörter sind richtig geschrieben und bleiben genau so: "
OLD_PLAIN_SYSTEM = (
    "Antworte in normalem Klartext. Verwende kein Markdown: keine Sternchen "
    "oder Unterstriche zur Hervorhebung, keine Fett- oder Kursivschrift, keine "
    "Überschriften, keine Backticks und keine Codeblöcke. Deine Antwort wird "
    "unverändert in ein Textfeld eingefügt."
)
OLD_DEFAULT_CLEANUP_PROMPT = (
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
#: sha256 of prompts/cleanup.txt (line endings normalised to LF) before the change.
OLD_CLEANUP_FILE_SHA256 = "46d3414c6304786b5b32b133fbc6a3fdb732db56de5ab702955b860eaccf5796"


class TestGermanIsUnchanged:
    def test_hotword_frame(self):
        assert dl.hotword_frame("de") == OLD_HOTWORD_FRAME
        assert glossary.HOTWORD_FRAME == OLD_HOTWORD_FRAME

    def test_glossary_hint(self):
        assert dl.glossary_hint("de", ("A", "B")) == OLD_GLOSSARY_HINT + "A, B."

    def test_plain_system(self):
        assert dl.plain_system("de") == OLD_PLAIN_SYSTEM

    def test_default_cleanup_prompt(self):
        assert config_mod.DEFAULT_CLEANUP_PROMPT == OLD_DEFAULT_CLEANUP_PROMPT

    def test_cleanup_file(self):
        data = dl.GERMAN_CLEANUP_FILE.read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == OLD_CLEANUP_FILE_SHA256

    def test_german_gets_exactly_the_configured_template(self):
        for template in (dl.GERMAN_CLEANUP_FILE.read_text(encoding="utf-8"), "Eigene Vorlage: {text}"):
            assert dl.cleanup_template("de", template) == template


# ---------------------------------------------------------------- settings and resolution


class TestSettings:
    @pytest.mark.parametrize(
        ("value", "expected"), [("de", "de"), (" EN ", "en"), ("Auto", "auto"), ("pt", "pt"), ("ja", None), ("", None)]
    )
    def test_normalise(self, value, expected):
        assert dl.normalise_setting(value) == expected

    def test_settings_start_with_auto(self):
        assert dl.SETTINGS[0] == "auto"
        assert {"de", "en"} <= set(dl.SUPPORTED)

    @pytest.mark.parametrize(
        ("ui", "environ", "expected"),
        [("auto", {"LANG": "de_DE.UTF-8"}, "de"), ("auto", {"LANG": "fr_FR.UTF-8"}, "en"), ("de", {}, "de")],
    )
    def test_default_follows_the_interface_language(self, ui, environ, expected):
        assert dl.default_setting(ui, platform="linux", environ=environ) == expected

    def test_whisper_language(self):
        assert dl.whisper_language("auto") is None
        assert dl.whisper_language("fr") == "fr"


class TestConfig:
    def test_template_has_no_fixed_language(self):
        text = (config_mod.APP_DIR / "config.default.toml").read_text(encoding="utf-8")
        section = text.split("[transcription]", 1)[1].split("[hotkeys]", 1)[0]
        assert 'language = "de"' in section, "documented as an example"
        assert not [line for line in section.splitlines() if line.startswith("language")]

    @pytest.mark.parametrize(("value", "expected"), [("en", "en"), ("AUTO", "auto"), ("de", "de")])
    def test_loading(self, value, expected):
        cfg = Config()
        _apply_toml(cfg, {"transcription": {"language": value}})
        assert cfg.transcription_language == expected

    def test_an_unknown_value_is_reported_and_ignored(self, caplog):
        cfg = Config()
        cfg.transcription_language = "de"
        with caplog.at_level("WARNING", logger="hams"):
            _apply_toml(cfg, {"transcription": {"language": "klingonisch"}})
        assert cfg.transcription_language == "de"
        assert "transcription.language" in caplog.text

    def test_a_missing_key_keeps_the_previous_layer(self):
        cfg = Config()
        cfg.transcription_language = "fr"
        _apply_toml(cfg, {"transcription": {"vad_filter": True}})
        assert cfg.transcription_language == "fr"

    def _load(self, tmp_path, monkeypatch, user_text: str, system: str) -> Config:
        template = tmp_path / "template.toml"
        template.write_text("[transcription]\nvad_filter = true\n", encoding="utf-8")
        user = tmp_path / "config.toml"
        user.write_text(user_text, encoding="utf-8")
        monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", template)
        monkeypatch.setattr(config_mod, "CONFIG_PATH", user)
        monkeypatch.setattr(config_mod.i18n, "detect_system_language", lambda *a: system)
        return _load_config()

    @pytest.mark.parametrize("system", ["de", "en"])
    def test_a_new_config_follows_the_system(self, tmp_path, monkeypatch, system):
        assert self._load(tmp_path, monkeypatch, "", system).transcription_language == system

    def test_a_new_config_follows_an_explicit_interface_language(self, tmp_path, monkeypatch):
        cfg = self._load(tmp_path, monkeypatch, '[ui]\nlanguage = "en"\n', "de")
        assert cfg.transcription_language == "en"

    def test_an_existing_config_keeps_its_value(self, tmp_path, monkeypatch):
        cfg = self._load(tmp_path, monkeypatch, '[transcription]\nlanguage = "de"\n', "en")
        assert cfg.transcription_language == "de"


# ---------------------------------------------------------------- per-language assets


class TestAssets:
    @pytest.mark.parametrize("language", dl.SUPPORTED)
    def test_every_supported_language_has_a_frame(self, language):
        frame = dl.hotword_frame(language)
        assert frame.count("{}") == 1
        assert frame.format("X").endswith("X.")

    def test_no_frame_for_other_languages(self):
        """An English frame would pull Japanese speech into English."""
        assert dl.hotword_frame("ja") is None
        assert dl.hotword_frame(None) is None

    def test_english_assets(self):
        assert dl.glossary_hint("en", ("Hams",)).startswith("These words are spelled correctly")
        assert "Markdown" in dl.plain_system("en")
        assert "language of the user's message" in dl.plain_system("en")

    @pytest.mark.parametrize("language", ["fr", "ja", ""])
    def test_other_languages_use_the_english_instructions(self, language):
        assert dl.plain_system(language) == dl.plain_system("en")
        assert dl.glossary_hint(language, ("X",)) == dl.glossary_hint("en", ("X",))

    def test_assets_language(self):
        assert dl.assets_language("fr") == "fr"
        assert dl.assets_language("ja") == "en"
        assert dl.assets_language(None) == "en"

    def test_no_words_no_hint(self):
        assert dl.glossary_hint("en", ()) == ""

    @pytest.mark.parametrize(("language", "name"), [("en", "English"), ("fr", "French"), ("pl", "Polish")])
    def test_the_builtin_german_prompt_is_replaced_and_names_the_language(self, language, name):
        german = dl.GERMAN_CLEANUP_FILE.read_text(encoding="utf-8")
        template = dl.cleanup_template(language, german)
        assert f"transcribed text in {name}" in template
        assert f"it stays in {name}" in template
        assert "{text}" in template and "{language}" not in template
        assert "Klartext" not in template

    def test_the_old_default_prompt_counts_as_builtin(self):
        assert "English" in dl.cleanup_template("en", OLD_DEFAULT_CLEANUP_PROMPT)

    def test_an_unknown_language_is_never_called_english(self):
        template = dl.cleanup_template("ja", OLD_DEFAULT_CLEANUP_PROMPT)
        assert "its original language" in template
        assert "English" not in template

    def test_a_custom_template_is_kept_for_every_language(self):
        assert dl.cleanup_template("en", "Fix this: {text}") == "Fix this: {text}"
        assert dl.cleanup_template("en", "") == ""

    def test_a_missing_english_file_keeps_the_configured_template(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dl, "ENGLISH_CLEANUP_FILE", tmp_path / "missing.txt")
        assert dl.cleanup_template("en", OLD_DEFAULT_CLEANUP_PROMPT) == OLD_DEFAULT_CLEANUP_PROMPT

    def test_every_frame_and_name_belongs_to_a_supported_language(self):
        assert set(dl.HOTWORD_FRAMES) == set(dl.SUPPORTED) == set(dl.ENGLISH_NAMES)


# ---------------------------------------------------------------- glossary


@pytest.fixture
def glossary_file(tmp_path, monkeypatch):
    target = tmp_path / "glossar.toml"
    monkeypatch.setattr(glossary, "glossary_path", lambda: target)
    monkeypatch.setitem(glossary._cache, "path", None)
    return target


class TestGlossary:
    def test_hotwords_use_the_frame_of_the_language(self, monkeypatch):
        monkeypatch.setattr(glossary, "words", lambda: ("Hams", "Ollama"))
        assert glossary.hotwords("en") == "An English text. Names and terms in it: Hams, Ollama."
        assert glossary.hotwords("de") == "Ein deutscher Text. Namen und Begriffe darin: Hams, Ollama."
        assert glossary.hotwords("ja") is None

    def test_the_cleanup_hint_follows_the_language(self, monkeypatch):
        monkeypatch.setattr(glossary, "words", lambda: ("Hams",))
        assert (
            glossary.cleanup_hint("en") == "These words are spelled correctly and must stay exactly as written: Hams."
        )

    def test_an_english_user_gets_the_english_default(self, glossary_file, monkeypatch):
        monkeypatch.setattr(CFG, "transcription_language", "en")
        glossary.words()
        text = glossary_file.read_text(encoding="utf-8")
        assert text == glossary.DEFAULT_PATH_EN.read_text(encoding="utf-8")
        assert "Pull-Request" not in text
        assert glossary.apply_replacements("a Pull Request on Git Hub") == "a Pull Request on GitHub"
        assert "PyTorch" in glossary.words()

    def test_a_german_user_gets_the_german_default(self, glossary_file):
        glossary.words()
        assert glossary_file.read_text(encoding="utf-8") == glossary.DEFAULT_PATH.read_text(encoding="utf-8")

    @pytest.mark.parametrize(("ui", "expected"), [("de", "glossar.default.toml"), ("en", "glossar.default.en.toml")])
    def test_auto_follows_the_interface(self, monkeypatch, ui, expected):
        monkeypatch.setattr(CFG, "transcription_language", "auto")
        monkeypatch.setattr(dl.i18n, "language", lambda: ui)
        assert glossary.default_path().name == expected

    def test_english_and_german_keys_both_work(self, glossary_file):
        glossary_file.write_text(
            'woerter = ["Eins"]\nwords = ["Two"]\n[ersetzen]\n"eins" = "Eins"\n[replace]\n"two" = "Two"\n',
            encoding="utf-8",
        )
        assert glossary.words() == ("Eins", "Two")
        assert glossary.apply_replacements("eins two") == "Eins Two"

    def test_wrong_types_are_ignored(self, glossary_file):
        glossary_file.write_text('words = "Hams"\nreplace = 3\n', encoding="utf-8")
        assert glossary.words() == ()
        assert glossary.apply_replacements("x") == "x"

    def test_the_english_default_is_neutral(self):
        """No private names, nothing German-specific."""
        text = glossary.DEFAULT_PATH_EN.read_text(encoding="utf-8")
        assert not any(char in text for char in "äöüÄÖÜß")


# ---------------------------------------------------------------- the transcription path


class FakeModel:
    """Records the options; optionally detects a language or reports one in `info`."""

    def __init__(self, detected=None, info_language=None, detect_error=None):
        self.calls = []
        self.detections = 0
        self.info_language = info_language
        self.detect_error = detect_error
        if detected is not None or detect_error is not None:
            self.detected = detected
            self.detect_language = self._detect

    def _detect(self, audio, vad_filter):
        self.detections += 1
        if self.detect_error is not None:
            raise self.detect_error
        return self.detected, 0.97, [(self.detected, 0.97)]

    def transcribe(self, audio, **options):
        self.calls.append(options)
        info = SimpleNamespace(language=self.info_language or options["language"])
        return [SimpleNamespace(text=" Hello world ")], info


@pytest.fixture
def app():
    a = FloatingTranscriberApp.__new__(FloatingTranscriberApp)
    a.root = SimpleNamespace(after=lambda *args, **kwargs: None)
    a._prompt_mode = False
    return a


@pytest.fixture
def words(monkeypatch):
    monkeypatch.setattr(glossary, "words", lambda: ("Hams",))
    monkeypatch.setattr(glossary, "apply_replacements", lambda text: text)


class TestTranscription:
    def test_german_passes_german_and_the_german_frame(self, app, words):
        model = FakeModel(detected="en")
        assert app._run_transcription(model, AUDIO) == ("Hello world", "de")
        assert model.calls[0]["language"] == "de"
        assert model.calls[0]["hotwords"] == OLD_HOTWORD_FRAME.format("Hams")
        assert model.detections == 0, "a fixed language is never detected"

    def test_english(self, app, words, monkeypatch):
        monkeypatch.setattr(CFG, "transcription_language", "en")
        model = FakeModel()
        assert app._run_transcription(model, AUDIO) == ("Hello world", "en")
        assert model.calls[0]["language"] == "en"
        assert model.calls[0]["hotwords"].startswith("An English text.")

    def test_auto_detects_first_and_uses_the_matching_frame(self, app, words, monkeypatch):
        monkeypatch.setattr(CFG, "transcription_language", "auto")
        model = FakeModel(detected="fr")
        assert app._run_transcription(model, AUDIO) == ("Hello world", "fr")
        assert model.calls[0]["language"] == "fr"
        assert model.calls[0]["hotwords"].startswith("Un texte en français.")

    def test_auto_with_an_unsupported_language_gets_no_frame(self, app, words, monkeypatch):
        monkeypatch.setattr(CFG, "transcription_language", "auto")
        model = FakeModel(detected="ja")
        assert app._run_transcription(model, AUDIO) == ("Hello world", "ja")
        assert model.calls[0]["hotwords"] is None

    def test_auto_without_detection_lets_whisper_decide(self, app, words, monkeypatch):
        """A model without `detect_language`: transcribe detects, `info.language` names the result."""
        monkeypatch.setattr(CFG, "transcription_language", "auto")
        model = FakeModel(info_language="en")
        assert app._run_transcription(model, AUDIO) == ("Hello world", "en")
        assert model.calls[0]["language"] is None
        assert model.calls[0]["hotwords"] is None

    def test_a_failed_detection_falls_back_to_transcribe(self, app, words, monkeypatch):
        monkeypatch.setattr(CFG, "transcription_language", "auto")
        model = FakeModel(detect_error=ValueError("no speech"), info_language="es")
        assert app._run_transcription(model, AUDIO) == ("Hello world", "es")
        assert model.calls[0]["language"] is None

    def test_no_info_means_unknown(self, app, words, monkeypatch):
        monkeypatch.setattr(CFG, "transcription_language", "auto")

        class Bare:
            def transcribe(self, audio, **options):
                return [SimpleNamespace(text="x")], None

        assert app._run_transcription(Bare(), AUDIO) == ("x", "")

    def test_batched_runs_get_the_detected_language_too(self, app, words, monkeypatch):
        monkeypatch.setattr(CFG, "transcription_language", "auto")
        model = FakeModel(detected="en")
        seen = []

        class Pipeline:
            def __init__(self, model):
                pass

            def transcribe(self, audio, **options):
                seen.append(options)
                return [SimpleNamespace(text="long")], None

        monkeypatch.setattr(transcription_mod, "BatchedInferencePipeline", Pipeline)
        long_audio = np.full(16000 * 40, 0.2, dtype=np.float32)
        assert app._run_transcription(model, long_audio) == ("long", "en")
        assert seen[0]["language"] == "en"


class TestOllamaInstructions:
    @pytest.fixture
    def sent(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            FloatingTranscriberApp, "_ollama_generate", lambda self, **kwargs: seen.update(kwargs) or "ok"
        )
        monkeypatch.setattr(glossary, "words", lambda: ("Hams",))
        monkeypatch.setattr(CFG, "cleanup_prompt_template", dl.GERMAN_CLEANUP_FILE.read_text(encoding="utf-8"))
        return seen

    def test_german_cleanup_is_unchanged(self, app, sent):
        app._cleanup_text("hallo", "de")
        german = dl.GERMAN_CLEANUP_FILE.read_text(encoding="utf-8")
        assert sent["prompt"] == OLD_GLOSSARY_HINT + "Hams.\n\n" + german.replace("{text}", "hallo")

    def test_detected_english_gets_the_english_cleanup(self, app, sent, monkeypatch):
        monkeypatch.setattr(CFG, "transcription_language", "auto")
        app._cleanup_text("hello", "en")
        assert sent["prompt"].startswith("These words are spelled correctly")
        assert "transcribed text in English" in sent["prompt"]
        assert sent["prompt"].endswith("Text: hello\n")

    def test_without_a_language_the_setting_decides(self, app, sent, monkeypatch):
        monkeypatch.setattr(CFG, "transcription_language", "fr")
        app._cleanup_text("bonjour")
        assert "transcribed text in French" in sent["prompt"]

    def test_auto_without_a_result_is_neutral(self, app, sent, monkeypatch):
        monkeypatch.setattr(CFG, "transcription_language", "auto")
        app._cleanup_text("text")
        assert "its original language" in sent["prompt"]

    def test_prompt_mode_system_follows_the_language(self, app, sent):
        app._generate_from_prompt("question", "en")
        assert sent["system"] == dl.plain_system("en")
        app._generate_from_prompt("Frage", "de")
        assert sent["system"] == OLD_PLAIN_SYSTEM

    def test_postprocessing_passes_the_language_on(self, app, monkeypatch):
        seen = []
        monkeypatch.setattr(CFG, "ollama_cleanup_enabled", True)
        monkeypatch.setattr(FloatingTranscriberApp, "_cleanup_text", lambda self, text, language: seen.append(language))
        monkeypatch.setattr(FloatingTranscriberApp, "_generate_from_prompt", lambda self, t, language: language)
        monkeypatch.setattr(glossary, "apply_replacements", lambda text: text)
        app._nachbearbeiten("text", False, "fr")
        assert seen == ["fr"]
        assert app._nachbearbeiten("text", True, "es") == "es"
