import base64
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

# "admin" (full feature set) vs "user" (restricted -- see every
# IS_ADMIN_BUILD check below) build variant. Baked in at compile time by
# build.ps1 overwriting _build_variant.py's literal value before invoking
# Nuitka -- not read from an environment variable, so it can't be changed
# at runtime by whoever ends up running the .exe. Always "admin" when
# running from source.
from ._build_variant import BUILD_VARIANT  # noqa: E402
IS_ADMIN_BUILD = BUILD_VARIANT == "admin"

if _RUNNING_COMPILED:
    # Nuitka --onefile extracts to a fresh temp dir every run, so __file__
    # is useless for anything that needs to persist (.env, status.json).
    # sys.argv[0] is the real, stable path to the distributed .exe.
    APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROFILE_DIR = os.path.join(os.path.expanduser("~"), "tv_profile")

CHART_URL = "https://www.tradingview.com/chart/?symbol=MNQ1!"
SIGNAL_SITE_URL = "https://test-english.tiiny.site/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# DEV ONLY: TradingGenerator's "MINI" contract size means the real mini
# ticker (NQ, ES, GC, ...), not the "M"-prefixed micro ticker (MNQ, MES,
# MGC, ...) -- see trading.resolve_symbol. While developing from source,
# though, it's safer to test against micro contracts (an account is far
# less likely to get liquidated), so MINI signals are deliberately routed
# to their micro ticker instead. This flips off automatically in the
# compiled .exe (_RUNNING_COMPILED above, built via build.ps1), which
# always uses the correct mini/micro mapping -- do not hardcode this True
# for a real build.
DEV_TREAT_MINI_AS_MICRO = not _RUNNING_COMPILED

# True (default): tradinggenerator.open_tab() opens TradingGenerator as its
# own OS-level window and hides it (browser.hide_window_by_title) so it's
# not reachable through normal user interaction. False: opens it as a
# plain, visible tab of the main browser window instead (no hiding) --
# useful for debugging, e.g. to actually see what TradingGenerator is doing.
HIDE_TRADINGGENERATOR_WINDOW = True

# Randomized polling ranges (hardcoded, not .env-configurable -- deliberately
# so, since these exist purely to avoid a perfectly regular timing signature
# during long unattended runs, not to be tuned per deployment). Shared by
# both 'web' and 'web_multi' wherever a repeated/idle wait would otherwise
# be a fixed interval:
#   - HEARTBEAT_POLL_RANGE: the background login/browser-alive check
#     (monitor.LoginMonitor).
#   - POSITION_POLL_RANGE: waiting on an open position to close, or on a
#     signal that isn't eligible to open yet.
#   - PORTFOLIO_RETRY_RANGE: waiting when every known portfolio is
#     locked/unavailable and there's nothing to check yet.
HEARTBEAT_POLL_RANGE = (5, 30)
POSITION_POLL_RANGE = (20, 300)
PORTFOLIO_RETRY_RANGE = (20, 300)

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
#
# MAX (the profit-target side) is split by account type (EVAL vs LIVE) since
# those genuinely differ in practice; MIN (the max-loss side) is shared.
_DEFAULT_ACCOUNT_TIER_MIN = {
    25000: 23000,
    50000: 47500,
}
_DEFAULT_ACCOUNT_TIER_MAX = {
    'EVAL': {25000: 27000, 50000: 53000},
    'LIVE': {25000: 27000, 50000: 53500},
}

# Flip Mode's own initial ("staged") and final equity targets (see the
# Flip Mode plan), confirmed with the user -- additive alongside
# _DEFAULT_ACCOUNT_TIER_MAX above, not a replacement for it: today's
# account_needs_removal/adjust_tp_for_max_balance keep reading ['max']
# exactly as before, untouched, until the Flip Mode state machine itself
# is wired in to use these instead.
_DEFAULT_ACCOUNT_TIER_MAX_INITIAL = {
    'EVAL': {25000: 26000, 50000: 52500},
    'LIVE': {25000: 26500, 50000: 53000},
}
_DEFAULT_ACCOUNT_TIER_MAX_FINAL = {
    'EVAL': {25000: 26500, 50000: 53000},
    'LIVE': {25000: 27000, 50000: 53500},
}

# Flip Mode's three qualifying conditions (see the Flip Mode plan) -- not
# yet consulted by anything until the state machine itself is wired in.
# Overridable via FLIP_MODE_MIN_PROFITABLE_DAYS/FLIP_MODE_MIN_DAILY_PROFIT/
# FLIP_MODE_CONSISTENCY_DIVISOR in .env.
_DEFAULT_FLIP_MODE_MIN_PROFITABLE_DAYS = 5
_DEFAULT_FLIP_MODE_MIN_DAILY_PROFIT = 200
_DEFAULT_FLIP_MODE_CONSISTENCY_DIVISOR = 0.5

# Second Withdrawal's re-entry target seed (see flip_mode.seed_reentry_
# target): how far above the account's post-withdrawal equity to seed its
# new running_equity_target, on the rare occasion that equity is already
# at/above the tier's final threshold. Overridable via
# FLIP_MODE_REENTRY_BUFFER in .env.
_DEFAULT_FLIP_MODE_REENTRY_BUFFER = 1500

# When a trade's take-profit would push the account's balance past its max
# (see trading.adjust_tp_for_max_balance), the TP is capped so the result
# lands at max + a random buffer in this $ range instead -- looks more
# natural than landing exactly on the max every time. Overridable via
# TP_CAP_BUFFER_MIN/TP_CAP_BUFFER_MAX in .env.
_DEFAULT_TP_CAP_BUFFER_RANGE = (50, 200)

# web_multi only: at most this many concurrently open positions at a single
# company. Overridable via MPPC in .env.
_DEFAULT_MAX_POSITIONS_PER_COMPANY = 3

# web_multi only: when a trade's take-profit/stop-loss would push today's
# P&L (see trading.read_total_pl) past DAILY_PROFIT_LIMIT/DAILY_LOSS_LIMIT,
# it's capped so the result instead lands a random buffer in this $ range
# short of the limit -- same idea as TP_CAP_BUFFER above, applied to today's
# P&L instead of account balance. Overridable via DAILY_PNL_CAP_BUFFER_MIN/
# DAILY_PNL_CAP_BUFFER_MAX in .env.
_DEFAULT_DAILY_PNL_CAP_BUFFER_RANGE = (50, 200)


ENV_FILE = os.path.join(APP_DIR, ".env")


def tradovate_env_key(company, field):
    """e.g. ('Apex Trader Funding', 'username') -> 'TRADOVATE_APEX_TRADER_FUNDING_USERNAME'"""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_").upper()
    return f"TRADOVATE_{slug}_{field.upper()}"


def get_env_value(key, default=""):
    return _env.get(key, default)


def read_env_value_from_disk(key, default=""):
    """Reads a single .env value straight from disk, bypassing the
    cached `_env` entirely -- unlike every other config.* value (loaded
    once at startup, refreshed only via set_env_values/the 'setup'
    command), this always reflects whatever's on disk *right now*.

    Meant for values worth rotating without restarting the whole app --
    e.g. TRADINGGENERATOR_ADMIN_CODE (see read_admin_code below): while
    web_multi is running, it holds the '>' prompt (and so 'setup')
    hostage for its entire run, so a cached value can't be changed any
    other way. Correctly un-obscures a key in _OBSCURED_ENV_KEYS, same as
    a normal load -- and since _unobscure only decodes a value that
    actually has the "b64:" prefix, a plain, un-obscured value written by
    hand works too, no encoding needed.

    Not used for most settings deliberately -- re-reading the whole file
    on every access would be needless disk I/O for values that
    essentially never change mid-run.
    """
    return _load_env_file(ENV_FILE).get(key, default)


def read_admin_code():
    """TRADINGGENERATOR_ADMIN_CODE, read fresh from .env every call (see
    read_env_value_from_disk) -- so it can be rotated on disk and take
    effect on the very next Flip Mode/Second Withdrawal button click,
    with no restart needed."""
    return read_env_value_from_disk("TRADINGGENERATOR_ADMIN_CODE")


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


def _safe_float(raw, default):
    """float(raw), falling back to `default` if raw is missing/blank/not a
    valid number -- so a malformed .env value degrades to "use the
    default" instead of crashing the whole app at import time.
    validate_env() below is what actually surfaces the problem to the
    user before a command runs; this is just the fallback for whatever
    slips through anyway (e.g. .env hand-edited while the app is running).
    """
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _safe_positive_int(raw, default):
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _check_time(raw):
    if _parse_time(raw) is None:
        raise ValueError('must be a 24-hour "HH:MM" time, e.g. "09:30"')


def _check_float(raw):
    try:
        float(raw)
    except ValueError:
        raise ValueError('must be a plain number, e.g. "500" or "23000.5"')


def _check_positive_int(raw):
    try:
        value = int(raw)
    except ValueError:
        raise ValueError('must be a whole number, e.g. "3"')
    if value <= 0:
        raise ValueError('must be greater than 0, e.g. "3"')


def _check_consistency_divisor(raw):
    try:
        value = float(raw)
    except ValueError:
        raise ValueError('must be a plain number greater than 0 and at most 1, e.g. "0.5"')
    if not (0 < value <= 1):
        raise ValueError('must be greater than 0 and at most 1, e.g. "0.5"')


def _balance_tier_field_specs():
    specs = []
    for size, default_min in _DEFAULT_ACCOUNT_TIER_MIN.items():
        prefix = f"ACCOUNT_{size // 1000}K"
        specs.append((f"{prefix}_MIN_BALANCE", _check_float, f'a dollar amount, e.g. "{default_min:g}"'))
        for account_type, defaults in _DEFAULT_ACCOUNT_TIER_MAX.items():
            specs.append((
                f"{prefix}_MAX_BALANCE_{account_type}", _check_float,
                f'a dollar amount, e.g. "{defaults[size]:g}"',
            ))
        for account_type, defaults in _DEFAULT_ACCOUNT_TIER_MAX_INITIAL.items():
            specs.append((
                f"{prefix}_MAX_BALANCE_INITIAL_{account_type}", _check_float,
                f'a dollar amount, e.g. "{defaults[size]:g}"',
            ))
        for account_type, defaults in _DEFAULT_ACCOUNT_TIER_MAX_FINAL.items():
            specs.append((
                f"{prefix}_MAX_BALANCE_FINAL_{account_type}", _check_float,
                f'a dollar amount, e.g. "{defaults[size]:g}"',
            ))
    return specs


# Every .env setting this app parses into something other than a plain
# string, paired with a validator (raises ValueError with a human-readable
# reason on a malformed *non-blank* value) and a "here's what a valid one
# looks like" example -- the single source of truth for validate_env()
# below. TRADINGGENERATOR_USERNAME/PASSWORD and the per-company Tradovate
# credentials aren't included: those are plain strings with no format to
# get wrong, just present-or-not (handled by setup_wizard instead).
_ENV_FIELD_SPECS = _balance_tier_field_specs() + [
    ("SESSION_START_TIME", _check_time, 'a 24-hour "HH:MM" time, e.g. "09:00"'),
    ("SESSION_END_TIME", _check_time, 'a 24-hour "HH:MM" time, e.g. "17:00"'),
    ("NO_TRADE_START_TIME", _check_time, 'a 24-hour "HH:MM" time, e.g. "12:00"'),
    ("NO_TRADE_END_TIME", _check_time, 'a 24-hour "HH:MM" time, e.g. "13:00"'),
    ("TP_CAP_BUFFER_MIN", _check_float, f'a dollar amount, e.g. "{_DEFAULT_TP_CAP_BUFFER_RANGE[0]:g}"'),
    ("TP_CAP_BUFFER_MAX", _check_float, f'a dollar amount, e.g. "{_DEFAULT_TP_CAP_BUFFER_RANGE[1]:g}"'),
    ("MPPC", _check_positive_int, f'a whole number, e.g. "{_DEFAULT_MAX_POSITIONS_PER_COMPANY}"'),
    ("DAILY_PROFIT_LIMIT", _check_float, 'a dollar amount, e.g. "500"'),
    ("DAILY_LOSS_LIMIT", _check_float, 'a dollar amount, e.g. "300"'),
    ("DAILY_PNL_CAP_BUFFER_MIN", _check_float, f'a dollar amount, e.g. "{_DEFAULT_DAILY_PNL_CAP_BUFFER_RANGE[0]:g}"'),
    ("DAILY_PNL_CAP_BUFFER_MAX", _check_float, f'a dollar amount, e.g. "{_DEFAULT_DAILY_PNL_CAP_BUFFER_RANGE[1]:g}"'),
    (
        "FLIP_MODE_MIN_PROFITABLE_DAYS", _check_positive_int,
        f'a whole number, e.g. "{_DEFAULT_FLIP_MODE_MIN_PROFITABLE_DAYS}"',
    ),
    (
        "FLIP_MODE_MIN_DAILY_PROFIT", _check_float,
        f'a dollar amount, e.g. "{_DEFAULT_FLIP_MODE_MIN_DAILY_PROFIT:g}"',
    ),
    (
        "FLIP_MODE_CONSISTENCY_DIVISOR", _check_consistency_divisor,
        f'a number greater than 0 and at most 1, e.g. "{_DEFAULT_FLIP_MODE_CONSISTENCY_DIVISOR:g}"',
    ),
    (
        "FLIP_MODE_REENTRY_BUFFER", _check_float,
        f'a dollar amount, e.g. "{_DEFAULT_FLIP_MODE_REENTRY_BUFFER:g}"',
    ),
]

# (start_key, end_key, label) -- a window needs both ends set to mean
# anything (see session_window_status/in_no_trade_window); having just one
# set silently disables the whole window rather than erroring, which is a
# much more surprising failure mode than a format typo, so validate_env()
# flags it too.
_WINDOW_FIELD_PAIRS = (
    ("SESSION_START_TIME", "SESSION_END_TIME", "the trading session window"),
    ("NO_TRADE_START_TIME", "NO_TRADE_END_TIME", "the mid-session no-trade window"),
)


def validate_env():
    """Checks every .env setting this app understands against its expected
    format -- called by setup_wizard.check_env_validity() before running a
    command that depends on them, so a malformed entry surfaces as a clear
    message + a valid example instead of either crashing or silently
    falling back to a default with no explanation (see _safe_float/
    _safe_positive_int above).

    Blank is always valid -- it means "use the built-in default", or
    "feature disabled" for the opt-in settings -- only a *present but
    malformed* value is reported. Returns a list of (env_key, raw_value,
    problem_message, example) tuples, empty if everything checks out.
    """
    problems = []
    for key, check, example in _ENV_FIELD_SPECS:
        raw = _env.get(key, "").strip()
        if not raw:
            continue
        try:
            check(raw)
        except ValueError as exc:
            problems.append((key, raw, str(exc), example))

    for start_key, end_key, label in _WINDOW_FIELD_PAIRS:
        start_raw = _env.get(start_key, "").strip()
        end_raw = _env.get(end_key, "").strip()
        if bool(start_raw) != bool(end_raw):
            missing_key = end_key if start_raw else start_key
            set_key = start_key if start_raw else end_key
            problems.append((
                missing_key, "",
                f"must also be set for {label} to take effect ({set_key} is set, but "
                f"{missing_key} isn't, so the window is currently being ignored entirely)",
                f'a 24-hour "HH:MM" time, matching {set_key}',
            ))
    return problems


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


# TradingGenerator's credentials are the only ones stored obscured in
# .env (Tradovate accounts are left as plain text -- not part of this ask).
# This is casual obscurity against someone glancing at the file, not real
# encryption -- base64 is trivially reversible by anyone who looks for it.
# The setup wizard's terminal prompts are untouched (still plain input()),
# and every other config.py consumer keeps reading/writing plain values
# from _env -- only _load_env_file/set_env_values below know this exists.
_OBSCURED_ENV_KEYS = {
    "TRADINGGENERATOR_USERNAME", "TRADINGGENERATOR_PASSWORD", "TRADINGGENERATOR_ADMIN_CODE",
}
_OBSCURE_PREFIX = "b64:"


def _obscure(value):
    return _OBSCURE_PREFIX + base64.b64encode(value.encode("utf-8")).decode("ascii")


def _unobscure(value):
    # The prefix (rather than just trying to base64-decode anything found)
    # is what lets this tell an already-obscured value apart from a plain
    # one -- e.g. an existing .env from before this existed -- without
    # guessing: decoding a plain password as if it might be base64 could,
    # rarely, "succeed" into corrupted garbage instead of failing loudly.
    if not value.startswith(_OBSCURE_PREFIX):
        return value
    try:
        return base64.b64decode(value[len(_OBSCURE_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        return value


def _load_env_file(path):
    values = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key in _OBSCURED_ENV_KEYS:
                    value = _unobscure(value)
                values[key] = value
    return values


def set_env_values(values):
    """Persist key/value pairs into .env (preserving other lines) and reload
    the in-memory config so the new values are visible immediately."""
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            lines = f.readlines()

    remaining = {
        key: (_obscure(value) if key in _OBSCURED_ENV_KEYS else value)
        for key, value in values.items()
    }
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
    global _env, TRADINGGENERATOR_USERNAME, TRADINGGENERATOR_PASSWORD, TRADINGGENERATOR_ADMIN_CODE
    global TRADOVATE_ACCOUNTS, ACCOUNT_BALANCE_TIERS
    global SESSION_START_TIME, SESSION_END_TIME
    global NO_TRADE_START_TIME, NO_TRADE_END_TIME
    global TP_CAP_BUFFER_RANGE
    global MAX_POSITIONS_PER_COMPANY
    global DAILY_PROFIT_LIMIT, DAILY_LOSS_LIMIT
    global DAILY_PNL_CAP_BUFFER_RANGE
    global FLIP_MODE_MIN_PROFITABLE_DAYS, FLIP_MODE_MIN_DAILY_PROFIT, FLIP_MODE_CONSISTENCY_DIVISOR
    global FLIP_MODE_REENTRY_BUFFER
    _env = _load_env_file(ENV_FILE)
    TRADINGGENERATOR_USERNAME = _env.get("TRADINGGENERATOR_USERNAME", "")
    TRADINGGENERATOR_PASSWORD = _env.get("TRADINGGENERATOR_PASSWORD", "")
    # Admin code for Flip Mode / Second Withdrawal's native window.prompt()
    # dialogs (see tradinggenerator.enable_flip_mode/mark_second_withdrawal)
    # -- a separate credential from the login password above, distinct on
    # the page itself (its own prompt, not the login form).
    TRADINGGENERATOR_ADMIN_CODE = _env.get("TRADINGGENERATOR_ADMIN_CODE", "")
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
    for size, default_min in _DEFAULT_ACCOUNT_TIER_MIN.items():
        prefix = f"ACCOUNT_{size // 1000}K"
        # _safe_float (not a bare float()) so a blank *or malformed* value
        # in .env -- e.g. a typo'd override -- falls back to the default
        # cleanly instead of crashing the whole app at import time.
        # validate_env() is what actually surfaces a malformed value to
        # the user; this is just the fallback for whatever slips through.
        min_val = _safe_float(_env.get(f"{prefix}_MIN_BALANCE"), default_min)
        max_by_type = {}
        for account_type, defaults in _DEFAULT_ACCOUNT_TIER_MAX.items():
            default_max = defaults[size]
            max_by_type[account_type] = _safe_float(_env.get(f"{prefix}_MAX_BALANCE_{account_type}"), default_max)
        # Additive alongside 'max' above, not a replacement for it -- see
        # _DEFAULT_ACCOUNT_TIER_MAX_INITIAL/_FINAL's own comment.
        max_initial_by_type = {}
        for account_type, defaults in _DEFAULT_ACCOUNT_TIER_MAX_INITIAL.items():
            default_max = defaults[size]
            max_initial_by_type[account_type] = _safe_float(
                _env.get(f"{prefix}_MAX_BALANCE_INITIAL_{account_type}"), default_max
            )
        max_final_by_type = {}
        for account_type, defaults in _DEFAULT_ACCOUNT_TIER_MAX_FINAL.items():
            default_max = defaults[size]
            max_final_by_type[account_type] = _safe_float(
                _env.get(f"{prefix}_MAX_BALANCE_FINAL_{account_type}"), default_max
            )
        ACCOUNT_BALANCE_TIERS[size] = {
            'min': min_val, 'max': max_by_type,
            'max_initial': max_initial_by_type, 'max_final': max_final_by_type,
        }

    # Unset by default -- no session window means trading is allowed anytime.
    SESSION_START_TIME = _parse_time(_env.get("SESSION_START_TIME", ""))
    SESSION_END_TIME = _parse_time(_env.get("SESSION_END_TIME", ""))

    # Unset by default -- no mid-session blackout unless both are configured.
    NO_TRADE_START_TIME = _parse_time(_env.get("NO_TRADE_START_TIME", ""))
    NO_TRADE_END_TIME = _parse_time(_env.get("NO_TRADE_END_TIME", ""))

    default_buffer_min, default_buffer_max = _DEFAULT_TP_CAP_BUFFER_RANGE
    TP_CAP_BUFFER_RANGE = (
        _safe_float(_env.get("TP_CAP_BUFFER_MIN"), default_buffer_min),
        _safe_float(_env.get("TP_CAP_BUFFER_MAX"), default_buffer_max),
    )

    MAX_POSITIONS_PER_COMPANY = _safe_positive_int(
        _env.get("MPPC"), _DEFAULT_MAX_POSITIONS_PER_COMPANY
    )

    # web_multi only. Both unset by default (no limit) -- opt-in risk
    # controls, not universal defaults like the balance tiers above.
    # DAILY_LOSS_LIMIT is a positive $ amount (the max acceptable loss),
    # compared against today's P&L going negative past it. A malformed
    # value falls back to None (disabled), same reasoning as elsewhere --
    # validate_env() is what actually flags it to the user.
    DAILY_PROFIT_LIMIT = _safe_float(_env.get("DAILY_PROFIT_LIMIT", ""), None)
    DAILY_LOSS_LIMIT = _safe_float(_env.get("DAILY_LOSS_LIMIT", ""), None)

    default_pnl_buffer_min, default_pnl_buffer_max = _DEFAULT_DAILY_PNL_CAP_BUFFER_RANGE
    DAILY_PNL_CAP_BUFFER_RANGE = (
        _safe_float(_env.get("DAILY_PNL_CAP_BUFFER_MIN"), default_pnl_buffer_min),
        _safe_float(_env.get("DAILY_PNL_CAP_BUFFER_MAX"), default_pnl_buffer_max),
    )

    # Not yet consulted by anything until the Flip Mode state machine
    # itself is wired in -- see _DEFAULT_FLIP_MODE_MIN_PROFITABLE_DAYS's
    # own comment.
    FLIP_MODE_MIN_PROFITABLE_DAYS = _safe_positive_int(
        _env.get("FLIP_MODE_MIN_PROFITABLE_DAYS"), _DEFAULT_FLIP_MODE_MIN_PROFITABLE_DAYS
    )
    FLIP_MODE_MIN_DAILY_PROFIT = _safe_float(
        _env.get("FLIP_MODE_MIN_DAILY_PROFIT"), _DEFAULT_FLIP_MODE_MIN_DAILY_PROFIT
    )
    FLIP_MODE_CONSISTENCY_DIVISOR = _safe_float(
        _env.get("FLIP_MODE_CONSISTENCY_DIVISOR"), _DEFAULT_FLIP_MODE_CONSISTENCY_DIVISOR
    )
    FLIP_MODE_REENTRY_BUFFER = _safe_float(
        _env.get("FLIP_MODE_REENTRY_BUFFER"), _DEFAULT_FLIP_MODE_REENTRY_BUFFER
    )


_reload_env()
