"""Kurzbefehle über das GlobalShortcuts-Portal – mit nachgebautem Sitzungsbus (kein echtes Portal, kein KDE)."""

import threading

import pytest

gi = pytest.importorskip("gi")
from gi.repository import Gio, GLib  # noqa: E402

from hams import portal_hotkeys  # noqa: E402
from hams.portal_hotkeys import PortalHotkeys  # noqa: E402


class Bus:
    """Antwortet wie das Portal: jede Anfrage bekommt sofort ihr Response-Signal."""

    def __init__(self, sitzung_code=0, bind_code=0, register_error=False):
        self.codes = {"CreateSession": sitzung_code, "BindShortcuts": bind_code}
        self.register_error = register_error
        self.abos: dict[int, tuple] = {}
        self.calls: list[tuple] = []

    def get_unique_name(self):
        return ":1.7"

    def call_sync(self, *a):
        self.calls.append(("sync", a[3]))
        if self.register_error:
            raise GLib.Error("schon angemeldet")

    def signal_subscribe(self, sender, iface, signal, path, arg0, flags, rueckruf):
        nr = len(self.abos) + 1
        self.abos[nr] = (signal, path, rueckruf)
        return nr

    def signal_unsubscribe(self, nr):
        self.abos.pop(nr, None)

    def call(self, goal, path, iface, methode, params, *rest):
        self.calls.append((methode, params.unpack()))
        value_list = {"session_handle": GLib.Variant("o", "/s/1")} if methode == "CreateSession" else {
            "shortcuts": GLib.Variant("a(sa{sv})", [("diktat", {"trigger_description": GLib.Variant("s", "Bild↓")})])
        }  # fmt: skip
        for signal, p, rueckruf in list(self.abos.values()):
            if signal == "Response" and p.endswith(params.unpack()[-1]["handle_token"]):
                rueckruf(
                    None, None, None, None, "Response", GLib.Variant("(ua{sv})", (self.codes[methode], value_list))
                )

    def druecken(self, sid):
        for signal, _p, rueckruf in list(self.abos.values()):
            if signal == "Activated":
                rueckruf(None, None, None, None, "Activated", GLib.Variant("(osta{sv})", ("/s/1", sid, 0, {})))


def _starten(monkeypatch, bus, entries):
    monkeypatch.setattr(Gio, "bus_get_sync", lambda *a: bus)
    p = PortalHotkeys("de.mosaik.Hams")
    ready, result = threading.Event(), {"failed": []}
    t = threading.Thread(target=p._run, args=(entries, ready, result), daemon=True)
    t.start()
    assert ready.wait(5)
    return p, t, result


def test_register_bind_and_press(monkeypatch):
    bus = Bus(register_error=True)
    pressed = []
    entries = [("diktat", "Diktat", "page down", lambda: pressed.append("diktat")),
                 ("kaputt", "Unbekannt", "???", lambda: 1 / 0)]  # fmt: skip
    p, t, result = _starten(monkeypatch, bus, entries)
    assert [a[0] for a in bus.calls] == ["sync", "CreateSession", "BindShortcuts"]
    bind = bus.calls[2][1]
    assert bind[0] == "/s/1" and [s[0] for s in bind[1]] == ["diktat", "kaputt"]
    assert "preferred_trigger" in bind[1][0][1] and result["failed"] == ["???"]
    bus.druecken("diktat")
    bus.druecken("kaputt")  # Fehler im Handler: nur protokolliert
    bus.druecken("gibtsnicht")
    assert pressed == ["diktat"]
    p.stop()
    t.join(5)
    assert not t.is_alive()


@pytest.mark.parametrize(("sitzung", "bind"), [(1, 0), (0, 2)])
def test_abgelehnt(monkeypatch, sitzung, bind):
    p, t, result = _starten(monkeypatch, Bus(sitzung, bind), [("diktat", "Diktat", "page down", lambda: None)])
    assert result["failed"] == ["page down"]
    p.stop()
    t.join(5)


def test_tastenschreibweise():
    assert portal_hotkeys.xdg_trigger("ctrl+shift+space") is not None
