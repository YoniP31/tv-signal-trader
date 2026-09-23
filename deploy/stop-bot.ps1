# Force-stops the bot, its chromedriver, and every Chrome process tied to its
# browser profile, then confirms nothing is left. Exits 0 only when clean;
# otherwise prints what survived and exits 1 -- a Chrome still holding the
# profile is what makes the next launch die with "Chrome instance exited".
#
# The bot is matched by process NAME (not command line), so this can never
# match -- and kill -- the very PowerShell running this script. chromedriver's
# own command line only ever has --port=..., never the profile path, and the
# bot leaves one behind every time it is force-killed, so it is matched by
# name too (this is a bot-dedicated machine). Chrome is matched by the
# profile path in its command line, but only among chrome.exe processes:
# asking WMI for just those is far cheaper than reading every process's
# command line, which is slow enough on a loaded VPS to time out over SSH.
#
# This file is the source of truth: setup_vps.ps1 embeds a copy (a test keeps
# them identical) and deploy.py uploads it before each stop.
$profileDir = Join-Path $env:USERPROFILE "tv_profile"
$botNames = @("tv-signal-trader", "tv-signal-trader-user", "chromedriver")

function Get-ProfileChrome {
    Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$profileDir*" }
}

for ($pass = 1; $pass -le 3; $pass++) {
    Get-Process -Name $botNames -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Get-ProfileChrome |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1

    $left = @()
    $left += @(Get-Process -Name $botNames -ErrorAction SilentlyContinue |
        ForEach-Object { "$($_.ProcessName) ($($_.Id))" })
    $left += @(Get-ProfileChrome | ForEach-Object { "chrome ($($_.ProcessId))" })
    if ($left.Count -eq 0) { exit 0 }
}
Write-Output ("Still running after 3 passes: " + ($left -join ", "))
exit 1
