import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVERS_DIR = os.path.join(REPO_ROOT, "drivers")

PROFILE_DIR = os.path.join(os.path.expanduser("~"), "tv_profile")

CHART_URL = "https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSD"
SIGNAL_SITE_URL = "https://tradinggenerator-english.tiiny.co/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LOGIN_POLL_INTERVAL_SECONDS = 15


def _load_env_file(path):
    values = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


_env = _load_env_file(os.path.join(REPO_ROOT, ".env"))

TRADINGGENERATOR_USERNAME = _env.get("TRADINGGENERATOR_USERNAME", "")
TRADINGGENERATOR_PASSWORD = _env.get("TRADINGGENERATOR_PASSWORD", "")
