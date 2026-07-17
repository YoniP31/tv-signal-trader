from . import config


def ensure_configured():
    """Called at startup. Only prompts for whatever is actually missing."""
    if not config.TRADINGGENERATOR_USERNAME:
        _set_username("TradingGenerator", "TRADINGGENERATOR_USERNAME")
    if not config.TRADINGGENERATOR_PASSWORD:
        _set_password("TradingGenerator", "TRADINGGENERATOR_PASSWORD")
    if not config.TRADOVATE_USERNAME:
        _set_username("Tradovate", "TRADOVATE_USERNAME")
    if not config.TRADOVATE_PASSWORD:
        _set_password("Tradovate", "TRADOVATE_PASSWORD")


def run_setup():
    """Called from the 'setup' command. Walks through every value, offering
    to keep the current one."""
    print("\n=== Setup (press Enter to keep the current value) ===")
    _set_username("TradingGenerator", "TRADINGGENERATOR_USERNAME")
    _set_password("TradingGenerator", "TRADINGGENERATOR_PASSWORD")
    _set_username("Tradovate", "TRADOVATE_USERNAME")
    _set_password("Tradovate", "TRADOVATE_PASSWORD")
    print("Setup complete [OK]\n")


def _set_username(service_label, env_key):
    current = getattr(config, env_key)
    suffix = f" [{current}]" if current else ""
    while True:
        raw = input(f"{service_label} username{suffix}: ").strip()
        value = raw or current
        if value:
            config.set_env_values({env_key: value})
            return
        print("  [FAIL] Username can't be empty.")


def _set_password(service_label, env_key):
    # Plain input(), not getpass: getpass reads via the Windows console API
    # directly rather than stdin, which mishandles pasted text (issue #1) and
    # hides what you're typing with no way to catch a paste corruption.
    current = getattr(config, env_key)
    suffix = " [keep current]" if current else ""
    while True:
        raw = input(f"{service_label} password{suffix}: ").strip()
        value = raw or current
        if value:
            config.set_env_values({env_key: value})
            return
        print("  [FAIL] Password can't be empty.")
