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
  program that had it - asks the running Windows shell (Explorer) to start it once more, and quits. A mitigation
  cannot be switched off for a running process; a fresh start by a process without it is the only way. The new
  Hams gets ``RESTART_ARGUMENT`` on its command line and never starts again itself, so this can never loop.

The request goes through the shell's own automation interface, the documented way to have Explorer start a
program (``IShellWindows.FindWindowSW`` -> desktop view -> ``Shell.Application`` -> ``ShellExecute``; Raymond Chen,
"How can I launch an unelevated process from my elevated process and vice versa?", 2013). Explorer starts the
program itself, so it is Explorer's child for real and takes Explorer's mitigations. No parent process is faked:
that trick (``PROC_THREAD_ATTRIBUTE_PARENT_PROCESS``) works too, but virus scanners rightly watch for it.

Measured on Windows 11 26H2 (build 26300): a child of a process with the mitigation has it too
(``GetProcessMitigationPolicy`` reports 1); a program started through the shell this way has it off (0) and
explorer.exe as its parent.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from contextlib import suppress

logger = logging.getLogger("hams")

#: The command-line argument the restarted Hams carries: it never restarts a second time. A command-line argument
#: and not an environment variable, because Explorer starts the program with its own environment.
RESTART_ARGUMENT = "--redirection-restarted"
#: The Windows error a path through an untrusted link ends with.
ERROR_UNTRUSTED_MOUNT_POINT = 448

_PROCESS_REDIRECTION_TRUST_POLICY = 16  # PROCESS_MITIGATION_POLICY enum


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


# ------------------------------------------------------------------ the shell's automation interface (COM, ctypes)

_CLSID_SHELL_WINDOWS = "{9BA05972-F6A8-11CF-A442-00A0C90A8F39}"
_IID_ISHELL_WINDOWS = "{85CB6900-4D95-11CF-960C-0080C7F4EE85}"
_IID_ISERVICE_PROVIDER = "{6D5140C1-7436-11CE-8034-00AA006009FA}"
_SID_STOP_LEVEL_BROWSER = "{4C96BE40-915C-11CF-99D3-00AA004AE837}"
_IID_ISHELL_BROWSER = "{000214E2-0000-0000-C000-000000000046}"
_IID_IDISPATCH = "{00020400-0000-0000-C000-000000000046}"
_IID_NULL = "{00000000-0000-0000-0000-000000000000}"

_CLSCTX_ALL = 0x17
_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106: COM is already set up differently on this thread; still usable
_SWC_DESKTOP = 8
_SWFO_NEEDDISPATCH = 1
_SVGIO_BACKGROUND = 0
_DISPATCH_METHOD = 1
_DISPATCH_PROPERTYGET = 2
_VT_I4 = 3
_VT_BSTR = 8
_SW_SHOWNORMAL = 1

# Positions in the interface tables (the order of the methods in the Windows SDK headers).
_QUERY_INTERFACE, _RELEASE = 0, 2
_IDISPATCH_GET_IDS_OF_NAMES, _IDISPATCH_INVOKE = 5, 6
_ISHELL_WINDOWS_FIND_WINDOW_SW = 15  # IDispatch (7), Count, Item, _NewEnum, Register, RegisterPending, Revoke, ...
_ISERVICE_PROVIDER_QUERY_SERVICE = 3
_ISHELL_BROWSER_QUERY_ACTIVE_SHELL_VIEW = 15  # IOleWindow (2), then 10 *SB methods before it
_ISHELL_VIEW_GET_ITEM_OBJECT = 15  # IOleWindow (2), then 10 view methods before it


class ShellUnavailable(Exception):
    """There is no Windows shell to ask, or it said no."""


class _Com:
    """Just enough COM through ctypes for one request to the shell: no pywin32 or comtypes in the package.

    Every object it hands out is released by `close`, every string freed; methods are called by their position in
    the interface table, except the shell's own methods, which go by name through IDispatch.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wintypes = ctypes, wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        class Value(ctypes.Union):
            _fields_ = [("llVal", ctypes.c_longlong), ("ptr", ctypes.c_void_p), ("pair", ctypes.c_void_p * 2)]

        class VARIANT(ctypes.Structure):
            _fields_ = [
                ("vt", wintypes.WORD),
                ("r1", wintypes.WORD),
                ("r2", wintypes.WORD),
                ("r3", wintypes.WORD),
                ("value", Value),
            ]

        class DISPPARAMS(ctypes.Structure):
            _fields_ = [
                ("rgvarg", ctypes.POINTER(VARIANT)),
                ("rgdispidNamedArgs", ctypes.c_void_p),
                ("cArgs", wintypes.UINT),
                ("cNamedArgs", wintypes.UINT),
            ]

        self.GUID, self.VARIANT, self.DISPPARAMS = GUID, VARIANT, DISPPARAMS
        self.ole32 = ctypes.OleDLL("ole32")
        self.oleaut32 = ctypes.WinDLL("oleaut32")
        self.oleaut32.SysAllocString.argtypes = [wintypes.LPCWSTR]
        self.oleaut32.SysAllocString.restype = ctypes.c_void_p
        self.oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
        self.oleaut32.VariantClear.argtypes = [ctypes.POINTER(VARIANT)]
        self._objects: list = []
        self._strings: list[int] = []
        self._initialized = False

    # ---------------------------------------------------------------- set-up and clean-up

    def open(self) -> None:
        try:
            self._initialized = self.ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED) >= 0
        except OSError as exc:
            if getattr(exc, "winerror", None) != _RPC_E_CHANGED_MODE:
                raise ShellUnavailable("COM could not be set up") from exc
            # COM already runs differently on this thread: usable, but not ours to close

    def close(self) -> None:
        for string in self._strings:
            self.oleaut32.SysFreeString(string)
        for pointer in reversed(self._objects):
            with suppress(OSError):
                self.call(pointer, _RELEASE)
        if self._initialized:
            self.ole32.CoUninitialize()

    # ---------------------------------------------------------------- building blocks

    def guid(self, text: str):
        value = self.GUID()
        self.ole32.CLSIDFromString(self.wintypes.LPCWSTR(text), self.ctypes.byref(value))
        return value

    def call(self, pointer, index: int, *args, argtypes=()) -> int:
        """Method `index` of a COM object; returns the HRESULT (0 or more: success)."""
        ctypes = self.ctypes
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
        return prototype(table[index])(pointer, *args)

    @staticmethod
    def check(result: int, what: str) -> None:
        if result < 0:
            raise ShellUnavailable(f"{what} failed (0x{result & 0xFFFFFFFF:08X})")

    def keep(self, pointer, what: str):
        if not pointer.value:
            raise ShellUnavailable(f"{what} returned nothing")
        self._objects.append(pointer)
        return pointer

    def out_call(self, pointer, index: int, args: tuple, argtypes: tuple, what: str):
        """Calls a method whose last parameter receives an interface; returns that interface, kept for `close`."""
        out = self.ctypes.c_void_p()
        p = self.ctypes.c_void_p
        self.check(self.call(pointer, index, *args, self.ctypes.byref(out), argtypes=(*argtypes, p)), what)
        return self.keep(out, what)

    def query(self, pointer, iid: str, what: str):
        p = self.ctypes.c_void_p
        return self.out_call(pointer, _QUERY_INTERFACE, (self.ctypes.byref(self.guid(iid)),), (p,), what)

    def text(self, value: str):
        variant = self.VARIANT()
        variant.vt = _VT_BSTR
        variant.value.ptr = self.oleaut32.SysAllocString(value)
        self._strings.append(variant.value.ptr)
        return variant

    def number(self, value: int):
        variant = self.VARIANT()
        variant.vt = _VT_I4
        variant.value.llVal = value
        return variant

    def dispatch(self, pointer, name: str, flags: int, args: list, what: str):
        """Calls a method or reads a property by name through IDispatch: no table position to get wrong.

        The caller clears the returned VARIANT (`VariantClear`).
        """
        ctypes, wintypes = self.ctypes, self.wintypes
        p = ctypes.c_void_p
        names = (wintypes.LPCWSTR * 1)(name)
        dispid = ctypes.c_long()
        null = ctypes.byref(self.guid(_IID_NULL))
        self.check(
            self.call(
                pointer,
                _IDISPATCH_GET_IDS_OF_NAMES,
                null,
                names,
                1,
                0,
                ctypes.byref(dispid),
                argtypes=(p, p, wintypes.UINT, wintypes.DWORD, p),
            ),
            f"{what} (name)",
        )
        array = (self.VARIANT * max(1, len(args)))(*reversed(args))  # IDispatch takes the arguments last to first
        params = self.DISPPARAMS(array if args else None, None, len(args), 0)
        result = self.VARIANT()
        self.check(
            self.call(
                pointer,
                _IDISPATCH_INVOKE,
                dispid,
                null,
                0,
                flags,
                ctypes.byref(params),
                ctypes.byref(result),
                None,
                None,
                argtypes=(ctypes.c_long, p, wintypes.DWORD, wintypes.WORD, p, p, p, p),
            ),
            what,
        )
        return result


def _desktop_folder_view(com: _Com):
    """The desktop's folder view, as IDispatch: the door to Explorer's own Shell.Application."""
    ctypes = com.ctypes
    p, i = ctypes.c_void_p, ctypes.c_int
    windows = ctypes.c_void_p()
    try:
        com.ole32.CoCreateInstance(
            ctypes.byref(com.guid(_CLSID_SHELL_WINDOWS)),
            None,
            _CLSCTX_ALL,
            ctypes.byref(com.guid(_IID_ISHELL_WINDOWS)),
            ctypes.byref(windows),
        )
    except OSError as exc:
        raise ShellUnavailable("the shell's window list is not available") from exc
    com.keep(windows, "ShellWindows")

    location, root, hwnd = com.number(0), com.VARIANT(), ctypes.c_long()  # CSIDL_DESKTOP, an empty root
    desktop = com.out_call(
        windows,
        _ISHELL_WINDOWS_FIND_WINDOW_SW,
        (ctypes.byref(location), ctypes.byref(root), _SWC_DESKTOP, ctypes.byref(hwnd), _SWFO_NEEDDISPATCH),
        (p, p, i, p, i),
        "FindWindowSW",
    )
    provider = com.query(desktop, _IID_ISERVICE_PROVIDER, "IServiceProvider")
    browser = com.out_call(
        provider,
        _ISERVICE_PROVIDER_QUERY_SERVICE,
        (ctypes.byref(com.guid(_SID_STOP_LEVEL_BROWSER)), ctypes.byref(com.guid(_IID_ISHELL_BROWSER))),
        (p, p),
        "QueryService",
    )
    view = com.out_call(browser, _ISHELL_BROWSER_QUERY_ACTIVE_SHELL_VIEW, (), (), "QueryActiveShellView")
    return com.out_call(
        view,
        _ISHELL_VIEW_GET_ITEM_OBJECT,
        (_SVGIO_BACKGROUND, ctypes.byref(com.guid(_IID_IDISPATCH))),
        (com.wintypes.UINT, p),
        "GetItemObject",
    )


def _shell_execute(file: str, arguments: str, directory: str) -> None:
    """Asks the running Windows shell to start `file`. Raises `ShellUnavailable` if that is not possible."""
    import ctypes

    if not ctypes.WinDLL("user32").GetShellWindow():
        raise ShellUnavailable("no shell window on this desktop")
    com = _Com()
    com.open()
    try:
        application = com.dispatch(_desktop_folder_view(com), "Application", _DISPATCH_PROPERTYGET, [], "Application")
        try:
            shell = ctypes.c_void_p(application.value.ptr)
            if not shell.value:
                raise ShellUnavailable("the shell gave no Application object")
            args = [
                com.text(file),
                com.text(arguments),
                com.text(directory),
                com.text("open"),
                com.number(_SW_SHOWNORMAL),
            ]
            outcome = com.dispatch(shell, "ShellExecute", _DISPATCH_METHOD, args, "ShellExecute")
            com.oleaut32.VariantClear(ctypes.byref(outcome))
        finally:
            com.oleaut32.VariantClear(ctypes.byref(application))
    finally:
        com.close()


def start_from_shell(command: list[str], *, cwd: str | None = None) -> bool:
    """Has the Windows shell (Explorer) start `command`, instead of starting it as a child of this process.

    The new process is Explorer's child and takes Explorer's process mitigations, so it runs without a
    RedirectionGuard this process may have. Explorer starts it with its own environment. False if there is no shell
    to ask (no Explorer, a desktop without one) or it refused; the reason is in the log.
    """
    if sys.platform != "win32" or not command:
        return False
    try:
        _shell_execute(command[0], subprocess.list2cmdline(command[1:]), cwd or os.getcwd())
    except (ShellUnavailable, OSError, AttributeError, ValueError) as exc:
        logger.warning("Start through the Windows shell failed: %s", exc)
        return False
    return True


def restart_command(argv: list[str] | None = None) -> list[str]:
    """The command line of this Hams again: the packaged exe, or the interpreter with ``-m hams``."""
    args = [arg for arg in (sys.argv[1:] if argv is None else argv) if arg != RESTART_ARGUMENT]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "hams", *args]


def restart_without_guard(
    argv: list[str] | None = None, *, check=enforced, start=start_from_shell, frozen: bool | None = None
) -> bool:
    """Starts this Hams once more without RedirectionGuard if it is on here. True: the caller quits now.

    Only the installed app (`frozen`): a source checkout runs in a terminal the user watches, and a restart would
    tear it away from there. Only once: the restarted Hams carries `RESTART_ARGUMENT` and keeps running whatever it
    finds. Called before the single-instance lock is taken, so the new Hams can take it.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not (getattr(sys, "frozen", False) if frozen is None else frozen):
        return False
    if RESTART_ARGUMENT in args:
        if check():
            logger.warning("RedirectionGuard is still on after the restart; links in user folders stay blocked")
        return False
    if not check():
        return False
    if not start([*restart_command(args), RESTART_ARGUMENT], cwd=os.getcwd()):
        logger.warning("RedirectionGuard is on and Hams could not start itself without it")
        return False
    logger.info("RedirectionGuard was on (inherited from the starting program): restarted through the Windows shell")
    return True
