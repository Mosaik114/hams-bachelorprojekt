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
