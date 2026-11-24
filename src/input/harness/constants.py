"""Constants that soak.py, analyze.py and realism.py share, as plain data (W1-4).

They live here, not in soak.py, because soak.py imports listener.py (which calls ctypes.WinDLL at import time) and
the offline checkers (analyze.py, realism.py) must import without touching Windows (W0-4).
"""

CI_UNIT_MS = 1.25  # PONG itvl is in 1.25 ms units (C1)
NS_PER_H = 3_600 * 1_000_000_000
FOCUS_LOST = "FOCUS_LOST"  # a harness status, not an InputError (C5)
