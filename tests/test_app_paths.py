"""Run-time data lives outside the program folder (hams/app_paths.py)."""

import sys
from pathlib import Path

from hams import app_paths, config, instance, setup_autostart


class TestLocalDataDir:
    def test_the_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HAMS_DATA_DIR", str(tmp_path))
        assert app_paths.local_data_dir() == tmp_path
        assert app_paths.default_log_dir() == tmp_path / "logs"
        assert app_paths.transcripts_dir() == tmp_path / "transcripts"
        assert app_paths.cuda_dir() == tmp_path / "cuda"

    def test_windows_uses_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HAMS_DATA_DIR", raising=False)
        monkeypatch.setattr(app_paths.sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        assert app_paths.local_data_dir() == tmp_path / "Local" / app_paths.DATA_DIR_NAME

    def test_windows_without_localappdata(self, monkeypatch):
        monkeypatch.delenv("HAMS_DATA_DIR", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setattr(app_paths.sys, "platform", "win32")
        assert app_paths.local_data_dir().parent == Path.home() / "AppData" / "Local"

    def test_elsewhere_xdg_data_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HAMS_DATA_DIR", raising=False)
        monkeypatch.setattr(app_paths.sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert app_paths.local_data_dir() == tmp_path / app_paths.DATA_DIR_NAME
        monkeypatch.delenv("XDG_DATA_HOME")
        assert app_paths.local_data_dir().parent == Path.home() / ".local" / "share"

    def test_never_inside_the_package(self):
        assert config.APP_DIR not in app_paths.local_data_dir().parents

    def test_the_suite_never_sees_the_real_folder(self):
        assert "hams-tests-data-" in str(app_paths.local_data_dir())


class TestFrozen:
    def test_not_frozen_in_the_tests(self):
        assert not app_paths.is_frozen()

    def test_frozen_flag(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert app_paths.is_frozen()

    def test_autostart_starts_the_exe_when_frozen(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", r"C:\Programme\Hams\Hams.exe")
        assert setup_autostart._resolve_launch_command() == r'"C:\Programme\Hams\Hams.exe" --background'

    def test_restart_starts_the_exe_without_a_script_when_frozen(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", r"C:\Programme\Hams\Hams.exe")
        assert instance.restart_command() == [r"C:\Programme\Hams\Hams.exe"]
