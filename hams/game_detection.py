"""Whether a game (or anything else that wants the graphics card) runs, for `idle_release`.

Windows has no public question "is a game running?", so three cheap signals are put together. All of them only
read: window positions and styles and the system's performance counters. No other process is opened, nothing is
read from its memory and nothing is hooked into it, so anti-cheat software and virus scanners have nothing to see.

1. **The shell** (`SHQueryUserNotificationState`, the same call the level at the pointer uses): exclusive Direct3D
   full screen, or a full-screen application or presentation mode the shell noticed. It misses many borderless games
   and looks only at the primary monitor.
2. **Window geometry** on every monitor: a top-level window whose *client area* covers its whole monitor
   (`MonitorFromWindow` + `GetMonitorInfo`, `rcMonitor`). Like Windows' own check (Raymond Chen) the client area
   counts, so a maximised window with a title bar never does, not even with an auto-hiding taskbar. Left out:
   invisible, minimised and cloaked windows (other virtual desktops, hidden store apps), the desktop and the
   taskbars, click-through overlays such as NVIDIA's or Discord's (transparent, no-activate or tool windows) and
   Hams' own windows.
3. **Graphics memory**: the whole card's dedicated usage from the performance counter
   `\\GPU Adapter Memory(*)\\Dedicated Usage` (PDH, no admin rights needed), minus what Hams holds itself (DXGI,
   `gpu_memory`), against the card's size. The card is the CUDA device Hams uses, matched by its LUID.

The rule (`GameDetector`):

- signal 1, or a full-monitor window in front: a game, at once;
- graphics memory *tight*: with Hams' model loaded, less than `TIGHT_FREE` (1.5 GiB) would stay free (and it stays
  tight until 256 MiB more are free again, `HYSTERESIS`);
- a full-monitor window that is *not* in front (a game on the second monitor while the user dictates on the first)
  only together with graphics memory *getting tight* (`GETTING_TIGHT_FREE`, 2.5 GiB). A video in full screen on a
  second monitor leaves plenty free, so Hams does not reload after every pause because of it.

Why PDH and not the DXGI budget: measured on an RTX 5070 (12 GB), the budget of a background CUDA process stays at
11175 MiB until less than about 2 GB of the card is free, then drops in one step to about 7200 MiB and stays there
however full the card gets. It never falls below what Hams holds (150 MiB parked, 2.3 GB loaded), so "budget below
usage" never fires. The counter follows every allocation within half a second.

Costs, measured: the counter query takes about 0.2 s to open (once, on a thread of its own), a reading 0.01 ms;
the DXGI query 0.4 ms; the window check of the foreground window a few µs. All windows are only walked while the
graphics memory is getting tight.

Every failure is quiet: the signal that failed counts as "no", Hams behaves as before, and it is logged once.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import threading
from ctypes import wintypes

from hams import gpu_memory
from hams.config import logger

_GIB = 1024**3
_MIB = 1024**2

#: Graphics memory is tight when, with Hams' model loaded, less than this would stay free (bytes). On the RTX 5070
#: Windows itself starts to cut the budgets of background programs between 2.1 and 1.6 GB free (measured); 1.5 GiB
#: is just below, so Hams only acts when Windows already finds memory scarce, and an ordinary desktop on a 6 GB card
#: with Hams loaded (about 2.8 GB free) does not count.
TIGHT_FREE = int(1.5 * _GIB)
#: Together with a full-monitor window that is not in front, already this much counts (bytes).
GETTING_TIGHT_FREE = int(2.5 * _GIB)
#: Once memory counted as tight, it stays so until this much more is free again (bytes): a game whose usage wobbles
#: around a threshold does not switch the signal (and the log, and Ollama's unloading) every ten seconds.
HYSTERESIS = 256 * _MIB

#: The reasons `GameDetector` gives (for the log).
SHELL = "full-screen application (Windows)"
IN_FRONT = "full-screen window in front"
MEMORY = "graphics memory tight"
ELSEWHERE = "full-screen window not in front, graphics memory getting tight"

#: Window classes of the desktop and the taskbars: they cover a monitor without being an application.
SHELL_CLASSES = frozenset({"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"})

WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
#: Extended styles of an overlay that lies over a game without being one (click-through, never active, no button).
OVERLAY_STYLES = WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE

GWL_EXSTYLE = -20
MONITOR_DEFAULTTONULL = 0
DWMWA_CLOAKED = 14
PDH_FMT_LARGE = 0x00000400
PDH_MORE_DATA = 0x800007D2
#: PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA: a value that may be used.
PDH_GOOD_STATUS = (0, 1)
COUNTER = "\\GPU Adapter Memory(*)\\Dedicated Usage"

Rect = tuple[int, int, int, int]


def covers(area: Rect | None, monitor: Rect | None) -> bool:
    """Whether `area` (left, top, right, bottom) covers all of `monitor`."""
    if area is None or monitor is None or monitor[2] <= monitor[0] or monitor[3] <= monitor[1]:
        return False
    return area[0] <= monitor[0] and area[1] <= monitor[1] and area[2] >= monitor[2] and area[3] >= monitor[3]


def full_monitor(windows, hwnd: int) -> bool:
    """Whether the top-level window `hwnd` is an application whose client area covers its whole monitor."""
    if not hwnd or not windows.visible(hwnd) or windows.minimised(hwnd):
        return False
    if windows.ex_style(hwnd) & OVERLAY_STYLES or windows.class_name(hwnd) in SHELL_CLASSES:
        return False
    if windows.own(hwnd) or windows.cloaked(hwnd):
        return False
    return covers(windows.client_area(hwnd), windows.monitor_area(hwnd))


def free_if_loaded(total: int, card_usage: int, own: int, own_loaded: int) -> int:
    """What would stay free of `total` with Hams' model loaded: the others' usage is `card_usage - own`, and Hams
    needs `own_loaded` (the most it held so far), so the answer does not jump when the model is parked or loaded."""
    others = max(0, card_usage - own)
    return total - others - max(own, own_loaded)


class Win32Windows:
    """The few window queries the check needs; tests replace it with a fake. Own `WinDLL` instances."""

    def __init__(self) -> None:
        void = ctypes.c_void_p
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.dwmapi = None
        with contextlib.suppress(OSError):
            self.dwmapi = ctypes.WinDLL("dwmapi")
        user32 = self.user32
        self._enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, void, void)
        _signature(user32.GetForegroundWindow, [], void)
        _signature(user32.EnumWindows, [self._enum_proc, void])
        _signature(user32.IsWindowVisible, [void])
        _signature(user32.IsIconic, [void])
        _signature(user32.GetWindowLongW, [void, ctypes.c_int], ctypes.c_long)
        _signature(user32.GetClassNameW, [void, ctypes.c_wchar_p, ctypes.c_int])
        _signature(user32.GetWindowThreadProcessId, [void, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD)
        _signature(user32.GetClientRect, [void, ctypes.POINTER(wintypes.RECT)])
        _signature(user32.ClientToScreen, [void, ctypes.POINTER(wintypes.POINT)])
        _signature(user32.MonitorFromWindow, [void, wintypes.DWORD], void)
        _signature(user32.GetMonitorInfoW, [void, ctypes.POINTER(_MonitorInfo)])
        if self.dwmapi is not None:
            _signature(self.dwmapi.DwmGetWindowAttribute, [void, wintypes.DWORD, void, wintypes.DWORD], ctypes.c_long)
        self._pid = os.getpid()

    def foreground(self) -> int:
        return int(self.user32.GetForegroundWindow() or 0)

    def top_level(self) -> list[int]:
        found: list[int] = []

        def collect(hwnd, _param):
            found.append(int(hwnd or 0))
            return True

        self.user32.EnumWindows(self._enum_proc(collect), None)
        return found

    def visible(self, hwnd: int) -> bool:
        return bool(self.user32.IsWindowVisible(hwnd))

    def minimised(self, hwnd: int) -> bool:
        return bool(self.user32.IsIconic(hwnd))

    def ex_style(self, hwnd: int) -> int:
        return int(self.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF

    def class_name(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(64)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def own(self, hwnd: int) -> bool:
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == self._pid

    def cloaked(self, hwnd: int) -> bool:
        if self.dwmapi is None:
            return False
        value = wintypes.DWORD()
        result = self.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
        return result == 0 and value.value != 0

    def client_area(self, hwnd: int) -> Rect | None:
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom

    def monitor_area(self, hwnd: int) -> Rect | None:
        handle = self.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONULL)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not handle or not self.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return None
        area = info.rcMonitor
        return area.left, area.top, area.right, area.bottom


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class _PdhValue(ctypes.Structure):
    _fields_ = [("status", wintypes.DWORD), ("value", ctypes.c_longlong)]


class _PdhItem(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar_p), ("value", _PdhValue)]


def _signature(function, argtypes: list, restype=ctypes.c_int) -> None:
    function.argtypes = argtypes
    function.restype = restype


class PdhCounter:
    """`\\GPU Adapter Memory(*)\\Dedicated Usage`: bytes in use per adapter instance, as the Task Manager shows."""

    def __init__(self) -> None:
        self.pdh = ctypes.WinDLL("pdh")
        void = ctypes.c_void_p
        _signature(self.pdh.PdhOpenQueryW, [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(void)], wintypes.DWORD)
        _signature(
            self.pdh.PdhAddEnglishCounterW,
            [void, ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(void)],
            wintypes.DWORD,
        )
        _signature(self.pdh.PdhCollectQueryData, [void], wintypes.DWORD)
        _signature(
            self.pdh.PdhGetFormattedCounterArrayW,
            [void, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), void],
            wintypes.DWORD,
        )
        self.query = void()
        self.counter = void()
        _check(self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)), "PdhOpenQuery")
        _check(self.pdh.PdhAddEnglishCounterW(self.query, COUNTER, 0, ctypes.byref(self.counter)), "PdhAddCounter")
        _check(self.pdh.PdhCollectQueryData(self.query), "PdhCollectQueryData")

    def read(self) -> dict[str, int]:
        """Instance name ("luid_0x…_0x…_phys_0") → dedicated usage in bytes."""
        _check(self.pdh.PdhCollectQueryData(self.query), "PdhCollectQueryData")
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        args = (self.counter, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count))
        status = self.pdh.PdhGetFormattedCounterArrayW(*args, None)
        if status != PDH_MORE_DATA:
            _check(status, "PdhGetFormattedCounterArray")
            return {}
        buffer = (ctypes.c_byte * size.value)()
        _check(self.pdh.PdhGetFormattedCounterArrayW(*args, buffer), "PdhGetFormattedCounterArray")
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhItem))
        return {
            items[index].name: int(items[index].value.value)
            for index in range(count.value)
            if items[index].value.status in PDH_GOOD_STATUS and items[index].name
        }


def _check(status: int, what: str) -> None:
    if status != 0:
        raise OSError(f"{what} failed: 0x{status & 0xFFFFFFFF:08X}")


def cuda_luid() -> str | None:
    """The LUID of CUDA device 0, the card CTranslate2 uses; None while CUDA is not loaded in this process.

    Only asked when `nvcuda.dll` is already loaded (the model runs on the GPU), so a CPU Hams never starts CUDA.
    """
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    if not kernel32.GetModuleHandleW("nvcuda.dll"):
        return None
    cuda = ctypes.WinDLL("nvcuda")
    device = ctypes.c_int()
    luid = (ctypes.c_ubyte * 8)()
    mask = ctypes.c_uint()
    if cuda.cuInit(0) != 0 or cuda.cuDeviceGet(ctypes.byref(device), 0) != 0:
        return None
    if cuda.cuDeviceGetLuid(luid, ctypes.byref(mask), device) != 0:
        return None
    raw = bytes(luid)
    return gpu_memory.luid_name(int.from_bytes(raw[4:], "little"), int.from_bytes(raw[:4], "little"))


class GraphicsMemory:
    """How much graphics memory would stay free with Hams' model loaded, on the card Hams uses.

    `counter` opens the PDH counter (on a thread of its own: opening takes about 0.2 s), `adapters` lists the DXGI
    adapters, `luid` names the CUDA card; tests replace all three, and `spawn` runs the opener inline.
    """

    def __init__(self, *, counter=PdhCounter, adapters=gpu_memory.adapter_details, luid=cuda_luid, spawn=None):
        self._open_counter = counter
        self._adapters = adapters
        self._luid = luid
        self._spawn = spawn or _thread
        self._counter = None
        #: "new" → "opening" → "open" or "failed".
        self._state = "new"
        self._card: str | None = None
        #: The most Hams held on the card so far: what its model needs when loaded.
        self._own_loaded = 0
        self._logged: set[str] = set()

    def free(self) -> int | None:
        """Bytes that would stay free with the model loaded; None while unknown (no GPU model, no counter)."""
        if self._state == "new":
            self._state = "opening"
            self._spawn(self._open)
        if self._state != "open":
            return None
        adapter = self._adapter()
        if adapter is None:
            return None
        usage = self._card_usage(adapter.luid)
        if usage is None:
            return None
        self._own_loaded = max(self._own_loaded, adapter.usage)
        return free_if_loaded(adapter.dedicated, usage, adapter.usage, self._own_loaded)

    def _open(self) -> None:
        try:
            self._counter = self._open_counter()
            self._state = "open"
        except Exception:  # noqa: BLE001 - no counter means no memory signal
            self._state = "failed"
            logger.info("Graphics memory counter not available; games are told by the screen only", exc_info=True)

    def _adapter(self):
        """The DXGI adapter of the CUDA card, else the one Hams holds the most memory on; None if Hams holds none."""
        if self._card is None:
            self._card = self._card_luid()
        found = self._adapters()
        if self._card:
            for adapter in found:
                if adapter.luid == self._card:
                    return adapter
            self._once("card", "The CUDA card %s is not among the graphics adapters", self._card)
        used = [adapter for adapter in found if adapter.usage > 0]
        return max(used, key=lambda adapter: adapter.usage) if used else None

    def _card_luid(self) -> str | None:
        """The CUDA card's LUID; "" once asking failed (then the card Hams holds memory on is taken)."""
        try:
            return self._luid()
        except Exception:  # noqa: BLE001 - an old driver without cuDeviceGetLuid
            logger.debug("CUDA card LUID unknown", exc_info=True)
            return ""

    def _card_usage(self, luid: str) -> int | None:
        prefix = luid.lower() + "_"
        values = [value for name, value in self._counter.read().items() if name.lower().startswith(prefix)]
        if not values:
            self._once("instance", "No graphics memory counter for %s", luid)
            return None
        return sum(values)

    def _once(self, key: str, message: str, *args) -> None:
        if key not in self._logged:
            self._logged.add(key)
            logger.info(message, *args)


def _thread(target) -> None:
    threading.Thread(target=target, daemon=True, name="gpu-counter").start()


class GameDetector:
    """Callable: the reason why a game seems to run (one of `SHELL`, `IN_FRONT`, `MEMORY`, `ELSEWHERE`), or None.

    `notifications` answers signal 1 (the shell), `windows` is a `Win32Windows`, `memory` a `GraphicsMemory`; tests
    pass fakes. Each signal that raises counts as "no" and is logged once; a change of the answer is logged once too.
    """

    def __init__(self, *, notifications=None, windows=None, memory=None) -> None:
        self._shell = notifications
        self._windows = windows
        self._memory = memory
        self._last: str | None = None
        self._failed: set[str] = set()

    def __call__(self) -> str | None:
        reason, free = self._reason()
        if reason != self._last:
            shown = "" if free is None else f" ({round(free / _MIB)} MiB would stay free)"
            logger.info("Game signal: %s%s", reason or "none", shown)
            self._last = reason
        return reason

    def _reason(self) -> tuple[str | None, int | None]:
        if self._safe("shell", self._shell_says, False):
            return SHELL, None
        windows = self._safe("windows", self._windows_api, None)
        foreground = self._safe("windows", windows.foreground, 0) if windows is not None else 0
        if windows is not None and self._safe("windows", lambda: full_monitor(windows, foreground), False):
            return IN_FRONT, None
        free = self._safe("memory", self._free, None)
        margin = HYSTERESIS if self._last in (MEMORY, ELSEWHERE) else 0
        if free is None or free >= GETTING_TIGHT_FREE + margin:
            return None, free
        if free < TIGHT_FREE + (margin if self._last == MEMORY else 0):
            return MEMORY, free
        if windows is not None and self._safe("windows", lambda: self._elsewhere(windows, foreground), False):
            return ELSEWHERE, free
        return None, free

    def _shell_says(self) -> bool:
        if self._shell is None:
            from hams.cursor_level import Win32Desktop

            self._shell = Win32Desktop().fullscreen
        return bool(self._shell())

    def _windows_api(self):
        if self._windows is None:
            self._windows = Win32Windows()
        return self._windows

    def _free(self) -> int | None:
        if self._memory is None:
            self._memory = GraphicsMemory()
        return self._memory.free()

    @staticmethod
    def _elsewhere(windows, foreground: int) -> bool:
        """A full-monitor window that is not in front (on another monitor, or behind)."""
        return any(hwnd != foreground and full_monitor(windows, hwnd) for hwnd in windows.top_level())

    def _safe(self, part: str, call, default):
        try:
            return call()
        except Exception:  # noqa: BLE001 - not knowing means "no", as before
            if part not in self._failed:
                self._failed.add(part)
                logger.info("Game detection: the %s check failed and counts as no", part, exc_info=True)
            return default


def detector():
    """A `GameDetector` on Windows; elsewhere a function that always says None."""
    if sys.platform != "win32":
        return lambda: None
    return GameDetector()
