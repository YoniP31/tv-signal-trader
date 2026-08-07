"""The trading loop engine behind both the 'web' and 'web_multi' commands
(see cli.py) -- they aren't separate implementations, just this same loop
called with a different max_positions_per_company (1 for 'web', whatever's
configured in .env for 'web_multi').

Concurrency rules (confirmed with the user):
  - One open position per portfolio.
  - At most config.MAX_POSITIONS_PER_COMPANY open at a single company at once.
  - Only one company may have any open positions at any given time.
  - No hedging: a new trade at a company already holding an open position in
    the opposite direction waits for that position to close first.
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
from .logging_utils import timestamped_print as print


def check_eligibility(next_company, next_portfolio, next_direction, engaged_company, open_positions,
                       max_positions_per_company=None):
    """Whether it's OK to generate+open a trade for next_company/next_portfolio
    right now, given which company is currently "engaged" (holds any open
    positions) and what's already open.

    `open_positions` is a dict keyed (company, portfolio) -- its values
    (each a ledger entry, see _open_position) matter here too now, for the
    hedge-conflict check below.

    `next_direction` ('LONG'/'SHORT') is optional -- pass None when it isn't
    known yet (the pre-generate hint call, before TradingGenerator's next
    signal has actually been read), which simply skips the hedge-conflict
    check for that call; the real check happens on the later post-generate
    call, once the candidate trade's direction is known.

    `max_positions_per_company` overrides config.MAX_POSITIONS_PER_COMPANY
    for this call when given (e.g. the 'web' command forces 1, reusing this
    same engine instead of its own separate single-position implementation)
    -- defaults to the configured value when None.

    Returns:
      - 'eligible': fine to generate and open now.
      - 'wait_different_company': a *different* company currently holds open
        positions -- must wait for all of those to close before switching.
      - 'wait_this_portfolio': next_portfolio itself already has an open
        position (not flat) -- must wait for it to close before reopening.
      - 'wait_hedge_conflict': next_direction conflicts with the direction of
        an already-open position at the same company (a hedge) -- must wait
        for the open position(s) to close before opening the opposite side.
        Since only one company may ever be engaged at once (see
        wait_different_company above), every currently-open position already
        belongs to next_company whenever there is one, so this only needs to
        compare directions, not re-check company.
      - 'wait_company_cap': next_company is already at the max positions
        allowed for this call -- must wait for at least one to close before
        opening another.
    """
    if engaged_company is not None and engaged_company != next_company:
        return 'wait_different_company'
    if (next_company, next_portfolio) in open_positions:
        return 'wait_this_portfolio'
    if next_direction is not None:
        for (c, _p), position in open_positions.items():
            if c == next_company and position['direction'] != next_direction:
                return 'wait_hedge_conflict'
    max_positions = (
        max_positions_per_company if max_positions_per_company is not None else config.MAX_POSITIONS_PER_COMPANY
    )
    company_open_count = sum(1 for (c, _p) in open_positions if c == next_company)
    if company_open_count >= max_positions:
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


def _select_and_verify(driver, company, portfolio, attempts=3):
    """Selects `company`/`portfolio` in TradingGenerator and confirms the
    page actually landed on that exact pair before a caller reports a
    result or removes a portfolio there.

    TradingGenerator's Trade Result prompt (#tradeResultSection) is a
    single, page-global element reflecting whatever's currently active --
    select_company/select_portfolio's own return values only say "a
    matching tab was found and clicked", not "this is now confirmed
    active". Acting on a failed or merely-assumed selection risks
    reporting/removing the wrong portfolio entirely (e.g. right after this
    exact portfolio was just deleted for hitting its balance limit, or a
    transient DOM lag). Retries a few times before giving up, since a
    mismatch is often transient rather than permanent.

    Returns True only once tradinggenerator.read_active_company_portfolio
    confirms (company, portfolio) is actually selected.
    """
    for attempt in range(attempts):
        if tg.select_company(driver, company) and tg.select_portfolio(driver, portfolio):
            active_company, active_portfolio = tg.read_active_company_portfolio(driver)
            if active_company == company and active_portfolio == portfolio:
                return True
        if attempt < attempts - 1:
            humanize.pause(0.5, 1.0)
    print(f"  [WARN] Could not confirm '{company} / {portfolio}' is actually selected in "
          "TradingGenerator after retrying - skipping rather than risk acting on the wrong portfolio.")
    return False


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

        # A manual close is always reported as Not Taken and quarantined
        # regardless of daily P&L (see below), so there's no point checking
        # it here.
        daily_limit_reason = None
        if outcome != 'manual_close' and (config.DAILY_PROFIT_LIMIT is not None
                                           or config.DAILY_LOSS_LIMIT is not None):
            if trading.click_account_summary_tab(driver):
                total_pl = trading.read_total_pl(driver)
                if total_pl is not None:
                    if config.DAILY_PROFIT_LIMIT is not None and total_pl >= config.DAILY_PROFIT_LIMIT:
                        daily_limit_reason = 'daily_profit_limit_reached'
                    elif config.DAILY_LOSS_LIMIT is not None and total_pl <= -config.DAILY_LOSS_LIMIT:
                        daily_limit_reason = 'daily_loss_limit_reached'

        driver.switch_to.window(web_tab)
        if not _select_and_verify(driver, company, portfolio):
            print(f"  [WARN] Could not safely select '{company} / {portfolio}' to report its "
                  "result - will retry next cycle.")
            continue

        if outcome == 'manual_close':
            print(f"  [WARN] '{company} / {portfolio}' appears to have been closed manually or "
                  "liquidated (both TP/SL cancelled, neither filled) - reporting Trade Not Taken "
                  "and quarantining until the next session.")
            tg.report_trade_result(driver, 'not_taken', company=company, portfolio=portfolio)
            status.record_trade_result(
                company, portfolio,
                asset=position['asset'], direction=position['direction'], contracts=position['contracts'],
                sl_ticks=position['sl_ticks'], tp_ticks=position['tp_ticks'], result='not_taken',
            )
            status.mark_portfolio_unavailable(company, portfolio, 'manual_close_or_liquidation')
            quarantined.add((company, portfolio))
        else:
            tg.report_trade_result(driver, outcome, company=company, portfolio=portfolio)
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
            elif daily_limit_reason:
                limit_kind = 'profit' if daily_limit_reason == 'daily_profit_limit_reached' else 'loss'
                print(f"  [WARN] '{company} / {portfolio}' hit its daily {limit_kind} limit - "
                      "quarantining until the next session.")
                status.mark_portfolio_unavailable(company, portfolio, daily_limit_reason)
                quarantined.add((company, portfolio))

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

    Returns (outcome, connected_company, ledger_entry):
      - outcome: 'opened' (ledger_entry is the dict to store in
        open_positions), 'rejected' (the broker rejected the entry order
        itself -- see trading.check_entry_rejected -- so this account
        should be quarantined until the next session), 'daily_profit_limit_reached'
        / 'daily_loss_limit_reached' (the account already hit
        config.DAILY_PROFIT_LIMIT/DAILY_LOSS_LIMIT -- also quarantine until
        the next session, no trade attempted), or 'failed' (some other
        failure, no quarantine implied). ledger_entry is None unless
        outcome is 'opened'.
    """
    company = params['company']
    portfolio = params['portfolio']

    driver.switch_to.window(tv_tab)
    trading.load_chart_for_signal(driver, params['asset'], params['contract_size'])

    connected_company = _ensure_tradovate_connection(driver, company, connected_company)
    if connected_company is None:
        print(f"  [FAIL] Could not connect to Tradovate for '{company}'.")
        return 'failed', None, None

    if not trading.select_tradovate_account(driver, portfolio):
        print(f"  [FAIL] Could not select Tradovate account '{portfolio}'.")
        return 'failed', connected_company, None

    needs_removal, balance, tier_size, tier_range = trading.account_needs_removal(driver, params['account_type'])
    status.update_portfolio(company, portfolio, balance=balance, tier_size=tier_size, tier_range=tier_range)
    if needs_removal:
        print(f"  [WARN] Account balance already outside its allowed range - "
              f"removing '{portfolio}' instead of trading.")
        driver.switch_to.window(web_tab)
        if _select_and_verify(driver, company, portfolio):
            tg.remove_portfolio(driver, portfolio)
            status.mark_portfolio_removed(company, portfolio, 'balance outside allowed range (blown/hit target)')
        else:
            print(f"  [WARN] Could not safely select '{company} / {portfolio}' to remove it - "
                  "will retry the next time it's attempted.")
        return 'failed', connected_company, None

    status.mark_portfolio_available(company, portfolio)

    total_pl = None
    daily_limit_configured = config.DAILY_PROFIT_LIMIT is not None or config.DAILY_LOSS_LIMIT is not None
    if daily_limit_configured:
        if trading.click_account_summary_tab(driver):
            total_pl = trading.read_total_pl(driver)
        if total_pl is None:
            # A daily limit IS configured, but couldn't be checked -- must
            # not fall through and trade as if none were configured at all
            # (adjust_ticks_for_daily_pnl below is itself a silent no-op for
            # total_pl=None, precisely because it trusts the caller to have
            # already handled "couldn't read" as a reason not to proceed).
            print(f"  [FAIL] '{company} / {portfolio}' - could not read Total P/L to check the daily "
                  "profit/loss limit before trading. Not risking an unchecked trade.")
            return 'failed', connected_company, None
    if total_pl is not None:
        # Bulletproofing beyond "report correctly and TradingGenerator
        # won't offer a new trade": gated on the buffer's *max* rather than
        # the raw limit, so a trade is refused outright once there's too
        # little room left to safely size (and possibly buffer) one,
        # rather than letting adjust_ticks_for_daily_pnl try to cram a
        # sliver-thin TP/SL into whatever's left (down to its 1-tick floor,
        # which could itself land past the limit if the true remaining
        # room is worth less than one tick).
        pnl_buffer_max = config.DAILY_PNL_CAP_BUFFER_RANGE[1]
        if config.DAILY_PROFIT_LIMIT is not None and total_pl >= config.DAILY_PROFIT_LIMIT - pnl_buffer_max:
            print(f"  [WARN] '{company} / {portfolio}' is within ${pnl_buffer_max:.2f} of its daily "
                  f"profit limit ({total_pl:.2f} >= {config.DAILY_PROFIT_LIMIT - pnl_buffer_max:.2f}) - "
                  "too little room to safely size a trade. Not trading until the next session.")
            return 'daily_profit_limit_reached', connected_company, None
        if config.DAILY_LOSS_LIMIT is not None and total_pl <= -config.DAILY_LOSS_LIMIT + pnl_buffer_max:
            print(f"  [WARN] '{company} / {portfolio}' is within ${pnl_buffer_max:.2f} of its daily "
                  f"loss limit ({total_pl:.2f} <= {-config.DAILY_LOSS_LIMIT + pnl_buffer_max:.2f}) - "
                  "too little room to safely size a trade. Not trading until the next session.")
            return 'daily_loss_limit_reached', connected_company, None

    tp_ticks_after_balance_cap = trading.adjust_tp_for_max_balance(
        balance, params['tp_ticks'], params['tp_dollars'], tier_range['max']
    )
    # Independently derived from the *original*, unadjusted ticks/dollars
    # (not tp_ticks_after_balance_cap) -- each capping function computes its
    # own $-per-tick ratio from the pair it's given, so chaining an already-
    # shrunk tick count against the original dollar figure would silently
    # produce a wrong ratio. Taking the smaller of the two results respects
    # whichever constraint is tighter.
    tp_ticks_after_pnl_cap, sl_ticks = trading.adjust_ticks_for_daily_pnl(
        total_pl, params['tp_ticks'], params['tp_dollars'], params['sl_ticks'], params['sl_dollars'],
        config.DAILY_PROFIT_LIMIT, config.DAILY_LOSS_LIMIT,
    )
    tp_ticks = min(tp_ticks_after_balance_cap, tp_ticks_after_pnl_cap)

    direction = {'LONG': 'buy', 'SHORT': 'sell'}.get(params['direction'])
    print(f"\n  Executing: {direction.upper()} | TP={tp_ticks} ticks | "
          f"SL={sl_ticks} ticks | contracts={params['contracts']} | '{company} / {portfolio}'")
    entered = trading.place_order(
        driver, tp_ticks=tp_ticks, sl_ticks=sl_ticks, side=direction, units=params['contracts'],
    )
    if not entered:
        if trading.check_entry_rejected(driver):
            print(f"  [WARN] Order for '{company} / {portfolio}' was rejected by the broker.")
            return 'rejected', connected_company, None
        print("\n[FAIL] Trade execution failed.")
        return 'failed', connected_company, None

    bracket = trading.find_working_bracket(driver)
    tp_id, sl_id = bracket.get('tp'), bracket.get('sl')
    if not tp_id or not sl_id:
        # The position may have already resolved (hit TP/SL almost
        # instantly) in the moment between place_order's own confirmation
        # and this re-check, rather than the bracket genuinely never having
        # been created -- check the last bracket regardless of status
        # before giving up, so a real fill doesn't get misreported as Not
        # Taken and silently lost from tracking until the next restart.
        last_bracket = trading.find_last_bracket(driver)
        last_tp_id, last_sl_id = last_bracket.get('tp'), last_bracket.get('sl')
        if last_tp_id and last_sl_id:
            quick_outcome = trading.check_bracket_status(driver, last_tp_id, last_sl_id)
            if quick_outcome in ('tp', 'sl', 'manual_close'):
                print(f"  [WARN] '{company} / {portfolio}' resolved before its bracket could be "
                      f"confirmed as working ({quick_outcome}) - tracking it as already-closed so "
                      "it gets reported normally.")
                tp_id, sl_id = last_tp_id, last_sl_id
        if not tp_id or not sl_id:
            print(f"  [FAIL] Could not confirm bracket IDs after opening '{portfolio}' - "
                  "can't track this position reliably.")
            return 'failed', connected_company, None

    entry = {
        'tp_id': tp_id,
        'sl_id': sl_id,
        'account_type': params['account_type'],
        'asset': params['asset'],
        'direction': params['direction'],
        'contracts': params['contracts'],
        'sl_ticks': sl_ticks,
        'tp_ticks': tp_ticks,
    }
    print(f"\n[OK] '{company} / {portfolio}' opened and tracked.")
    return 'opened', connected_company, entry


def _reconcile_open_positions_at_startup(driver, web_tab, tv_tab):
    """Runs once at startup, before the main loop begins: for every
    (company, portfolio) with a configured Tradovate account, checks
    whether a previous (e.g. crashed) run left a position open or closed
    without reporting it, and either rehydrates it into a fresh
    open_positions ledger or reports its already-known result -- so a
    crash never permanently loses track of a live position or leaves
    TradingGenerator's Trade Result prompt stuck. Also clears any
    portfolio TradingGenerator's "OPEN TRADES" grid still lists as open
    despite having no Trade Result prompt to report through (a new day's
    reset, or a TradingGenerator-side bug) by clicking its own Close Trade
    button instead -- see tg.has_open_trade_card/close_open_trade_card.

    TradingGenerator's Open Trades grid is always checked (tg.
    list_open_trade_cards), never skipped just because a normal Trade
    Result prompt is also present -- Tradovate only ever allows one real
    open position per sub-account, but TradingGenerator can still end up
    with several stale duplicate cards for the same company/portfolio,
    left behind by an earlier, never-cleared attempt, alongside an
    otherwise perfectly normal reportable trade. If Tradovate confirms a
    position really is still open, the newest card is the one recovered
    into the ledger and any older duplicates are closed alongside it; if
    nothing is genuinely open, every remaining card for that
    company/portfolio gets closed once the outcome (if any) is reported.

    Source of truth is live DOM state (Tradovate's Orders table,
    TradingGenerator's still-displayed trade parameters/Trade Result
    prompt), not status.json -- it has no "pending report" bit and could
    itself be stale after a crash.

    Returns (open_positions, connected_company) to seed the main loop with.
    """
    open_positions = {}
    connected_company = None
    driver.switch_to.window(web_tab)
    candidates = tg.list_all_candidates(driver)

    for company, portfolio in candidates:
        if config.TRADOVATE_ACCOUNTS.get(company) is None:
            continue

        driver.switch_to.window(tv_tab)
        connected_company = _ensure_tradovate_connection(driver, company, connected_company)
        if connected_company is None:
            print(f"  [WARN] Startup check: could not connect to Tradovate for '{company}' - skipping.")
            continue
        if not trading.select_tradovate_account(driver, portfolio):
            print(f"  [WARN] Startup check: could not select Tradovate account '{portfolio}' - skipping.")
            continue
        if not trading.click_orders_tab(driver):
            print(f"  [WARN] Startup check: could not open the Orders tab for '{portfolio}' - skipping.")
            continue

        bracket = trading.find_working_bracket(driver)
        tp_id, sl_id = bracket.get('tp'), bracket.get('sl')

        if tp_id and sl_id:
            # Position still open -- TradingGenerator still displays the
            # pending trade's original parameters until a result is
            # reported, so recover them from there rather than guessing.
            driver.switch_to.window(web_tab)
            if not _select_and_verify(driver, company, portfolio):
                print(f"  [WARN] '{company} / {portfolio}' has an open position but couldn't be "
                      "safely selected in TradingGenerator to recover its parameters - leaving it "
                      "for manual review.")
                continue
            params = tg.read_trade_parameters(driver)
            missing = [k for k in ('asset', 'direction', 'contracts', 'sl_ticks', 'tp_ticks', 'account_type')
                       if params.get(k) is None]
            if missing:
                print(f"  [WARN] '{company} / {portfolio}' has an open position but its trade "
                      f"parameters couldn't be fully recovered ({', '.join(missing)}) - leaving it "
                      "for manual review.")
                continue
            open_positions[(company, portfolio)] = {
                'tp_id': tp_id,
                'sl_id': sl_id,
                'account_type': params['account_type'],
                'asset': params['asset'],
                'direction': params['direction'],
                'contracts': params['contracts'],
                'sl_ticks': params['sl_ticks'],
                # Best-effort: this is the original, possibly-unadjusted TP
                # from TradingGenerator (see adjust_tp_for_max_balance) --
                # the live order's actual tick count isn't recoverable here,
                # but this field is only used for status/record-keeping, not
                # for determining the outcome (that's tp_id/sl_id above).
                'tp_ticks': params['tp_ticks'],
            }
            print(f"  [RECOVER] '{company} / {portfolio}' has an open position from a previous run - "
                  "added to the ledger.")
            # Tradovate only ever allows one real open position per sub-
            # account, so the newest Open Trades card is the one just
            # recovered above -- anything older for this same
            # company/portfolio is a stale duplicate left over from an
            # earlier, never-cleared attempt, and needs clearing too.
            extra_closed = tg.close_open_trade_cards(driver, company, portfolio, keep_newest=True)
            if extra_closed:
                print(f"  [WARN] '{company} / {portfolio}' also had {extra_closed} stale duplicate "
                      f"Open Trades entr{'y' if extra_closed == 1 else 'ies'} alongside its real open "
                      "position - closed the older one(s), keeping the newest.")
            continue

        driver.switch_to.window(web_tab)
        if not _select_and_verify(driver, company, portfolio):
            print(f"  [WARN] '{company} / {portfolio}' couldn't be safely selected in "
                  "TradingGenerator to check for a pending Trade Result - skipping.")
            continue
        # _select_and_verify can return instantly with no settle time at all
        # if this portfolio's tab happened to already be marked 'active'
        # (select_portfolio only pauses when it actually has to click) --
        # e.g. right after closing the previous portfolio's last Open
        # Trades card, if TradingGenerator auto-advances its own selection
        # as a side effect. Without a beat here, the Trade Result section
        # and Open Trades grid below can still be catching up to that
        # transition and read as empty even though they're not.
        humanize.pause(0.5, 1.0)

        pending_result = tg.has_pending_trade_result(driver)
        # Always check the Open Trades grid too, even when there's a normal
        # pending Trade Result prompt -- TradingGenerator can end up with
        # extra stale duplicate cards for this same company/portfolio on
        # top of a perfectly normal reportable trade (e.g. left behind by
        # an earlier, never-cleared attempt), so this isn't something the
        # presence of a pending result lets us skip checking.
        has_open_card = tg.has_open_trade_card(driver, company, portfolio)
        if not pending_result and not has_open_card:
            continue

        # Either TradingGenerator still shows a pending Trade Result prompt,
        # or it has no prompt but still lists this portfolio as open -- the
        # position resolved (or was manually closed) while we were down and
        # TradingGenerator's own bookkeeping was never cleared for it.
        driver.switch_to.window(tv_tab)
        bracket = trading.find_last_bracket(driver)
        tp_id, sl_id = bracket.get('tp'), bracket.get('sl')
        if tp_id and sl_id:
            outcome = trading.check_bracket_status(driver, tp_id, sl_id)
        else:
            # No bracket order history at all for this account -- nothing
            # concrete to determine an outcome from, so there's no TP/SL
            # result to report. Reporting Not Taken clears TradingGenerator's
            # stuck entry rather than leaving it stuck for manual review.
            print(f"  [WARN] '{company} / {portfolio}' has an unreported/stale open trade but its "
                  "last bracket order couldn't be found - reporting Trade Not Taken.")
            outcome = 'not_taken'

        driver.switch_to.window(web_tab)
        if not _select_and_verify(driver, company, portfolio):
            print(f"  [WARN] '{company} / {portfolio}' has an unreported/stale open trade but "
                  "couldn't be safely re-selected to clear it - leaving it for manual review.")
            continue

        quarantine_after = False
        if outcome == 'manual_close':
            print(f"  [WARN] '{company} / {portfolio}' closed manually or was liquidated while the "
                  "bot was down - reporting Trade Not Taken and quarantining until the next session.")
            outcome = 'not_taken'
            quarantine_after = True
        elif outcome not in ('tp', 'sl', 'not_taken'):
            print(f"  [WARN] '{company} / {portfolio}' has an unreported/stale open trade but its "
                  f"last bracket status ('{outcome}') couldn't be resolved - leaving it for manual review.")
            continue

        params = tg.read_trade_parameters(driver)
        print(f"  [RECOVER] '{company} / {portfolio}' resolved ({outcome}) while the bot was down and "
              "was never reported - reporting now.")
        tg.report_trade_result(driver, outcome, company=company, portfolio=portfolio)
        status.record_trade_result(
            company, portfolio,
            asset=params['asset'], direction=params['direction'], contracts=params['contracts'],
            sl_ticks=params['sl_ticks'], tp_ticks=params['tp_ticks'], result=outcome,
        )
        if quarantine_after:
            status.mark_portfolio_unavailable(company, portfolio, 'manual_close_or_liquidation')

        # Nothing is genuinely open for this account (confirmed above --
        # this branch is only reached when find_working_bracket found
        # nothing), so every card still left for it in the Open Trades grid
        # is stale -- the report just above may have already cleared one
        # via its own Close Trade fallback, but there can be more than one
        # duplicate left over from an earlier, never-cleared attempt.
        extra_closed = tg.close_open_trade_cards(driver, company, portfolio, keep_newest=False)
        if extra_closed:
            print(f"  Cleared {extra_closed} stale Open Trades entr{'y' if extra_closed == 1 else 'ies'} "
                  f"for '{company} / {portfolio}'.")

    return open_positions, connected_company


def run_web_loop_multi(driver, max_positions_per_company=None, command_name="web_multi", hide_tg_window=None):
    """The trading loop behind both the 'web' and 'web_multi' commands --
    supports several concurrent open positions under the rules described at
    the top of this module. 'web' is this same engine with
    max_positions_per_company forced to 1 (see cli.py): combined with the
    existing "only one company may be engaged at a time" rule, that
    reproduces the single-position-at-a-time behavior 'web' used to
    implement separately, so there's one engine instead of two to maintain.

    Every iteration: refresh every tracked open position's status first
    (reporting/freeing any that resolved), then check whether the next
    signal (per the previous trade's hint) is eligible to open right now.
    If not, wait a bit and refresh again -- positions accumulate in
    `open_positions` and get drained opportunistically rather than a
    strictly one-at-a-time wait-for-close.

    A portfolio whose position closes via manual close/liquidation (see
    _refresh_open_positions) is quarantined until the next session day:
    added to `quarantined`, which gets folded into `unavailable` right
    before every generate attempt so signal_source.generate_next_trade's
    own rotation keeps steering clear of it regardless of `unavailable`'s
    own clear/reset cycles, and gets reset alongside the daily sweep.

    Before the loop starts, _reconcile_open_positions_at_startup() checks
    every configured company/portfolio for a position left open (or closed
    but never reported) by a previous run -- e.g. a crash. Recovered open
    positions seed `open_positions` directly; no new trade is generated
    until they've all drained (same wait as any other open position).

    `hide_tg_window` overrides config.HIDE_TRADINGGENERATOR_WINDOW for this
    run only (None = use the config default) -- see tg.open_tab. Only has
    an effect if TradingGenerator's window/tab isn't already open from
    earlier in this same browser session.
    """
    print(f"\n[{command_name.upper()}] Starting automatic trading loop (Ctrl+C to stop)...")
    tv_tab = driver.current_window_handle
    next_company = None
    next_portfolio = None
    unavailable = set()
    quarantined = set()
    engaged_company = None
    consecutive_failures = 0
    last_backup_date = None
    last_sweep_date = None
    status.update(loop_state='trading', loop_started_at=status.timestamp(), stop_reason=None)
    try:
        web_tab = tg.open_tab(driver, tv_tab, hide_window=hide_tg_window)
        print("  TradingGenerator tab ready [OK]")

        print("  Checking for positions left open or unreported by a previous run...")
        open_positions, connected_company = _reconcile_open_positions_at_startup(driver, web_tab, tv_tab)
        recovering = bool(open_positions)
        if recovering:
            print(f"  Recovered {len(open_positions)} position(s) from a previous run - "
                  "will hold off on new trades until they're all closed.")
        else:
            print("  Nothing to recover - starting fresh.")

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

            if recovering:
                if open_positions:
                    status.update(loop_state='recovering')
                    print(f"  Recovering from a previous run - {len(open_positions)} position(s) still "
                          "open. Waiting for them to close before generating new trades...")
                    humanize.random_wait(*config.POSITION_POLL_RANGE)
                    continue
                print("  Recovery complete - all positions from the previous run are closed and reported.")
                recovering = False

            if session_status == 'waiting':
                status.update(loop_state='waiting_for_session')
                now = config.now_in_israel()
                if open_positions:
                    print(f"  Outside the trading session with {len(open_positions)} position(s) still "
                          "open - waiting for them to close (checking again shortly)...")
                    humanize.random_wait(*config.POSITION_POLL_RANGE)
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
                          "open - waiting for them to close (checking again shortly)...")
                    humanize.random_wait(*config.POSITION_POLL_RANGE)
                    continue
                print(f"  Inside the no-trade window ({config.NO_TRADE_START_TIME.strftime('%H:%M')}-"
                      f"{config.NO_TRADE_END_TIME.strftime('%H:%M')} Israel time) - not generating new "
                      "trades; checking again shortly...")
                humanize.random_wait(*config.PORTFOLIO_RETRY_RANGE)
                continue

            # next_direction is unknown at this point (only a portfolio hint
            # is available before Generate is clicked) -- the hedge-conflict
            # check is skipped here and applied for real below, once
            # params['direction'] is known.
            eligibility = check_eligibility(
                next_company, next_portfolio, None, engaged_company, open_positions,
                max_positions_per_company=max_positions_per_company,
            )
            if eligibility != 'eligible':
                status.update(loop_state='waiting_for_position_slot')
                print(f"  Not eligible to open the next signal yet ({eligibility}) - "
                      "checking again shortly...")
                humanize.random_wait(*config.POSITION_POLL_RANGE)
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
                print("  No tradeable portfolio available right now (all locked/unavailable) - "
                      "checking again shortly...")
                unavailable.clear()
                status.update(loop_state='waiting_for_portfolio')
                humanize.random_wait(*config.PORTFOLIO_RETRY_RANGE)
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
                if not _select_and_verify(driver, company, portfolio):
                    print(f"  [WARN] Could not safely select '{company} / {portfolio}' to report "
                          "Not Taken - skipping (it may have just been removed).")
                    return
                tg.report_trade_result(driver, 'not_taken', company=company, portfolio=portfolio)
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
                portfolio_eligibility = check_eligibility(
                    company, portfolio, params['direction'], engaged_company, open_positions,
                    max_positions_per_company=max_positions_per_company,
                )
                if portfolio_eligibility == 'eligible':
                    portfolio_params = dict(params, portfolio=portfolio)
                    open_outcome, connected_company, entry = _open_position(
                        driver, web_tab, tv_tab, portfolio_params, connected_company
                    )
                    if open_outcome == 'opened':
                        open_positions[(company, portfolio)] = entry
                        engaged_company = company
                        opened_any = True
                        continue
                    if open_outcome == 'rejected':
                        print(f"  [WARN] Quarantining '{company} / {portfolio}' until the next session.")
                        quarantined.add((company, portfolio))
                        status.mark_portfolio_unavailable(company, portfolio, 'order_rejected')
                    elif open_outcome in ('daily_profit_limit_reached', 'daily_loss_limit_reached'):
                        limit_kind = 'profit' if open_outcome == 'daily_profit_limit_reached' else 'loss'
                        print(f"  [WARN] Quarantining '{company} / {portfolio}' until the next session "
                              f"(daily {limit_kind} limit reached).")
                        quarantined.add((company, portfolio))
                        status.mark_portfolio_unavailable(company, portfolio, open_outcome)
                    else:
                        print(f"  [FAIL] Could not open '{company} / {portfolio}'.")
                else:
                    print(f"  [WARN] '{company} / {portfolio}' isn't eligible right now "
                          f"({portfolio_eligibility}) - can't open it for this signal.")

                # Not opened (ineligible, failed, or rejected) -- there's no
                # later retry for this specific signal instance, so report
                # Not Taken on this portfolio's own field.
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
