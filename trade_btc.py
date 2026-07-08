from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
import pyautogui
import time
import random
import os

pyautogui.FAILSAFE = False

# ── Human-like timing ─────────────────────────────────────────────────────────

def pause(min_s=0.4, max_s=0.9):
    time.sleep(random.uniform(min_s, max_s))

def long_pause(min_s=1.0, max_s=2.5):
    time.sleep(random.uniform(min_s, max_s))

def type_humanlike(element, text):
    for char in str(text):
        element.send_keys(char)
        time.sleep(random.uniform(0.08, 0.22))

# ── Chrome stealth ────────────────────────────────────────────────────────────

profile_dir = os.path.join(os.environ.get('USERPROFILE', 'C:\\Users\\Administrator'), 'tv_profile')

options = Options()
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1920,1080")
options.add_argument("--user-data-dir=" + profile_dir)
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)
options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

driver_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chromedriver.exe')
service = Service(driver_path)
driver = webdriver.Chrome(service=service, options=options)

driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
    "source": """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins',   { get: () => [1,2,3,4,5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
    """
})

print("Opening BTC chart...")
driver.get("https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSD")
long_pause(5, 8)
print("Chart loaded:", driver.title)


# ── Helpers ───────────────────────────────────────────────────────────────────

def set_field(input_el, value):
    driver.execute_script("arguments[0].scrollIntoView(true);", input_el)
    pause(0.3, 0.6)
    driver.execute_script("arguments[0].click();", input_el)
    pause(0.2, 0.4)
    input_el.send_keys(Keys.CONTROL + "a")
    pause(0.1, 0.2)
    input_el.send_keys(Keys.DELETE)
    pause(0.2, 0.3)
    type_humanlike(input_el, value)
    pause(0.2, 0.4)
    input_el.send_keys(Keys.TAB)
    pause(0.3, 0.6)


def get_panel_inputs():
    result = []
    for inp in driver.find_elements(By.XPATH, "//input"):
        try:
            if not inp.is_displayed():
                continue
            val = inp.get_attribute('value') or ''
            rect = driver.execute_script(
                "var r=arguments[0].getBoundingClientRect();"
                "return {x:r.x,y:r.y,w:r.width,h:r.height};", inp)
            if rect['x'] > 1050:
                result.append((inp, val, rect))
        except:
            pass
    result.sort(key=lambda x: x[2]['y'])
    return result


def scan_inputs():
    print("\nAll inputs in right panel:")
    for inp, val, rect in get_panel_inputs():
        print(f"  val='{val}'  x={int(rect['x'])}  y={int(rect['y'])}")


def enable_tp_sl_toggles():
    print("  Enabling TP/SL toggles...")
    try:
        driver.execute_script("""
            var toggles = document.querySelectorAll('[role="switch"]');
            for (var i = 0; i < toggles.length; i++) {
                var t = toggles[i];
                if (!t.offsetParent) continue;
                var rect = t.getBoundingClientRect();
                if (rect.x < 1050) continue;
                if (t.getAttribute('aria-checked') === 'false') t.click();
            }
        """)
        pause(0.6, 1.0)
    except:
        pass


def get_right_indicator_text(label_y, tolerance=80):
    result = driver.execute_script("""
        var labelY = arguments[0], tol = arguments[1];
        var all = document.querySelectorAll('button, span, div');
        var best = null, bestX = 0;
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (!el.offsetParent) continue;
            var rect = el.getBoundingClientRect();
            if (rect.x < 1050) continue;
            if (rect.y < labelY + 5)  continue;
            if (rect.y > labelY + tol) continue;
            var txt = (el.innerText || el.textContent || '').trim().toLowerCase();
            if ((txt === 'price' || txt === 'ticks') && rect.x > bestX) {
                best  = txt;
                bestX = rect.x;
            }
        }
        return best;
    """, label_y, tolerance)
    return result


def click_swap_button_near_label(label_y, tolerance=80):
    result = driver.execute_script("""
        var labelY = arguments[0], tol = arguments[1];
        var allBtns = document.querySelectorAll('button');
        var best = null;
        for (var i = 0; i < allBtns.length; i++) {
            var btn  = allBtns[i];
            if (!btn.offsetParent) continue;
            var rect = btn.getBoundingClientRect();
            if (rect.x < 1050) continue;
            if (rect.width < 5 || rect.width > 45) continue;
            if (rect.y < labelY + 5)  continue;
            if (rect.y > labelY + tol) continue;
            if (best === null || rect.y < best.rect.y) {
                best = {btn: btn, rect: rect};
            }
        }
        if (best) {
            best.btn.click();
            return 'clicked';
        }
        return 'not_found';
    """, label_y, tolerance)
    return 'clicked' in result


def ensure_price_mode(label_y, label_name):
    for attempt in range(3):
        mode = get_right_indicator_text(label_y)
        print(f"  {label_name} mode: '{mode}' (attempt {attempt+1})")
        if mode == 'price':
            return True
        pause(0.3, 0.6)
        click_swap_button_near_label(label_y)
        pause(0.6, 1.0)
    return False


def find_left_input_near_label(label_y, tolerance=80):
    candidates = []
    for inp, val, rect in get_panel_inputs():
        if rect['y'] >= label_y + 5 and rect['y'] <= label_y + tolerance:
            candidates.append((inp, val, rect))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[2]['x'])
    inp, val, rect = candidates[0]
    print(f"    Left input: val='{val}' x={int(rect['x'])} y={int(rect['y'])}")
    return inp, val


def find_label_y(label_text):
    result = driver.execute_script("""
        var target = arguments[0].toLowerCase();
        var all = document.querySelectorAll('span, div, label');
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (!el.offsetParent) continue;
            var rect = el.getBoundingClientRect();
            if (rect.x < 1050) continue;
            var txt = (el.innerText || el.textContent || '').trim().toLowerCase();
            if (txt.startsWith(target)) return rect.y;
        }
        return null;
    """, label_text)
    print(f"  '{label_text}' label y={result}")
    return result


# ── Main order function ───────────────────────────────────────────────────────

def place_order(tp_dollars=2000, sl_dollars=2000, side="buy", units=1):

    # 0. החלף ל-Buy/Sell — body.click() + Shift+S/B
    print(f"\n[0] Selecting {side.upper()} side...")
    pause(0.4, 0.8)
    body = driver.find_element(By.TAG_NAME, "body")
    body.click()
    pause(0.3, 0.6)
    if side == "sell":
        body.send_keys(Keys.SHIFT + 's')
    else:
        body.send_keys(Keys.SHIFT + 'b')
    pause(1.5, 2.5)
    print(f"  {side.upper()} ✓")

    # 1. Market order
    print("\n[1] Selecting Market order...")
    pause(0.5, 1.0)
    try:
        for b in driver.find_elements(By.TAG_NAME, "button"):
            if b.text.strip() == "Market" and b.is_displayed():
                pause(0.3, 0.6)
                driver.execute_script("arguments[0].click();", b)
                pause(0.6, 1.2)
                print("  Market ✓")
                break
    except:
        pass

    # 2. Units = 1
    print("\n[2] Setting Units = 1...")
    long_pause(0.5, 1.0)
    inputs = get_panel_inputs()
    if inputs:
        top_inp, top_val, top_rect = inputs[0]
        print(f"  Units: val='{top_val}' y={int(top_rect['y'])}")
        set_field(top_inp, units)
        print(f"  Units = {units} ✓")
    long_pause(0.5, 1.0)

    # 3. Enable TP/SL
    print("\n[3] Enabling TP/SL toggles...")
    enable_tp_sl_toggles()
    long_pause(1.0, 2.0)

    # 4. Find TP / SL labels
    print("\n[4] Finding TP / SL label positions...")
    tp_label_y = find_label_y("take profit")
    sl_label_y = find_label_y("stop loss")

    if tp_label_y is None or sl_label_y is None:
        print("  ERROR: Could not find TP or SL labels")
        driver.save_screenshot("debug.png")
        return

    # 5. TP → price mode + set value
    print("\n[5] Setting TP...")
    if ensure_price_mode(tp_label_y, "TP"):
        long_pause(0.4, 0.8)
        tp_inp, _ = find_left_input_near_label(tp_label_y)
        if tp_inp:
            set_field(tp_inp, tp_dollars)
            print(f"  TP = {tp_dollars} ✓")
    long_pause(0.6, 1.2)

    # 6. SL → price mode + set value
    print("\n[6] Setting SL...")
    if ensure_price_mode(sl_label_y, "SL"):
        long_pause(0.4, 0.8)
        sl_inp, _ = find_left_input_near_label(sl_label_y)
        if sl_inp:
            set_field(sl_inp, sl_dollars)
            print(f"  SL = {sl_dollars} ✓")
    long_pause(0.8, 1.5)

    # 7. לחץ על כפתור Buy/Sell הגדול
    label = "Buy" if side == "buy" else "Sell"
    print(f"\n[7] Clicking {label} button...")
    long_pause(0.5, 1.0)
    clicked = False
    for b in driver.find_elements(By.TAG_NAME, "button"):
        try:
            if not b.is_displayed():
                continue
            if b.text.strip().startswith(label) and b.size['width'] > 150:
                pause(0.3, 0.7)
                driver.execute_script("arguments[0].click();", b)
                long_pause(1.5, 3.0)
                driver.save_screenshot(f"after_{side}.png")
                print(f"  Done! after_{side}.png saved")
                clicked = True
                break
        except:
            pass
    if not clicked:
        print(f"  WARNING: {label} button not found!")
        return False
    return True


def trade_from_website():
    """פותח את האתר בטאב חדש, לוחץ על צור עסקה, קורא נתונים ומבצע עסקה"""
    print("\n[WEB] Opening website...")
    tv_tab = driver.current_window_handle
    try:
        # פתח טאב חדש
        driver.execute_script("window.open('https://white-martynne-45.tiiny.site/', '_blank');")
        long_pause(3, 5)

        # עבור לטאב החדש
        all_tabs = driver.window_handles
        web_tab = all_tabs[-1]
        driver.switch_to.window(web_tab)
        print("  Website tab opened ✓")
        long_pause(2, 3)

        # לחץ על "צור עסקה חדשה"
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

        long_pause(2, 3)

        # קרא נתונים מהדף
        print("  Reading trade data...")
        page_text = driver.find_element(By.TAG_NAME, "body").text
        print("  Page preview: " + page_text[:300].replace("\n", " | "))

        # פרס נתונים ישירות מה-DOM
        import re
        data = {'direction': None, 'tp': None, 'sl': None, 'contracts': 1}

        # קרא כל אלמנט בנפרד לפי טקסט
        all_elements = driver.find_elements(By.XPATH, "//*[not(self::script) and not(self::style)]")
        texts = []
        for el in all_elements:
            try:
                t = el.text.strip()
                if t and len(t) < 50:  # רק טקסטים קצרים
                    texts.append(t)
            except:
                pass

        print("  Short elements found:")
        for t in texts:
            if any(w in t for w in ['LONG','SHORT','טיקים','חוזים','TAKE','STOP']):
                print(f"    '{t}'")

        # כיוון
        for t in texts:
            if t == 'LONG':  data['direction'] = 'buy';  break
            if t == 'SHORT': data['direction'] = 'sell'; break

        # TP וSL — מחפש טקסט כמו "69 טיקים" או "83 טיקים"
        tp_found = False
        sl_found = False
        for i, t in enumerate(texts):
            if 'TAKE PROFIT' in t.upper():
                # הערך בשורה הבאה
                for j in range(i+1, min(i+5, len(texts))):
                    m = re.match(r'^(\d+)\s*טיקים', texts[j])
                    if m:
                        data['tp'] = int(m.group(1))
                        tp_found = True
                        break
            if 'STOP LOSS' in t.upper():
                for j in range(i+1, min(i+5, len(texts))):
                    m = re.match(r'^(\d+)\s*טיקים', texts[j])
                    if m:
                        data['sl'] = int(m.group(1))
                        sl_found = True
                        break

        # חוזים — מחפש "חוזים" ואז מספר
        for i, t in enumerate(texts):
            if 'חוזים' in t or 'חוזה' in t:
                # המספר יכול להיות בטקסט עצמו או בשורה הבאה
                nums = re.findall(r'\d+', t)
                if nums:
                    data['contracts'] = int(nums[0])
                elif i+1 < len(texts):
                    nums = re.findall(r'\d+', texts[i+1])
                    if nums:
                        data['contracts'] = int(nums[0])
                break

        print(f"  Direction: {data['direction']}")
        print(f"  TP:        {data['tp']} ticks")
        print(f"  SL:        {data['sl']} ticks")
        print(f"  Contracts: {data['contracts']}")

        # עבור חזרה ל-TradingView — השאר את טאב האתר פתוח
        driver.switch_to.window(tv_tab)
        print("  Back to TradingView ✓")
        long_pause(1, 2)

        # בצע עסקה
        direction = data['direction'] or 'buy'
        tp        = int(data['tp']        or 1000)
        sl        = int(data['sl']        or 1000)
        contracts = int(data['contracts'] or 1)

        print(f"\n  Executing: {direction.upper()} | TP={tp} | SL={sl} | contracts={contracts}")
        result = place_order(tp_dollars=tp, sl_dollars=sl, side=direction, units=contracts)
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
        except:
            pass


# ── Main loop ─────────────────────────────────────────────────────────────────
print("\nCommands: 'web', 'buy', 'sell', 'scan', 'screenshot', 'quit'")
while True:
    cmd = input("> ").strip().lower()
    if cmd == "web":
        trade_from_website()
    elif cmd == "buy":
        place_order(tp_dollars=2000, sl_dollars=2000, side="buy")
    elif cmd == "sell":
        place_order(tp_dollars=2000, sl_dollars=2000, side="sell")
    elif cmd == "scan":
        scan_inputs()
    elif cmd == "screenshot":
        driver.save_screenshot("current.png")
        print("Saved: current.png")
    elif cmd == "quit":
        break

driver.quit()
