"""Pytest configuration and fixtures."""

import atexit
import contextlib
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
# noch beschreiben. `HAMS_CONFIG` muss stehen, *bevor* hams.config importiert
# wird — conftest laeuft vor den Testmodulen, deshalb hier auf Modulebene.
_config_dir = tempfile.mkdtemp(prefix="hams-tests-")
_config_file = Path(_config_dir) / "config.toml"
_template = project_root / "hams" / "config.default.toml"
if _template.exists():
    shutil.copy2(_template, _config_file)
os.environ["HAMS_CONFIG"] = str(_config_file)

# Aus demselben Grund das Log: sonst schreibt jeder Testlauf seine Eintraege
# in hams/logs/hams.log, zwischen die echten des Nutzers. Das ist die
# Aufzeichnung, aus der spaeter ein Fehler rekonstruiert wird - sie darf
# keine Zeitstempel aus pytest-Temporaerverzeichnissen enthalten.
_log_dir = tempfile.mkdtemp(prefix="hams-tests-logs-")
os.environ["HAMS_LOG_DIR"] = _log_dir

# Und die uebrigen Laufzeitdaten (Transkripte, heruntergeladene GPU-Bibliotheken,
# siehe hams/app_paths.py): nie der echte Ordner %LOCALAPPDATA%\Hams.
_data_dir = tempfile.mkdtemp(prefix="hams-tests-data-")
os.environ["HAMS_DATA_DIR"] = _data_dir

atexit.register(shutil.rmtree, _config_dir, True)
atexit.register(shutil.rmtree, _log_dir, True)
atexit.register(shutil.rmtree, _data_dir, True)

#: `WhisperModelMixin.model_needs_download` before `no_implicit_model_download` replaces it.
_REAL_NEEDS_DOWNLOAD = None


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
def ui_language():
    """Die Oberflaeche spricht in den Tests Deutsch, unabhaengig vom System.

    Viele Tests pruefen sichtbare Texte woertlich. Ohne feste Sprache hinge ihr
    Ergebnis an der Systemsprache des Rechners (der CI-Runner ist englisch).
    Englische Texte pruefen `test_i18n.py` und die Tests, die die Sprache
    ausdruecklich umstellen; danach gilt wieder Deutsch.
    """
    from hams import i18n

    i18n.set_language("de")
    yield "de"
    i18n.set_language("de")


@pytest.fixture(autouse=True)
def dictation_language(monkeypatch):
    """Diktiert wird in den Tests Deutsch, unabhaengig von der Systemsprache.

    Ohne Eintrag in der Konfiguration folgt die Diktiersprache der Oberflaeche und damit dem System (der
    CI-Runner ist englisch). Andere Sprachen pruefen `test_dictation_language.py` ausdruecklich.
    """
    from hams.config import CFG

    monkeypatch.setattr(CFG, "transcription_language", "de")
    return "de"


@pytest.fixture
def english(ui_language):
    """Schaltet die Oberflaeche fuer einen Test auf Englisch."""
    from hams import i18n

    i18n.set_language("en")
    return "en"


@pytest.fixture(autouse=True)
def no_implicit_model_download(monkeypatch):
    """No test starts a real model download: by default every model counts as cached.

    Loading an uncached model goes through the download (see `WhisperModelMixin._load_or_download`), and
    the real cache differs from machine to machine. Tests of that path use `model_cache_check`.
    """
    from hams.whisper_model import WhisperModelMixin

    global _REAL_NEEDS_DOWNLOAD
    _REAL_NEEDS_DOWNLOAD = WhisperModelMixin.__dict__["model_needs_download"]
    monkeypatch.setattr(WhisperModelMixin, "model_needs_download", staticmethod(lambda model_id: False))


@pytest.fixture
def model_cache_check(monkeypatch):
    """The real check against the cache; `installed` is the set of cached models the test pretends."""
    from hams import models as whisper_models
    from hams.whisper_model import WhisperModelMixin

    installed: set = set()
    monkeypatch.setattr(whisper_models, "installed_whisper_models", lambda: set(installed))
    monkeypatch.setattr(WhisperModelMixin, "model_needs_download", _REAL_NEEDS_DOWNLOAD)
    return installed


@pytest.fixture(autouse=True)
def no_animation():
    """Animationen aus, ausser ein Test schaltet sie ausdruecklich ein.

    Sonst haengt jede Zustandspruefung davon ab, wie schnell die Maschine
    gerade ist: ein Toggle stuende mitten in der Bewegung, und die Farbe waere
    ein Zwischenwert. Die Animationen selbst pruefen `test_animation.py`.
    """
    from hams import ui_kit as _uk

    before = _uk.Animator.enabled
    _uk.Animator.enabled = False
    yield
    _uk.Animator.enabled = before


@pytest.fixture
def patch_everywhere(monkeypatch):
    """Ersetzt eine Funktion aus hams.config in jedem Modul, das sie importiert hat.

    Fuer Pruefungen der Art "nirgends wird gespeichert": ein Patch an nur
    einer Stelle liesse jeden anderen Aufrufer unbemerkt durch.
    """

    def patch(name, value):
        from hams import config

        original = getattr(config, name)
        for module in list(sys.modules.values()):
            if getattr(module, "__name__", "").startswith("hams") and getattr(module, name, None) is original:
                monkeypatch.setattr(module, name, value)

    return patch


@pytest.fixture
def hams(monkeypatch, patch_everywhere, tmp_path):
    """Alle Attrappen fuer die echte Anwendung, siehe tests/live_app.py."""
    from types import SimpleNamespace

    from live_app import FakeClipboard, FakeHotkeys, FakeKeyboard, FakeStream, FakeTray, fake_sounddevice

    from hams import app as app_mod
    from hams import ollama as ollama_api
    from hams import recording as recording_mod
    from hams import settings_status as status_mod
    from hams import transcription as transcription_mod
    from hams.app import FloatingTranscriberApp as App
    from hams.config import CFG

    fakes = SimpleNamespace(
        hotkeys=None,
        keyboard=FakeKeyboard(),
        clipboard=FakeClipboard(),
        dots=[],
        beeps=[],
        saved=[],
        loads=0,
        streams=FakeStream.opened,
        devices=[("Headset", "Headset (USB Audio)")],
        probe=ollama_api.OllamaState(False, error="aus"),
    )

    class Hotkeys(FakeHotkeys):
        def __init__(self):
            super().__init__()
            fakes.hotkeys = self

    def dot(state, level=None):
        fakes.dots.append((state, level))

    def load(self):
        fakes.loads += 1

    FakeStream.opened.clear()
    FakeStream.fail_with = None
    FakeHotkeys.failing = set()
    FakeHotkeys.taken = set()
    snapshot = dict(vars(CFG))
    monkeypatch.setattr(app_mod, "SystemHotkeys", Hotkeys)
    monkeypatch.setattr(app_mod, "keyboard", fakes.keyboard)
    monkeypatch.setattr(transcription_mod, "keyboard", fakes.keyboard)
    monkeypatch.setattr(transcription_mod, "pyperclip", fakes.clipboard)
    monkeypatch.setattr(transcription_mod, "OUTPUT_DIR", tmp_path / "transkripte")
    monkeypatch.setattr(app_mod, "winsound", SimpleNamespace(Beep=lambda f, d: fakes.beeps.append((f, d))))
    monkeypatch.setattr(app_mod, "_notify_cursor_dot", dot)
    monkeypatch.setattr(recording_mod, "_notify_cursor_dot", dot)
    monkeypatch.setattr(recording_mod, "sd", fake_sounddevice())
    monkeypatch.setattr(recording_mod, "_resolve_mic_device", lambda raw: 7)
    monkeypatch.setattr(status_mod, "sd", fake_sounddevice())
    monkeypatch.setattr(status_mod, "_resolve_mic_device", lambda raw: 7)
    monkeypatch.setattr(status_mod, "_list_input_devices", lambda: list(fakes.devices))
    monkeypatch.setattr(ollama_api, "probe", lambda base, timeout=None: fakes.probe)
    patch_everywhere("_persist_config_value", lambda *args: fakes.saved.append(args) or True)
    monkeypatch.setattr(App, "_load_model_async", load)
    monkeypatch.setattr(App, "_warmup_ollama_async", lambda self: None)
    # Nie ins Netz: die Update-Abfrage pruefen test_updates.py und test_update_notice.py mit Attrappen.
    monkeypatch.setattr(App, "_start_update_check", lambda self: None)
    monkeypatch.setattr(App, "_setup_tray", lambda self: setattr(self, "tray_icon", FakeTray()))
    CFG.sound_feedback = True
    yield fakes
    for name, value in snapshot.items():
        setattr(CFG, name, value)


@pytest.fixture
def live_app(root, hams):
    """Die echte Anwendung in einem eigenen, versteckten Fenster."""
    from hams import ui_kit as _uk
    from hams.app import FloatingTranscriberApp as App

    theme = _uk.Theme.mode
    window = tk.Toplevel(root)
    app = App(window)
    _uk.Animator.enabled = False  # init_animations folgt dem System
    root.update()
    yield app
    app._shutting_down = True
    if app.settings_win is not None and app.settings_win.alive():
        app.settings_win.close()
    app._release_resources()
    app._cancel_position_job()
    with contextlib.suppress(tk.TclError):
        window.destroy()
    _uk.Theme.apply(theme)
    root.update()


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
    from hams import ui_kit as _uk

    _uk.init_scale(window)
    _uk.init_fonts(window)
    yield window
    with contextlib.suppress(tk.TclError):
        window.destroy()
