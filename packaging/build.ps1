<#
.SYNOPSIS
    Builds the Windows app (PyInstaller, onedir) and, if Inno Setup is available, the installer.

.DESCRIPTION
    Output:
        build\dist\Hams\Hams.exe              the frozen app (one folder)
        build\installer\Hams-Setup-<ver>.exe  the installer (with -Installer)

    The version comes from pyproject.toml. The Python environment must already contain
    the locked dependencies, or pass -InstallDependencies:

        pip install --require-hashes -r requirements.txt
        pip install --require-hashes -r packaging\requirements-build.txt
        pip install --no-deps -e .

    Do not install the [cuda] extra into the build environment: the CUDA libraries are
    downloaded by the app on demand (hams\gpu_support.py) and must not be bundled.

.EXAMPLE
    .\packaging\build.ps1 -InstallDependencies -Installer
#>
[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$InstallDependencies,
    [switch]$Installer,
    [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

function Find-InnoCompiler {
    if ($InnoCompiler) { return $InnoCompiler }
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    throw "Inno Setup 6 (ISCC.exe) not found. Install it or pass -InnoCompiler."
}

$Version = & $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0 -or -not $Version) { throw "Could not read the version from pyproject.toml" }
Write-Host "Hams $Version"

if ($InstallDependencies) {
    Invoke-Checked $Python @("-m", "pip", "install", "--require-hashes", "-r", "requirements.txt")
    Invoke-Checked $Python @("-m", "pip", "install", "--require-hashes", "-r", "packaging\requirements-build.txt")
    Invoke-Checked $Python @("-m", "pip", "install", "--no-deps", "-e", ".")
}

# A leftover CUDA install in the build environment would end up in the bundle.
& $Python -c "import importlib.util, sys; sys.exit(1 if importlib.util.find_spec('nvidia') else 0)"
if ($LASTEXITCODE -ne 0) { throw "The build environment contains the nvidia packages; use a clean venv without [cuda]." }

Invoke-Checked $Python @(
    "-m", "PyInstaller", "packaging\hams.spec",
    "--noconfirm", "--clean",
    "--distpath", "build\dist",
    "--workpath", "build\pyinstaller"
)

$AppDir = Join-Path $Root "build\dist\Hams"
$Size = (Get-ChildItem $AppDir -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("App folder: {0} ({1:N0} MB)" -f $AppDir, ($Size / 1MB))

if ($Installer) {
    $Iscc = Find-InnoCompiler
    Write-Host "Inno Setup: $Iscc"
    Invoke-Checked $Iscc @(
        "/Qp",
        "/DAppVersion=$Version",
        "/DSourceDir=$AppDir",
        "/DOutputDir=$(Join-Path $Root 'build\installer')",
        "packaging\hams.iss"
    )
    $Setup = Join-Path $Root "build\installer\Hams-Setup-$Version.exe"
    Write-Host ("Installer: {0} ({1:N0} MB)" -f $Setup, ((Get-Item $Setup).Length / 1MB))
}
