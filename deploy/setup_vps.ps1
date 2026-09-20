<#
tv-signal-trader -- one-time per-VPS setup for remote deployment.

Run this ONCE on each VPS, via RDP, from an elevated PowerShell:

    powershell -ExecutionPolicy Bypass -File .\setup_vps.ps1

Safe to re-run: every step checks whether it's already done before acting.

What it does:
  1. Enables OpenSSH Server and sets it to start automatically.
  2. Makes the SSH firewall rule apply on every network profile (a VPS's
     network is usually classed "Public", which the default rule -- scoped
     to "Private" only -- doesn't cover).
  3. Installs the deploy tool's public key for the Administrators group.
  4. Writes stop-bot.ps1 next to the exe: force-stops the bot and every
     Chrome/chromedriver process tied to its profile.
  5. Registers a Scheduled Task that starts the bot (with the trading
     command as an argument, so it actually starts trading instead of
     sitting at the '>' prompt). The bot drives a real, visible Chrome
     window, so the task runs in the user's interactive session (at
     logon) -- NOT "whether user is logged on or not", which runs
     without a real desktop and can't work for this.
  6. Verifies what it can.

NOTE: keep this file pure ASCII -- Windows PowerShell 5.1 can misread
other characters in a script file that has no byte-order mark.
#>

$ErrorActionPreference = "Stop"

# ---- Configuration -- edit per VPS if it differs ---------------------------
$DeployPublicKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKwFpScyGoeSJQTkE4TFkTFVbMFu+c5Eo2HSAtOvnf+s yonip@DESKTOP-TQ7MA4L"
$InstallRoot     = "C:\Users\Administrator\Desktop"   # where the bot's folder lives
$InstallPrefix   = "tv-signal-trader"                 # the folder's name STARTS with this, e.g. tv-signal-trader-v1.1.0
$InstallDir      = ""                                 # normally leave empty; set an exact folder to skip the search
$ExeName         = "tv-signal-trader.exe"     # or tv-signal-trader-user.exe
$StartCommand    = "web_multi"                # or "web" -- whichever you normally type at the '>' prompt
$TaskName        = "TVSignalTrader"
$RunAsUser       = "$env:COMPUTERNAME\Administrator"
# -----------------------------------------------------------------------------

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    WARN: $msg" -ForegroundColor Yellow }

# The folder is named after the release it came from (tv-signal-trader-v1.1.0,
# ...), so it is found by prefix instead of by an exact path: the one folder
# under $Root whose name starts with $Prefix and that actually holds $Exe.
# Refuses to guess between several.
function Resolve-InstallDir($Root, $Prefix, $Exe) {
    if (-not (Test-Path $Root)) { throw "Install root not found: $Root (fix `$InstallRoot at the top of this script)." }
    $found = @(Get-ChildItem -Path $Root -Directory -Filter "$Prefix*" |
        Where-Object { Test-Path (Join-Path $_.FullName $Exe) })
    if ($found.Count -eq 0) {
        throw "No folder starting with '$Prefix' that contains $Exe was found in $Root (fix `$InstallRoot / `$ExeName, or set `$InstallDir, at the top of this script)."
    }
    if ($found.Count -gt 1) {
        $names = ($found | ForEach-Object { $_.FullName }) -join "`n  "
        throw "More than one folder starting with '$Prefix' contains ${Exe}:`n  $names`nSet `$InstallDir at the top of this script to the one to use (or remove the old one)."
    }
    return $found[0].FullName
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Run this from an elevated PowerShell (right-click -> Run as administrator)." }

# 1. OpenSSH Server ------------------------------------------------------------
Write-Step "OpenSSH Server"
$cap = Get-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0"
if ($cap.State -ne "Installed") {
    Add-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0" | Out-Null
    Write-Ok "installed"
} else { Write-Ok "already installed" }

if ((Get-Service sshd).Status -ne "Running") { Start-Service sshd; Write-Ok "started" } else { Write-Ok "already running" }
Set-Service -Name sshd -StartupType Automatic

# 2. Firewall ------------------------------------------------------------------
Write-Step "Firewall rule"
$rule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
if ($rule) {
    if ($rule.Profile.ToString() -ne "Any") {
        Set-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -Profile Any
        Write-Ok "widened to all network profiles"
    } else { Write-Ok "already applies to all profiles" }
} else {
    New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -Profile Any | Out-Null
    Write-Ok "created"
}

# 3. Deploy key ----------------------------------------------------------------
Write-Step "Deploy key"
$authKeysPath = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
if (-not (Test-Path $authKeysPath)) { New-Item -ItemType File -Path $authKeysPath -Force | Out-Null }
$existingKeys = @(Get-Content $authKeysPath -ErrorAction SilentlyContinue)
if ($existingKeys -contains $DeployPublicKey) {
    Write-Ok "key already present"
} else {
    Add-Content -Path $authKeysPath -Value $DeployPublicKey
    Write-Ok "key added"
}
# sshd silently ignores this file unless only SYSTEM + Administrators can touch it.
icacls $authKeysPath /inheritance:r | Out-Null
icacls $authKeysPath /grant "SYSTEM:F" "Administrators:F" | Out-Null
Write-Ok "permissions locked to SYSTEM + Administrators"

# 4. stop-bot.ps1 --------------------------------------------------------------
Write-Step "stop-bot.ps1"
if ($InstallDir) {
    if (-not (Test-Path $InstallDir)) { throw "Install directory not found: $InstallDir (fix `$InstallDir at the top of this script)." }
} else {
    $InstallDir = Resolve-InstallDir $InstallRoot $InstallPrefix $ExeName
    Write-Ok "found the install folder: $InstallDir"
}
$stopScriptPath = Join-Path $InstallDir "stop-bot.ps1"
$stopScript = @'
# Force-stops the bot, its chromedriver, and every Chrome process tied to its
# browser profile. Matches the bot by process NAME (not command line), so it
# can never match -- and kill -- the very PowerShell running this script.
$profileDir = Join-Path $env:USERPROFILE "tv_profile"
Get-Process -Name "tv-signal-trader", "tv-signal-trader-user" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
# chromedriver's own command line only ever has --port=..., never the profile
# path, so the profile match below can't find it -- and the bot leaves one
# behind every time it's force-killed. This is a bot-dedicated machine, so
# match it by name.
Get-Process -Name "chromedriver" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process |
    Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like "*$profileDir*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
'@
Set-Content -Path $stopScriptPath -Value $stopScript -Encoding ASCII
Write-Ok "written to $stopScriptPath"

# 5. Scheduled Task ------------------------------------------------------------
Write-Step "Scheduled Task '$TaskName'"
$exePath = Join-Path $InstallDir $ExeName
if (-not (Test-Path $exePath)) { Write-Warn2 "$exePath does not exist yet - the task will fail to start until it does." }

$action    = New-ScheduledTaskAction -Execute $exePath -Argument $StartCommand -WorkingDirectory $InstallDir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $RunAsUser
$settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
# RunLevel Limited (NOT Highest): confirmed live that Chrome exits immediately
# ("session not created: Chrome instance exited") when the bot is launched
# elevated -- from an elevated PowerShell or a Highest-level task alike --
# while a normal double-click, which runs non-elevated, works fine.
$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Write-Ok "updated in place"
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Write-Ok "created -- does NOT launch anything now; it fires at the next logon of $RunAsUser"
}

# 6. Verify --------------------------------------------------------------------
Write-Step "Verification"
if (netstat -an | Select-String ":22\s.*LISTENING") { Write-Ok "sshd is listening on port 22" } else { Write-Warn2 "sshd does NOT appear to be listening" }
$task = Get-ScheduledTask -TaskName $TaskName
Write-Ok "task state: $($task.State); runs as $($task.Principal.UserId), logon type $($task.Principal.LogonType)"
Write-Host "    Network profile: $((Get-NetConnectionProfile)[0].NetworkCategory) (the firewall rule now applies regardless)"

Write-Host ""
Write-Host "Done. Next: stop whatever's running by hand, then test the task:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  & `"$stopScriptPath`""
