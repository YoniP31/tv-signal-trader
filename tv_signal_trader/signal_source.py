import re

from selenium.webdriver.common.by import By

from . import config
from . import humanize
from . import login
from . import status
from . import trading


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


def _extract_trade_parameters(driver):
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


def _resolve_tv_symbol(asset, contract_size):
    """Maps a TradingGenerator asset/size to a TradingView continuous-futures
    ticker, e.g. ("NQ", "MINI") -> "MNQ1!". "1!" is TradingView's standard
    suffix for the continuous front-month contract of a futures symbol.
    """
    ticker = f"M{asset}" if (contract_size or '').upper() == 'MINI' else asset
    return f"{ticker}1!"


def load_chart_for_signal(driver, asset, contract_size):
    """Switches the current tab's chart to the symbol implied by asset/contract_size."""
    symbol = _resolve_tv_symbol(asset, contract_size)
    url = f"https://www.tradingview.com/chart/?symbol={symbol}"
    print(f"  Switching chart to {symbol}...")
    driver.get(url)
    humanize.long_pause(5, 8)
    print(f"  Chart loaded: {driver.title}")
    return symbol


def trade_from_website(driver):
    """Opens TradingGenerator in a new tab, generates and reads a trade signal,
    switches the chart to the implied symbol, and executes the trade."""
    print("\n[WEB] Opening website...")
    tv_tab = driver.current_window_handle
    try:
        driver.execute_script(f"window.open('{config.SIGNAL_SITE_URL}', '_blank');")
        humanize.long_pause(3, 5)

        all_tabs = driver.window_handles
        web_tab = all_tabs[-1]
        driver.switch_to.window(web_tab)
        print("  Website tab opened [OK]")
        humanize.long_pause(2, 3)

        tg_logged_in = login.ensure_tradinggenerator_login(driver)
        status.update(tradinggenerator_logged_in=tg_logged_in)
        if not tg_logged_in:
            print("  [FAIL] TradingGenerator login failed - aborting.")
            driver.switch_to.window(tv_tab)
            return
        humanize.long_pause(1, 2)

        print("  Looking for button...")
        clicked = False
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if 'generate new trade' in btn.text.strip().lower():
                btn.click()
                clicked = True
                print(f"  Clicked: '{btn.text.strip()}' [OK]")
                break
        if not clicked:
            print("  Button not found! Buttons seen on page:")
            for btn in driver.find_elements(By.TAG_NAME, "button"):
                text = btn.text.strip()
                if text:
                    print(f"    '{text}'")
            driver.switch_to.window(tv_tab)
            return

        humanize.long_pause(2, 3)

        print("  Reading trade parameters...")
        params = _extract_trade_parameters(driver)
        print(f"  Asset:       {params['asset']}")
        print(f"  Direction:   {params['direction']}")
        print(f"  Contracts:   {params['contracts']} {params['contract_size']}")
        print(f"  Stop Loss:   {params['sl_ticks']} ticks")
        print(f"  Take Profit: {params['tp_ticks']} ticks")

        driver.switch_to.window(tv_tab)
        print("  Back to TradingView [OK]")

        direction = {'LONG': 'buy', 'SHORT': 'sell'}.get(params['direction'])
        missing = [k for k in ('asset', 'contracts', 'sl_ticks', 'tp_ticks') if params[k] is None]
        if direction is None:
            missing.append('direction')
        if missing:
            print(f"  [FAIL] Missing trade parameters ({', '.join(missing)}) - not executing.")
            return

        load_chart_for_signal(driver, params['asset'], params['contract_size'])

        print(f"\n  Executing: {direction.upper()} | TP={params['tp_ticks']} ticks | "
              f"SL={params['sl_ticks']} ticks | contracts={params['contracts']}")
        result = trading.place_order(
            driver,
            tp_ticks=params['tp_ticks'],
            sl_ticks=params['sl_ticks'],
            side=direction,
            units=params['contracts'],
        )
        if result:
            print("\n[OK] Trade executed!")
        else:
            print("\n[FAIL] Trade execution failed!")

    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        try:
            driver.switch_to.window(tv_tab)
        except Exception:
            pass
