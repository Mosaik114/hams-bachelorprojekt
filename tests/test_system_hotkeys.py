"""Zerlegen der Kurzbefehle fuer `RegisterHotKey`.

Die Anmeldung selbst wird hier nicht ausprobiert: sie belegte die Kombination
fuer den ganzen Rechner, auch fuer ein gerade laufendes Hams.
"""

import queue
import sys
import threading
from types import SimpleNamespace

import pytest

from hams import system_hotkeys
from hams.system_hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    MOD_WIN,
    PROBE_ID,
    SystemHotkeys,
    parse_combo,
)


class TestParseCombo:
    def test_the_two_default_shortcuts(self):
        assert parse_combo("ctrl+shift+space") == (MOD_CONTROL | MOD_SHIFT, 0x20)
        assert parse_combo("ctrl+shift+alt+space") == (MOD_CONTROL | MOD_SHIFT | MOD_ALT, 0x20)

    def test_letters_digits_and_function_keys(self):
        assert parse_combo("win+h") == (MOD_WIN, ord("H"))
        assert parse_combo("alt+1") == (MOD_ALT, ord("1"))
        assert parse_combo("ctrl+F9") == (MOD_CONTROL, 0x78)

    def test_spaces_and_case_do_not_matter(self):
        assert parse_combo(" Ctrl + Shift + Space ") == (MOD_CONTROL | MOD_SHIFT, 0x20)

    def test_anything_ambiguous_is_left_to_the_library(self):
        for combo in ("ctrl+shift", "ctrl+a+b", "ctrl+ä", "ctrl+", "ctrl+shift+space, a"):
            assert parse_combo(combo) is None


class FakeUser32:
    """`RegisterHotKey` ohne echte Anmeldung; `taken` gilt als von einem anderen Programm belegt."""

    def __init__(self, taken=()):
        self.taken = set(taken)
        self.calls = []

    def RegisterHotKey(self, hwnd, hid, mods, vk):  # noqa: N802 - Name der Windows-Funktion
        self.calls.append(("register", hid, mods, vk))
        return (mods & ~MOD_NOREPEAT, vk) not in self.taken

    def UnregisterHotKey(self, hwnd, hid):  # noqa: N802
        self.calls.append(("unregister", hid))
        return True


class TestAvailable:
    @pytest.fixture
    def user32(self, monkeypatch):
        fake = FakeUser32(taken={(MOD_CONTROL | MOD_ALT, ord("D"))})
        monkeypatch.setattr(system_hotkeys, "ctypes", SimpleNamespace(windll=SimpleNamespace(user32=fake)))
        return fake

    def test_a_free_combination_is_registered_and_released_at_once(self, user32):
        assert SystemHotkeys.available("ctrl+alt+p") is True
        assert user32.calls == [
            ("register", PROBE_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("P")),
            ("unregister", PROBE_ID),
        ]

    def test_a_taken_combination_is_reported_and_nothing_is_released(self, user32):
        assert SystemHotkeys.available("ctrl+alt+d") is False
        assert [call[0] for call in user32.calls] == ["register"]

    def test_an_unparsable_combination_is_never_available(self, user32):
        assert SystemHotkeys.available("ctrl+ä") is False
        assert user32.calls == []


# ------------------------------------------------------------- thread lifecycle


class FakeWinApi:
    """The hotkey thread's Win32 calls without Windows: a message queue per thread, nothing registered for real."""

    def __init__(self, taken=()):
        self.taken = set(taken)
        self.messages: dict[int, queue.SimpleQueue] = {}
        self.registered: dict[int, int] = {}  # hid -> registering thread
        self.unregistered: list[tuple[int, int]] = []  # (hid, calling thread)
        self.quits = 0

    def current_thread_id(self):
        ident = threading.get_ident()
        self.messages.setdefault(ident, queue.SimpleQueue())
        return ident

    def register(self, hid, mods, vk):
        if (mods & ~MOD_NOREPEAT, vk) in self.taken or hid in self.registered:
            return False
        self.registered[hid] = threading.get_ident()
        return True

    def unregister(self, hid):
        self.unregistered.append((hid, threading.get_ident()))
        return self.registered.pop(hid, None) is not None

    def next_hotkey(self):
        message = self.messages[threading.get_ident()].get()
        return None if message == "quit" else message

    def post_quit(self, thread_id):
        self.quits += 1
        self.messages[thread_id].put("quit")
        return True

    def press(self, hid):
        thread = self.registered[hid]
        self.messages[thread].put(hid)


class TestThreadLifecycle:
    def test_a_pressed_shortcut_calls_its_handler(self):
        api = FakeWinApi()
        pressed = threading.Event()
        hotkeys = SystemHotkeys(api)
        assert hotkeys.register([("ctrl+shift+space", pressed.set)]) == []
        api.press(1)
        assert pressed.wait(2)
        hotkeys.stop()

    def test_stop_unregisters_on_the_registering_thread(self):
        api = FakeWinApi()
        hotkeys = SystemHotkeys(api)
        hotkeys.register([("ctrl+shift+space", lambda: None), ("ctrl+alt+space", lambda: None)])
        owners = set(api.registered.values())
        hotkeys.stop()
        assert api.registered == {}
        assert {thread for _hid, thread in api.unregistered} == owners
        assert not any(t.name == "system-hotkeys" and t.is_alive() for t in threading.enumerate())

    def test_a_taken_combination_is_reported(self):
        api = FakeWinApi(taken={(MOD_CONTROL | MOD_ALT, ord("D"))})
        hotkeys = SystemHotkeys(api)
        assert hotkeys.register([("ctrl+alt+d", lambda: None), ("ctrl+alt+e", lambda: None)]) == ["ctrl+alt+d"]
        hotkeys.stop()

    def test_registering_again_replaces_the_old_thread(self):
        """Regression: re-registering while the old thread still held the ids fell back to the keyboard hook."""
        api = FakeWinApi()
        hotkeys = SystemHotkeys(api)
        hotkeys.register([("ctrl+shift+space", lambda: None)])
        assert hotkeys.register([("ctrl+shift+space", lambda: None)]) == []
        hotkeys.stop()
        assert api.registered == {}

    def test_a_failing_handler_does_not_end_the_loop(self):
        api = FakeWinApi()
        second = threading.Event()

        def broken():
            raise RuntimeError("kaputt")

        hotkeys = SystemHotkeys(api)
        hotkeys.register([("ctrl+shift+space", broken), ("ctrl+alt+space", second.set)])
        api.press(1)
        api.press(2)
        assert second.wait(2)
        hotkeys.stop()

    def test_stop_gives_up_after_the_timeout_and_says_so(self, caplog):
        api = FakeWinApi()
        release = threading.Event()
        entered = threading.Event()

        def stuck():
            entered.set()
            release.wait(5)

        hotkeys = SystemHotkeys(api)
        hotkeys.STOP_TIMEOUT = 0.3
        hotkeys.register([("ctrl+shift+space", stuck)])
        api.press(1)
        assert entered.wait(2)
        hotkeys.stop()
        assert "did not stop" in caplog.text
        release.set()

    def test_stop_without_register_is_harmless(self):
        SystemHotkeys(FakeWinApi()).stop()

    def test_the_app_handler_never_blocks_the_hotkey_thread(self, root):
        """Regression: the handler called root.after, waited for the Tk thread - which was waiting in stop()."""
        from hams.ui_dispatch import UiDispatcher

        api = FakeWinApi()
        dispatcher = UiDispatcher(root)
        ran = []
        handled = threading.Event()

        def handler():
            dispatcher.call(lambda: ran.append("toggle"))
            handled.set()

        hotkeys = SystemHotkeys(api)
        hotkeys.register([("ctrl+shift+space", handler)])
        api.press(1)
        assert handled.wait(2), "the handler blocked on Tk"
        hotkeys.stop()  # on the Tk thread, without servicing Tk in between
        assert api.registered == {}
        dispatcher.drain()
        dispatcher.close()
        assert ran == ["toggle"]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 message queue")
def test_the_real_message_loop_ends_on_quit():
    """The real Win32 wrapper, without registering any shortcut: WM_QUIT ends the wait."""
    result = {}

    def loop():
        api = system_hotkeys._WinApi()
        result["id"] = api.current_thread_id()
        assert api.post_quit(result["id"])
        result["hotkey"] = api.next_hotkey()

    thread = threading.Thread(target=loop)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert result == {"id": result["id"], "hotkey": None}
    assert result["id"] > 0
