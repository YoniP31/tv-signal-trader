from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import humanize
from . import panel


def place_order(driver, tp_dollars=2000, sl_dollars=2000, side="buy", units=1):
    # 0. Select Buy/Sell side — body.click() + Shift+S/B
    print(f"\n[0] Selecting {side.upper()} side...")
    humanize.pause(0.4, 0.8)
    body = driver.find_element(By.TAG_NAME, "body")
    body.click()
    humanize.pause(0.3, 0.6)
    if side == "sell":
        body.send_keys(Keys.SHIFT + 's')
    else:
        body.send_keys(Keys.SHIFT + 'b')
    humanize.long_pause(1.5, 2.5)
    print(f"  {side.upper()} ✓")

    # 1. Market order
    print("\n[1] Selecting Market order...")
    humanize.pause(0.5, 1.0)
    try:
        for b in driver.find_elements(By.TAG_NAME, "button"):
            if b.text.strip() == "Market" and b.is_displayed():
                humanize.pause(0.3, 0.6)
                driver.execute_script("arguments[0].click();", b)
                humanize.pause(0.6, 1.2)
                print("  Market ✓")
                break
    except Exception:
        pass

    # 2. Units
    print(f"\n[2] Setting Units = {units}...")
    humanize.long_pause(0.5, 1.0)
    inputs = panel.get_panel_inputs(driver)
    if inputs:
        top_inp, top_val, top_rect = inputs[0]
        print(f"  Units: val='{top_val}' y={int(top_rect['y'])}")
        panel.set_field(driver, top_inp, units)
        print(f"  Units = {units} ✓")
    humanize.long_pause(0.5, 1.0)

    # 3. Enable TP/SL
    print("\n[3] Enabling TP/SL toggles...")
    panel.enable_tp_sl_toggles(driver)
    humanize.long_pause(1.0, 2.0)

    # 4. Find TP / SL labels
    print("\n[4] Finding TP / SL label positions...")
    tp_label_y = panel.find_label_y(driver, "take profit")
    sl_label_y = panel.find_label_y(driver, "stop loss")

    if tp_label_y is None or sl_label_y is None:
        print("  ERROR: Could not find TP or SL labels")
        driver.save_screenshot("debug.png")
        return False

    # 5. TP -> price mode + set value
    print("\n[5] Setting TP...")
    if panel.ensure_price_mode(driver, tp_label_y, "TP"):
        humanize.long_pause(0.4, 0.8)
        tp_inp, _ = panel.find_left_input_near_label(driver, tp_label_y)
        if tp_inp:
            panel.set_field(driver, tp_inp, tp_dollars)
            print(f"  TP = {tp_dollars} ✓")
    humanize.long_pause(0.6, 1.2)

    # 6. SL -> price mode + set value
    print("\n[6] Setting SL...")
    if panel.ensure_price_mode(driver, sl_label_y, "SL"):
        humanize.long_pause(0.4, 0.8)
        sl_inp, _ = panel.find_left_input_near_label(driver, sl_label_y)
        if sl_inp:
            panel.set_field(driver, sl_inp, sl_dollars)
            print(f"  SL = {sl_dollars} ✓")
    humanize.long_pause(0.8, 1.5)

    # 7. Click the big Buy/Sell confirm button
    label = "Buy" if side == "buy" else "Sell"
    print(f"\n[7] Clicking {label} button...")
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
                driver.save_screenshot(f"after_{side}.png")
                print(f"  Done! after_{side}.png saved")
                clicked = True
                break
        except Exception:
            pass
    if not clicked:
        print(f"  WARNING: {label} button not found!")
        return False
    return True
