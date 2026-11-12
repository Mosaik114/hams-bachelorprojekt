"""Windows-Aktivierung ohne Mikrofon, Modell oder echte Tk-Fenster."""

import importlib.util
import os
import subprocess
import sys
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hams_activation_test", ROOT / "hams" / "activation.py")
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)


class FakeRoot:
    def __init__(self):
        self.jobs = {}
        self.sequence = 0

    def after(self, _delay, callback):
        self.sequence += 1
        self.jobs[self.sequence] = callback
        return self.sequence

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def tick(self):
        job = min(self.jobs)
        self.jobs.pop(job)()


@unittest.skipUnless(sys.platform == "win32", "Windows-Ereignisse")
class TestSettingsActivation(unittest.TestCase):
    def test_request_is_delivered_once_on_the_ui_thread(self):
        name = "Local\\Hams.ActivationTest." + uuid.uuid4().hex
        root, opened = FakeRoot(), Mock()
        receiver = activation.SettingsRequests(root, opened, name=name)
        try:
            root.tick()
            opened.assert_not_called()
            self.assertTrue(activation.request_settings(timeout=0, name=name))
            opened.assert_not_called()
            root.tick()
            opened.assert_called_once_with()
            root.tick()
            opened.assert_called_once_with()
        finally:
            receiver.close()
        self.assertFalse(root.jobs)
        self.assertFalse(activation.request_settings(timeout=0, name=name))

    def test_native_launcher_activates_without_starting_python(self):
        executable = os.environ.get("HAMS_TEST_LAUNCHER")
        if not executable:
            self.skipTest("Kein gebautes Startprogramm angegeben")
        # Die alte laufende Version hat noch keinen Empfaenger. Niemals einen
        # bereits vorhandenen Empfaenger der echten Anwendung konsumieren.
        api = activation._kernel32()
        existing = api.OpenEventW(activation.EVENT_MODIFY_STATE, False, activation.SETTINGS_EVENT)
        if existing:
            api.CloseHandle(existing)
            self.skipTest("Eine aktualisierte Hams-Instanz laeuft bereits")
        root, opened = FakeRoot(), Mock()
        receiver = activation.SettingsRequests(root, opened)
        try:
            result = subprocess.run([executable], timeout=5)
            self.assertEqual(result.returncode, 0)
            root.tick()
            opened.assert_called_once_with()
        finally:
            receiver.close()


class TestStartupActivation(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType("hams")
        package.__path__ = []
        self.app = Mock()
        self.factory = Mock(return_value=self.app)
        self.release = Mock()
        config = types.SimpleNamespace(
            CFG=types.SimpleNamespace(),
            CONFIG_PATH="unused",
            logger=Mock(),
            ensure_user_config=Mock(return_value="vorhanden"),
            _load_config=Mock(),
        )
        modules = {
            "hams": package,
            "hams.branding": types.SimpleNamespace(PRODUCT_NAME="Hams"),
            "hams.ui_kit": Mock(),
            "hams.activation": activation,
            "hams.app": types.SimpleNamespace(FloatingTranscriberApp=self.factory),
            "hams.config": config,
            "hams.instance": types.SimpleNamespace(
                _acquire_single_instance_mutex=Mock(return_value=1),
                _release_single_instance_mutex=self.release,
            ),
        }
        spec = importlib.util.spec_from_file_location("hams_main_test", ROOT / "hams" / "main.py")
        self.main = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules), patch.object(sys, "path", list(sys.path)):
            spec.loader.exec_module(self.main)
        self.root = Mock()
        self.main.tk = types.SimpleNamespace(Tk=Mock(return_value=self.root))
        self.main.sys = types.SimpleNamespace(platform="win32", exit=Mock(side_effect=SystemExit))
        self.main.ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(user32=Mock()))
        self.main.SettingsRequests = Mock()
        self.main.request_settings = Mock(return_value=True)

    def test_existing_instance_receives_settings_request_without_another_window(self):
        self.main._acquire_single_instance_mutex.return_value = None
        self.main.main(show_settings=True)
        self.main.request_settings.assert_called_once_with()
        self.main.tk.Tk.assert_not_called()
        self.main.ctypes.windll.user32.MessageBoxW.assert_not_called()

    def test_cold_start_opens_settings_and_closes_receiver(self):
        self.main.main(show_settings=True)
        self.root.after_idle.assert_called_once_with(self.app._open_settings)
        self.main.SettingsRequests.assert_called_once_with(self.root, self.app._open_settings)
        self.main.SettingsRequests.return_value.close.assert_called_once_with()
        self.release.assert_called_once_with()

    def test_normal_autostart_stays_hidden(self):
        self.main.main()
        self.root.after_idle.assert_not_called()

    def test_cleanup_also_runs_after_mainloop_failure(self):
        self.root.mainloop.side_effect = RuntimeError("test")
        with self.assertRaises(RuntimeError):
            self.main.main(show_settings=True)
        self.main.SettingsRequests.return_value.close.assert_called_once_with()
        self.release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
