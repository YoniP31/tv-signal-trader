"""Detects and dismisses TradingView's ad/upsell popups -- caught in
production covering the order-entry panel and other controls the bot
needs to click, which can otherwise leave a click silently swallowed by
the overlay (Selenium raises ElementClickInterceptedException) or, worse,
land on whatever's underneath it instead of the intended control.

Two variants confirmed live so far, both handled by the same mechanism:
  - A small corner "charting ad" toast (Google Publisher Tag content in
    TradingView's own toast-notification system).
  - A full-screen "Ad blocker detected" upsell dialog, shown instead of
    the toast when an ad blocker is active in this Chrome profile.

Two complementary mechanisms share AD_CLOSE_SELECTORS below, so a newly
caught popup type only ever needs one new entry to be handled by both:
  - browser.create_driver() injects WATCHDOG_SCRIPT once per browser
    session via Page.addScriptToEvaluateOnNewDocument -- a persistent,
    debounced MutationObserver running entirely in the page's own JS,
    closing a popup within a couple hundred milliseconds of it appearing
    with no Selenium round-trip at all, and surviving every later
    navigation on its own (no re-injection needed, including after a
    crash-triggered browser relaunch, since that calls create_driver()
    fresh). This is the primary defense.
  - dismiss_ads(driver) runs the identical check on demand from Python --
    a reactive fallback for the rare case a popup appears in the split
    second between the watchdog's own debounce window and a real click
    landing (see trading.place_order's retry-after-dismiss wrapper).
"""

import json

from .logging_utils import timestamped_print as print

# CSS selectors matching the close/dismiss control of every TradingView
# ad/upsell popup variant caught in production so far -- add one here as
# soon as a new shape shows up.
#
# Deliberately none of the CSS-module hashed classes these popups are
# otherwise built from (e.g. "toastGroup-Tb5VG65Y", "overlayBtn-DQ4k9hNT")
# -- those look build-specific and likely to change on TradingView's next
# deploy, the same reasoning already applied throughout trading.py (e.g.
# read_total_pl matching a data-label rather than a class name). Every
# entry here is either TradingView's own stable data-name/data-qa-id
# convention or a generic Google-Publisher-Tag signature, neither tied to
# a specific build:
#   - the ad-toast group's own bulk "Close all" button (a prefix match,
#     not the exact "-charting-ad" suffix, in case some other placement
#     uses this same toast-group mechanism under a different ad name)
#   - the full-screen promo/upsell dialog's close (X) button
AD_CLOSE_SELECTORS = [
    '[data-name^="toast-group-close-button-"]',
    '[data-qa-id="promo-dialog-close-button"]',
]

_SELECTORS_JSON = json.dumps(AD_CLOSE_SELECTORS)

# Returns how many close buttons were actually found and clicked (0 if
# none of AD_CLOSE_SELECTORS matched anything) -- run standalone via
# driver.execute_script for dismiss_ads() below.
_DISMISS_ONCE_JS = f"""
    return (function() {{
        var selectors = {_SELECTORS_JSON};
        var closed = 0;
        for (var i = 0; i < selectors.length; i++) {{
            var els = document.querySelectorAll(selectors[i]);
            for (var j = 0; j < els.length; j++) {{
                try {{ els[j].click(); closed++; }} catch (e) {{}}
            }}
        }}
        return closed;
    }})();
"""

# Injected once per browser session (see browser.create_driver) via
# Page.addScriptToEvaluateOnNewDocument, which runs before the page's own
# scripts on every document/frame load -- document.body doesn't exist yet
# at that point, so setup is deferred to DOMContentLoaded when needed.
# The MutationObserver watches the whole body (TradingView never
# navigates away from the chart SPA in normal operation, so one observer
# per page load covers the whole session) but debounces its callback --
# a chart repaints via DOM mutations constantly, and checking on every
# single one would be wasted work; coalescing bursts into one check every
# 250ms is more than fast enough to beat a human-speed click while still
# being cheap.
WATCHDOG_SCRIPT = f"""
    (function() {{
        var selectors = {_SELECTORS_JSON};
        function tryDismiss() {{
            for (var i = 0; i < selectors.length; i++) {{
                var els = document.querySelectorAll(selectors[i]);
                for (var j = 0; j < els.length; j++) {{
                    try {{ els[j].click(); }} catch (e) {{}}
                }}
            }}
        }}
        var pending = false;
        function scheduleDismiss() {{
            if (pending) return;
            pending = true;
            setTimeout(function() {{ pending = false; tryDismiss(); }}, 250);
        }}
        function start() {{
            new MutationObserver(scheduleDismiss).observe(document.body, {{ childList: true, subtree: true }});
            tryDismiss();
        }}
        if (document.body) {{ start(); }}
        else {{ document.addEventListener('DOMContentLoaded', start); }}
    }})();
"""


def dismiss_ads(driver):
    """Runs AD_CLOSE_SELECTORS' check once, on demand, against whichever
    tab/frame `driver` is currently on -- see WATCHDOG_SCRIPT's persistent
    version for the primary defense this backs up. Returns how many close
    buttons were found and clicked; 0 (never raises) if the script itself
    couldn't run or nothing matched."""
    try:
        closed = driver.execute_script(_DISMISS_ONCE_JS)
    except Exception:
        return 0
    closed = closed or 0
    if closed:
        print(f"  Dismissed {closed} TradingView ad/popup overlay(s) that were in the way.")
    return closed
