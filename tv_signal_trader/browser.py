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
