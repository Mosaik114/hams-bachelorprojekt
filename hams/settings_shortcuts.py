"""The settings group "Shortcuts": show, change, reset, and restart Hams when they stop working.

On Windows "Change" records the new combination directly, checks it (`hotkey_editor.check`), writes it to
config.toml and registers both shortcuts again at once. On Linux KDE manages the portal's shortcuts itself; the
row says where to change them.
"""

from __future__ import annotations

import sys
import tkinter as tk
from types import SimpleNamespace

from hams import hotkey_editor
from hams import ui_kit as uk
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import Text, t
from hams.settings_sections import Section, group
from hams.ui_kit import Theme


def editable() -> bool:
    """Only on Windows Hams records shortcuts itself; on Linux the system manages them."""
    return sys.platform == "win32"


def current_combos() -> dict[str, str]:
    return {action: getattr(CFG, f"hotkey_{action}") for action in hotkey_editor.DEFAULTS}


class ShortcutsSection(Section):
    """Both shortcuts as keys - with "Change" - and the restart, in case they fail."""

    CHANGE_WIDTH = 96  # logical; "Ändern" plus air
    DEFAULT_WIDTH = 108  # logical; "Standard" plus air
    RESTART_WIDTH = 108  # logical; "Neu starten" plus air
    #: The description wraps, so it may be a whole sentence.
    RESTART_HINT = Text("settings.restart.hint")

    def __init__(self, window, master: tk.Misc) -> None:
        super().__init__(window)
        self.group = group(master, t("settings.section.shortcuts"))
        body = self.group.body
        self.shortcut_keys: dict[str, uk.KeyCombo] = {}
        self.shortcut_rows: dict[str, SimpleNamespace] = {}
        #: The shortcut being recorded, or None.
        self.capture: str | None = None
        self._recorder = hotkey_editor.ComboRecorder()
        for key, title, combo in self.app.shortcuts():
            self._build_shortcut_row(body, key, title, combo)

        # Deliberately right below the shortcuts: that is the case in which it is needed. Windows can remove the
        # keyboard hook without the app noticing - window and tray icon keep running, only the key combinations no
        # longer arrive. The hook cannot be set again from inside (see `restart_app`), a fresh process can.
        self.row_restart = uk.SettingRow(
            body, t("settings.restart.title"), variant="description", description=self.RESTART_HINT, hairline=False
        )
        self.row_restart.pack(fill="x")
        self.restart_button = uk.SecondaryButton(
            body,
            t("settings.restart.button"),
            self.restart_app,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(self.restart_button, self.RESTART_WIDTH)
        self.row_restart.set_control(self.restart_button)

    def sync(self) -> None:
        for key, parts in self.shortcut_rows.items():
            parts.keys.set_combo(getattr(CFG, f"hotkey_{key}"))
            parts.row.set_description(self.shortcut_hint(key))
            if parts.change is not None:
                parts.cancel.pack_forget()
                parts.default.pack_forget()
                parts.change.pack(side="right")
        self.restart_button.set_enabled(True)
        self.row_restart.set_description(self.RESTART_HINT)

    def on_hide(self) -> None:
        super().on_hide()
        self.close_capture()

    def _build_shortcut_row(self, body: tk.Misc, key: str, title: str, combo: str) -> None:
        """Title and hint on top, below them the keys on the left and the actions on the right."""
        row = uk.SettingRow(body, title, variant="description", description=self.shortcut_hint(key), stacked=True)
        row.pack(fill="x")
        box = tk.Frame(body, bg=Theme.GROUP_SURFACE, highlightthickness=0, takefocus=0)
        box.focus_ring = True  # the buttons inside carry it
        keys = uk.KeyCombo(box, bg_under=Theme.GROUP_SURFACE, combo=combo)
        keys.pack(side="left")
        parts = SimpleNamespace(row=row, box=box, keys=keys, change=None, default=None, cancel=None)
        if editable():
            self._build_shortcut_buttons(parts, key)
        row.set_control(box)
        self.shortcut_keys[key] = keys
        self.shortcut_rows[key] = parts

    def _build_shortcut_buttons(self, parts: SimpleNamespace, key: str) -> None:
        parts.cancel = uk.IconButton(
            parts.box,
            "close",
            self.cancel_capture,
            bg_under=Theme.GROUP_SURFACE,
            tooltip=t("settings.shortcut.cancel_tooltip"),
        )
        parts.default = uk.SecondaryButton(
            parts.box,
            t("settings.shortcut.default"),
            lambda: self.reset_shortcut(key),
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(parts.default, self.DEFAULT_WIDTH)
        parts.change = uk.SecondaryButton(
            parts.box,
            t("settings.shortcut.change"),
            lambda: self.start_capture(key),
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(parts.change, self.CHANGE_WIDTH)
        parts.change.pack(side="right")
        # The recording lands on the surface itself and ends there ("break"): otherwise the window's bindings would
        # act, such as Esc to close or Home/End to scroll.
        parts.box.bind("<KeyPress>", self.on_capture_key)
        parts.box.bind("<KeyRelease>", self.on_capture_release)
        parts.box.bind("<FocusOut>", self.on_capture_focus_out, add="+")
        parts.box.bind("<FocusIn>", self.on_capture_focus_in, add="+")

    @staticmethod
    def shortcut_hint(key: str) -> str:
        if not editable():
            return t("settings.shortcut.linux_hint")
        return t(f"settings.shortcut.{key}_hint")

    # ---------------------------------------------------------- recording a combination

    def start_capture(self, key: str) -> None:
        """Releases the shortcuts and waits for the new combination."""
        if self.capture is not None and self.capture != key:
            self._finish_capture(self.shortcut_hint(self.capture))
        self.capture = key
        self._recorder.reset()
        self.app.suspend_hotkeys()
        parts = self.shortcut_rows[key]
        parts.change.pack_forget()
        parts.cancel.pack(side="right")
        if hotkey_editor.canonical(getattr(CFG, f"hotkey_{key}")) != hotkey_editor.DEFAULTS[key]:
            parts.default.pack(side="right", padx=(0, uk.px(uk.Space.XS)))
        parts.row.set_description(t("settings.shortcut.capture"))
        parts.box.focus_set()
        logger.info("Shortcut capture started (%s)", key)

    def on_capture_key(self, event: tk.Event) -> str:
        if self.capture is None:
            return "break"
        result = self._recorder.press(int(event.keycode))
        parts = self.shortcut_rows[self.capture]
        if result == hotkey_editor.CANCEL:
            self.cancel_capture()
        elif result == hotkey_editor.PENDING:
            parts.keys.set_combo(self._recorder.held())
        elif result == hotkey_editor.UNSUPPORTED:
            parts.row.set_description(t("settings.shortcut.unsupported"))
        else:
            self._try_combo(self.capture, result)
        return "break"

    def on_capture_focus_out(self, _event=None) -> None:
        """Leaving the capture (another window, a rejected combination left open): the hotkeys work again."""
        self._recorder.reset()
        if self.capture is not None:
            self.app.reregister_hotkeys()

    def on_capture_focus_in(self, _event=None) -> None:
        """Back in an open capture: the old combination must not start a dictation while keys are pressed."""
        if self.capture is not None:
            self.app.suspend_hotkeys()

    def on_capture_release(self, event: tk.Event) -> str:
        self._recorder.release(int(event.keycode))
        return "break"

    def _try_combo(self, key: str, combo: str) -> None:
        """Checks the combination; a good one applies at once, otherwise the capture stays open."""
        result = hotkey_editor.check(combo, key, current_combos(), self.app.shortcut_available)
        if not result.ok:
            parts = self.shortcut_rows[key]
            parts.keys.set_combo(getattr(CFG, f"hotkey_{key}"))
            parts.row.set_description(result.problem)
            logger.info("Shortcut rejected (%s): %s", key, combo)
            return
        self._apply_shortcut(key, result.combo)

    def reset_shortcut(self, key: str) -> None:
        self._try_combo(key, hotkey_editor.DEFAULTS[key])

    def _apply_shortcut(self, key: str, combo: str) -> None:
        setattr(CFG, f"hotkey_{key}", combo)
        saved = _persist_config_value("hotkeys", key, combo)
        logger.info("Shortcut changed (%s): %s", key, combo)
        self._finish_capture(t("settings.shortcut.saved") if saved else t("settings.shortcut.not_saved"))

    def cancel_capture(self) -> None:
        if self.capture is not None:
            self._finish_capture(self.shortcut_hint(self.capture))

    def _finish_capture(self, description: str) -> None:
        """Ends the capture and registers the shortcuts from CFG again."""
        key, self.capture = self.capture, None
        self._recorder.reset()
        self.app.reregister_hotkeys()
        if key is None:
            return
        parts = self.shortcut_rows[key]
        parts.keys.set_combo(getattr(CFG, f"hotkey_{key}"))
        parts.cancel.pack_forget()
        parts.default.pack_forget()
        parts.change.pack(side="right")
        parts.row.set_description(description)

    def close_capture(self) -> None:
        """When the window closes: an open capture must not leave the shortcuts unregistered."""
        if self.capture is not None:
            self.capture = None
            self.app.reregister_hotkeys()

    # ---------------------------------------------------------- restart

    def restart_app(self) -> None:
        """Restarts Hams - or says why not right now.

        No confirmation dialog: the button sits behind a row of its own with a clear label, and the two cases in
        which a restart would really hurt (a recording, a download) are refused by `restart_app` itself.
        """
        self.restart_button.set_enabled(False)
        self.row_restart.set_description(t("settings.restart.running"))
        reason = self.app.restart_app()
        if reason:
            self.restart_button.set_enabled(True)
            self.row_restart.set_description(reason)
            logger.info("Restart refused: %s", reason)
