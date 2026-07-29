from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import humanize
from .logging_utils import timestamped_print as print


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


TP_TOGGLE_QA_ID = "order-ticket-take-profit-checkbox-bracket"
SL_TOGGLE_QA_ID = "order-ticket-stop-loss-checkbox-bracket"


def _ensure_toggle_checked(driver, qa_id):
    try:
        toggle = driver.find_element(By.CSS_SELECTOR, f'[data-qa-id="{qa_id}"]')
    except Exception:
        print(f"  {qa_id} not found")
        return False

    if toggle.get_attribute("aria-checked") != "true":
        driver.execute_script("arguments[0].click();", toggle)
        humanize.pause(0.4, 0.8)

    if toggle.get_attribute("aria-checked") != "true":
        print(f"  {qa_id} still not checked after click")
        return False
    return True


def enable_tp_sl_toggles(driver):
    """Turns on the Take Profit / Stop Loss enable switches (identified by
    their own data-qa-id) if they're off, then verifies each ended up
    checked. Returns True only if both are confirmed on."""
    print("  Enabling TP/SL toggles...")
    tp_ok = _ensure_toggle_checked(driver, TP_TOGGLE_QA_ID)
    sl_ok = _ensure_toggle_checked(driver, SL_TOGGLE_QA_ID)
    return tp_ok and sl_ok


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
            ).filter(function(el) {
                var style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                var rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            });
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


def is_order_ticket_open(driver, attempts=3):
    """True if the order ticket panel is actually open, checked via the
    quantity field, which only exists once it's rendered. Retries briefly
    to absorb render timing, not because we expect it to appear late."""
    for _ in range(attempts):
        try:
            if driver.find_element(By.ID, "quantity-field").is_displayed():
                return True
        except Exception:
            pass
        humanize.pause(0.5, 1.0)
    return False


def click_market_tab(driver):
    """Clicks the 'Market' order-type tab. Returns the button element if a
    visible, exact-text 'Market' button was actually found and clicked, or
    None if it wasn't -- the caller decides that's fatal, not this function
    silently doing nothing."""
    for b in driver.find_elements(By.TAG_NAME, "button"):
        try:
            if b.text.strip() == "Market" and b.is_displayed():
                driver.execute_script("arguments[0].click();", b)
                return b
        except Exception:
            continue
    return None


def ensure_exits_expanded(driver):
    """Makes sure the Exits (TP/SL) section is expanded, via the
    'hide-brackets-button' toggle's own aria-expanded attribute -- no text
    matching or position heuristics needed."""
    try:
        button = driver.find_element(By.CSS_SELECTOR, '[data-qa-id="hide-brackets-button"]')
    except Exception:
        print("  Exits section toggle button not found")
        return False

    if button.get_attribute("aria-expanded") == "true":
        return True

    driver.execute_script("arguments[0].click();", button)
    humanize.pause(0.6, 1.0)
    return button.get_attribute("aria-expanded") == "true"
