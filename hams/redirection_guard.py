"""Windows' RedirectionGuard: notice it on Hams' own process and start Hams once more without it.

RedirectionGuard (Windows 11, Windows 10 22H2; Microsoft calls the policy *Redirection Trust*) refuses to follow
symbolic links and junctions that a normal user created; every such path then fails with error 448
(``ERROR_UNTRUSTED_MOUNT_POINT``). Programs do not switch it on themselves, but Inno Setup 6.7.0 and later does so
for the setup, and Windows hands it on to every process the setup starts, Hams included: after an install, an
update ("Update now", ``/RESTARTHAMS``) or the "Start Hams" tick. Inno Setup 7.1.1 stopped that for programs started
with the user's own rights, except when the setup runs without asking for administrator rights - which is how Hams
installs (``PrivilegesRequired=lowest``).

A Hams in that state could not read its own speech models wherever the Hugging Face cache had created links
(Windows with developer mode). Two answers, both needed:

* ``packaging/hams.iss`` sets ``RedirectionGuard=no``: a setup of 1.3.6 or later switches nothing on any more.
* This module: a Hams that finds the mitigation on its process anyway - started by an older setup or by another
  program that had it - starts itself once more, as a child of the Windows shell (explorer.exe) and so without
  the mitigation, and quits. A mitigation cannot be switched off for a running process; a fresh start is the only
  way. The child gets ``HAMS_REDIRECTION_RESTART`` in its environment and never starts again itself, so this can
  never loop.

Measured on Windows 11 26H2 (build 26300): a child of a process with the mitigation has it too
(``GetProcessMitigationPolicy`` reports 1); a child created with explorer.exe as its parent
(``PROC_THREAD_ATTRIBUTE_PARENT_PROCESS``) has it off (0).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger("hams")

#: The environment variable the restarted Hams carries: it never restarts a second time.
RESTART_MARKER = "HAMS_REDIRECTION_RESTART"
#: The Windows error a path through an untrusted link ends with.
ERROR_UNTRUSTED_MOUNT_POINT = 448

_PROCESS_REDIRECTION_TRUST_POLICY = 16  # PROCESS_MITIGATION_POLICY enum
_PROC_THREAD_ATTRIBUTE_PARENT_PROCESS = 0x00020000
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_PROCESS_CREATE_PROCESS = 0x0080


def enforced() -> bool | None:
    """Whether RedirectionGuard is on for this process. None where Windows cannot say (older Windows, not Windows)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessMitigationPolicy.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
        kernel32.GetProcessMitigationPolicy.restype = wintypes.BOOL
        flags = wintypes.DWORD(0)
        if not kernel32.GetProcessMitigationPolicy(
            kernel32.GetCurrentProcess(), _PROCESS_REDIRECTION_TRUST_POLICY, ctypes.byref(flags), ctypes.sizeof(flags)
        ):
            return None  # Windows before the policy existed
        return bool(flags.value & 1)  # EnforceRedirectionTrust
    except (OSError, AttributeError):
        return None


def describe() -> str:
    """One word for the diagnostic report: on, off or unknown."""
    state = enforced()
    return "unknown" if state is None else ("on" if state else "off")


def _environment_block(env: dict[str, str]) -> str:
    """A CreateProcessW environment block: ``NAME=value`` entries, sorted, each NUL-terminated, one more NUL."""
    entries = sorted(env.items(), key=lambda item: item[0].upper())
    return "".join(f"{name}={value}\0" for name, value in entries) + "\0"


def start_from_shell(command: list[str], *, env: dict[str, str] | None = None, cwd: str | None = None) -> int | None:
    """Starts `command` as a child of the Windows shell (explorer.exe) instead of this process.

    The new process takes its process mitigations from the shell, so it runs without a RedirectionGuard this
    process may have. Returns the new process id, or None if there is no shell to start from or Windows refused.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):  # noqa: N801 - the Windows name
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetShellWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]

        shell = user32.GetShellWindow()
        if not shell:
            logger.warning("No Windows shell window to start from")
            return None
        shell_pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(shell, ctypes.byref(shell_pid))
        parent = kernel32.OpenProcess(_PROCESS_CREATE_PROCESS, False, shell_pid.value)
        if not parent:
            logger.warning("Windows shell could not be opened (error %d)", ctypes.get_last_error())
            return None
        try:
            size = ctypes.c_size_t(0)
            kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            if not kernel32.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
                raise OSError(ctypes.get_last_error(), "InitializeProcThreadAttributeList")
            try:
                parent_handle = wintypes.HANDLE(parent)
                if not kernel32.UpdateProcThreadAttribute(
                    attributes,
                    0,
                    _PROC_THREAD_ATTRIBUTE_PARENT_PROCESS,
                    ctypes.byref(parent_handle),
                    ctypes.sizeof(parent_handle),
                    None,
                    None,
                ):
                    raise OSError(ctypes.get_last_error(), "UpdateProcThreadAttribute")
                startup = STARTUPINFOEXW()
                startup.StartupInfo.cb = ctypes.sizeof(startup)
                startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
                info = PROCESS_INFORMATION()
                line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
                block = ctypes.create_unicode_buffer(_environment_block(dict(os.environ if env is None else env)))
                if not kernel32.CreateProcessW(
                    None,
                    line,
                    None,
                    None,
                    False,
                    _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
                    block,
                    cwd,
                    ctypes.byref(startup),
                    ctypes.byref(info),
                ):
                    raise OSError(ctypes.get_last_error(), "CreateProcessW")
                kernel32.CloseHandle(info.hThread)
                kernel32.CloseHandle(info.hProcess)
                return int(info.dwProcessId)
            finally:
                kernel32.DeleteProcThreadAttributeList(attributes)
        finally:
            kernel32.CloseHandle(parent)
    except (OSError, AttributeError, ValueError):
        logger.warning("Start from the Windows shell failed", exc_info=True)
        return None


def restart_command(argv: list[str] | None = None) -> list[str]:
    """The command line of this Hams again: the packaged exe, or the interpreter with ``-m hams``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "hams", *args]


def restart_without_guard(
    argv: list[str] | None = None, *, check=enforced, start=start_from_shell, frozen: bool | None = None
) -> bool:
    """Starts this Hams once more without RedirectionGuard if it is on here. True: the caller quits now.

    Only the installed app (`frozen`): a source checkout runs in a terminal the user watches, and a restart would
    tear it away from there. Only once: the restarted Hams carries `RESTART_MARKER` and keeps running whatever it
    finds. Called before the single-instance lock is taken, so the new Hams can take it.
    """
    if not (getattr(sys, "frozen", False) if frozen is None else frozen):
        return False
    if os.environ.get(RESTART_MARKER):
        if check():
            logger.warning("RedirectionGuard is still on after the restart; links in user folders stay blocked")
        return False
    if not check():
        return False
    env = dict(os.environ)
    env[RESTART_MARKER] = "1"
    pid = start(restart_command(argv), env=env, cwd=os.getcwd())
    if pid is None:
        logger.warning("RedirectionGuard is on and Hams could not start itself without it")
        return False
    logger.info("RedirectionGuard was on (inherited from the starting program): restarted as process %d", pid)
    return True
