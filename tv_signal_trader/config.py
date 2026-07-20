import os
import re
import sys

try:
    __compiled__  # noqa: F821 - injected by Nuitka into compiled modules
    _RUNNING_COMPILED = True
except NameError:
    _RUNNING_COMPILED = False

if _RUNNING_COMPILED:
    # Nuitka --onefile extracts to a fresh temp dir every run, so __file__
    # is useless for anything that needs to persist (.env, status.json).
    # sys.argv[0] is the real, stable path to the distributed .exe.
    APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROFILE_DIR = os.path.join(os.path.expanduser("~"), "tv_profile")

CHART_URL = "https://www.tradingview.com/chart/?symbol=MNQ1!"
SIGNAL_SITE_URL = "https://tradinggenerator-english.tiiny.co/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LOGIN_POLL_INTERVAL_SECONDS = 15
TRADE_CLOSE_POLL_INTERVAL_SECONDS = 20
TRADE_CLOSE_TIMEOUT_SECONDS = 24 * 60 * 60

# Starter list of well-known futures prop firms -- easy to extend, this is
# just what's popular at time of writing. One Tradovate account per firm.
PROP_FIRMS = [
    "Apex Trader Funding",
    "TopStep",
    "Tradeify",
    "MyFundedFutures",
    "Take Profit Trader",
    "Bulenox",
    "Elite Trader Funding",
    "Leeloo Trading",
]

# Default $ balance range per standard prop-firm account size. There's no
# way to read which size an account actually is from the page, so
# trading.account_needs_removal() guesses by picking whichever size here the
# current balance is numerically closest to -- safe since the real ranges
# are far apart (a 50K account is never anywhere near a 25K account's ~$27K
# ceiling). Override any of these via .env once real per-size numbers are
# confirmed; these are placeholders.
_DEFAULT_ACCOUNT_TIER_RANGES = {
    25000: (23000, 27000),
    50000: (47500, 53000),
}


ENV_FILE = os.path.join(APP_DIR, ".env")


def tradovate_env_key(company, field):
    """e.g. ('Apex Trader Funding', 'username') -> 'TRADOVATE_APEX_TRADER_FUNDING_USERNAME'"""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_").upper()
    return f"TRADOVATE_{slug}_{field.upper()}"


def get_env_value(key, default=""):
    return _env.get(key, default)


def _load_env_file(path):
    values = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def set_env_values(values):
    """Persist key/value pairs into .env (preserving other lines) and reload
    the in-memory config so the new values are visible immediately."""
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            lines = f.readlines()

    remaining = dict(values)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}\n"

    for key, value in remaining.items():
        lines.append(f"{key}={value}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)

    _reload_env()


def _reload_env():
    global _env, TRADINGGENERATOR_USERNAME, TRADINGGENERATOR_PASSWORD
    global TRADOVATE_ACCOUNTS, ACCOUNT_BALANCE_TIERS
    _env = _load_env_file(ENV_FILE)
    TRADINGGENERATOR_USERNAME = _env.get("TRADINGGENERATOR_USERNAME", "")
    TRADINGGENERATOR_PASSWORD = _env.get("TRADINGGENERATOR_PASSWORD", "")
    # One Tradovate account per prop firm. Only firms with both a username
    # and password saved show up here -- see README "Planned work" for the
    # still-missing piece: picking the right account for a given trade.
    TRADOVATE_ACCOUNTS = {}
    for company in PROP_FIRMS:
        username = _env.get(tradovate_env_key(company, "username"), "")
        password = _env.get(tradovate_env_key(company, "password"), "")
        if username and password:
            TRADOVATE_ACCOUNTS[company] = {"username": username, "password": password}

    ACCOUNT_BALANCE_TIERS = {}
    for size, (default_min, default_max) in _DEFAULT_ACCOUNT_TIER_RANGES.items():
        prefix = f"ACCOUNT_{size // 1000}K"
        min_val = float(_env.get(f"{prefix}_MIN_BALANCE", default_min))
        max_val = float(_env.get(f"{prefix}_MAX_BALANCE", default_max))
        ACCOUNT_BALANCE_TIERS[size] = (min_val, max_val)


_reload_env()
