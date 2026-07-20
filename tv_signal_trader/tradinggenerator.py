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


def _tab_name(tab_el):
    """The plain-text name span inside a company/portfolio tab, excluding its
    icon/badge/delete-x children."""
    try:
        return tab_el.find_element(
            By.CSS_SELECTOR, "span:not(.tab-x):not(.tab-type):not(.tab-delete)"
        ).text.strip()
    except Exception:
        return None


def select_company(driver, company):
    """Clicks the #companiesBar tab matching `company` by name, if it isn't
    already active. Returns True if a matching tab was found."""
    for tab in driver.find_elements(By.CSS_SELECTOR, "#companiesBar .company-tab"):
        if _tab_name(tab) == company:
            if 'active' not in (tab.get_attribute('class') or '').split():
                tab.click()
                humanize.long_pause(1, 2)
            return True
    print(f"  [WARN] Company tab '{company}' not found.")
    return False


def select_portfolio(driver, portfolio):
    """Clicks the #portfolioBar tab matching `portfolio` by name, if it isn't
    already active. Returns True if a matching tab was found -- e.g. it can
    fail if the portfolio belongs to a different (not-yet-selected) company,
    or is tucked away behind a "+N Portfolios" picker this doesn't open yet."""
    for tab in driver.find_elements(By.CSS_SELECTOR, "#portfolioBar .portfolio-tab"):
        if _tab_name(tab) == portfolio:
            classes = (tab.get_attribute('class') or '').split()
            if not any(c.startswith('active') for c in classes):
                tab.click()
                humanize.long_pause(1, 2)
            return True
    print(f"  [WARN] Portfolio tab '{portfolio}' not visible.")
    return False


def _read_wrong_account_warning(driver):
    """If TradingGenerator's "wrong account" warning modal is showing (you
    tried to generate a trade for the wrong company/portfolio), returns the
    (user, company, portfolio) it says should be selected instead. Otherwise
    None."""
    try:
        text_el = driver.find_element(By.ID, "wrongAccountText")
    except Exception:
        return None

    visible = driver.execute_script("""
        var el = arguments[0];
        var style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        var rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    """, text_el)
    if not visible:
        return None

    try:
        strong = text_el.find_element(By.TAG_NAME, "strong")
    except Exception:
        return None
    parts = [p.strip() for p in strong.text.split('›')]
    if len(parts) != 3:
        return None
    return tuple(parts)


def _cancel_wrong_account_modal(driver):
    for btn in driver.find_elements(By.CSS_SELECTOR, ".modal-cancel"):
        if btn.is_displayed():
            btn.click()
            humanize.long_pause(1, 2)
            return True
    return False


def generate_trade(driver, expected_company=None, expected_portfolio=None):
    """Clicks GENERATE NEW TRADE (#generateBtn) on the current (TradingGenerator) tab.

    `expected_company`/`expected_portfolio` (the "next trade to trade" hint
    read off the previous generate -- see read_trade_parameters) are selected
    first, if given. Either way, if TradingGenerator still pops up its "wrong
    account" warning (e.g. on the very first trade of a run, when there's no
    hint yet, or if something's out of sync), this reads the correct
    company/portfolio off the warning itself, cancels, switches, and retries
    once.

    Returns 'generated', 'locked' (the daily trade limit's been reached --
    the button relabels itself to something like "REACHED 4 TRADES TODAY -
    PORTFOLIO LOCKED"), 'cooldown' (a previous trade hasn't finished its ~30s
    cooldown yet -- the button stays disabled but keeps reading "GENERATE NEW
    TRADE", so a naive click would silently no-op and leave us reading stale
    parameters from the previous trade), 'wrong_account' (still mismatched
    after one correction attempt), or 'not_found' (something unexpected).
    """
    if expected_company:
        select_company(driver, expected_company)
    if expected_portfolio:
        select_portfolio(driver, expected_portfolio)

    for _ in range(2):
        try:
            btn = driver.find_element(By.ID, "generateBtn")
        except Exception:
            print("  GENERATE NEW TRADE button (#generateBtn) not found!")
            return 'not_found'

        text = btn.text.strip()
        disabled = btn.get_attribute("disabled") is not None

        if disabled:
            if 'locked' in text.lower() or 'reached' in text.lower():
                print(f"  Portfolio locked: '{text}'")
                return 'locked'
            print(f"  Generate button is disabled (cooldown in progress): '{text}'")
            return 'cooldown'

        btn.click()
        print(f"  Clicked: '{text}' [OK]")
        humanize.long_pause(1, 2)

        warning = _read_wrong_account_warning(driver)
        if warning is None:
            return 'generated'

        _, warn_company, warn_portfolio = warning
        print(f"  [WARN] Wrong company/portfolio selected - TradingGenerator wants "
              f"'{warn_company} / {warn_portfolio}'. Cancelling and switching...")
        _cancel_wrong_account_modal(driver)
        select_company(driver, warn_company)
        select_portfolio(driver, warn_portfolio)

    print("  [FAIL] Still on the wrong company/portfolio after switching - giving up.")
    return 'wrong_account'


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


def _extract_by_id(driver, element_id):
    try:
        return driver.find_element(By.ID, element_id).text.strip()
    except Exception:
        return None


def _extract_by_class(driver, class_name):
    try:
        return driver.find_element(By.CSS_SELECTOR, f".{class_name}").text.strip()
    except Exception:
        return None


def read_trade_parameters(driver):
    """Reads the TRADE PARAMETERS box (asset/direction/contracts/SL/TP), the
    active portfolio/company, and the "Next Portfolio to Trade" hint (which
    company/portfolio the *next* generate should target -- shown after this
    one was generated) from the currently open TradingGenerator tab."""
    asset = _extract_field(driver, "ASSET")
    direction = _extract_field(driver, "DIRECTION")
    contracts_raw = _extract_field(driver, "CONTRACTS") or ""
    sl_raw = _extract_field(driver, "STOP LOSS") or ""
    tp_raw = _extract_field(driver, "TAKE PROFIT") or ""

    contracts_match = re.search(r'\d+', contracts_raw)
    sl_match = re.search(r'(\d+)\s*ticks', sl_raw, re.IGNORECASE)
    tp_match = re.search(r'(\d+)\s*ticks', tp_raw, re.IGNORECASE)

    portfolio = _extract_by_id(driver, "portfolioTitle")
    company_raw = _extract_by_id(driver, "companyBreadcrumb")
    # companyBreadcrumb is prefixed with a "diamond" bullet icon (e.g. "◆ Main Company").
    company = re.sub(r'^[^\w]+', '', company_raw).strip() if company_raw else None

    next_company = _extract_by_class(driver, "next-acc-firm")
    next_portfolio = _extract_by_class(driver, "next-acc-account")

    return {
        'asset': asset,
        'direction': direction.upper() if direction else None,
        'contracts': int(contracts_match.group()) if contracts_match else None,
        'contract_size': re.sub(r'[\d\s]', '', contracts_raw) or None,
        'sl_ticks': int(sl_match.group(1)) if sl_match else None,
        'tp_ticks': int(tp_match.group(1)) if tp_match else None,
        'portfolio': portfolio or None,
        'company': company or None,
        'next_company': next_company or None,
        'next_portfolio': next_portfolio or None,
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
