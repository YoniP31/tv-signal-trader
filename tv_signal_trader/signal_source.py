from . import config
from . import humanize
from . import status
from . import trading
from . import tradinggenerator as tg


def run_web_loop(driver):
    """Repeatedly: generates a TradingGenerator signal, executes it on
    TradingView, waits for it to close, and reports the result back --
    until the daily trade limit locks the portfolio or something goes wrong.
    Stoppable with Ctrl+C at any point.
    """
    print("\n[WEB] Starting automatic trading loop (Ctrl+C to stop)...")
    tv_tab = driver.current_window_handle
    try:
        web_tab = tg.open_tab(driver, tv_tab)
        print("  TradingGenerator tab ready [OK]")

        while True:
            driver.switch_to.window(web_tab)

            tg_logged_in = tg.ensure_logged_in(driver)
            status.update(tradinggenerator_logged_in=tg_logged_in)
            if not tg_logged_in:
                print("  [FAIL] TradingGenerator login failed - stopping loop.")
                return

            outcome = tg.generate_trade(driver)
            if outcome == 'locked':
                print("  Daily trade limit reached - stopping loop.")
                return
            if outcome == 'not_found':
                print("  [FAIL] Could not generate a trade - stopping loop.")
                return

            humanize.long_pause(2, 3)
            params = tg.read_trade_parameters(driver)
            print(f"  Asset:       {params['asset']}")
            print(f"  Direction:   {params['direction']}")
            print(f"  Contracts:   {params['contracts']} {params['contract_size']}")
            print(f"  Stop Loss:   {params['sl_ticks']} ticks")
            print(f"  Take Profit: {params['tp_ticks']} ticks")

            direction = {'LONG': 'buy', 'SHORT': 'sell'}.get(params['direction'])
            missing = [k for k in ('asset', 'contracts', 'sl_ticks', 'tp_ticks') if params[k] is None]
            if direction is None:
                missing.append('direction')
            if missing:
                print(f"  [FAIL] Missing trade parameters ({', '.join(missing)}) - stopping loop.")
                tg.report_trade_result(driver, 'not_taken')
                return

            driver.switch_to.window(tv_tab)
            trading.load_chart_for_signal(driver, params['asset'], params['contract_size'])

            if not trading.is_tradovate_connected(driver):
                print("  Tradovate not connected - attempting to connect...")
                if not trading.connect_tradovate(
                    driver, config.TRADOVATE_USERNAME, config.TRADOVATE_PASSWORD
                ):
                    print("  [FAIL] Could not connect to Tradovate - stopping loop.")
                    driver.switch_to.window(web_tab)
                    tg.report_trade_result(driver, 'not_taken')
                    return

            print(f"\n  Executing: {direction.upper()} | TP={params['tp_ticks']} ticks | "
                  f"SL={params['sl_ticks']} ticks | contracts={params['contracts']}")
            entered = trading.place_order(
                driver,
                tp_ticks=params['tp_ticks'],
                sl_ticks=params['sl_ticks'],
                side=direction,
                units=params['contracts'],
            )
            if not entered:
                print("\n[FAIL] Trade execution failed - stopping loop.")
                driver.switch_to.window(web_tab)
                tg.report_trade_result(driver, 'not_taken')
                return

            print("\n[OK] Trade executed! Waiting for it to close...")
            close_outcome = trading.wait_for_close(driver)
            if close_outcome is None:
                print("  Could not determine how the trade closed - stopping "
                      "loop without reporting a result. Report it manually on "
                      "TradingGenerator.")
                return

            driver.switch_to.window(web_tab)
            tg.report_trade_result(driver, close_outcome)
            humanize.long_pause(2, 3)

    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            driver.switch_to.window(tv_tab)
        except Exception:
            pass
