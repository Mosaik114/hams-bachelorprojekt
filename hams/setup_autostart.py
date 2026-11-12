"""
Einmalig ausführen, um Hams beim Login automatisch zu starten.
Startet im Hintergrund ohne Konsolenfenster.

Unter Linux legt es zusätzlich den Startmenü-Eintrag an; über dessen Namen
ordnet KDE die Kurzbefehle aus dem Portal dem Programm zu.

Autostart entfernen:
    python setup_autostart.py --remove
"""

import shutil
import sys
from pathlib import Path

if sys.platform == "win32":
    import winreg

REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Hams"
#: Eintrag aus der Zeit, als das Programm „Wisper“ hiess. Beim Einrichten wird
#: er durch `APP_NAME` ersetzt, damit beim Login nicht zwei Fassungen um die
#: Einzelinstanz wetteifern; `--remove` entfernt beide.
LEGACY_APP_NAME = "Wisper"

# Projekt-Root = eine Ebene ueber dem hams-Package; das VBS liegt in scripts/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VBS_PATH = PROJECT_ROOT / "scripts" / "start_hams.vbs"


def _resolve_launch_command() -> str:
    """Bevorzugt das VBS-Skript (kein Konsolenfenster). Falls nicht vorhanden,
    nutze pythonw.exe aus .venv, sonst system-pythonw.exe."""
    if VBS_PATH.exists():
        return f'wscript.exe //B "{VBS_PATH}"'

    venv_pythonw = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    main_py = PROJECT_ROOT / "hams" / "main.py"
    pythonw = str(venv_pythonw) if venv_pythonw.exists() else "pythonw.exe"
    return f'"{pythonw}" "{main_py}"'


def _delete_value(key, name: str) -> bool:
    """Entfernt einen Run-Eintrag. False, wenn es ihn nicht gab."""
    try:
        winreg.DeleteValue(key, name)
    except FileNotFoundError:
        return False
    return True


def add_autostart() -> None:
    cmd = _resolve_launch_command()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        replaced = _delete_value(key, LEGACY_APP_NAME)
    print("Autostart eingerichtet.")
    print(f"Befehl: {cmd}")
    if replaced:
        print(f"Der alte Eintrag „{LEGACY_APP_NAME}“ wurde ersetzt.")
    print("Hams startet ab sofort automatisch beim Windows-Login.")


def remove_autostart() -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE) as key:
        removed = [name for name in (APP_NAME, LEGACY_APP_NAME) if _delete_value(key, name)]
    print("Autostart entfernt." if removed else "Kein Autostart-Eintrag gefunden.")


# ---------------------------------------------------------------- Linux


def _linux_paths() -> dict:
    from hams import linux

    data = Path.home() / ".local" / "share"
    return {
        "menu": data / "applications" / f"{linux.APP_ID}.desktop",
        "icon": data / "icons" / "hicolor" / "256x256" / "apps" / f"{linux.APP_ID}.png",
        # früherer Weg, wird beim Einrichten des Dienstes entfernt
        "autostart": linux.autostart_dir() / f"{linux.APP_ID}.desktop",
        "service": Path.home() / ".local" / "share" / "systemd" / "user" / linux.SERVICE,
    }


def _systemctl(*args: str) -> None:
    import subprocess

    subprocess.run(["systemctl", "--user", *args], check=True)


def add_autostart_linux() -> None:
    """Hams als Benutzerdienst: startet mit der Sitzung und nach einem Absturz neu.

    Der Startmenü-Eintrag startet den Dienst, statt Hams daneben ein zweites
    Mal zu starten.
    """
    from hams import linux

    paths = _linux_paths()
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    main_py = PROJECT_ROOT / "hams" / "main.py"
    paths["icon"].parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PROJECT_ROOT / "hams" / "assets" / "branding" / "hams_app_icon.png", paths["icon"])
    paths["menu"].parent.mkdir(parents=True, exist_ok=True)
    paths["menu"].write_text(
        linux.desktop_entry(f"systemctl --user start {linux.SERVICE}", linux.APP_ID), encoding="utf-8"
    )
    paths["service"].parent.mkdir(parents=True, exist_ok=True)
    paths["service"].write_text(linux.service_unit(str(python), str(main_py)), encoding="utf-8")
    paths["autostart"].unlink(missing_ok=True)
    _systemctl("daemon-reload")
    _systemctl("enable", linux.SERVICE)
    print(f"Dienst {linux.SERVICE} eingerichtet (startet mit der Sitzung und nach Abstürzen).")
    print(f"Befehl: {python} {main_py}")


def remove_autostart_linux() -> None:
    from hams import linux

    paths = _linux_paths()
    if paths["service"].exists():
        _systemctl("disable", linux.SERVICE)
        paths["service"].unlink()
        _systemctl("daemon-reload")
    paths["autostart"].unlink(missing_ok=True)
    if "--alles" in sys.argv:
        paths["menu"].unlink(missing_ok=True)
        paths["icon"].unlink(missing_ok=True)
    print("Autostart entfernt.")


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.path.insert(0, str(PROJECT_ROOT))
        if "--remove" in sys.argv:
            remove_autostart_linux()
        else:
            add_autostart_linux()
    elif "--remove" in sys.argv:
        remove_autostart()
    else:
        add_autostart()
