"""Linux: ein zweiter Start holt das Fenster der laufenden Instanz per SIGUSR1 nach vorn."""

import os
import signal
import sys

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux-Sperrdatei und Signale")


@pytest.fixture
def linux():
    from hams import linux as module

    return module


def _fake_proc(monkeypatch, tmp_path, cmdline: bytes) -> None:
    real_read = type(tmp_path).read_bytes

    def read_bytes(self):
        if str(self) == "/proc/4242/cmdline":
            return cmdline
        return real_read(self)

    monkeypatch.setattr(type(tmp_path), "read_bytes", read_bytes)


@pytest.fixture
def lock(tmp_path):
    path = tmp_path / "hams.lock"
    path.write_text("4242\n", encoding="utf-8")
    return path


def test_signals_a_hams_process(linux, lock, tmp_path, monkeypatch):
    _fake_proc(monkeypatch, tmp_path, b"/usr/bin/python3\x00-m\x00hams\x00")
    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert linux.request_show(lock)
    assert sent == [(4242, signal.SIGUSR1)]


def test_a_foreign_process_gets_no_signal(linux, lock, tmp_path, monkeypatch):
    _fake_proc(monkeypatch, tmp_path, b"/usr/bin/bash\x00")
    monkeypatch.setattr(os, "kill", lambda *_a: pytest.fail("kein Signal an fremde Prozesse"))
    assert not linux.request_show(lock)


def test_a_vanished_process_is_not_an_error(linux, lock, tmp_path, monkeypatch):
    _fake_proc(monkeypatch, tmp_path, b"hams\x00")

    def gone(*_args):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", gone)
    assert not linux.request_show(lock)


def test_without_a_lock_file_nothing_happens(linux, tmp_path):
    assert not linux.request_show(tmp_path / "fehlt.lock")
    (tmp_path / "leer.lock").write_text("", encoding="utf-8")
    assert not linux.request_show(tmp_path / "leer.lock")
