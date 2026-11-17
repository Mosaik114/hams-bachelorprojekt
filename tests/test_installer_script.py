"""The Inno Setup script against the code it must agree with."""

import re
from pathlib import Path

from hams import models

ISS = (Path(__file__).resolve().parent.parent / "packaging" / "hams.iss").read_text(encoding="utf-8-sig")


def test_the_uninstaller_offers_exactly_the_catalog_models():
    """Only Hams' own folders in the shared Hugging Face cache, and every one of them."""
    folders = set(re.findall(r"'(models--[^']+)'", ISS))
    expected = {"models--" + info.repo_id.replace("/", "--") for info in models.CATALOG}
    assert folders == expected
    assert f"ModelCount = {len(expected)};" in ISS


def test_a_silent_uninstall_asks_nothing():
    assert "(CurUninstallStep = usPostUninstall) and not UninstallSilent()" in ISS
    assert ISS.count("MB_YESNO or MB_DEFBUTTON2") == 2, "both questions default to No"


def test_the_one_click_update_and_the_setup_agree(tmp_path):
    """Hams starts the setup with /RESTARTHAMS; the setup waits for exactly Hams' mutex and starts it again."""
    from hams import instance, update_install

    command = update_install.setup_command(tmp_path / "Hams-Setup-9.0.0.exe", background=True)
    assert "/RESTARTHAMS=background" in command
    assert "ExpandConstant('{param:RESTARTHAMS|}')" in ISS
    assert f'#define AppMutexName "{instance.MUTEX_NAME}"' in ISS
    assert "CheckForMutexes('{#AppMutexName}')" in ISS
    restart = next(line for line in ISS.splitlines() if "Check: RestartAfterUpdate" in line)
    assert "skipifsilent" not in restart, "the update is silent, so the restart entry must run silently too"
