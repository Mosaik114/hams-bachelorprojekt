"""Changing a shortcut in the settings: recording the keys and checking the result.

Pure logic without Tk, so that it can be tested without a window. The settings
window (`settings_shortcuts.py`) feeds key presses in and shows what comes out.

Keys are recorded by their Windows virtual-key code (`event.keycode` in Tk on
Windows), not by their keysym: with Shift held, a keysym becomes the shifted
character of the current layout ("exclam" instead of "1"), while the key code
stays the key. The names produced are the ones `system_hotkeys.parse_combo`
and `ui_kit.keys.key_labels` already understand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hams.i18n import t
from hams.ui_kit.keys import key_labels

#: The shortcuts that can be changed, with their default. Same values as `Config`
#: and `config.default.toml`.
DEFAULTS = {"transcript": "ctrl+shift+space", "prompt": "ctrl+shift+alt+space"}

#: Written in this order, like the defaults ("ctrl+shift+alt+space").
MODIFIER_ORDER = ("ctrl", "shift", "alt", "win")

_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "strg": "ctrl",
    "shift": "shift",
    "umschalt": "shift",
    "alt": "alt",
    "win": "win",
    "windows": "win",
    "super": "win",
}

_KEY_ALIASES = {
    "leertaste": "space",
    "escape": "esc",
    "return": "enter",
    "pageup": "page up",
    "pagedown": "page down",
    "del": "delete",
}

#: Virtual-key codes of the modifiers, left and right variants included.
MODIFIER_VKS = {
    0x10: "shift",
    0xA0: "shift",
    0xA1: "shift",
    0x11: "ctrl",
    0xA2: "ctrl",
    0xA3: "ctrl",
    0x12: "alt",
    0xA4: "alt",
    0xA5: "alt",
    0x5B: "win",
    0x5C: "win",
}

#: Virtual-key codes of the named keys, the inverse of `system_hotkeys._KEYS`.
_NAMED_VKS = {
    0x20: "space",
    0x0D: "enter",
    0x09: "tab",
    0x08: "backspace",
    0x2D: "insert",
    0x2E: "delete",
    0x24: "home",
    0x23: "end",
    0x21: "page up",
    0x22: "page down",
    0x26: "up",
    0x28: "down",
    0x25: "left",
    0x27: "right",
    0x13: "pause",
}

VK_ESCAPE = 0x1B

#: Combinations Windows keeps for itself, or that would break typing and pasting
#: (Hams itself pastes with Ctrl+V). F12 is reserved for debuggers at all times.
RESERVED = frozenset(
    {
        "alt+tab",
        "shift+alt+tab",
        "ctrl+alt+tab",
        "alt+esc",
        "alt+f4",
        "alt+space",
        "ctrl+esc",
        "ctrl+shift+esc",
        "ctrl+alt+delete",
        "ctrl+a",
        "ctrl+c",
        "ctrl+v",
        "ctrl+x",
        "ctrl+y",
        "ctrl+z",
        "win+a",
        "win+d",
        "win+e",
        "win+h",
        "win+i",
        "win+l",
        "win+m",
        "win+r",
        "win+s",
        "win+v",
        "win+x",
        "win+tab",
        "win+space",
        "f12",
    }
)

#: Recorder results besides a finished combination.
PENDING = "pending"
CANCEL = "cancel"
UNSUPPORTED = "unsupported"


def key_name(vk: int) -> str | None:
    """Name of a non-modifier key, or None for keys a shortcut can't use."""
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x70 + 1}"
    return _NAMED_VKS.get(vk)


def _split(combo: str) -> tuple[set[str], str | None] | None:
    """Modifiers and the one key of a combination; None if it isn't exactly that."""
    modifiers: set[str] = set()
    key = None
    for part in (p.strip().lower() for p in str(combo or "").split("+")):
        if not part:
            return None
        if part in _MODIFIER_ALIASES:
            modifiers.add(_MODIFIER_ALIASES[part])
        elif key is None:
            key = _KEY_ALIASES.get(part, part)
        else:
            return None
    return modifiers, key


def canonical(combo: str) -> str | None:
    """One spelling for each combination: "Strg+Umschalt+Leertaste" -> "ctrl+shift+space"."""
    split = _split(combo)
    if split is None or split[1] is None:
        return None
    modifiers, key = split
    return "+".join([m for m in MODIFIER_ORDER if m in modifiers] + [key])


def is_function_key(key: str) -> bool:
    return key[:1] == "f" and key[1:].isdigit() and 1 <= int(key[1:]) <= 24


def display(combo: str) -> str:
    """The combination as its keycaps read, joined with plus signs (e.g. Strg, Umschalt, Leertaste)."""
    return "+".join(key_labels(combo))


class ComboRecorder:
    """Collects the held modifiers until a real key completes the combination.

    `press` returns PENDING while only modifiers are down, CANCEL for Esc on its
    own, UNSUPPORTED for a key a shortcut can't use, and otherwise the
    combination in canonical spelling.
    """

    def __init__(self) -> None:
        self._held: set[str] = set()

    def reset(self) -> None:
        self._held.clear()

    def held(self) -> str:
        """The modifiers held right now, for showing them while the user builds the combination."""
        return "+".join(m for m in MODIFIER_ORDER if m in self._held)

    def press(self, vk: int) -> str:
        modifier = MODIFIER_VKS.get(vk)
        if modifier is not None:
            self._held.add(modifier)
            return PENDING
        if vk == VK_ESCAPE and not self._held:
            return CANCEL
        key = "esc" if vk == VK_ESCAPE else key_name(vk)
        if key is None:
            return UNSUPPORTED
        return "+".join([m for m in MODIFIER_ORDER if m in self._held] + [key])

    def release(self, vk: int) -> None:
        modifier = MODIFIER_VKS.get(vk)
        if modifier is not None:
            self._held.discard(modifier)


@dataclass(frozen=True)
class Check:
    """Result of `check`: the combination to save, or the reason it can't be used."""

    combo: str = ""
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem


def check(
    combo: str, action: str, current: dict[str, str], available: Callable[[str], bool] = lambda _combo: True
) -> Check:
    """Whether `combo` may become the shortcut of `action`.

    `current` maps each action to its combination today; `available` asks the
    system whether no other program holds the combination (it is only asked
    when everything else is fine).
    """
    combo = canonical(combo) or ""
    problem = _rule_problem(combo, action, current)
    if problem:
        return Check(problem=problem)
    if combo != canonical(current.get(action, "")) and not available(combo):
        return Check(problem=t("settings.shortcut.in_use", combo=display(combo)))
    return Check(combo=combo)


def _rule_problem(combo: str, action: str, current: dict[str, str]) -> str:
    """The first rule `combo` breaks, as a sentence, or ""."""
    if not combo:
        return t("settings.shortcut.unsupported")
    modifiers, key = _split(combo) or (set(), "")
    if not (modifiers - {"shift"}) and not is_function_key(key or ""):
        return t("settings.shortcut.need_modifier")
    if combo in RESERVED:
        return t("settings.shortcut.reserved", combo=display(combo))
    for other, other_combo in current.items():
        if other != action and canonical(other_combo) == combo:
            return t("settings.shortcut.duplicate", combo=display(combo), name=t(f"shortcut.{other}"))
    return ""
