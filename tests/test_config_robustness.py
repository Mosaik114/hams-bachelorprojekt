"""A hand-edited config.toml must never stop Hams from starting or lose the user's settings.

Two reviewer findings are pinned down here:

1. A value with the wrong type (`timeout_cleanup = "30s"`, `hotkeys = "x"`) raised during
   the import of `hams.config`; the windowed app then never came up, without a log line.
   And `bool("false")` is True, so `[updates] check = "false"` kept the network check on.
2. Saving a setting into a file whose header carries a comment (`[hotkeys] # mine`)
   appended a second `[hotkeys]` table. tomllib rejects that file, so the next start
   silently reset *all* settings.
"""

import logging
import math
import tomllib

import pytest

import hams.config as config_mod
from hams.config import Config, _apply_toml, _load_config, _persist_config_value
from hams.config_toml import _set_toml_value, _toml_scalar, verify_toml_value
from hams.config_validation import SectionReader

# (section, key, Config attribute, kind) for every key the loader reads.
FIELDS = [
    ("audio", "max_recording_seconds", "max_recording_seconds", "int"),
    ("model", "size", "model_size", "text"),
    ("transcription", "vad_filter", "transcription_vad_filter", "bool"),
    ("hotkeys", "transcript", "hotkey_transcript", "text"),
    ("hotkeys", "prompt", "hotkey_prompt", "text"),
    ("ollama", "url", "ollama_url", "text"),
    ("ollama", "model_transcript_cleanup", "ollama_model_cleanup", "text"),
    ("ollama", "model_prompt_generate", "ollama_model_prompt", "text"),
    ("ollama", "timeout_cleanup", "ollama_timeout_cleanup", "int"),
    ("ollama", "timeout_prompt", "ollama_timeout_prompt", "int"),
    ("ollama", "cleanup_temperature", "ollama_cleanup_temperature", "number"),
    ("ollama", "cleanup_enabled", "ollama_cleanup_enabled", "bool"),
    ("ollama", "keep_alive", "ollama_keep_alive", "keep_alive"),
    ("clipboard", "paste_timeout", "clipboard_paste_timeout", "number"),
    ("output", "save_transcripts", "save_transcripts", "bool"),
    ("output", "keep_days", "transcripts_keep_days", "days"),
    ("ui", "sound_feedback", "sound_feedback", "bool"),
    ("updates", "check", "update_check", "bool"),
    ("integrations", "cursor_dot", "integrations_cursor_dot", "bool"),
]

WRONG_VALUES = {
    "int": ["30s", "30", True, 1.5, [30], {"a": 1}, 0, -5, 10**9],
    "days": ["30s", "30", True, 1.5, [30], {"a": 1}, -5, 10**9],
    "number": ["0.3", True, [0.3], {"a": 1}, -1.0, 99.0, math.nan, math.inf],
    "text": [42, 1.5, True, ["x"], {"a": 1}],
    "keep_alive": [1.5, True, ["30m"], {"a": 1}],
    "bool": ["yes", "nein", 1, 0, 1.0, ["true"], {"a": 1}],
}

CASES = [
    pytest.param(section, key, attr, value, id=f"{section}.{key}={value!r}")
    for section, key, attr, kind in FIELDS
    for value in WRONG_VALUES[kind]
]


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


class TestWrongTypesFallBack:
    @pytest.mark.parametrize("section,key,attr,value", CASES)
    def test_wrong_value_keeps_the_default_and_is_reported(self, section, key, attr, value, caplog):
        cfg = Config()
        default = getattr(cfg, attr)
        expected = False if (section, key) == ("updates", "check") else default

        with caplog.at_level(logging.WARNING, logger="hams"):
            _apply_toml(cfg, {section: {key: value}})

        assert getattr(cfg, attr) == expected
        assert type(getattr(cfg, attr)) is type(expected)
        assert any(f"{section}.{key}" in message for message in _warnings(caplog))

    @pytest.mark.parametrize("section", sorted({f[0] for f in FIELDS} | {"model"}))
    @pytest.mark.parametrize("value", ["x", 1, True, [1, 2], 1.5])
    def test_a_section_that_is_no_table_is_ignored(self, section, value, caplog):
        with caplog.at_level(logging.WARNING, logger="hams"):
            cfg = Config()
            _apply_toml(cfg, {section: value})

        assert cfg == Config()
        assert any(f"[{section}] should be a table" in message for message in _warnings(caplog))

    def test_every_loaded_key_is_in_the_table(self):
        """A key added to the loader without a case here would go untested."""
        template = tomllib.loads(config_mod.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        covered = {(section, key) for section, key, _attr, _kind in FIELDS}
        special = {
            ("audio", "device"),
            ("model", "device_priority"),
            ("ollama", "cleanup_prompt_file"),
            ("ui", "appearance"),
            ("ui", "language"),
        }
        for section, values in template.items():
            for key in values:
                assert (section, key) in covered | special, f"{section}.{key} has no wrong-type case"


class TestBooleans:
    @pytest.mark.parametrize("section,key,attr", [(s, k, a) for s, k, a, kind in FIELDS if kind == "bool"])
    @pytest.mark.parametrize("word,expected", [("false", False), ("true", True), (" FALSE ", False)])
    def test_a_quoted_true_or_false_means_what_it_says(self, section, key, attr, word, expected, caplog):
        cfg = Config()
        setattr(cfg, attr, not expected)
        with caplog.at_level(logging.WARNING, logger="hams"):
            _apply_toml(cfg, {section: {key: word}})
        assert getattr(cfg, attr) is expected
        assert any("without quotes" in message for message in _warnings(caplog))

    def test_quoted_false_switches_the_update_check_off(self):
        """Privacy: `bool("false")` is True and used to keep the network check on."""
        cfg = Config()
        _apply_toml(cfg, tomllib.loads('[updates]\ncheck = "false"\n'))
        assert cfg.update_check is False

    def test_an_unclear_update_check_means_off(self):
        cfg = Config()
        _apply_toml(cfg, {"updates": {"check": "maybe"}})
        assert cfg.update_check is False

    @pytest.mark.parametrize("value", [True, False])
    def test_real_booleans_pass_unchanged(self, value, caplog):
        cfg = Config()
        with caplog.at_level(logging.WARNING, logger="hams"):
            _apply_toml(cfg, {"ui": {"sound_feedback": value}, "updates": {"check": value}})
        assert cfg.sound_feedback is value
        assert cfg.update_check is value
        assert _warnings(caplog) == []


class TestValidValuesStillWork:
    def test_valid_values_are_taken(self, caplog):
        data = {
            "audio": {"max_recording_seconds": 60, "device": 3},
            "model": {"size": "small", "device_priority": ["cpu"]},
            "transcription": {"vad_filter": False},
            "hotkeys": {"transcript": "ctrl+h"},
            "ollama": {"timeout_cleanup": 45.0, "keep_alive": -1},
            "clipboard": {"paste_timeout": 1},
        }
        cfg = Config()
        with caplog.at_level(logging.WARNING, logger="hams"):
            _apply_toml(cfg, data)
        assert (cfg.max_recording_seconds, cfg.mic_device_raw) == (60, 3)
        assert cfg.model_size == "small"
        assert cfg.model_device_priority == ("cpu",)
        assert cfg.transcription_vad_filter is False
        assert cfg.hotkey_transcript == "ctrl+h"
        assert cfg.ollama_timeout_cleanup == 45
        assert type(cfg.ollama_timeout_cleanup) is int
        assert cfg.ollama_keep_alive == "-1"
        assert cfg.clipboard_paste_timeout == 1.0
        assert _warnings(caplog) == []


class TestListsAndDevices:
    @pytest.mark.parametrize("value", [True, 1.5, ["Yeti"], {"a": 1}])
    def test_a_wrong_device_means_the_default_device(self, value, caplog):
        cfg = Config()
        with caplog.at_level(logging.WARNING, logger="hams"):
            _apply_toml(cfg, {"audio": {"device": value}})
        assert cfg.mic_device_raw is None
        assert cfg.mic_device is None
        assert any("audio.device" in message for message in _warnings(caplog))

    @pytest.mark.parametrize("value", ["cuda", [], [1, 2], {"a": 1}])
    def test_a_wrong_device_priority_keeps_the_default(self, value, caplog):
        cfg = Config()
        with caplog.at_level(logging.WARNING, logger="hams"):
            _apply_toml(cfg, {"model": {"device_priority": value}})
        assert cfg.model_device_priority == Config().model_device_priority
        assert any("model.device_priority" in message for message in _warnings(caplog))

    def test_a_cleanup_prompt_file_that_is_no_text_is_ignored(self):
        cfg = Config()
        _apply_toml(cfg, {"ollama": {"cleanup_prompt_file": 7}})
        assert cfg.cleanup_prompt_template == Config().cleanup_prompt_template

    def test_an_undecodable_cleanup_prompt_falls_back(self, tmp_path):
        broken = tmp_path / "prompt.txt"
        broken.write_bytes(b"\xff\xfe\xfa")
        cfg = Config()
        _apply_toml(cfg, {"ollama": {"cleanup_prompt_file": str(broken)}})
        assert cfg.cleanup_prompt_template == Config().cleanup_prompt_template


class TestSectionReader:
    def test_long_values_are_cut_in_the_warning(self):
        messages = []
        reader = SectionReader({"s": {"k": "x" * 500}}, "s", messages.append)
        assert reader.integer("k", 1, 0, 2) == 1
        assert len(messages[0]) < 200

    def test_a_non_dict_file_is_ignored(self, caplog):
        cfg = Config()
        with caplog.at_level(logging.WARNING, logger="hams"):
            _apply_toml(cfg, ["not", "a", "table"])  # type: ignore[arg-type]
        assert cfg == Config()
        assert _warnings(caplog)


# ------------------------------------------------------------------ loading


@pytest.fixture
def user_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
    monkeypatch.setattr(config_mod, "LEGACY_USER_DIR", None)
    monkeypatch.setattr(config_mod, "BROKEN_CONFIG", None)
    return path


class TestLoadingNeverRaises:
    def test_the_reviewer_examples_load(self, user_file):
        user_file.write_text(
            'hotkeys = "x"\n\n[ollama]\ntimeout_cleanup = "30s"\n\n[updates]\ncheck = "false"\n',
            encoding="utf-8",
        )
        cfg = _load_config()
        assert cfg.hotkey_transcript == Config().hotkey_transcript
        assert cfg.ollama_timeout_cleanup == 30
        assert cfg.update_check is False
        assert config_mod.BROKEN_CONFIG is None

    def test_an_unexpected_error_gives_the_defaults(self, user_file, monkeypatch, caplog):
        def explode(*_args):
            raise RuntimeError("boom")

        monkeypatch.setattr(config_mod, "_apply_toml", explode)
        with caplog.at_level(logging.ERROR, logger="hams"):
            cfg = _load_config()
        assert cfg.max_recording_seconds == Config().max_recording_seconds
        assert cfg.transcription_language
        assert any("could not be loaded" in r.getMessage() for r in caplog.records)

    def test_a_broken_template_is_logged_not_fatal(self, user_file, tmp_path, monkeypatch, caplog):
        template = tmp_path / "template.toml"
        template.write_text("[[[", encoding="utf-8")
        monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", template)
        with caplog.at_level(logging.ERROR, logger="hams"):
            cfg = _load_config()
        assert cfg.model_size == Config().model_size
        assert config_mod.BROKEN_CONFIG is None, "only the user's own file triggers the hint"
        assert any("template.toml" in r.getMessage() for r in caplog.records)


class TestBrokenUserFile:
    BROKEN = '[ui]\nlanguage = "de"\n\n[hotkeys]\ntranscript = "a"\n[hotkeys]\nprompt = "b"\n'

    def test_the_file_stays_and_a_copy_is_kept(self, user_file, caplog):
        user_file.write_text(self.BROKEN, encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger="hams"):
            cfg = _load_config()

        assert cfg.hotkey_transcript == Config().hotkey_transcript
        assert user_file.read_text(encoding="utf-8") == self.BROKEN
        broken = config_mod.BROKEN_CONFIG
        assert broken is not None and broken.path == user_file
        assert broken.backup is not None and broken.backup.name.startswith("config.toml.broken-")
        assert broken.backup.read_text(encoding="utf-8") == self.BROKEN
        assert any("USER CONFIG UNREADABLE" in r.getMessage() for r in caplog.records)

    def test_no_second_copy_of_the_same_content(self, user_file):
        user_file.write_text(self.BROKEN, encoding="utf-8")
        _load_config()
        _load_config()
        assert len(list(user_file.parent.glob("config.toml.broken-*"))) == 1

    def test_a_changed_broken_file_gets_its_own_copy(self, user_file):
        user_file.write_text(self.BROKEN, encoding="utf-8")
        _load_config()
        user_file.write_text(self.BROKEN + "[[[\n", encoding="utf-8")
        _load_config()
        assert len(list(user_file.parent.glob("config.toml.broken-*"))) == 2

    def test_a_copy_failure_is_survived(self, user_file, monkeypatch):
        user_file.write_text(self.BROKEN, encoding="utf-8")

        def refuse(*_args, **_kwargs):
            raise PermissionError("read-only")

        monkeypatch.setattr(type(user_file), "read_bytes", refuse)
        _load_config()
        assert config_mod.BROKEN_CONFIG is not None
        assert config_mod.BROKEN_CONFIG.backup is None

    def test_not_utf8_counts_as_broken(self, user_file):
        user_file.write_bytes(b"[ui]\nlanguage = \"\xff\"\n")
        _load_config()
        assert config_mod.BROKEN_CONFIG is not None

    def test_saving_never_overwrites_the_broken_file(self, user_file, caplog):
        user_file.write_text(self.BROKEN, encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger="hams"):
            assert _persist_config_value("ui", "sound_feedback", False) is False
        assert user_file.read_text(encoding="utf-8") == self.BROKEN
        assert any("Config not saved" in r.getMessage() for r in caplog.records)

    def test_a_readable_file_clears_the_flag(self, user_file):
        user_file.write_text(self.BROKEN, encoding="utf-8")
        _load_config()
        user_file.write_text('[ui]\nlanguage = "de"\n', encoding="utf-8")
        _load_config()
        assert config_mod.BROKEN_CONFIG is None


# ------------------------------------------------------------------ writing


class TestHeaderWithComment:
    def test_reviewer_repro(self):
        src = '[ui]\nlanguage = "de"\n\n[hotkeys] # meine Tasten\ntranscript = "ctrl+shift+space"\n'
        out = _set_toml_value(src, "hotkeys", "transcript", "ctrl+alt+h")
        assert tomllib.loads(out)["hotkeys"]["transcript"] == "ctrl+alt+h"
        assert out.count("[hotkeys]") == 1
        assert "# meine Tasten" in out

    @pytest.mark.parametrize(
        "header",
        ["[hotkeys]#x", "[hotkeys]   # x", "  [hotkeys]", "[ hotkeys ]", '["hotkeys"]', "[hotkeys]\t# [x]"],
    )
    def test_header_variants_are_found(self, header):
        src = f'{header}\ntranscript = "a"\n'
        out = _set_toml_value(src, "hotkeys", "prompt", "b")
        data = tomllib.loads(out)
        assert data["hotkeys"] == {"transcript": "a", "prompt": "b"}

    def test_array_of_tables_is_not_the_section(self):
        src = '[ui]\nlanguage = "de"\n\n[[plugins]]\nname = "x"\n'
        out = _set_toml_value(src, "ui", "sound_feedback", False)
        data = tomllib.loads(out)
        assert data["ui"]["sound_feedback"] is False
        assert data["plugins"] == [{"name": "x"}]

    def test_dotted_section(self):
        src = '[model]\nsize = "turbo"\n\n[model.compute_priority]\ncpu = ["int8"]\n'
        out = _set_toml_value(src, "model.compute_priority", "cpu", "float32")
        assert verify_toml_value(out, "model.compute_priority", "cpu", "float32") == ""


class TestQuotedKeys:
    @pytest.mark.parametrize("line", ['"transcript" = "a"', "'transcript' = 'a'", 'transcript="a" # c'])
    def test_quoted_or_tight_key_is_replaced(self, line):
        src = f"[hotkeys]\n{line}\n"
        out = _set_toml_value(src, "hotkeys", "transcript", "z")
        assert tomllib.loads(out)["hotkeys"]["transcript"] == "z"
        assert out.count("transcript") == 1

    def test_literal_string_with_hash_keeps_its_comment(self):
        src = "[hotkeys]\ntranscript = 'a#b' # mine\n"
        out = _set_toml_value(src, "hotkeys", "transcript", "z")
        assert tomllib.loads(out)["hotkeys"]["transcript"] == "z"
        assert out.endswith("# mine\n")


class TestEscaping:
    @pytest.mark.parametrize(
        "value",
        ["x\ny", "a\r\nb", "tab\there", 'q"uote', "back\\slash", "\x00\x01\x1f\x7f", "\b\f", "Größe ✓", ""],
    )
    def test_every_string_round_trips(self, value):
        assert tomllib.loads(f"k = {_toml_scalar(value)}\n")["k"] == value

    def test_newline_in_value_keeps_the_file_valid(self, user_file):
        user_file.write_text('[ollama]\nmodel_transcript_cleanup = "a"\n', encoding="utf-8")
        assert _persist_config_value("ollama", "model_transcript_cleanup", "x\ny") is True
        assert tomllib.loads(user_file.read_text(encoding="utf-8"))["ollama"]["model_transcript_cleanup"] == "x\ny"


class TestRefusedEdits:
    """What the text edit cannot handle is refused, the old file stays."""

    @pytest.mark.parametrize(
        "src",
        [
            'hotkeys = { transcript = "a" }\n',
            'hotkeys.transcript = "a"\n',
            '[hotkeys]\ntranscript = """a\n[x]\n"""\n',
        ],
    )
    def test_unsupported_layouts_are_not_written(self, user_file, src, caplog):
        user_file.write_text(src, encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger="hams"):
            assert _persist_config_value("hotkeys", "transcript", "z") is False
        assert user_file.read_text(encoding="utf-8") == src
        assert any("Config not saved" in r.getMessage() for r in caplog.records)

    def test_verify_reports_a_wrong_value(self):
        assert verify_toml_value('[a]\nk = 1\n', "a", "k", 2)
        assert verify_toml_value('[a]\nk = 1\n', "a", "k", True), "1 is not True"
        assert verify_toml_value('a = 1\n', "a", "k", 1)
        assert verify_toml_value('[a]\nk = 1\n', "a", "k", 1) == ""

    def test_nan_is_never_written(self, user_file):
        user_file.write_text("[ui]\nwindow_x = 1\n", encoding="utf-8")
        assert _persist_config_value("ui", "window_x", math.nan) is False
        assert user_file.read_text(encoding="utf-8") == "[ui]\nwindow_x = 1\n"


# ------------------------------------------------------------------ import side effects


class TestLoggingSetup:
    def test_an_unusable_log_folder_does_not_raise(self, tmp_path, monkeypatch):
        blocker = tmp_path / "not-a-folder"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(config_mod, "LOG_DIR", blocker / "logs")
        log = logging.getLogger("hams")
        saved = log.handlers[:]
        log.handlers = []
        try:
            result = config_mod._setup_logging()
            assert result is log
            assert not any(isinstance(h, logging.FileHandler) for h in log.handlers)
            assert log.handlers, "the console handler is still there"
        finally:
            for handler in log.handlers:
                handler.close()
            log.handlers = saved

    def test_the_file_handler_is_added_when_possible(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config_mod, "LOG_DIR", tmp_path / "logs")
        log = logging.getLogger("hams")
        saved = log.handlers[:]
        log.handlers = []
        try:
            config_mod._setup_logging()
            assert any(isinstance(h, logging.FileHandler) for h in log.handlers)
        finally:
            for handler in log.handlers:
                handler.close()
            log.handlers = saved


class TestRetiredKeys:
    """Whisper and Ollama internals are constants now (audit finding H13); old files still carry the keys."""

    OLD_TEMPLATE = (
        "[model]\n"
        'size = "turbo"\n'
        'device_priority = ["cuda", "cpu"]\n'
        "[model.compute_priority]\n"
        'cuda = ["float16", "int8_float16", "int8"]\n'
        'cpu = ["int8", "float32"]\n'
        "[transcription]\n"
        "word_timestamps = false\nbeam_size = 1\ncondition_on_previous_text = false\nwithout_timestamps = true\n"
        "[ollama]\nstream_prompt = true\nwarmup_on_start = true\n"
    )

    def _load(self, tmp_path, monkeypatch, caplog, text):
        path = tmp_path / "config.toml"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
        with caplog.at_level(logging.WARNING, logger="hams"):
            cfg = _load_config()
        return cfg, [r.getMessage() for r in caplog.records if "no longer settings" in r.getMessage()]

    def test_an_old_template_copy_loads_without_a_warning(self, tmp_path, monkeypatch, caplog):
        cfg, warnings = self._load(tmp_path, monkeypatch, caplog, self.OLD_TEMPLATE)
        assert warnings == []
        assert cfg.model_device_priority == ("cuda", "cpu")

    def test_changed_internals_are_ignored_with_one_warning(self, tmp_path, monkeypatch, caplog):
        text = "[transcription]\nbeam_size = 5\n[ollama]\nwarmup_on_start = false\nstream = false\n"
        cfg, warnings = self._load(tmp_path, monkeypatch, caplog, text)
        assert len(warnings) == 1
        for key in ("transcription.beam_size = 5", "ollama.warmup_on_start = False", "ollama.stream = False"):
            assert key in warnings[0]
        assert not hasattr(cfg, "transcription_beam_size")

    def test_the_constants_reach_whisper(self):
        from hams.transcription import WHISPER_OPTIONS
        from hams.whisper_model import COMPUTE_PRIORITY

        assert WHISPER_OPTIONS == {
            "beam_size": 1,
            "condition_on_previous_text": False,
            "word_timestamps": False,
            "without_timestamps": True,
        }
        assert COMPUTE_PRIORITY["cuda"][0] == "float16"
        assert COMPUTE_PRIORITY["cpu"][0] == "int8"
