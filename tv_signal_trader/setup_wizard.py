import getpass
import os

from . import browser
from . import config


def ensure_configured():
    """Called at startup. Only prompts for whatever is actually missing."""
    if not (config.CHROMEDRIVER_PATH and os.path.isfile(config.CHROMEDRIVER_PATH)):
        print("\nChromedriver isn't configured yet.")
        _set_chromedriver_path()
    if not config.TRADINGGENERATOR_USERNAME:
        _set_username()
    if not config.TRADINGGENERATOR_PASSWORD:
        _set_password()


def run_setup():
    """Called from the 'setup' command. Walks through every value, offering
    to keep the current one."""
    print("\n=== Setup (press Enter to keep the current value) ===")
    _set_chromedriver_path()
    _set_username()
    _set_password()
    print("Setup complete [OK]\n")


def _set_chromedriver_path():
    current = config.CHROMEDRIVER_PATH
    guess = browser.default_chromedriver_guess()
    default = current or (guess if os.path.isfile(guess) else "")

    print("Chromedriver must match your installed Chrome version.")
    print("Download it from: https://googlechromelabs.github.io/chrome-for-testing/")
    while True:
        suffix = f" [{default}]" if default else ""
        raw = input(f"Path to chromedriver.exe{suffix}: ").strip().strip('"')
        path = raw or default
        if path and os.path.isfile(path):
            config.set_env_values({"CHROMEDRIVER_PATH": path})
            print(f"  [OK] Using {path}")
            return
        print(f"  [FAIL] Not a file: '{path or '(empty)'}'. Try again.")


def _set_username():
    current = config.TRADINGGENERATOR_USERNAME
    suffix = f" [{current}]" if current else ""
    while True:
        raw = input(f"TradingGenerator username{suffix}: ").strip()
        value = raw or current
        if value:
            config.set_env_values({"TRADINGGENERATOR_USERNAME": value})
            return
        print("  [FAIL] Username can't be empty.")


def _set_password():
    current = config.TRADINGGENERATOR_PASSWORD
    suffix = " [keep current]" if current else ""
    while True:
        raw = getpass.getpass(f"TradingGenerator password{suffix}: ").strip()
        value = raw or current
        if value:
            config.set_env_values({"TRADINGGENERATOR_PASSWORD": value})
            return
        print("  [FAIL] Password can't be empty.")
