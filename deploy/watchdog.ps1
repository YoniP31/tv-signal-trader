<#
tv-signal-trader -- watchdog: checks whether the bot is actually running,
and restarts it if not.

Meant to run on a Scheduled Task every few hours (see setup_vps.ps1's
"Watchdog Scheduled Task" step, which registers one running this file --
every 4 hours by default). Ships in the release zip next to the exe(s), so
a fresh install already has it; nothing extra to fetch or set up by hand
beyond running setup_vps.ps1 once, same as everything else.

Usage (normally run by its Scheduled Task, but safe to run by hand too):

    powershell -ExecutionPolicy Bypass -File .\watchdog.ps1

What it does, each run:
  1. Reads which exe the 'TVSignalTrader' Scheduled Task actually runs (the
     same technique deploy.py uses from the admin's own machine) -- never
     hardcodes tv-signal-trader.exe vs. tv-signal-trader-user.exe.
  2. Checks whether that exe's process is currently running.
  3. If it is: logs OK and exits -- nothing else happens.
  4. If it isn't: runs stop-bot.ps1 (same folder) first, in case the crash
     left Chrome/chromedriver orphaned and holding the profile's lock file
     -- starting the exe again against that would just fail the same way
     (see browser.create_driver's own retry/force_kill for the in-process
     half of this same problem). Then starts the Scheduled Task again and
     checks it actually came back up.

Every run appends a line or two to watchdog.log next to this script -- a
dedicated log, separate from the bot's own app.log.

Limitation: this can only bring the bot back within an interactive session
that's already open (the one its Scheduled Task runs in -- see
setup_vps.ps1's own notes on why that task needs LogonType Interactive). It
does not solve "nobody is logged into this VPS at all" -- there is no
auto-logon configured, so a machine that was fully rebooted with nobody
signed back in still needs a person to log in before either task can do
anything.

NOTE: keep this file pure ASCII -- Windows PowerShell 5.1 can misread other
characters in a script file that has no byte-order mark.
#>

param(
    [string]$TaskName = "TVSignalTrader",
    [int]$VerifySeconds = 30
)

$ErrorActionPreference = "Stop"
$InstallDir = $PSScriptRoot
$LogPath = Join-Path $InstallDir "watchdog.log"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Add-Content -Path $LogPath -Value $line -Encoding ASCII
    Write-Host $line
}

# The exe file name a Scheduled Task runs, from `schtasks /query /xml` --
# the same parsing deploy.py does on the admin's own machine
# (parse_task_exe), reimplemented here since this runs on the VPS itself,
# where there is no Python. $null if the task doesn't exist or its command
# can't be read.
function Get-TaskExeName {
    param([string]$Name)
    # No stderr redirect on the native call: with $ErrorActionPreference =
    # "Stop" in effect, redirecting a native command's stderr (even to
    # $null) turns its error output into a terminating exception instead of
    # just text -- schtasks writes exactly that ("ERROR: The system cannot
    # find the file specified.") when $Name isn't a real task, which must
    # be handled as an ordinary "not found" here, not thrown.
    $xmlLines = & schtasks /query /tn $Name /xml
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -or -not $xmlLines) { return $null }
    $text = $xmlLines -join "`n"
    $m = [regex]::Match($text, '(?s)<Command>\s*(.*?)\s*</Command>')
    if (-not $m.Success) { return $null }
    $command = $m.Groups[1].Value.Trim('"') -replace '/', '\'
    if (-not $command) { return $null }
    return ($command -split '\\')[-1]
}

function Test-ProcessRunning {
    param([string]$ExeName)
    # Same reasoning as Get-TaskExeName above -- no stderr redirect.
    $result = & tasklist /fi "imagename eq $ExeName" /fo csv /nh
    return (($result -join "`n").ToLower()).Contains($ExeName.ToLower())
}

try {
    $exeName = Get-TaskExeName -Name $TaskName
    if (-not $exeName) {
        Write-Log "[FAIL] Could not read the '$TaskName' Scheduled Task (not registered yet? run setup_vps.ps1) - nothing to watch."
        exit 1
    }

    if (Test-ProcessRunning -ExeName $exeName) {
        Write-Log "OK - $exeName is running."
        exit 0
    }

    Write-Log "$exeName is NOT running - checking for leftover Chrome/chromedriver before restarting."
    $stopScript = Join-Path $InstallDir "stop-bot.ps1"
    if (Test-Path $stopScript) {
        $cleanupOutput = & $stopScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Cleanup: OK (nothing left running, or it was already clean)."
        } else {
            Write-Log "[WARN] Cleanup: $($cleanupOutput -join ' ')"
        }
    } else {
        Write-Log "[WARN] stop-bot.ps1 not found in $InstallDir - restarting without cleanup."
    }

    Write-Log "Starting the '$TaskName' task."
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds $VerifySeconds
    if (Test-ProcessRunning -ExeName $exeName) {
        Write-Log "OK - $exeName is running again."
    } else {
        Write-Log ("[FAIL] Restarted the '$TaskName' task, but $exeName is still not running " +
            "${VerifySeconds}s later - check app.log on this machine, and whether it has been " +
            "activated yet (the one-time activation password, entered once by hand).")
        exit 1
    }
} catch {
    Write-Log "[FAIL] Unexpected error: $($_.Exception.Message)"
    exit 1
}
