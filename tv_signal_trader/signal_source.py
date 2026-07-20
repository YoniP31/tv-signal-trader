import datetime
import time

from . import config
from . import humanize
from . import status
from . import trading
from . import tradinggenerator as tg

# Circuit breaker for failures that aren't tied to a specific company/
# portfolio (missing trade parameters, order execution) -- those retry by
# just moving on to the next signal, so this bounds how many times in a row
# that can happen before something is clearly systemically wrong.
MAX_CONSECUTIVE_FAILURES = 5

# How long to wait before re-checking when every known portfolio is
# locked/unavailable -- there's no way to know exactly when one might free
# up (a daily limit resetting, a human re-enabling something), so this just
# polls at a low, non-disruptive pace rather than giving up for the day.
PORTFOLIO_RETRY_INTERVAL_SECONDS = 60


def _generate_next_trade(driver, next_company, next_portfolio, unavailable):
    """Tries to generate a trade for the hinted next_company/next_portfolio
    (or, if there's no hint yet, whatever's currently selected). If that
    portfolio is locked, mismatched, or already known bad, falls back to
    trying every other configured company/portfolio pair in turn.

    `unavailable` is a set of (company, portfolio) pairs already found to be
    unusable this session (locked, persistent mismatch, etc.) -- mutated in
    place so the caller keeps skipping them on future calls too.

    Returns 'generated', 'not_found' (the generate button itself is missing
    -- a structural problem, not specific to any one portfolio), or
    'exhausted' (every known candidate is locked/unavailable).
    """
    hint = (next_company, next_portfolio) if next_company and next_portfolio else None
    if hint is None or hint not in unavailable:
        outcome = tg.generate_trade(
            driver, expected_company=next_company, expected_portfolio=next_portfolio
        )
        if outcome == 'generated':
            return 'generated'
        if outcome == 'not_found':
            return 'not_found'
        bad = hint or tg.read_active_company_portfolio(driver)
        if bad[0] and bad[1]:
            print(f"  '{bad[0]} / {bad[1]}' isn't available ({outcome}) - trying other portfolios...")
            unavailable.add(bad)
        else:
            print(f"  Current portfolio isn't available ({outcome}) - trying other portfolios...")

    for company, portfolio in tg.list_all_candidates(driver):
        if (company, portfolio) in unavailable:
            continue
        print(f"  Trying '{company} / {portfolio}'...")
        outcome = tg.generate_trade(
            driver, expected_company=company, expected_portfolio=portfolio, force=True
        )
        if outcome == 'generated':
            return 'generated'
        if outcome == 'not_found':
            return 'not_found'
        unavailable.add((company, portfolio))

    return 'exhausted'


def run_web_loop(driver):
    """Repeatedly: generates a TradingGenerator signal, executes it on
    TradingView, waits for it to close, and reports the result back.
    Rotates through other companies/portfolios when the current one is
    locked or otherwise unavailable; if *every* one currently is, it doesn't
    give up -- it polls every PORTFOLIO_RETRY_INTERVAL_SECONDS in case one
    frees up (a daily limit resetting, a portfolio being re-added). Only
    stops for real when TradingGenerator login fails or a trade's outcome
    genuinely can't be determined. Stoppable with Ctrl+C at any point.

    Bounded by config.SESSION_START_TIME/SESSION_END_TIME (Israel time), if
    set: pauses generating anything outside the window and resumes on its
    own once back inside it (today's window if we're early, tomorrow's if
    today's already closed) -- checked only between trades, so a trade
    already open when the window closes still runs through wait_for_close
    normally rather than being cut off mid-position.
    """
    print("\n[WEB] Starting automatic trading loop (Ctrl+C to stop)...")
    tv_tab = driver.current_window_handle
    connected_company = None
    next_company = None
    next_portfolio = None
    unavailable = set()
    consecutive_failures = 0
    try:
        web_tab = tg.open_tab(driver, tv_tab)
        print("  TradingGenerator tab ready [OK]")

        while True:
            session_status, session_wait = config.session_window_status()
            if session_status == 'waiting':
                resume_at = config.now_in_israel() + datetime.timedelta(seconds=session_wait)
                print(f"  Outside the trading session - waiting until "
                      f"{resume_at.strftime('%Y-%m-%d %H:%M')} Israel time ({int(session_wait)}s)...")
                time.sleep(session_wait)
                continue

            driver.switch_to.window(web_tab)

            tg_logged_in = tg.ensure_logged_in(driver)
            status.update(tradinggenerator_logged_in=tg_logged_in)
            if not tg_logged_in:
                print("  [FAIL] TradingGenerator login failed - stopping loop.")
                return

            gen_outcome = _generate_next_trade(driver, next_company, next_portfolio, unavailable)
            if gen_outcome == 'not_found':
                print("  [FAIL] Could not generate a trade - stopping loop.")
                return
            if gen_outcome != 'generated':
                print(f"  No tradeable portfolio available right now (all locked/unavailable) - "
                      f"checking again in {PORTFOLIO_RETRY_INTERVAL_SECONDS}s...")
                # Nothing's usable *right now*, but that can change (a daily
                # limit resets, someone re-adds a portfolio) -- forget what
                # we've ruled out and give everything a fresh look next time
                # instead of blacklisting it for the rest of the run.
                unavailable.clear()
                time.sleep(PORTFOLIO_RETRY_INTERVAL_SECONDS)
                continue

            humanize.long_pause(2, 3)
            missing = []
            for attempt in range(3):
                params = tg.read_trade_parameters(driver)
                direction = {'LONG': 'buy', 'SHORT': 'sell'}.get(params['direction'])
                missing = [k for k in ('asset', 'contracts', 'sl_ticks', 'tp_ticks', 'company', 'portfolio') if params[k] is None]
                if direction is None:
                    missing.append('direction')
                if not missing:
                    break
                if attempt < 2:
                    humanize.pause(1, 2)

            print(f"  Portfolio:   {params['portfolio']}")
            print(f"  Company:     {params['company']}")
            print(f"  Asset:       {params['asset']}")
            print(f"  Direction:   {params['direction']}")
            print(f"  Contracts:   {params['contracts']} {params['contract_size']}")
            print(f"  Stop Loss:   {params['sl_ticks']} ticks")
            print(f"  Take Profit: {params['tp_ticks']} ticks")

            next_company = params['next_company']
            next_portfolio = params['next_portfolio']

            if missing:
                print(f"  [FAIL] Missing trade parameters ({', '.join(missing)}) after retries - "
                      "reporting Trade Not Taken and moving on.")
                tg.report_trade_result(driver, 'not_taken')
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"  [FAIL] {consecutive_failures} consecutive failures - stopping loop.")
                    return
                humanize.long_pause(2, 3)
                continue

            company = params['company']
            account = config.TRADOVATE_ACCOUNTS.get(company)
            if account is None:
                configured = ', '.join(config.TRADOVATE_ACCOUNTS) or 'none'
                print(f"  [FAIL] No Tradovate account configured for company '{company}' "
                      f"(configured: {configured}) - run 'setup' to add it. Trying other portfolios...")
                tg.report_trade_result(driver, 'not_taken')
                unavailable.add((company, params['portfolio']))
                humanize.long_pause(2, 3)
                continue

            driver.switch_to.window(tv_tab)
            trading.load_chart_for_signal(driver, params['asset'], params['contract_size'])

            if company != connected_company or not trading.is_tradovate_connected(driver):
                if trading.is_tradovate_connected(driver):
                    print(f"  Switching Tradovate account to '{company}'...")
                    trading.disconnect_tradovate(driver)
                else:
                    print(f"  Connecting Tradovate account for '{company}'...")
                if not trading.connect_tradovate(driver, account['username'], account['password']):
                    print(f"  [FAIL] Could not connect to Tradovate for '{company}' - "
                          "trying other portfolios...")
                    connected_company = None
                    driver.switch_to.window(web_tab)
                    tg.report_trade_result(driver, 'not_taken')
                    unavailable.add((company, params['portfolio']))
                    humanize.long_pause(2, 3)
                    continue
                connected_company = company

            if not trading.select_tradovate_account(driver, params['portfolio']):
                print(f"  [FAIL] Could not select Tradovate account '{params['portfolio']}' - "
                      "trying other portfolios...")
                driver.switch_to.window(web_tab)
                tg.report_trade_result(driver, 'not_taken')
                unavailable.add((company, params['portfolio']))
                humanize.long_pause(2, 3)
                continue

            # Belt-and-suspenders: the same balance check runs after every
            # trade closes and should already have removed a violating
            # account, but check again here too in case that removal ever
            # failed -- opening a new trade on an already-blown/maxed-out
            # account is worse than a redundant check.
            if trading.account_needs_removal(driver):
                print(f"  [WARN] Account balance already outside its allowed range - "
                      f"removing '{params['portfolio']}' instead of trading.")
                driver.switch_to.window(web_tab)
                tg.report_trade_result(driver, 'not_taken')
                tg.remove_portfolio(driver, params['portfolio'])
                humanize.long_pause(2, 3)
                continue

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
                print("\n[FAIL] Trade execution failed - reporting Trade Not Taken and moving on.")
                driver.switch_to.window(web_tab)
                tg.report_trade_result(driver, 'not_taken')
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"  [FAIL] {consecutive_failures} consecutive failures - stopping loop.")
                    return
                humanize.long_pause(2, 3)
                continue

            consecutive_failures = 0

            print("\n[OK] Trade executed! Waiting for it to close...")
            close_outcome = trading.wait_for_close(driver)
            if close_outcome is None:
                print("  Could not determine how the trade closed - stopping "
                      "loop without reporting a result. Report it manually on "
                      "TradingGenerator.")
                return

            # Right after close is the one moment we're certain which account
            # is active and that no position is open, so it's the trustworthy
            # time to check whether this account has blown past its loss
            # limit or hit its profit target and needs pulling out of rotation.
            needs_removal = trading.account_needs_removal(driver)

            driver.switch_to.window(web_tab)
            tg.report_trade_result(driver, close_outcome)

            if needs_removal:
                tg.remove_portfolio(driver, params['portfolio'])

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
