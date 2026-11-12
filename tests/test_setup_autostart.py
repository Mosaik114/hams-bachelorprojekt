"""Autostart unter Windows: Run-Eintrag „Hams“, der alte Eintrag „Wisper“ wird abgeloest.

Die Registry wird nie angefasst: `winreg` ist durch ein Woerterbuch ersetzt.
"""

import contextlib

import pytest

from hams import setup_autostart as autostart


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, values=None):
        self.values = dict(values or {})

    def OpenKey(self, root, path, _reserved, _access):
        assert (root, path) == (self.HKEY_CURRENT_USER, autostart.REGISTRY_KEY)
        return contextlib.nullcontext(self)

    def SetValueEx(self, _key, name, _reserved, kind, value):
        assert kind == self.REG_SZ
        self.values[name] = value

    def DeleteValue(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.fixture
def registry(monkeypatch):
    fake = _FakeWinreg({"Anderes": "bleibt"})
    monkeypatch.setattr(autostart, "winreg", fake, raising=False)
    return fake


class TestNames:
    def test_the_entry_is_called_hams(self):
        assert autostart.APP_NAME == "Hams"
        assert autostart.LEGACY_APP_NAME == "Wisper"

    def test_the_start_script_ships_in_scripts(self):
        assert autostart.VBS_PATH == autostart.PROJECT_ROOT / "scripts" / "start_hams.vbs"
        assert autostart.VBS_PATH.exists()

    def test_the_start_script_finds_the_package_one_level_up(self):
        text = autostart.VBS_PATH.read_text(encoding="utf-8")
        assert 'projectDir = fso.GetParentFolderName(scriptDir)' in text
        assert 'fso.BuildPath(projectDir, "hams\\main.py")' in text
        assert "wisper" not in text.lower()

    def test_the_command_prefers_the_start_script(self):
        assert autostart._resolve_launch_command() == f'wscript.exe //B "{autostart.VBS_PATH}"'

    def test_without_the_start_script_python_runs_main_directly(self, monkeypatch, tmp_path):
        monkeypatch.setattr(autostart, "VBS_PATH", tmp_path / "fehlt.vbs")
        command = autostart._resolve_launch_command()
        assert str(autostart.PROJECT_ROOT / "hams" / "main.py") in command
        assert "pythonw.exe" in command


class TestWindowsEntry:
    def test_setting_up_writes_hams(self, registry, capsys):
        autostart.add_autostart()
        assert registry.values["Hams"] == autostart._resolve_launch_command()
        assert registry.values["Anderes"] == "bleibt"
        assert "ersetzt" not in capsys.readouterr().out

    def test_setting_up_replaces_the_old_entry(self, registry, capsys):
        """Sonst starteten beim Login zwei Fassungen und nur der Zufall entschiede, welche bleibt."""
        registry.values["Wisper"] = 'wscript.exe //B "C:\\alt\\start_wisper.vbs"'
        autostart.add_autostart()
        assert "Wisper" not in registry.values
        assert "Hams" in registry.values
        assert "Wisper" in capsys.readouterr().out

    def test_removing_takes_both_entries(self, registry, capsys):
        registry.values.update({"Hams": "neu", "Wisper": "alt"})
        autostart.remove_autostart()
        assert registry.values == {"Anderes": "bleibt"}
        assert "entfernt" in capsys.readouterr().out

    def test_removing_nothing_says_so(self, registry, capsys):
        autostart.remove_autostart()
        assert registry.values == {"Anderes": "bleibt"}
        assert "Kein Autostart-Eintrag" in capsys.readouterr().out
