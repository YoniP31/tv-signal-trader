import random
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import config
from . import humanize
from . import panel


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
    """
    try:
        status_el = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="paper_trading"] [data-status]')
    except Exception:
        print("  Broker tab not found (not connected)")
        return False
    status = status_el.get_attribute("data-status")
    print(f"  Broker tab status: '{status}'")
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


def connect_tradovate(driver, username, password, timeout=15):
    """Opens the broker-connection flow, logs into Tradovate with the given
    credentials, and confirms the connection actually took.

    Takes credentials as plain arguments rather than reading them from
    config itself -- how they get stored (multiple accounts, etc.) isn't
    decided yet, so this stays agnostic to that and just uses whatever the
    caller hands it.

    Deliberately does not touch the broker-selection dialog's Live/Demo
    toggle -- picking the wrong one could connect a different account/mode
    than intended, so that choice is left at whatever TradingView already
    has pre-selected (its own remembered state) rather than guessed at here.
    """
    if is_tradovate_connected(driver):
        print("  Already connected to Tradovate [OK]")
        return True

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
    print("  Waiting for the Tradovate login tab...")
    login_tab = _wait_for_new_tab(driver, tabs_before, timeout=10)
    if login_tab is None:
        print("  FAILED: Tradovate login tab never opened.")
        return False

    driver.switch_to.window(login_tab)
    humanize.long_pause(1.0, 2.0)

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
    print("  Waiting for the login tab to close...")
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
    # rather than assume it worked immediately.
    elapsed = 0
    while elapsed < timeout:
        if is_tradovate_connected(driver):
            print("  Connected to Tradovate [OK]")
            return True
        time.sleep(1)
        elapsed += 1
    print("  FAILED: could not confirm connection after logging in.")
    return False


def disconnect_tradovate(driver, timeout=15):
    """Logs out of the currently connected Tradovate broker via the
    trade-dropdown menu's 'Log out' item."""
    if not is_tradovate_connected(driver):
        print("  Already disconnected from Tradovate [OK]")
        return True

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


def select_tradovate_account(driver, account_name, timeout=10):
    """Selects `account_name` (e.g. "APEX1871970000006") in the broker
    panel's account-selector dropdown -- the specific Tradovate sub-account
    the next trade should be placed against, distinct from which company's
    Tradovate *login* is connected (see connect_tradovate). A single login
    can have several sub-accounts (e.g. multiple eval accounts).

    No-ops (returns True) if that account is already selected. Matches the
    dropdown item by TradingView's stable data-qa-id="account-name-N"
    attribute rather than its CSS-module class names, which look
    build-specific and liable to change across TradingView releases.
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


def list_tradovate_accounts(driver):
    """Names of every Tradovate sub-account under the currently connected
    login, read from the broker panel's account-selector dropdown -- opens
    it to read the list, then closes it again."""
    if not _ensure_broker_panel_open(driver):
        print("  FAILED: could not open the broker panel to list accounts.")
        return []

    try:
        selector_btn = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="account-selector"]')
    except Exception:
        print("  FAILED: account-selector button not found.")
        return []

    driver.execute_script("arguments[0].click();", selector_btn)
    humanize.long_pause(0.6, 1.0)

    try:
        dropdown = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="account-dropdown"]')
    except Exception:
        print("  FAILED: account-dropdown list did not open.")
        return []

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
    ticker, e.g. ("NQ", "MINI") -> "MNQ1!". "1!" is TradingView's standard
    suffix for the continuous front-month contract of a futures symbol.
    """
    ticker = f"M{asset}" if (contract_size or '').upper() == 'MINI' else asset
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
    if not _click_orders_tab(driver):
        print("  FAILED: could not open the broker panel/Orders tab. Aborting.")
        return False
    tp_id = sl_id = None
    for _ in range(5):
        humanize.pause(1.0, 1.5)
        bracket = _find_working_bracket(driver)
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


def _click_orders_tab(driver):
    """Opens the broker panel if it's collapsed, then clicks the 'Orders'
    tab (a stable element id, not text-matched) so its table is rendered."""
    if not _ensure_broker_panel_open(driver):
        return False
    try:
        driver.find_element(By.ID, "orders").click()
    except Exception:
        print("  Orders tab not found")
        return False
    humanize.pause(0.5, 1.0)
    return True


def _find_working_bracket(driver):
    """Finds the data-row-id of the currently 'Working' Take Profit and Stop
    Loss orders in the (already-open) Orders table.

    The table keeps every historical order, including already-resolved
    brackets from earlier trades, so matching by Type text alone isn't
    enough -- filtering to Status 'working' is what scopes this to the
    live bracket (this bot only ever has one position open at a time, so
    at most one Take Profit and one Stop Loss order can be 'working'
    simultaneously).
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


def wait_for_close(driver, timeout=None, poll_interval=None):
    """Polls the broker's Orders table until the take-profit or stop-loss
    bracket order fills, returning 'tp' or 'sl'.

    A filled TP/SL order auto-cancels its linked sibling (confirmed by
    inspecting the real Orders table: the filled leg shows Status "Filled",
    the other "Cancelled"), so that Status is a direct, reliable signal --
    no need to infer the outcome from price or P&L sign. The specific
    bracket order IDs are captured once up front and polled by ID from then
    on, rather than re-matched by Type each time, so an older resolved
    order from a previous trade can never be mistaken for the current one.

    Returns None if the wait times out, the current bracket can't be found,
    or it resolves without either side actually filling (e.g. the position
    was closed manually) -- misreporting which side a trade closed on is
    worse than not reporting a result at all.
    """
    timeout = timeout if timeout is not None else config.TRADE_CLOSE_TIMEOUT_SECONDS
    poll_interval = poll_interval if poll_interval is not None else config.TRADE_CLOSE_POLL_INTERVAL_SECONDS

    print(f"  Waiting for the position to close (checking every {poll_interval}s)...")
    if not _click_orders_tab(driver):
        print("  Could not open the broker panel/Orders tab.")
        return None

    bracket = _find_working_bracket(driver)
    tp_id, sl_id = bracket.get('tp'), bracket.get('sl')
    if not tp_id or not sl_id:
        print(f"  Could not find the working TP/SL bracket orders (tp={tp_id}, sl={sl_id}).")
        return None

    elapsed = 0
    while elapsed < timeout:
        tp_status = _read_order_status(driver, tp_id)
        sl_status = _read_order_status(driver, sl_id)

        if tp_status == 'filled':
            print("  Take Profit filled [OK]")
            return 'tp'
        if sl_status == 'filled':
            print("  Stop Loss filled [OK]")
            return 'sl'
        if tp_status not in (None, 'working') and sl_status not in (None, 'working'):
            print(f"  Bracket resolved without a fill (TP='{tp_status}', "
                  f"SL='{sl_status}') - can't determine outcome.")
            return None

        time.sleep(poll_interval)
        elapsed += poll_interval

    print("  Timed out waiting for the position to close.")
    return None


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
