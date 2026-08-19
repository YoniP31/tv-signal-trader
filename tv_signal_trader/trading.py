import random
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import config
from . import humanize
from . import panel
from .logging_utils import timestamped_print as print


def _find_trade_button(driver):
    """Returns the visible top-toolbar Trade button element, or None.
    TradingView renders this button multiple times (hidden duplicates used
    for responsive-layout measurement), so this picks whichever copy is
    actually visible rather than assuming DOM order."""
    return driver.execute_script("""
        var all = document.querySelectorAll('[data-qa-id="trade-button"]');
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            var style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            var rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            return el;
        }
        return null;
    """)


def is_tradovate_connected(driver):
    """Checks the bottom broker panel's Tradovate tab button, via its nested
    data-status attribute. That button only exists in the DOM at all once a
    broker is actually connected -- the top-toolbar Trade button was tried
    first, but its text stays "Trade" either way; only a small icon next to
    it changes, which isn't something worth relying on.

    Returns True/False -- the button's absence is itself a reliable "not
    connected" signal here, not an error, so unlike other checks in this
    module this doesn't return None for "couldn't determine".

    Deliberately silent -- this is a low-level primitive called very
    often, including inside polling loops (see connect_tradovate/
    disconnect_tradovate), so printing on every single call would flood
    the log with "Broker tab status: ..." noise. Callers narrate the
    meaningful state transitions themselves instead.
    """
    try:
        status_el = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="paper_trading"] [data-status]')
    except Exception:
        return False
    status = status_el.get_attribute("data-status")
    return status == "connected"


def _wait_for_new_tab(driver, existing_tabs, timeout=10):
    """Polls until a tab not in `existing_tabs` appears, returning its
    handle, or None on timeout."""
    elapsed = 0.0
    while elapsed < timeout:
        new_tabs = set(driver.window_handles) - existing_tabs
        if new_tabs:
            return next(iter(new_tabs))
        time.sleep(0.5)
        elapsed += 0.5
    return None


def _wait_for_tab_to_close(driver, tab_handle, timeout=15):
    """Polls until `tab_handle` is no longer in the open window list."""
    elapsed = 0.0
    while elapsed < timeout:
        if tab_handle not in driver.window_handles:
            return True
        time.sleep(0.5)
        elapsed += 0.5
    return False


def _submit_tradovate_login(driver, username, password):
    """Fills and submits Tradovate's own login form -- a separate site
    (trader.tradovate.com) opened in a new tab by the Connect flow, not
    part of TradingView itself."""
    try:
        username_inp = driver.find_element(By.ID, "name-input")
        password_inp = driver.find_element(By.ID, "password-input")
    except Exception:
        print("  Tradovate login fields not found")
        return False

    panel.set_field(driver, username_inp, username)
    panel.set_field(driver, password_inp, password)

    for b in driver.find_elements(By.TAG_NAME, "button"):
        try:
            if b.is_displayed() and b.text.strip() == "Login":
                driver.execute_script("arguments[0].click();", b)
                return True
        except Exception:
            continue
    print("  Tradovate Login button not found")
    return False


def connect_tradovate(driver, username, password, company=None, timeout=15):
    """Opens the broker-connection flow, logs into Tradovate with the given
    credentials, and confirms the connection actually took.

    Takes credentials as plain arguments rather than reading them from
    config itself -- how they get stored (multiple accounts, etc.) isn't
    decided yet, so this stays agnostic to that and just uses whatever the
    caller hands it. `company` is purely for the narrative prints below
    (e.g. "Connecting to Apex Trader Funding Tradovate account...") -- pass
    it whenever the caller has it, since otherwise a multi-step
    disconnect/reconnect sequence reads as an opaque wall of low-level DOM
    polling with no indication of what's actually being attempted or why.

    Deliberately does not touch the broker-selection dialog's Live/Demo
    toggle -- picking the wrong one could connect a different account/mode
    than intended, so that choice is left at whatever TradingView already
    has pre-selected (its own remembered state) rather than guessed at here.
    """
    label = f"{company} Tradovate account" if company else "Tradovate account"
    if is_tradovate_connected(driver):
        print(f"  Already connected to the {label} [OK]")
        return True

    print(f"  Connecting to the {label}...")
    tv_tab = driver.current_window_handle
    tabs_before = set(driver.window_handles)

    trade_button = _find_trade_button(driver)
    if trade_button is None:
        print("  FAILED: Trade button not found.")
        return False
    driver.execute_script("arguments[0].click();", trade_button)
    humanize.long_pause(1.0, 1.5)

    try:
        tile = driver.find_element(By.CSS_SELECTOR, '[data-broker="TRADOVATE"]')
    except Exception:
        print("  FAILED: Tradovate broker tile not found.")
        return False
    driver.execute_script("arguments[0].click();", tile)
    humanize.long_pause(1.0, 1.5)

    clicked = False
    for b in driver.find_elements(By.TAG_NAME, "button"):
        try:
            if b.is_displayed() and b.text.strip() == "Connect":
                driver.execute_script("arguments[0].click();", b)
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        print("  FAILED: Connect button not found.")
        return False

    # Clicking Connect opens Tradovate's own login page in a new tab.
    login_tab = _wait_for_new_tab(driver, tabs_before, timeout=10)
    if login_tab is None:
        print("  FAILED: Tradovate login tab never opened.")
        return False

    driver.switch_to.window(login_tab)
    humanize.long_pause(1.0, 2.0)

    print("  Entering Tradovate login credentials...")
    if not _submit_tradovate_login(driver, username, password):
        print("  FAILED: could not submit Tradovate login form.")
        try:
            driver.switch_to.window(tv_tab)
        except Exception:
            pass
        return False

    # On success the login tab closes itself and focus is expected to
    # return to TradingView -- but that's not guaranteed, so wait for it to
    # actually close and switch back explicitly rather than assume it did.
    if not _wait_for_tab_to_close(driver, login_tab, timeout=15):
        print("  FAILED: Tradovate login tab never closed - login may have failed.")
        try:
            driver.switch_to.window(tv_tab)
        except Exception:
            pass
        return False

    driver.switch_to.window(tv_tab)
    humanize.long_pause(1.0, 2.0)

    # The connect flow may take a moment to reflect in the UI, so poll
    # rather than assume it worked immediately -- silently (see
    # is_tradovate_connected); only the final outcome is worth a line.
    elapsed = 0
    while elapsed < timeout:
        if is_tradovate_connected(driver):
            print("  Connected to Tradovate [OK]")
            return True
        time.sleep(1)
        elapsed += 1
    print("  FAILED: could not confirm connection after logging in.")
    return False


def disconnect_tradovate(driver, company=None, timeout=15):
    """Logs out of the currently connected Tradovate broker via the
    trade-dropdown menu's 'Log out' item. `company` is purely for the
    narrative prints, same as connect_tradovate -- pass whichever company
    the caller believes is currently connected, if known."""
    label = f"{company} Tradovate account" if company else "Tradovate account"
    if not is_tradovate_connected(driver):
        print(f"  Already disconnected from the {label} [OK]")
        return True

    print(f"  Disconnecting from the {label}...")
    try:
        dropdown_button = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="trade-dropdown-button"]')
    except Exception:
        print("  FAILED: trade-dropdown-button not found.")
        return False
    driver.execute_script("arguments[0].click();", dropdown_button)
    humanize.long_pause(0.6, 1.0)

    try:
        logout_item = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="trade-dropdown-item-log-out"]')
    except Exception:
        print("  FAILED: 'Log out' menu item not found.")
        return False
    driver.execute_script("arguments[0].click();", logout_item)
    humanize.long_pause(1.0, 2.0)

    # Logging out may take a moment to reflect in the UI, so poll rather
    # than assume it worked immediately.
    elapsed = 0
    while elapsed < timeout:
        if not is_tradovate_connected(driver):
            print("  Disconnected from Tradovate [OK]")
            return True
        time.sleep(1)
        elapsed += 1
    print("  FAILED: could not confirm disconnection after clicking Log out.")
    return False


def select_tradovate_account(driver, account_name, timeout=45):
    """Selects `account_name` (e.g. "APEX1871970000006") in the broker
    panel's account-selector dropdown -- the specific Tradovate sub-account
    the next trade should be placed against, distinct from which company's
    Tradovate *login* is connected (see connect_tradovate). A single login
    can have several sub-accounts (e.g. multiple eval accounts).

    No-ops (returns True) if that account is already selected. Matches the
    dropdown item by TradingView's stable data-qa-id="account-name-N"
    attribute rather than its CSS-module class names, which look
    build-specific and liable to change across TradingView releases.

    `timeout` only bounds the final confirmation wait (the account switch
    itself has already been clicked by then) -- on a slow/high-latency
    machine, actually loading the new account's data can take a while, so
    this defaults generously rather than giving up early on something
    that's still genuinely in progress. See
    select_tradovate_account_with_reconnect for what to use instead when
    the connection itself can drop mid-switch.
    """
    if not account_name:
        return True
    if not _ensure_broker_panel_open(driver):
        print("  FAILED: could not open the broker panel to select an account.")
        return False

    try:
        selector_btn = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="account-selector"]')
    except Exception:
        print("  FAILED: account-selector button not found.")
        return False

    if account_name in selector_btn.text:
        print(f"  Tradovate account already set to '{account_name}' [OK]")
        return True

    driver.execute_script("arguments[0].click();", selector_btn)
    humanize.long_pause(0.6, 1.0)

    try:
        dropdown = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="account-dropdown"]')
    except Exception:
        print("  FAILED: account-dropdown list did not open.")
        return False

    item = None
    for name_el in dropdown.find_elements(By.CSS_SELECTOR, '[data-qa-id^="account-name-"]'):
        if name_el.text.strip() == account_name:
            item = name_el
            break
    if item is None:
        print(f"  FAILED: Tradovate account '{account_name}' not found in the dropdown.")
        return False

    clickable = item.find_element(By.XPATH, './ancestor::div[@data-is-popover-item-button="true"][1]')
    driver.execute_script("arguments[0].click();", clickable)
    humanize.long_pause(0.6, 1.0)

    elapsed = 0
    while elapsed < timeout:
        try:
            selector_btn = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="account-selector"]')
            if account_name in selector_btn.text:
                print(f"  Switched Tradovate account to '{account_name}' [OK]")
                return True
        except Exception:
            pass
        time.sleep(0.5)
        elapsed += 0.5
    print(f"  FAILED: could not confirm switch to Tradovate account '{account_name}'.")
    return False


def select_tradovate_account_with_reconnect(driver, company, account_name, timeout=45):
    """select_tradovate_account, but if it fails *and* the Tradovate
    connection itself has dropped in the meantime, reconnects and retries
    the same account once instead of leaving it to the caller to give up
    and move on to something else.

    Switching accounts can occasionally drop the broker panel's connection
    entirely partway through loading the new account's data (more likely
    the longer it takes, e.g. on a slow/high-latency machine) -- from the
    caller's side that looks identical to the account genuinely being
    unavailable, but it isn't: the account was never actually confirmed
    bad, the connection just needs re-establishing. If the connection is
    still up after a failure, this is a real failure (e.g. the account
    genuinely isn't in the dropdown) and isn't retried.

    Returns True/False, same as select_tradovate_account. The caller's own
    connected_company tracking doesn't need updating either way -- any
    reconnect here is to the same company it already thought was
    connected.
    """
    if select_tradovate_account(driver, account_name, timeout=timeout):
        return True
    if is_tradovate_connected(driver):
        return False

    account = config.TRADOVATE_ACCOUNTS.get(company)
    if account is None:
        return False
    print(f"  [WARN] Lost the Tradovate connection while switching to '{account_name}' - "
          "reconnecting and retrying the same account...")
    if not connect_tradovate(driver, account['username'], account['password']):
        print(f"  [FAIL] Could not reconnect to Tradovate for '{company}' after losing the "
              f"connection while switching to '{account_name}'.")
        return False
    return select_tradovate_account(driver, account_name, timeout=timeout)


def list_tradovate_accounts(driver):
    """Names of every Tradovate sub-account under the currently connected
    login, read from the broker panel's account-selector dropdown -- opens
    it to read the list, then closes it again.

    Returns None (not []) if the list couldn't actually be read for any
    reason -- a caller that treats an empty list as "confirmed zero
    accounts" (e.g. sweep_liquidated_accounts, which removes any
    TradingGenerator portfolio not found in this list) must be able to
    tell that apart from "the read itself failed", since conflating the
    two would treat a transient DOM/timing glitch as grounds to remove
    every single portfolio. Only a *successful* read returns a real list,
    which may legitimately be empty.
    """
    if not _ensure_broker_panel_open(driver):
        print("  FAILED: could not open the broker panel to list accounts.")
        return None

    try:
        selector_btn = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="account-selector"]')
    except Exception:
        print("  FAILED: account-selector button not found.")
        return None

    driver.execute_script("arguments[0].click();", selector_btn)
    humanize.long_pause(0.6, 1.0)

    try:
        dropdown = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="account-dropdown"]')
    except Exception:
        print("  FAILED: account-dropdown list did not open.")
        return None

    names = [
        name_el.text.strip()
        for name_el in dropdown.find_elements(By.CSS_SELECTOR, '[data-qa-id^="account-name-"]')
        if name_el.text.strip()
    ]

    # Close the dropdown again rather than leaving it open.
    driver.execute_script("arguments[0].click();", selector_btn)
    humanize.long_pause(0.3, 0.6)

    return names


def resolve_symbol(asset, contract_size):
    """Maps a TradingGenerator asset/size to a TradingView continuous-futures
    ticker. "1!" is TradingView's standard suffix for the continuous
    front-month contract of a futures symbol.

    TradingGenerator's `asset` field is sometimes already the micro ticker
    itself (e.g. "MNQ") and sometimes the plain/mini ticker (e.g. "NQ"),
    independent of what `contract_size` says -- so:
      - ("NQ", "MINI")  -> "NQ1!"  (real mini, un-prefixed ticker)
      - ("NQ", "MICRO") -> "MNQ1!" (micro contract -> "M"-prefixed ticker)
      - ("MNQ", "MINI")  -> "MNQ1!" (asset is already the micro ticker --
        trust it as given rather than stripping the "M")
      - ("MNQ", "MICRO") -> "MNQ1!" (same, consistent)

    See config.DEV_TREAT_MINI_AS_MICRO: while developing from source, a
    plain-ticker asset with MINI is deliberately also routed to its micro
    ticker (safer to test against); the compiled .exe always uses the real
    mapping above.
    """
    asset = (asset or '').upper()
    if asset.startswith('M'):
        return f"{asset}1!"
    size = (contract_size or '').upper()
    use_micro_ticker = size == 'MICRO' or (size == 'MINI' and config.DEV_TREAT_MINI_AS_MICRO)
    ticker = f"M{asset}" if use_micro_ticker else asset
    return f"{ticker}1!"


def load_chart_for_signal(driver, asset, contract_size):
    """Switches the current tab's chart to the symbol implied by asset/contract_size."""
    symbol = resolve_symbol(asset, contract_size)
    url = f"https://www.tradingview.com/chart/?symbol={symbol}"
    print(f"  Switching chart to {symbol}...")
    driver.get(url)
    humanize.long_pause(5, 8)
    print(f"  Chart loaded: {driver.title}")
    return symbol


def place_order(driver, tp_ticks=150, sl_ticks=150, side="buy", units=1):
    # 1. Select Buy/Sell side, then confirm the order ticket actually opened.
    # It won't if e.g. no broker (Tradovate) connection is active -- in that
    # case every later step would just be flailing against a chart with no
    # order panel at all, so this has to be a hard stop, not a warning.
    print(f"\n[1] Selecting {side.upper()} side...")
    humanize.pause(0.4, 0.8)
    body = driver.find_element(By.TAG_NAME, "body")
    body.click()
    humanize.pause(0.3, 0.6)
    if side == "sell":
        body.send_keys(Keys.SHIFT + 's')
    else:
        body.send_keys(Keys.SHIFT + 'b')
    humanize.long_pause(1.5, 2.5)

    if not panel.is_order_ticket_open(driver):
        print("  FAILED: order ticket didn't open -- check that a broker "
              "(Tradovate) connection is active. Aborting.")
        return False
    print(f"  {side.upper()} [OK] - order ticket is open")

    # 2. Market order
    print("\n[2] Selecting Market order...")
    humanize.pause(0.5, 1.0)
    market_btn = panel.click_market_tab(driver)
    if market_btn is None:
        print("  FAILED: 'Market' order-type tab not found. Aborting.")
        return False
    humanize.long_pause(0.6, 1.2)
    selected = market_btn.get_attribute("aria-selected")
    if selected is not None:
        print(f"  Market [OK] (aria-selected='{selected}')")
    else:
        print("  Market [OK]")

    # 3. Units (making sure the quantity dropdown says "Units", not
    # "Contracts"/"Lots"/etc., before typing the contract count), verified
    # by reading the field back rather than trusting the typing succeeded.
    print(f"\n[3] Setting Units = {units}...")
    humanize.long_pause(0.5, 1.0)
    if not panel.ensure_units_mode(driver):
        print("  FAILED: could not confirm 'Units' mode. Aborting.")
        return False
    try:
        quantity_inp = driver.find_element(By.ID, "quantity-field")
    except Exception:
        print("  FAILED: quantity-field not found. Aborting.")
        return False
    panel.set_field(driver, quantity_inp, units)
    actual_units = quantity_inp.get_attribute("value")
    if actual_units != str(units):
        print(f"  FAILED: quantity field shows '{actual_units}', expected '{units}'. Aborting.")
        return False
    print(f"  Units = {units} [OK]")
    humanize.long_pause(0.5, 1.0)

    # 4. Reveal the Exits section (TP/SL controls) if it's collapsed
    print("\n[4] Making sure Exits section is expanded...")
    if not panel.ensure_exits_expanded(driver):
        print("  FAILED: could not expand/confirm the Exits section. Aborting.")
        return False
    print("  Exits section expanded [OK]")
    humanize.long_pause(0.5, 1.0)

    # 5. Enable TP/SL toggles
    print("\n[5] Enabling TP/SL toggles...")
    if not panel.enable_tp_sl_toggles(driver):
        print("  FAILED: could not confirm TP/SL toggles are on. Aborting.")
        return False
    print("  TP/SL toggles on [OK]")
    humanize.long_pause(1.0, 2.0)

    # 6. Switch TP/SL to Ticks mode
    print("\n[6] Switching TP/SL to Ticks mode...")
    if not panel.ensure_ticks_mode(driver, "order-ticket-take-profit-dropdown-button", "TP"):
        print("  FAILED: could not switch TP to Ticks mode. Aborting.")
        return False
    if not panel.ensure_ticks_mode(driver, "order-ticket-stop-loss-dropdown-button", "SL"):
        print("  FAILED: could not switch SL to Ticks mode. Aborting.")
        return False
    humanize.long_pause(0.4, 0.8)

    # 7. Enter the tick values, verified by reading each field back
    print("\n[7] Setting TP/SL tick values...")
    try:
        tp_inp = driver.find_element(By.CSS_SELECTOR, '[data-qa-id~="order-ticket-take-profit-input"]')
    except Exception:
        print("  FAILED: take-profit input not found. Aborting.")
        return False
    panel.set_field(driver, tp_inp, tp_ticks)
    actual_tp = tp_inp.get_attribute("value")
    if actual_tp != str(tp_ticks):
        print(f"  FAILED: take-profit field shows '{actual_tp}', expected '{tp_ticks}'. Aborting.")
        return False
    print(f"  TP = {tp_ticks} ticks [OK]")
    humanize.long_pause(0.4, 0.8)

    try:
        sl_inp = driver.find_element(By.CSS_SELECTOR, '[data-qa-id~="order-ticket-stop-loss-input"]')
    except Exception:
        print("  FAILED: stop-loss input not found. Aborting.")
        return False
    panel.set_field(driver, sl_inp, sl_ticks)
    actual_sl = sl_inp.get_attribute("value")
    if actual_sl != str(sl_ticks):
        print(f"  FAILED: stop-loss field shows '{actual_sl}', expected '{sl_ticks}'. Aborting.")
        return False
    print(f"  SL = {sl_ticks} ticks [OK]")
    humanize.long_pause(0.6, 1.2)

    # 8. Click the big Buy/Sell confirm button
    label = "Buy" if side == "buy" else "Sell"
    print(f"\n[8] Clicking {label} button...")
    humanize.long_pause(0.5, 1.0)
    clicked = False
    for b in driver.find_elements(By.TAG_NAME, "button"):
        try:
            if not b.is_displayed():
                continue
            if b.text.strip().startswith(label) and b.size['width'] > 150:
                humanize.pause(0.3, 0.7)
                driver.execute_script("arguments[0].click();", b)
                humanize.long_pause(1.5, 3.0)
                print("  Done!")
                clicked = True
                break
        except Exception:
            pass
    if not clicked:
        print(f"  FAILED: {label} button not found. Aborting.")
        return False

    # 9. Confirm the trade actually executed: both the Take Profit and Stop
    # Loss bracket orders should now be live ("Working") in the broker's
    # Orders table. TradingView/Tradovate only creates those child orders
    # once the entry itself has actually filled, so this alone proves both
    # the entry and both limits went through -- not just that we clicked
    # buttons that looked right.
    print("\n[9] Verifying the trade and TP/SL orders are live...")
    if not click_orders_tab(driver):
        print("  FAILED: could not open the broker panel/Orders tab. Aborting.")
        return False
    tp_id = sl_id = None
    for _ in range(5):
        humanize.pause(1.0, 1.5)
        bracket = find_working_bracket(driver)
        tp_id, sl_id = bracket.get('tp'), bracket.get('sl')
        if tp_id and sl_id:
            break
    if not tp_id or not sl_id:
        print(f"  FAILED: could not confirm both TP/SL orders are live (tp={tp_id}, sl={sl_id}).")
        return False
    print("  Take Profit and Stop Loss orders confirmed live [OK]")
    return True


def _ensure_broker_panel_open(driver):
    """Makes sure the bottom broker (Tradovate) panel is expanded.

    It can be collapsed to just its tab bar (38px tall) with the actual
    Positions/Orders content sitting in the DOM under a 'js-hidden' class --
    present, but not rendered/interactable. The toggle button's aria-label
    reliably reports which state it's in ("Open panel" vs "Collapse panel"),
    so that's checked directly rather than guessing from layout/visibility.
    """
    try:
        toggle = driver.find_element(By.CSS_SELECTOR, '[data-name="toggle-visibility-button"]')
    except Exception:
        print("  Broker panel toggle button not found")
        return False

    if toggle.get_attribute("aria-label") == "Open panel":
        driver.execute_script("arguments[0].click();", toggle)
        humanize.long_pause(0.8, 1.2)

    try:
        toggle = driver.find_element(By.CSS_SELECTOR, '[data-name="toggle-visibility-button"]')
    except Exception:
        print("  Broker panel toggle button not found after opening")
        return False
    return toggle.get_attribute("aria-label") != "Open panel"


def click_orders_tab(driver, attempts=5):
    """Opens the broker panel if it's collapsed, then clicks the 'Orders'
    tab (a stable element id, not text-matched) so its table is rendered.

    Retries briefly rather than giving up on the first miss: right after an
    order is placed, Tradovate pushes a live UI update to the broker panel
    (the new position/working orders appearing), which can leave '#orders'
    transiently not found for a moment even though the panel is genuinely
    open -- a plain re-render, not a real failure. On a slow/high-latency
    machine that window is wide enough to matter (caught live: consistently
    failed the post-trade verification step on a slower VPS, a single
    find_element with no retry losing the race every time, while the exact
    same code never hit it on a faster machine).
    """
    if not _ensure_broker_panel_open(driver):
        return False
    for attempt in range(attempts):
        try:
            driver.find_element(By.ID, "orders").click()
            humanize.pause(0.5, 1.0)
            return True
        except Exception:
            if attempt < attempts - 1:
                humanize.pause(0.5, 1.0)
    print("  Orders tab not found")
    return False


def click_account_summary_tab(driver):
    """Opens the broker panel if it's collapsed, then clicks the 'Account
    summary' tab (a stable element id, "summary", not text-matched) so its
    Account Info row (Total P/L, Net Liq, etc.) is rendered."""
    if not _ensure_broker_panel_open(driver):
        return False
    try:
        driver.find_element(By.ID, "summary").click()
    except Exception:
        print("  Account summary tab not found")
        return False
    humanize.pause(0.5, 1.0)
    return True


def read_total_pl(driver):
    """Reads the Account Summary tab's "Total P/L" value (e.g. 8.30, or a
    negative loss) for the currently selected Tradovate account. Call
    click_account_summary_tab first to make sure that tab is open.

    Matched by data-label="Total P/L" on the Account Info row's cell, same
    querySelector approach as the Orders table reads (find_working_bracket,
    check_bracket_status) rather than TradingView's CSS-module class names,
    which look build-specific.
    """
    result = driver.execute_script("""
        var cell = document.querySelector('td[data-label="Total P/L"]');
        return cell ? cell.innerText.trim() : null;
    """)
    if not result:
        print("  Could not find the Total P/L field.")
        return None
    try:
        return float(result.replace(',', ''))
    except ValueError:
        print(f"  Could not parse Total P/L from '{result}'.")
        return None


def find_working_bracket(driver):
    """Finds the data-row-id of the currently 'Working' Take Profit and Stop
    Loss orders in the (already-open) Orders table, for whichever Tradovate
    sub-account is currently selected.

    The table keeps every historical order, including already-resolved
    brackets from earlier trades, so matching by Type text alone isn't
    enough -- filtering to Status 'working' is what scopes this to the
    live bracket. At most one position can ever be open per sub-account (one
    trade per portfolio), so at most one Take Profit and one Stop Loss order
    can be 'working' simultaneously for the currently-selected account.
    """
    result = driver.execute_script("""
        var rows = document.querySelectorAll('tr[data-row-id]');
        var tp = null, sl = null;
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var typeCell = row.querySelector('td[data-label="Type"]');
            var statusCell = row.querySelector('td[data-label="Status"]');
            if (!typeCell || !statusCell) continue;
            var type = (typeCell.innerText || '').trim().toLowerCase();
            var status = (statusCell.innerText || '').trim().toLowerCase();
            if (status !== 'working') continue;
            if (type === 'take profit') tp = row.getAttribute('data-row-id');
            if (type === 'stop loss') sl = row.getAttribute('data-row-id');
        }
        return {tp: tp, sl: sl};
    """)
    return result or {}


def find_last_bracket(driver):
    """Finds the data-row-id of the most recent Take Profit and Stop Loss
    orders in the (already-open) Orders table, regardless of status --
    unlike find_working_bracket, which only matches 'working' rows.

    Used during startup reconciliation to identify a bracket pair that
    already resolved (Filled/Cancelled) before the bot could report it --
    the table lists newest first (see check_entry_rejected), so the first
    Take Profit and first Stop Loss typed rows encountered are the most
    recent pair.
    """
    result = driver.execute_script("""
        var rows = document.querySelectorAll('tr[data-row-id]');
        var tp = null, sl = null;
        for (var i = 0; i < rows.length; i++) {
            var typeCell = rows[i].querySelector('td[data-label="Type"]');
            if (!typeCell) continue;
            var type = (typeCell.innerText || '').trim().toLowerCase();
            if (type === 'take profit' && tp === null) tp = rows[i].getAttribute('data-row-id');
            if (type === 'stop loss' && sl === null) sl = rows[i].getAttribute('data-row-id');
            if (tp !== null && sl !== null) break;
        }
        return {tp: tp, sl: sl};
    """)
    return result or {}


def _read_order_status(driver, row_id):
    """Reads the Status cell of a specific order row by its data-row-id."""
    if row_id is None:
        return None
    return driver.execute_script("""
        var row = document.querySelector('tr[data-row-id="' + arguments[0] + '"]');
        if (!row) return null;
        var statusCell = row.querySelector('td[data-label="Status"]');
        if (!statusCell) return null;
        return (statusCell.innerText || '').trim().toLowerCase();
    """, row_id)


def check_bracket_status(driver, tp_id, sl_id):
    """Checks a specific, already-known TP/SL bracket pair's current status
    (by data-row-id) in the (already-open) Orders table for whichever
    Tradovate sub-account is currently selected.

    A filled TP/SL order auto-cancels its linked sibling, so that Status is
    a direct, reliable signal on its own -- no need to infer the outcome
    from price or P&L sign. Checks a specific, already-known bracket pair
    by ID rather than "whatever's currently working", so callers tracking
    several concurrent positions can check each one independently instead
    of only ever the single currently-working bracket.

    Returns 'tp' (take profit filled), 'sl' (stop loss filled),
    'manual_close' (both resolved without either filling -- the position
    was closed outside of our own TP/SL, e.g. manually or via liquidation),
    or 'open' (still working).
    """
    tp_status = _read_order_status(driver, tp_id)
    sl_status = _read_order_status(driver, sl_id)
    if tp_status == 'filled':
        return 'tp'
    if sl_status == 'filled':
        return 'sl'
    if tp_status not in (None, 'working') and sl_status not in (None, 'working'):
        return 'manual_close'
    return 'open'


def check_entry_rejected(driver):
    """Checks whether the most recent Market entry order was rejected by
    the broker -- call this when place_order() fails to confirm any
    working TP/SL brackets, to tell a rejection apart from some other
    failure (in which case no bracket orders were ever created at all,
    confirmed via a real "Rejected" order: Type=Market, Status=Rejected,
    no Take Profit/Stop Loss rows for it).

    The Orders table lists newest first, so the first Type="Market" row is
    the most recent entry attempt. Returns True if its Status is
    "rejected", False if it's anything else (or no Market row is found).
    """
    result = driver.execute_script("""
        var rows = document.querySelectorAll('tr[data-row-id]');
        for (var i = 0; i < rows.length; i++) {
            var typeCell = rows[i].querySelector('td[data-label="Type"]');
            if (!typeCell) continue;
            if ((typeCell.innerText || '').trim().toLowerCase() !== 'market') continue;
            var statusCell = rows[i].querySelector('td[data-label="Status"]');
            if (!statusCell) return false;
            return (statusCell.innerText || '').trim().toLowerCase() === 'rejected';
        }
        return false;
    """)
    return bool(result)


def read_account_balance(driver):
    """Reads the broker panel's "Account Balance" value (e.g. 25040.84) for
    the currently selected Tradovate account.

    Matched by the "Account Balance" label text and DOM structure (the
    title sits in a box whose next sibling holds the value) rather than
    TradingView's class names on this element, which are CSS-module hashes
    with no data-qa-id alternative available and look build-specific.
    """
    try:
        value_el = driver.find_element(
            By.XPATH,
            '//span[normalize-space(text())="Account Balance"]/parent::div/following-sibling::div[1]'
        )
    except Exception:
        print("  Could not find the Account Balance field.")
        return None
    try:
        return float(value_el.text.strip().replace(',', ''))
    except ValueError:
        print(f"  Could not parse account balance from '{value_el.text}'.")
        return None


def account_needs_removal(driver, account_type, tiers=None):
    """Reads the current account balance and checks whether it's crossed
    outside its account-size tier's allowed range (e.g. below ~$47,500 or
    above ~$53,000 for a $50K account) -- the account's blown-past-max-loss
    or hit-profit-target point, at which it needs removing from
    TradingGenerator.

    There's no way to read which size (25K/50K/etc.) an account actually
    is from the page, so this guesses by picking whichever tier's nominal
    size (`tiers`' keys, in `config.ACCOUNT_BALANCE_TIERS` by default) the
    balance is numerically closest to -- safe since the tiers' real ranges
    are far apart (a 50K account is never anywhere near a 25K account's
    ~$27K ceiling).

    `account_type` ('EVAL'/'LIVE', see
    tradinggenerator.read_active_account_type) picks which max applies for
    that tier, since eval/live accounts have different profit targets. If
    it can't be determined, falls back to the more permissive (higher) of
    EVAL/LIVE for that tier rather than risk prematurely removing an
    account we're unsure about.

    Call this right after a trade closes: that's the one moment we're
    certain which account is active and that no position is open, so the
    balance reading is trustworthy.

    Returns (needs_removal, balance, tier_size, tier_range):
      - needs_removal: True/False, or None if the balance couldn't be read
      - balance: the balance just read, or None
      - tier_size: the account-size tier it was matched against (its
        nominal size), or None if the balance couldn't be read
      - tier_range: {'min': float, 'max': float} -- the min, and the max
        actually resolved for `account_type` (or the fallback) -- or None
        if the balance couldn't be read
    """
    tiers = tiers if tiers is not None else config.ACCOUNT_BALANCE_TIERS
    balance = read_account_balance(driver)
    if balance is None:
        return None, None, None, None
    nearest_size = min(tiers, key=lambda size: abs(balance - size))
    tier = tiers[nearest_size]
    min_threshold = tier['min']
    if account_type in tier['max']:
        max_threshold = tier['max'][account_type]
    else:
        print(f"  [WARN] Unknown account type '{account_type}' - using the more permissive "
              "EVAL/LIVE max for this tier.")
        max_threshold = max(tier['max'].values())
    tier_range = {'min': min_threshold, 'max': max_threshold}
    print(f"  Account balance: {balance:.2f} (closest tier: {nearest_size} {account_type or '?'}, "
          f"allowed range: {min_threshold:.2f} - {max_threshold:.2f})")
    needs_removal = balance <= min_threshold or balance >= max_threshold
    if needs_removal:
        print(f"  [WARN] Account balance {balance:.2f} is outside its allowed range.")
    return needs_removal, balance, nearest_size, tier_range


def adjust_tp_for_max_balance(balance, tp_ticks, tp_dollars, max_balance, buffer_range=None):
    """If hitting this trade's take-profit would push the account's balance
    past `max_balance`, caps tp_ticks so the resulting balance instead lands
    at max_balance + a random buffer within `buffer_range` (dollars) --
    clearing the target by a small, randomized amount rather than
    overshooting it by however much the original TP happened to be worth.

    Pure function, no DOM access -- `tp_dollars` (the $ value of the
    current tp_ticks, e.g. from TradingGenerator's own displayed figure) is
    used to derive $-per-tick, rather than needing a separate per-asset
    tick-value table. Returns tp_ticks unchanged if it wouldn't cross
    max_balance, or if tp_ticks/tp_dollars aren't usable (e.g. zero).

    Never returns less than 1 tick.
    """
    buffer_range = buffer_range if buffer_range is not None else config.TP_CAP_BUFFER_RANGE
    if not tp_ticks or not tp_dollars:
        return tp_ticks
    if balance + tp_dollars <= max_balance:
        return tp_ticks
    dollar_per_tick = tp_dollars / tp_ticks
    buffer = random.uniform(*buffer_range)
    target_profit = (max_balance + buffer) - balance
    adjusted_ticks = max(1, round(target_profit / dollar_per_tick))
    print(f"  TP would push balance past its max ({balance:.2f} + {tp_dollars:.2f} > "
          f"{max_balance:.2f}) - capping TP from {tp_ticks} to {adjusted_ticks} ticks "
          f"(buffer ${buffer:.2f} above max).")
    return adjusted_ticks


def adjust_ticks_for_daily_pnl(total_pl, tp_ticks, tp_dollars, sl_ticks, sl_dollars,
                                daily_profit_limit, daily_loss_limit, buffer_range=None):
    """If hitting this trade's take-profit/stop-loss would push today's P&L
    (see read_total_pl) past daily_profit_limit/daily_loss_limit, caps the
    relevant side so the result instead lands a random buffer (dollars,
    buffer_range) short of the limit -- same idea as
    adjust_tp_for_max_balance, but applied to today's cumulative P&L
    instead of account balance, and to both sides: a losing trade's
    stop-loss can push today's P&L past the daily loss limit just as a
    winning trade's take-profit can push it past the daily profit limit.

    Pure function, no DOM access. Returns (tp_ticks, sl_ticks) unchanged
    for whichever side has no limit configured, an unusable dollar figure
    (e.g. zero), an unreadable total_pl, or wouldn't cross its limit.
    Never returns less than 1 tick for either side.
    """
    buffer_range = buffer_range if buffer_range is not None else config.DAILY_PNL_CAP_BUFFER_RANGE
    adjusted_tp_ticks = tp_ticks
    adjusted_sl_ticks = sl_ticks

    if total_pl is None:
        return adjusted_tp_ticks, adjusted_sl_ticks

    if daily_profit_limit is not None and tp_ticks and tp_dollars:
        if total_pl + tp_dollars > daily_profit_limit:
            dollar_per_tick = tp_dollars / tp_ticks
            buffer = random.uniform(*buffer_range)
            target_profit = (daily_profit_limit - buffer) - total_pl
            adjusted_tp_ticks = max(1, round(target_profit / dollar_per_tick))
            print(f"  TP would push today's P&L past its daily profit limit ({total_pl:.2f} + "
                  f"{tp_dollars:.2f} > {daily_profit_limit:.2f}) - capping TP from {tp_ticks} to "
                  f"{adjusted_tp_ticks} ticks (buffer ${buffer:.2f} below limit).")

    if daily_loss_limit is not None and sl_ticks and sl_dollars:
        if total_pl - sl_dollars < -daily_loss_limit:
            dollar_per_tick = sl_dollars / sl_ticks
            buffer = random.uniform(*buffer_range)
            target_loss = total_pl + daily_loss_limit - buffer
            adjusted_sl_ticks = max(1, round(target_loss / dollar_per_tick))
            print(f"  SL would push today's P&L past its daily loss limit ({total_pl:.2f} - "
                  f"{sl_dollars:.2f} < {-daily_loss_limit:.2f}) - capping SL from {sl_ticks} to "
                  f"{adjusted_sl_ticks} ticks (buffer ${buffer:.2f} above limit).")

    return adjusted_tp_ticks, adjusted_sl_ticks
