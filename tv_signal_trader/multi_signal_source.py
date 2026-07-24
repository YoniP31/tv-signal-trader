"""Multi-position trading loop -- a separate, still-evolving path alongside
signal_source.run_web_loop()/the 'web' command, which stays untouched and
single-position. See the approved plan (multi-position support) for the
full design; built and checked step by step rather than all at once.

Concurrency rules (confirmed with the user):
  - One open position per portfolio.
  - At most MAX_POSITIONS_PER_COMPANY open at a single company at once.
  - Only one company may have any open positions at any given time.
  - Eligibility for the next signal is decided *before* clicking Generate,
    using the previous trade's "next portfolio to trade" hint -- not
    "generate first, then discover we have to wait".

Reuses signal_source.generate_next_trade/sweep_liquidated_accounts/
report_not_taken (promoted to public, unchanged behavior) rather than
duplicating that rotation/reporting logic.
"""

import datetime
import time

from . import config
from . import humanize
from . import signal_source
from . import status
from . import trading
from . import tradinggenerator as tg

# One open position per portfolio; at most this many concurrently open at a
# single company; only one company may have any open positions at a time.
MAX_POSITIONS_PER_COMPANY = 3

# How long to wait before re-checking eligibility when we're just waiting on
# an open position (not everything locked/unavailable -- that's
# signal_source.PORTFOLIO_RETRY_INTERVAL_SECONDS). Matches the single-position
# flow's TRADE_CLOSE_POLL_INTERVAL_SECONDS cadence.
POSITION_WAIT_INTERVAL_SECONDS = config.TRADE_CLOSE_POLL_INTERVAL_SECONDS


def check_eligibility(next_company, next_portfolio, engaged_company, open_positions):
    """Whether it's OK to generate+open a trade for next_company/next_portfolio
    right now, given which company is currently "engaged" (holds any open
    positions) and what's already open.

    `open_positions` is a dict keyed (company, portfolio) -- only its keys
    matter here, values (bracket order IDs, etc.) are irrelevant to this
    check.

    Returns:
      - 'eligible': fine to generate and open now.
      - 'wait_different_company': a *different* company currently holds open
        positions -- must wait for all of those to close before switching.
      - 'wait_this_portfolio': next_portfolio itself already has an open
        position (not flat) -- must wait for it to close before reopening.
      - 'wait_company_cap': next_company is already at
        MAX_POSITIONS_PER_COMPANY open positions -- must wait for at least
        one to close before opening another.
    """
    if engaged_company is not None and engaged_company != next_company:
        return 'wait_different_company'
    if (next_company, next_portfolio) in open_positions:
        return 'wait_this_portfolio'
    company_open_count = sum(1 for (c, _p) in open_positions if c == next_company)
    if company_open_count >= MAX_POSITIONS_PER_COMPANY:
        return 'wait_company_cap'
    return 'eligible'


def _ensure_tradovate_connection(driver, company, connected_company):
    """Connects/switches to `company`'s Tradovate login if it isn't already
    the active one. Returns the (possibly updated) connected_company, or
    None if the connection failed."""
    account = config.TRADOVATE_ACCOUNTS.get(company)
    if account is None:
        return None
    if company == connected_company and trading.is_tradovate_connected(driver):
        return connected_company
    if trading.is_tradovate_connected(driver):
        trading.disconnect_tradovate(driver)
    if not trading.connect_tradovate(driver, account['username'], account['password']):
        return None
    return company


def _refresh_open_positions(driver, web_tab, tv_tab, open_positions, connected_company, quarantined):
    """Checks every tracked open position's bracket status once, reporting
    (or quarantining, for a manual close/liquidation) and removing any that
    have resolved.

    A manual close/liquidation adds (company, portfolio) to `quarantined`
    (mutated in place) -- that account shouldn't be traded again until the
    next session day, since something closed its position outside of our
    own TP/SL and we don't know why. The caller is responsible for actually
    keeping quarantined portfolios out of rotation (see run_web_loop_multi)
    and for clearing `quarantined` at the start of a new session day.

    Every portfolio -- including each one from a multi-portfolio signal, see
    read_eval_portfolios -- has its own independent "Trade Result" field in
    TradingGenerator, so each resolved position here is selected and
    reported on its own, same as the single-position flow.

    Returns the (possibly updated) connected_company -- cycling through
    sub-accounts here may have switched which Tradovate login is active.
    """
    for (company, portfolio), position in list(open_positions.items()):
        driver.switch_to.window(tv_tab)
        connected_company = _ensure_tradovate_connection(driver, company, connected_company)
        if connected_company is None:
            print(f"  [WARN] Could not connect to Tradovate for '{company}' while "
                  f"checking on '{portfolio}' - will retry next cycle.")
            continue

        if not trading.select_tradovate_account(driver, portfolio):
            print(f"  [WARN] Could not select Tradovate account '{portfolio}' while "
                  "checking open positions - will retry next cycle.")
            continue

        if not trading.click_orders_tab(driver):
            print(f"  [WARN] Could not open the Orders tab for '{portfolio}' - will retry next cycle.")
            continue

        outcome = trading.check_bracket_status(driver, position['tp_id'], position['sl_id'])
        if outcome == 'open':
            continue

        needs_removal, balance, tier_size, tier_range = trading.account_needs_removal(
            driver, position['account_type']
        )
        status.update_portfolio(company, portfolio, balance=balance, tier_size=tier_size, tier_range=tier_range)

        driver.switch_to.window(web_tab)
        tg.select_company(driver, company)
        tg.select_portfolio(driver, portfolio)

        if outcome == 'manual_close':
            print(f"  [WARN] '{company} / {portfolio}' appears to have been closed manually or "
                  "liquidated (both TP/SL cancelled, neither filled) - no correct result to report. "
                  "Quarantining until the next session.")
            status.mark_portfolio_unavailable(company, portfolio, 'manual_close_or_liquidation')
            quarantined.add((company, portfolio))
        else:
            tg.report_trade_result(driver, outcome)
            status.record_trade_result(
                company, portfolio,
                asset=position['asset'], direction=position['direction'], contracts=position['contracts'],
                sl_ticks=position['sl_ticks'], tp_ticks=position['tp_ticks'], result=outcome,
            )
            if needs_removal:
                tg.remove_portfolio(driver, portfolio)
                status.mark_portfolio_removed(
                    company, portfolio, 'balance outside allowed range (blown/hit target)'
                )

        del open_positions[(company, portfolio)]
        print(f"  '{company} / {portfolio}' closed ({outcome}) - slot freed.")

    return connected_company


def _open_position(driver, web_tab, tv_tab, params, connected_company):
    """Connects/selects the right Tradovate account, runs the pre-trade
    balance check + TP adjustment, and places the order for `params`
    (already known to be eligible -- see check_eligibility).

    Deliberately does *not* report Not Taken to TradingGenerator on failure
    -- each portfolio has its own independent "Trade Result" field (even
    within a multi-portfolio signal, see read_eval_portfolios), so the
    caller reports Not Taken itself, on the specific portfolio that failed,
    rather than this function guessing at which one to select. The one
    exception is a portfolio whose balance is already outside its allowed
    range -- that removal is independent of this signal and happens
    regardless.

    Returns (success, connected_company, ledger_entry). ledger_entry is
    None on failure; otherwise the dict to store in open_positions.
    """
    company = params['company']
    portfolio = params['portfolio']

    driver.switch_to.window(tv_tab)
    trading.load_chart_for_signal(driver, params['asset'], params['contract_size'])

    connected_company = _ensure_tradovate_connection(driver, company, connected_company)
    if connected_company is None:
        print(f"  [FAIL] Could not connect to Tradovate for '{company}'.")
        return False, None, None

    if not trading.select_tradovate_account(driver, portfolio):
        print(f"  [FAIL] Could not select Tradovate account '{portfolio}'.")
        return False, connected_company, None

    needs_removal, balance, tier_size, tier_range = trading.account_needs_removal(driver, params['account_type'])
    status.update_portfolio(company, portfolio, balance=balance, tier_size=tier_size, tier_range=tier_range)
    if needs_removal:
        print(f"  [WARN] Account balance already outside its allowed range - "
              f"removing '{portfolio}' instead of trading.")
        driver.switch_to.window(web_tab)
        tg.select_company(driver, company)
        tg.select_portfolio(driver, portfolio)
        tg.remove_portfolio(driver, portfolio)
        status.mark_portfolio_removed(company, portfolio, 'balance outside allowed range (blown/hit target)')
        return False, connected_company, None

    status.mark_portfolio_available(company, portfolio)

    tp_ticks = trading.adjust_tp_for_max_balance(
        balance, params['tp_ticks'], params['tp_dollars'], tier_range['max']
    )

    direction = {'LONG': 'buy', 'SHORT': 'sell'}.get(params['direction'])
    print(f"\n  Executing: {direction.upper()} | TP={tp_ticks} ticks | "
          f"SL={params['sl_ticks']} ticks | contracts={params['contracts']} | '{company} / {portfolio}'")
    entered = trading.place_order(
        driver, tp_ticks=tp_ticks, sl_ticks=params['sl_ticks'], side=direction, units=params['contracts'],
    )
    if not entered:
        print("\n[FAIL] Trade execution failed.")
        return False, connected_company, None

    bracket = trading.find_working_bracket(driver)
    tp_id, sl_id = bracket.get('tp'), bracket.get('sl')
    if not tp_id or not sl_id:
        print(f"  [FAIL] Could not confirm bracket IDs after opening '{portfolio}' - "
              "can't track this position reliably.")
        return False, connected_company, None

    entry = {
        'tp_id': tp_id,
        'sl_id': sl_id,
        'account_type': params['account_type'],
        'asset': params['asset'],
        'direction': params['direction'],
        'contracts': params['contracts'],
        'sl_ticks': params['sl_ticks'],
        'tp_ticks': tp_ticks,
    }
    print(f"\n[OK] '{company} / {portfolio}' opened and tracked.")
    return True, connected_company, entry


def run_web_loop_multi(driver):
    """Like signal_source.run_web_loop(), but supports several concurrent
    open positions under the rules described at the top of this module.

    Every iteration: refresh every tracked open position's status first
    (reporting/freeing any that resolved), then check whether the next
    signal (per the previous trade's hint) is eligible to open right now.
    If not, wait a bit and refresh again -- positions accumulate in
    `open_positions` and get drained opportunistically rather than the
    single-position flow's one-at-a-time wait_for_close.

    Session window, no-trade window, daily liquidated-account sweep, and
    daily backup all work the same way as run_web_loop() (see there for
    details) -- this reuses the same config/status plumbing.

    A portfolio whose position closes via manual close/liquidation (see
    _refresh_open_positions) is quarantined until the next session day:
    added to `quarantined`, which gets folded into `unavailable` right
    before every generate attempt so signal_source.generate_next_trade's
    own rotation keeps steering clear of it regardless of `unavailable`'s
    own clear/reset cycles, and gets reset alongside the daily sweep.
    """
    print("\n[WEB-MULTI] Starting automatic multi-position trading loop (Ctrl+C to stop)...")
    tv_tab = driver.current_window_handle
    connected_company = None
    next_company = None
    next_portfolio = None
    unavailable = set()
    quarantined = set()
    open_positions = {}
    engaged_company = None
    consecutive_failures = 0
    last_backup_date = None
    last_sweep_date = None
    status.update(loop_state='trading', loop_started_at=status.timestamp(), stop_reason=None)
    try:
        web_tab = tg.open_tab(driver, tv_tab)
        print("  TradingGenerator tab ready [OK]")

        while True:
            session_status, session_wait = config.session_window_status()
            status.update(
                session_window={
                    'start': config.SESSION_START_TIME.strftime('%H:%M') if config.SESSION_START_TIME else None,
                    'end': config.SESSION_END_TIME.strftime('%H:%M') if config.SESSION_END_TIME else None,
                    'status': session_status,
                }, no_trade_window={
                    'start': config.NO_TRADE_START_TIME.strftime('%H:%M') if config.NO_TRADE_START_TIME else None,
                    'end': config.NO_TRADE_END_TIME.strftime('%H:%M') if config.NO_TRADE_END_TIME else None,
                    'active': config.in_no_trade_window(),
                }
            )
            today = config.now_in_israel().date()
            if last_sweep_date != today:
                connected_company = signal_source.sweep_liquidated_accounts(
                    driver, web_tab, tv_tab, connected_company
                )
                status.update(last_sweep_at=status.timestamp())
                last_sweep_date = today
                if quarantined:
                    print(f"  New session day - releasing {len(quarantined)} quarantined portfolio(s).")
                    # Also drop them from `unavailable` itself -- otherwise a
                    # released portfolio could stay excluded until the
                    # unrelated exhaustion-retry cycle happens to clear it,
                    # which might not be soon if other portfolios keep
                    # trading fine in the meantime.
                    unavailable.difference_update(quarantined)
                    quarantined.clear()

            # Always refresh open positions, regardless of the session/
            # no-trade window below -- an already-open position doesn't stop
            # just because we've stopped generating new ones; it needs to
            # keep being checked (and reported/freed once it closes)
            # wherever it is.
            connected_company = _refresh_open_positions(
                driver, web_tab, tv_tab, open_positions, connected_company, quarantined
            )
            if engaged_company and not any(c == engaged_company for c, _p in open_positions):
                engaged_company = None
            status.update(
                open_positions=[f"{c} / {p}" for c, p in open_positions],
                quarantined=[f"{c} / {p}" for c, p in quarantined],
            )

            if session_status == 'waiting':
                status.update(loop_state='waiting_for_session')
                now = config.now_in_israel()
                if open_positions:
                    print(f"  Outside the trading session with {len(open_positions)} position(s) still "
                          f"open - waiting for them to close (checking again in "
                          f"{POSITION_WAIT_INTERVAL_SECONDS}s)...")
                    time.sleep(POSITION_WAIT_INTERVAL_SECONDS)
                    continue

                if (config.SESSION_END_TIME and now.time() > config.SESSION_END_TIME
                        and last_backup_date != now.date()):
                    print("  Trading session ended for today - saving a TradingGenerator backup...")
                    driver.switch_to.window(web_tab)
                    tg.save_backup(driver)
                    status.update(last_backup_at=status.timestamp())
                    last_backup_date = now.date()

                resume_at = now + datetime.timedelta(seconds=session_wait)
                print(f"  Outside the trading session - waiting until "
                      f"{resume_at.strftime('%Y-%m-%d %H:%M')} Israel time ({int(session_wait)}s)...")
                time.sleep(session_wait)
                continue

            if config.in_no_trade_window():
                status.update(loop_state='no_trade_window')
                if open_positions:
                    print(f"  Inside the no-trade window with {len(open_positions)} position(s) still "
                          f"open - waiting for them to close (checking again in "
                          f"{POSITION_WAIT_INTERVAL_SECONDS}s)...")
                    time.sleep(POSITION_WAIT_INTERVAL_SECONDS)
                    continue
                print(f"  Inside the no-trade window ({config.NO_TRADE_START_TIME.strftime('%H:%M')}-"
                      f"{config.NO_TRADE_END_TIME.strftime('%H:%M')} Israel time) - not generating new "
                      f"trades; checking again in {signal_source.PORTFOLIO_RETRY_INTERVAL_SECONDS}s...")
                time.sleep(signal_source.PORTFOLIO_RETRY_INTERVAL_SECONDS)
                continue

            eligibility = check_eligibility(next_company, next_portfolio, engaged_company, open_positions)
            if eligibility != 'eligible':
                status.update(loop_state='waiting_for_position_slot')
                print(f"  Not eligible to open the next signal yet ({eligibility}) - "
                      f"checking again in {POSITION_WAIT_INTERVAL_SECONDS}s...")
                time.sleep(POSITION_WAIT_INTERVAL_SECONDS)
                continue

            status.update(loop_state='trading')
            driver.switch_to.window(web_tab)

            tg_logged_in = tg.ensure_logged_in(driver)
            status.update(tradinggenerator_logged_in=tg_logged_in)
            if not tg_logged_in:
                print("  [FAIL] TradingGenerator login failed - stopping loop.")
                status.update(loop_state='stopped', stop_reason='tradinggenerator_login_failed')
                return

            # Fold in quarantined portfolios every time (not just after a
            # clear) so generate_next_trade's own rotation steers clear of
            # them regardless of `unavailable`'s own clear/reset cycles.
            unavailable.update(quarantined)
            gen_outcome = signal_source.generate_next_trade(driver, next_company, next_portfolio, unavailable)
            if gen_outcome == 'not_found':
                print("  [FAIL] Could not generate a trade - stopping loop.")
                status.update(loop_state='stopped', stop_reason='generate_button_not_found')
                return
            if gen_outcome != 'generated':
                print(f"  No tradeable portfolio available right now (all locked/unavailable) - "
                      f"checking again in {signal_source.PORTFOLIO_RETRY_INTERVAL_SECONDS}s...")
                unavailable.clear()
                status.update(loop_state='waiting_for_portfolio')
                time.sleep(signal_source.PORTFOLIO_RETRY_INTERVAL_SECONDS)
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
                signal_source.report_not_taken(driver, params)
                consecutive_failures += 1
                if consecutive_failures >= signal_source.MAX_CONSECUTIVE_FAILURES:
                    print(f"  [FAIL] {consecutive_failures} consecutive failures - stopping loop.")
                    status.update(loop_state='stopped', stop_reason='too_many_consecutive_failures_missing_params')
                    return
                humanize.long_pause(2, 3)
                continue

            company = params['company']
            # Usually just [params['portfolio']], but TradingGenerator's
            # "Taken in the following portfolios" box (read_eval_portfolios)
            # means one signal can need opening on several sibling
            # portfolios at once -- each with its own independent "Trade
            # Result" field, so each gets opened (or reported Not Taken)
            # separately below.
            targets = params['eval_portfolios'] or [params['portfolio']]

            def _report_not_taken_for(portfolio):
                driver.switch_to.window(web_tab)
                tg.select_company(driver, company)
                tg.select_portfolio(driver, portfolio)
                tg.report_trade_result(driver, 'not_taken')
                status.record_trade_result(
                    company, portfolio,
                    asset=params['asset'], direction=params['direction'], contracts=params['contracts'],
                    sl_ticks=params['sl_ticks'], tp_ticks=params['tp_ticks'], result='not_taken',
                )

            if config.TRADOVATE_ACCOUNTS.get(company) is None:
                configured = ', '.join(config.TRADOVATE_ACCOUNTS) or 'none'
                print(f"  [FAIL] No Tradovate account configured for company '{company}' "
                      f"(configured: {configured}) - run 'setup' to add it.")
                for portfolio in targets:
                    unavailable.add((company, portfolio))
                    status.mark_portfolio_unavailable(company, portfolio, 'no Tradovate account configured')
                    _report_not_taken_for(portfolio)
                humanize.long_pause(2, 3)
                continue

            opened_any = False
            for portfolio in targets:
                portfolio_eligibility = check_eligibility(company, portfolio, engaged_company, open_positions)
                if portfolio_eligibility == 'eligible':
                    portfolio_params = dict(params, portfolio=portfolio)
                    success, connected_company, entry = _open_position(
                        driver, web_tab, tv_tab, portfolio_params, connected_company
                    )
                    if success:
                        open_positions[(company, portfolio)] = entry
                        engaged_company = company
                        opened_any = True
                        continue
                    print(f"  [FAIL] Could not open '{company} / {portfolio}'.")
                else:
                    print(f"  [WARN] '{company} / {portfolio}' isn't eligible right now "
                          f"({portfolio_eligibility}) - can't open it for this signal.")

                # Not opened (either ineligible right now, or the open attempt
                # failed) -- there's no later retry for this specific signal
                # instance, so report Not Taken on this portfolio's own field.
                print(f"  Reporting Trade Not Taken for '{company} / {portfolio}'.")
                _report_not_taken_for(portfolio)

            if opened_any:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= signal_source.MAX_CONSECUTIVE_FAILURES:
                    print(f"  [FAIL] {consecutive_failures} consecutive failures - stopping loop.")
                    status.update(loop_state='stopped', stop_reason='too_many_consecutive_failures_order_execution')
                    return

            humanize.long_pause(2, 3)

    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        status.update(loop_state='stopped', stop_reason='exception',
                       last_error=str(e), last_error_at=status.timestamp())
        import traceback
        traceback.print_exc()
    finally:
        try:
            driver.switch_to.window(tv_tab)
        except Exception:
            pass
