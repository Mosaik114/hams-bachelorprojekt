"""Saved transcripts: a switch, a retention period, and nothing on disk by default (audit finding H3).

Every dictation used to be written to a plain-text file, forever and without a way to turn it off. The tray
history does not depend on those files (it lives in memory), so a new configuration saves nothing. A user file
from before the option has no `[output]` keys and keeps the old behaviour: every text saved, kept forever.
"""

import os
import threading
import time
import tomllib

import pytest

from hams import config as config_mod
from hams import transcription as transcription_mod
from hams.config import CFG
from hams.transcription import prune_transcripts

DAY = 86_400


def _load(tmp_path, monkeypatch, text: str):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
    return config_mod._load_config()


class TestDefaults:
    def test_the_template_saves_nothing_and_keeps_30_days(self):
        template = tomllib.loads(config_mod.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        assert template["output"] == {"save_transcripts": False, "keep_days": 30, "transcripts_dir": ""}

    def test_a_new_user_file_saves_nothing(self, tmp_path, monkeypatch):
        cfg = _load(tmp_path, monkeypatch, config_mod.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        assert (cfg.save_transcripts, cfg.transcripts_keep_days) == (False, 30)

    def test_an_existing_file_without_the_option_keeps_saving_forever(self, tmp_path, monkeypatch):
        cfg = _load(tmp_path, monkeypatch, '[model]\nsize = "small"\n')
        assert (cfg.save_transcripts, cfg.transcripts_keep_days) == (True, 0)

    def test_switching_off_in_an_old_file_keeps_its_unlimited_retention(self, tmp_path, monkeypatch):
        cfg = _load(tmp_path, monkeypatch, "[output]\nsave_transcripts = false\n")
        assert (cfg.save_transcripts, cfg.transcripts_keep_days) == (False, 0)

    def test_the_user_values_win(self, tmp_path, monkeypatch):
        cfg = _load(tmp_path, monkeypatch, "[output]\nsave_transcripts = true\nkeep_days = 7\n")
        assert (cfg.save_transcripts, cfg.transcripts_keep_days) == (True, 7)

    @pytest.mark.parametrize("value", [-1, 4000, "7", 1.5])
    def test_a_wrong_keep_days_is_ignored(self, tmp_path, monkeypatch, value):
        cfg = _load(
            tmp_path, monkeypatch, f"[output]\nsave_transcripts = true\nkeep_days = {value!r}\n".replace("'", '"')
        )
        assert cfg.transcripts_keep_days == 30


def _wait_for_save() -> None:
    end = time.monotonic() + 2
    while any(t.name == "save-transcript" and t.is_alive() for t in threading.enumerate()):
        assert time.monotonic() < end
        time.sleep(0.01)


class TestDelivery:
    def test_nothing_is_written_when_saving_is_off(self, live_app, hams):
        CFG.save_transcripts = False
        live_app.pipeline._deliver("geheim")
        _wait_for_save()
        assert hams.clipboard.copies == ["geheim"]
        assert live_app._history[0][2] == "geheim", "the tray history does not need the file"
        assert not transcription_mod.OUTPUT_DIR.exists()

    def test_a_file_is_written_when_saving_is_on(self, live_app, hams):
        CFG.save_transcripts = True
        live_app.pipeline._deliver("behalten")
        _wait_for_save()
        files = list(transcription_mod.OUTPUT_DIR.glob("*.txt"))
        assert [f.read_text(encoding="utf-8") for f in files] == ["behalten"]


def _transcript(folder, name: str, age_days: float, now: float):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("x", encoding="utf-8")
    os.utime(path, (now - age_days * DAY, now - age_days * DAY))
    return path


class TestRetention:
    def test_old_transcripts_go_recent_ones_stay(self, tmp_path):
        now = time.time()
        old = _transcript(tmp_path, "2026-01-01_10-00-00.txt", 31, now)
        old_second = _transcript(tmp_path, "2026-01-01_10-00-00_2.txt", 40, now)
        recent = _transcript(tmp_path, "2026-11-01_10-00-00.txt", 29, now)
        assert prune_transcripts(tmp_path, 30, now) == 2
        assert not old.exists() and not old_second.exists()
        assert recent.exists()

    def test_files_hams_did_not_name_are_left_alone(self, tmp_path):
        now = time.time()
        mine = _transcript(tmp_path, "notizen.txt", 400, now)
        other = _transcript(tmp_path, "2026-01-01_10-00-00.md", 400, now)
        assert prune_transcripts(tmp_path, 30, now) == 0
        assert mine.exists() and other.exists()

    def test_zero_keeps_everything(self, tmp_path):
        now = time.time()
        old = _transcript(tmp_path, "2020-01-01_10-00-00.txt", 2000, now)
        assert prune_transcripts(tmp_path, 0, now) == 0
        assert old.exists()

    def test_a_missing_folder_is_fine(self, tmp_path):
        assert prune_transcripts(tmp_path / "gibt-es-nicht", 30) == 0

    def test_saving_applies_the_retention(self, live_app, hams):
        now = time.time()
        old = _transcript(transcription_mod.OUTPUT_DIR, "2026-01-01_10-00-00.txt", 31, now)
        CFG.transcripts_keep_days = 30
        transcription_mod.save_transcript_file("neu")
        assert not old.exists()
        assert len(list(transcription_mod.OUTPUT_DIR.glob("*.txt"))) == 1

    def test_the_start_applies_it_too_even_with_saving_off(self, live_app, hams):
        now = time.time()
        old = _transcript(transcription_mod.OUTPUT_DIR, "2026-01-01_10-00-00.txt", 31, now)
        CFG.save_transcripts = False
        CFG.transcripts_keep_days = 30
        transcription_mod.prune_transcripts_async()
        end = time.monotonic() + 2
        while old.exists():
            assert time.monotonic() < end, "start did not prune"
            time.sleep(0.01)


class TestSettingsSwitch:
    def test_the_switch_is_saved(self, live_app, hams):
        CFG.save_transcripts = False
        live_app._open_settings()
        live_app.root.update()
        general = live_app.settings_win.general
        assert (
            general.row_transcripts.sub_text
            == f"Behält jeden eingefügten Text {CFG.transcripts_keep_days} Tage als Datei"
        )
        general.switch_transcripts.toggle()
        assert CFG.save_transcripts is True
        assert ("output", "save_transcripts", True) in hams.saved

    def test_unlimited_retention_is_named_as_such(self):
        from hams.settings_sections import GeneralSection

        assert GeneralSection.transcripts_hint(0) == "Behält jeden eingefügten Text als Datei"


class TestFolder:
    """`[output] transcripts_dir` and the row "Speicherort" (Settings -> General)."""

    def test_empty_means_the_data_folder(self, tmp_path, monkeypatch):
        cfg = _load(tmp_path, monkeypatch, "[output]\ntranscripts_dir = \"\"\n")
        assert cfg.transcripts_dir == ""
        monkeypatch.setattr(CFG, "transcripts_dir", "")
        assert transcription_mod.transcripts_folder() == transcription_mod.OUTPUT_DIR

    def test_an_absolute_folder_is_used(self, tmp_path, monkeypatch):
        folder = tmp_path / "Diktate"
        cfg = _load(tmp_path, monkeypatch, f"[output]\ntranscripts_dir = '{folder}'\n")
        assert cfg.transcripts_dir == str(folder)

    @pytest.mark.parametrize("value", ["'relativ\\ordner'", "3"])
    def test_a_relative_or_wrong_value_is_ignored(self, tmp_path, monkeypatch, caplog, value):
        cfg = _load(tmp_path, monkeypatch, f"[output]\ntranscripts_dir = {value}\n")
        assert cfg.transcripts_dir == ""
        assert "transcripts_dir" in caplog.text

    def test_saving_and_pruning_use_the_chosen_folder(self, tmp_path, monkeypatch):
        folder = tmp_path / "eigener Ordner"
        monkeypatch.setattr(CFG, "transcripts_dir", str(folder))
        monkeypatch.setattr(CFG, "transcripts_keep_days", 30)
        now = time.time()
        old = _transcript(folder, "2020-01-01_10-00-00.txt", 40, now)
        transcription_mod.save_transcript_file("Hallo")
        saved = [p for p in folder.iterdir() if p != old]
        assert len(saved) == 1 and saved[0].read_text(encoding="utf-8") == "Hallo"
        assert not old.exists(), "the retention applies to the chosen folder"

    def test_usable_folder(self, tmp_path):
        assert transcription_mod.usable_transcripts_folder(tmp_path / "neu" / "tiefer")
        assert not transcription_mod.usable_transcripts_folder(tmp_path.relative_to(tmp_path.anchor))
        blocker = tmp_path / "datei"
        blocker.write_text("x", encoding="utf-8")
        assert not transcription_mod.usable_transcripts_folder(blocker / "drin")

    def _general(self, live_app):
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win.general

    def test_the_row_shows_the_path_and_changes_it(self, live_app, hams, tmp_path, monkeypatch):
        monkeypatch.setattr(CFG, "transcripts_dir", "")
        general = self._general(live_app)
        assert general.row_folder._title_text == "Speicherort"
        assert general.row_folder.sub_text == str(transcription_mod.transcripts_folder())
        chosen = tmp_path / "Diktate"
        asked = []
        monkeypatch.setattr(general, "choose_folder", lambda **kw: asked.append(kw) or str(chosen))
        general.folder_change_button._invoke()
        assert asked[0]["title"] == "Ordner für die Transkripte wählen"
        assert CFG.transcripts_dir == str(chosen)
        assert ("output", "transcripts_dir", str(chosen)) in hams.saved
        assert general.row_folder.sub_text == str(chosen)
        monkeypatch.setattr(general, "choose_folder", lambda **kw: str(transcription_mod.OUTPUT_DIR))
        general.change_transcripts_folder()
        assert CFG.transcripts_dir == "", "the data folder itself is stored as empty"

    def test_a_cancelled_or_unusable_choice_changes_nothing(self, live_app, hams, tmp_path, monkeypatch):
        monkeypatch.setattr(CFG, "transcripts_dir", "")
        general = self._general(live_app)
        monkeypatch.setattr(general, "choose_folder", lambda **kw: "")
        general.change_transcripts_folder()
        blocker = tmp_path / "datei"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(general, "choose_folder", lambda **kw: str(blocker / "drin"))
        general.change_transcripts_folder()
        assert CFG.transcripts_dir == ""
        assert general.row_folder.sub_text == "Dort kann Hams nicht schreiben · der bisherige Ordner bleibt"
        assert not [entry for entry in hams.saved if entry[1] == "transcripts_dir"]

    def test_open_creates_and_opens_the_folder(self, live_app, tmp_path, monkeypatch):
        folder = tmp_path / "noch nicht da"
        monkeypatch.setattr(CFG, "transcripts_dir", str(folder))
        general = self._general(live_app)
        opened = []
        monkeypatch.setattr(transcription_mod, "OUTPUT_DIR", transcription_mod.OUTPUT_DIR)
        from hams import support

        monkeypatch.setattr(support, "open_path", opened.append)
        general.folder_open_button._invoke()
        assert opened == [folder] and folder.is_dir()

        def fail(path):
            raise OSError("kein Explorer")

        monkeypatch.setattr(support, "open_path", fail)
        general.open_transcripts_folder()
        assert general.row_folder.sub_text == f"Ordner ließ sich nicht öffnen · {folder}"
