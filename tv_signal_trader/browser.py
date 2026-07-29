import ctypes
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from . import config


def build_options():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--user-data-dir=" + config.PROFILE_DIR)
    options.add_argument("--log-level=3")
    options.add_argument("--disable-background-networking")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    # TradingGenerator's "Save Backup" button downloads a .json file -- if
    # Chrome's set to ask where to save each download, that'd be a native
    # OS dialog Selenium can't see or dismiss, silently stalling the
    # backup. This forces the profile to always download automatically,
    # landing next to .env/status.json rather than wherever Chrome's
    # default download folder happens to be.
    options.add_experimental_option("prefs", {
        "download.prompt_for_download": False,
        "download.default_directory": config.APP_DIR,
        "download.directory_upgrade": True,
    })
    options.add_argument("user-agent=" + config.USER_AGENT)
    return options


def create_driver():
    # No explicit Service/executable path: Selenium Manager (built into
    # Selenium 4.6+) detects the installed Chrome version and downloads a
    # matching chromedriver automatically, caching it for later runs.
    driver = webdriver.Chrome(options=build_options())
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',   { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
        """
    })
    return driver


def is_alive(driver):
    """False once the browser itself is gone (e.g. the user closed every
    window) -- at that point chromedriver's underlying session is dead too,
    so any call to the driver raises rather than returning an empty list."""
    try:
        return bool(driver.window_handles)
    except Exception:
        return False


def hide_window_by_title(title_substring, timeout=10):
    """Windows only: finds the top-level OS window whose title contains
    `title_substring` and hides it via the Win32 API (SW_HIDE) -- removed
    from the taskbar and Alt-Tab entirely, not just minimized, so it's not
    reachable through normal user interaction.

    This only works on a window that's genuinely separate at the OS level
    (see tradinggenerator.open_tab, which opens it with window features
    rather than as a tab of the main browser window) -- a tab has no hwnd
    of its own to hide independently of its parent window.

    Hiding the window doesn't affect Selenium's ability to keep driving it:
    WebDriver commands talk to the browser over the DevTools protocol (DOM/
    JS-level), not via OS-level mouse/keyboard input or window focus, so a
    hidden window keeps responding to .click()/execute_script() normally.

    Polls for up to `timeout` seconds since the window may not have set its
    real title yet right after creation. Returns True if found and hidden,
    False otherwise (e.g. not on Windows, or the window never appeared).
    """
    if sys.platform != "win32":
        return False

    user32 = ctypes.windll.user32
    SW_HIDE = 0

    def _find_hwnd():
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _callback(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_substring.lower() in buf.value.lower():
                    found.append(hwnd)
            return True

        user32.EnumWindows(_callback, 0)
        return found[0] if found else None

    elapsed = 0.0
    while elapsed < timeout:
        hwnd = _find_hwnd()
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)
            return True
        time.sleep(0.5)
        elapsed += 0.5
    return False
