"""Tray-Symbol, Verlauf und Ein-/Ausblenden des Fensters."""

import sys
import threading
import tkinter as tk
from datetime import datetime

import pyperclip
import pystray
from PIL import Image, ImageTk

from hams import branding, support
from hams import ui_kit as uk
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import t
from hams.ui_kit import Theme

#: Farben des Tray-Symbols je **Taskleisten**-Thema, nicht nach dem Hams-Thema: Fenster und Taskleiste sind
#: zwei Oberflaechen. Auf der dunklen Leiste die leuchtenden Toene, auf der hellen (#F3F3F3) die dunklen
#: Fuellungen, damit das Symbol dort mindestens 3:1 erreicht (vorher 1,98:1). Ein Fehler hat einen eigenen
#: Ton (Bernstein): vorher war er vom Aufnehmen nicht zu unterscheiden.
TRAY_COLOURS = {
    "dark": {
        "ACCENT_TINT": Theme.palette(Theme.DARK)["ACCENT_TINT"],
        "RECORDING": Theme.palette(Theme.DARK)["RECORDING"],
        "AI": Theme.palette(Theme.DARK)["AI"],
        "ERROR": Theme.palette(Theme.DARK)["WARNING"],
    },
    "light": {
        "ACCENT_TINT": Theme.palette(Theme.LIGHT)["ACCENT_SURFACE"],
        "RECORDING": Theme.palette(Theme.LIGHT)["RECORDING"],
        "AI": Theme.palette(Theme.LIGHT)["AI"],
        "ERROR": Theme.palette(Theme.LIGHT)["WARNING"],
    },
}


def taskbar_is_light() -> bool:
    """Ob die Windows-Taskleiste hell ist (`SystemUsesLightTheme`); sonst und ausserhalb von Windows: dunkel."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except OSError:
        return False


def tray_palette() -> dict:
    return TRAY_COLOURS["light" if taskbar_is_light() else "dark"]


TRAY_PALETTE = tray_palette()


def _create_tray_icon_image(color: str | None = None) -> Image.Image:
    """Das Tray-Symbol im aktuellen Zustand, in allen Groessen (`hams_frames`).

    Die Form ist immer die Hams-Bildmarke, die Farbe kommt aus dem Zustand. Die kleinen Groessen kommen aus
    ihren eigens gezeichneten Vorlagen; `_SmallIconTray` reicht sie an Windows weiter.
    """
    frames = branding.tray_images(color or tray_palette()["ACCENT_TINT"])
    image = frames[max(frames)]
    image.hams_frames = frames
    return image


if sys.platform == "win32":  # pragma: no branch - the tray class differs per system

    class _SmallIconTray(pystray.Icon):
        """pystray laedt das Symbol in der grossen Standardgroesse; Windows rechnete die Tray-Groesse daraus
        herunter. Hier: alle gezeichneten Groessen in die ICO-Datei und genau die Tray-Groesse laden."""

        def _assert_icon_handle(self):  # pragma: no cover - needs the Windows shell
            if self._icon_handle:
                return
            frames = getattr(self.icon, "hams_frames", None)
            if not frames:
                super()._assert_icon_handle()
                return
            import ctypes
            import os
            import tempfile

            from pystray._util import win32

            size = ctypes.windll.user32.GetSystemMetrics(49)  # SM_CXSMICON: 16, 20, 24 ... je Skalierung
            handle, path = tempfile.mkstemp(".ico")
            os.close(handle)
            try:
                branding.write_tray_ico(frames, path)
                self._icon_handle = win32.LoadImage(None, path, win32.IMAGE_ICON, size, size, win32.LR_LOADFROMFILE)
            finally:
                os.unlink(path)

    TrayIcon = _SmallIconTray
else:  # pragma: no cover - Linux
    TrayIcon = pystray.Icon


#: Tk interpreters (`id(root.tk)`) whose default window icon is already set, see `_apply_window_icon`.
_DEFAULT_ICON_SET: set[int] = set()


class TrayMixin:
    """Tray-Symbol, Verlauf und Ein-/Ausblenden des Fensters."""

    # ------------------------------------------------------------------ tray

    def _setup_tray(self) -> None:
        image = _create_tray_icon_image()
        self.tray_icon = TrayIcon(branding.PRODUCT_NAME, image, self._tray_tooltip(), self._build_tray_menu())
        threading.Thread(target=self.tray_icon.run, daemon=True, name="tray").start()

    def _apply_window_icon(self) -> None:
        """Setzt das Anwendungssymbol auf dem vorhandenen Tk-Weg.

        Beides, weil beides gebraucht wird: `iconbitmap(default=...)` reicht das
        ICO mit allen Groessen an Windows durch (Taskleiste, Alt+Tab), und
        `iconphoto` versorgt Tk-eigene Fenster, die kein ICO annehmen. Ein
        randloses Fenster zeigt selbst kein Symbol — deshalb keine zusaetzliche
        native Schicht dafuer.
        """
        icon = branding.icon_path() if sys.platform == "win32" else None
        interpreter = id(self.root.tk)
        if icon is not None and interpreter not in _DEFAULT_ICON_SET:
            # Once per Tk interpreter: `default=` applies to every later window, and Tk does not free the icons of
            # an earlier call (measured: about 25 GDI and 9 USER handles per call, against a limit of 10,000).
            try:
                self.root.iconbitmap(default=str(icon))
                _DEFAULT_ICON_SET.add(interpreter)
            except tk.TclError:  # pragma: no cover - ICO nicht annehmbar
                logger.warning("Window icon %s refused", icon, exc_info=True)
        image = branding.app_icon_image(64) or _create_tray_icon_image()
        self._tk_icon = ImageTk.PhotoImage(image)
        self.root.iconphoto(True, self._tk_icon)

    def _tray_tooltip(self) -> str:
        product = branding.PRODUCT_NAME
        if not self.models.model_ready.is_set():
            return t("tray.tooltip.loading", product=product)
        if self.is_recording:
            return t("tray.tooltip.prompt" if self._prompt_mode else "tray.tooltip.recording", product=product)
        with self._history_lock:
            last = self._history[0] if self._history else None
        if last is not None:
            return t("tray.tooltip.last", product=product, preview=last[1])
        return t("tray.tooltip.ready", product=product)

    def _update_tray_tooltip(self) -> None:
        if self.tray_icon is not None:
            self.tray_icon.title = self._tray_tooltip()

    def _history_action(self, index: int):
        """Rückruf für einen Verlaufseintrag — mit genau zwei Parametern.

        pystray liest die Zahl der Positionsparameter aus `co_argcount` und
        lässt nur 0, 1 oder 2 zu; alles andere quittiert es mit `ValueError`.
        Der naheliegende Trick mit Vorgabewerten

            lambda _icon=None, _item=None, i=idx: ...

        zählt drei — Vorgabewerte ändern daran nichts, und das Tray-Menü liesse
        sich nicht mehr aufbauen, sobald der Verlauf einen Eintrag hat.
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
                logger.warning("History entry %d unreadable: %r", idx, entry)
                continue
            history_items.append(pystray.MenuItem(label, self._history_action(idx)))
        if not history_items:
            history_items.append(pystray.MenuItem(t("tray.history_empty"), None, enabled=False))

        update_items = self.updates.menu_items(pystray.MenuItem)
        if update_items:
            update_items.append(pystray.Menu.SEPARATOR)
        return pystray.Menu(
            *update_items,
            pystray.MenuItem(t("tray.toggle_window"), self._toggle_visibility, default=True),
            pystray.MenuItem(t("tray.settings"), self._open_settings),
            pystray.MenuItem(t("tray.setup"), self._open_setup),
            pystray.MenuItem(t("tray.help"), support.open_user_guide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray.history"), pystray.Menu(*history_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray.quit"), self._quit_app),
        )

    def _refresh_tray(self) -> None:
        """Baut das Tray-Menü neu.

        Der Aufbau steht mit im Schutzbereich: ein Fehler darin meldet sich im
        Log, statt den ganzen Tk-Rückruf mitzureissen.
        """
        if self.tray_icon is None:
            return
        try:
            self.tray_icon.menu = self._build_tray_menu()
            self.tray_icon.update_menu()
        except Exception:
            logger.exception("Tray menu could not be built")
        self._update_tray_tooltip()

    def _copy_history_entry(self, index: int) -> None:
        with self._history_lock:
            if index >= len(self._history):
                return
            _stamp, _preview, full = self._history[index]
        try:
            pyperclip.copy(full)
            self.ui_call(lambda: self.apply_state("done", t("status.copied_from_history")))
        except pyperclip.PyperclipException:
            logger.exception("Clipboard copy from history failed")
            self.ui_call(
                lambda: self.apply_state(
                    "error",
                    t("error.clipboard_unavailable"),
                    t("error.clipboard_blocked"),
                )
            )

    def _add_to_history(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        preview = (text[:40] + "…") if len(text) > 40 else text
        preview = preview.replace("\n", " ").replace("\r", " ")
        with self._history_lock:
            self._history.insert(0, (stamp, preview, text))
            del self._history[5:]
        self.ui_call(self._refresh_tray)

    def _set_tray_icon(self, color: str) -> None:
        # The first state is shown before the tray exists.
        if self.tray_icon is not None:
            self.tray_icon.icon = _create_tray_icon_image(color)

    def _hide_window(self) -> None:
        self._window_visible = False
        self._flush_position()
        self.root.withdraw()
        self._explain_tray_once()

    def _explain_tray_once(self) -> None:
        """The first time the window goes away, say where Hams is now: the tray icon is easy to miss.

        Once per installation (`[ui] tray_hint_shown`). A tray without notifications (Linux AppIndicator)
        says nothing and asks again next time.
        """
        if CFG.tray_hint_shown:
            return
        icon = self.tray_icon
        if icon is None or not getattr(icon, "HAS_NOTIFICATION", False):  # pystray backends differ
            return
        try:
            icon.notify(t("tray.hidden.body"), t("tray.hidden.title", product=branding.PRODUCT_NAME))
        except Exception:  # noqa: BLE001 - nur ein Hinweis
            logger.exception("Tray notification failed")
            return
        CFG.tray_hint_shown = True
        _persist_config_value("ui", "tray_hint_shown", True)

    def _show_window(self) -> None:
        self._window_visible = True
        uk.bring_to_front(self.root)
        uk.round_window_corners(self.root)

    def _toggle_visibility(self, icon=None, item=None) -> None:
        """Tray entry and double click - on pystray's thread, so the decision happens on the Tk thread too."""
        self.ui_call(self._toggle_window)

    def _toggle_window(self) -> None:
        if self._window_visible:
            self._hide_window()
        else:
            self._show_window()
