"""Whether a window of this process is being resized at its edge right now.

While it is, work that is otherwise left for a quiet moment is done at once: a plate that waits for the layout to
settle would otherwise show its picture of the last size for a frame, stretched or cut, before the new one comes
(the "fragments" at the growing edge). `WindowChrome` enters and leaves; painted surfaces ask `Resizing.active()`.
"""

from __future__ import annotations


class Resizing:
    _count = 0

    @classmethod
    def active(cls) -> bool:
        return cls._count > 0

    @classmethod
    def enter(cls) -> None:
        cls._count += 1

    @classmethod
    def leave(cls) -> None:
        cls._count = max(0, cls._count - 1)
