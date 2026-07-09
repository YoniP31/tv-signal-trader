from . import browser
from . import config
from . import humanize
from . import monitor
from . import panel
from . import signal_source
from . import state
from . import status
from . import trading


def main():
    driver = browser.create_driver()
    status.mark_app_started()
    login_monitor = monitor.LoginMonitor(driver)

    try:
        print("Opening BTC chart...")
        driver.get(config.CHART_URL)
        humanize.long_pause(5, 8)
        print("Chart loaded:", driver.title)
        state.session.tv_tab = driver.current_window_handle

        tv_logged_in = status.check_tradingview_logged_in(driver)
        status.update(tradingview_logged_in=tv_logged_in)
        if tv_logged_in is False:
            print("⚠️  TradingView doesn't look logged in — sign in in the browser window.")

        login_monitor.start()

        print("\nCommands: 'web', 'buy', 'sell', 'scan', 'screenshot', 'quit'")
        while True:
            cmd = input("> ").strip().lower()
            with state.session.driver_lock:
                if cmd == "web":
                    signal_source.trade_from_website(driver)
                elif cmd == "buy":
                    trading.place_order(driver, tp_dollars=2000, sl_dollars=2000, side="buy")
                elif cmd == "sell":
                    trading.place_order(driver, tp_dollars=2000, sl_dollars=2000, side="sell")
                elif cmd == "scan":
                    panel.scan_inputs(driver)
                elif cmd == "screenshot":
                    driver.save_screenshot("current.png")
                    print("Saved: current.png")
                elif cmd == "quit":
                    break
    finally:
        login_monitor.stop()
        status.mark_app_stopped()
        driver.quit()


if __name__ == "__main__":
    main()
