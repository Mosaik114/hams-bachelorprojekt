"""Startpunkt von Hams: Konfiguration vorbereiten, eine Instanz sichern, Fenster starten."""

import ctypes
import sys
import threading
import tkinter as tk
from pathlib import Path

# Projekt-Root in sys.path aufnehmen, damit auch der direkte Skript-Start
# (pythonw.exe hams\main.py, z. B. aus scripts\start_hams.vbs) die Package-
# Imports unten findet - dort ist sys.path[0] sonst nur der hams-Ordner.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import contextlib

from hams import branding, config, i18n, setup_wizard
from hams import ui_kit as uk
from hams.activation import SETTINGS_EVENT, SHOW_EVENT, SettingsRequests, request_settings, request_show
from hams.app import FloatingTranscriberApp
from hams.config import CFG, CONFIG_CREATED, CONFIG_MIGRATED, CONFIG_PATH, _load_config, ensure_user_config, logger
from hams.instance import _acquire_single_instance_mutex, _release_single_instance_mutex


def _second_instance(show_settings: bool) -> None:
    """Hams läuft schon: dort Einstellungen oder Hauptfenster zeigen lassen; nur notfalls Bescheid geben."""
    if sys.platform == "win32":
        _wake_running_instance(show_settings)
        return
    logger.warning("Another Hams instance is already running — exiting")
    from hams import linux

    if not linux.request_show():
        linux.notify(branding.PRODUCT_NAME, i18n.t("instance.already_running", product=branding.PRODUCT_NAME))
    sys.exit(0)


def _warn_about_broken_config() -> None:
    """Die Nutzerdatei war unlesbar: sagen, dass die Voreinstellungen gelten und wo die Kopie liegt.

    Ohne diesen Hinweis saehe der Nutzer nur, dass alle Einstellungen
    verschwunden sind. Die Meldung blockiert den Start nicht.
    """
    broken = getattr(config, "BROKEN_CONFIG", None)
    if broken is None:
        return
    message = i18n.t(
        "config.broken",
        product=branding.PRODUCT_NAME,
        path=broken.path,
        error=broken.error,
        backup=broken.backup or "-",
    )
    if sys.platform != "win32":
        from hams import linux

        linux.notify(branding.PRODUCT_NAME, message)
        return

    def show() -> None:
        with contextlib.suppress(Exception):
            ctypes.windll.user32.MessageBoxW(0, message, branding.PRODUCT_NAME, 0x30)  # MB_ICONWARNING

    threading.Thread(target=show, name="config-warning", daemon=True).start()


def _wake_running_instance(show_settings: bool) -> None:
    """Windows: Anfrage an die laufende Instanz; die Meldung kommt nur, wenn niemand antwortet."""
    if show_settings:
        if request_settings():
            return
        key = "instance.restart_for_settings"
    else:
        if request_show():
            logger.info("Another Hams instance is already running — asked it to show its window")
            return
        logger.warning("Another Hams instance is already running and did not answer")
        key = "instance.already_running_tray"
    with contextlib.suppress(Exception):
        ctypes.windll.user32.MessageBoxW(0, i18n.t(key, product=branding.PRODUCT_NAME), branding.PRODUCT_NAME, 0x40)


def _bring_to_front(app: FloatingTranscriberApp) -> None:
    """Zweiter Start: Hauptfenster zeigen; ein offener Assistent kommt obendrauf."""
    app._show_window()
    setup_win = getattr(app, "setup_win", None)
    if setup_win is not None and setup_win.alive():
        setup_win.focus()


def _accept_requests(root: tk.Tk, app: FloatingTranscriberApp) -> list[SettingsRequests]:
    """Unter Windows: eine zweite Instanz (Hams.exe) darf Einstellungen oder Hauptfenster der laufenden öffnen."""
    if sys.platform != "win32":
        return []
    receivers = []
    for name, callback in ((SETTINGS_EVENT, app._open_settings), (SHOW_EVENT, lambda: _bring_to_front(app))):
        try:
            receivers.append(SettingsRequests(root, callback, name=name))
        except OSError:
            logger.exception("Aktivierungs-Ereignis %s konnte nicht verbunden werden", name)
    logger.info("Activation requests accepted (%d)", len(receivers))
    return receivers


def main(*, show_settings: bool = False, show_setup: bool = False) -> None:
    state = ensure_user_config()
    logger.info("Nutzerkonfiguration %s (%s)", CONFIG_PATH, state)
    if state in (CONFIG_MIGRATED, CONFIG_CREATED):
        # die eben erst entstandene Datei mitnehmen; an Ort und Stelle, weil
        # alle Module dasselbe CFG-Objekt importiert haben
        vars(CFG).update(vars(_load_config()))
    # Vor jedem sichtbaren Text, auch vor der Meldung an eine zweite Instanz.
    logger.info("UI language: %s (setting %s)", i18n.set_language(CFG.ui_language), CFG.ui_language)

    mutex_handle = _acquire_single_instance_mutex()
    if mutex_handle is None:
        _second_instance(show_settings)
        return

    receivers: list[SettingsRequests] = []
    _warn_about_broken_config()
    # Nach dem Schloss: eine zweite Instanz darf die Markierung nicht anlegen.
    setup = show_setup or setup_wizard.needs_setup(state, setup_wizard.marker_path(CONFIG_PATH))
    try:
        awareness = uk.enable_dpi_awareness()  # muss vor tk.Tk() stehen
        logger.info("DPI awareness: %s", awareness)
        root = tk.Tk()
        app = FloatingTranscriberApp(root, setup=setup)
        receivers = _accept_requests(root, app)
        if setup:
            root.after_idle(app._open_setup)
        elif show_settings:
            root.after_idle(app._open_settings)
        root.mainloop()
    finally:
        for receiver in receivers:
            receiver.close()
        # Beim Neustart ist das Schloss schon freigegeben; dann ist hier nichts
        # mehr zu tun und der Nachfolger haelt es bereits.
        _release_single_instance_mutex()
    if sys.platform != "win32":
        from hams import linux

        if linux.restart_requested:
            sys.exit(linux.RESTART_EXIT_CODE)


if __name__ == "__main__":
    main(show_settings="--settings" in sys.argv[1:], show_setup="--setup" in sys.argv[1:])
