from . import config


def ensure_configured():
    """Called at startup. Only prompts for whatever is actually missing."""
    if not config.TRADINGGENERATOR_USERNAME:
        _set_username("TradingGenerator", "TRADINGGENERATOR_USERNAME")
    if not config.TRADINGGENERATOR_PASSWORD:
        _set_password("TradingGenerator", "TRADINGGENERATOR_PASSWORD")
    if not config.TRADOVATE_ACCOUNTS:
        print("\nNo Tradovate accounts configured yet - let's add one.")
        _add_tradovate_account()


def run_setup():
    """Called from the 'setup' command. Walks through every value, offering
    to keep the current one, then lets you add/update Tradovate accounts."""
    print("\n=== Setup (press Enter to keep the current value) ===")
    _set_username("TradingGenerator", "TRADINGGENERATOR_USERNAME")
    _set_password("TradingGenerator", "TRADINGGENERATOR_PASSWORD")
    _manage_tradovate_accounts()
    print("Setup complete [OK]\n")


def _manage_tradovate_accounts():
    print("\n--- Tradovate accounts (one per prop firm) ---")
    while True:
        _add_tradovate_account()
        again = input("Add/update another Tradovate account? [y/N]: ").strip().lower()
        if again != "y":
            return


def _add_tradovate_account():
    company = _choose_prop_firm()
    username_key = config.tradovate_env_key(company, "username")
    password_key = config.tradovate_env_key(company, "password")
    _set_username(f"Tradovate ({company})", username_key)
    _set_password(f"Tradovate ({company})", password_key)


def _choose_prop_firm():
    print("\nProp firms:")
    for i, company in enumerate(config.PROP_FIRMS, 1):
        marker = " [configured]" if company in config.TRADOVATE_ACCOUNTS else ""
        print(f"  {i}. {company}{marker}")
    while True:
        raw = input(f"Select a company (1-{len(config.PROP_FIRMS)}): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(config.PROP_FIRMS):
            return config.PROP_FIRMS[int(raw) - 1]
        print(f"  [FAIL] Enter a number from 1 to {len(config.PROP_FIRMS)}.")


def _set_username(service_label, env_key):
    current = config.get_env_value(env_key)
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
    current = config.get_env_value(env_key)
    suffix = " [keep current]" if current else ""
    while True:
        raw = input(f"{service_label} password{suffix}: ").strip()
        value = raw or current
        if value:
            config.set_env_values({env_key: value})
            return
        print("  [FAIL] Password can't be empty.")
