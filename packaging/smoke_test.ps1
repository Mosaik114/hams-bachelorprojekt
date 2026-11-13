<#
.SYNOPSIS
    Installs Hams silently, runs Hams.exe --self-test, uninstalls, and checks that nothing is left.

.DESCRIPTION
    Meant for CI (a throwaway Windows runner). It creates a real Start menu entry and a
    real autostart entry for the current user, so do not run it on your own machine.
    Logs go to build\smoke\.

.EXAMPLE
    .\packaging\smoke_test.ps1 -Setup build\installer\Hams-Setup-1.1.1.exe
#>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Setup)

$ErrorActionPreference = "Stop"
$Setup = (Resolve-Path $Setup).Path
$AppDir = Join-Path $env:LOCALAPPDATA "Programs\Hams"
$Exe = Join-Path $AppDir "Hams.exe"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$Shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Hams.lnk"
$Logs = Join-Path (Get-Location) "build\smoke"
New-Item -ItemType Directory -Force $Logs | Out-Null

function Assert-That([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FAILED: $Message" }
    Write-Host "ok   $Message"
}

function Get-Autostart {
    (Get-ItemProperty $RunKey -Name Hams -ErrorAction SilentlyContinue).Hams
}

function Get-FileList([string]$Folder) {
    Get-ChildItem $Folder -Recurse -File | ForEach-Object { $_.FullName.Substring($Folder.Length) } | Sort-Object
}

if (Test-Path $AppDir) { throw "$AppDir exists already; this test needs a clean machine." }

# --- install -----------------------------------------------------------------------------
Write-Host "Installing $Setup"
$arguments = "/VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART /TASKS=autostart /LOG=`"$Logs\install.log`""
$process = Start-Process $Setup -ArgumentList $arguments -Wait -PassThru
Assert-That ($process.ExitCode -eq 0) "setup exits with 0 (got $($process.ExitCode))"
Assert-That (Test-Path $Exe) "Hams.exe installed per user in $AppDir"
Assert-That (Test-Path $Shortcut) "Start menu shortcut exists"
Assert-That ((Get-Autostart) -eq "`"$Exe`"") "autostart entry points at Hams.exe"
$installed = Get-FileList $AppDir
Write-Host ("Installed: {0} files, {1:N0} MB" -f $installed.Count, ((Get-ChildItem $AppDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))

# --- self-test ---------------------------------------------------------------------------
$report = Join-Path $Logs "self-test.txt"
$process = Start-Process $Exe -ArgumentList "--self-test --report `"$report`"" -Wait -PassThru
if (Test-Path $report) { Get-Content $report | Write-Host }
Assert-That ($process.ExitCode -eq 0) "Hams.exe --self-test exits with 0 (got $($process.ExitCode))"
Assert-That ((Get-Content $report -Raw) -match "self-test passed") "self-test report says passed"
$after = Get-FileList $AppDir
$new = Compare-Object $installed $after | Where-Object SideIndicator -eq "=>" | ForEach-Object InputObject
Assert-That (-not $new) "nothing was written into the install folder $(($new | Select-Object -First 5) -join ', ')"
Assert-That (Test-Path (Join-Path $env:LOCALAPPDATA "Hams\logs\hams.log")) "the log is in %LOCALAPPDATA%\Hams\logs"

# --- uninstall ---------------------------------------------------------------------------
$uninstaller = Join-Path $AppDir "unins000.exe"
Assert-That (Test-Path $uninstaller) "uninstaller exists"
$arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$Logs\uninstall.log`""
Start-Process $uninstaller -ArgumentList $arguments -Wait | Out-Null
# The uninstaller hands over to a copy of itself in %TEMP% so it can delete its own file;
# the original returns before the work is done.
$deadline = (Get-Date).AddSeconds(120)
while ((Test-Path $AppDir) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
Assert-That (-not (Test-Path $AppDir)) "install folder removed"
Assert-That (-not (Test-Path $Shortcut)) "Start menu shortcut removed"
Assert-That ($null -eq (Get-Autostart)) "autostart entry removed"
Assert-That (Test-Path (Join-Path $env:LOCALAPPDATA "Hams")) "silent uninstall keeps the user's data (offered, not forced)"
Write-Host "Smoke test passed."
