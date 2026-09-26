"""Startpunkt von Hams: Konfiguration vorbereiten, eine Instanz sichern, Fenster starten."""
import ctypes
import sys
import tkinter as tk
from pathlib import Path

# Projekt-Root in sys.path aufnehmen, damit auch der direkte Skript-Start
# (pythonw.exe wisper\main.py, z. B. aus dem Autostart-VBS) die Package-
# Imports unten findet - dort ist sys.path[0] sonst nur der wisper-Ordner.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from wisper import branding
from wisper import ui_kit as uk
from wisper.app import FloatingTranscriberApp
from wisper.config import CFG, CONFIG_PATH, _load_config, ensure_user_config, logger
from wisper.instance import _acquire_single_instance_mutex, _release_single_instance_mutex


def main() -> None:
    state = ensure_user_config()
    logger.info("Nutzerkonfiguration %s (%s)", CONFIG_PATH, state)
    if state in ("migriert", "angelegt"):
        # die eben erst entstandene Datei mitnehmen; an Ort und Stelle, weil
        # alle Module dasselbe CFG-Objekt importiert haben
        vars(CFG).update(vars(_load_config()))

    mutex_handle = _acquire_single_instance_mutex()
    if mutex_handle is None:
        logger.warning("Another Wisper instance is already running — exiting")
        if sys.platform != "win32":
            from wisper import linux
            linux.notify(branding.PRODUCT_NAME, f"{branding.PRODUCT_NAME} läuft bereits.")
            sys.exit(0)
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"{branding.PRODUCT_NAME} läuft bereits. "
                f"Überprüfe das System-Tray.",
                branding.PRODUCT_NAME,
                0x40,
            )
        except Exception:
            pass
        sys.exit(0)

    try:
        awareness = uk.enable_dpi_awareness()   # muss vor tk.Tk() stehen
        logger.info("DPI awareness: %s", awareness)
        root = tk.Tk()
        FloatingTranscriberApp(root)
        root.mainloop()
    finally:
        # Beim Neustart ist das Schloss schon freigegeben; dann ist hier nichts
        # mehr zu tun und der Nachfolger haelt es bereits.
        _release_single_instance_mutex()
    if sys.platform != "win32":
        from wisper import linux
        if linux.restart_requested:
            sys.exit(linux.RESTART_EXIT_CODE)


if __name__ == "__main__":
    main()
