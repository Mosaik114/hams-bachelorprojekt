"""Command line (hams/cli.py) and the self-test (hams/self_test.py)."""

import io
import sys
import types

import pytest

from hams import cli, gpu_support, self_test


class TestDispatch:
    def test_without_options_the_app_starts(self, monkeypatch):
        calls = []
        monkeypatch.setitem(sys.modules, "hams.main", types.SimpleNamespace(main=lambda **kw: calls.append(kw)))
        assert cli.run([]) == 0
        assert calls == [{"show_settings": False, "show_setup": False, "background": False}]

    def test_settings_and_unknown_options(self, monkeypatch):
        calls = []
        monkeypatch.setitem(sys.modules, "hams.main", types.SimpleNamespace(main=lambda **kw: calls.append(kw)))
        assert cli.run(["--settings", "--unbekannt"]) == 0
        assert calls == [{"show_settings": True, "show_setup": False, "background": False}]

    def test_setup_opens_the_wizard(self, monkeypatch):
        calls = []
        monkeypatch.setitem(sys.modules, "hams.main", types.SimpleNamespace(main=lambda **kw: calls.append(kw)))
        assert cli.run(["--setup"]) == 0
        assert calls == [{"show_settings": False, "show_setup": True, "background": False}]

    def test_argv_defaults_to_the_process_arguments(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["Hams.exe", "--self-test"])
        monkeypatch.setattr(self_test, "run", lambda stream: 0)
        assert cli.run() == 0

    def test_self_test_writes_the_report(self, monkeypatch, tmp_path, capsys):
        def fake_run(stream):
            print("self-test passed", file=stream)
            return 0

        monkeypatch.setattr(self_test, "run", fake_run)
        report = tmp_path / "unter" / "report.txt"
        assert cli.run(["--self-test", "--report", str(report)]) == 0
        assert report.read_text(encoding="utf-8") == "self-test passed\n"
        assert "self-test passed" in capsys.readouterr().out

    def test_self_test_failures_give_exit_code_one(self, monkeypatch):
        monkeypatch.setattr(self_test, "run", lambda stream: 3)
        assert cli.run(["--self-test"]) == 1

    def test_install_gpu_support(self, monkeypatch):
        seen = []
        monkeypatch.setattr(gpu_support, "install_cli", lambda stream: seen.append(stream) or 0)
        assert cli.run(["--install-gpu-support"]) == 0
        assert len(seen) == 1


class TestOutputWithoutConsole:
    """Hams.exe is a windowed program: sys.stdout is None there."""

    def test_falls_back_to_the_parent_console(self, monkeypatch):
        console = io.StringIO()
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(cli, "_parent_console", lambda: console)
        stream = cli._output(None)
        stream.write("hallo")
        assert console.getvalue() == "hallo"
        stream.close()
        assert console.closed

    def test_without_any_console_only_the_report(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(cli, "_parent_console", lambda: None)
        stream = cli._output(tmp_path / "r.txt")
        stream.write("x")
        stream.close()
        assert (tmp_path / "r.txt").read_text(encoding="utf-8") == "x"

    def test_a_broken_target_does_not_stop_the_others(self):
        class Broken(io.StringIO):
            def write(self, text):
                raise OSError("weg")

        good = io.StringIO()
        tee = cli._Tee([Broken(), good], [])
        assert tee.write("abc") == 3
        assert good.getvalue() == "abc"

    def test_parent_console_off_windows(self, monkeypatch):
        monkeypatch.setattr(cli.sys, "platform", "linux")
        assert cli._parent_console() is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_parent_console_when_attach_fails(self, monkeypatch):
        import ctypes

        kernel32 = types.SimpleNamespace(AttachConsole=lambda pid: 0)
        monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel32))
        assert cli._parent_console() is None


class TestSelfTest:
    def test_counts_failures_and_says_so(self):
        def broken():
            raise ImportError("ctranslate2 fehlt")

        out = io.StringIO()
        failures = self_test.run(out, (("gut", lambda: "1.0"), ("kaputt", broken)))
        text = out.getvalue()
        assert failures == 1
        assert "ok   gut: 1.0" in text
        assert "FAIL kaputt: ImportError: ctranslate2 fehlt" in text
        assert "self-test failed (1)" in text

    def test_passes_when_everything_works(self):
        out = io.StringIO()
        assert self_test.run(out, (("gut", lambda: "ja"),)) == 0
        assert out.getvalue().endswith("self-test passed\n")

    @pytest.mark.parametrize(
        "check",
        [
            self_test.check_hams,
            self_test.check_config,
            self_test.check_package_data,
            self_test.check_backend,
            self_test.check_vad,
            self_test.check_audio,
            self_test.check_app_modules,
            self_test.check_gpu_support,
        ],
    )
    def test_each_real_check_passes_in_the_source_tree(self, check):
        assert check()

    def test_tk_check_withdraws_and_destroys(self, monkeypatch):
        import tkinter

        events = []

        class FakeTk:
            def __init__(self):
                self.tk = types.SimpleNamespace(call=lambda *args: "8.6.99")

            def withdraw(self):
                events.append("withdraw")

            def destroy(self):
                events.append("destroy")

        monkeypatch.setattr(tkinter, "Tk", FakeTk)
        assert self_test.check_tk() == "Tcl/Tk 8.6.99"
        assert events == ["withdraw", "destroy"]

    def test_missing_package_data_fails(self, monkeypatch, tmp_path):
        from hams import config

        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "fehlt.toml")
        with pytest.raises(FileNotFoundError, match="fehlt.toml"):
            self_test.check_package_data()

    def test_unknown_distribution_version(self):
        assert self_test._version("gibt-es-nicht-hams") == "?"


def test_python_dash_m_hams_uses_the_cli():
    import hams.__main__ as entry

    assert entry.run is cli.run
