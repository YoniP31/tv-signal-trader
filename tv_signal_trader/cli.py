from . import browser
from . import config
from . import humanize
from . import panel
from . import signal_source
from . import trading


def main():
    driver = browser.create_driver()

    print("Opening BTC chart...")
    driver.get(config.CHART_URL)
    humanize.long_pause(5, 8)
    print("Chart loaded:", driver.title)

    print("\nCommands: 'web', 'buy', 'sell', 'scan', 'screenshot', 'quit'")
    while True:
        cmd = input("> ").strip().lower()
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

    driver.quit()


if __name__ == "__main__":
    main()
