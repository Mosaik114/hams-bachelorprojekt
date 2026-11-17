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
    # the two uninstall questions and the warning about administrator rights
    assert ISS.count("MB_YESNO or MB_DEFBUTTON2") == 3, "every question defaults to No"


def _function(name: str) -> str:
    start = ISS.index(f"function {name}(")
    return ISS[start : ISS.index("\nend;", start)]


def test_administrator_rights_are_asked_about_never_forced():
    """Without RedirectionGuard, only a setup with administrator rights would be at risk: it asks first."""
    assert "Result := AdminAllowed();" in _function("InitializeSetup")
    admin = _function("AdminAllowed")
    assert "if not IsAdmin() then" in admin
    assert "UacIsOff()" in admin  # no boundary to protect
    assert "RestartAfterUpdate()" in admin  # Hams' own update never leaves the user without Hams
    assert "{param:ALLOWADMIN|}" in admin
    assert "WizardSilent()" in admin and "Result := False" in admin
    assert "CustomMessage('AdminWarning')" in admin
    assert "english.AdminWarning=" in ISS and "german.AdminWarning=" in ISS


def test_the_redirection_guard_line_says_when_it_can_go():
    assert "RedirectionGuard=no" in ISS
    assert "Remove this line" in ISS


def test_a_test_build_has_names_of_its_own():
    """/DTestInstance: own AppId, folder, lock and autostart value, and the Hams it starts is a test instance."""
    start = ISS.index("#ifdef TestInstance")
    block = ISS[start : ISS.index("#endif", start)]
    for name in ("AppName", "AppDisplayName", "AppMutexName", "TestArgument"):
        assert f"#undef {name}" in block and f"#define {name}" in block
    assert '"Global\\Hams-SingleInstance-7a3f2c1b-test-" + TestInstance' in block
    assert '" --test-instance=" + TestInstance' in block
    ids = re.findall(r"^AppId=\{(\{[0-9A-F-]+\})$", ISS, re.MULTILINE)
    assert len(ids) == 2 and ids[0] != ids[1]
    assert "#ifndef TestInstance\nAppId={" + ids[0] in ISS  # the real AppId comes first, for winget and tests
    assert '--background{#TestArgument}"' in ISS
    assert 'Parameters: "{#TestArgument}"' in ISS
    assert "'{#TestArgument}'" in _function("RestartParameters")


def test_the_test_instance_names_match_the_app():
    from hams import instance, test_instance

    assert f'"{instance.MUTEX_NAME}-test-" + TestInstance' in ISS
    assert f'" {test_instance.ARGUMENT}=" + TestInstance' in ISS


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
