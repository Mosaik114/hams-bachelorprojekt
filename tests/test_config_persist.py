"""Tests für das abschnittsbewusste, atomare Schreiben von config.toml."""

import tomllib

import pytest

from wisper.main import _persist_config_value, _toml_scalar, _trailing_comment


SAMPLE = """# Wisper-Konfiguration

[audio]
# Mikrofon: "default" für System-Standard
device = "default"
sample_rate = 16000

[model]
size = "turbo"
device = "cuda"

[transcription]
beam_size = 1

[ui]
sound_feedback = true
"""


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Legt eine Beispiel-Config an und hängt CONFIG_PATH darauf um."""
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE, encoding="utf-8", newline="")
    monkeypatch.setattr("wisper.main.CONFIG_PATH", path)
    return path


def read(path):
    return path.read_text(encoding="utf-8")


class TestSectionAwareness:
    """Der Kernpunkt: gleiche Schlüsselnamen in verschiedenen Abschnitten."""

    def test_same_key_in_two_sections(self, config):
        assert _persist_config_value("audio", "device", "Shure MV7+") is True

        data = tomllib.loads(read(config))
        assert data["audio"]["device"] == "Shure MV7+"
        assert data["model"]["device"] == "cuda", "Fremder Abschnitt wurde verändert"

    def test_writes_into_later_section(self, config):
        assert _persist_config_value("model", "device", "cpu") is True

        data = tomllib.loads(read(config))
        assert data["model"]["device"] == "cpu"
        assert data["audio"]["device"] == "default"

    def test_device_vs_device_priority(self, tmp_path, monkeypatch):
        path = tmp_path / "config.toml"
        path.write_text(
            '[audio]\ndevice = "default"\n\n'
            '[model]\ndevice_priority = ["cuda", "cpu"]\n',
            encoding="utf-8",
            newline="",
        )
        monkeypatch.setattr("wisper.main.CONFIG_PATH", path)

        assert _persist_config_value("audio", "device", "Yeti") is True

        data = tomllib.loads(read(path))
        assert data["audio"]["device"] == "Yeti"
        assert data["model"]["device_priority"] == ["cuda", "cpu"]

    def test_size_vs_beam_size(self, config):
        assert _persist_config_value("model", "size", "medium") is True

        data = tomllib.loads(read(config))
        assert data["model"]["size"] == "medium"
        assert data["transcription"]["beam_size"] == 1


class TestMissingEntries:
    def test_missing_key_added_to_section(self, config):
        assert _persist_config_value("ui", "window_x", 120) is True

        data = tomllib.loads(read(config))
        assert data["ui"]["window_x"] == 120
        assert data["ui"]["sound_feedback"] is True

    def test_missing_key_lands_in_correct_section(self, config):
        assert _persist_config_value("audio", "channels", 2) is True

        text = read(config)
        audio_block = text.split("[audio]", 1)[1].split("[model]", 1)[0]
        assert "channels = 2" in audio_block

        data = tomllib.loads(text)
        assert data["audio"]["channels"] == 2
        assert "channels" not in data["model"]

    def test_missing_section_created(self, config):
        assert _persist_config_value("window", "position", "40,40") is True

        data = tomllib.loads(read(config))
        assert data["window"]["position"] == "40,40"
        assert data["audio"]["device"] == "default"

    def test_file_without_trailing_newline(self, tmp_path, monkeypatch):
        path = tmp_path / "config.toml"
        path.write_text('[ui]\nsound_feedback = true', encoding="utf-8", newline="")
        monkeypatch.setattr("wisper.main.CONFIG_PATH", path)

        assert _persist_config_value("ui", "window_x", 5) is True
        assert tomllib.loads(read(path))["ui"]["window_x"] == 5


class TestFormatPreservation:
    def test_comments_and_blank_lines_preserved(self, config):
        _persist_config_value("audio", "device", "Shure MV7+")

        text = read(config)
        assert "# Wisper-Konfiguration" in text
        assert '# Mikrofon: "default" für System-Standard' in text
        assert "\n\n[model]" in text, "Leerzeile vor dem Abschnitt verloren"

    def test_key_order_preserved(self, config):
        _persist_config_value("audio", "device", "Yeti")

        text = read(config)
        assert text.index("device =") < text.index("sample_rate =")

    def test_trailing_comment_preserved(self, tmp_path, monkeypatch):
        path = tmp_path / "config.toml"
        path.write_text(
            '[ui]\nsound_feedback = true  # Beeps bei Start/Stopp\n',
            encoding="utf-8",
            newline="",
        )
        monkeypatch.setattr("wisper.main.CONFIG_PATH", path)

        _persist_config_value("ui", "sound_feedback", False)

        text = read(path)
        assert "sound_feedback = false  # Beeps bei Start/Stopp" in text
        assert tomllib.loads(text)["ui"]["sound_feedback"] is False

    def test_crlf_preserved(self, tmp_path, monkeypatch):
        path = tmp_path / "config.toml"
        path.write_text(
            '[audio]\r\ndevice = "default"\r\n', encoding="utf-8", newline=""
        )
        monkeypatch.setattr("wisper.main.CONFIG_PATH", path)

        _persist_config_value("audio", "device", "Yeti")

        raw = path.read_bytes()
        assert b"\r\n" in raw
        # jedes \n gehört zu einem \r\n — keine gemischten Zeilenenden
        assert raw.count(b"\n") == raw.count(b"\r\n")
        assert b'device = "Yeti"' in raw

    def test_result_stays_valid_toml(self, config):
        _persist_config_value("audio", "device", "Shure MV7+")
        _persist_config_value("ollama", "cleanup_enabled", False)
        _persist_config_value("model", "size", "large-v3")

        data = tomllib.loads(read(config))
        assert data["audio"]["device"] == "Shure MV7+"
        assert data["ollama"]["cleanup_enabled"] is False
        assert data["model"]["size"] == "large-v3"


class TestValueFormatting:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, "true"),
            (False, "false"),
            (3, "3"),
            ("Yeti", '"Yeti"'),
            ('Mikro "X"', '"Mikro \\"X\\""'),
            ("C:\\Pfad", '"C:\\\\Pfad"'),
        ],
    )
    def test_toml_scalar(self, value, expected):
        assert _toml_scalar(value) == expected

    def test_bool_is_not_written_as_int(self, config):
        _persist_config_value("ui", "sound_feedback", False)
        assert "sound_feedback = false" in read(config)

    def test_quotes_survive_roundtrip(self, config):
        _persist_config_value("audio", "device", 'Mikro "Alt"')
        assert tomllib.loads(read(config))["audio"]["device"] == 'Mikro "Alt"'

    @pytest.mark.parametrize(
        "rest,expected",
        [
            ('true  # Kommentar', '  # Kommentar'),
            ('"wert"', ''),
            ('"wert # kein Kommentar"', ''),
            ('"a" # danach', ' # danach'),
        ],
    )
    def test_trailing_comment(self, rest, expected):
        assert _trailing_comment(rest) == expected


class TestFailureModes:
    def test_a_missing_user_file_is_created(self, tmp_path, monkeypatch):
        """Seit Phase 20 ist die fehlende Nutzerdatei der normale Erststart."""
        target = tmp_path / "gibtsnicht.toml"
        monkeypatch.setattr("wisper.main.CONFIG_PATH", target)
        assert _persist_config_value("audio", "device", "Yeti") is True
        assert target.exists()
        assert 'device = "Yeti"' in target.read_text(encoding="utf-8")

    def test_an_unwritable_place_returns_false(self, tmp_path, monkeypatch):
        """Liegt eine Datei im Weg, laesst sich das Verzeichnis nicht anlegen."""
        blocker = tmp_path / "blockiert"
        blocker.write_text("keine Datei zum Hineinschreiben", encoding="utf-8")
        monkeypatch.setattr("wisper.main.CONFIG_PATH", blocker / "config.toml")
        assert _persist_config_value("audio", "device", "Yeti") is False

    def test_write_error_leaves_original_untouched(self, config, monkeypatch):
        before = read(config)

        def boom(*_args, **_kwargs):
            raise OSError("Simulierter Schreibfehler")

        monkeypatch.setattr("wisper.main.os.replace", boom)

        assert _persist_config_value("audio", "device", "Yeti") is False
        assert read(config) == before

    def test_no_temp_file_left_behind(self, config, monkeypatch):
        def boom(*_args, **_kwargs):
            raise OSError("Simulierter Schreibfehler")

        monkeypatch.setattr("wisper.main.os.replace", boom)
        _persist_config_value("audio", "device", "Yeti")

        assert not (config.parent / "config.toml.tmp").exists()

    def test_success_leaves_no_temp_file(self, config):
        _persist_config_value("audio", "device", "Yeti")
        assert not (config.parent / "config.toml.tmp").exists()
