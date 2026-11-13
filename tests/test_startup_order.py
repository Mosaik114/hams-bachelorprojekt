"""Die Anwendung muss auch mit einer fehlerhaften Nutzerkonfiguration starten.

Das Laden ist dafuer gebaut, ungueltige Werte zu *melden* und weiter laufen
zu lassen. Das setzt voraus, dass es beim Melden schon ein Log gibt:
`CFG = _load_config()` laeuft auf Modulebene, und wenn `logger` dort noch nicht
existiert, endet der Import in einem NameError.

Sichtbar wird das fuer den Nutzer gar nicht — Hams startet aus dem Autostart
ueber pythonw.exe ohne Konsole. Die Anwendung kaeme einfach nicht hoch, ohne
Fenster, ohne Meldung und ohne Logzeile.

Der Test startet deshalb einen echten Interpreter. Im laufenden Testprozess
ist das Modul laengst importiert; die Reihenfolge auf Modulebene laesst sich
nur von aussen pruefen.
"""

import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import tomllib

from hams.config import Config, _apply_toml

PROJECT_ROOT = Path(__file__).parent.parent

#: Je ein Wert, den das Laden meldet und uebergeht.
KAPUTTE_DATEIEN = {
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


class TestEineKaputteKonfigurationHaeltNichtAuf:
    """Zwei echte Interpreterstarts, mehr braucht es nicht.

    Die Reihenfolge auf Modulebene ist *eine* Eigenschaft, keine neun. Welcher
    Wert die Warnung ausloest, ist dafuer gleichgueltig — dass ueberhaupt eine
    ausgeloest wird, genuegt. Die einzelnen Faelle prueft die Klasse weiter
    unten im selben Prozess, ohne jedes Mal Python neu zu starten.
    """

    def test_the_app_still_comes_up(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(KAPUTTE_DATEIEN["max_recording_seconds"], encoding="utf-8")

        result = _import_in_a_fresh_interpreter(config_file)

        assert "NameError" not in result.stderr, result.stderr
        assert result.returncode == 0, result.stderr
        assert "IMPORT-OK" in result.stdout

    def test_the_broken_value_is_reported(self, tmp_path):
        """Durchlassen heisst nicht verschweigen."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(KAPUTTE_DATEIEN["max_recording_seconds"], encoding="utf-8")

        result = _import_in_a_fresh_interpreter(config_file)

        assert "Config: audio.max_recording_seconds" in result.stderr
        assert "99999" in result.stderr


class TestJederFehlerWirdGemeldet:
    @pytest.mark.parametrize("regel", sorted(KAPUTTE_DATEIEN))
    def test_each_broken_file_is_reported_while_loading(self, regel, caplog):
        """Der Weg von der Datei bis zur Meldung, ohne Interpreterstart: genau eine Warnung, kein Abbruch."""
        cfg = Config()
        with caplog.at_level(logging.WARNING, logger="hams"):
            _apply_toml(cfg, tomllib.loads(KAPUTTE_DATEIEN[regel]))
        assert len([r for r in caplog.records if r.getMessage().startswith("Config: ")]) == 1

    def test_a_broken_url_is_refused_already_while_loading(self):
        """Die Adresse bekommt jedes Diktat - sie wird gar nicht erst uebernommen."""
        cfg = Config()
        _apply_toml(cfg, tomllib.loads('[ollama]\nurl = "ftp://127.0.0.1:11434/api/generate"\n'))

        assert cfg.ollama_url == Config().ollama_url
