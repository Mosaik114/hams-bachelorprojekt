"""Die Gruppe „Kurzbefehle“ im Einstellungsfenster: anzeigen, aendern, zuruecksetzen, neu starten.

Unter Windows nimmt „Ändern“ die neue Kombination direkt auf, prueft sie (`hotkey_editor.check`),
schreibt sie in config.toml und meldet beide Kurzbefehle sofort neu an. Unter Linux verwaltet KDE die
Kurzbefehle des Portals selbst; die Zeile sagt dort, wo man sie aendert.
"""

from __future__ import annotations

import sys
import tkinter as tk
from types import SimpleNamespace

from hams import hotkey_editor
from hams import ui_kit as uk
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import t
from hams.ui_kit import Theme


def editable() -> bool:
    """Nur unter Windows nimmt Hams Kurzbefehle selbst auf; unter Linux verwaltet sie das System."""
    return sys.platform == "win32"


def current_combos() -> dict[str, str]:
    return {action: getattr(CFG, f"hotkey_{action}") for action in hotkey_editor.DEFAULTS}


class ShortcutsSectionMixin:
    """Baut die Gruppe und fuehrt die Aufnahme (braucht `app`, `win` und `_group` von SettingsWindow)."""

    CHANGE_WIDTH = 96  # logisch; „Ändern“ plus Luft
    DEFAULT_WIDTH = 108  # logisch; „Standard“ plus Luft

    def _build_shortcuts(self, master: tk.Misc) -> None:
        """Beide Kurzbefehle als Tasten — mit „Ändern“ — und der Neustart, falls sie ausfallen."""
        group = self._group(master, t("settings.section.shortcuts"))

        self.shortcut_keys = {}
        self.shortcut_rows: dict[str, SimpleNamespace] = {}
        self._capture: str | None = None
        self._recorder = hotkey_editor.ComboRecorder()
        for key, title, combo in self.app.shortcuts():
            self._build_shortcut_row(group, key, title, combo)

        # Steht bewusst direkt unter den Kurzbefehlen: das ist der Fall, in dem
        # man ihn braucht. Windows kann den Tastaturhook entfernen, ohne dass
        # die Anwendung etwas davon mitbekommt — Fenster und Tray-Symbol laufen
        # dann weiter, nur die Tastenkombinationen kommen nicht mehr an. Von
        # innen laesst sich der Hook nicht neu setzen (siehe `restart_app`),
        # ein frischer Prozess dagegen schon.
        self.row_restart = uk.SettingRow(
            group.body,
            t("settings.restart.title"),
            variant="description",
            description=self.RESTART_HINT,
            hairline=False,
        )
        self.row_restart.pack(fill="x")
        self.restart_button = uk.SecondaryButton(
            group.body,
            t("settings.restart.button"),
            self._restart_app,
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(self.restart_button, self.RESTART_WIDTH)
        self.row_restart.set_control(self.restart_button)

    def _build_shortcut_row(self, group: uk.GroupSurface, key: str, title: str, combo: str) -> None:
        """Titel und Hinweis oben, darunter die Tasten links und die Aktionen rechts."""
        row = uk.SettingRow(
            group.body, title, variant="description", description=self._shortcut_hint(key), stacked=True
        )
        row.pack(fill="x")
        box = tk.Frame(group.body, bg=Theme.GROUP_SURFACE, highlightthickness=0, takefocus=0)
        box.focus_ring = True  # die Knoepfe darin tragen ihn
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
            self._cancel_capture,
            bg_under=Theme.GROUP_SURFACE,
            tooltip=t("settings.shortcut.cancel_tooltip"),
        )
        parts.default = uk.SecondaryButton(
            parts.box,
            t("settings.shortcut.default"),
            lambda: self._reset_shortcut(key),
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(parts.default, self.DEFAULT_WIDTH)
        parts.change = uk.SecondaryButton(
            parts.box,
            t("settings.shortcut.change"),
            lambda: self._start_capture(key),
            bg_under=Theme.GROUP_SURFACE,
            height=uk.Height.STANDARD,
        )
        uk.keep_logical_width(parts.change, self.CHANGE_WIDTH)
        parts.change.pack(side="right")
        # Die Aufnahme landet auf der Flaeche selbst und endet dort ("break"): sonst griffen die
        # Bindungen des Fensters, etwa Esc zum Schliessen oder Pos1/Ende zum Scrollen.
        parts.box.bind("<KeyPress>", self._on_capture_key)
        parts.box.bind("<KeyRelease>", self._on_capture_release)
        parts.box.bind("<FocusOut>", self._on_capture_focus_out, add="+")
        parts.box.bind("<FocusIn>", self._on_capture_focus_in, add="+")

    @staticmethod
    def _shortcut_hint(key: str) -> str:
        if not editable():
            return t("settings.shortcut.linux_hint")
        return t(f"settings.shortcut.{key}_hint")

    # ---------------------------------------------------------- Aufnahme

    def _start_capture(self, key: str) -> None:
        """Gibt die Kurzbefehle frei und wartet auf die neue Kombination."""
        if self._capture is not None and self._capture != key:
            self._finish_capture(self._shortcut_hint(self._capture))
        self._capture = key
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

    def _on_capture_key(self, event: tk.Event) -> str:
        if self._capture is None:
            return "break"
        result = self._recorder.press(int(event.keycode))
        parts = self.shortcut_rows[self._capture]
        if result == hotkey_editor.CANCEL:
            self._cancel_capture()
        elif result == hotkey_editor.PENDING:
            parts.keys.set_combo(self._recorder.held())
        elif result == hotkey_editor.UNSUPPORTED:
            parts.row.set_description(t("settings.shortcut.unsupported"))
        else:
            self._try_combo(self._capture, result)
        return "break"

    def _on_capture_focus_out(self, _event=None) -> None:
        """Leaving the capture (another window, a rejected combination left open): the hotkeys work again."""
        self._recorder.reset()
        if self._capture is not None:
            self.app.reregister_hotkeys()

    def _on_capture_focus_in(self, _event=None) -> None:
        """Back in an open capture: the old combination must not start a dictation while keys are pressed."""
        if self._capture is not None:
            self.app.suspend_hotkeys()

    def _on_capture_release(self, event: tk.Event) -> str:
        self._recorder.release(int(event.keycode))
        return "break"

    def _try_combo(self, key: str, combo: str) -> None:
        """Prueft die Kombination; ist sie gut, gilt sie sofort, sonst bleibt die Aufnahme offen."""
        result = hotkey_editor.check(combo, key, current_combos(), self.app.shortcut_available)
        if not result.ok:
            parts = self.shortcut_rows[key]
            parts.keys.set_combo(getattr(CFG, f"hotkey_{key}"))
            parts.row.set_description(result.problem)
            logger.info("Shortcut rejected (%s): %s", key, combo)
            return
        self._apply_shortcut(key, result.combo)

    def _reset_shortcut(self, key: str) -> None:
        self._try_combo(key, hotkey_editor.DEFAULTS[key])

    def _apply_shortcut(self, key: str, combo: str) -> None:
        setattr(CFG, f"hotkey_{key}", combo)
        saved = _persist_config_value("hotkeys", key, combo)
        logger.info("Shortcut changed (%s): %s", key, combo)
        self._finish_capture(t("settings.shortcut.saved") if saved else t("settings.shortcut.not_saved"))

    def _cancel_capture(self) -> None:
        if self._capture is not None:
            self._finish_capture(self._shortcut_hint(self._capture))

    def _finish_capture(self, description: str) -> None:
        """Beendet die Aufnahme und meldet die Kurzbefehle aus CFG wieder an."""
        key, self._capture = self._capture, None
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

    def _close_capture(self) -> None:
        """Beim Schliessen des Fensters: eine offene Aufnahme darf die Kurzbefehle nicht abgemeldet lassen."""
        if getattr(self, "_capture", None) is not None:
            self._capture = None
            self.app.reregister_hotkeys()
