import contextlib
import random
import time

_fast = False

# In fast_mode(), pause()/long_pause()/type_humanlike() sleep this fraction
# of their normal duration instead of the full randomized amount -- still
# non-zero, so the DOM/JS involved (e.g. a modal's CSS transition) gets a
# moment to settle rather than being raced, just without the deliberately
# human-like pacing that matters for actual trading actions on
# TradingView/Tradovate.
_FAST_SCALE = 0.1


@contextlib.contextmanager
def fast_mode():
    """Speeds up every pause()/long_pause()/type_humanlike() call for the
    duration of the `with` block -- for administrative flows (e.g. the
    'add_accounts' command) that never touch TradingView/Tradovate and
    have no anti-bot reason to move at human speed, unlike everything else
    in this module. random_wait() (polling/retry cadences, not UI-action
    pacing) is deliberately unaffected."""
    global _fast
    previous = _fast
    _fast = True
    try:
        yield
    finally:
        _fast = previous


def _duration(min_s, max_s):
    if _fast:
        min_s, max_s = min_s * _FAST_SCALE, max_s * _FAST_SCALE
    return random.uniform(min_s, max_s)


def pause(min_s=0.4, max_s=0.9):
    time.sleep(_duration(min_s, max_s))


def long_pause(min_s=1.0, max_s=2.5):
    time.sleep(_duration(min_s, max_s))


def random_wait(min_s, max_s):
    """Sleeps a random duration in [min_s, max_s] -- for polling/retry
    cadences (tens of seconds/minutes), not pause()/long_pause()'s
    sub-3-second UI-action pacing. Unaffected by fast_mode()."""
    time.sleep(random.uniform(min_s, max_s))


def type_humanlike(element, text):
    delay_range = (0.008, 0.022) if _fast else (0.08, 0.22)
    for char in str(text):
        element.send_keys(char)
        time.sleep(random.uniform(*delay_range))
