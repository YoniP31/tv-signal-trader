import re

from selenium.webdriver.common.by import By

from . import config
from . import humanize
from . import trading


def trade_from_website(driver):
    """Opens the placeholder signal site in a new tab, reads the signal, and executes the trade."""
    print("\n[WEB] Opening website...")
    tv_tab = driver.current_window_handle
    try:
        driver.execute_script(f"window.open('{config.SIGNAL_SITE_URL}', '_blank');")
        humanize.long_pause(3, 5)

        all_tabs = driver.window_handles
        web_tab = all_tabs[-1]
        driver.switch_to.window(web_tab)
        print("  Website tab opened ✓")
        humanize.long_pause(2, 3)

        print("  Looking for button...")
        clicked = False
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if 'צור' in btn.text:
                btn.click()
                clicked = True
                print(f"  Clicked: '{btn.text.strip()}' ✓")
                break
        if not clicked:
            print("  Button not found!")
            driver.switch_to.window(tv_tab)
            return

        humanize.long_pause(2, 3)

        print("  Reading trade data...")
        page_text = driver.find_element(By.TAG_NAME, "body").text
        print("  Page preview: " + page_text[:300].replace("\n", " | "))

        data = {'direction': None, 'tp': None, 'sl': None, 'contracts': 1}

        all_elements = driver.find_elements(By.XPATH, "//*[not(self::script) and not(self::style)]")
        texts = []
        for el in all_elements:
            try:
                t = el.text.strip()
                if t and len(t) < 50:
                    texts.append(t)
            except Exception:
                pass

        print("  Short elements found:")
        for t in texts:
            if any(w in t for w in ['LONG', 'SHORT', 'טיקים', 'חוזים', 'TAKE', 'STOP']):
                print(f"    '{t}'")

        for t in texts:
            if t == 'LONG':
                data['direction'] = 'buy'
                break
            if t == 'SHORT':
                data['direction'] = 'sell'
                break

        for i, t in enumerate(texts):
            if 'TAKE PROFIT' in t.upper():
                for j in range(i + 1, min(i + 5, len(texts))):
                    m = re.match(r'^(\d+)\s*טיקים', texts[j])
                    if m:
                        data['tp'] = int(m.group(1))
                        break
            if 'STOP LOSS' in t.upper():
                for j in range(i + 1, min(i + 5, len(texts))):
                    m = re.match(r'^(\d+)\s*טיקים', texts[j])
                    if m:
                        data['sl'] = int(m.group(1))
                        break

        for i, t in enumerate(texts):
            if 'חוזים' in t or 'חוזה' in t:
                nums = re.findall(r'\d+', t)
                if nums:
                    data['contracts'] = int(nums[0])
                elif i + 1 < len(texts):
                    nums = re.findall(r'\d+', texts[i + 1])
                    if nums:
                        data['contracts'] = int(nums[0])
                break

        print(f"  Direction: {data['direction']}")
        print(f"  TP:        {data['tp']} ticks")
        print(f"  SL:        {data['sl']} ticks")
        print(f"  Contracts: {data['contracts']}")

        driver.switch_to.window(tv_tab)
        print("  Back to TradingView ✓")
        humanize.long_pause(1, 2)

        direction = data['direction'] or 'buy'
        tp = int(data['tp'] or 1000)
        sl = int(data['sl'] or 1000)
        contracts = int(data['contracts'] or 1)

        print(f"\n  Executing: {direction.upper()} | TP={tp} | SL={sl} | contracts={contracts}")
        result = trading.place_order(driver, tp_dollars=tp, sl_dollars=sl, side=direction, units=contracts)
        if result:
            print("\n✅ Trade executed!")
        else:
            print("\n❌ Trade FAILED — button not found!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        try:
            driver.switch_to.window(tv_tab)
        except Exception:
            pass
