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


def update(**fields):
    data = _read()
    data.update(fields)
    data["last_updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def mark_app_started():
    """Called once at startup, before login state has actually been verified."""
    update(app_running=True, tradingview_logged_in=False, tradinggenerator_logged_in=False)


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
    `sessionid` on login. `driver.get_cookies()` reads it straight from the
    browser's cookie jar rather than `document.cookie`, so it sees it even
    though the cookie is httpOnly.
    """
    try:
        cookies = driver.get_cookies()
        session_cookie = next(
            (c for c in cookies if c.get("name") == TRADINGVIEW_SESSION_COOKIE), None
        )
        return bool(session_cookie and session_cookie.get("value"))
    except Exception:
        return None
