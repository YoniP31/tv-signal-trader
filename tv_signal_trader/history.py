"""Pure functions over an account's persisted equity history
(status.get_equity_history) -- a list of {date, equity} dicts, oldest
first: the day-consistency/day-count math Flip Mode's state machine needs
(daily_profits/best_day_profit/total_profit/profitable_day_count), and
Second Withdrawal's own detect_withdrawal (see the Flip Mode plan's
Phase 3).

A day's profit isn't stored directly -- it's always the delta from the
previous *included* day's equity (or from `starting_balance` for the
first included day -- see daily_profits' own docstring for what that
means once a `since_date` cycle cutoff is involved), computed here
rather than at write time, so there's a single place responsible for
what "a day's profit" means. Deliberately takes plain lists/numbers
rather than reading status.json itself, so this stays trivial to unit
test with fixture data and has no live-DOM dependency at all.
"""


def daily_profits(days, starting_balance, since_date=None):
    """[(date, profit), ...] for every day on/after `since_date` (all of
    them, oldest first, if since_date is None), where profit is the
    equity delta from the previous *included* day -- or from
    `starting_balance` for the first included day.

    `since_date` (inclusive) is Flip Mode's per-withdrawal-cycle tracking
    (see the Flip Mode plan): once an account is re-added to
    TradingGenerator after a detected withdrawal, consistency/day-count
    must measure profit from the equity right after that withdrawal, not
    from the account's original onboarding balance -- a withdrawal isn't a
    trading loss and must never be counted as one. So `starting_balance`
    here needs to be *that* cycle's starting equity (the caller's
    responsibility to pass the right one for since_date), and the day
    right at/after since_date resets against it directly rather than
    continuing the chain from whatever the last pre-cutoff day happened to
    record -- days before since_date are skipped entirely, not used to
    compute anything.
    """
    profits = []
    previous_equity = starting_balance
    baseline_pending = since_date is not None
    for day in days:
        if since_date is not None and day["date"] < since_date:
            continue
        if baseline_pending:
            previous_equity = starting_balance
            baseline_pending = False
        profits.append((day["date"], day["equity"] - previous_equity))
        previous_equity = day["equity"]
    return profits


def best_day_profit(days, starting_balance, since_date=None):
    """The single largest day's profit, or 0.0 if there's no history yet."""
    profits = daily_profits(days, starting_balance, since_date=since_date)
    if not profits:
        return 0.0
    return max(profit for _date, profit in profits)


def total_profit(days, starting_balance, since_date=None):
    """Profit-to-date -- with no since_date, this telescopes to the latest
    recorded equity minus `starting_balance`; with one, it's profit since
    that date's cutoff instead (the sum of daily_profits' filtered
    deltas). Returns 0.0 if there's no history yet."""
    if not days:
        return 0.0
    profits = daily_profits(days, starting_balance, since_date=since_date)
    return sum(profit for _date, profit in profits)


def profitable_day_count(days, starting_balance, min_profit, since_date=None):
    """How many recorded days had a profit >= min_profit."""
    profits = daily_profits(days, starting_balance, since_date=since_date)
    return sum(1 for _date, profit in profits if profit >= min_profit)


def detect_withdrawal(days, current_balance, today_total_pl, tolerance=1.0):
    """(is_withdrawal, details) -- True if `current_balance` has dropped by
    more than today's own trading P&L (`today_total_pl` -- Tradovate's
    "Total P/L", resets daily, same figure the daily profit/loss limit
    checks already read via trading.read_total_pl) can account for -- see
    the Flip Mode plan's Phase 3 (Second Withdrawal detection, LIVE
    accounts only).

    Comparing against the recorded equity history alone isn't enough: an
    ordinary losing trading day also drops the balance, and must not be
    mistaken for a withdrawal. So this checks what's *unexplained* by
    trading instead:

        unexplained = (current_balance - last_recorded_equity) - today_total_pl

    A withdrawal is `unexplained` meaningfully negative (more balance
    left the account than trading alone accounts for) -- which also
    correctly catches a withdrawal that happened *alongside* trading the
    same day (e.g. a $200 trading loss plus a $500 withdrawal shows up
    as unexplained = -500, not -700, since the trading loss is its own,
    separate, real thing), not just a clean no-trading day.

    `is_withdrawal` is False (never guesses) if there's no recorded
    history yet, or if `today_total_pl` couldn't be read at all -- `None`
    there means "unknown", not "zero".

    `details` -- a dict of every intermediate value this computed, mirroring
    flip_mode.evaluate()'s own (decision, ..., details) shape and for the
    same reason: this function itself stays silent (pure, hand-derived-
    testable) and a caller narrates it (see
    signal_source._print_withdrawal_check), rather than ever guessing at
    why a particular call came back False from the outside. Always
    contains 'last_recorded_date'/'last_recorded_equity' (None if `days`
    is empty), 'current_balance', 'today_total_pl', 'tolerance', and
    'actual_change'/'unexplained' (None if there was nothing to compute
    them from -- no history, or no readable P&L).
    """
    details = {
        'last_recorded_date': days[-1]['date'] if days else None,
        'last_recorded_equity': days[-1]['equity'] if days else None,
        'current_balance': current_balance,
        'today_total_pl': today_total_pl,
        'tolerance': tolerance,
        'actual_change': None,
        'unexplained': None,
    }
    if not days or today_total_pl is None:
        return False, details
    last_recorded_equity = days[-1]["equity"]
    actual_change = current_balance - last_recorded_equity
    unexplained = actual_change - today_total_pl
    details['actual_change'] = actual_change
    details['unexplained'] = unexplained
    return unexplained < -tolerance, details
