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
    connected_company = None
    next_company = None
    next_portfolio = None
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

            outcome = tg.generate_trade(
                driver, expected_company=next_company, expected_portfolio=next_portfolio
            )
            if outcome == 'locked':
                print("  Daily trade limit reached - stopping loop.")
                return
            if outcome == 'cooldown':
                print("  [FAIL] Generate button is still on cooldown from a previous "
                      "trade - stopping loop rather than reading stale parameters.")
                return
            if outcome == 'wrong_account':
                print("  [FAIL] Could not get onto the right company/portfolio before "
                      "generating - stopping loop.")
                return
            if outcome == 'not_found':
                print("  [FAIL] Could not generate a trade - stopping loop.")
                return

            humanize.long_pause(2, 3)
            params = tg.read_trade_parameters(driver)
            next_company = params['next_company']
            next_portfolio = params['next_portfolio']
            print(f"  Portfolio:   {params['portfolio']}")
            print(f"  Company:     {params['company']}")
            print(f"  Asset:       {params['asset']}")
            print(f"  Direction:   {params['direction']}")
            print(f"  Contracts:   {params['contracts']} {params['contract_size']}")
            print(f"  Stop Loss:   {params['sl_ticks']} ticks")
            print(f"  Take Profit: {params['tp_ticks']} ticks")

            direction = {'LONG': 'buy', 'SHORT': 'sell'}.get(params['direction'])
            missing = [k for k in ('asset', 'contracts', 'sl_ticks', 'tp_ticks', 'company') if params[k] is None]
            if direction is None:
                missing.append('direction')
            if missing:
                print(f"  [FAIL] Missing trade parameters ({', '.join(missing)}) - stopping loop.")
                tg.report_trade_result(driver, 'not_taken')
                return

            company = params['company']
            account = config.TRADOVATE_ACCOUNTS.get(company)
            if account is None:
                configured = ', '.join(config.TRADOVATE_ACCOUNTS) or 'none'
                print(f"  [FAIL] No Tradovate account configured for company '{company}' "
                      f"(configured: {configured}) - run 'setup' to add it. Stopping loop.")
                tg.report_trade_result(driver, 'not_taken')
                return

            driver.switch_to.window(tv_tab)
            trading.load_chart_for_signal(driver, params['asset'], params['contract_size'])

            if company != connected_company or not trading.is_tradovate_connected(driver):
                if trading.is_tradovate_connected(driver):
                    print(f"  Switching Tradovate account to '{company}'...")
                    trading.disconnect_tradovate(driver)
                else:
                    print(f"  Connecting Tradovate account for '{company}'...")
                if not trading.connect_tradovate(driver, account['username'], account['password']):
                    print("  [FAIL] Could not connect to Tradovate - stopping loop.")
                    connected_company = None
                    driver.switch_to.window(web_tab)
                    tg.report_trade_result(driver, 'not_taken')
                    return
                connected_company = company

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
