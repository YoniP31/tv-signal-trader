from . import browser
from . import config
from . import humanize
from . import monitor
from . import setup_wizard
from . import signal_source
from . import state
from . import status
from . import trading


def main():
    setup_wizard.ensure_configured()

    driver = browser.create_driver()
    status.mark_app_started()
    login_monitor = monitor.LoginMonitor(driver)

    try:
        print("Opening chart...")
        driver.get(config.CHART_URL)
        humanize.long_pause(5, 8)
        print("Chart loaded:", driver.title)

        tv_logged_in = status.check_tradingview_logged_in(driver)
        status.update(tradingview_logged_in=tv_logged_in)
        if tv_logged_in is False:
            print("[WARN] TradingView doesn't look logged in - sign in in the browser window.")

        login_monitor.start()

        print("\nCommands: 'web', 'buy', 'sell', 'connect_tradovate'," \
        " 'disconnect_tradovate', 'setup', 'quit'")
        while True:
            cmd = input("> ").strip().lower()
            with state.session.driver_lock:
                if cmd == "web":
                    signal_source.run_web_loop(driver)
                elif cmd == "buy":
                    trading.place_order(driver, tp_ticks=150, sl_ticks=150, side="buy")
                elif cmd == "sell":
                    trading.place_order(driver, tp_ticks=150, sl_ticks=150, side="sell")
                elif cmd == "connect_tradovate":
                    # Temporary manual-test command for trading.connect_tradovate().
                    # Uses the sole configured account -- see signal_source._pick_tradovate_account.
                    if len(config.TRADOVATE_ACCOUNTS) == 1:
                        account = next(iter(config.TRADOVATE_ACCOUNTS.values()))
                        trading.connect_tradovate(driver, account['username'], account['password'])
                    else:
                        print(f"[FAIL] {len(config.TRADOVATE_ACCOUNTS)} accounts configured, "
                              "need exactly 1 for this test command.")
                elif cmd == "disconnect_tradovate":
                    # Temporary manual-test command for trading.disconnect_tradovate().
                    trading.disconnect_tradovate(driver)
                elif cmd == "setup":
                    setup_wizard.run_setup()
                elif cmd == "quit":
                    break
    finally:
        login_monitor.stop()
        status.mark_app_stopped()
        driver.quit()


if __name__ == "__main__":
    main()
