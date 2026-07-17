import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import config
from . import humanize
from . import panel


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
