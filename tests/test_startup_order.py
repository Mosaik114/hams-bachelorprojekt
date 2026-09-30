"""Die Anwendung muss auch mit einer fehlerhaften Nutzerkonfiguration starten.

`validate_config` ist dafuer gebaut, ungueltige Werte zu *melden* und weiter
laufen zu lassen. Das setzt voraus, dass es beim Melden schon ein Log gibt:
`CFG = _load_config()` laeuft auf Modulebene, und wenn `logger` dort noch nicht
existiert, endet der Import in einem NameError.

Sichtbar wird das fuer den Nutzer gar nicht — Wisper startet aus dem Autostart
ueber pythonw.exe ohne Konsole. Die Anwendung kaeme einfach nicht hoch, ohne
Fenster, ohne Meldung und ohne Logzeile.

Der Test startet deshalb einen echten Interpreter. Im laufenden Testprozess
ist das Modul laengst importiert; die Reihenfolge auf Modulebene laesst sich
nur von aussen pruefen.
"""

import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

from wisper.config_validation import validate_config
from wisper.main import Config, _apply_toml

PROJECT_ROOT = Path(__file__).parent.parent

#: Je ein Wert, der genau eine Regel in `validate_config` reisst.
KAPUTTE_DATEIEN = {
    "sample_rate": '[audio]\nsample_rate = 12345\n',
    "channels": '[audio]\nchannels = 7\n',
    "max_recording_seconds": '[audio]\nmax_recording_seconds = 99999\n',
    "model_size": '[model]\nsize = "gibtsnicht"\n',
    "beam_size": '[transcription]\nbeam_size = 99\n',
    "timeout_cleanup": '[ollama]\ntimeout_cleanup = 0\n',
    "paste_timeout": '[clipboard]\npaste_timeout = -1.0\n',
    "url": '[ollama]\nurl = "ftp://127.0.0.1:11434/api/generate"\n',
}


def _import_in_a_fresh_interpreter(config_file: Path) -> subprocess.CompletedProcess:
    """Importiert wisper.main so, wie der Autostart es tut: von vorn."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import wisper.main
            print("IMPORT-OK", wisper.main.CFG.sample_rate)
        """)],
        cwd=PROJECT_ROOT,
        # Die Umgebung wird geerbt und nur ergaenzt. Eine kuenstlich magere
        # Umgebung ohne SystemDrive/ProgramData laesst Windows Pfade nicht
        # aufloesen und legt einen Ordner namens "%SystemDrive%" im Projekt an.
        env={**os.environ,
             "WISPER_CONFIG": str(config_file),
             # Auch der Kindprozess schreibt sonst ins echte Log.
             "WISPER_LOG_DIR": str(config_file.parent / "logs")},
        capture_output=True, text=True, timeout=300,
    )


class TestEineKaputteKonfigurationHaeltNichtAuf:
    """Zwei echte Interpreterstarts, mehr braucht es nicht.

    Die Reihenfolge auf Modulebene ist *eine* Eigenschaft, keine neun. Welcher
    Wert die Warnung ausloest, ist dafuer gleichgueltig — dass ueberhaupt eine
    ausgeloest wird, genuegt. Die einzelnen Regeln prueft `validate_config`
    weiter unten im selben Prozess, ohne jedes Mal Python neu zu starten.
    """

    def test_the_app_still_comes_up(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(KAPUTTE_DATEIEN["sample_rate"], encoding="utf-8")

        result = _import_in_a_fresh_interpreter(config_file)

        assert "NameError" not in result.stderr, result.stderr
        assert result.returncode == 0, result.stderr
        assert "IMPORT-OK" in result.stdout

    def test_the_broken_value_is_reported(self, tmp_path):
        """Durchlassen heisst nicht verschweigen."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(KAPUTTE_DATEIEN["sample_rate"], encoding="utf-8")

        result = _import_in_a_fresh_interpreter(config_file)

        assert "Config validation" in result.stderr
        assert "12345" in result.stderr


class TestDieRegelnSindAbgedeckt:

    def test_every_rule_has_a_case(self):
        """Kommt eine Regel dazu, faellt hier auf, dass sie ungeprueft ist."""
        cfg = Config()
        cfg.sample_rate = 12345
        cfg.channels = 7
        cfg.max_recording_seconds = 99999
        cfg.model_size = "gibtsnicht"
        cfg.transcription_beam_size = 99
        cfg.ollama_timeout_cleanup = 0
        cfg.ollama_timeout_prompt = 0
        cfg.clipboard_paste_timeout = -1.0
        cfg.ollama_url = "ftp://127.0.0.1:11434/api/generate"
        assert len(validate_config(cfg)) == 9

    @pytest.mark.parametrize("regel", sorted(KAPUTTE_DATEIEN))
    def test_each_broken_file_trips_exactly_its_rule(self, regel):
        """Der Weg von der Datei bis zur Meldung, ohne Interpreterstart."""
        cfg = Config()
        _apply_toml(cfg, tomllib.loads(KAPUTTE_DATEIEN[regel]))

        assert len(validate_config(cfg)) == 1
