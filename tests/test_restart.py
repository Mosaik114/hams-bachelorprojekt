"""Neustart der Anwendung: Befehl, Einzelinstanz-Schloss, Abbruchgruende.

Warum es diesen Knopf gibt, steht als Messung in `test_hotkey_recovery.py`:
Ein von Windows entfernter Tastaturhook laesst sich im laufenden Prozess nicht
neu setzen. Hier geht es nur darum, dass der Neustart selbst sauber ablaeuft.
"""

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

import pytest
from test_settings_window_live import disk, open_settings  # noqa: F401 - Fixtures

from hams import app as app_mod
from hams import config as config_mod
from hams import instance as instance_mod
from hams.app import FloatingTranscriberApp
from hams.settings_shortcuts import ShortcutsSection

App = FloatingTranscriberApp


# ------------------------------------------------------------------- Befehl


class TestRestartCommand:
    def test_it_starts_the_same_interpreter(self):
        assert instance_mod.restart_command()[0] == sys.executable

    def test_it_names_the_script_absolutely(self):
        """`sys.argv[0]` waere je nach Startweg relativ — und nach einem
        Verzeichniswechsel wertlos."""
        skript = Path(instance_mod.restart_command()[1])
        assert skript.is_absolute()
        assert skript.name == "main.py"
        assert skript.exists()

    def test_it_is_exactly_two_parts(self):
        assert len(instance_mod.restart_command()) == 2


# -------------------------------------------------------------------- Schloss


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="benanntes Mutex gibt es nur unter Windows")


class _FakeMutexApi:
    """Benannte Mutexe ohne Betriebssystem: kein Test fasst die echten Namen an."""

    def __init__(self, held=(), errors=None):
        self.held = set(held)
        #: Name -> Fehlercode, mit dem CreateMutexW scheitert (Handle 0).
        self.errors = dict(errors or {})
        self.opened = {}
        self.closed = []
        self._next = 100

    def create(self, name):
        if name in self.errors:
            return 0, self.errors[name]
        self._next += 1
        error = instance_mod.ERROR_ALREADY_EXISTS if name in self.held else 0
        self.held.add(name)
        self.opened[self._next] = name
        return self._next, error

    def close(self, handle):
        if handle == "kaputt":
            raise OSError("geht nicht")
        self.closed.append(handle)
        return True


@pytest.fixture
def kernel32(monkeypatch):
    fake = _FakeMutexApi()
    monkeypatch.setattr(instance_mod, "_kernel32", lambda: fake)
    monkeypatch.setattr(instance_mod, "_MUTEX_HANDLES", None)
    return fake


class TestMutex:
    def test_releasing_nothing_succeeds(self, monkeypatch):
        monkeypatch.setattr(instance_mod, "_MUTEX_HANDLES", None)
        assert instance_mod._release_single_instance_mutex() is True

    @windows_only
    def test_releasing_closes_the_handle_and_forgets_it(self, kernel32, monkeypatch):
        monkeypatch.setattr(instance_mod, "_MUTEX_HANDLES", (4711,))
        assert instance_mod._release_single_instance_mutex() is True
        assert kernel32.closed == [4711]
        assert instance_mod._MUTEX_HANDLES is None

    @windows_only
    def test_releasing_twice_is_harmless(self, kernel32, monkeypatch):
        monkeypatch.setattr(instance_mod, "_MUTEX_HANDLES", (4711,))
        assert instance_mod._release_single_instance_mutex() is True
        assert instance_mod._release_single_instance_mutex() is True

    @windows_only
    def test_a_failure_is_reported_not_swallowed(self, kernel32, monkeypatch):
        """Sonst startete der Nachfolger und faende das Schloss noch belegt."""
        monkeypatch.setattr(instance_mod, "_MUTEX_HANDLES", ("kaputt",))
        assert instance_mod._release_single_instance_mutex() is False

    @windows_only
    def test_releasing_closes_every_handle(self, kernel32, monkeypatch):
        monkeypatch.setattr(instance_mod, "_MUTEX_HANDLES", (11, 12))
        assert instance_mod._release_single_instance_mutex() is True
        assert kernel32.closed == [11, 12]


class TestMutexErrors:
    """CreateMutexW liefert NULL: das ist kein gehaltenes Schloss."""

    def test_access_denied_means_another_session_holds_it(self, kernel32):
        kernel32.errors[instance_mod.MUTEX_NAME] = instance_mod.ERROR_ACCESS_DENIED
        assert instance_mod._acquire_windows() is None
        assert kernel32.closed == []

    def test_access_denied_on_the_second_name_gives_the_first_back(self, kernel32):
        kernel32.errors[instance_mod.LEGACY_MUTEX_NAME] = instance_mod.ERROR_ACCESS_DENIED
        assert instance_mod._acquire_windows() is None
        assert sorted(kernel32.closed) == sorted(kernel32.opened)

    def test_any_other_error_starts_without_that_lock(self, kernel32, caplog):
        kernel32.errors[instance_mod.LEGACY_MUTEX_NAME] = 1450  # ERROR_NO_SYSTEM_RESOURCES
        handles = instance_mod._acquire_windows()
        assert handles == tuple(kernel32.opened)
        assert 0 not in handles, "a NULL handle is never kept"
        assert "unavailable" in caplog.text

    def test_no_lock_at_all_still_starts(self, kernel32, caplog):
        for name in instance_mod.MUTEX_NAMES:
            kernel32.errors[name] = 1450
        assert instance_mod._acquire_windows() == ()
        assert "without a single-instance lock" in caplog.text

    @windows_only
    def test_starting_without_any_lock_is_still_a_start(self, kernel32):
        for name in instance_mod.MUTEX_NAMES:
            kernel32.errors[name] = 1450
        assert instance_mod._acquire_single_instance_mutex()  # nicht None: keine zweite Instanz erkannt
        assert instance_mod._release_single_instance_mutex() is True
        assert kernel32.closed == []

    @windows_only
    def test_the_real_api_has_its_own_prototypes(self):
        """Eigenes WinDLL: die geteilten ctypes.windll-Zeiger bleiben unberuehrt."""
        shared = ctypes.windll.kernel32.CreateMutexW
        before = getattr(shared, "argtypes", None)
        api = instance_mod._kernel32()
        assert api.kernel32.CreateMutexW is not shared
        assert getattr(ctypes.windll.kernel32.CreateMutexW, "argtypes", None) == before

    @windows_only
    def test_the_real_api_reports_an_existing_mutex(self):
        """Mit einem eigenen Namen in der eigenen Sitzung - nie den echten Schloessern."""
        import uuid

        api = instance_mod._kernel32()
        name = rf"Local\Hams-Test-{uuid.uuid4().hex}"
        first = instance_mod._create_named_mutex(api, name)
        try:
            assert first
            assert instance_mod._create_named_mutex(api, name) is None
        finally:
            api.close(first)


class TestLegacyInstanceName:
    """Hams und eine noch installierte Fassung aus der Wisper-Zeit laufen nie gleichzeitig."""

    def test_the_new_name_is_hams(self):
        assert "Hams" in instance_mod.MUTEX_NAME
        assert "Wisper" in instance_mod.LEGACY_MUTEX_NAME

    def test_both_names_are_held(self, kernel32):
        assert instance_mod._acquire_windows() is not None
        assert set(kernel32.opened.values()) == {instance_mod.MUTEX_NAME, instance_mod.LEGACY_MUTEX_NAME}
        assert kernel32.closed == []

    def test_a_running_old_instance_blocks_the_start(self, kernel32):
        kernel32.held.add(instance_mod.LEGACY_MUTEX_NAME)
        assert instance_mod._acquire_windows() is None

    def test_a_running_hams_blocks_the_start(self, kernel32):
        kernel32.held.add(instance_mod.MUTEX_NAME)
        assert instance_mod._acquire_windows() is None

    def test_a_failed_start_gives_everything_back(self, kernel32):
        """Sonst hielte der scheiternde Prozess bis zu seinem Ende den neuen Namen fest."""
        kernel32.held.add(instance_mod.LEGACY_MUTEX_NAME)
        instance_mod._acquire_windows()
        assert sorted(kernel32.closed) == sorted(kernel32.opened)

    @windows_only
    def test_acquiring_keeps_every_handle(self, kernel32):
        assert instance_mod._acquire_single_instance_mutex() == 101
        assert instance_mod._MUTEX_HANDLES == (101, 102)

    @windows_only
    def test_a_refused_start_keeps_nothing(self, kernel32):
        kernel32.held.add(instance_mod.MUTEX_NAME)
        assert instance_mod._acquire_single_instance_mutex() is None
        assert instance_mod._MUTEX_HANDLES is None


# ------------------------------------------------------------------ Neustart


class _Tray:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class _Root:
    def __init__(self):
        self.jobs = []

    def after(self, _ms, fn=None):
        self.jobs.append(fn)
        return "job"

    def destroy(self):
        self.jobs.append("destroy")


@pytest.fixture
def app(live_app, monkeypatch):
    """The live app; only the restart's own steps are real, the rest is recorded."""
    monkeypatch.setattr(live_app, "root", _Root())
    live_app.tray_icon = _Tray()
    live_app.released = 0
    live_app._window_visible = True
    #: Shortcuts and the successor's start, in their order.
    live_app.sequence = []
    live_app.remembered = False
    monkeypatch.setattr(live_app, "_flush_position", lambda: setattr(live_app, "remembered", True))
    monkeypatch.setattr(live_app, "_release_resources", lambda: setattr(live_app, "released", live_app.released + 1))
    monkeypatch.setattr(live_app, "suspend_hotkeys", lambda: live_app.sequence.append("hotkeys frei"))
    monkeypatch.setattr(live_app, "_register_hotkeys", lambda: live_app.sequence.append("hotkeys an"))
    monkeypatch.setattr(instance_mod, "_MUTEX_HANDLES", None)
    return live_app


@pytest.fixture
def popen(monkeypatch):
    calls = []

    def _fake(command, **kwargs):
        calls.append((command, kwargs))
        return object()

    monkeypatch.setattr(subprocess, "Popen", _fake)
    _fake.calls = calls
    return _fake


@pytest.mark.skipif(sys.platform == "win32", reason="systemd gibt es nur unter Linux")
class TestRestartUnderTheService:
    """Als Dienst startet systemd neu; ein eigener Nachfolger wuerde beim
    Dienststopp mit beendet."""

    def test_no_successor_is_started(self, app, popen, monkeypatch):
        from hams import linux

        monkeypatch.setattr(linux, "under_systemd", lambda: True)
        monkeypatch.setattr(linux, "restart_requested", False)
        assert app.restart_app() == ""
        assert popen.calls == []
        assert linux.restart_requested is True

    def test_the_service_restarts_on_the_agreed_code(self):
        from hams import linux

        unit = linux.service_unit("/py", "/main.py")
        assert "Restart=on-failure" in unit
        assert f"RestartForceExitStatus={linux.RESTART_EXIT_CODE}" in unit


class TestRestartApp:
    def test_it_starts_a_successor_and_steps_aside(self, app, popen):
        assert app.restart_app() == ""
        assert len(popen.calls) == 1
        command, kwargs = popen.calls[0]
        assert command == instance_mod.restart_command()
        assert app.tray_icon.stopped == 1
        assert app._shutting_down is True
        assert app.released == 1
        assert app.root.jobs and app.root.jobs[-1] == app.root.destroy

    def test_the_window_position_is_saved_first(self, app, popen):
        app.restart_app()
        assert app.remembered is True

    def test_the_successor_is_detached(self, app, popen):
        """Sonst haengt er am sterbenden Elternprozess."""
        app.restart_app()
        _command, kwargs = popen.calls[0]
        if sys.platform != "win32":
            assert kwargs["start_new_session"] is True
            return
        flags = kwargs["creationflags"]
        assert flags & subprocess.DETACHED_PROCESS
        assert flags & subprocess.CREATE_NEW_PROCESS_GROUP

    def test_it_runs_from_the_project_root(self, app, popen):
        app.restart_app()
        _command, kwargs = popen.calls[0]
        assert Path(kwargs["cwd"]) == Path(config_mod.APP_DIR).parent

    def test_the_lock_is_released_before_the_successor_starts(self, app, monkeypatch):
        """Sonst meldet der Nachfolger „läuft bereits" und beendet sich sofort."""
        order = []
        monkeypatch.setattr(app_mod, "_release_single_instance_mutex", lambda: order.append("frei") or True)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: order.append("start"))
        app.restart_app()
        assert order == ["frei", "start"]

    def test_a_recording_blocks_the_restart(self, app, popen):
        app.recorder.is_recording = True
        assert app.restart_app() == "Läuft gerade eine Aufnahme"
        assert popen.calls == []
        assert app._shutting_down is False

    def test_a_download_blocks_the_restart(self, app, popen, monkeypatch):
        monkeypatch.setattr(type(app.models), "downloading", property(lambda self: "turbo"))
        assert app.restart_app() == "Ein Download läuft gerade"
        assert popen.calls == []

    def test_an_unreleasable_lock_blocks_the_restart(self, app, popen, monkeypatch):
        monkeypatch.setattr(app_mod, "_release_single_instance_mutex", lambda: False)
        assert app.restart_app() == "Neustart nicht möglich"
        assert popen.calls == []
        assert app._shutting_down is False

    def test_a_failed_start_leaves_everything_running(self, app, monkeypatch):
        """Lieber eine Meldung als ein Rechner ohne laufendes Hams."""
        again = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
        monkeypatch.setattr(app_mod, "_acquire_single_instance_mutex", lambda: again.append(1))
        assert app.restart_app() == "Neustart fehlgeschlagen"
        assert app._shutting_down is False
        assert app.tray_icon.stopped == 0
        assert again == [1], "das Schloss muss zurueckgenommen werden"
        assert app.sequence == ["hotkeys frei", "hotkeys an"], "die Kurzbefehle gelten wieder"

    def test_the_hotkeys_are_freed_before_the_successor_starts(self, app, monkeypatch):
        """Third review: the successor registers the same shortcuts and failed while they were still held."""
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: app.sequence.append("start"))
        app.restart_app()
        assert app.sequence[:2] == ["hotkeys frei", "start"]

    def test_a_dictation_still_in_the_pipeline_blocks_the_restart(self, app, popen):
        """Third review: restarting dropped a finished recording that was still being transcribed."""
        app.pipeline.job_running = True
        assert app.restart_app() == "Ein Diktat wird noch verarbeitet"
        assert popen.calls == []
        assert app._shutting_down is False

    def test_a_hidden_app_restarts_in_the_background(self, app, popen):
        app._window_visible = False
        app.restart_app()
        assert popen.calls[0][0] == [*instance_mod.restart_command(), "--background"]


# ------------------------------------------------------------ Einstellungszeile


@pytest.fixture
def restart_row(open_settings, live_app, monkeypatch):  # noqa: F811
    """The shortcuts group of the real settings window; the app's restart only answers with a refusal."""
    sections = open_settings().shortcuts
    sections.refusal, sections.calls = "", []

    def restart_app():
        sections.calls.append(1)
        return sections.refusal

    monkeypatch.setattr(live_app, "restart_app", restart_app)
    return sections


class TestRestartRow:
    def test_a_refusal_names_the_reason_and_gives_the_button_back(self, restart_row):
        restart_row.refusal = "Läuft gerade eine Aufnahme"
        restart_row.restart_app()
        assert restart_row.row_restart.sub_text == "Läuft gerade eine Aufnahme"
        assert restart_row.restart_button.enabled is True

    def test_a_successful_restart_leaves_the_button_disabled(self, restart_row):
        """Danach verschwindet das Fenster ohnehin — ein zweiter Klick waere
        nur eine Einladung, zwei Nachfolger zu starten."""
        restart_row.restart_app()
        assert restart_row.restart_button.enabled is False
        assert restart_row.row_restart.sub_text == "Wird neu gestartet …"
        assert restart_row.calls == [1]

    def test_the_hint_names_the_situation_it_is_for(self, restart_row):
        assert "Kurzbefehle" in ShortcutsSection.RESTART_HINT
        assert restart_row.row_restart.sub_text == ShortcutsSection.RESTART_HINT


# ------------------------------------------------- Die Annahme dahinter


@windows_only
class TestTheHandoverActuallyWorks:
    """Der Neustart ruht darauf, dass der Nachfolger das Schloss sofort bekommt.

    Geprueft mit einem echten zweiten Prozess und einem eigenen Namen — die
    laufende Hams-Instanz bleibt unberuehrt. Traegt die Annahme nicht, stuende
    der Nutzer nach einem Klick ohne laufendes Hams da.
    """

    NAME = f"Global\\Hams-Test-Uebergabe-{os.getpid()}"
    FRAGT = (
        "import ctypes;from ctypes import wintypes;k=ctypes.windll.kernel32;"
        "k.CreateMutexW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR];"
        "k.CreateMutexW.restype=wintypes.HANDLE;"
        "k.CreateMutexW(None,False,r'{name}');"
        "print('belegt' if k.GetLastError()==183 else 'frei')"
    )

    def _zweiter_prozess(self):
        done = subprocess.run(
            [sys.executable, "-c", self.FRAGT.format(name=self.NAME)], capture_output=True, text=True, timeout=60
        )
        return done.stdout.strip()

    def test_the_successor_gets_the_lock_once_the_handle_is_closed(self):
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        griff = kernel32.CreateMutexW(None, False, self.NAME)
        assert griff, "Testschloss liess sich nicht anlegen"
        try:
            assert self._zweiter_prozess() == "belegt"
        finally:
            kernel32.CloseHandle(griff)
        assert self._zweiter_prozess() == "frei", (
            "Ein benanntes Mutex muss verschwinden, sobald der letzte Griff zu "
            "ist — sonst faende der Nachfolger es belegt und beendete sich."
        )
