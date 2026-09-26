"""Tray-Symbol und die Meldung an die Mosaiks-Suche (Soundbar am Mauszeiger)."""

import ctypes
import os
import threading
import tkinter as tk
from datetime import datetime
from typing import Optional


import pyperclip
import pystray
from PIL import Image, ImageTk

from wisper import branding
from wisper import ui_kit as uk
from wisper.ui_kit import Theme
from wisper.config import logger


#: Farben des Tray-Symbols — bewusst **fest**, nicht aus dem aktiven
#: Darstellungsmodus. Das Hams-Fenster und die Windows-Taskleiste sind zwei
#: verschiedene Oberflaechen: wer Hams hell stellt, hat deshalb noch lange
#: keine helle Taskleiste. Ein Symbol, das seine Farbe nach dem App-Theme
#: richtet, waere auf der Leiste mal zu blass und mal zu dunkel. Genommen sind
#: die kraeftigen Toene, die sich auf beiden Leistenfarben behaupten.
TRAY_PALETTE = Theme.palette(Theme.DARK)


_DOT_MSG = ctypes.windll.user32.RegisterWindowMessageW("MosaiksSuche.HamsZustand")


def _notify_cursor_dot(state: str, level: Optional[float] = None) -> None:
    """Meldet der Mosaiks-Suche Zustand und Pegel fuer die Soundbar am Mauszeiger.

    1 = Diktat (rot), 2 = Prompt (lila), 0 = aus; der Pegel (0..1) kommt ab
    Bit 8 als Wert + 1 dazu. PostMessage wartet nicht; laeuft die Mosaiks-Suche
    nicht, passiert einfach nichts. Die eigene Prozess-ID reist mit, damit die
    Anzeige verschwindet, falls Hams abstuerzt.
    """
    mode = {"recording": 1, "recording_prompt": 2}.get(state, 0)
    if level is not None:
        mode |= (int(max(0.0, min(1.0, level)) * 255) + 1) << 8
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "Mosaiks-Suche")
        if hwnd and _DOT_MSG:
            user32.PostMessageW(hwnd, _DOT_MSG, mode, os.getpid())
    except OSError:
        pass


def _create_tray_icon_image(color: Optional[str] = None) -> Image.Image:
    """Das Tray-Symbol im aktuellen Zustand.

    Die Form ist immer die Hams-Bildmarke, die Farbe kommt aus dem Zustand.
    Hier stand frueher ein blauer Kreis mit weissem „W"; beides gehoerte zum
    alten Produktnamen und ist ersatzlos verschwunden.
    """
    return branding.tray_image(color or TRAY_PALETTE["ACCENT_TINT"])


class TrayMixin:
    """Tray-Symbol, Verlauf und Ein-/Ausblenden des Fensters."""

    # ------------------------------------------------------------------ tray

    def _setup_tray(self) -> None:
        image = _create_tray_icon_image()
        self.tray_icon = pystray.Icon(branding.PRODUCT_NAME, image,
                                      self._tray_tooltip(), self._build_tray_menu())
        threading.Thread(target=self.tray_icon.run, daemon=True, name="tray").start()

    def _apply_window_icon(self) -> None:
        """Setzt das Anwendungssymbol auf dem vorhandenen Tk-Weg.

        Beides, weil beides gebraucht wird: `iconbitmap(default=...)` reicht das
        ICO mit allen Groessen an Windows durch (Taskleiste, Alt+Tab), und
        `iconphoto` versorgt Tk-eigene Fenster, die kein ICO annehmen. Ein
        randloses Fenster zeigt selbst kein Symbol — deshalb keine zusaetzliche
        native Schicht dafuer.
        """
        icon = branding.icon_path()
        if icon is not None:
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:      # pragma: no cover - ICO nicht annehmbar
                logger.warning("Fenstersymbol %s abgelehnt", icon, exc_info=True)
        image = branding.app_icon_image(64) or _create_tray_icon_image()
        self._tk_icon = ImageTk.PhotoImage(image)
        self.root.iconphoto(True, self._tk_icon)

    def _tray_tooltip(self) -> str:
        base = branding.PRODUCT_NAME
        if not self.model_ready.is_set():
            return f"{base} — Modell lädt"
        if self.is_recording:
            mode = "Prompt-Modus" if self._prompt_mode else "Aufnahme"
            return f"{base} — {mode}"
        with self._history_lock:
            last = self._history[0] if self._history else None
        if last is not None:
            return f"{base} — zuletzt: {last[1]}"
        return f"{base} — bereit"

    def _update_tray_tooltip(self) -> None:
        if hasattr(self, "tray_icon"):
            self.tray_icon.title = self._tray_tooltip()

    def _history_action(self, index: int):
        """Rückruf für einen Verlaufseintrag — mit genau zwei Parametern.

        pystray liest die Zahl der Positionsparameter aus `co_argcount` und
        lässt nur 0, 1 oder 2 zu; alles andere quittiert es mit `ValueError`.
        Der frühere Schlüssel-Trick

            lambda _icon=None, _item=None, i=idx: ...

        zählt drei — Vorgabewerte ändern daran nichts. Das Tray-Menü liess sich
        deshalb nicht mehr aufbauen, sobald der Verlauf einen Eintrag hatte.
        Die geschlossene Variable hier hält den Index, ohne ihn in die Signatur
        zu schreiben.
        """
        def activate(_icon, _item) -> None:
            self._copy_history_entry(index)

        return activate

    def _build_tray_menu(self) -> pystray.Menu:
        with self._history_lock:
            history_snapshot = list(self._history)

        history_items: list = []
        for idx, entry in enumerate(history_snapshot):
            try:
                stamp, preview, _full = entry
                label = f"{stamp}  —  {preview}"
            except (TypeError, ValueError):
                # Ein unbrauchbarer Eintrag kostet seine Zeile, nicht das Menü.
                logger.warning("Verlaufseintrag %d unlesbar: %r", idx, entry)
                continue
            history_items.append(pystray.MenuItem(label, self._history_action(idx)))
        if not history_items:
            history_items.append(pystray.MenuItem("Noch keine Transkripte", None, enabled=False))

        return pystray.Menu(
            pystray.MenuItem("Fenster zeigen oder ausblenden", self._toggle_visibility, default=True),
            pystray.MenuItem("Einstellungen …", self._open_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Letzte Transkripte", pystray.Menu(*history_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self._quit_app),
        )

    def _refresh_tray(self) -> None:
        """Baut das Tray-Menü neu.

        Der Aufbau steht mit im Schutzbereich: er lief bisher davor und riss bei
        einem Fehler den ganzen Tk-Rückruf mit, statt sich im Log zu melden.
        """
        if not hasattr(self, "tray_icon"):
            return
        try:
            self.tray_icon.menu = self._build_tray_menu()
            self.tray_icon.update_menu()
        except Exception:
            logger.exception("Tray-Menü konnte nicht aufgebaut werden")
        self._update_tray_tooltip()

    def _copy_history_entry(self, index: int) -> None:
        with self._history_lock:
            if index >= len(self._history):
                return
            _stamp, _preview, full = self._history[index]
        try:
            pyperclip.copy(full)
            self._ui_call(lambda: self._apply_state("done", "Aus Verlauf kopiert"))
        except pyperclip.PyperclipException:
            logger.exception("Clipboard copy from history failed")
            self._ui_call(lambda: self._apply_state(
                "error", "Zwischenablage nicht verfügbar",
                "Ein anderes Programm blockiert sie gerade. Versuch es erneut.",
            ))

    def _add_to_history(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        preview = (text[:40] + "…") if len(text) > 40 else text
        preview = preview.replace("\n", " ").replace("\r", " ")
        with self._history_lock:
            self._history.insert(0, (stamp, preview, text))
            del self._history[5:]
        self._ui_call(self._refresh_tray)

    def _set_tray_icon(self, color: str) -> None:
        # Der erste Zustand wird gesetzt, bevor das Tray existiert.
        if hasattr(self, "tray_icon"):
            self.tray_icon.icon = _create_tray_icon_image(color)

    def _hide_window(self) -> None:
        self._window_visible = False
        self._flush_position()
        self.root.withdraw()

    def _show_window(self) -> None:
        self._window_visible = True
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        uk.round_window_corners(self.root)

    def _toggle_visibility(self, icon=None, item=None) -> None:
        if self._window_visible:
            self.root.after(0, self._hide_window)
        else:
            self.root.after(0, self._show_window)
