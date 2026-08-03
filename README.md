# TradingView Auto-Trader

Selenium-based bot that reads a trade signal from [TradingGenerator](https://tradinggenerator-english.tiiny.co/) (asset, direction, contract count, stop-loss/take-profit in ticks, and which prop firm/portfolio it's for), switches the TradingView chart to the matching futures symbol, connects the matching Tradovate account, and places the trade (Market order + Take Profit + Stop Loss) through TradingView's order ticket. Runs unattended across multiple companies/portfolios, a bounded daily trading session, and account balance limits.

## What the code currently does

The implementation lives in the [tv_signal_trader/](tv_signal_trader/) package, entered via [main.py](main.py):

| File | Responsibility |
|------|----------------|
| [main.py](main.py) | Entry point — `python main.py` |
| [stop.py](stop.py) | Standalone script (not part of the package) — a guaranteed, independent way to stop everything if Ctrl+C ever doesn't; see "How to run it" below |
| [tv_signal_trader/config.py](tv_signal_trader/config.py) | Paths, URLs, `.env`-backed settings, and other constants |
| [tv_signal_trader/setup_wizard.py](tv_signal_trader/setup_wizard.py) | First-run/`setup` command: prompts for and persists TradingGenerator credentials and (multiple, one per prop firm) Tradovate accounts |
| [tv_signal_trader/browser.py](tv_signal_trader/browser.py) | Chrome setup, stealth tweaks, download preferences, and process lifecycle (`is_alive`, `force_kill`, `hide_window_by_title`) |
| [tv_signal_trader/humanize.py](tv_signal_trader/humanize.py) | Randomized pauses and human-like typing |
| [tv_signal_trader/panel.py](tv_signal_trader/panel.py) | Low-level DOM helpers for reading/filling the order-ticket panel |
| [tv_signal_trader/trading.py](tv_signal_trader/trading.py) | All TradingView-side actions: connecting/switching Tradovate accounts and sub-accounts, `place_order()`, chart switching, checking bracket/trade status, reading account balance |
| [tv_signal_trader/tradinggenerator.py](tv_signal_trader/tradinggenerator.py) | All TradingGenerator-side actions: login, company/portfolio tab switching, generating a trade, reading its parameters, reporting the result, removing a portfolio, saving a backup |
| [tv_signal_trader/signal_source.py](tv_signal_trader/signal_source.py) | Shared helpers reused by the trading loop below: `generate_next_trade()`, `sweep_liquidated_accounts()`, `report_not_taken()` |
| [tv_signal_trader/multi_signal_source.py](tv_signal_trader/multi_signal_source.py) | `run_web_loop_multi()` — the trading loop behind **both** `web` and `web_multi` (see below); the concurrency support that used to make this "multi_signal_source" a separate, `web_multi`-only path is now just a parameter |
| [tv_signal_trader/status.py](tv_signal_trader/status.py) / [state.py](tv_signal_trader/state.py) / [monitor.py](tv_signal_trader/monitor.py) | `status.json` tracking (app/loop state, per-portfolio balances and trade history) and the background login/browser-alive poller |
| [tv_signal_trader/cli.py](tv_signal_trader/cli.py) | Interactive command loop |

### 1. Browser setup

On startup ([tv_signal_trader/browser.py](tv_signal_trader/browser.py)):

- Launches Chrome via `chromedriver` with a persistent profile folder (`tv_profile` in the user's home directory, resolved with `os.path.expanduser("~")`), so you only need to log into TradingView once. No explicit driver executable is configured — [Selenium Manager](https://www.selenium.dev/documentation/selenium_manager/) (built into Selenium 4.6+) detects the installed Chrome version and downloads a matching `chromedriver` automatically on first run, caching it for later ones.
- Applies a few anti-bot-detection tweaks (custom user-agent, hides `navigator.webdriver`, fakes `navigator.plugins`/`navigator.languages`) so TradingView is less likely to flag the session as automated.
- Sets Chrome to always download automatically without prompting, into `config.APP_DIR` (next to `.env`/`status.json`) — needed for TradingGenerator's "Save Backup" download (see the trading loop below) to work headlessly instead of stalling behind a native "Save As" dialog.
- Opens the chart at `config.CHART_URL` (the default startup symbol — the `web` flow below switches it to whatever symbol the signal actually calls for).

TradingGenerator credentials are read from a local `.env` file ([tv_signal_trader/config.py](tv_signal_trader/config.py)) — see "First-run setup" below for how that gets populated.

### 2. First-run setup — [tv_signal_trader/setup_wizard.py](tv_signal_trader/setup_wizard.py)

Before opening the browser, `ensure_configured()` checks `.env` for a TradingGenerator username/password, and for at least one Tradovate account. Anything missing is prompted for right there in the terminal and written back to `.env` — no manual file editing needed. Tradovate accounts are entered per prop firm: pick one from a fixed list (`config.PROP_FIRMS`) and enter its username/password; each firm gets at most one account, and `setup` lets you add/update as many as you need. The password prompts are plain, visible `input()` rather than masked ones: `getpass` reads via the Windows console API directly rather than stdin, which mishandled pasted text and hid what was typed with no way to catch the corruption. Already-configured values are left untouched and skipped silently. Type `setup` at the `>` prompt anytime to change any of them.

A handful of settings live in `.env` but aren't part of this wizard — edit `.env` directly, or start from [docs/.env](docs/.env), a committed template listing every field with credentials blanked out:

- **Account balance limits** (`ACCOUNT_25K_MIN_BALANCE`/`ACCOUNT_50K_MIN_BALANCE`, `ACCOUNT_25K_MAX_BALANCE_EVAL`/`_LIVE`/`ACCOUNT_50K_MAX_BALANCE_EVAL`/`_LIVE`) — see step 5 below.
- **Trading session window** (`SESSION_START_TIME`/`SESSION_END_TIME`, `HH:MM` 24-hour, Israel time) — see step 6 below. Leave unset to allow trading at any time.
- **No-trade window** (`NO_TRADE_START_TIME`/`NO_TRADE_END_TIME`), **take-profit cap buffer** (`TP_CAP_BUFFER_MIN`/`TP_CAP_BUFFER_MAX`), **daily profit/loss limits** (`DAILY_PROFIT_LIMIT`/`DAILY_LOSS_LIMIT`) and their cap buffer (`DAILY_PNL_CAP_BUFFER_MIN`/`DAILY_PNL_CAP_BUFFER_MAX`), and **max positions per company** (`MAX_POSITIONS_PER_COMPANY`, forced to 1 for `web` regardless of this setting) — see [docs/WEB_MULTI.md](docs/WEB_MULTI.md), which documents the shared trading loop behind both `web` and `web_multi`.

### 3. Order placement — `place_order(driver, tp_ticks, sl_ticks, side, units)`

This is the core trading action. It drives TradingView's order panel (the right-hand sidebar), preferring stable selectors (`id`/`data-qa-id` attributes) where TradingView's markup actually provides them, and falling back to coordinate/DOM-text matching where it doesn't:

1. Clicks the page body and sends TradingView's built-in keyboard shortcut (`Shift+B` for buy, `Shift+S` for sell) to open the order ticket.
2. Clicks the **Market** order-type button.
3. Makes sure the "Quantity type" dropdown (`#quantity-dropdown-types`) is set to **Units** (not Contracts/Lots/etc.) before typing `units` into `#quantity-field`.
4. Expands the **Exits** section (if collapsed) to reveal the Take Profit/Stop Loss controls.
5. Turns on the **Take Profit** and **Stop Loss** toggles.
6. Makes sure each of the TP/SL bracket dropdowns (`order-ticket-take-profit-dropdown-button` / `order-ticket-stop-loss-dropdown-button`) is set to **Ticks** — since individual menu options aren't uniquely identifiable, "Ticks" is selected positionally (it's always the 2nd item in the menu) — then types `tp_ticks` / `sl_ticks` directly into the `order-ticket-take-profit-input` / `order-ticket-stop-loss-input` fields.
7. Clicks the final **Buy**/**Sell** confirm button.

All typing is done character-by-character with randomized delays (`type_humanlike`), and most steps have randomized pauses between them, to look more like a human user than a script.

### 4. The automatic trading loop — `run_web_loop_multi(driver, ...)`

**`web` and `web_multi` are the same engine** ([tv_signal_trader/multi_signal_source.py](tv_signal_trader/multi_signal_source.py), orchestrating [tv_signal_trader/tradinggenerator.py](tv_signal_trader/tradinggenerator.py) and [tv_signal_trader/trading.py](tv_signal_trader/trading.py)), not two separate implementations — the only difference is `MAX_POSITIONS_PER_COMPANY`. `web` forces it to 1; combined with the engine's "only one company may be engaged at a time" rule, that reproduces single-position-at-a-time behavior system-wide. `web_multi` uses whatever's configured in `.env` (default 3), allowing several concurrent positions per company. There's one engine to maintain instead of two, and `web` automatically benefits from everything the engine does — quarantining a manually-closed or rejected-order portfolio instead of stopping outright, daily profit/loss limits, crash recovery on restart, and so on — not just `web_multi`.

Stoppable with Ctrl+C at any point (see "Stopping it" below). Most conditions that used to stop the loop don't — a locked portfolio, a cooldown, a temporarily-unusable account, a manual close/liquidation, a rejected order, or an outside-session-hours pause are all retried, quarantined, or waited out instead. It only stops for real if TradingGenerator login fails or the generate button disappears entirely.

At a high level, every iteration: refreshes any already-tracked open position(s) first (reporting/freeing any that resolved), checks whether the next signal is eligible to open given the concurrency rules, and if so clicks **GENERATE NEW TRADE**, reads the resulting trade parameters, connects the right Tradovate account, checks balance and daily P&L limits (capping the take-profit/stop-loss if a win or loss would cross either), places the order, and tracks it going forward. TradingGenerator's own window is opened hidden by default (`config.HIDE_TRADINGGENERATOR_WINDOW = True`, Windows only, via `browser.hide_window_by_title`) — typing `web`/`web_multi` at the `>` prompt asks whether to show it just for that run instead (Enter keeps whatever `config.py` says); set `HIDE_TRADINGGENERATOR_WINDOW` to `False` in code if you want visible to be the persistent default. Either way, the prompt only has an effect the first time TradingGenerator's window/tab is actually created in a given browser session — once it's open, later prompts (e.g. running `web` then `web_multi` back to back) can't retroactively hide/show it.

Full step-by-step detail, every configurable behavior (account balance limits, trading session window, no-trade window, daily profit/loss limits and their TP/SL capping, the hedging guard, crash recovery, the daily liquidated-account sweep), and how to test each one are in **[docs/WEB_MULTI.md](docs/WEB_MULTI.md)** — despite the filename, it now documents the engine shared by both commands, not just `web_multi`.

### 5. Account balance limits and trading session window

Checked before opening a trade and right after one closes: an account whose balance is at/beyond its tier's min (blown past its loss limit) or max (hit its profit target) gets pulled out of TradingGenerator automatically. The tiers' min/max are placeholder defaults, overridable per-tier via `.env` (`ACCOUNT_25K_MIN_BALANCE`/`ACCOUNT_50K_MIN_BALANCE`, plus `ACCOUNT_25K_MAX_BALANCE_EVAL`/`_LIVE`/`ACCOUNT_50K_MAX_BALANCE_EVAL`/`_LIVE`) — not part of the setup wizard, so edit `.env` directly.

`SESSION_START_TIME`/`SESSION_END_TIME` in `.env` (`HH:MM`, 24-hour, Israel time) bound when the loop will generate new trades — unset by default, so trading is allowed at any time. Outside the window, the loop waits until it opens and resumes automatically rather than stopping for the day; a position already open when the window closes keeps running normally. Once per day, right as the session ends, it also saves a TradingGenerator backup.

Full detail on both (including the exact balance-tier guessing logic and account-type handling) is in [docs/WEB_MULTI.md](docs/WEB_MULTI.md).

### 6. Status tracking — [tv_signal_trader/status.py](tv_signal_trader/status.py), [state.py](tv_signal_trader/state.py), [monitor.py](tv_signal_trader/monitor.py)

A `status.json` file (next to `.env`) works as a monitoring/CRM-style record of what the bot is doing, without needing to watch the console:

- **Run-level**: `app_running`, `tradingview_logged_in`, `tradinggenerator_logged_in`; `loop_state` (`idle`/`trading`/`waiting_for_session`/`waiting_for_portfolio`/`stopped`) with a `stop_reason` once stopped; `session_window` (today's configured start/end and current status); `last_backup_at`; `loop_started_at`; `last_error`/`last_error_at` from the last exception the loop caught.
- **Per-portfolio**, under a `"portfolios"` dict keyed `"Company / Portfolio"` (`status.update_portfolio`/`record_trade_result`/`mark_portfolio_removed`/`mark_portfolio_unavailable`/`mark_portfolio_available`): latest `balance`/`tier_size`/`tier_range`; `last_trade` plus lifetime `trades_total`/`wins_total`/`losses_total`/`not_taken_total` counters (not reset daily — TradingGenerator's own on-page counters are the source of truth for "today"); `active: false` with `removed_at`/`removed_reason` once pulled from TradingGenerator; `unavailable_reason`/`unavailable_since` while sitting in the loop's skip-set (locked, no account configured, connect failed, etc.), cleared automatically once it trades successfully again.

Refreshed on startup/shutdown, whenever `web` touches something worth recording, and by a background `LoginMonitor` thread that runs on a randomized cadence (`config.HEARTBEAT_POLL_RANGE`, see "Randomized timing" below) and does double duty:

- Re-checks TradingView's login cookie (via the `Network.getAllCookies` CDP command, so it doesn't need to switch tabs and visibly hijack the browser). TradingGenerator's login status can only be determined by reading its tab's DOM, which *would* require disruptively switching to it, so that one's only updated on-demand whenever `web` actually uses it.
- Checks whether the TradingView window specifically is still open (`browser.is_alive(driver, tv_tab)`) — not "any window at all", since TradingGenerator now runs as a permanently-open hidden window (see section 4 above) that the user can't close by hand, so "any window" could never go false just because they closed the one they can actually see. If the TradingView window is gone, this marks `status.json` stopped, force-kills the browser (`browser.force_kill` — see "Stopping it" below), and hard-exits the whole process (`os._exit(0)` — a plain `sys.exit()` from this background thread wouldn't reach past the main thread's blocked `input()` call at the `>` prompt) instead of leaving the CLI sitting uselessly forever.

### 7. Randomized timing

Every repeated/idle wait in the codebase — the background `LoginMonitor` heartbeat, waiting for an open position to close, and waiting for a locked/unavailable rotation to free up — draws a fresh random duration from a range each time, rather than sleeping a fixed interval. This is deliberate: ticking at a perfectly regular cadence for hours or days is exactly the kind of timing signature that makes automated usage easier to spot, so these are jittered instead. The ranges are hardcoded in `config.py` (`HEARTBEAT_POLL_RANGE`, `POSITION_POLL_RANGE`, `PORTFOLIO_RETRY_RANGE`) rather than `.env`-configurable, since they exist purely for this reason rather than being something to tune per deployment. Short, bounded UI-confirmation loops (waiting a few seconds for a tab or connection to confirm) are left alone — they're brief, active-action ticks, not long-running idle polling.

### 8. Timestamped console output

Every `print()` in the app (except the interactive first-run setup wizard, which is a one-time prompt flow rather than an ongoing log) is prefixed with a `[HH:MM:SS]` timestamp (local machine time), via [tv_signal_trader/logging_utils.py](tv_signal_trader/logging_utils.py) — each module imports `timestamped_print` in place of the built-in `print`, so no individual print call needed to change. Useful for a long-running unattended session's console/log capture, where otherwise there's no way to tell when something happened without cross-referencing the system clock.

### 9. Interactive command loop

Running the script drops you into a `>` prompt that accepts:

| Command      | Effect                                                              |
|--------------|----------------------------------------------------------------------|
| `web`        | Runs the automatic trading loop, one position at a time (Ctrl+C to stop) |
| `web_multi`  | Runs the same loop, allowing several concurrent positions per company (see above) |
| `buy`        | Places a manual buy with 150-tick TP/SL                              |
| `sell`       | Places a manual sell with 150-tick TP/SL                             |
| `setup`      | Re-run setup to change your TradingGenerator credentials or Tradovate accounts |
| `quit`       | Closes the browser and exits                                         |

## How to run it

1. Install Python 3.9+ and Chrome.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the script:

   ```bash
   python main.py
   ```

4. On first run you'll be walked through setup in the terminal for anything missing: your TradingGenerator username/password, then at least one Tradovate account (pick a prop firm from a fixed list, enter its username/password — one account per firm). This gets saved to a local `.env` file so you're only asked once — type `setup` at the `>` prompt anytime to add more accounts or change existing values. (No chromedriver download needed — Selenium Manager fetches it automatically the first time Chrome launches.)
5. Chrome then opens to the chart — log into TradingView (and make sure the intended Tradovate/broker connection is active) in that window. The session persists in the `tv_profile` folder for future runs; TradingView itself isn't part of the `.env`/setup flow since it relies on that persistent cookie-based session, not password auto-fill.
6. Type a command at the `>` prompt (see table above).

### Stopping it — Ctrl+C, and the `stop.py` backstop

Ctrl+C reliably closes everything, including TradingGenerator's hidden window (which can't be closed by hand — see "The automatic trading loop" above): `cli.py` installs a `signal.signal(signal.SIGINT, ...)` handler that force-kills the whole browser and hard-exits immediately, rather than the Python default of raising `KeyboardInterrupt` wherever the program happens to be and hoping that unwinds cleanly through however many nested calls sit between there and cleanup. It works the same whether you're sitting at the `>` prompt or deep inside a running `web`/`web_multi` loop.

Force-killing (`browser.force_kill`) works by finding every process — any name — whose command line references this bot's `tv_profile` browser profile directory, and killing them directly via the OS, rather than asking nicely via `driver.quit()` (which talks to the browser over a session that can already be broken, and can hang instead of failing fast).

For the rare, more pathological case — Python itself is completely wedged and can't run *any* code, not even a signal handler — there's a backstop that doesn't depend on the main program being responsive at all. Run this from a *separate* terminal window:

```bash
python stop.py
```

It uses the same profile-directory process lookup as `force_kill`, plus stops the bot's own `main.py`/`tv-signal-trader.exe` process, purely via the OS — independent of whatever state the main program is in.

## Building a standalone .exe

For sharing this with a few trusted people without handing them the source, [Nuitka](https://nuitka.net/) compiles the whole app (Python → C → machine code) into a single `tv-signal-trader.exe`. This is obfuscation, not real security — treat it as raising the bar for casual inspection, not as a place to store secrets. No credentials are ever compiled in: `.env` and `status.json` are read from/written next to wherever the `.exe` itself lives at runtime ([tv_signal_trader/config.py](tv_signal_trader/config.py) resolves this via `sys.argv[0]`, not `__file__` — `--onefile` self-extracts to a new temp directory on every launch, so anything anchored to `__file__` would silently reset each run).

1. `pip install -r requirements-build.txt`
2. Run [build.ps1](build.ps1) (or the `nuitka` command inside it directly). Output is `dist/tv-signal-trader.exe`. Includes `--include-package-data=tzdata`: the trading-session check needs Israel's IANA timezone data bundled in, since Windows has no system tz database and Nuitka doesn't pick up a pure-data package's files automatically.
3. Hand the recipient just that one `.exe` — they'll get the same first-run setup wizard prompting for their own TradingGenerator credentials, and Selenium Manager still fetches chromedriver on their machine automatically.
4. If Nuitka builds with MSVC (`cl.exe`) rather than MinGW64, it can't statically link the Windows C runtime, so recipients without it already installed will need the [Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe) — a small, extremely common one-time install.
5. Don't commit `dist/` or the `.exe` into git — publish built binaries as [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github) assets instead, so the repo itself doesn't accumulate large binary blobs. Attach a copy of [docs/.env](docs/.env) alongside it (already a committed template with credentials blanked out, listing every configurable field) so recipients who skip the setup wizard's prompts still see what keys exist — see [v0.2.0](https://github.com/YoniP31/tv-signal-trader/releases/tag/v0.2.0) for an earlier example.

## Known limitations / things to watch out for

- **UI-automation is brittle**: element lookup relies on a mix of stable IDs/`data-qa-id` attributes (where TradingView provides them) and screen coordinates/label text matching (where it doesn't). Any TradingView layout change, browser zoom level, or window-size change can break the latter.
- **The "Ticks" bracket-mode selection is positional**: since the TP/SL dropdown's menu items don't expose a stable per-option identifier, "Ticks" is selected by assuming it's always the 2nd item in the menu — if TradingView ever reorders that menu, this breaks silently.
- **Two conditions still stop the loop outright rather than retrying** (both `web` and `web_multi`, since they share the same engine): TradingGenerator login failure, and the generate button disappearing entirely — both are structural problems needing a human to look, not something to guess past. A trade closing without either its TP or SL leg actually filling (e.g. closed manually, or liquidated) no longer stops the loop at all: that portfolio is quarantined until the next session instead (see [docs/WEB_MULTI.md](docs/WEB_MULTI.md)). Everything else — locked/cooldown/misconfigured portfolios, missing trade parameters, a failed or rejected order, being outside the trading session — is retried, rotated past, quarantined, or waited out instead of raised. Check `status.json`'s `stop_reason`/`last_error` after an unexpected stop.
- **Places real orders**: `place_order()` clicks the live Buy/Sell confirm button. Test against a paper/demo Tradovate connection before pointing this at a live account.

## Repository docs

- [docs/GIT_SETUP.md](docs/GIT_SETUP.md) — how to get set up with Git and the day-to-day commands (status/add/commit/push/pull) for contributing to this repo.
- [docs/WEB_MULTI.md](docs/WEB_MULTI.md) — what the shared `web`/`web_multi` trading loop does and how to test each of its behaviors.
