from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import humanize

# The order-ticket panel is docked to the right of the chart; anything to
# the left of this x-coordinate belongs to the chart/toolbar, not the panel.
PANEL_X_MIN = 1050


def set_field(driver, input_el, value):
    driver.execute_script("arguments[0].scrollIntoView(true);", input_el)
    humanize.pause(0.3, 0.6)
    driver.execute_script("arguments[0].click();", input_el)
    humanize.pause(0.2, 0.4)
    input_el.send_keys(Keys.CONTROL + "a")
    humanize.pause(0.1, 0.2)
    input_el.send_keys(Keys.DELETE)
    humanize.pause(0.2, 0.3)
    humanize.type_humanlike(input_el, value)
    humanize.pause(0.2, 0.4)
    input_el.send_keys(Keys.TAB)
    humanize.pause(0.3, 0.6)


def get_panel_inputs(driver):
    result = []
    for inp in driver.find_elements(By.XPATH, "//input"):
        try:
            if not inp.is_displayed():
                continue
            val = inp.get_attribute('value') or ''
            rect = driver.execute_script(
                "var r=arguments[0].getBoundingClientRect();"
                "return {x:r.x,y:r.y,w:r.width,h:r.height};", inp)
            if rect['x'] > PANEL_X_MIN:
                result.append((inp, val, rect))
        except Exception:
            pass
    result.sort(key=lambda x: x[2]['y'])
    return result


def scan_inputs(driver):
    print("\nAll inputs in right panel:")
    for inp, val, rect in get_panel_inputs(driver):
        print(f"  val='{val}'  x={int(rect['x'])}  y={int(rect['y'])}")


def enable_tp_sl_toggles(driver):
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
        humanize.pause(0.6, 1.0)
    except Exception:
        pass


def get_right_indicator_text(driver, label_y, tolerance=80):
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


def click_swap_button_near_label(driver, label_y, tolerance=80):
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


def ensure_price_mode(driver, label_y, label_name):
    for attempt in range(3):
        mode = get_right_indicator_text(driver, label_y)
        print(f"  {label_name} mode: '{mode}' (attempt {attempt+1})")
        if mode == 'price':
            return True
        humanize.pause(0.3, 0.6)
        click_swap_button_near_label(driver, label_y)
        humanize.pause(0.6, 1.0)
    return False


def find_left_input_near_label(driver, label_y, tolerance=80):
    candidates = []
    for inp, val, rect in get_panel_inputs(driver):
        if rect['y'] >= label_y + 5 and rect['y'] <= label_y + tolerance:
            candidates.append((inp, val, rect))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[2]['x'])
    inp, val, rect = candidates[0]
    print(f"    Left input: val='{val}' x={int(rect['x'])} y={int(rect['y'])}")
    return inp, val


def find_label_y(driver, label_text):
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
