"""Pytest configuration and fixtures."""

import atexit
import os
import shutil
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Die Testsuite darf die persoenliche Konfiguration des Nutzers weder lesen
# noch beschreiben. `WISPER_CONFIG` muss stehen, *bevor* wisper.main importiert
# wird — conftest laeuft vor den Testmodulen, deshalb hier auf Modulebene.
_config_dir = tempfile.mkdtemp(prefix="wisper-tests-")
_config_file = Path(_config_dir) / "config.toml"
_template = project_root / "wisper" / "config.default.toml"
if _template.exists():
    shutil.copy2(_template, _config_file)
os.environ["WISPER_CONFIG"] = str(_config_file)

# Aus demselben Grund das Log: sonst schreibt jeder Testlauf seine Eintraege
# in wisper/logs/wisper.log, zwischen die echten des Nutzers. Das ist die
# Aufzeichnung, aus der spaeter ein Fehler rekonstruiert wird - sie darf
# keine Zeitstempel aus pytest-Temporaerverzeichnissen enthalten.
_log_dir = tempfile.mkdtemp(prefix="wisper-tests-logs-")
os.environ["WISPER_LOG_DIR"] = _log_dir

atexit.register(shutil.rmtree, _config_dir, True)
atexit.register(shutil.rmtree, _log_dir, True)


def fokus_erzwingen(fenster, root, frist: float = 0.5) -> bool:
    """Holt ein Fenster in den Vordergrund und wartet, bis der Fokus da ist.

    `focus_force()` allein genuegt nicht. Es *bittet* den Fenstermanager um
    den Vordergrund; ob und wann der ihn vergibt, entscheidet der. Und
    `root.update()` arbeitet nur die bereits vorliegenden Ereignisse ab — es
    wartet auf nichts.

    Ohne echten Fokus kommen Tastenereignisse nicht an und ein Popover
    schliesst sich sofort wieder. Gemessen scheiterte die Testsuite dadurch in
    1 von 5 vollstaendigen Laeufen, jedes Mal an einer anderen Stelle: einmal
    die Tastaturaktivierung eines Knopfes, einmal die Bedienung des
    Darstellungs-Selectors, einmal ein Popover, das gar nicht erst aufging.
    Das lag nie am geprueften Verhalten, immer an dieser Mechanik.

    Returns:
        True, wenn der Fokus innerhalb der Frist wirklich angekommen ist.
    """
    fenster.focus_force()
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        root.update()
        if root.focus_displayof() is not None:
            return True
        time.sleep(0.01)
    root.update()
    return False


@pytest.fixture(autouse=True)
def no_animation():
    """Animationen aus, ausser ein Test schaltet sie ausdruecklich ein.

    Sonst haengt jede Zustandspruefung davon ab, wie schnell die Maschine
    gerade ist: ein Toggle stuende mitten in der Bewegung, und die Farbe waere
    ein Zwischenwert. Die Animationen selbst pruefen `test_animation.py`.
    """
    from wisper import ui_kit as _uk

    before = _uk.Animator.enabled
    _uk.Animator.enabled = False
    yield
    _uk.Animator.enabled = before


@pytest.fixture(scope="session")
def root():
    """Eine einzige Tk-Wurzel fuer die gesamte Testsitzung.

    Mehrere Wurzeln nacheinander im selben Prozess sind unter Tk unzuverlaessig
    und fuehren dazu, dass Tests je nach Reihenfolge uebersprungen werden.
    Deshalb genau eine, geteilt ueber alle Testdateien.
    """
    try:
        window = tk.Tk()
    except tk.TclError:  # pragma: no cover - nur auf headless-Systemen
        pytest.skip("Keine Anzeige verfuegbar")
    window.withdraw()
    # Tk laedt seine Tcl-Prozeduren erst bei Bedarf nach. tk_focusNext gehoert
    # dazu und fehlt sonst, je nachdem welche Tests vorher liefen.
    window.tk.eval("catch {tk_focusNext .}")
    # Schriftrollen bereitstellen, damit Controls unter Testbedingungen
    # dieselben Fonts benutzen wie im Programm.
    from wisper import ui_kit as _uk
    _uk.init_scale(window)
    _uk.init_fonts(window)
    yield window
    try:
        window.destroy()
    except tk.TclError:  # pragma: no cover
        pass
