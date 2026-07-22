import datetime
import os
import re
import sys
from zoneinfo import ZoneInfo

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

# The trading session window (SESSION_START_TIME/SESSION_END_TIME in .env,
# "HH:MM" 24-hour) is always interpreted in Israel local time, DST and all.
SESSION_TIMEZONE = ZoneInfo("Asia/Jerusalem")

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


def _parse_time(raw):
    """Parses a "HH:MM" 24-hour string into a datetime.time, or None if
    it's missing/malformed."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        hour, minute = raw.split(":", 1)
        return datetime.time(int(hour), int(minute))
    except ValueError:
        return None


def now_in_israel():
    return datetime.datetime.now(SESSION_TIMEZONE)


def session_window_status(now=None):
    """Whether trading is currently allowed under the configured session
    window (SESSION_START_TIME/SESSION_END_TIME), Israel time.

    Returns a (status, seconds) tuple:
      - ('unrestricted', None): no window configured -- always allowed.
      - ('open', None): within today's window.
      - ('waiting', seconds_until_open): outside the window -- either
        today's hasn't started yet, or today's has already closed and this
        counts down to tomorrow's start instead, so the caller can just
        sleep and resume automatically rather than stopping for the day.

    Assumes a same-day window (start < end) -- there's no support for a
    window that spans midnight.
    """
    if SESSION_START_TIME is None or SESSION_END_TIME is None:
        return 'unrestricted', None
    now = now or now_in_israel()
    current_time = now.time()
    if SESSION_START_TIME <= current_time <= SESSION_END_TIME:
        return 'open', None
    start_date = now.date() if current_time < SESSION_START_TIME else now.date() + datetime.timedelta(days=1)
    start_dt = datetime.datetime.combine(start_date, SESSION_START_TIME, tzinfo=SESSION_TIMEZONE)
    return 'waiting', (start_dt - now).total_seconds()


def in_no_trade_window(now=None):
    """Whether the current moment falls inside the configured mid-session
    blackout (NO_TRADE_START_TIME/NO_TRADE_END_TIME), Israel time -- a
    window where no *new* trades should be generated, distinct from the
    overall SESSION_START_TIME/SESSION_END_TIME window it sits inside.

    False if either bound is unset (no blackout configured). Assumes a
    same-day window, same as session_window_status().
    """
    if NO_TRADE_START_TIME is None or NO_TRADE_END_TIME is None:
        return False
    current_time = (now or now_in_israel()).time()
    return NO_TRADE_START_TIME <= current_time <= NO_TRADE_END_TIME


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
    global SESSION_START_TIME, SESSION_END_TIME
    global NO_TRADE_START_TIME, NO_TRADE_END_TIME
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
        # `or default` (not a dict-get default) so a present-but-blank line
        # in .env -- e.g. a commented-out template value someone uncommented
        # without filling in -- falls back cleanly instead of `float('')`
        # raising.
        min_val = float(_env.get(f"{prefix}_MIN_BALANCE") or default_min)
        max_val = float(_env.get(f"{prefix}_MAX_BALANCE") or default_max)
        ACCOUNT_BALANCE_TIERS[size] = (min_val, max_val)

    # Unset by default -- no session window means trading is allowed anytime.
    SESSION_START_TIME = _parse_time(_env.get("SESSION_START_TIME", ""))
    SESSION_END_TIME = _parse_time(_env.get("SESSION_END_TIME", ""))

    # Unset by default -- no mid-session blackout unless both are configured.
    NO_TRADE_START_TIME = _parse_time(_env.get("NO_TRADE_START_TIME", ""))
    NO_TRADE_END_TIME = _parse_time(_env.get("NO_TRADE_END_TIME", ""))


_reload_env()
