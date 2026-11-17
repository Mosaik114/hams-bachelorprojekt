<#
.SYNOPSIS
    Installs Hams silently, runs Hams.exe --self-test, runs the silent one-click update (Hams must come back without
    Windows' RedirectionGuard, also when the program that started the update had it), uninstalls, and checks that
    nothing is left.

.DESCRIPTION
    Meant for CI (a throwaway Windows runner). With the defaults it creates a real Start menu entry and a real
    autostart entry for the current user, so do not run it that way on your own machine.

    On a developer machine, test a setup built as a test instance instead and pass the same name here:

        .\packaging\build.ps1 -Installer -TestInstance smoke
        .\packaging\smoke_test.ps1 -Setup build\installer-test\Hams-Setup-1.3.6.exe -TestInstance smoke

    That setup has its own AppId, program folder, Start menu entry and autostart value, and every Hams it starts runs
    as that test instance (hams\test_instance.py): own settings, data, model cache, lock and events, and no hotkeys.
    The real Hams of the machine, running or not, is never touched. The test removes the test instance's folders at
    the end. Logs go to build\smoke\.

    The step "update started by a program with RedirectionGuard" needs the Windows shell (Explorer), which a CI
    runner or an invisible desktop may not have; there it is skipped with a note. Hams then simply keeps running
    with the mitigation (hams\redirection_guard.py), which the step reports as well.

.EXAMPLE
    .\packaging\smoke_test.ps1 -Setup build\installer\Hams-Setup-1.3.6.exe
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [string]$TestInstance = "",
    [string]$Logs = (Join-Path (Get-Location) "build\smoke")
)

$ErrorActionPreference = "Stop"
$Setup = (Resolve-Path $Setup).Path
if ($TestInstance) {
    $AppName = "Hams-test-$TestInstance"
    $DisplayName = "Hams Dictate Test $TestInstance"
    $AppIdGuid = "5B0C7E7A-3C1D-4F7B-9E57-2B8E6A1D4C94"
    $InstanceArgument = "--test-instance=$TestInstance"
}
else {
    $AppName = "Hams"
    $DisplayName = "Hams Dictate"
    $AppIdGuid = "5B0C7E7A-3C1D-4F7B-9E57-2B8E6A1D4C93"
    $InstanceArgument = ""
}
$AppDir = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$Exe = Join-Path $AppDir "Hams.exe"
$DataDir = Join-Path $env:LOCALAPPDATA $AppName
$SettingsDir = Join-Path $env:APPDATA $AppName
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
# Found as "Hams Dictate" (Start menu, Windows "Apps"); folders and the EXE keep the plain name.
$Shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$DisplayName.lnk"
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{$AppIdGuid}_is1"
New-Item -ItemType Directory -Force $Logs | Out-Null

function Assert-That([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FAILED: $Message" }
    Write-Host "ok   $Message"
}

function Get-Autostart {
    (Get-ItemProperty $RunKey -Name $AppName -ErrorAction SilentlyContinue).$AppName
}

function Get-FileList([string]$Folder) {
    Get-ChildItem $Folder -Recurse -File | ForEach-Object { $_.FullName.Substring($Folder.Length) } | Sort-Object
}

# A setup with administrator rights (a CI runner runs elevated) stops when silent unless told it may (hams.iss,
# AdminAllowed): the test first checks that it does stop, then passes /ALLOWADMIN=1 to every setup it runs. With UAC
# off there is no boundary, and the setup goes on by itself.
$Elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
$Lua = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name EnableLUA -ErrorAction SilentlyContinue).EnableLUA
$UacOn = $Lua -ne 0
$AdminSwitch = if ($Elevated) { "/ALLOWADMIN=1" } else { "" }

function Install([string]$Arguments, [string]$Log) {
    $all = "/VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART $Arguments $AdminSwitch /LOG=`"$Logs\$Log`""
    # Not -Wait: that would also wait for the Hams the update starts again (a child of the setup).
    $process = Start-Process $Setup -ArgumentList $all -PassThru
    $process.WaitForExit()
    Assert-That ($process.ExitCode -eq 0) "setup $Arguments exits with 0 (got $($process.ExitCode))"
}

# Whether a process runs with Windows' RedirectionGuard enforced (ProcessRedirectionTrustPolicy = 16): then it cannot
# follow links a user made without admin rights (error 448). Null where Windows cannot tell (before 10 22H2).
# StartGuarded switches it on for a new PowerShell and has that one start a program: a stand-in for an older Hams
# (or any program) that inherited it and starts the update.
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class HamsSmokeMitigation {
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, uint id);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetProcessMitigationPolicy(IntPtr process, int policy, out uint flags, UIntPtr size);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("user32.dll")] static extern IntPtr GetShellWindow();
    public static bool? RedirectionGuard(int id) {
        IntPtr process = OpenProcess(0x0400, false, (uint)id);   // PROCESS_QUERY_INFORMATION
        if (process == IntPtr.Zero) return null;
        try { uint flags; return GetProcessMitigationPolicy(process, 16, out flags, (UIntPtr)4) ? (bool?)((flags & 1) != 0) : null; }
        catch (EntryPointNotFoundException) { return null; }
        finally { CloseHandle(process); }
    }
    public static bool HasShell() { return GetShellWindow() != IntPtr.Zero; }
}
"@

# The Hams processes started from the install folder.
function Get-InstalledHams {
    @(Get-CimInstance Win32_Process -Filter "Name = 'Hams.exe'" |
        Where-Object { $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -ieq [IO.Path]::GetFullPath($Exe)) })
}

function Wait-InstalledHams([int]$Seconds, [scriptblock]$Until) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $found = Get-InstalledHams
        if (& $Until $found) { return $found }
        Start-Sleep -Milliseconds 250
    }
    return Get-InstalledHams
}

function Stop-InstalledHams {
    Get-InstalledHams | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $null = Wait-InstalledHams 30 { param($found) $found.Count -eq 0 }
}

if (Test-Path $AppDir) { throw "$AppDir exists already; this test needs a clean machine (or a fresh -TestInstance)." }

# A test instance starts quietly: settings that exist already mean "set up" (no first-run assistant), Tiny keeps a
# model download small, and no update check. The real Hams is not involved either way.
if ($TestInstance) {
    New-Item -ItemType Directory -Force $SettingsDir | Out-Null
    $config = Get-Content (Join-Path $PSScriptRoot "..\hams\config.default.toml") -Raw
    $config = $config -replace '(?m)^size = "turbo"', 'size = "tiny"' -replace '(?m)^check = true', 'check = false'
    [IO.File]::WriteAllText((Join-Path $SettingsDir "config.toml"), $config)
}

try {
    # --- with administrator rights: the silent setup stops on its own --------------------
    if ($Elevated -and $UacOn) {
        $all = "/VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART /TASKS=autostart /LOG=`"$Logs\admin-refused.log`""
        $process = Start-Process $Setup -ArgumentList $all -PassThru
        $process.WaitForExit()
        Assert-That ($process.ExitCode -ne 0) "with administrator rights the silent setup stops (exit $($process.ExitCode))"
        Assert-That (-not (Test-Path $AppDir)) "and installs nothing"
        Assert-That ($null -eq (Get-Autostart)) "and writes no autostart entry"
        Write-Host "     the remaining setups run with $AdminSwitch"
    }
    elseif ($Elevated) {
        Write-Host "     administrator rights with UAC off: the setup goes on by itself (no boundary to protect)"
    }

    # --- install -------------------------------------------------------------------------
    Write-Host "Installing $Setup"
    Install "/TASKS=autostart" "install.log"
    Assert-That (Test-Path $Exe) "Hams.exe installed per user in $AppDir"
    Assert-That (Test-Path $Shortcut) "Start menu shortcut exists"
    $displayed = (Get-ItemProperty $UninstallKey -Name DisplayName -ErrorAction SilentlyContinue).DisplayName
    Assert-That ($displayed -eq $DisplayName) "listed in Windows Apps as '$DisplayName' (got '$displayed')"
    $expected = ("`"$Exe`" --background $InstanceArgument").Trim()
    Assert-That ((Get-Autostart) -eq $expected) "autostart entry starts Hams.exe in the tray ($expected)"
    $installed = Get-FileList $AppDir
    Write-Host ("Installed: {0} files, {1:N0} MB" -f $installed.Count, ((Get-ChildItem $AppDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))

    # --- self-test -----------------------------------------------------------------------
    $report = Join-Path $Logs "self-test.txt"
    $process = Start-Process $Exe -ArgumentList ("--self-test --report `"$report`" $InstanceArgument").Trim() -Wait -PassThru
    if (Test-Path $report) { Get-Content $report | Write-Host }
    Assert-That ($process.ExitCode -eq 0) "Hams.exe --self-test exits with 0 (got $($process.ExitCode))"
    Assert-That ((Get-Content $report -Raw) -match "self-test passed") "self-test report says passed"
    $after = Get-FileList $AppDir
    $new = Compare-Object $installed $after | Where-Object SideIndicator -eq "=>" | ForEach-Object InputObject
    Assert-That (-not $new) "nothing was written into the install folder $(($new | Select-Object -First 5) -join ', ')"
    Assert-That (Test-Path (Join-Path $DataDir "logs\hams.log")) "the log is in %LOCALAPPDATA%\$AppName\logs"

    # --- the one-click update: silent, Hams comes back without RedirectionGuard -------------
    # What hams\update_install.py passes. Inno Setup 6.7+ would hand its RedirectionGuard to the Hams it starts;
    # packaging\hams.iss switches that off.
    Install "/RESTARTHAMS=background" "update.log"
    $running = Wait-InstalledHams 30 { param($found) $found.Count -ge 1 }
    Assert-That ($running.Count -eq 1) "after the update exactly one Hams runs again from $AppDir (got $($running.Count))"
    $guard = [HamsSmokeMitigation]::RedirectionGuard([int]$running[0].ProcessId)
    Assert-That ($guard -ne $true) "the Hams started by the setup runs without RedirectionGuard (got $guard)"
    Stop-InstalledHams

    # --- the same update, started by a program that has RedirectionGuard ----------------------
    # A Hams 1.3.5 started by its setup had it, and so does the setup it starts for "Update now": the setup and the
    # Hams it starts inherit it whatever hams.iss says. The new Hams must notice and come back through the shell.
    if ([HamsSmokeMitigation]::HasShell()) {
        $helper = Join-Path $Logs "start-guarded.ps1"
        @"
`$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public static class Guard {
    [DllImport("kernel32.dll", SetLastError = true)] public static extern bool SetProcessMitigationPolicy(int policy, ref uint flags, UIntPtr size);
}
'@
`$flags = [uint32]1
if (-not [Guard]::SetProcessMitigationPolicy(16, [ref]`$flags, [UIntPtr]4)) { exit 3 }
`$setup = Start-Process '$Setup' -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART /RESTARTHAMS=background $AdminSwitch /LOG="$Logs\update-guarded.log"' -PassThru
`$setup.WaitForExit()
exit `$setup.ExitCode
"@ | Set-Content -Path $helper -Encoding UTF8
        $shell = (Get-Process -Id $PID).Path
        $process = Start-Process $shell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$helper`"" -WindowStyle Hidden -PassThru
        $process.WaitForExit()
        Assert-That ($process.ExitCode -eq 0) "a setup started by a program with RedirectionGuard exits with 0 (got $($process.ExitCode))"
        # The Hams the setup starts inherits the guard, asks the shell to start it again and quits once the new one
        # holds the lock; the new one has the guard off and Explorer as its parent.
        $running = Wait-InstalledHams 45 {
            param($found)
            $found.Count -eq 1 -and [HamsSmokeMitigation]::RedirectionGuard([int]$found[0].ProcessId) -eq $false
        }
        Assert-That ($running.Count -eq 1) "after the guarded update exactly one Hams runs from $AppDir (got $($running.Count))"
        $hams = $running[0]
        $guard = [HamsSmokeMitigation]::RedirectionGuard([int]$hams.ProcessId)
        Assert-That ($guard -ne $true) "that Hams runs without RedirectionGuard (got $guard)"
        $parent = (Get-Process -Id $hams.ParentProcessId -ErrorAction SilentlyContinue).ProcessName
        Assert-That ($parent -eq "explorer") "it was started by the Windows shell (parent: $parent)"
        Assert-That ($hams.CommandLine -match "--redirection-restarted") "it carries the restart marker, so it never restarts again"
        if ($TestInstance) {
            Assert-That ($hams.CommandLine -match [regex]::Escape($InstanceArgument)) "it is still the test instance ($InstanceArgument)"
        }
        $log = Get-Content (Join-Path $DataDir "logs\hams.log") -Raw
        Assert-That ($log -match "restarted through the Windows shell") "the log says why it restarted"
        Stop-InstalledHams
    }
    else {
        Write-Host "skip guarded update: no Windows shell here (CI runner or invisible desktop); Hams keeps running with the mitigation there"
    }

    # --- uninstall -----------------------------------------------------------------------
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
    Assert-That (-not (Test-Path $UninstallKey)) "no longer listed in Windows Apps"
    Assert-That (Test-Path $DataDir) "silent uninstall keeps the user's data (offered, not forced)"
}
finally {
    if ($TestInstance) {
        # Nothing of the test instance stays behind, whatever failed above.
        Stop-InstalledHams
        $uninstaller = Join-Path $AppDir "unins000.exe"
        if (Test-Path $uninstaller) {
            Start-Process $uninstaller -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART" -Wait | Out-Null
            $deadline = (Get-Date).AddSeconds(120)
            while ((Test-Path $AppDir) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
        }
        foreach ($folder in $AppDir, $DataDir, $SettingsDir) {
            if (Test-Path $folder) { Remove-Item -Recurse -Force $folder -ErrorAction SilentlyContinue }
        }
        Remove-ItemProperty $RunKey -Name $AppName -ErrorAction SilentlyContinue
        if (Test-Path $UninstallKey) { Remove-Item $UninstallKey -Force -ErrorAction SilentlyContinue }
        if (Test-Path $Shortcut) { Remove-Item $Shortcut -Force -ErrorAction SilentlyContinue }
    }
}
if ($TestInstance) {
    $left = @($AppDir, $DataDir, $SettingsDir, $Shortcut, $UninstallKey) | Where-Object { Test-Path $_ }
    Assert-That (-not $left -and $null -eq (Get-Autostart)) "nothing of the test instance is left $($left -join ', ')"
}
Write-Host "Smoke test passed."
