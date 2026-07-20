import datetime
import json
import os

from . import config

STATUS_FILE = os.path.join(config.APP_DIR, "status.json")


def _read():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _write(data):
    data["last_updated"] = _now()
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def update(**fields):
    """Updates top-level, run-wide status fields (app_running, loop_state,
    etc.) -- see update_portfolio() for per-portfolio fields instead."""
    data = _read()
    data.update(fields)
    return _write(data)


def mark_app_started():
    """Called once at startup, before login state has actually been verified."""
    update(
        app_running=True, tradingview_logged_in=False, tradinggenerator_logged_in=False,
        loop_state="idle", stop_reason=None,
    )


def mark_app_stopped():
    """Called on shutdown (normal quit or crash via try/finally) so a stale
    status.json from a dead process never reads as "logged in"."""
    update(app_running=False, tradingview_logged_in=False, tradinggenerator_logged_in=False)


TRADINGVIEW_SESSION_COOKIE = "sessionid"


def check_tradingview_logged_in(driver):
    """Returns True/False, or None if the check itself couldn't run.

    TradingView renders essentially the same chart page whether you're logged
    in or not (only order placement actually differs), so a DOM/text check
    can't tell the two apart. The session cookie can: TradingView sets
    `sessionid` on login. Uses the Network.getAllCookies CDP command rather
    than driver.get_cookies() so it works no matter which tab is currently
    active — driver.get_cookies() only returns cookies for the current tab's
    domain, which would require switching to the TradingView tab first (and
    switching tabs via Selenium actually changes the frontmost tab in the
    real browser window, visibly hijacking whatever the user is looking at).
    Since Network.getAllCookies returns cookies for every domain, the
    TradingView domain is checked explicitly to avoid matching some other
    site's unrelated cookie that happens to also be named "sessionid".
    """
    try:
        result = driver.execute_cdp_cmd("Network.getAllCookies", {})
        session_cookie = next(
            (
                c for c in result.get("cookies", [])
                if c.get("name") == TRADINGVIEW_SESSION_COOKIE
                and "tradingview.com" in c.get("domain", "")
            ),
            None,
        )
        return bool(session_cookie and session_cookie.get("value"))
    except Exception:
        return None


def _portfolio_key(company, portfolio):
    return f"{company} / {portfolio}"


def update_portfolio(company, portfolio, **fields):
    """Merges `fields` into this portfolio's entry under status.json's
    "portfolios" dict (keyed "Company / Portfolio"), creating it if needed."""
    data = _read()
    portfolios = data.setdefault("portfolios", {})
    entry = portfolios.setdefault(_portfolio_key(company, portfolio), {})
    entry.update(fields)
    entry["updated_at"] = _now()
    return _write(data)


def record_trade_result(company, portfolio, *, asset, direction, contracts, sl_ticks, tp_ticks, result):
    """Records the most recent trade for this portfolio and bumps its
    running trade counters. These counters are lifetime totals (as long as
    status.json sticks around) rather than reset daily -- TradingGenerator's
    own on-page counters are already the source of truth for "today"."""
    data = _read()
    portfolios = data.setdefault("portfolios", {})
    entry = portfolios.setdefault(_portfolio_key(company, portfolio), {})
    entry["last_trade"] = {
        "asset": asset,
        "direction": direction,
        "contracts": contracts,
        "sl_ticks": sl_ticks,
        "tp_ticks": tp_ticks,
        "result": result,
        "closed_at": _now(),
    }
    entry["trades_total"] = entry.get("trades_total", 0) + 1
    if result == "tp":
        entry["wins_total"] = entry.get("wins_total", 0) + 1
    elif result == "sl":
        entry["losses_total"] = entry.get("losses_total", 0) + 1
    elif result == "not_taken":
        entry["not_taken_total"] = entry.get("not_taken_total", 0) + 1
    entry["updated_at"] = _now()
    return _write(data)


def mark_portfolio_removed(company, portfolio, reason):
    """`reason` is a short human-readable note, e.g. "balance outside
    allowed range (blown/hit target)"."""
    update_portfolio(company, portfolio, active=False, removed_at=_now(), removed_reason=reason)


def mark_portfolio_unavailable(company, portfolio, reason):
    update_portfolio(company, portfolio, unavailable_reason=reason, unavailable_since=_now())


def mark_portfolio_available(company, portfolio):
    """Clears a previously-recorded unavailable_reason, e.g. once a
    portfolio has successfully traded again."""
    update_portfolio(company, portfolio, unavailable_reason=None, unavailable_since=None)


def timestamp():
    return _now()
