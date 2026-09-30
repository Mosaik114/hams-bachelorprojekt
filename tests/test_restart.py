"""Neustart der Anwendung: Befehl, Einzelinstanz-Schloss, Abbruchgruende.

Warum es diesen Knopf gibt, steht als Messung in `test_hotkey_recovery.py`:
Ein von Windows entfernter Tastaturhook laesst sich im laufenden Prozess nicht
neu setzen. Hier geht es nur darum, dass der Neustart selbst sauber ablaeuft.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from wisper import main as wm

App = wm.FloatingTranscriberApp


# ------------------------------------------------------------------- Befehl


class TestRestartCommand:
    def test_it_starts_the_same_interpreter(self):
        assert wm.restart_command()[0] == sys.executable

    def test_it_names_the_script_absolutely(self):
        """`sys.argv[0]` waere je nach Startweg relativ — und nach einem
        Verzeichniswechsel wertlos."""
        skript = Path(wm.restart_command()[1])
        assert skript.is_absolute()
        assert skript.name == "main.py"
        assert skript.exists()

    def test_it_is_exactly_two_parts(self):
        assert len(wm.restart_command()) == 2


# -------------------------------------------------------------------- Schloss


class TestMutex:
    def test_releasing_nothing_succeeds(self, monkeypatch):
        monkeypatch.setattr(wm, "_MUTEX_HANDLE", None)
        assert wm._release_single_instance_mutex() is True

    def test_releasing_closes_the_handle_and_forgets_it(self, monkeypatch):
        geschlossen = []
        monkeypatch.setattr(wm, "_MUTEX_HANDLE", 4711)
        monkeypatch.setattr(wm.ctypes.windll.kernel32, "CloseHandle",
                            lambda h: geschlossen.append(h) or 1)
        assert wm._release_single_instance_mutex() is True
        assert geschlossen == [4711]
        assert wm._MUTEX_HANDLE is None

    def test_releasing_twice_is_harmless(self, monkeypatch):
        monkeypatch.setattr(wm, "_MUTEX_HANDLE", 4711)
        monkeypatch.setattr(wm.ctypes.windll.kernel32, "CloseHandle", lambda h: 1)
        assert wm._release_single_instance_mutex() is True
        assert wm._release_single_instance_mutex() is True

    def test_a_failure_is_reported_not_swallowed(self, monkeypatch):
        """Sonst startete der Nachfolger und faende das Schloss noch belegt."""
        def _kaputt(_handle):
            raise OSError("geht nicht")

        monkeypatch.setattr(wm, "_MUTEX_HANDLE", 4711)
        monkeypatch.setattr(wm.ctypes.windll.kernel32, "CloseHandle", _kaputt)
        assert wm._release_single_instance_mutex() is False


# ------------------------------------------------------------------ Neustart


class _Tray:
    def __init__(self):
        self.gestoppt = 0

    def stop(self):
        self.gestoppt += 1


class _Root:
    def __init__(self):
        self.jobs = []

    def after(self, _ms, fn=None):
        self.jobs.append(fn)
        return "job"

    def destroy(self):
        self.jobs.append("destroy")


@pytest.fixture
def app(monkeypatch):
    """Die Anwendung ohne Tk, Audio und Tray — nur die Felder des Neustarts."""
    instanz = App.__new__(App)
    instanz.is_recording = False
    instanz._shutting_down = False
    instanz.root = _Root()
    instanz.tray_icon = _Tray()
    instanz.freigegeben = 0

    def _flush():
        instanz.gemerkt = True

    def _release():
        instanz.freigegeben += 1

    instanz.gemerkt = False
    monkeypatch.setattr(App, "_flush_position", lambda self: _flush())
    monkeypatch.setattr(App, "_release_resources", lambda self: _release())
    monkeypatch.setattr(App, "downloading_model", property(lambda self: None))
    monkeypatch.setattr(wm, "_MUTEX_HANDLE", None)
    return instanz


@pytest.fixture
def popen(monkeypatch):
    aufrufe = []

    def _fake(befehl, **kwargs):
        aufrufe.append((befehl, kwargs))
        return object()

    monkeypatch.setattr(wm.subprocess, "Popen", _fake)
    _fake.aufrufe = aufrufe
    return _fake


class TestRestartApp:
    def test_it_starts_a_successor_and_steps_aside(self, app, popen):
        assert app.restart_app() == ""
        assert len(popen.aufrufe) == 1
        befehl, kwargs = popen.aufrufe[0]
        assert befehl == wm.restart_command()
        assert app.tray_icon.gestoppt == 1
        assert app._shutting_down is True
        assert app.freigegeben == 1
        assert app.root.jobs and app.root.jobs[-1] == app.root.destroy

    def test_the_window_position_is_saved_first(self, app, popen):
        app.restart_app()
        assert app.gemerkt is True

    def test_the_successor_is_detached(self, app, popen):
        """Sonst haengt er am sterbenden Elternprozess."""
        app.restart_app()
        _befehl, kwargs = popen.aufrufe[0]
        flags = kwargs["creationflags"]
        assert flags & subprocess.DETACHED_PROCESS
        assert flags & subprocess.CREATE_NEW_PROCESS_GROUP

    def test_it_runs_from_the_project_root(self, app, popen):
        app.restart_app()
        _befehl, kwargs = popen.aufrufe[0]
        assert Path(kwargs["cwd"]) == Path(wm.APP_DIR).parent

    def test_the_lock_is_released_before_the_successor_starts(self, app,
                                                              monkeypatch):
        """Sonst meldet der Nachfolger „läuft bereits" und beendet sich sofort."""
        reihenfolge = []
        monkeypatch.setattr(wm, "_release_single_instance_mutex",
                            lambda: reihenfolge.append("frei") or True)
        monkeypatch.setattr(wm.subprocess, "Popen",
                            lambda *a, **k: reihenfolge.append("start"))
        app.restart_app()
        assert reihenfolge == ["frei", "start"]

    def test_a_recording_blocks_the_restart(self, app, popen):
        app.is_recording = True
        assert app.restart_app() == "Läuft gerade eine Aufnahme"
        assert popen.aufrufe == []
        assert app._shutting_down is False

    def test_a_download_blocks_the_restart(self, app, popen, monkeypatch):
        monkeypatch.setattr(App, "downloading_model", property(lambda self: "turbo"))
        assert app.restart_app() == "Ein Download läuft gerade"
        assert popen.aufrufe == []

    def test_an_unreleasable_lock_blocks_the_restart(self, app, popen, monkeypatch):
        monkeypatch.setattr(wm, "_release_single_instance_mutex", lambda: False)
        assert app.restart_app() == "Neustart nicht möglich"
        assert popen.aufrufe == []
        assert app._shutting_down is False

    def test_a_failed_start_leaves_everything_running(self, app, monkeypatch):
        """Lieber eine Meldung als ein Rechner ohne laufendes Hams."""
        wieder = []
        monkeypatch.setattr(wm.subprocess, "Popen",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
        monkeypatch.setattr(wm, "_acquire_single_instance_mutex",
                            lambda: wieder.append(1))
        assert app.restart_app() == "Neustart fehlgeschlagen"
        assert app._shutting_down is False
        assert app.tray_icon.gestoppt == 0
        assert wieder == [1], "das Schloss muss zurueckgenommen werden"


# ------------------------------------------------------------ Einstellungszeile


class _Row:
    def __init__(self):
        self.text = ""

    def set_description(self, text):
        self.text = text


class _Button:
    def __init__(self):
        self.enabled = True

    def set_enabled(self, value):
        self.enabled = value


class _AppStub:
    def __init__(self, grund=""):
        self.grund = grund
        self.aufrufe = 0

    def restart_app(self):
        self.aufrufe += 1
        return self.grund


def _fenster(grund=""):
    fenster = wm.SettingsWindow.__new__(wm.SettingsWindow)
    fenster.app = _AppStub(grund)
    fenster.row_restart = _Row()
    fenster.restart_button = _Button()
    return fenster


class TestRestartRow:
    def test_a_refusal_names_the_reason_and_gives_the_button_back(self):
        fenster = _fenster("Läuft gerade eine Aufnahme")
        wm.SettingsWindow._restart_app(fenster)
        assert fenster.row_restart.text == "Läuft gerade eine Aufnahme"
        assert fenster.restart_button.enabled is True

    def test_a_successful_restart_leaves_the_button_disabled(self):
        """Danach verschwindet das Fenster ohnehin — ein zweiter Klick waere
        nur eine Einladung, zwei Nachfolger zu starten."""
        fenster = _fenster("")
        wm.SettingsWindow._restart_app(fenster)
        assert fenster.restart_button.enabled is False
        assert fenster.row_restart.text == "Wird neu gestartet …"
        assert fenster.app.aufrufe == 1

    def test_the_hint_names_the_situation_it_is_for(self):
        assert "Kurzbefehle" in wm.SettingsWindow.RESTART_HINT


# ------------------------------------------------- Die Annahme dahinter


class TestTheHandoverActuallyWorks:
    """Der Neustart ruht darauf, dass der Nachfolger das Schloss sofort bekommt.

    Geprueft mit einem echten zweiten Prozess und einem eigenen Namen — die
    laufende Hams-Instanz bleibt unberuehrt. Traegt die Annahme nicht, stuende
    der Nutzer nach einem Klick ohne laufendes Hams da.
    """

    NAME = "Global\\Hams-Test-Uebergabe-%d" % os.getpid()
    FRAGT = (
        "import ctypes;from ctypes import wintypes;k=ctypes.windll.kernel32;"
        "k.CreateMutexW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR];"
        "k.CreateMutexW.restype=wintypes.HANDLE;"
        "k.CreateMutexW(None,False,r'{name}');"
        "print('belegt' if k.GetLastError()==183 else 'frei')"
    )

    def _zweiter_prozess(self):
        fertig = subprocess.run(
            [sys.executable, "-c", self.FRAGT.format(name=self.NAME)],
            capture_output=True, text=True, timeout=60)
        return fertig.stdout.strip()

    def test_the_successor_gets_the_lock_once_the_handle_is_closed(self):
        kernel32 = wm.ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wm.wintypes.LPVOID, wm.wintypes.BOOL,
                                          wm.wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wm.wintypes.HANDLE
        griff = kernel32.CreateMutexW(None, False, self.NAME)
        assert griff, "Testschloss liess sich nicht anlegen"
        try:
            assert self._zweiter_prozess() == "belegt"
        finally:
            kernel32.CloseHandle(griff)
        assert self._zweiter_prozess() == "frei", (
            "Ein benanntes Mutex muss verschwinden, sobald der letzte Griff zu "
            "ist — sonst faende der Nachfolger es belegt und beendete sich.")
