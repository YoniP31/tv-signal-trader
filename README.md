# TradingView Auto-Trader (BTC)

Selenium-based bot that drives the TradingView web UI to place trades (Market order + Take Profit + Stop Loss) on a BTCUSD chart, based on a signal read from an external source. This is an early prototype — the current signal source is a placeholder test site, meant to be swapped for the real signal feed later.

## What the code currently does

The implementation lives in the [tv_signal_trader/](tv_signal_trader/) package, entered via [main.py](main.py):

| File | Responsibility |
|------|----------------|
| [main.py](main.py) | Entry point — `python main.py` |
| [tv_signal_trader/config.py](tv_signal_trader/config.py) | Paths, URLs, and other constants |
| [tv_signal_trader/browser.py](tv_signal_trader/browser.py) | Chrome/chromedriver setup and stealth tweaks |
| [tv_signal_trader/humanize.py](tv_signal_trader/humanize.py) | Randomized pauses and human-like typing |
| [tv_signal_trader/panel.py](tv_signal_trader/panel.py) | Low-level DOM helpers for reading/filling the order-ticket panel |
| [tv_signal_trader/trading.py](tv_signal_trader/trading.py) | `place_order()` — the core trading action |
| [tv_signal_trader/signal_source.py](tv_signal_trader/signal_source.py) | `trade_from_website()` — reads a signal and triggers a trade |
| [tv_signal_trader/cli.py](tv_signal_trader/cli.py) | Interactive command loop |

### 1. Browser setup

On startup ([tv_signal_trader/browser.py](tv_signal_trader/browser.py)):

- Launches Chrome via `chromedriver` with a persistent profile folder (`tv_profile` in the user's home directory, resolved with `os.path.expanduser("~")`), so you only need to log into TradingView once.
- Applies a few anti-bot-detection tweaks (custom user-agent, hides `navigator.webdriver`, fakes `navigator.plugins`/`navigator.languages`) so TradingView is less likely to flag the session as automated.
- Opens `https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSD`.

The `chromedriver` binary is expected at `drivers/chromedriver` (or `drivers/chromedriver.exe` on Windows) — `browser.get_chromedriver_path()` picks the right filename for the current OS automatically via `platform.system()`, so the same code runs unmodified on Windows/macOS/Linux.

### 2. Order placement — `place_order(driver, tp_dollars, sl_dollars, side, units)`

This is the core trading action. It drives TradingView's order panel (the right-hand sidebar) by locating elements via coordinates/DOM text rather than fixed selectors, since TradingView doesn't expose stable IDs for these controls:

1. Clicks the page body and sends TradingView's built-in keyboard shortcut (`Shift+B` for buy, `Shift+S` for sell) to open the order ticket.
2. Clicks the **Market** order-type button.
3. Sets the **Units/Quantity** field (the top-most input in the right panel) to `units`.
4. Turns on the **Take Profit** and **Stop Loss** toggles.
5. Locates the "Take Profit" and "Stop Loss" labels on screen, makes sure each row is displaying in **Price** mode (as opposed to **Ticks** — it clicks a small swap button next to the label if needed), then types `tp_dollars` / `sl_dollars` into the corresponding value field.
6. Clicks the final **Buy**/**Sell** confirm button and saves a screenshot (`after_buy.png` / `after_sell.png`).

All typing is done character-by-character with randomized delays (`type_humanlike`), and most steps have randomized pauses between them, to look more like a human user than a script.

### 3. Reading a signal — `trade_from_website(driver)`

This function currently points at a placeholder test site (`https://white-martynne-45.tiiny.site/`), not a real signal provider:

1. Opens the site in a new browser tab and clicks its "create test trade" button.
2. Scans the page's DOM text for a direction keyword (`LONG` → buy, `SHORT` → sell) and for Take Profit / Stop Loss values (expressed in ticks) and a contract count, using simple text pattern matching.
3. Switches back to the TradingView tab and calls `place_order(...)` with whatever it parsed (falling back to `buy` / 1000-tick TP / 1000-tick SL / 1 contract if parsing fails).

This is clearly a stand-in for hooking up the real trade-signal source — expect this function to be reworked once that source is defined.

### 4. Interactive command loop

Running the script drops you into a `>` prompt that accepts:

| Command      | Effect                                                              |
|--------------|----------------------------------------------------------------------|
| `web`        | Runs `trade_from_website()` — pulls a signal and executes the trade |
| `buy`        | Places a manual buy with $2000 TP/SL                                 |
| `sell`       | Places a manual sell with $2000 TP/SL                                |
| `scan`       | Debug: prints every input field detected in the right-hand panel     |
| `screenshot` | Saves a screenshot to `current.png`                                  |
| `quit`       | Closes the browser and exits                                         |

## How to run it

1. Install Python 3.9+ and Chrome.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Download the `chromedriver` build matching your installed Chrome version and place it in [drivers/](drivers/) — as `drivers/chromedriver` on macOS/Linux, or `drivers/chromedriver.exe` on Windows. The correct filename for your OS is picked automatically.
4. Run the script:

   ```bash
   python main.py
   ```

5. On first run, Chrome opens to the TradingView chart — log into TradingView (and make sure the intended Tradovate/broker connection is active) in that window. The session persists in the `tv_profile` folder for future runs.
6. Type a command at the `>` prompt (see table above).

## Known limitations / things to watch out for

- **UI-automation is brittle**: element lookup relies on screen coordinates (e.g. "anything right of x=1050 is the order panel") and label text matching. Any TradingView layout change, browser zoom level, or window-size change can break it.
- **Minimal error handling**: most failures just print a warning and move on rather than retrying or raising — check the console output after each command.
- **Places real orders**: `place_order()` clicks the live Buy/Sell confirm button. Test against a paper/demo Tradovate connection before pointing this at a live account.
- Some inline comments in the source are mangled/garbled (an encoding issue from how the file was passed along) — cosmetic only, doesn't affect behavior.

## Repository docs

- [GIT_SETUP.md](GIT_SETUP.md) — how to get set up with Git and the day-to-day commands (status/add/commit/push/pull) for contributing to this repo.
