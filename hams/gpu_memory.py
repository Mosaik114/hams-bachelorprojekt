"""How much graphics memory this process uses, as Windows counts it (DXGI `QueryVideoMemoryInfo`).

NVML and `nvidia-smi` give no per-process figures under Windows (WDDM); DXGI does: `CurrentUsage` of the local
segment group is what this process holds on the adapter, CUDA allocations included. Only for the log: it shows
whether parking the model really gives the memory back. Every failure is quiet (None), on Linux it is always None.

Plain ctypes and COM by vtable index, so no extra package; nothing here creates a device.
"""

from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger("hams")

_MIB = 1024 * 1024


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _Guid:
    parts = text.split("-")
    tail = bytes.fromhex(parts[3] + parts[4])
    return _Guid(int(parts[0], 16), int(parts[1], 16), int(parts[2], 16), (ctypes.c_ubyte * 8)(*tail))


#: IIDs from dxgi.h / dxgi1_4.h.
IID_IDXGI_FACTORY1 = "770aae78-f26f-4dba-a829-253c83d1b387"
IID_IDXGI_ADAPTER3 = "645967a4-1392-4310-a798-8053ce3e93fd"

#: vtable slots: IUnknown 0-2, IDXGIObject 3-6, IDXGIFactory 7-11, IDXGIFactory1 12-13.
_FACTORY_ENUM_ADAPTERS1 = 12
#: IDXGIAdapter 7-9, IDXGIAdapter1 10 (GetDesc1), IDXGIAdapter2 11, IDXGIAdapter3 12-17.
_ADAPTER_GET_DESC1 = 10
_ADAPTER3_QUERY_VIDEO_MEMORY_INFO = 14
_QUERY_INTERFACE = 0
_RELEASE = 2

DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_MEMORY_SEGMENT_GROUP_LOCAL = 0
DXGI_ADAPTER_FLAG_SOFTWARE = 2


class _AdapterDesc1(ctypes.Structure):
    _fields_ = [
        ("description", ctypes.c_wchar * 128),
        ("vendor_id", ctypes.c_uint),
        ("device_id", ctypes.c_uint),
        ("subsys_id", ctypes.c_uint),
        ("revision", ctypes.c_uint),
        ("dedicated_video_memory", ctypes.c_size_t),
        ("dedicated_system_memory", ctypes.c_size_t),
        ("shared_system_memory", ctypes.c_size_t),
        ("luid_low", ctypes.c_uint32),
        ("luid_high", ctypes.c_int32),
        ("flags", ctypes.c_uint),
    ]


class _VideoMemoryInfo(ctypes.Structure):
    _fields_ = [
        ("budget", ctypes.c_uint64),
        ("current_usage", ctypes.c_uint64),
        ("available_for_reservation", ctypes.c_uint64),
        ("current_reservation", ctypes.c_uint64),
    ]


def _method(pointer: ctypes.c_void_p, slot: int, *argtypes):
    """The COM method in `slot` of the object `pointer`, callable with the object first."""
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return prototype(vtable[slot])


def _release(pointer: ctypes.c_void_p) -> None:
    if pointer:
        _method(pointer, _RELEASE)(pointer)


def _adapter_usage(adapter: ctypes.c_void_p) -> tuple[str, int, int] | None:
    """(name, dedicated memory, this process' local usage) of one hardware adapter, else None."""
    desc = _AdapterDesc1()
    if _method(adapter, _ADAPTER_GET_DESC1, ctypes.POINTER(_AdapterDesc1))(adapter, ctypes.byref(desc)) < 0:
        return None
    if desc.flags & DXGI_ADAPTER_FLAG_SOFTWARE:
        return None
    adapter3 = ctypes.c_void_p()
    query = _method(adapter, _QUERY_INTERFACE, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p))
    if query(adapter, ctypes.byref(_guid(IID_IDXGI_ADAPTER3)), ctypes.byref(adapter3)) < 0:
        return None  # before Windows 10
    try:
        info = _VideoMemoryInfo()
        call = _method(
            adapter3, _ADAPTER3_QUERY_VIDEO_MEMORY_INFO, ctypes.c_uint, ctypes.c_int, ctypes.POINTER(_VideoMemoryInfo)
        )
        if call(adapter3, 0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, ctypes.byref(info)) < 0:
            return None
        return desc.description, int(desc.dedicated_video_memory), int(info.current_usage)
    finally:
        _release(adapter3)


def adapters() -> list[tuple[str, int, int]]:
    """(name, dedicated memory in bytes, this process' usage in bytes) per hardware adapter; [] if unknown."""
    if sys.platform != "win32":
        return []
    try:
        dxgi = ctypes.WinDLL("dxgi")
        create = dxgi.CreateDXGIFactory1
        create.argtypes = [ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_long
        factory = ctypes.c_void_p()
        if create(ctypes.byref(_guid(IID_IDXGI_FACTORY1)), ctypes.byref(factory)) < 0:
            return []
    except (OSError, AttributeError):
        return []
    found = []
    try:
        enum = _method(factory, _FACTORY_ENUM_ADAPTERS1, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        for index in range(16):
            adapter = ctypes.c_void_p()
            if enum(factory, index, ctypes.byref(adapter)) < 0:  # DXGI_ERROR_NOT_FOUND ends the list
                break
            try:
                usage = _adapter_usage(adapter)
            finally:
                _release(adapter)
            if usage is not None:
                found.append(usage)
    except OSError:
        logger.debug("DXGI query failed", exc_info=True)
    finally:
        _release(factory)
    return found


def process_usage_mib() -> int | None:
    """This process' graphics memory on the adapter with the most dedicated memory, in MiB; None if unknown."""
    try:
        found = adapters()
    except Exception:  # noqa: BLE001 - only a figure for the log
        logger.debug("Graphics memory could not be measured", exc_info=True)
        return None
    if not found:
        return None
    _name, _dedicated, usage = max(found, key=lambda entry: entry[1])
    return round(usage / _MIB)


def describe() -> str:
    """For the log: "1834 MiB" or "unknown"."""
    usage = process_usage_mib()
    return "unknown" if usage is None else f"{usage} MiB"
