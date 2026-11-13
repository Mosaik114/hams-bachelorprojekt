"""The cursor-dot messages are an optional integration, off by default (audit finding H8).

Hams used to post its recording state and level 20 times a second to a window of a separate, private tool, and
looked that window up by title on every message. Now nothing is sent unless `[integrations] cursor_dot = true`,
and the window handle is cached.
"""

import pytest
import tomllib

from hams import config as config_mod
from hams import cursor_dot
from hams.config import CFG


class FakeUser32:
    def __init__(self, hwnd: int = 0x1234) -> None:
        self.hwnd = hwnd
        self.lookups = 0
        self.posts: list = []
        self.alive = True

    def RegisterWindowMessageW(self, name):  # noqa: N802 - Win32 name
        assert name == cursor_dot.MESSAGE_NAME
        return 0xC123

    def FindWindowW(self, _cls, title):  # noqa: N802
        assert title == cursor_dot.WINDOW_TITLE
        self.lookups += 1
        return self.hwnd

    def IsWindow(self, hwnd):  # noqa: N802
        return self.alive and hwnd == self.hwnd

    def PostMessageW(self, hwnd, message, wparam, lparam):  # noqa: N802
        self.posts.append((hwnd, message, wparam, lparam))
        return 1


@pytest.fixture
def user32(monkeypatch):
    fake = FakeUser32()
    receiver = cursor_dot._WindowsReceiver()
    receiver.user32 = fake
    monkeypatch.setattr(cursor_dot, "_WINDOWS", receiver)
    monkeypatch.setattr(cursor_dot.sys, "platform", "win32")
    monkeypatch.setattr(CFG, "integrations_cursor_dot", True)
    return fake


def test_off_by_default():
    template = tomllib.loads(config_mod.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    assert template["integrations"]["cursor_dot"] is False
    assert config_mod.Config().integrations_cursor_dot is False


def test_off_means_nothing_is_sent(user32, monkeypatch):
    monkeypatch.setattr(CFG, "integrations_cursor_dot", False)
    cursor_dot.notify("recording", 0.5)
    assert user32.posts == [] and user32.lookups == 0


def test_state_level_and_pid_are_posted(user32):
    cursor_dot.notify("recording", 1.0)
    cursor_dot.notify("recording_prompt")
    cursor_dot.notify("done")
    (_h, message, first, pid), (_h2, _m, second, _p), (_h3, _m3, third, _p3) = user32.posts
    assert message == 0xC123
    assert first == 1 | (256 << 8)
    assert (second, third) == (2, 0)
    assert pid == cursor_dot.os.getpid()


def test_the_window_is_looked_up_once_while_it_exists(user32):
    for _ in range(20):
        cursor_dot.notify("recording", 0.3)
    assert user32.lookups == 1
    assert len(user32.posts) == 20


def test_a_missing_window_is_looked_for_at_most_every_two_seconds(user32, monkeypatch):
    user32.hwnd = 0
    clock = [100.0]
    monkeypatch.setattr(cursor_dot.time, "monotonic", lambda: clock[0])
    for _ in range(20):
        cursor_dot.notify("recording", 0.3)
    assert user32.lookups == 1 and user32.posts == []
    clock[0] += cursor_dot.LOOKUP_SECONDS
    user32.hwnd = 0x99
    cursor_dot.notify("recording", 0.3)
    assert user32.lookups == 2
    assert user32.posts[-1][0] == 0x99


def test_a_closed_window_is_found_again(user32, monkeypatch):
    cursor_dot.notify("recording")
    user32.alive = False
    clock = [cursor_dot._WINDOWS.next_lookup]
    monkeypatch.setattr(cursor_dot.time, "monotonic", lambda: clock[0])
    user32.alive = True
    user32.hwnd = 0x77
    cursor_dot.notify("recording")
    assert user32.posts[-1][0] == 0x77


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list = []

    def sendto(self, data, address):
        self.sent.append((data.decode(), address))


def test_linux_sends_a_datagram(monkeypatch, tmp_path):
    receiver = cursor_dot._LinuxReceiver()
    receiver.sock = FakeSocket()
    monkeypatch.setattr(cursor_dot, "_LINUX", receiver)
    monkeypatch.setattr(cursor_dot.sys, "platform", "linux")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(CFG, "integrations_cursor_dot", True)
    cursor_dot.notify("recording", 0.5)
    cursor_dot.notify("ready")
    pid = cursor_dot.os.getpid()
    assert receiver.sock.sent == [
        (f"hams 1 0.5 {pid}", str(tmp_path / cursor_dot.SOCKET_NAME)),
        (f"hams 0 -1 {pid}", str(tmp_path / cursor_dot.SOCKET_NAME)),
    ]
