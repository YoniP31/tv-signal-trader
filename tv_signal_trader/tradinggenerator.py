import re

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import config
from . import humanize
from . import panel


def is_login_form_present(driver):
    """True if the current tab is showing a visible password field.

    TradingGenerator (unlike TradingView) actually gates its content behind a
    login form when logged out, so this DOM check is a reliable signal there.
    """
    try:
        return any(
            el.is_displayed()
            for el in driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
        )
    except Exception:
        return False


def ensure_logged_in(driver):
    """If a login form is present on the current tab, fill it from .env and submit.

    Relies on the persistent Chrome profile to skip this most of the time —
    only kicks in when there's no valid session cookie yet.
    """
    if not is_login_form_present(driver):
        print("  Already logged in (no login form found) [OK]")
        return True

    if not config.TRADINGGENERATOR_USERNAME or not config.TRADINGGENERATOR_PASSWORD:
        print("  Login form detected but no credentials in .env - log in manually.")
        return False

    print("  Login form detected, filling credentials...")
    password_el = next(
        el for el in driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
        if el.is_displayed()
    )

    username_el = driver.execute_script("""
        var pwd = arguments[0];
        var inputs = document.querySelectorAll('input');
        var candidate = null;
        for (var i = 0; i < inputs.length; i++) {
            if (inputs[i] === pwd) break;
            var type = (inputs[i].type || '').toLowerCase();
            if (type === 'text' || type === 'email' || type === '') {
                candidate = inputs[i];
            }
        }
        return candidate;
    """, password_el)

    if username_el is None:
        print("  Could not find a username field near the password field.")
        return False

    panel.set_field(driver, username_el, config.TRADINGGENERATOR_USERNAME)
    panel.set_field(driver, password_el, config.TRADINGGENERATOR_PASSWORD)

    clicked = False
    for btn in driver.find_elements(By.TAG_NAME, "button"):
        try:
            if btn.is_displayed() and 'sign in' in btn.text.strip().lower():
                btn.click()
                clicked = True
                break
        except Exception:
            pass
    if not clicked:
        password_el.send_keys(Keys.RETURN)

    humanize.long_pause(2, 3)

    if is_login_form_present(driver):
        print("  Login failed - password field still present after submit.")
        return False

    print("  Login succeeded [OK]")
    return True


def open_tab(driver, other_tab):
    """Finds an already-open TradingGenerator tab, or opens a new one.

    `other_tab` (e.g. the TradingView tab) is skipped when searching, so the
    loop doesn't mistake it for TradingGenerator. Leaves the driver switched
    to the TradingGenerator tab.
    """
    site_host = config.SIGNAL_SITE_URL.split('//')[-1].split('/')[0]
    for handle in driver.window_handles:
        if handle == other_tab:
            continue
        driver.switch_to.window(handle)
        if site_host in driver.current_url:
            return handle

    driver.switch_to.window(other_tab)
    driver.execute_script(f"window.open('{config.SIGNAL_SITE_URL}', '_blank');")
    humanize.long_pause(3, 5)
    web_tab = driver.window_handles[-1]
    driver.switch_to.window(web_tab)
    humanize.long_pause(2, 3)
    return web_tab


def generate_trade(driver):
    """Clicks GENERATE NEW TRADE on the current (TradingGenerator) tab.

    Returns 'generated', 'locked' (the daily trade limit's been reached -- the
    button relabels itself to something like "REACHED 4 TRADES TODAY -
    PORTFOLIO LOCKED"), or 'not_found' (something unexpected).
    """
    for btn in driver.find_elements(By.TAG_NAME, "button"):
        text = btn.text.strip()
        if 'generate new trade' in text.lower():
            btn.click()
            print(f"  Clicked: '{text}' [OK]")
            return 'generated'

    for btn in driver.find_elements(By.TAG_NAME, "button"):
        text = btn.text.strip().lower()
        if 'locked' in text or 'reached' in text:
            print(f"  Portfolio locked: '{btn.text.strip()}'")
            return 'locked'

    print("  GENERATE NEW TRADE button not found! Buttons seen on page:")
    for btn in driver.find_elements(By.TAG_NAME, "button"):
        text = btn.text.strip()
        if text:
            print(f"    '{text}'")
    return 'not_found'


def _extract_field(driver, label):
    """Finds the TRADE PARAMETERS box whose text starts with `label` (e.g.
    "ASSET\nNQ") and returns just the value part ("NQ").

    Prefers the shortest such match: the same label can also show up inside
    a larger ancestor container that concatenates multiple boxes' text, and
    the smallest matching element is reliably the single label+value box
    itself rather than one of those larger wrappers.
    """
    candidates = []
    for el in driver.find_elements(By.XPATH, "//*[not(self::script) and not(self::style)]"):
        try:
            text = el.text.strip()
        except Exception:
            continue
        if text.startswith(label) and text != label:
            candidates.append(text)
    if not candidates:
        return None
    shortest = min(candidates, key=len)
    return shortest[len(label):].strip()


def read_trade_parameters(driver):
    """Reads the TRADE PARAMETERS box (asset/direction/contracts/SL/TP) from
    the currently open TradingGenerator tab."""
    asset = _extract_field(driver, "ASSET")
    direction = _extract_field(driver, "DIRECTION")
    contracts_raw = _extract_field(driver, "CONTRACTS") or ""
    sl_raw = _extract_field(driver, "STOP LOSS") or ""
    tp_raw = _extract_field(driver, "TAKE PROFIT") or ""

    contracts_match = re.search(r'\d+', contracts_raw)
    sl_match = re.search(r'(\d+)\s*ticks', sl_raw, re.IGNORECASE)
    tp_match = re.search(r'(\d+)\s*ticks', tp_raw, re.IGNORECASE)

    return {
        'asset': asset,
        'direction': direction.upper() if direction else None,
        'contracts': int(contracts_match.group()) if contracts_match else None,
        'contract_size': re.sub(r'[\d\s]', '', contracts_raw) or None,
        'sl_ticks': int(sl_match.group(1)) if sl_match else None,
        'tp_ticks': int(tp_match.group(1)) if tp_match else None,
    }


RESULT_BUTTON_LABELS = {
    'tp': 'take profit',
    'sl': 'stop loss',
    'not_taken': 'trade not taken',
}


def report_trade_result(driver, outcome):
    """Clicks the matching TRADE RESULT button on the current (TradingGenerator)
    tab. `outcome` is one of 'tp', 'sl', 'not_taken'."""
    label = RESULT_BUTTON_LABELS[outcome]
    for btn in driver.find_elements(By.TAG_NAME, "button"):
        text = btn.text.strip()
        if text.lower().startswith(label):
            btn.click()
            print(f"  Reported result: '{text}' [OK]")
            return True
    print(f"  WARNING: could not find a '{label}' result button")
    return False
