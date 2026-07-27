# `web_multi` — the multi-position trading loop

`web_multi` is a second automatic trading command, alongside the original `web`. `web` trades one signal at a time — generate, open, wait for it to close, report, repeat. `web_multi` can hold several positions open at once, across several accounts of the same prop firm, under the rules below. The two commands are independent: `web` never changed and works exactly as before; `web_multi` lives in its own file, [tv_signal_trader/multi_signal_source.py](../tv_signal_trader/multi_signal_source.py).

Type `web_multi` at the `>` prompt to run it, same as `web`.

## The concurrency rules

1. **One open position per portfolio.** A portfolio that already has a trade open won't get a second one until the first closes.
2. **At most `MAX_POSITIONS_PER_COMPANY` open positions per company at once** (default 3, set in `.env`).
3. **Only one company may have open positions at a time.** If a signal comes in for a different company while the current one still has open positions, it waits for all of them to close first, then switches over.

The bot decides whether it's allowed to open a signal *before* pressing "Generate New Trade" — it always knows the next portfolio TradingGenerator wants to trade next (from the "Next Portfolio to Trade" box), and checks it against the rules above first.

**How to test:** open a trade on Portfolio A, let TradingGenerator's next signal land on Portfolio B of the *same* company, and confirm the bot opens B alongside A (2 open at once) rather than waiting. Then let a signal come in for a *different* company and confirm the bot waits (you'll see `wait_different_company` printed, checking again shortly) until A and B both close before opening there.

## No hedging

The bot won't open a trade at a company that already has an open position in the *opposite* direction — e.g. if Portfolio A is currently long, a new short signal at the same company gets reported Not Taken and the bot waits for A (and any other open position at that company) to close before trying again. Same-direction signals at the same company are unaffected (that's just the normal multi-position case above).

**How to test:** with a long position open via `web_multi`, arrange for the next signal at the same company to be a short (or vice versa) — confirm the console prints `wait_hedge_conflict`, Not Taken is reported, and the bot doesn't open the opposite-direction trade until the existing position closes.

## Multiple portfolios per signal

Sometimes TradingGenerator says one signal should be opened on *several* portfolios of the same company at once (the "Taken in the following portfolios" box on its page). The bot opens the trade on every one of those portfolios independently — each has its own balance, its own risk of hitting a limit, and its own "Trade Result" buttons on TradingGenerator, so each is opened and later reported separately, not as one bundle.

**How to test:** wait for (or trigger) a signal that lists more than one portfolio under "Taken in the following portfolios", and confirm the bot opens a trade on each one listed, then reports each one's result independently once they close.

## Account balance limits

Every account is assumed to be a $25K or $50K account (whichever is numerically closer to its current balance), each with a minimum balance (max loss) and a maximum balance (profit target). If a trade closing would leave — or a balance check finds — an account at or beyond its limit, that portfolio is removed from TradingGenerator automatically.

Since $25K/$50K "eval" and "live" accounts usually have different profit targets, the maximum is configured separately per type; the minimum is shared. See `.env`:

```
ACCOUNT_25K_MIN_BALANCE=23000
ACCOUNT_50K_MIN_BALANCE=47500
ACCOUNT_25K_MAX_BALANCE_EVAL=27000
ACCOUNT_25K_MAX_BALANCE_LIVE=27000
ACCOUNT_50K_MAX_BALANCE_EVAL=53000
ACCOUNT_50K_MAX_BALANCE_LIVE=53500
```

**How to test:** temporarily set a MAX (or MIN) very close to an account's real current balance, run `web_multi`, and confirm it removes that portfolio from TradingGenerator instead of trading it. Remember to set the values back afterward.

## Daily profit limit

An optional per-account cap on today's profit, read from Tradovate's broker-panel Account Summary tab ("Total P/L", which resets daily on Tradovate's side). Once an account's Total P/L reaches the limit, that account stops opening new trades for the rest of the day — quarantined the same way as a manual close or a rejected order — but keeps reporting/monitoring any position already open normally. Set in `.env`:

```
DAILY_PROFIT_LIMIT=500
```

Leave commented out (the default) to disable it entirely.

**How to test:** set the limit just below an account's current Total P/L, run `web_multi`, and confirm the console prints that the account hit its daily profit limit and is quarantined, rather than opening the next signal for it.

## Take-profit capping

If a trade's take-profit would push an account's balance *past* its maximum, the bot shrinks the take-profit so the account instead lands just above the maximum — by a random amount between `TP_CAP_BUFFER_MIN` and `TP_CAP_BUFFER_MAX` dollars (default $50–$200) — rather than blowing far past it. This keeps profit-target accounts from wildly overshooting once they're close to passing.

**How to test:** get an account's balance close to its maximum, then let it take a trade whose full take-profit would cross that maximum — check the console output for a line saying the TP was capped, and confirm the resulting order's TP ticks are smaller than TradingGenerator's original number.

## No-trade window

A second, optional time window inside the main trading session where the bot simply won't start any *new* trades — useful for a lunch break or a period you don't want to trade through. Positions already open keep running and get reported normally; only new trades are paused. Set in `.env`:

```
NO_TRADE_START_TIME=12:00
NO_TRADE_END_TIME=13:00
```

Leave both blank to disable it.

**How to test:** set the window to bracket the next few minutes, run `web_multi`, and confirm it prints that it's pausing and stops generating trades, then resumes on its own once the window passes.

## Manual close / liquidation

If a position's Take Profit *and* Stop Loss both end up cancelled and *neither* filled, something closed it outside of the bot's own control — you closed it by hand, or the account was liquidated. There's no correct result to report for that, so the bot doesn't report anything; it just quarantines that portfolio (won't trade it again) until the next trading session starts.

**How to test:** with a position open via `web_multi`, manually close it from the Tradovate panel in TradingView. Confirm the bot detects this, logs a warning, and doesn't try opening a new trade on that same portfolio again until you restart the session (or the next day's session begins).

## Rejected orders

If the broker rejects an order outright (e.g. the position size requested is too large for the account), no Take Profit/Stop Loss brackets ever get created. The bot recognizes this specifically (checking the Orders table for a "Rejected" entry order) and quarantines that portfolio the same way as a manual close — until the next session.

**How to test:** hard to trigger deliberately (it depends on the broker actually rejecting something, e.g. requesting far more contracts than the account allows). If you do hit one, confirm the console shows it was recognized as a rejection (not just a generic failure) and that the portfolio gets quarantined.

## Daily account sweep

Once per calendar day (at startup if already inside the trading session, or right when a new day's session opens), the bot checks each configured company: it connects to that company's Tradovate login, lists every actual sub-account there, and compares that against the portfolios shown in TradingGenerator. Any TradingGenerator portfolio whose account isn't in the Tradovate list anymore has been liquidated, so it gets removed from TradingGenerator automatically.

**How to test:** hard to fully test without an actually-liquidated account. At minimum, confirm the sweep runs cleanly on startup (look for `[SWEEP] Checking for liquidated accounts...` and `[SWEEP] Done.` in the console) without errors, for every company you have a Tradovate account configured for.

## Crash recovery (startup reconciliation)

Every time `web_multi` starts, before it generates any new trade, it checks every configured company/portfolio for a position a *previous* run left behind (e.g. the bot crashed or was closed unexpectedly):

- **Still open** (a working Take Profit/Stop Loss bracket): recovered straight into the ledger, same as if the bot had just opened it itself. Its trade parameters (asset/direction/contracts/SL/TP) are read back from TradingGenerator, which keeps showing them until a result is reported.
- **Already closed, but TradingGenerator's Trade Result prompt is still waiting**: the bot works out whether it hit TP or SL from the Orders table and reports it now, exactly as if it had just happened.
- **Closed manually/liquidated while the bot was down** (neither TP nor SL filled): same as a live manual close — no result is reported, that portfolio is quarantined until the next session.

While any recovered position remains open, the bot won't generate any new trade — it just keeps monitoring/reporting (the same as any other open position) until the ledger is fully drained, then resumes normally.

**How to test:** open a position via `web_multi`, then stop the bot (Ctrl+C or close the browser) while it's still open. Restart `web_multi` and confirm the console reports finding it ("Recovered N position(s) from a previous run"), keeps monitoring it until it closes, and only then starts generating new trades. To test the "closed but unreported" path, stop the bot, manually resolve the position from the Tradovate panel (let TP or SL fill), then restart and confirm it detects and reports the result on its own.

## Randomized polling delays

`web_multi` (like `web`) checks on a randomized cadence rather than a fixed one, so it doesn't tick at a perfectly regular interval over a long unattended run. See the main [README](../README.md) — this behavior is shared with `web`, not specific to `web_multi`.

**How to test:** run `web_multi` with an open position and watch the console's "checking again" messages — they should land after varying delays, not a constant one.

## Where things live, if you need to dig deeper

| What | Where |
|------|-------|
| The main loop, eligibility rules, opening/refreshing positions | [tv_signal_trader/multi_signal_source.py](../tv_signal_trader/multi_signal_source.py) |
| Reading balances, placing orders, checking bracket status, Tradovate accounts | [tv_signal_trader/trading.py](../tv_signal_trader/trading.py) |
| Reading TradingGenerator's page, switching company/portfolio, reporting results | [tv_signal_trader/tradinggenerator.py](../tv_signal_trader/tradinggenerator.py) |
| All the `.env` settings mentioned above | [tv_signal_trader/config.py](../tv_signal_trader/config.py) |
| What each portfolio is doing right now | `status.json`, next to `.env` |
