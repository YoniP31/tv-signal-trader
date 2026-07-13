import threading


class SessionState:
    """Runtime state shared between the CLI loop and the background login monitor.

    driver_lock must be held around every direct use of the driver so the
    monitor thread's checks never interleave with a command in progress
    (e.g. mid-trade).
    """

    def __init__(self):
        self.driver_lock = threading.Lock()


session = SessionState()
