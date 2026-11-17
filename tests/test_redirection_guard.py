"""Windows' RedirectionGuard: Hams notices it on its own process and starts itself once more without it.

The logic runs with fakes everywhere; on Windows two tests also switch the guard on for real, in a child process
(it can never be switched off again for a running process, so never in the test process itself).
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hams import redirection_guard as rg

ROOT = Path(__file__).resolve().parent.parent


class TestRestart:
    def test_only_the_installed_app_restarts(self):
        started = []
        assert not rg.restart_without_guard([], check=lambda: True, start=started.append, frozen=False)
        assert started == []

    def test_without_the_guard_nothing_happens(self):
        started = []
        assert not rg.restart_without_guard([], check=lambda: False, start=started.append, frozen=True)
        assert started == []

    def test_with_the_guard_it_restarts_with_the_same_options_and_the_marker(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        calls = []

        def start(command, *, cwd):
            calls.append((command, cwd))
            return True

        assert rg.restart_without_guard(["--background"], check=lambda: True, start=start, frozen=True)
        command, cwd = calls[0]
        assert command == [sys.executable, "--background", rg.RESTART_ARGUMENT]
        assert cwd == os.getcwd()

    def test_never_a_second_time(self):
        started = []
        assert not rg.restart_without_guard(
            ["--background", rg.RESTART_ARGUMENT], check=lambda: True, start=started.append, frozen=True
        )
        assert started == []

    def test_if_the_restart_fails_this_hams_keeps_running(self):
        assert not rg.restart_without_guard([], check=lambda: True, start=lambda *a, **k: False, frozen=True)

    def test_from_source_the_module_is_started(self, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        assert rg.restart_command(["--settings"]) == [sys.executable, "-m", "hams", "--settings"]

    def test_the_marker_never_doubles(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert rg.restart_command(["--background", rg.RESTART_ARGUMENT]) == [sys.executable, "--background"]

    def test_the_command_line_parser_lets_the_marker_through(self):
        from hams import cli

        args, unknown = cli._parser().parse_known_args(["--background", rg.RESTART_ARGUMENT])
        assert args.background and unknown == [rg.RESTART_ARGUMENT]


class TestStartFromShell:
    def test_without_a_shell_it_says_no_and_raises_nothing(self, monkeypatch):
        def unavailable(*_args):
            raise rg.ShellUnavailable("no shell window on this desktop")

        monkeypatch.setattr(rg, "_shell_execute", unavailable)
        monkeypatch.setattr(sys, "platform", "win32")
        assert rg.start_from_shell(["x.exe", "--background"]) is False

    def test_file_arguments_and_folder_go_to_the_shell(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(rg, "_shell_execute", lambda *args: calls.append(args))
        monkeypatch.setattr(sys, "platform", "win32")
        assert rg.start_from_shell(["C:/Hams/Hams.exe", "--background", "a b"], cwd=str(tmp_path)) is True
        assert calls == [("C:/Hams/Hams.exe", '--background "a b"', str(tmp_path))]

    def test_no_parent_process_is_faked(self):
        """The old way (PROC_THREAD_ATTRIBUTE_PARENT_PROCESS) is what virus scanners watch for; it must stay gone."""
        source = Path(rg.__file__).read_text(encoding="utf-8")
        assert "UpdateProcThreadAttribute" not in source
        assert "_PROC_THREAD_ATTRIBUTE_PARENT_PROCESS =" not in source


class TestDescribe:
    def test_one_word(self, monkeypatch):
        for value, word in ((True, "on"), (False, "off"), (None, "unknown")):
            monkeypatch.setattr(rg, "enforced", lambda value=value: value)
            assert rg.describe() == word

    @pytest.mark.skipif(sys.platform == "win32", reason="elsewhere only")
    def test_outside_windows_unknown(self):
        assert rg.enforced() is None
        assert rg.start_from_shell(["x"]) is False


_CHILD = textwrap.dedent(
    """
    import ctypes, sys
    from ctypes import wintypes
    sys.path.insert(0, {root!r})
    from hams import redirection_guard as rg
    k = ctypes.WinDLL("kernel32")
    k.SetProcessMitigationPolicy.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
    flags = wintypes.DWORD(1)
    k.SetProcessMitigationPolicy(16, ctypes.byref(flags), ctypes.sizeof(flags))
    print("guard", rg.describe())
    {body}
    """
)


def _run_guarded(body: str) -> str:
    code = _CHILD.format(root=str(ROOT), body=textwrap.dedent(body))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="RedirectionGuard is Windows")
class TestForReal:
    def test_a_child_inherits_the_guard(self):
        out = _run_guarded(
            """
            import subprocess
            child = "import sys; sys.path.insert(0, %r); from hams import redirection_guard as rg; print(rg.describe())"
            print("child", subprocess.run([sys.executable, "-c", child % sys.path[0]], capture_output=True,
                                          text=True).stdout.strip())
            """
        )
        if "guard unknown" in out:
            pytest.skip("this Windows has no RedirectionGuard")
        assert "guard on" in out
        assert "child on" in out  # why Hams must start itself anew rather than as a child

    def test_a_start_from_the_shell_runs_without_it(self, tmp_path):
        import ctypes

        if not ctypes.windll.user32.GetShellWindow():
            pytest.skip("no Windows shell on this desktop")
        target = tmp_path / "child.txt"
        out = _run_guarded(
            f"""
            import time, os
            # the base interpreter, not a venv launcher: its parent is the process that started it
            pyw = os.path.join(os.path.dirname(sys._base_executable), "pythonw.exe")
            code = ("import sys, os; sys.path.insert(0, %r); from hams import redirection_guard as rg; "
                    "open(%r, 'w').write(rg.describe() + ' ' + str(os.getppid()) + ' ' + sys.argv[-1])")
            print("started", rg.start_from_shell([pyw, "-c", code % (sys.path[0], {str(target)!r}),
                                                  rg.RESTART_ARGUMENT]))
            for _ in range(100):
                if os.path.exists({str(target)!r}) and open({str(target)!r}).read():
                    break
                time.sleep(0.1)
            """
        )
        if "guard unknown" in out:
            pytest.skip("this Windows has no RedirectionGuard")
        assert "started True" in out
        guard, parent, marker = target.read_text().split()
        assert (guard, marker) == ("off", rg.RESTART_ARGUMENT)
        name = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {parent}).ProcessName"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert name.lower() == "explorer"  # Explorer's own child, no parent faked


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_this_test_process_has_no_guard():
    """The suite itself runs without it; otherwise the real tests above would mean nothing."""
    assert rg.enforced() in (False, None)
