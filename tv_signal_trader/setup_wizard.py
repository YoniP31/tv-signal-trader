from . import config


def ensure_configured():
    """Called at startup. Only prompts for whatever is actually missing."""
    if not config.TRADINGGENERATOR_USERNAME:
        _set_username()
    if not config.TRADINGGENERATOR_PASSWORD:
        _set_password()


def run_setup():
    """Called from the 'setup' command. Walks through every value, offering
    to keep the current one."""
    print("\n=== Setup (press Enter to keep the current value) ===")
    _set_username()
    _set_password()
    print("Setup complete [OK]\n")


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
    # Plain input(), not getpass: getpass reads via the Windows console API
    # directly rather than stdin, which mishandles pasted text (issue #1) and
    # hides what you're typing with no way to catch a paste corruption.
    current = config.TRADINGGENERATOR_PASSWORD
    suffix = " [keep current]" if current else ""
    while True:
        raw = input(f"TradingGenerator password{suffix}: ").strip()
        value = raw or current
        if value:
            config.set_env_values({"TRADINGGENERATOR_PASSWORD": value})
            return
        print("  [FAIL] Password can't be empty.")
