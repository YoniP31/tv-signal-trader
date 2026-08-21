import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import browser
from . import config
from . import humanize
from . import panel
from .logging_utils import timestamped_print as print


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


def open_tab(driver, other_tab, hide_window=None):
    """Finds an already-open TradingGenerator window, or opens a new one.

    `other_tab` (e.g. the TradingView tab) is skipped when searching, so the
    loop doesn't mistake it for TradingGenerator. Leaves the driver switched
    to the TradingGenerator window.

    `hide_window` (None = use config.HIDE_TRADINGGENERATOR_WINDOW) controls
    which of two ways it's opened -- only takes effect when a new window is
    actually created here; if one's already open (found above), it keeps
    whatever visibility it was originally created with:
      - True (default): with explicit window features (not a plain
        `window.open(url, '_blank')`, which Chrome treats as a new tab of
        the same window), so it becomes its own separate OS-level window,
        hidden via browser.hide_new_window -- not reachable through normal
        user interaction. A tab has no hwnd of its own to hide
        independently of the TradingView window, hence the window instead
        of a tab here.
      - False: a plain, visible tab of the main browser window, no hiding
        -- useful for debugging, e.g. to actually watch what
        TradingGenerator is doing.
    """
    if hide_window is None:
        hide_window = config.HIDE_TRADINGGENERATOR_WINDOW

    site_host = config.SIGNAL_SITE_URL.split('//')[-1].split('/')[0]
    for handle in driver.window_handles:
        if handle == other_tab:
            continue
        driver.switch_to.window(handle)
        if site_host in driver.current_url:
            return handle

    driver.switch_to.window(other_tab)
    if hide_window:
        # Snapshotting hwnds right before window.open (rather than after)
        # and hiding whichever one is new, as soon as it exists, catches it
        # long before hide_window_by_title could -- that has to wait for
        # the page to load and set its real title first, during which the
        # window sits fully visible on screen.
        before_hwnds = browser.snapshot_hwnds()
        driver.execute_script(
            f"window.open('{config.SIGNAL_SITE_URL}', '_blank', 'width=1280,height=800');"
        )
        hidden_immediately = browser.hide_new_window(before_hwnds)
    else:
        driver.execute_script(f"window.open('{config.SIGNAL_SITE_URL}', '_blank');")
    humanize.long_pause(3, 5)
    web_tab = driver.window_handles[-1]
    driver.switch_to.window(web_tab)
    humanize.long_pause(2, 3)

    if hide_window:
        if hidden_immediately:
            print("  TradingGenerator window hidden from the taskbar/Alt-Tab [OK]")
        elif browser.hide_window_by_title("Trading Generator"):
            # Fallback path -- the window was visible for however long this
            # took to find it by title, unlike the immediate path above.
            print("  TradingGenerator window hidden from the taskbar/Alt-Tab (fallback) [OK]")
        else:
            print("  [WARN] Could not hide the TradingGenerator window (not on Windows, or "
                  "its title wasn't found in time) - it'll stay visible.")

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


def read_active_account_type(driver):
    """Reads whether the currently active portfolio is an 'EVAL' or 'LIVE'
    account, from the badge next to the portfolio title (#activeTypeBadge,
    e.g. <span id="activeTypeBadge" class="active-type-badge live">LIVE</span>).
    Returns the uppercased text, or None if it can't be read."""
    raw = _extract_by_id(driver, "activeTypeBadge")
    return raw.strip().upper() if raw else None


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


def add_company(driver, company):
    """Creates a new company tab in #companiesBar via the '+ Company'
    button and its modal, then selects it. Always attempts creation --
    doesn't check list_companies(driver) itself first, since the caller
    (cli.py's add_accounts command) already needs to do that check anyway
    to decide whether to call this at all or reuse an existing tab.
    Returns True once the new tab is confirmed present afterward."""
    if not _click_visible(driver, "#companiesBar .add-btn-company"):
        print("  [FAIL] '+ Company' button not found.")
        return False
    try:
        name_input = driver.find_element(By.ID, "companyNameInput")
    except Exception:
        print("  [FAIL] Company name field not found.")
        return False
    panel.set_field(driver, name_input, company)
    if not _click_visible(driver, ".btn-confirm-purple"):
        print("  [FAIL] Could not confirm company creation.")
        return False
    humanize.long_pause(1, 2)
    if company not in list_companies(driver):
        print(f"  [FAIL] '{company}' doesn't appear in the companies list after creating it.")
        return False
    select_company(driver, company)
    print(f"  Created company '{company}' in TradingGenerator [OK]")
    return True


def _click_add_portfolio_button(driver, attempts=6):
    """Clicks the single-portfolio '+ Portfolio' button in #portfolioBar.
    Matched by its own text, not just its 'add-portfolio-btn' class --
    TradingGenerator has a second button with that exact same class for
    bulk creation ('+ N Portfolios'), so relying on DOM order alone to
    pick the right one would be fragile."""
    for _ in range(attempts):
        for btn in driver.find_elements(By.CSS_SELECTOR, "#portfolioBar .add-portfolio-btn"):
            if btn.is_displayed() and btn.text.strip() == "+ Portfolio":
                btn.click()
                humanize.long_pause(1, 2)
                return True
        humanize.pause(0.4, 0.7)
    return False


def add_portfolio(driver, portfolio, account_type='live'):
    """Creates a new portfolio tab in #portfolioBar for whichever company
    is currently selected, via the '+ Portfolio' button and its modal.
    `account_type` is 'live' (default -- matches the modal's own
    pre-selected state, so nothing extra is clicked) or 'eval' (clicks the
    EVAL option before confirming).

    Always attempts creation -- doesn't check list_portfolios(driver)
    itself first, same reasoning as add_company above: the caller already
    needs that check to decide whether to call this at all.

    Returns True once the new tab is confirmed present afterward."""
    if not _click_add_portfolio_button(driver):
        print("  [FAIL] '+ Portfolio' button not found.")
        return False
    try:
        name_input = driver.find_element(By.ID, "portfolioNameInput")
    except Exception:
        print("  [FAIL] Portfolio name field not found.")
        return False
    panel.set_field(driver, name_input, portfolio)
    if account_type == 'eval':
        if not _click_visible(driver, "#optPaper"):
            print("  [FAIL] Could not select the EVAL portfolio type.")
            return False
    if not _click_visible(driver, ".btn-confirm-green"):
        print("  [FAIL] Could not confirm portfolio creation.")
        return False
    humanize.long_pause(1, 2)
    if portfolio not in list_portfolios(driver):
        print(f"  [FAIL] '{portfolio}' doesn't appear in the portfolio list after creating it.")
        return False
    print(f"  Created portfolio '{portfolio}' ({account_type.upper()}) [OK]")
    return True


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


def _extract_dollar_amount(driver, element_id):
    """Parses the leading "$X,XXX" dollar amount out of an element's text
    (e.g. #takeProfitSub's "$1,500  ·  $5.00/tick"). Returns a float, or
    None if the element or a dollar amount in it can't be found."""
    raw = _extract_by_id(driver, element_id)
    if not raw:
        return None
    match = re.search(r'\$([\d,]+(?:\.\d+)?)', raw)
    if not match:
        return None
    return float(match.group(1).replace(',', ''))


def read_eval_portfolios(driver):
    """Reads TradingGenerator's "Taken in the following portfolios" list
    (#evalPortfoliosBox / #evalPortfoliosList's .eval-p-chip spans) -- when
    visible, this signal should be opened on *every* one of these
    portfolios (all the same company), not just the single currently-active
    one -- the currently-active portfolio is itself included in this list,
    not listed separately from it.

    Returns an empty list if the box isn't showing (a normal single-
    portfolio signal).
    """
    try:
        box = driver.find_element(By.ID, "evalPortfoliosBox")
    except Exception:
        return []
    if not box.is_displayed():
        return []
    return [
        chip.text.strip()
        for chip in box.find_elements(By.CSS_SELECTOR, ".eval-p-chip")
        if chip.text.strip()
    ]


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
    account_type = read_active_account_type(driver)
    tp_dollars = _extract_dollar_amount(driver, "takeProfitSub")
    sl_dollars = _extract_dollar_amount(driver, "stopLossSub")

    next_company = _extract_by_class(driver, "next-acc-firm")
    next_portfolio = _extract_by_class(driver, "next-acc-account")
    eval_portfolios = read_eval_portfolios(driver)

    return {
        'asset': asset,
        'direction': direction.upper() if direction else None,
        'contracts': int(contracts_match.group()) if contracts_match else None,
        'contract_size': re.sub(r'[\d\s]', '', contracts_raw) or None,
        'sl_ticks': int(sl_match.group(1)) if sl_match else None,
        'tp_ticks': int(tp_match.group(1)) if tp_match else None,
        'tp_dollars': tp_dollars,
        'sl_dollars': sl_dollars,
        'portfolio': portfolio,
        'company': company,
        'account_type': account_type,
        'eval_portfolios': eval_portfolios,
        'next_company': next_company or None,
        'next_portfolio': next_portfolio or None,
    }


RESULT_BUTTON_LABELS = {
    'tp': 'take profit',
    'sl': 'stop loss',
    'not_taken': 'trade not taken',
}


def report_trade_result(driver, outcome, company=None, portfolio=None):
    """Clicks the matching TRADE RESULT button on the current (TradingGenerator)
    tab. `outcome` is one of 'tp', 'sl', 'not_taken'.

    If that button can't be found -- the Trade Result prompt isn't in the
    state we expect, e.g. after a new trading day resets it, or a
    TradingGenerator-side bug -- falls back to clicking this
    company/portfolio's own '(X) Close Trade' button in the "OPEN TRADES"
    grid instead, when company/portfolio are given: that's the only other
    way TradingGenerator offers to clear a stuck open trade."""
    label = RESULT_BUTTON_LABELS[outcome]
    for btn in driver.find_elements(By.TAG_NAME, "button"):
        text = btn.text.strip()
        if text.lower().startswith(label):
            btn.click()
            print(f"  Reported result: '{text}' [OK]")
            return True
    print(f"  WARNING: could not find a '{label}' result button")
    if company and portfolio and close_open_trade_card(driver, company, portfolio):
        return True
    return False


def has_pending_trade_result(driver):
    """Whether the Trade Result prompt (#tradeResultSection) is currently
    open for the selected company/portfolio -- i.e. a trade was generated
    here but its result was never reported (e.g. the bot crashed before
    reporting it). The section's 'visible' class toggles whether it's
    actually shown."""
    try:
        section = driver.find_element(By.ID, "tradeResultSection")
    except Exception:
        return False
    return 'visible' in (section.get_attribute('class') or '').split()


def _card_company_portfolio(card):
    try:
        company = card.find_element(By.CSS_SELECTOR, ".meta-company").text.strip()
        portfolio = card.find_element(By.CSS_SELECTOR, ".meta-portfolio").text.strip()
    except Exception:
        return None, None
    return company, portfolio


def list_open_trade_cards(driver, company, portfolio):
    """Every card in TradingGenerator's "OPEN TRADES" grid (.open-trade-card)
    for this exact company/portfolio, in DOM order -- which is also visual
    left-to-right order, i.e. newest first, oldest last.

    Unlike the Trade Result prompt (has_pending_trade_result, which only
    ever applies to whichever portfolio is currently selected), this grid
    lists every portfolio TradingGenerator currently considers to have an
    open trade, regardless of selection. Tradovate only ever allows one
    real open position per sub-account, but this list can still come back
    with more than one card for the same company/portfolio -- stale
    duplicates left behind by an earlier, never-cleared attempt -- which is
    exactly the case close_open_trade_cards below exists to clean up."""
    return [
        card for card in driver.find_elements(By.CSS_SELECTOR, ".open-trade-card")
        if _card_company_portfolio(card) == (company, portfolio)
    ]


def has_open_trade_card(driver, company, portfolio):
    """Whether TradingGenerator's "OPEN TRADES" grid still lists at least
    one trade for this company/portfolio. Can be True even when
    has_pending_trade_result is False -- seen after a new trading day
    resets the Trade Result prompt, or a TradingGenerator-side bug -- in
    which case there's no Trade Result button to report through, and
    close_open_trade_card below is the only way to clear the stale entry."""
    return bool(list_open_trade_cards(driver, company, portfolio))


def _click_card_close_button(card):
    try:
        card.find_element(By.CSS_SELECTOR, ".otl-close").click()
        return True
    except Exception:
        return False


def close_open_trade_card(driver, company, portfolio):
    """Clicks the '(X) Close Trade' button on this company/portfolio's
    newest card in TradingGenerator's "OPEN TRADES" grid -- the fallback
    way to clear a stale open-trade entry when the normal Trade Result
    prompt isn't available to report through (see has_open_trade_card).
    Returns True if a matching card was found and clicked. If more than
    one card exists for this company/portfolio, see close_open_trade_cards
    to clear all of them at once."""
    cards = list_open_trade_cards(driver, company, portfolio)
    if not cards:
        return False
    if not _click_card_close_button(cards[0]):
        print(f"  [WARN] Found '{company} / {portfolio}' in TradingGenerator's Open Trades grid "
              "but couldn't click its Close Trade button.")
        return False
    print(f"  Closed stale 'Open Trades' entry for '{company} / {portfolio}' in TradingGenerator [OK]")
    return True


def close_open_trade_cards(driver, company, portfolio, keep_newest=False):
    """Closes every card for this company/portfolio in TradingGenerator's
    "OPEN TRADES" grid, via each one's own Close Trade button --
    TradingGenerator can end up with more than one card for the same
    account (stale duplicates left behind by an earlier, never-cleared
    attempt), even though Tradovate only ever allows one real open
    position per sub-account.

    If `keep_newest`, leaves the first (newest, leftmost) card alone and
    only closes the rest -- use this when Tradovate confirms a position
    really is still open, so the newest card keeps tracking it while any
    older, stale duplicates get cleared alongside it.

    Re-queries the grid fresh before every click rather than closing
    everything off one upfront list -- closing a card makes TradingGenerator
    re-render the grid, which can invalidate (stale-element) the WebElement
    references already held for every *other* card that was fetched at the
    same time, one click before it actually gets used.

    Returns how many cards were successfully closed."""
    target_remaining = 1 if keep_newest else 0
    close_at_index = 1 if keep_newest else 0
    initial_count = len(list_open_trade_cards(driver, company, portfolio))
    closed = 0
    for _ in range(initial_count):
        cards = list_open_trade_cards(driver, company, portfolio)
        if len(cards) <= target_remaining:
            break
        if not _click_card_close_button(cards[close_at_index]):
            print(f"  [WARN] Found a stale 'Open Trades' entry for '{company} / {portfolio}' but "
                  "couldn't click its Close Trade button.")
            break
        closed += 1
        humanize.pause(0.4, 0.8)
    return closed


def save_backup(driver):
    """Clicks TradingGenerator's "Save Backup" button (.backup-btn-save),
    which downloads a .json backup of its current state -- run once the
    trading session ends for the day."""
    try:
        btn = driver.find_element(By.CSS_SELECTOR, ".backup-btn-save")
    except Exception:
        print("  [WARN] Save Backup button not found.")
        return False
    btn.click()
    humanize.long_pause(1, 2)
    print("  TradingGenerator backup saved [OK]")
    return True


def _send_admin_code_to_prompt(driver, code, attempts=10):
    """Waits briefly for the native browser window.prompt() dialog that
    clicking #flipModeBtn/#secondWithdrawalBtn triggers (confirmed via
    screenshot -- there's no page-side DOM for it at all, so this is the
    only way to interact with it), types `code` into it, and accepts (OK).
    Returns True once found and accepted -- False if no prompt ever
    appeared (e.g. the click landed on nothing)."""
    for _ in range(attempts):
        try:
            alert = driver.switch_to.alert
            alert.send_keys(code)
            alert.accept()
            humanize.long_pause(1, 2)
            return True
        except Exception:
            humanize.pause(0.3, 0.6)
    return False


def is_flip_mode_active(driver):
    """Reads #flipModeBtn's current label to tell whether Flip Mode is on
    for whichever portfolio is currently selected. Confirmed via live
    testing: reads "Enable Flip Mode" when off, "FLIP MODE ON — click to
    turn off" once on (an earlier guess -- matching "disable" in the
    label -- was wrong; the real "on" wording doesn't contain that word at
    all, and was silently falling through to the None/can't-tell case).
    Always prints the raw label read. Returns None if the button can't be
    found, or its label matches neither confirmed wording -- callers must
    treat None as "unknown", never as "off" (see _toggle_flip_mode)."""
    try:
        btn = driver.find_element(By.ID, "flipModeBtn")
    except Exception:
        print("  [WARN] #flipModeBtn not found.")
        return None
    label = btn.text.strip()
    print(f"  #flipModeBtn label: '{label}'")
    lowered = label.lower()
    if "flip mode on" in lowered:
        return True
    if "enable flip mode" in lowered:
        return False
    print("  [WARN] #flipModeBtn's label doesn't match either known wording - can't tell its state.")
    return None


def _toggle_flip_mode(driver, password, target_active, action_label):
    current = is_flip_mode_active(driver)
    if current is None:
        # #flipModeBtn is a single toggle, not a separate on/off pair --
        # clicking it without knowing the current state risks flipping it
        # the *wrong* way (e.g. turning off Flip Mode that was actually
        # already on, right when the state machine most needs it to stay
        # on). Refusing beats guessing here, even though the label
        # wording is now confirmed -- this only trips if TradingGenerator
        # changes it again, or the button doesn't render as expected.
        print("  [FAIL] Can't tell Flip Mode's current state - refusing to click #flipModeBtn "
              "rather than risk toggling it the wrong way. See the label logged above.")
        return False
    if current is target_active:
        print(f"  Flip Mode already {'enabled' if target_active else 'disabled'} - nothing to do.")
        return True
    try:
        btn = driver.find_element(By.ID, "flipModeBtn")
    except Exception:
        print("  [FAIL] #flipModeBtn not found.")
        return False
    btn.click()
    if not _send_admin_code_to_prompt(driver, password):
        print(f"  [FAIL] No admin-code prompt appeared after clicking #flipModeBtn ({action_label}).")
        return False
    if is_flip_mode_active(driver) is not target_active:
        print(f"  [FAIL] Flip Mode doesn't show as {'enabled' if target_active else 'disabled'} "
              f"after {action_label} it.")
        return False
    print(f"  Flip Mode {'enabled' if target_active else 'disabled'} [OK]")
    return True


def enable_flip_mode(driver, password):
    """Clicks #flipModeBtn and enters `password` into the native admin-code
    prompt it triggers, turning Flip Mode on for whichever portfolio is
    currently selected. No-ops (returns True without clicking anything) if
    it's already on -- #flipModeBtn is a single toggle, not a separate
    on/off pair, so clicking it while already active would turn it back
    OFF instead of leaving it alone."""
    return _toggle_flip_mode(driver, password, True, "enabling")


def disable_flip_mode(driver, password):
    """The reverse of enable_flip_mode -- same toggle button, same
    no-op-if-already-there guard."""
    return _toggle_flip_mode(driver, password, False, "disabling")


def is_second_withdrawal_marked(driver):
    """Reads #secondWithdrawalBtn's current label to tell whether the
    currently selected portfolio is already marked. Confirmed live: reads
    "Mark as Second Withdrawal" when not marked, "Second Withdrawal —
    click to cancel" once marked -- it's a genuine toggle button, same
    mechanics as #flipModeBtn, *not* the one-way flag its intended usage
    (see the Flip Mode plan) originally suggested. An earlier version
    trusted only the `disabled` DOM attribute and always read as "not
    marked" as a result -- confirmed live to be wrong (see
    mark_second_withdrawal's docstring for why that mattered).

    Always prints the raw label read. Returns None if the button can't be
    found, or its label matches neither confirmed wording -- callers must
    treat None as "unknown", never as "not marked" (see
    mark_second_withdrawal)."""
    try:
        btn = driver.find_element(By.ID, "secondWithdrawalBtn")
    except Exception:
        print("  [WARN] #secondWithdrawalBtn not found.")
        return None
    label = btn.text.strip()
    print(f"  #secondWithdrawalBtn label: '{label}'")
    lowered = label.lower()
    if "click to cancel" in lowered:
        return True
    if "mark as second withdrawal" in lowered:
        return False
    print("  [WARN] #secondWithdrawalBtn's label doesn't match either known wording - can't tell its state.")
    return None


def mark_second_withdrawal(driver, password):
    """Clicks #secondWithdrawalBtn and enters `password` into the native
    admin-code prompt it triggers, marking the currently selected
    portfolio for second withdrawal.

    Confirmed live to be a single toggle button, same as #flipModeBtn ("✓
    Second Withdrawal — click to cancel" is a real, clickable un-mark
    state) -- not the one-way flag its intended usage (see the Flip Mode
    plan) originally suggested. An earlier version treated re-clicking as
    harmless and skipped this check; confirmed live that it actually
    cancels the mark right back off, which is exactly the bug this guards
    against now: no-ops (returns True without clicking) if it already
    reads as marked, and refuses to click at all (returns False) if the
    current state can't be determined, same reasoning as
    _toggle_flip_mode."""
    current = is_second_withdrawal_marked(driver)
    if current is None:
        print("  [FAIL] Can't tell whether second withdrawal is already marked - refusing to click "
              "#secondWithdrawalBtn rather than risk cancelling it if it's already on. See the label "
              "logged above.")
        return False
    if current:
        print("  Already marked as second withdrawal - nothing to do.")
        return True
    try:
        btn = driver.find_element(By.ID, "secondWithdrawalBtn")
    except Exception:
        print("  [FAIL] #secondWithdrawalBtn not found.")
        return False
    btn.click()
    if not _send_admin_code_to_prompt(driver, password):
        print("  [FAIL] No admin-code prompt appeared after clicking #secondWithdrawalBtn.")
        return False
    if not is_second_withdrawal_marked(driver):
        print("  [FAIL] Doesn't show as marked for second withdrawal after marking it.")
        return False
    print("  Marked as second withdrawal [OK]")
    return True
