"""Guaranteed stop for tv-signal-trader -- run this from a *separate*
terminal/window whenever you need to be sure everything actually closes,
instead of Ctrl+C in the bot's own window.

Ctrl+C depends on the main process being in a state where it can actually
receive and act on the interrupt (blocked at the '>' prompt, or wherever
it happens to be mid-loop) -- if it's deep inside a Selenium call, or the
console's own Ctrl+C delivery is delayed, it can stop the *program* while
leaving Chrome (including TradingGenerator's hidden window, which you
can't close by hand) still running and holding the profile lock, so the
bot can't be restarted.

This script doesn't depend on the main process's state at all: it finds
every Chrome/chromedriver process tied to this bot's browser profile, and
the bot's own process (main.py or the built .exe), purely by OS-level
process lookup, and force-kills them directly -- guaranteed to work even
if the main program is completely stuck.

Usage: python stop.py
"""
import os
import subprocess

PROFILE_DIR = os.path.join(os.path.expanduser("~"), "tv_profile")
MAIN_PY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


def _kill_matching(cmdline_substring):
    """Force-kills every process (any name) whose command line contains
    `cmdline_substring`, in one PowerShell call -- catches chrome.exe and
    chromedriver.exe together (both reference the profile dir on their
    command line), regardless of how many windows/processes are running."""
    script = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*{cmdline_substring}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, timeout=20,
    )


def main():
    print(f"Closing every Chrome window tied to {PROFILE_DIR} (visible and hidden) ...")
    _kill_matching(PROFILE_DIR)

    print("Stopping the bot's own process ...")
    _kill_matching(MAIN_PY_PATH)
    _kill_matching("tv-signal-trader.exe")

    print("Done.")


if __name__ == "__main__":
    main()
