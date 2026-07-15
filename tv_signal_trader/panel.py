from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import humanize


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


def ensure_ticks_mode(driver, button_qa_id, label_name):
    """Makes sure the TP/SL bracket dropdown (identified by `button_qa_id`)
    is set to 'Ticks' rather than 'Price'/'% price'/'Reward'/etc.

    Unlike the quantity-type dropdown, individual options here don't have a
    stable data-qa-id of their own (the button's own data-qa-id changes to
    reflect whichever mode is currently selected, e.g.
    "bracket-input-type-Pips", so it can't be used as a fixed target either).
    Ticks is always the 2nd option (index 1, top to bottom) in the menu.
    """
    try:
        button = driver.find_element(By.CSS_SELECTOR, f'[data-qa-id="{button_qa_id}"]')
    except Exception:
        print(f"  {label_name} dropdown button not found")
        return False

    current = button.text.strip()
    print(f"  {label_name} mode: '{current}'")
    if 'tick' in current.lower():
        return True

    try:
        driver.execute_script("arguments[0].click();", button)
        humanize.pause(0.4, 0.8)
        clicked = driver.execute_script("""
            var items = Array.prototype.slice.call(
                document.querySelectorAll('[data-is-popover-item-button="true"]')
            ).filter(function(el) { return el.offsetParent; });
            items.sort(function(a, b) {
                return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
            });
            if (items.length > 1) { items[1].click(); return true; }
            return false;
        """)
        humanize.pause(0.4, 0.8)
        if not clicked:
            print(f"  Could not find 'Ticks' option in the {label_name} dropdown menu")
        return clicked
    except Exception:
        print(f"  Error selecting Ticks mode for {label_name}")
        return False


def ensure_units_mode(driver):
    """Makes sure the "Quantity type" dropdown is set to 'Units' rather than
    'Contracts'/'Lots'/etc."""
    try:
        container = driver.find_element(By.ID, "quantity-dropdown-types")
    except Exception:
        print("  Quantity type dropdown not found")
        return False

    quantity_type = container.text.strip()
    print(f"  Quantity type: '{quantity_type}'")
    if quantity_type == 'Units':
        return True

    try:
        driver.execute_script("arguments[0].click();", container)
        humanize.pause(0.4, 0.8)
        units_option = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="quantity-type-units"]')
        driver.execute_script("arguments[0].click();", units_option)
        humanize.pause(0.4, 0.8)
        return True
    except Exception:
        print("  Could not find 'Units' option in the dropdown menu")
        return False


def ensure_exits_expanded(driver):
    """Clicks the 'Exits' section header to reveal the TP/SL controls, if
    they aren't already visible."""
    if find_label_y(driver, "take profit", quiet=True) is not None:
        return True
    clicked = driver.execute_script("""
        var all = document.querySelectorAll('button, div, span');
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (!el.offsetParent) continue;
            var rect = el.getBoundingClientRect();
            if (rect.x < 1050) continue;
            if ((el.innerText || '').trim() === 'Exits') { el.click(); return true; }
        }
        return false;
    """)
    humanize.pause(0.6, 1.0)
    return clicked


def find_label_y(driver, label_text, quiet=False):
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
    if not quiet:
        print(f"  '{label_text}' label y={result}")
    return result
