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


def check_env_validity():
    """Called by cli.py before running a command that depends on .env
    (web/web_multi/test) -- validates every setting via config.validate_env()
    and, if anything's malformed, walks through fixing each one right here
    in the terminal rather than leaving the user to go hunt down a typo in
    a text editor. Returns True if it's safe to proceed (nothing wrong, or
    the user chose to continue anyway), False if the command should be
    aborted (the user backed out without fixing everything).
    """
    problems = config.validate_env()
    if not problems:
        return True

    print(f"\n[WARN] Found {len(problems)} problem(s) in .env:")
    for key, raw, message, example in problems:
        current = f' (currently "{raw}")' if raw else ""
        print(f"  - {key}{current}: {message}")

    print("\nLet's fix these now (press Enter to leave a value as-is and skip it).")
    unresolved = []
    for key, raw, message, example in problems:
        if _fix_one_env_problem(key, raw, message, example):
            continue
        unresolved.append(key)

    if not unresolved:
        print("[OK] .env is valid now.\n")
        return True

    print(f"\n[WARN] Still unresolved: {', '.join(unresolved)}.")
    choice = input("Continue anyway using built-in defaults for those? [y/N]: ").strip().lower()
    print()
    return choice in ("y", "yes")


def _fix_one_env_problem(key, raw, message, example):
    """Prompts for a replacement value for a single invalid .env entry,
    re-validating each attempt before saving so a second typo doesn't slip
    through unnoticed. Returns True once a valid value is saved, False if
    the user presses Enter to skip it instead."""
    current = f' (currently "{raw}")' if raw else ""
    print(f"\n{key}{current}")
    print(f"  Problem: {message}")
    print(f"  Example: {example}")
    while True:
        new_raw = input(f"  New value for {key} (Enter to skip): ").strip()
        if not new_raw:
            return False
        config.set_env_values({key: new_raw})
        # Re-check just this one key -- easier than re-running the whole
        # batch, and set_env_values() already reloaded config._env.
        still_broken = next((p for p in config.validate_env() if p[0] == key), None)
        if still_broken is None:
            print(f"  [OK] {key} updated.")
            return True
        print(f"  [FAIL] Still invalid: {still_broken[2]}")


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
