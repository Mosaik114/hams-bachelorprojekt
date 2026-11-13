"""The winget manifest templates (packaging/winget) match the Inno Setup installer and the project.

`winget validate` itself runs only by hand (see CONTRIBUTING.md); here the templates are checked for what
would silently go wrong: a version, product code or file name that drifted from pyproject.toml and hams.iss.
"""

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hams.config import tomllib  # tomllib, or tomli on Python 3.10

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WINGET = ROOT / "packaging" / "winget"
TEMPLATES = WINGET / "manifest"
ISS = (ROOT / "packaging" / "hams.iss").read_text(encoding="utf-8-sig")
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def load(name: str) -> dict:
    return yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifests() -> dict:
    return {
        "version": load("Mosaik114.Hams.yaml"),
        "installer": load("Mosaik114.Hams.installer.yaml"),
        "defaultLocale": load("Mosaik114.Hams.locale.en-US.yaml"),
    }


def test_one_set_with_shared_identity(manifests):
    assert sorted(path.name for path in TEMPLATES.iterdir()) == [
        "Mosaik114.Hams.installer.yaml",
        "Mosaik114.Hams.locale.en-US.yaml",
        "Mosaik114.Hams.yaml",
    ]
    for kind, manifest in manifests.items():
        assert manifest["ManifestType"] == kind
        assert manifest["PackageIdentifier"] == "Mosaik114.Hams"
        assert manifest["PackageVersion"] == VERSION
        assert manifest["ManifestVersion"] == manifests["version"]["ManifestVersion"]


def test_schema_comments_name_the_manifest_version(manifests):
    version = manifests["version"]["ManifestVersion"]
    for path in TEMPLATES.iterdir():
        assert f".{version}.schema.json" in path.read_text(encoding="utf-8"), path.name


def test_installer_matches_hams_iss(manifests):
    installer = manifests["installer"]
    assert installer["InstallerType"] == "inno"
    assert installer["Scope"] == "user"
    assert "PrivilegesRequired=lowest" in ISS
    app_id = re.search(r"^AppId=\{(\{[0-9A-F-]+\})$", ISS, re.MULTILINE).group(1)
    assert installer["ProductCode"] == f"{app_id}_is1"
    assert installer["AppsAndFeaturesEntries"][0]["ProductCode"] == f"{app_id}_is1"
    assert "/VERYSILENT" in installer["InstallerSwitches"]["Silent"]
    assert "OutputBaseFilename=Hams-Setup-{#AppVersion}" in ISS
    (entry,) = installer["Installers"]
    assert entry["Architecture"] == "x64" and "ArchitecturesAllowed=x64compatible" in ISS
    assert entry["InstallerUrl"] == (
        f"https://github.com/Mosaik114/hams-bachelorprojekt/releases/download/v{VERSION}/Hams-Setup-{VERSION}.exe"
    )
    assert re.fullmatch(r"0{64}", entry["InstallerSha256"]), "the template keeps its placeholder"


def test_locale(manifests):
    locale = manifests["defaultLocale"]
    assert manifests["version"]["DefaultLocale"] == locale["PackageLocale"] == "en-US"
    assert locale["PackageName"] == "Hams" and locale["License"] == "MIT"
    assert locale["ReleaseNotesUrl"].endswith(f"/releases/tag/v{VERSION}")
    assert len(locale["ShortDescription"]) <= 256


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 not installed")
def test_update_script_fills_version_url_and_hash(tmp_path):
    setup = tmp_path / "Hams-Setup-9.8.7.exe"
    setup.write_bytes(b"not really an installer")
    out = tmp_path / "out"
    script = WINGET / "update-manifest.ps1"
    subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), "-Setup", str(setup), "-OutDir", str(out)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    target = out / "manifests" / "m" / "Mosaik114" / "Hams" / "9.8.7"
    installer = yaml.safe_load((target / "Mosaik114.Hams.installer.yaml").read_text(encoding="utf-8"))
    entry = installer["Installers"][0]
    assert installer["PackageVersion"] == "9.8.7"
    assert entry["InstallerSha256"] == hashlib.sha256(setup.read_bytes()).hexdigest().upper()
    assert entry["InstallerUrl"].endswith("/download/v9.8.7/Hams-Setup-9.8.7.exe")
    for name in ("Mosaik114.Hams.yaml", "Mosaik114.Hams.locale.en-US.yaml"):
        assert yaml.safe_load((target / name).read_text(encoding="utf-8"))["PackageVersion"] == "9.8.7"
    assert "placeholder" not in (target / "Mosaik114.Hams.installer.yaml").read_text(encoding="utf-8")
    assert (TEMPLATES / "Mosaik114.Hams.yaml").read_text(encoding="utf-8").count(f"PackageVersion: {VERSION}") == 1
