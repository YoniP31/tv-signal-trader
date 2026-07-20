import re
import time

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
    fail if the portfolio belongs to a different (not-yet-selected) company."""
    for tab in driver.find_elements(By.CSS_SELECTOR, "#portfolioBar .portfolio-tab"):
        if _tab_name(tab) == portfolio:
            classes = (tab.get_attribute('class') or '').split()
            if not any(c.startswith('active') for c in classes):
                tab.click()
                humanize.long_pause(1, 2)
            return True
    print(f"  [WARN] Portfolio tab '{portfolio}' not visible.")
    return False


def list_companies(driver):
    """Names of every company tab in #companiesBar -- all of them are
    always visible there (no hidden picker), so this is the full list."""
    return [
        name for name in (
            _tab_name(tab) for tab in driver.find_elements(By.CSS_SELECTOR, "#companiesBar .company-tab")
        ) if name
    ]


def list_portfolios(driver):
    """Names of every portfolio tab in #portfolioBar for whichever company
    is currently selected -- all of a company's portfolios are always
    visible there (no hidden picker; "+N Portfolios" just bulk-creates)."""
    return [
        name for name in (
            _tab_name(tab) for tab in driver.find_elements(By.CSS_SELECTOR, "#portfolioBar .portfolio-tab")
        ) if name
    ]


def list_all_candidates(driver):
    """Every (company, portfolio) pair currently configured in
    TradingGenerator, by selecting each company in turn and reading its
    portfolio tabs. Leaves the last company in the list selected."""
    candidates = []
    for company in list_companies(driver):
        select_company(driver, company)
        for portfolio in list_portfolios(driver):
            candidates.append((company, portfolio))
    return candidates


def read_active_company_portfolio(driver):
    """Reads whichever company/portfolio is currently selected/active in
    TradingGenerator's UI, independent of whether a trade's been generated
    for it yet."""
    portfolio = _extract_by_id(driver, "portfolioTitle")
    company_raw = _extract_by_id(driver, "companyBreadcrumb")
    # companyBreadcrumb is prefixed with a "diamond" bullet icon (e.g. "◆ Main Company").
    company = re.sub(r'^[^\w]+', '', company_raw).strip() if company_raw else None
    return company or None, portfolio or None


def _click_visible(driver, selector, attempts=6):
    """Clicks the first currently-visible element matching `selector`,
    retrying briefly in case it hasn't rendered yet (e.g. a modal still
    animating in). Returns True if something was clicked."""
    for _ in range(attempts):
        for el in driver.find_elements(By.CSS_SELECTOR, selector):
            if el.is_displayed():
                el.click()
                humanize.long_pause(1, 2)
                return True
        humanize.pause(0.4, 0.7)
    return False


def remove_portfolio(driver, portfolio):
    """Clicks the 'X' (.tab-delete) on the #portfolioBar tab matching
    `portfolio` by name, then confirms the "Delete Portfolio" warning modal
    that pops up -- removing it from TradingGenerator, e.g. once its
    account has blown past its loss limit or hit its profit target and
    shouldn't be traded anymore. Returns True if found, clicked, and
    confirmed."""
    for tab in driver.find_elements(By.CSS_SELECTOR, "#portfolioBar .portfolio-tab"):
        if _tab_name(tab) == portfolio:
            try:
                delete_btn = tab.find_element(By.CSS_SELECTOR, ".tab-delete")
            except Exception:
                print(f"  [WARN] Portfolio '{portfolio}' has no delete button.")
                return False
            delete_btn.click()
            humanize.long_pause(1, 2)
            if not _click_visible(driver, ".modal-confirm"):
                print(f"  [WARN] Delete confirmation modal for '{portfolio}' not found.")
                return False
            print(f"  Removed portfolio '{portfolio}' from TradingGenerator [OK]")
            return True
    print(f"  [WARN] Portfolio '{portfolio}' not found to remove.")
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


def _read_cooldown_seconds(driver):
    """Parses the "MM:SS" countdown in #cooldownTimerText (the "Waiting for
    next trade" cooldown after a generate), or None if it's not present/
    visible -- e.g. the generate button is disabled for some other reason."""
    try:
        el = driver.find_element(By.ID, "cooldownTimerText")
    except Exception:
        return None
    if not el.is_displayed():
        return None
    match = re.match(r'^(\d+):(\d+)$', el.text.strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def generate_trade(driver, expected_company=None, expected_portfolio=None, force=False):
    """Clicks GENERATE NEW TRADE (#generateBtn) on the current (TradingGenerator) tab.

    `expected_company`/`expected_portfolio` (the "next trade to trade" hint
    read off the previous generate -- see read_trade_parameters -- or an
    explicit fallback portfolio picked by the caller) are selected first, if
    given.

    If the generate button is on its post-trade cooldown, this waits it out
    (reading the countdown itself) and retries rather than giving up.

    If TradingGenerator pops up its "wrong account" warning -- the selected
    company/portfolio doesn't match what it expects next -- behavior depends
    on `force`:
      - force=False (default): treat our selection as a mistake. Read the
        correct company/portfolio off the warning, cancel, switch, and retry
        (up to twice) -- this is the normal path, using TradingGenerator's
        own rotation.
      - force=True: we deliberately chose a specific company/portfolio
        (e.g. trying a fallback because the usual one is locked), so click
        "Proceed Anyway" instead of cancelling.

    Returns 'generated', 'locked' (the daily trade limit's been reached --
    the button relabels itself to something like "REACHED 4 TRADES TODAY -
    PORTFOLIO LOCKED"), 'wrong_account' (still mismatched after retrying, or
    "Proceed Anyway" didn't work), or 'not_found' (the generate button
    itself isn't on the page).
    """
    if expected_company:
        select_company(driver, expected_company)
    if expected_portfolio:
        select_portfolio(driver, expected_portfolio)

    wrong_account_attempts = 0
    cooldown_waits = 0
    while True:
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
            cooldown = _read_cooldown_seconds(driver)
            cooldown_waits += 1
            if cooldown is None or cooldown_waits > 5:
                print(f"  Generate button is disabled for an unrecognized reason: '{text}'")
                return 'locked'
            print(f"  On cooldown - waiting {cooldown}s for it to clear...")
            time.sleep(cooldown + 1)
            continue

        btn.click()
        print(f"  Clicked: '{text}' [OK]")
        humanize.long_pause(1, 2)

        warning = _read_wrong_account_warning(driver)
        if warning is None:
            return 'generated'

        _, warn_company, warn_portfolio = warning
        if force:
            print(f"  [WARN] TradingGenerator wants '{warn_company} / {warn_portfolio}' "
                  f"next, but proceeding anyway with the explicitly requested portfolio...")
            if not _click_visible(driver, ".modal-confirm"):
                print("  [FAIL] Could not confirm 'Proceed Anyway'.")
                return 'wrong_account'
            return 'generated'

        wrong_account_attempts += 1
        if wrong_account_attempts > 2:
            print("  [FAIL] Still on the wrong company/portfolio after switching - giving up.")
            return 'wrong_account'
        print(f"  [WARN] Wrong company/portfolio selected - TradingGenerator wants "
              f"'{warn_company} / {warn_portfolio}'. Cancelling and switching...")
        _click_visible(driver, ".modal-cancel")
        select_company(driver, warn_company)
        select_portfolio(driver, warn_portfolio)


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

    company, portfolio = read_active_company_portfolio(driver)

    next_company = _extract_by_class(driver, "next-acc-firm")
    next_portfolio = _extract_by_class(driver, "next-acc-account")

    return {
        'asset': asset,
        'direction': direction.upper() if direction else None,
        'contracts': int(contracts_match.group()) if contracts_match else None,
        'contract_size': re.sub(r'[\d\s]', '', contracts_raw) or None,
        'sl_ticks': int(sl_match.group(1)) if sl_match else None,
        'tp_ticks': int(tp_match.group(1)) if tp_match else None,
        'portfolio': portfolio,
        'company': company,
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
