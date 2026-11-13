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
    """`sounddevice` without devices; counts how often PortAudio was re-initialised (`reinits`)."""
    fake = SimpleNamespace(InputStream=FakeStream, PortAudioError=sd.PortAudioError, reinits=0)
    fake._terminate = lambda: None
    fake._initialize = lambda: setattr(fake, "reinits", fake.reinits + 1)
    return fake


class FakeOllama:
    """`OllamaClient` without a service: records every request and answers with `answer`.

    `answer` is the text, an exception to raise, or a function of the prompt. `requests` holds one dict per
    `generate` call: `prompt`, `model` and the keyword arguments that were passed.
    """

    def __init__(self, answer="ok") -> None:
        self.answer = answer
        self.requests: list = []
        self.warmed: list = []

    def generate(self, prompt, model, **kwargs) -> str:
        self.requests.append({"prompt": prompt, "model": model, **kwargs})
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer(prompt) if callable(self.answer) else self.answer

    def warm_up(self, model, keep_alive, timeout=60.0) -> None:
        self.warmed.append((model, keep_alive))


class LastRequest:
    """The newest request a `FakeOllama` received, read like a dict at the moment it is asked."""

    def __init__(self, ollama: FakeOllama) -> None:
        self.ollama = ollama

    def __getitem__(self, key):
        return self.ollama.requests[-1][key]

    def __contains__(self, key) -> bool:
        return key in self.ollama.requests[-1]


class RecordingSink:
    """A `StatusSink` without a window: runs UI calls at once and records every state and answer length."""

    def __init__(self) -> None:
        self.is_recording = False
        self.states: list = []
        self.lengths: list = []

    def ui_call(self, fn) -> None:
        fn()

    def apply_state(self, state, detail=None, hint=None, *, calm=False) -> None:
        self.states.append((state, detail, hint))

    def show_answer_length(self, characters) -> None:
        self.lengths.append(characters)


class RecordingEvents:
    """`ModelEvents` that only record what the model manager reported."""

    def __init__(self) -> None:
        self.loaded: list = []
        self.progress = 0
        self.finished: list = []

    def model_loaded(self, model_id, ok, requested) -> None:
        self.loaded.append((model_id, ok, requested))

    def download_progress(self) -> None:
        self.progress += 1

    def download_finished(self, model_id, error) -> None:
        self.finished.append((model_id, error))


def ready_models(model=None):
    """A real `ModelManager` with `model` loaded (an object with `transcribe`), callbacks run at once."""
    from hams.whisper_model import ModelManager

    models = ModelManager(lambda fn: fn(), RecordingEvents())
    if model is not None:
        models.model = model
        models.active_model_size = "turbo"
        models.model_ready.set()
    return models


def make_pipeline(model=None, *, ollama=None, sink=None):
    """A real `Pipeline` with fakes around it.

    Returns the pipeline; `pipeline.sink.states` holds the states, `pipeline.pasted` what was pasted and
    `pipeline.delivered` what went to the history. Pasting is recorded instead of touching the clipboard.
    """
    from hams.transcription import Pipeline

    delivered: list = []
    pipeline = Pipeline(
        ready_models(object() if model is None else model),
        sink or RecordingSink(),
        ollama or FakeOllama(),
        on_delivered=delivered.append,
    )
    pipeline.delivered = delivered
    pipeline.states = pipeline.sink.states
    pipeline.pasted = []
    pipeline.copy_and_paste = pipeline.pasted.append
    return pipeline
