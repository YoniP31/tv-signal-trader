from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import humanize
from . import panel


def place_order(driver, tp_ticks=150, sl_ticks=150, side="buy", units=1):
    # 1. Select Buy/Sell side
    print(f"\n[1] Selecting {side.upper()} side...")
    humanize.pause(0.4, 0.8)
    body = driver.find_element(By.TAG_NAME, "body")
    body.click()
    humanize.pause(0.3, 0.6)
    if side == "sell":
        body.send_keys(Keys.SHIFT + 's')
    else:
        body.send_keys(Keys.SHIFT + 'b')
    humanize.long_pause(1.5, 2.5)
    print(f"  {side.upper()} [OK]")

    # 2. Market order
    print("\n[2] Selecting Market order...")
    humanize.pause(0.5, 1.0)
    try:
        for b in driver.find_elements(By.TAG_NAME, "button"):
            if b.text.strip() == "Market" and b.is_displayed():
                humanize.pause(0.3, 0.6)
                driver.execute_script("arguments[0].click();", b)
                humanize.pause(0.6, 1.2)
                print("  Market [OK]")
                break
    except Exception:
        pass

    # 3. Units (making sure the quantity dropdown says "Units", not
    # "Contracts"/"Lots"/etc., before typing the contract count)
    print(f"\n[3] Setting Units = {units}...")
    humanize.long_pause(0.5, 1.0)
    if not panel.ensure_units_mode(driver):
        print("  WARNING: could not confirm 'Units' mode")
    try:
        quantity_inp = driver.find_element(By.ID, "quantity-field")
        panel.set_field(driver, quantity_inp, units)
        print(f"  Units = {units} [OK]")
    except Exception:
        print("  WARNING: quantity-field not found")
    humanize.long_pause(0.5, 1.0)

    # 4. Reveal the Exits section (TP/SL controls) if it's collapsed
    print("\n[4] Making sure Exits section is expanded...")
    panel.ensure_exits_expanded(driver)
    humanize.long_pause(0.5, 1.0)

    # 5. Enable TP/SL toggles
    print("\n[5] Enabling TP/SL toggles...")
    panel.enable_tp_sl_toggles(driver)
    humanize.long_pause(1.0, 2.0)

    # 6. Switch TP/SL to Ticks mode
    print("\n[6] Switching TP/SL to Ticks mode...")
    panel.ensure_ticks_mode(driver, "order-ticket-take-profit-dropdown-button", "TP")
    panel.ensure_ticks_mode(driver, "order-ticket-stop-loss-dropdown-button", "SL")
    humanize.long_pause(0.4, 0.8)

    # 7. Enter the tick values
    print("\n[7] Setting TP/SL tick values...")
    try:
        tp_inp = driver.find_element(By.CSS_SELECTOR, '[data-qa-id~="order-ticket-take-profit-input"]')
        panel.set_field(driver, tp_inp, tp_ticks)
        print(f"  TP = {tp_ticks} ticks [OK]")
    except Exception:
        print("  WARNING: take-profit input not found")
    humanize.long_pause(0.4, 0.8)

    try:
        sl_inp = driver.find_element(By.CSS_SELECTOR, '[data-qa-id~="order-ticket-stop-loss-input"]')
        panel.set_field(driver, sl_inp, sl_ticks)
        print(f"  SL = {sl_ticks} ticks [OK]")
    except Exception:
        print("  WARNING: stop-loss input not found")
    humanize.long_pause(0.6, 1.2)

    # 8. Click the big Buy/Sell confirm button
    label = "Buy" if side == "buy" else "Sell"
    print(f"\n[8] Clicking {label} button...")
    humanize.long_pause(0.5, 1.0)
    clicked = False
    for b in driver.find_elements(By.TAG_NAME, "button"):
        try:
            if not b.is_displayed():
                continue
            if b.text.strip().startswith(label) and b.size['width'] > 150:
                humanize.pause(0.3, 0.7)
                driver.execute_script("arguments[0].click();", b)
                humanize.long_pause(1.5, 3.0)
                print("  Done!")
                clicked = True
                break
        except Exception:
            pass
    if not clicked:
        print(f"  WARNING: {label} button not found!")
        return False
    return True
