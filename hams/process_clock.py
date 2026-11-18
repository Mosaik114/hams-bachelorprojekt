"""How long this process has been running, for the start lines in the log.

The first log line used to come only after the heavy imports, so the time they took was invisible. Windows tells the
creation time of the process (`GetProcessTimes`), Linux its start in clock ticks after boot (`/proc/self/stat`); where
neither works, the time since this module was imported stands in.
"""

from __future__ import annotations

import os
import sys
import time

#: The fallback: as early as Hams can know anything.
_IMPORTED = time.monotonic()


def _windows() -> float | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    creation, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetSystemTimePreciseAsFileTime.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
    kernel32.GetSystemTimePreciseAsFileTime.restype = None
    if not kernel32.GetProcessTimes(
        kernel32.GetCurrentProcess(),
        ctypes.byref(creation),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return None
    now = wintypes.FILETIME()
    kernel32.GetSystemTimePreciseAsFileTime(ctypes.byref(now))

    def ticks(value: wintypes.FILETIME) -> int:  # 100 ns since 1601
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    return (ticks(now) - ticks(creation)) / 10_000_000


def _linux() -> float | None:
    with open("/proc/self/stat", encoding="ascii") as handle:
        # The command name in brackets may contain spaces; the fields after it are plain numbers.
        fields = handle.read().rsplit(")", 1)[1].split()
    start_ticks = int(fields[19])  # field 22 of the whole line: starttime
    with open("/proc/uptime", encoding="ascii") as handle:
        uptime = float(handle.read().split()[0])
    return uptime - start_ticks / os.sysconf("SC_CLK_TCK")


def seconds_since_start() -> float:
    """Seconds since this process was created; never raises."""
    try:
        measured = _windows() if sys.platform == "win32" else _linux()
    except Exception:  # noqa: BLE001 - only a figure for the log
        measured = None
    if measured is None or measured < 0:
        return time.monotonic() - _IMPORTED
    return measured
