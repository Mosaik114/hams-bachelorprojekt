"""Die Anwendung muss auch mit einer fehlerhaften Nutzerkonfiguration starten.

Das Laden ist dafuer gebaut, ungueltige Werte zu *melden* und weiter laufen
zu lassen. Das setzt voraus, dass es beim Melden schon ein Log gibt:
`bootstrap.load()` richtet erst das Log ein und liest dann die Einstellungen.

Sichtbar wird das fuer den Nutzer gar nicht — Hams startet aus dem Autostart
ueber pythonw.exe ohne Konsole. Die Anwendung kaeme einfach nicht hoch, ohne
Fenster, ohne Meldung und ohne Logzeile.

Der Test startet deshalb einen echten Interpreter, so wie der Autostart: im
laufenden Testprozess ist das Log laengst eingerichtet.
"""

import logging
import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from hams import cli, main, setup_autostart
from hams.config import Config, _apply_toml

PROJECT_ROOT = Path(__file__).parent.parent

#: Je ein Wert, den das Laden meldet und uebergeht.
BROKEN_FILES = {
    "max_recording_seconds": '[audio]\nmax_recording_seconds = 99999\n',
    "model_size": '[model]\nsize = "gibtsnicht"\n',
    "timeout_cleanup": '[ollama]\ntimeout_cleanup = 0\n',
    "paste_timeout": '[clipboard]\npaste_timeout = -1.0\n',
}


def _import_in_a_fresh_interpreter(config_file: Path) -> subprocess.CompletedProcess:
    """Importiert hams.main so, wie der Autostart es tut: von vorn."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""
            import hams.main
            from hams import bootstrap
            bootstrap.load()
            print("IMPORT-OK", hams.main.CFG.max_recording_seconds)
        """),
        ],
        cwd=PROJECT_ROOT,
        # Die Umgebung wird geerbt und nur ergaenzt. Eine kuenstlich magere
        # Umgebung ohne SystemDrive/ProgramData laesst Windows Pfade nicht
        # aufloesen und legt einen Ordner namens "%SystemDrive%" im Projekt an.
        env={
            **os.environ,
            "HAMS_CONFIG": str(config_file),
            # Auch der Kindprozess schreibt sonst ins echte Log.
            "HAMS_LOG_DIR": str(config_file.parent / "logs"),
        },
        capture_output=True,
        text=True,
        timeout=300,
    )


class TestABrokenConfigDoesNotStopTheStart:
    """Zwei echte Interpreterstarts, mehr braucht es nicht.

    Die Reihenfolge auf Modulebene ist *eine* Eigenschaft, keine neun. Welcher
    Wert die Warnung ausloest, ist dafuer gleichgueltig — dass ueberhaupt eine
    ausgeloest wird, genuegt. Die einzelnen Faelle prueft die Klasse weiter
    unten im selben Prozess, ohne jedes Mal Python neu zu starten.
    """

    def test_the_app_still_comes_up(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(BROKEN_FILES["max_recording_seconds"], encoding="utf-8")

        result = _import_in_a_fresh_interpreter(config_file)

        assert "NameError" not in result.stderr, result.stderr
        assert result.returncode == 0, result.stderr
        assert "IMPORT-OK" in result.stdout

    def test_the_broken_value_is_reported(self, tmp_path):
        """Durchlassen heisst nicht verschweigen."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(BROKEN_FILES["max_recording_seconds"], encoding="utf-8")

        result = _import_in_a_fresh_interpreter(config_file)

        assert "Config: audio.max_recording_seconds" in result.stderr
        assert "99999" in result.stderr


class TestEveryErrorIsReported:
    @pytest.mark.parametrize("regel", sorted(BROKEN_FILES))
    def test_each_broken_file_is_reported_while_loading(self, regel, caplog):
        """Der Weg von der Datei bis zur Meldung, ohne Interpreterstart: genau eine Warnung, kein Abbruch."""
        cfg = Config()
        with caplog.at_level(logging.WARNING, logger="hams"):
            _apply_toml(cfg, tomllib.loads(BROKEN_FILES[regel]))
        assert len([r for r in caplog.records if r.getMessage().startswith("Config: ")]) == 1

    def test_a_broken_url_is_refused_already_while_loading(self):
        """Die Adresse bekommt jedes Diktat - sie wird gar nicht erst uebernommen."""
        cfg = Config()
        _apply_toml(cfg, tomllib.loads('[ollama]\nurl = "ftp://127.0.0.1:11434/api/generate"\n'))

        assert cfg.ollama_url == Config().ollama_url


ISS = (Path(__file__).resolve().parent.parent / "packaging" / "hams.iss").read_text(encoding="utf-8-sig")


class _Root:
    def __init__(self):
        self.idle = []

    def after_idle(self, fn):
        self.idle.append(fn)


class TestTheWindowOnStart:
    @pytest.fixture
    def app(self):
        return SimpleNamespace(_show_window="show", _open_setup="setup", _open_settings="settings")

    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            ({}, ["show"]),
            ({"background": True}, []),
            ({"setup": True}, ["setup"]),
            ({"setup": True, "background": True}, ["setup"]),
            ({"settings": True}, ["show", "settings"]),
            ({"settings": True, "background": True}, ["settings"]),
        ],
    )
    def test_only_the_autostart_starts_hidden(self, app, flags, expected):
        root = _Root()
        options = {"setup": False, "settings": False, "background": False, **flags}
        main._first_windows(root, app, **options)
        assert root.idle == expected

    def test_the_command_line_passes_background_on(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(main, "main", lambda **kwargs: seen.update(kwargs))
        assert cli.run(["--background"]) == 0
        assert seen == {"show_settings": False, "show_setup": False, "background": True}
        cli.run([])
        assert seen["background"] is False

    def test_the_installer_autostart_entry_and_only_it_starts_in_the_background(self):
        run_value = next(line for line in ISS.splitlines() if "CurrentVersion\\Run" in line and "ValueData" in line)
        assert "--background" in run_value
        icons_and_launch = [line for line in ISS.splitlines() if line.startswith(("Name:", "Filename:"))]
        assert icons_and_launch and not any("--background" in line for line in icons_and_launch)

    def test_a_source_autostart_entry_starts_in_the_background(self, monkeypatch):
        monkeypatch.setattr(setup_autostart.sys, "frozen", True, raising=False)
        assert setup_autostart._resolve_launch_command().endswith(" --background")

    def test_the_start_script_passes_its_arguments_on(self):
        script = (setup_autostart.PROJECT_ROOT / "scripts" / "start_hams.vbs").read_text(encoding="utf-8")
        assert "WScript.Arguments" in script and "& args" in script

    @pytest.mark.skipif(sys.platform == "win32", reason="hams.linux needs Linux")
    def test_the_linux_service_is_the_autostart_and_starts_in_the_background(self):
        from hams import linux

        assert '"/main.py" --background' in linux.service_unit("/py", "/main.py")
