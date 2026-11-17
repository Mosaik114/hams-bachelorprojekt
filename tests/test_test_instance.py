"""A test instance: own folders, lock and events, no hotkeys - so the installer smoke test never meets the real Hams."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hams import test_instance

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in (test_instance.ENV, "HAMS_DATA_DIR", "HAMS_CONFIG", "HF_HUB_CACHE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    return tmp_path


class TestApply:
    def test_the_normal_hams_changes_nothing(self, clean_env):
        assert test_instance.apply([]) == ""
        assert "HAMS_DATA_DIR" not in os.environ and "HAMS_CONFIG" not in os.environ
        assert test_instance.suffix() == "" and test_instance.argument() == ""

    def test_from_the_command_line(self, clean_env):
        assert test_instance.apply(["--background", "--test-instance=smoke"]) == "smoke"
        assert os.environ[test_instance.ENV] == "smoke"
        local, roaming = clean_env / "Local" / "Hams-test-smoke", clean_env / "Roaming" / "Hams-test-smoke"
        assert Path(os.environ["HAMS_DATA_DIR"]) == local
        assert Path(os.environ["HAMS_CONFIG"]) == roaming / "config.toml"
        assert Path(os.environ["HF_HUB_CACHE"]) == local / "huggingface" / "hub"
        assert test_instance.suffix() == "-test-smoke"
        assert test_instance.argument() == "--test-instance=smoke"

    def test_two_tokens_work_too(self, clean_env):
        assert test_instance.apply(["--test-instance", "x1"]) == "x1"

    def test_from_the_environment(self, clean_env, monkeypatch):
        monkeypatch.setenv(test_instance.ENV, "ci")
        assert test_instance.apply([]) == "ci"

    def test_settings_already_set_stay(self, clean_env, monkeypatch):
        """The test suite points HAMS_CONFIG elsewhere itself; a test instance does not override it."""
        monkeypatch.setenv("HAMS_CONFIG", "C:/elsewhere/config.toml")
        test_instance.apply(["--test-instance=smoke"])
        assert os.environ["HAMS_CONFIG"] == "C:/elsewhere/config.toml"

    @pytest.mark.parametrize("name", ["", "a b", "..\\x", "x" * 41, "ä"])
    def test_an_invalid_name_is_no_test_instance(self, clean_env, name):
        assert test_instance.apply([f"--test-instance={name}"]) == ""
        assert test_instance.suffix() == ""


def test_lock_and_events_are_its_own():
    """In a fresh process (the names are fixed at import): only its own lock, not the real nor the Wisper one."""
    code = (
        f"import json, sys; sys.path.insert(0, {str(ROOT)!r})\n"
        "from hams import test_instance; test_instance.apply(['--test-instance=smoke'])\n"
        "from hams import instance, activation\n"
        "print(json.dumps([instance.MUTEX_NAMES, activation.SETTINGS_EVENT, activation.SHOW_EVENT]))\n"
    )
    env = {key: value for key, value in os.environ.items() if key != test_instance.ENV}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    names, settings, show = json.loads(out.stdout.strip().splitlines()[-1])
    assert names == ["Global\\Hams-SingleInstance-7a3f2c1b-test-smoke"]
    assert settings.endswith("-test-smoke") and show.endswith("-test-smoke")


def test_the_normal_names_stay_as_they_were():
    from hams import activation, instance

    assert instance.MUTEX_NAMES == (instance.MUTEX_NAME, instance.LEGACY_MUTEX_NAME)
    assert activation.SETTINGS_EVENT == "Local\\Hams.OpenSettings-7a3f2c1b"
    assert activation.SHOW_EVENT == "Local\\Hams.ShowWindow-7a3f2c1b"


def test_a_test_instance_takes_no_hotkeys(monkeypatch):
    """Next to the user's Hams it would otherwise start a recording on the user's key as well."""
    from hams import app

    monkeypatch.setenv(test_instance.ENV, "smoke")
    calls = []

    class Fake:
        _system_hotkeys = type("S", (), {"register": lambda self, entries: calls.append(entries) or set()})()

    app.FloatingTranscriberApp._register_hotkeys(Fake())
    assert calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 mutex")
def test_held_elsewhere_sees_a_lock_held_by_another_process():
    from hams import instance

    probe = instance.MUTEX_NAMES[0] + "-probe"
    code = (
        "import ctypes, sys\n"
        f"h = ctypes.windll.kernel32.CreateMutexW(None, False, {probe!r})\n"
        "print('ready', flush=True); sys.stdin.readline()\n"
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        api = instance._kernel32()
        assert api.exists(instance.MUTEX_NAMES[0] + "-probe")
        assert not api.exists(instance.MUTEX_NAMES[0] + "-nobody-holds-this")
    finally:
        child.communicate("\n", timeout=10)
