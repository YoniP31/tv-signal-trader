"""Flip Mode's 3-condition profit-target state machine (see the Flip Mode
plan) -- pure decision logic only, no driver/DOM access at all, so every
branch can be covered with hand-derived unit test cases instead of relying
on live testing (this project has already been burned once by a subtly
wrong branch in similar condition logic elsewhere this session).

evaluate() is the single entry point; everything else here is a private
helper built for it. Deliberately takes plain values (current balance,
equity history, tier thresholds, the three configured knobs) rather than
reading config.py/status.py itself -- a later integration step owns
fetching real data and acting on/persisting the returned decision; this
module only ever decides. It also stays silent (no print calls, no
logging_utils import) on principle -- narrating *why* a decision was
reached belongs to whoever actually has a console to narrate to, using
the `details` this returns; a pure module has no business assuming one
exists. See multi_signal_source.evaluate_account_for_removal for that
narration.
"""

from . import history


def _consistency_check(days, starting_balance, consistency_divisor, since_date=None):
    """Returns (holds, total_profit, best_day_profit). Consistency holds
    when the single best day's profit is at least `consistency_divisor`
    of total profit-to-date -- i.e. no other day (or combination of days)
    contributed more than the remaining share. 50K start, $2,500 best
    day, divisor 0.5: holds as long as total profit stays at/under
    $5,000 (2x the best day) -- see the Flip Mode plan's own worked
    example."""
    total = history.total_profit(days, starting_balance, since_date=since_date)
    best_day = history.best_day_profit(days, starting_balance, since_date=since_date)
    return best_day >= consistency_divisor * total, total, best_day


def _day_count_check(days, starting_balance, min_profitable_days, min_daily_profit, since_date=None):
    """Returns (met, actual_count)."""
    count = history.profitable_day_count(days, starting_balance, min_daily_profit, since_date=since_date)
    return count >= min_profitable_days, count


def _recompute_target(consistency_holds, best_day_profit, starting_balance, tier_final, consistency_divisor):
    """The equity target for the next round of NORMAL trading: the tier's
    final threshold if consistency already holds, or -- if it doesn't --
    the lowest balance at which it WOULD hold (plus a $1 buffer so it's a
    strict improvement over where it just failed), whichever is higher.

    This is "starting_balance + 2*best_day_profit + $1" in the Flip Mode
    plan's own words -- the "2" there is 1/consistency_divisor (0.5 by
    default), not a separate hardcoded constant: it's exactly the total
    profit at which consistency's own boundary sits, re-derived here so
    this generalizes to any configured divisor instead of silently
    assuming 0.5. Takes consistency_holds/best_day_profit already
    computed by the caller (see _consistency_check) rather than
    re-deriving them, so this is never subtly evaluated against a
    different day/since_date window than the decision that led here."""
    if consistency_holds:
        return tier_final
    escalated = starting_balance + (best_day_profit / consistency_divisor) + 1
    return max(tier_final, escalated)


def evaluate(
    *,
    current_balance,
    starting_balance,
    tier_final,
    running_equity_target,
    in_flip_mode,
    days,
    min_profitable_days,
    min_daily_profit,
    consistency_divisor,
    since_date=None,
):
    """One evaluation pass of the Flip Mode state machine (see the Flip
    Mode plan's own flow diagram) for a single account. `days` is that
    account's full equity history (status.get_equity_history); `since_date`
    restricts the consistency/day-count math to the account's current
    withdrawal cycle (see history.py's own since_date support) -- None
    considers the whole history, correct for an account still on its
    first cycle.

    `starting_balance` must match whichever cycle `since_date` selects:
    the account's true original tier size (25000/50000) on a first cycle
    (since_date=None), or the equity right after its most recent detected
    withdrawal for any cycle after that (see
    status.set_cycle_starting_balance) -- never the original tier size
    once a cycle has actually reset. A withdrawal isn't a trading loss;
    history.py's since_date-aware helpers reset their own delta baseline
    to `starting_balance` at the cycle boundary specifically so a
    withdrawal is never counted as one.

    Returns (decision, new_running_equity_target, details):

    - 'keep_normal': not yet at (or fell back under) the target; nothing
      to do. The returned target is unchanged if the account never even
      reached running_equity_target this pass, or freshly re-derived if
      it did but the (possibly re-derived) target still isn't cleared.
    - 'enter_flip_mode': target reached, consistency held, but day-count
      isn't met yet -- caller should call tradinggenerator.enable_flip_mode.
    - 'keep_flip_mode': already in Flip Mode, day-count still not met --
      nothing to do, target unchanged.
    - 'exit_flip_mode': was in Flip Mode, day-count is now met, but
      consistency and/or the (still-unchanged-until-now) target no longer
      both hold -- caller should call tradinggenerator.disable_flip_mode.
      A freshly re-derived target is returned.
    - 'qualifies_for_removal': every condition holds -- caller should call
      tradinggenerator.remove_portfolio (and, for a LIVE account,
      eventually detect the resulting withdrawal -- see Phase 3).

    `details` is a dict for narrating *why*, meant to be logged rather
    than branched on: 'current_balance', 'running_equity_target_before',
    'in_flip_mode', 'consistency_divisor', and 'min_profitable_days' are
    always present; 'total_profit'/'best_day_profit'/'consistency_holds'
    and 'profitable_days'/'day_count_met' are None unless this pass
    actually checked that condition (e.g. 'keep_flip_mode' never reaches
    the consistency check at all, so those stay None).
    """
    details = {
        'current_balance': current_balance,
        'running_equity_target_before': running_equity_target,
        'in_flip_mode': in_flip_mode,
        'consistency_divisor': consistency_divisor,
        'total_profit': None,
        'best_day_profit': None,
        'consistency_holds': None,
        'min_profitable_days': min_profitable_days,
        'profitable_days': None,
        'day_count_met': None,
    }

    if in_flip_mode:
        day_count_met, profitable_days = _day_count_check(
            days, starting_balance, min_profitable_days, min_daily_profit, since_date=since_date
        )
        details['day_count_met'] = day_count_met
        details['profitable_days'] = profitable_days
        if not day_count_met:
            return 'keep_flip_mode', running_equity_target, details

        consistency_holds, total_profit, best_day_profit = _consistency_check(
            days, starting_balance, consistency_divisor, since_date=since_date
        )
        details['consistency_holds'] = consistency_holds
        details['total_profit'] = total_profit
        details['best_day_profit'] = best_day_profit
        if consistency_holds and current_balance >= running_equity_target:
            return 'qualifies_for_removal', running_equity_target, details

        new_target = _recompute_target(
            consistency_holds, best_day_profit, starting_balance, tier_final, consistency_divisor
        )
        return 'exit_flip_mode', new_target, details

    # NORMAL trading.
    if current_balance < running_equity_target:
        return 'keep_normal', running_equity_target, details

    consistency_holds, total_profit, best_day_profit = _consistency_check(
        days, starting_balance, consistency_divisor, since_date=since_date
    )
    details['consistency_holds'] = consistency_holds
    details['total_profit'] = total_profit
    details['best_day_profit'] = best_day_profit
    new_target = _recompute_target(
        consistency_holds, best_day_profit, starting_balance, tier_final, consistency_divisor
    )
    if current_balance < new_target:
        return 'keep_normal', new_target, details

    day_count_met, profitable_days = _day_count_check(
        days, starting_balance, min_profitable_days, min_daily_profit, since_date=since_date
    )
    details['day_count_met'] = day_count_met
    details['profitable_days'] = profitable_days
    if day_count_met:
        return 'qualifies_for_removal', new_target, details
    return 'enter_flip_mode', new_target, details
