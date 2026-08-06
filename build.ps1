# Builds a standalone Windows .exe with Nuitka -- either the "admin"
# (default; full feature set, unchanged from the app's original behavior)
# or "user" (restricted -- see config.IS_ADMIN_BUILD for what that changes)
# variant. See README.md "Building a standalone .exe" for details and
# distribution notes.
#
# Usage:
#   .\build.ps1                 # admin build (tv-signal-trader.exe)
#   .\build.ps1 -Variant user   # user build (tv-signal-trader-user.exe)
#
# The variant is baked into the .exe at compile time (not read from an
# environment variable at runtime, so it can't be changed by whoever ends
# up running the .exe): this script overwrites
# tv_signal_trader/_build_variant.py's BUILD_VARIANT literal right before
# invoking Nuitka, then restores it back to "admin" afterward, so the
# working tree is left clean either way. If the build is interrupted before
# that restore runs (e.g. Ctrl+C), run
# `git checkout -- tv_signal_trader/_build_variant.py` to put it back.
#
# --include-package-data=tzdata: the trading-session check needs Israel's
# IANA timezone data bundled in -- Windows has no system tz database, and
# Nuitka doesn't pick up a pure-data package's files automatically.
param(
    [ValidateSet("admin", "user")]
    [string]$Variant = "admin"
)

$variantFile = Join-Path $PSScriptRoot "tv_signal_trader\_build_variant.py"
$outputFilename = if ($Variant -eq "admin") { "tv-signal-trader.exe" } else { "tv-signal-trader-user.exe" }

$variantFileTemplate = @'
"""Which .exe variant this is -- "admin" (full feature set, unchanged from
the app's original behavior) or "user" (restricted, see config.IS_ADMIN_BUILD
for what that changes). build.ps1 overwrites this file's BUILD_VARIANT value
immediately before compiling the "user" variant, then restores it back to
"admin" afterward -- don't edit this by hand for a real build.

Running from source (python main.py) always behaves as "admin" -- that's
this file's default/committed value, and it never changes unless build.ps1
is actively mid-build.
"""

BUILD_VARIANT = "{0}"
'@

function Set-BuildVariant([string]$value) {
    # Set-Content -Encoding utf8 writes a BOM in Windows PowerShell 5.1,
    # which breaks Python's parser -- write plain BOM-less UTF-8 directly.
    $content = $variantFileTemplate -f $value
    [System.IO.File]::WriteAllText($variantFile, $content, (New-Object System.Text.UTF8Encoding($false)))
}

Write-Host "Building the '$Variant' variant -> dist\$outputFilename"
Set-BuildVariant $Variant
try {
    python -m nuitka --onefile --output-dir=dist --windows-console-mode=force --assume-yes-for-downloads --include-package-data=tzdata --output-filename=$outputFilename main.py
}
finally {
    Set-BuildVariant "admin"
}
