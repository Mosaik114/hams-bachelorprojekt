<#
.SYNOPSIS
    Fills the winget manifest templates with the version, download URL and SHA-256 of a built installer.

.DESCRIPTION
    Reads the three templates in packaging\winget\manifest (version, installer, defaultLocale), replaces
    PackageVersion, InstallerUrl, InstallerSha256 and ReleaseNotesUrl, and writes the result in the
    folder layout of microsoft/winget-pkgs:

        <OutDir>\manifests\m\Mosaik114\Hams\<version>\Mosaik114.Hams*.yaml

    The templates themselves are not changed. The hash must be the one of the file that is attached to
    the GitHub release, so run this on the installer downloaded from the release (or on the identical
    CI artifact), not on a local rebuild.

.EXAMPLE
    .\packaging\winget\update-manifest.ps1 -Setup build\installer\Hams-Setup-1.1.0.exe -Validate
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [string]$Version = "",
    [string]$Url = "",
    [string]$OutDir = "",
    [switch]$Validate
)

$ErrorActionPreference = "Stop"
$Templates = Join-Path $PSScriptRoot "manifest"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Repository = "https://github.com/Mosaik114/hams-bachelorprojekt"

if (-not (Test-Path -LiteralPath $Setup -PathType Leaf)) { throw "Installer not found: $Setup" }
$SetupItem = Get-Item -LiteralPath $Setup

if (-not $Version) {
    if ($SetupItem.Name -match '^Hams-Setup-(?<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\.exe$') {
        $Version = $Matches.version
    } else {
        throw "Can't read the version from '$($SetupItem.Name)'; pass -Version."
    }
}
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') { throw "Not a version: $Version" }
if (-not $Url) { $Url = "$Repository/releases/download/v$Version/Hams-Setup-$Version.exe" }
if (-not $OutDir) { $OutDir = [System.IO.Path]::Combine($Root, "build", "winget") }

$Sha256 = (Get-FileHash -LiteralPath $SetupItem.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
$Target = [System.IO.Path]::Combine($OutDir, "manifests", "m", "Mosaik114", "Hams", $Version)
New-Item -ItemType Directory -Force -Path $Target | Out-Null

$Replacements = [ordered]@{
    '(?m)^PackageVersion: .*$'       = "PackageVersion: $Version"
    '(?m)^(\s*)InstallerUrl: .*$'    = "`${1}InstallerUrl: $Url"
    '(?m)^(\s*)InstallerSha256: .*$' = "`${1}InstallerSha256: $Sha256"
    '(?m)^ReleaseNotesUrl: .*$'      = "ReleaseNotesUrl: $Repository/releases/tag/v$Version"
    # The template warns about its placeholder hash; the filled copy has a real one.
    '(?m)^# The all-zero InstallerSha256.*\r?\n' = ""
}

foreach ($Template in Get-ChildItem -LiteralPath $Templates -Filter "Mosaik114.Hams*.yaml") {
    $Text = [System.IO.File]::ReadAllText($Template.FullName)
    foreach ($Pattern in $Replacements.Keys) { $Text = $Text -replace $Pattern, $Replacements[$Pattern] }
    $Destination = Join-Path $Target $Template.Name
    [System.IO.File]::WriteAllText($Destination, $Text, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Wrote $Destination"
}
Write-Host "Hams $Version  SHA256 $Sha256"
Write-Host "URL  $Url"

if ($Validate) {
    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $Winget) { throw "winget not found; validate the folder on a machine with winget." }
    & $Winget.Source validate --manifest $Target
    if ($LASTEXITCODE -ne 0) { throw "winget validate failed with exit code $LASTEXITCODE" }
}
