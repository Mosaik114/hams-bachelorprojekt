"""How the core objects (`Pipeline`, and through the app `Recorder` and `ModelManager`) talk to the interface.

The core has no Tk. It hands every update to a `StatusSink`; the application implements it with its main window,
and tests implement it with a recorder of calls.
"""

from collections.abc import Callable
from typing import Protocol


class StatusSink(Protocol):
    """The interface as the core sees it."""

    @property
    def is_recording(self) -> bool:
        """Whether a recording is running: the main window belongs to it until it stops."""

    def ui_call(self, fn: Callable[[], object]) -> None:
        """Runs `fn` on the interface thread soon. Safe from any thread, never blocks."""

    def apply_state(
        self, state: str, detail: str | None = None, hint: str | None = None, *, calm: bool = False
    ) -> None:
        """Shows a state of the main window (see `FloatingTranscriberApp.STATE_KEYS`). On the interface thread."""

    def show_answer_length(self, characters: int) -> None:
        """The live character count of a streamed AI answer. On the interface thread."""
