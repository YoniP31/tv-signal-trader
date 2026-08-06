import ctypes
import subprocess
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from . import config
from .logging_utils import timestamped_print as print


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


def is_alive(driver, tab_handle=None):
    """False once the browser's gone. If `tab_handle` is given, checks
    specifically whether *that* window/tab is still open, rather than
    "any window at all" -- necessary now that TradingGenerator runs as a
    permanently-open hidden window (see tradinggenerator.open_tab):
    without a specific handle to check, this could never go False just
    because the user closed the one window they can actually see
    (TradingView), since the hidden one is still there. If the whole
    session is dead, any call to the driver raises rather than returning
    an empty list, so that's treated as "not alive" too either way."""
    try:
        if tab_handle is not None:
            return tab_handle in driver.window_handles
        return bool(driver.window_handles)
    except Exception:
        return False


def force_kill(driver=None):
    """Immediately kills every Chrome/chromedriver process tied to this
    bot's browser profile (all windows, hidden or not) via the OS, rather
    than driver.quit()'s own WebDriver/CDP-based shutdown.

    Deliberately does NOT target chromedriver's own PID as the root of a
    process-tree kill (an earlier version did, and it was observed to
    fail): chromedriver shares this Python process's console by default,
    so a Ctrl+C is broadcast to it directly too, and it can exit on its
    own before this even runs -- at which point a PID-targeted kill finds
    nothing ("process not found") and leaves chrome.exe, now parentless,
    running untouched. Instead this finds every process (any name) whose
    command line references the profile directory -- same technique as
    stop.py, which doesn't depend on any single process still being alive
    -- so it doesn't matter which one already died or is still running.

    `driver` is accepted but unused (kept so existing call sites don't need
    to change); the profile directory alone is enough to find everything.

    Safe to call even if every process is already gone. Reports its own
    outcome (rather than swallowing failures silently) so a failure to
    actually close the browser is visible instead of a silent no-op.
    """
    script = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*{config.PROFILE_DIR}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, timeout=15,
        )
        print(f"  Force-killed every Chrome process under {config.PROFILE_DIR} [OK]")
    except Exception as e:
        print(f"  [WARN] force_kill: sweeping/killing profile processes failed: {e}")


def _enum_hwnds():
    """Windows only: every current top-level window's hwnd, regardless of
    title or visibility -- shared by hide_window_by_title (which filters by
    title afterward) and snapshot_hwnds/hide_new_window (which don't care
    about title at all, only "is this hwnd new"). Empty list on non-Windows."""
    if sys.platform != "win32":
        return []

    user32 = ctypes.windll.user32
    hwnds = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _lparam):
        hwnds.append(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    return hwnds


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
    real title yet right after creation -- prefer hide_new_window instead
    when possible (see there): waiting for the real title means the window
    is visible on screen for however long that takes, which this exists
    purely as a fallback for. Returns True if found and hidden, False
    otherwise (e.g. not on Windows, or the window never appeared).
    """
    if sys.platform != "win32":
        return False

    user32 = ctypes.windll.user32
    SW_HIDE = 0

    def _find_hwnd():
        for hwnd in _enum_hwnds():
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_substring.lower() in buf.value.lower():
                    return hwnd
        return None

    elapsed = 0.0
    while elapsed < timeout:
        hwnd = _find_hwnd()
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)
            return True
        time.sleep(0.5)
        elapsed += 0.5
    return False


def snapshot_hwnds():
    """Windows only: every current top-level window's hwnd -- call this
    right *before* triggering a new window's creation (e.g. window.open),
    then pass the result to hide_new_window to catch whichever hwnd shows
    up that wasn't here yet. Empty list on non-Windows."""
    return _enum_hwnds()


def hide_new_window(before_hwnds, timeout=5, poll_interval=0.05):
    """Windows only: hides whichever top-level window appears that wasn't
    in `before_hwnds` (see snapshot_hwnds). Polls much faster than
    hide_window_by_title and doesn't wait for a real title at all -- it
    catches the OS-level window object as soon as it exists, typically
    before it's even painted, so (unlike hide_window_by_title, which has to
    wait for the page to load and set its title) it has little to no
    chance to actually flash on screen.

    Returns True if a new window was found and hidden, False otherwise
    (e.g. not on Windows, or none appeared within `timeout`) -- callers
    should fall back to hide_window_by_title in that case.
    """
    if sys.platform != "win32":
        return False

    user32 = ctypes.windll.user32
    SW_HIDE = 0
    before = set(before_hwnds)
    elapsed = 0.0
    while elapsed < timeout:
        new_hwnds = [h for h in _enum_hwnds() if h not in before]
        if new_hwnds:
            for hwnd in new_hwnds:
                user32.ShowWindow(hwnd, SW_HIDE)
            return True
        time.sleep(poll_interval)
        elapsed += poll_interval
    return False
