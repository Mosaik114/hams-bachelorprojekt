# PyInstaller spec for the Windows app: one folder (onedir), no console window.
#
# Build with packaging/build.ps1, which passes the version from pyproject.toml.
# Output: build/dist/Hams/Hams.exe plus _internal/ (Python, ctranslate2, Tcl/Tk, ...).
#
# Deliberately NOT bundled: the CUDA libraries (cuBLAS/cuDNN, ~1.9 GB). The app
# downloads them on demand on machines with an NVIDIA GPU (hams/gpu_support.py).
# torch/transformers are excluded too: faster-whisper runs without them.

import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller
PACKAGE = ROOT / "hams"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
NUMERIC = tuple(int(part) for part in (VERSION.split("+")[0].split(".") + ["0"] * 4)[:4])

datas = [
    (str(PACKAGE / "config.default.toml"), "hams"),
    (str(PACKAGE / "glossar.default.toml"), "hams"),
    (str(PACKAGE / "glossar.default.en.toml"), "hams"),
    (str(PACKAGE / "prompts"), "hams/prompts"),
    (str(PACKAGE / "assets" / "branding"), "hams/assets/branding"),
]
datas += collect_data_files("faster_whisper")  # Silero VAD model
for distribution in ("hams", "faster-whisper", "ctranslate2", "pystray", "sounddevice", "onnxruntime"):
    datas += copy_metadata(distribution)

# Linux-only modules are imported by sys.platform switches; on Windows they would only add weight.
hiddenimports = [
    name
    for name in collect_submodules("hams")
    if name not in ("hams.linux", "hams.portal_hotkeys", "hams.setup_autostart")
]
hiddenimports += ["pystray._win32"]

excludes = [
    "torch",
    "transformers",
    "tensorflow",
    "nvidia",
    "matplotlib",
    "IPython",
    "pytest",
    "sympy",
    "mpmath",
    "ctranslate2.converters",
]

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMERIC, prodvers=NUMERIC),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Hams"),
                        StringStruct("FileDescription", "Hams - local voice input"),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("InternalName", "Hams"),
                        StringStruct("OriginalFilename", "Hams.exe"),
                        StringStruct("ProductName", "Hams"),
                        StringStruct("ProductVersion", VERSION),
                        StringStruct("LegalCopyright", "MIT License"),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "hams_launcher.py")],
    pathex=[str(ROOT)],
    binaries=collect_dynamic_libs("ctranslate2"),
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Hams",
    icon=str(PACKAGE / "assets" / "branding" / "hams.ico"),
    version=version_info,
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Hams",
)
