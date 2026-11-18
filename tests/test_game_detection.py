"""Telling whether a game runs (`game_detection`): window geometry on every monitor and tight graphics memory.

Windows and the counters are replaced by fakes: two monitors side by side (2560 x 1440 each, the second one to the
right), windows with their client area, styles and state, and a graphics card whose usage the test sets. The real
calls are checked at the end against this PC, without a window being shown.
"""

import ctypes
import sys
from types import SimpleNamespace

import pytest

from hams import game_detection as gd
from hams import gpu_memory
from hams.gpu_memory import Adapter, luid_name

GIB = 1024**3
MIB = 1024**2
PRIMARY = (0, 0, 2560, 1440)
SECOND = (2560, 0, 5120, 1440)


def window(client, monitor=PRIMARY, **state):
    values = {"visible": True, "minimised": False, "ex_style": 0x100, "cls": "UnityWndClass", "own": False}
    values.update({"cloaked": False, "client": client, "monitor": monitor})
    values.update(state)
    return SimpleNamespace(**values)


class FakeWindows:
    """`Win32Windows` with a dictionary of windows (top of the z-order first)."""

    def __init__(self, windows=None, foreground=0):
        self.windows = dict(windows or {})
        self.foreground_hwnd = foreground
        self.walks = 0

    def foreground(self):
        return self.foreground_hwnd

    def top_level(self):
        self.walks += 1
        return list(self.windows)

    def visible(self, hwnd):
        return self.windows[hwnd].visible

    def minimised(self, hwnd):
        return self.windows[hwnd].minimised

    def ex_style(self, hwnd):
        return self.windows[hwnd].ex_style

    def class_name(self, hwnd):
        return self.windows[hwnd].cls

    def own(self, hwnd):
        return self.windows[hwnd].own

    def cloaked(self, hwnd):
        return self.windows[hwnd].cloaked

    def client_area(self, hwnd):
        return self.windows[hwnd].client

    def monitor_area(self, hwnd):
        return self.windows[hwnd].monitor


class FakeMemory:
    def __init__(self, free=None):
        self.value = free

    def free(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


#: A chat window on the primary monitor (where the user dictates) with a title bar: not full screen.
CHAT = window((8, 31, 1900, 1380))
#: A borderless game on the second monitor.
GAME_2 = window(SECOND, SECOND)


def detector(windows=None, free=None, shell_says=False):
    return gd.GameDetector(notifications=lambda: shell_says, windows=windows or FakeWindows(), memory=FakeMemory(free))


class TestCovers:
    @pytest.mark.parametrize(
        ("area", "expected"),
        [
            (PRIMARY, True),
            ((-8, -8, 2568, 1448), True),  # a little larger, as some games make it
            ((0, 0, 2560, 1392), False),  # the taskbar stays visible
            ((0, 23, 2560, 1440), False),  # a title bar
            (None, False),
        ],
    )
    def test_the_whole_monitor(self, area, expected):
        assert gd.covers(area, PRIMARY) is expected

    def test_no_monitor(self):
        assert not gd.covers(PRIMARY, None)
        assert not gd.covers(PRIMARY, (0, 0, 0, 0))


class TestFullMonitorWindows:
    def test_a_borderless_game_on_the_primary_monitor(self):
        assert gd.full_monitor(FakeWindows({1: window(PRIMARY)}), 1)

    def test_a_borderless_game_on_the_second_monitor(self):
        assert gd.full_monitor(FakeWindows({1: GAME_2}), 1)

    def test_a_maximised_window_with_a_title_bar_is_none(self):
        assert not gd.full_monitor(FakeWindows({1: window((0, 23, 2560, 1392))}), 1)

    def test_a_window_on_the_primary_monitor_does_not_cover_the_second(self):
        assert not gd.full_monitor(FakeWindows({1: window(PRIMARY, SECOND)}), 1)

    @pytest.mark.parametrize(
        "ex_style",
        [
            pytest.param(0x080800A8, id="NVIDIA overlay: layered, transparent, tool window, no-activate, topmost"),
            pytest.param(0x00080020, id="click-through"),
            pytest.param(0x00000088, id="tool window"),
            pytest.param(0x08000000, id="never active"),
        ],
    )
    def test_overlays_are_left_out(self, ex_style):
        assert not gd.full_monitor(FakeWindows({1: window(PRIMARY, ex_style=ex_style)}), 1)

    def test_a_layered_game_window_still_counts(self):
        assert gd.full_monitor(FakeWindows({1: window(PRIMARY, ex_style=0x00080000)}), 1)

    @pytest.mark.parametrize("cls", sorted(gd.SHELL_CLASSES))
    def test_the_desktop_and_the_taskbars_are_left_out(self, cls):
        assert not gd.full_monitor(FakeWindows({1: window(PRIMARY, cls=cls)}), 1)

    @pytest.mark.parametrize(
        "state",
        [
            pytest.param({"visible": False}, id="invisible"),
            pytest.param({"minimised": True}, id="minimised"),
            pytest.param({"cloaked": True}, id="cloaked (another virtual desktop)"),
            pytest.param({"own": True}, id="one of Hams' own windows"),
            pytest.param({"monitor": None}, id="on no monitor"),
            pytest.param({"client": None}, id="no client area"),
        ],
    )
    def test_left_out(self, state):
        state = {"client": PRIMARY, **state}
        assert not gd.full_monitor(FakeWindows({1: window(**state)}), 1)

    def test_no_window(self):
        assert not gd.full_monitor(FakeWindows(), 0)


class TestTheRule:
    def test_nothing(self):
        assert detector(FakeWindows({1: CHAT}, foreground=1), free=6 * GIB)() is None

    def test_the_shell_says_full_screen(self):
        assert detector(shell_says=True)() == gd.SHELL

    def test_a_full_monitor_window_in_front_counts_at_once(self):
        windows = FakeWindows({1: GAME_2, 2: CHAT}, foreground=1)
        assert detector(windows, free=8 * GIB)() == gd.IN_FRONT

    def test_in_front_also_without_the_memory_counter(self):
        assert detector(FakeWindows({1: GAME_2}, foreground=1), free=None)() == gd.IN_FRONT

    def test_a_video_on_the_second_monitor_with_plenty_of_memory_does_not_count(self):
        windows = FakeWindows({2: CHAT, 1: GAME_2}, foreground=2)
        found = detector(windows, free=6 * GIB)
        assert found() is None
        assert windows.walks == 0  # all windows are only walked when memory gets tight

    def test_a_game_on_the_second_monitor_with_memory_getting_tight(self):
        windows = FakeWindows({2: CHAT, 1: GAME_2}, foreground=2)
        assert detector(windows, free=2 * GIB)() == gd.ELSEWHERE

    def test_memory_getting_tight_alone_does_not_count(self):
        assert detector(FakeWindows({2: CHAT}, foreground=2), free=2 * GIB)() is None

    def test_tight_memory_counts_alone(self):
        assert detector(FakeWindows({2: CHAT}, foreground=2), free=1 * GIB)() == gd.MEMORY

    @pytest.mark.parametrize(
        ("free", "alone", "with_a_window_elsewhere"),
        [
            (gd.TIGHT_FREE - 1, gd.MEMORY, gd.MEMORY),
            (gd.TIGHT_FREE, None, gd.ELSEWHERE),
            (gd.GETTING_TIGHT_FREE - 1, None, gd.ELSEWHERE),
            (gd.GETTING_TIGHT_FREE, None, None),
            (-1 * GIB, gd.MEMORY, gd.MEMORY),  # more wanted than the card has
        ],
    )
    def test_the_thresholds(self, free, alone, with_a_window_elsewhere):
        assert detector(FakeWindows({2: CHAT}, foreground=2), free=free)() == alone
        windows = FakeWindows({2: CHAT, 1: GAME_2}, foreground=2)
        assert detector(windows, free=free)() == with_a_window_elsewhere

    def test_a_game_behind_the_window_in_front_counts_like_one_elsewhere(self):
        windows = FakeWindows({2: CHAT, 1: window(PRIMARY)}, foreground=2)
        assert detector(windows, free=2 * GIB)() == gd.ELSEWHERE

    def test_overlays_elsewhere_do_not_count(self):
        overlay = window(SECOND, SECOND, ex_style=0x080800A8, cls="CEF-OSC-WIDGET")
        windows = FakeWindows({2: CHAT, 1: overlay, 3: window((0, 0, 5120, 1440), cls="Progman")}, foreground=2)
        assert detector(windows, free=2 * GIB)() is None

    def test_a_wobbling_game_does_not_switch_the_signal(self):
        memory = FakeMemory(gd.TIGHT_FREE - 1)
        found = gd.GameDetector(notifications=lambda: False, windows=FakeWindows(), memory=memory)
        assert found() == gd.MEMORY
        memory.value = gd.TIGHT_FREE + gd.HYSTERESIS - 1
        assert found() == gd.MEMORY
        memory.value = gd.TIGHT_FREE + gd.HYSTERESIS
        assert found() is None
        memory.value = gd.TIGHT_FREE + 1
        assert found() is None  # not tight again until under the threshold itself

    def test_a_game_elsewhere_keeps_its_signal_while_memory_wobbles(self):
        windows = FakeWindows({2: CHAT, 1: GAME_2}, foreground=2)
        memory = FakeMemory(gd.GETTING_TIGHT_FREE - 1)
        found = gd.GameDetector(notifications=lambda: False, windows=windows, memory=memory)
        assert found() == gd.ELSEWHERE
        memory.value = gd.GETTING_TIGHT_FREE + gd.HYSTERESIS - 1
        assert found() == gd.ELSEWHERE
        memory.value = gd.GETTING_TIGHT_FREE + gd.HYSTERESIS
        assert found() is None

    def test_thresholds_are_in_order(self):
        assert 0 < gd.TIGHT_FREE < gd.TIGHT_FREE + gd.HYSTERESIS < gd.GETTING_TIGHT_FREE


class TestFailures:
    """Any failure counts as "no", as before; it is logged once and the other signals still work."""

    def test_the_shell_fails(self, caplog):
        caplog.set_level("INFO", logger="hams")

        def broken():
            raise OSError("no shell32")

        windows = FakeWindows({1: GAME_2}, foreground=1)
        found = gd.GameDetector(notifications=broken, windows=windows, memory=FakeMemory())
        assert found() == gd.IN_FRONT
        assert found() == gd.IN_FRONT
        assert caplog.text.count("the shell check failed") == 1

    def test_the_windows_fail(self, caplog):
        caplog.set_level("INFO", logger="hams")
        windows = FakeWindows()
        windows.foreground = lambda: (_ for _ in ()).throw(OSError("user32"))
        found = detector(windows, free=1 * GIB)
        assert found() == gd.MEMORY
        assert detector(windows, free=2 * GIB)() is None
        assert found() == gd.MEMORY
        assert caplog.text.count("the windows check failed") == 2  # once per detector

    def test_the_memory_counter_fails(self, caplog):
        caplog.set_level("INFO", logger="hams")
        windows = FakeWindows({2: CHAT, 1: GAME_2}, foreground=2)
        found = detector(windows, free=OSError("PdhCollectQueryData failed"))
        assert found() is None
        assert found() is None
        assert caplog.text.count("the memory check failed") == 1

    def test_everything_fails(self):
        def broken():
            raise RuntimeError("no")

        windows = SimpleNamespace(foreground=broken, top_level=broken)
        found = gd.GameDetector(notifications=broken, windows=windows, memory=FakeMemory(RuntimeError("no")))
        assert found() is None

    def test_a_change_is_logged_once(self, caplog):
        caplog.set_level("INFO", logger="hams")
        memory = FakeMemory(6 * GIB)
        found = gd.GameDetector(notifications=lambda: False, windows=FakeWindows(), memory=memory)
        found()
        memory.value = 1 * GIB
        for _ in range(5):
            found()
        memory.value = 6 * GIB
        found()
        found()
        assert caplog.text.count("Game signal: graphics memory tight (1024 MiB would stay free)") == 1
        assert caplog.text.count("Game signal: none") == 1


# ---------------------------------------------------------------------- graphics memory

DGPU = luid_name(0, 0x14A0C)
IGPU = luid_name(0, 0x17FC6)


class FakeCounter:
    def __init__(self, values):
        self.values = values

    def read(self):
        if isinstance(self.values, Exception):
            raise self.values
        return dict(self.values)


def memory(card_usage, own, *, luid=DGPU, counter=None, adapters=None):
    """A `GraphicsMemory` on a 12 GB card next to a small integrated one, the counter opened inline."""
    state = SimpleNamespace(card_usage=card_usage, own=own)
    values = {f"{DGPU}_phys_0": card_usage, f"{IGPU}_phys_0": 10 * MIB}

    def list_adapters():
        if adapters is not None:
            return adapters
        return [
            Adapter("NVIDIA GeForce RTX 5070", 11943 * MIB, state.own, DGPU),
            Adapter("AMD Radeon(TM) Graphics", 486 * MIB, 0, IGPU),
        ]

    fake = FakeCounter(values)
    found = gd.GraphicsMemory(
        counter=counter or (lambda: fake),
        adapters=list_adapters,
        luid=lambda: luid,
        spawn=lambda target: target(),
    )
    return found, state, fake


class TestGraphicsMemory:
    def test_free_with_the_model_loaded(self):
        found, _state, _fake = memory(card_usage=6000 * MIB, own=2300 * MIB)
        assert found.free() == 11943 * MIB - 6000 * MIB

    def test_parking_does_not_change_the_answer(self):
        """Hams' 2.3 GB count as needed while parked, so the signal stays and the next one is not churned."""
        found, state, fake = memory(card_usage=9000 * MIB, own=2300 * MIB)
        loaded = found.free()
        state.own = 150 * MIB
        fake.values[f"{DGPU}_phys_0"] = 9000 * MIB - 2150 * MIB
        assert found.free() == loaded

    def test_what_others_use_is_what_counts(self):
        found, state, fake = memory(card_usage=4000 * MIB, own=2300 * MIB)
        before = found.free()
        fake.values[f"{DGPU}_phys_0"] = 11000 * MIB  # a game takes 7 GB
        assert found.free() == before - 7000 * MIB
        assert found.free() < gd.TIGHT_FREE

    def test_the_cuda_card_is_found_by_its_luid(self):
        adapters = [
            Adapter("NVIDIA A", 8192 * MIB, 0, luid_name(0, 0x1)),
            Adapter("NVIDIA B", 24576 * MIB, 2300 * MIB, luid_name(0, 0x2)),
        ]
        counter = FakeCounter({f"{luid_name(0, 0x1)}_phys_0": 7000 * MIB, f"{luid_name(0, 0x2)}_phys_0": 5000 * MIB})
        found = gd.GraphicsMemory(
            counter=lambda: counter, adapters=lambda: adapters, luid=lambda: luid_name(0, 0x1), spawn=lambda t: t()
        )
        assert found.free() == 8192 * MIB - 7000 * MIB

    def test_without_cuda_the_card_hams_holds_memory_on(self):
        found, _state, _fake = memory(card_usage=6000 * MIB, own=2300 * MIB, luid=None)
        assert found.free() == 11943 * MIB - 6000 * MIB

    def test_a_luid_that_cannot_be_asked_falls_back(self):
        def broken():
            raise AttributeError("cuDeviceGetLuid")

        found, _state, fake = memory(card_usage=6000 * MIB, own=2300 * MIB)
        found._luid = broken
        assert found.free() == 11943 * MIB - 6000 * MIB

    def test_on_the_cpu_there_is_no_answer(self):
        found, _state, _fake = memory(card_usage=6000 * MIB, own=0, luid=None)
        assert found.free() is None

    def test_linked_adapters_are_added_up(self):
        found, _state, fake = memory(card_usage=3000 * MIB, own=1000 * MIB)
        fake.values[f"{DGPU}_phys_1"] = 2000 * MIB
        assert found.free() == 11943 * MIB - 5000 * MIB

    def test_the_counter_names_the_luid_in_any_case(self):
        found, _state, fake = memory(card_usage=0, own=1000 * MIB)
        fake.values = {f"{DGPU.lower()}_phys_0": 3000 * MIB}
        assert found.free() == 11943 * MIB - 3000 * MIB

    def test_no_counter_for_the_card(self, caplog):
        caplog.set_level("INFO", logger="hams")
        found, _state, fake = memory(card_usage=0, own=1000 * MIB)
        fake.values = {f"{IGPU}_phys_0": 1}
        assert found.free() is None
        assert found.free() is None
        assert caplog.text.count("No graphics memory counter for") == 1

    def test_a_cuda_card_dxgi_does_not_know(self, caplog):
        caplog.set_level("INFO", logger="hams")
        found, _state, _fake = memory(card_usage=6000 * MIB, own=2300 * MIB, luid=luid_name(0, 0x99))
        assert found.free() == 11943 * MIB - 6000 * MIB  # the card Hams holds memory on
        found.free()
        assert caplog.text.count("is not among the graphics adapters") == 1

    def test_the_counter_cannot_be_opened(self, caplog):
        caplog.set_level("INFO", logger="hams")
        opened = []

        def broken():
            opened.append(1)
            raise OSError("PdhAddCounter failed: 0xC0000BB8")

        found, _state, _fake = memory(card_usage=0, own=1000 * MIB, counter=broken)
        assert found.free() is None
        assert found.free() is None
        assert opened == [1]  # tried once
        assert caplog.text.count("Graphics memory counter not available") == 1

    def test_nothing_until_the_counter_is_open(self):
        started = []
        found = gd.GraphicsMemory(counter=FakeCounter, adapters=list, luid=lambda: None, spawn=started.append)
        assert found.free() is None
        assert found.free() is None
        assert len(started) == 1  # one opener, on its own thread

    def test_the_arithmetic(self):
        assert gd.free_if_loaded(12 * GIB, card_usage=4 * GIB, own=2 * GIB, own_loaded=2 * GIB) == 8 * GIB
        assert gd.free_if_loaded(12 * GIB, card_usage=2 * GIB, own=0, own_loaded=2 * GIB) == 8 * GIB
        assert gd.free_if_loaded(12 * GIB, card_usage=1 * GIB, own=2 * GIB, own_loaded=0) == 10 * GIB  # no minus


def test_the_luid_spelling():
    assert luid_name(0, 0x14A0C) == "luid_0x00000000_0x00014A0C"
    assert luid_name(-1, 0xFFFFFFFF) == "luid_0xFFFFFFFF_0xFFFFFFFF"


class TestOnLinux:
    def test_always_none(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert gd.detector()() is None


# ---------------------------------------------------------------------- the real calls (Windows)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 and PDH")


@windows_only
class TestTheRealCalls:
    def test_the_detector_answers(self):
        assert gd.detector()() in (None, gd.SHELL, gd.IN_FRONT, gd.MEMORY, gd.ELSEWHERE)

    def test_the_window_queries_on_a_hidden_window_that_covers_the_primary_monitor(self):
        """A popup the size of the primary monitor, never shown: its geometry covers the monitor, but it is
        invisible and Hams' own, so it does not count."""
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        user32.CreateWindowExW.argtypes += [ctypes.c_int] * 4 + [ctypes.c_void_p] * 4
        user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        windows = gd.Win32Windows()
        point_monitor = windows.monitor_area(windows.foreground()) or None
        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        hwnd = user32.CreateWindowExW(0, "STATIC", "", 0x80000000, 0, 0, width, height, None, None, None, None)
        assert hwnd
        try:
            monitor = windows.monitor_area(hwnd)
            assert monitor is not None and monitor[:2] == (0, 0)
            assert gd.covers(windows.client_area(hwnd), monitor)
            assert windows.own(hwnd) and not windows.visible(hwnd) and not windows.minimised(hwnd)
            assert not windows.cloaked(hwnd)
            assert windows.class_name(hwnd).lower() == "static"
            assert windows.ex_style(hwnd) & gd.OVERLAY_STYLES == 0
            assert not gd.full_monitor(windows, hwnd)
            assert hwnd in windows.top_level()
        finally:
            user32.DestroyWindow(hwnd)
        assert point_monitor is None or len(point_monitor) == 4

    def test_the_counter(self):
        try:
            counter = gd.PdhCounter()
        except OSError:
            pytest.skip("no GPU performance counters here")
        values = counter.read()
        assert all(name.startswith("luid_0x") and value >= 0 for name, value in values.items())

    def test_the_adapters_carry_their_luid(self):
        found = gpu_memory.adapter_details()
        assert all(adapter.luid.startswith("luid_0x") and len(adapter.luid) == 26 for adapter in found)
        assert gpu_memory.adapters() == [(adapter.name, adapter.dedicated, adapter.usage) for adapter in found]

    def test_the_cuda_luid_only_with_cuda_loaded(self):
        luid = gd.cuda_luid()
        assert luid is None or luid.startswith("luid_0x")
