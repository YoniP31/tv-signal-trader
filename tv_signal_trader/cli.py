from . import browser
from . import config
from . import humanize
from . import monitor
from . import panel
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

        print("\nCommands: 'web', 'buy', 'sell', 'scan', 'screenshot', 'setup', 'quit'")
        while True:
            cmd = input("> ").strip().lower()
            with state.session.driver_lock:
                if cmd == "web":
                    signal_source.trade_from_website(driver)
                elif cmd == "buy":
                    trading.place_order(driver, tp_ticks=150, sl_ticks=150, side="buy")
                elif cmd == "sell":
                    trading.place_order(driver, tp_ticks=150, sl_ticks=150, side="sell")
                elif cmd == "scan":
                    panel.scan_inputs(driver)
                elif cmd == "screenshot":
                    driver.save_screenshot("current.png")
                    print("Saved: current.png")
                elif cmd == "setup":
                    setup_wizard.run_setup()
                    print("  (chromedriver path change takes effect next run)")
                elif cmd == "quit":
                    break
    finally:
        login_monitor.stop()
        status.mark_app_stopped()
        driver.quit()


if __name__ == "__main__":
    main()
