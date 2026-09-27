"""Kurzbefehle unter Wayland ueber das Portal `org.freedesktop.portal.GlobalShortcuts`.

Unter Wayland darf kein Programm die Tastatur abhoeren; RegisterHotKey und der
Hook der `keyboard`-Bibliothek haben hier kein Gegenstueck. Stattdessen meldet
Hams seine Kurzbefehle beim Portal an, KDE traegt sie in seine eigene
Kurzbefehl-Verwaltung ein und meldet jeden Druck mit dem Signal `Activated`.
Beim ersten Anmelden fragt KDE einmal nach; danach stehen die Befehle unter
Systemeinstellungen → Tastatur → Kurzbefehle und lassen sich dort aendern.

Alles laeuft in einem eigenen Thread mit eigenem GLib-Kontext, damit es nicht
mit der GTK-Schleife des Tray-Symbols (pystray) in die Quere kommt.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger("wisper")

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.GlobalShortcuts"

_MODS = {
    "ctrl": "CTRL",
    "control": "CTRL",
    "strg": "CTRL",
    "shift": "SHIFT",
    "umschalt": "SHIFT",
    "alt": "ALT",
    "win": "LOGO",
    "windows": "LOGO",
    "super": "LOGO",
    "meta": "LOGO",
}
_KEYS = {
    "space": "space",
    "leertaste": "space",
    "enter": "Return",
    "tab": "Tab",
    "esc": "Escape",
    "escape": "Escape",
    "backspace": "BackSpace",
    "insert": "Insert",
    "delete": "Delete",
    "home": "Home",
    "end": "End",
    "page up": "Prior",
    "pageup": "Prior",
    "page down": "Next",
    "pagedown": "Next",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "pause": "Pause",
}


def xdg_trigger(combo: str) -> str | None:
    """„ctrl+shift+space“ -> „CTRL+SHIFT+space“ (Schreibweise der XDG-Kurzbefehle)."""
    parts = []
    key = None
    for teil in (t.strip().lower() for t in combo.split("+")):
        if teil in _MODS:
            parts.append(_MODS[teil])
        elif key is None and teil in _KEYS:
            key = _KEYS[teil]
        elif key is None and len(teil) == 1 and teil.isascii() and teil.isalnum():
            key = teil
        elif key is None and teil[:1] == "f" and teil[1:].isdigit():
            key = teil.upper()
        else:
            return None
    return "+".join(parts + [key]) if key else None


class PortalHotkeys:
    """Gleiche Aufgabe wie `SystemHotkeys`, aber ueber das Desktop-Portal."""

    def __init__(self, app_id: str) -> None:
        self._app_id = app_id
        self._thread: threading.Thread | None = None
        self._loop = None
        self._handlers: dict[str, Callable[[], None]] = {}

    def register(self, entries: list[tuple[str, str, str, Callable[[], None]]]) -> list[str]:
        """entries: (Kennung, Beschreibung, Kombination, Handler).

        Gibt die Kombinationen zurueck, die sich nicht anmelden liessen. Die
        Antwort kommt erst nach einer moeglichen Rueckfrage von KDE; deshalb
        wird hoechstens kurz gewartet und ein spaeterer Fehler nur protokolliert.
        """
        ready = threading.Event()
        result: dict[str, list[str]] = {"failed": []}

        def run() -> None:
            try:
                self._run(entries, ready, result)
            except Exception:
                logger.exception("Portal-Kurzbefehle fehlgeschlagen")
                result["failed"] = [combo for _, _, combo, _ in entries]
                ready.set()

        self._thread = threading.Thread(target=run, name="portal-hotkeys", daemon=True)
        self._thread.start()
        ready.wait(3)
        return result["failed"]

    def _run(self, entries, ready: threading.Event, result: dict) -> None:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        context = GLib.MainContext.new()
        context.push_thread_default()
        self._loop = GLib.MainLoop.new(context, False)
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        # Ohne Anmeldung kennt das Portal das Programm nicht und KDE kann die
        # Befehle keinem Eintrag im Startmenue zuordnen.
        try:
            bus.call_sync(
                BUS,
                PATH,
                "org.freedesktop.host.portal.Registry",
                "Register",
                GLib.Variant("(sa{sv})", (self._app_id, {})),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error as exc:
            logger.info("Portal-Registry: %s", exc.message)

        sender = bus.get_unique_name()[1:].replace(".", "_")
        counter = [0]

        def request(method: str, params_for, on_done) -> None:
            """Portal-Muster: erst auf `Response` am Request-Objekt lauschen, dann rufen."""
            counter[0] += 1
            token = f"hams{counter[0]}"
            path = f"{PATH}/request/{sender}/{token}"
            sub = [0]

            def on_response(_c, _s, _p, _i, _sig, params) -> None:
                bus.signal_unsubscribe(sub[0])
                code, results = params.unpack()
                on_done(code, results)

            sub[0] = bus.signal_subscribe(
                BUS, "org.freedesktop.portal.Request", "Response", path, None, Gio.DBusSignalFlags.NONE, on_response
            )
            bus.call(BUS, PATH, IFACE, method, params_for(token), None, Gio.DBusCallFlags.NONE, -1, None, None)

        def on_activated(_c, _s, _p, _i, _sig, params) -> None:
            _session, shortcut_id, _ts, _opts = params.unpack()
            handler = self._handlers.get(shortcut_id)
            if handler is not None:
                try:
                    handler()
                except Exception:
                    logger.exception("Kurzbefehl-Handler fehlgeschlagen")

        bus.signal_subscribe(BUS, IFACE, "Activated", PATH, None, Gio.DBusSignalFlags.NONE, on_activated)

        shortcuts = []
        for sid, description, combo, handler in entries:
            self._handlers[sid] = handler
            opts = {"description": GLib.Variant("s", description)}
            trigger = xdg_trigger(combo)
            if trigger:
                opts["preferred_trigger"] = GLib.Variant("s", trigger)
            else:
                result["failed"].append(combo)
            shortcuts.append((sid, opts))

        def bound(code: int, results: dict) -> None:
            if code != 0:
                logger.error("Kurzbefehle abgelehnt (Antwort %s)", code)
                result["failed"] = [combo for _, _, combo, _ in entries]
            else:
                for sid, info in results.get("shortcuts", []):
                    logger.info("Kurzbefehl %s: %s", sid, info.get("trigger_description", "?"))
            ready.set()

        def created(code: int, results: dict) -> None:
            if code != 0:
                logger.error("Portal-Sitzung abgelehnt (Antwort %s)", code)
                result["failed"] = [combo for _, _, combo, _ in entries]
                ready.set()
                return
            session = results["session_handle"]
            request(
                "BindShortcuts",
                lambda token: GLib.Variant(
                    "(oa(sa{sv})sa{sv})", (session, shortcuts, "", {"handle_token": GLib.Variant("s", token)})
                ),
                bound,
            )

        request(
            "CreateSession",
            lambda token: GLib.Variant(
                "(a{sv})",
                (
                    {
                        "handle_token": GLib.Variant("s", token),
                        "session_handle_token": GLib.Variant("s", "hams_session"),
                    },
                ),
            ),
            created,
        )

        self._loop.run()
        context.pop_thread_default()

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.quit()
        if self._thread is not None:
            self._thread.join(2)
        self._thread = None
        self._loop = None
        self._handlers.clear()


class PortalSystemHotkeys(PortalHotkeys):
    """`SystemHotkeys`-Schnittstelle: register([(Kombination, Handler), …]).

    Das Portal braucht fuer jeden Befehl eine feste Kennung und eine
    Beschreibung, unter der er in den Systemeinstellungen steht. Beides ergibt
    sich daraus, welcher Einstellung die Kombination gehoert.
    """

    def __init__(self) -> None:
        from wisper.linux import APP_ID

        super().__init__(APP_ID)

    def register(self, entries):
        from wisper.config import CFG

        named = {
            CFG.hotkey_transcript: ("transkript", "Hams: Diktat aufnehmen"),
            CFG.hotkey_prompt: ("prompt", "Hams: Prompt aufnehmen"),
        }
        return super().register(
            [
                (*named.get(combo, (f"kurzbefehl{i}", f"Hams: Kurzbefehl {i}")), combo, handler)
                for i, (combo, handler) in enumerate(entries, start=1)
            ]
        )
