from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import config
from . import humanize
from . import panel


def is_login_form_present(driver):
    """True if the current tab is showing a visible password field.

    TradingGenerator (unlike TradingView) actually gates its content behind a
    login form when logged out, so this DOM check is a reliable signal there.
    """
    try:
        return any(
            el.is_displayed()
            for el in driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
        )
    except Exception:
        return False


def ensure_tradinggenerator_login(driver):
    """If a login form is present on the current tab, fill it from .env and submit.

    Relies on the persistent Chrome profile to skip this most of the time —
    only kicks in when there's no valid session cookie yet.
    """
    if not is_login_form_present(driver):
        print("  Already logged in (no login form found) [OK]")
        return True

    if not config.TRADINGGENERATOR_USERNAME or not config.TRADINGGENERATOR_PASSWORD:
        print("  Login form detected but no credentials in .env - log in manually.")
        return False

    print("  Login form detected, filling credentials...")
    password_el = next(
        el for el in driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
        if el.is_displayed()
    )

    username_el = driver.execute_script("""
        var pwd = arguments[0];
        var inputs = document.querySelectorAll('input');
        var candidate = null;
        for (var i = 0; i < inputs.length; i++) {
            if (inputs[i] === pwd) break;
            var type = (inputs[i].type || '').toLowerCase();
            if (type === 'text' || type === 'email' || type === '') {
                candidate = inputs[i];
            }
        }
        return candidate;
    """, password_el)

    if username_el is None:
        print("  Could not find a username field near the password field.")
        return False

    panel.set_field(driver, username_el, config.TRADINGGENERATOR_USERNAME)
    panel.set_field(driver, password_el, config.TRADINGGENERATOR_PASSWORD)

    clicked = False
    for btn in driver.find_elements(By.TAG_NAME, "button"):
        try:
            if btn.is_displayed() and 'sign in' in btn.text.strip().lower():
                btn.click()
                clicked = True
                break
        except Exception:
            pass
    if not clicked:
        password_el.send_keys(Keys.RETURN)

    humanize.long_pause(2, 3)

    if is_login_form_present(driver):
        print("  Login failed - password field still present after submit.")
        return False

    print("  Login succeeded [OK]")
    return True
