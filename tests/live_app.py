"""Die echte Anwendung, aufgebaut ohne Wirkung nach aussen.

Hauptfenster und Einstellungsfenster entstehen wirklich, mit allen Widgets und
Zustaenden. Ersetzt wird nur, was den Rechner beruehrt: Mikrofon, globale
Kurzbefehle, Tray-Symbol, Signalton, Zwischenablage, Tastendruck, die optionale
Meldung an ein Overlay (cursor_dot), Ollama und das Speichern der Konfiguration.
"""

from types import SimpleNamespace

import numpy as np
import sounddevice as sd


class FakeHotkeys:
    """`SystemHotkeys` ohne `RegisterHotKey`; `failing` scheitert absichtlich."""

    failing: set = set()
    #: Kombinationen, die ein anderes Programm haelt (`available` meldet sie belegt).
    taken: set = set()

    def __init__(self) -> None:
        self.entries: list = []
        self.stopped = False
        self.probed: list = []

    def register(self, entries) -> list:
        self.entries = list(entries)
        return [combo for combo, _handler in self.entries if combo in self.failing]

    def available(self, combo) -> bool:
        self.probed.append(combo)
        return combo not in self.taken

    def stop(self) -> None:
        self.stopped = True
        self.entries = []


class FakeKeyboard:
    """Die `keyboard`-Bibliothek ohne Hook und ohne echten Tastendruck."""

    def __init__(self) -> None:
        self.hotkeys: dict = {}
        self.refuse: dict = {}
        self.sent: list = []
        self.unhooked = False

    def add_hotkey(self, combo, callback) -> None:
        if combo in self.refuse:
            raise self.refuse[combo]
        self.hotkeys[combo] = callback

    def unhook_all_hotkeys(self) -> None:
        self.unhooked = True

    def send(self, keys) -> None:
        self.sent.append(keys)


class FakeTray:
    def __init__(self) -> None:
        self.icon = None
        self.title = ""
        self.menu = None
        self.stopped = False

    def update_menu(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True


class FakeStream:
    """`sd.InputStream`: merkt sich den Rueckruf, oeffnet kein Geraet."""

    opened: list = []
    fail_with = None

    def __init__(self, **kwargs) -> None:
        if FakeStream.fail_with is not None:
            raise FakeStream.fail_with
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.running = False
        self.closed = False
        FakeStream.opened.append(self)

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def abort(self) -> None:
        self.running = False

    def close(self) -> None:
        self.closed = True

    def feed(self, samples) -> None:
        """Ein Puffer so, wie PortAudio ihn liefert: (Frames, Kanaele)."""
        block = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        self.callback(block, len(block), None, None)


class FakeClipboard:
    PyperclipException = type("PyperclipException", (Exception,), {})

    def __init__(self) -> None:
        self.content = ""
        self.copies: list = []
        self.broken = False

    def copy(self, text) -> None:
        self.copies.append(text)
        self.content = text

    def paste(self) -> str:
        if self.broken:
            raise self.PyperclipException("belegt")
        return self.content


def fake_sounddevice() -> SimpleNamespace:
    return SimpleNamespace(InputStream=FakeStream, PortAudioError=sd.PortAudioError)
