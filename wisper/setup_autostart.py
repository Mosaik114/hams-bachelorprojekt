"""
Einmalig ausführen, um Wisper beim Windows-Login automatisch zu starten.
Startet im Hintergrund ohne Konsolenfenster.

Autostart entfernen:
    python setup_autostart.py --remove
"""

import sys
import winreg
from pathlib import Path

REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Wisper"

# Projekt-Root = eine Ebene ueber dem wisper-Package (dort liegt das VBS).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VBS_PATH = PROJECT_ROOT / "start_wisper.vbs"


def _resolve_launch_command() -> str:
    """Bevorzugt das VBS-Skript (kein Konsolenfenster). Falls nicht vorhanden,
    nutze pythonw.exe aus .venv, sonst system-pythonw.exe."""
    if VBS_PATH.exists():
        return f'wscript.exe //B "{VBS_PATH}"'

    venv_pythonw = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    main_py = PROJECT_ROOT / "wisper" / "main.py"
    pythonw = str(venv_pythonw) if venv_pythonw.exists() else "pythonw.exe"
    return f'"{pythonw}" "{main_py}"'


def add_autostart() -> None:
    cmd = _resolve_launch_command()
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
    print("Autostart eingerichtet.")
    print(f"Befehl: {cmd}")
    print("Wisper startet ab sofort automatisch beim Windows-Login.")


def remove_autostart() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
        print("Autostart entfernt.")
    except FileNotFoundError:
        print("Kein Autostart-Eintrag gefunden.")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove_autostart()
    else:
        add_autostart()
