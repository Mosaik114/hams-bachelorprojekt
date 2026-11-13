"""Windows-Aktivierung ohne Mikrofon, Modell oder echte Tk-Fenster."""

import importlib.util
import os
import subprocess
import sys
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, call, patch

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

    def test_show_request_uses_its_own_event(self):
        root, shown = FakeRoot(), Mock()
        with patch.object(activation, "SHOW_EVENT", "Local\\Hams.ShowTest." + uuid.uuid4().hex):
            receiver = activation.SettingsRequests(root, shown, name=activation.SHOW_EVENT)
            try:
                self.assertTrue(activation.request_show(timeout=0))
                root.tick()
                shown.assert_called_once_with()
            finally:
                receiver.close()
            self.assertFalse(activation.request_show(timeout=0))

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
        self.setup_wizard = types.SimpleNamespace(needs_setup=Mock(return_value=False), marker_path=Mock())
        config = types.SimpleNamespace(
            CFG=types.SimpleNamespace(ui_language="de"),
            CONFIG_PATH="unused",
            logger=Mock(),
            ensure_user_config=Mock(return_value="existing"),
            CONFIG_CREATED="created",
            CONFIG_MIGRATED="migrated",
            BROKEN_CONFIG=None,
            _load_config=Mock(),
        )
        modules = {
            "hams": package,
            "hams.branding": types.SimpleNamespace(PRODUCT_NAME="Hams"),
            "hams.ui_kit": Mock(),
            "hams.activation": activation,
            # Ohne Ersatz schriebe der echte Assistent seine Markierung neben "unused", also ins Arbeitsverzeichnis.
            "hams.setup_wizard": self.setup_wizard,
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
        self.main.request_show = Mock(return_value=True)

    def test_existing_instance_receives_settings_request_without_another_window(self):
        self.main._acquire_single_instance_mutex.return_value = None
        self.main.main(show_settings=True)
        self.main.request_settings.assert_called_once_with()
        self.main.tk.Tk.assert_not_called()
        self.main.ctypes.windll.user32.MessageBoxW.assert_not_called()

    def test_second_start_brings_the_running_window_to_the_front(self):
        self.main._acquire_single_instance_mutex.return_value = None
        self.main.main()
        self.main.request_show.assert_called_once_with()
        self.main.request_settings.assert_not_called()
        self.main.tk.Tk.assert_not_called()
        self.main.ctypes.windll.user32.MessageBoxW.assert_not_called()

    def test_second_start_falls_back_to_the_tray_message(self):
        self.main._acquire_single_instance_mutex.return_value = None
        self.main.request_show.return_value = False
        self.main.main()
        text = self.main.ctypes.windll.user32.MessageBoxW.call_args.args[1]
        self.assertIn("Hams", text)

    def test_on_linux_a_second_start_signals_the_running_instance(self):
        self.main._acquire_single_instance_mutex.return_value = None
        self.main.sys.platform = "linux"
        fake = types.SimpleNamespace(request_show=Mock(return_value=True), notify=Mock())
        # On Linux `hams.linux` is already an attribute of the package, so
        # `from hams import linux` would ignore sys.modules: patch both.
        package = sys.modules["hams"]
        with (
            patch.dict(sys.modules, {"hams.linux": fake}),
            patch.object(package, "linux", fake, create=True),
            self.assertRaises(SystemExit),
        ):
            self.main.main()
        fake.notify.assert_not_called()
        fake.request_show.return_value = False
        with (
            patch.dict(sys.modules, {"hams.linux": fake}),
            patch.object(package, "linux", fake, create=True),
            self.assertRaises(SystemExit),
        ):
            self.main.main()
        fake.notify.assert_called_once()

    def test_old_instance_without_settings_receiver_gets_a_hint(self):
        self.main._acquire_single_instance_mutex.return_value = None
        self.main.request_settings.return_value = False
        self.main.main(show_settings=True)
        self.main.request_show.assert_not_called()
        self.main.ctypes.windll.user32.MessageBoxW.assert_called_once()

    def test_cold_start_opens_settings_and_closes_receivers(self):
        self.main.main(show_settings=True)
        self.assertEqual(
            self.root.after_idle.call_args_list, [call(self.app._show_window), call(self.app._open_settings)]
        )
        calls = self.main.SettingsRequests.call_args_list
        self.assertEqual([c.kwargs["name"] for c in calls], [activation.SETTINGS_EVENT, activation.SHOW_EVENT])
        self.assertEqual(calls[0].args, (self.root, self.app._open_settings))
        self.assertEqual(self.main.SettingsRequests.return_value.close.call_count, 2)
        self.release.assert_called_once_with()

    def test_show_request_shows_the_window_and_focuses_an_open_wizard(self):
        self.main.main()
        show = self.main.SettingsRequests.call_args_list[1].args[1]
        show()
        self.app._show_window.assert_called_once_with()
        self.app.setup_win.focus.assert_called_once_with()
        self.app.setup_win.alive.return_value = False
        show()
        self.app.setup_win.focus.assert_called_once_with()

    def test_a_failing_receiver_does_not_stop_the_start(self):
        self.main.SettingsRequests.side_effect = [OSError("busy"), Mock()]
        self.main.main()
        self.root.mainloop.assert_called_once_with()

    def test_normal_autostart_stays_hidden(self):
        self.main.main(background=True)
        self.root.after_idle.assert_not_called()

    def test_a_start_by_hand_shows_the_window(self):
        self.main.main()
        self.root.after_idle.assert_called_once_with(self.app._show_window)

    def test_first_start_opens_the_setup_wizard(self):
        self.setup_wizard.needs_setup.return_value = True
        self.main.main(show_settings=True)
        self.factory.assert_called_once_with(self.root, setup=True)
        self.root.after_idle.assert_called_once_with(self.app._open_setup)

    def test_setup_flag_opens_the_wizard_after_it_was_completed(self):
        self.main.main(show_setup=True)
        self.root.after_idle.assert_called_once_with(self.app._open_setup)

    def test_cleanup_also_runs_after_mainloop_failure(self):
        self.root.mainloop.side_effect = RuntimeError("test")
        with self.assertRaises(RuntimeError):
            self.main.main(show_settings=True)
        self.assertEqual(self.main.SettingsRequests.return_value.close.call_count, 2)
        self.release.assert_called_once_with()

    def _run_threads_inline(self):
        def thread(target, **_kwargs):
            return types.SimpleNamespace(start=target)

        self.main.threading = types.SimpleNamespace(Thread=thread)

    def test_a_broken_config_is_shown_without_blocking_the_start(self):
        self._run_threads_inline()
        self.main.config.BROKEN_CONFIG = types.SimpleNamespace(
            path="C:/x/config.toml", backup="C:/x/config.toml.broken-1", error="duplicate table"
        )
        self.main.main()
        box = self.main.ctypes.windll.user32.MessageBoxW
        box.assert_called_once()
        message = box.call_args.args[1]
        assert "config.toml.broken-1" in message
        assert "duplicate table" in message
        self.factory.assert_called_once()

    def test_a_readable_config_shows_nothing(self):
        self._run_threads_inline()
        self.main.main()
        self.main.ctypes.windll.user32.MessageBoxW.assert_not_called()

    def test_a_second_instance_does_not_repeat_the_hint(self):
        self.main.config.BROKEN_CONFIG = types.SimpleNamespace(path="p", backup=None, error="e")
        self.main._acquire_single_instance_mutex.return_value = None
        self.main.main(show_settings=True)
        self.main.ctypes.windll.user32.MessageBoxW.assert_not_called()

    def test_on_linux_the_hint_is_a_notification(self):
        self.main.config.BROKEN_CONFIG = types.SimpleNamespace(path="p", backup=None, error="e")
        self.main.sys = types.SimpleNamespace(platform="linux")
        linux = types.SimpleNamespace(notify=Mock())
        import hams

        with patch.dict(sys.modules, {"hams.linux": linux}), patch.object(hams, "linux", linux, create=True):
            self.main._warn_about_broken_config()
        linux.notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
