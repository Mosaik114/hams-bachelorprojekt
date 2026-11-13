"""Hams records 16 kHz mono, whatever an old config file says (audit finding H1).

`[audio] sample_rate` and `channels` used to be settings. Nothing converted the audio, so 48 kHz reached
Whisper as if it were 16 kHz (three times too slow) and stereo arrived interleaved: both came out as
gibberish, without any error. The options are gone; a file that still sets them is ignored with a warning.
"""

import logging

from hams import config as config_mod
from hams.recording import CHANNELS, SAMPLE_RATE


def test_whisper_format_is_fixed():
    assert (SAMPLE_RATE, CHANNELS) == (16_000, 1)
    assert not hasattr(config_mod.Config(), "sample_rate")
    assert not hasattr(config_mod.Config(), "channels")


def test_the_recording_opens_16_khz_mono(live_app, hams):
    live_app.toggle_recording()

    stream = hams.streams[-1]
    assert stream.kwargs["samplerate"] == 16_000
    assert stream.kwargs["channels"] == 1


def test_the_level_monitor_opens_16_khz_mono(live_app, hams):
    live_app._open_settings()
    live_app.root.update()

    monitor = hams.streams[-1]
    assert (monitor.kwargs["samplerate"], monitor.kwargs["channels"]) == (16_000, 1)


class TestOldConfigFiles:
    def _load(self, tmp_path, monkeypatch, text: str):
        path = tmp_path / "config.toml"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
        return config_mod._load_config()

    def test_a_different_rate_or_channel_count_is_ignored_with_one_warning(self, tmp_path, monkeypatch, caplog):
        with caplog.at_level(logging.WARNING, logger="hams"):
            cfg = self._load(tmp_path, monkeypatch, "[audio]\nsample_rate = 48000\nchannels = 2\n")

        warnings = [r.getMessage() for r in caplog.records if "no longer settings" in r.getMessage()]
        assert len(warnings) == 1
        assert "audio.sample_rate = 48000" in warnings[0]
        assert "audio.channels = 2" in warnings[0]
        assert cfg.max_recording_seconds == config_mod.Config().max_recording_seconds

    def test_the_old_template_values_are_ignored_silently(self, tmp_path, monkeypatch, caplog):
        """Every existing user file is a copy of the old template: 16000/1 is no reason to warn on each start."""
        with caplog.at_level(logging.WARNING, logger="hams"):
            self._load(tmp_path, monkeypatch, "[audio]\nsample_rate = 16000\nchannels = 1\n")

        assert not [r for r in caplog.records if "no longer settings" in r.getMessage()]

    def test_the_template_no_longer_offers_them(self):
        text = config_mod.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        assert "sample_rate" not in text
        assert "\nchannels" not in text
