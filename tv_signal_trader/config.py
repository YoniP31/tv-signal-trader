import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVERS_DIR = os.path.join(REPO_ROOT, "drivers")

PROFILE_DIR = os.path.join(os.path.expanduser("~"), "tv_profile")

CHART_URL = "https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSD"
SIGNAL_SITE_URL = "https://white-martynne-45.tiiny.site/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
