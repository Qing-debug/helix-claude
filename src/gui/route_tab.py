"""The Route tab (spec 5.2-5.5, contract C5 of docs/superpowers/plans/2026-10-31-route-tab.md).

This module is `src.gui`'s one door to the run driver: it imports `src.route.session`, which reaches the input layer
(import-linter contract "gui reaches input only through the run driver"). The pure helpers below hold the words and the
rules the tab shows (settings checks, the class key, the end-of-run texts, the countdown line); none of them touches Tk.
"""
import math
import re
import threading
from pathlib import Path

from src.route import session

LAPS_ERROR = "Laps must be a whole number of 1 or more"
MINUTES_ERROR = f"Minutes must be a number above 0 and at most {threading.TIMEOUT_MAX / 60:g}"
COUNTDOWN_ERROR = "Countdown must be a whole number of seconds from 1 to 60"

_WHOLE_NUMBER = re.compile(r"[0-9]+")  # ASCII digits only: int() would also take "1_0" and some other Unicode digits

# spec 5.5, "Stop, the end of a run, and its words". Texts that need no folder name.
_CLICKED_THE_APP = "Stopped: you clicked the app. Press Start to run again."
_STOPPED_TEXTS = {  # stop.json reason "stopped" by source (a walked run)
    "timer": "Ended: the time limit was reached.",
    "hotkey": "Ended: you pressed F12.",
    "button": "Ended: you pressed Stop.",
    "app_focus": _CLICKED_THE_APP,
    "window_close": "",  # the app closes after the cleanup; nothing to show
}
_CANCELLED_TEXTS = {  # stop.json reason "cancelled" by source (a stop before the walk, choice 6)
    "hotkey": "Ended: cancelled before the walk (you pressed F12).",
    "button": "Ended: cancelled before the walk (you pressed Stop).",
    "app_focus": _CLICKED_THE_APP,
    "window_close": "",
    "countdown_focus": "Not started: click the game first. Press Start, then click the game before the countdown ends.",
}
_ALL_LAPS_DONE = "Ended: all laps done."
_CODE_TEXTS = {  # the walk's exit codes (walk.py:6-9); 6 and 7 name the run folder, so they are built in endedText
    1: "Ended: the board link was lost.",
    2: "Could not start: the run folder could not be written.",
    3: "Could not start: no minimap frame or character found.",
    4: "Could not start: the board did not connect.",
    5: "Could not start: the map, route or character icon did not load.",
    9: "Could not start: the F12 stop key could not be installed.",
    10: "Ended: the character was lost (exit 10).",
}
_WALKER_FAILED_CODES = (6, 7)


def validateSettings(laps: str, minutes: str, countdown: str
                     ) -> tuple[tuple[int | None, float | None, int] | None, str | None]:
    """The three field strings (spec 5.3) as `((laps, minutes, countdown), None)`, or `(None, error text)` naming the
    first bad field. Spaces around a value are ignored; an empty Laps or Minutes means no limit (None)."""
    laps = laps.strip()
    laps_value = None
    if laps:
        if not _WHOLE_NUMBER.fullmatch(laps) or int(laps) < 1:
            return None, LAPS_ERROR
        laps_value = int(laps)
    minutes = minutes.strip()
    minutes_value = None
    if minutes:
        try:
            minutes_value = float(minutes) if "_" not in minutes else math.nan  # float() takes "1_0" too
        except ValueError:
            minutes_value = math.nan
        # nan fails both comparisons, inf and "1e400" fail the upper one (as the walk CLI's check, walk.py:98)
        if not (math.isfinite(minutes_value) and 0 < minutes_value <= threading.TIMEOUT_MAX / 60):
            return None, MINUTES_ERROR
    countdown = countdown.strip()
    if not _WHOLE_NUMBER.fullmatch(countdown) or not 1 <= int(countdown) <= 60:
        return None, COUNTDOWN_ERROR
    return (laps_value, minutes_value, int(countdown)), None


def classKey(instance: object) -> str | None:
    """The key of `instance`'s class in `session.CLASSES` (what the run takes, spec 5.3), or None when the class has
    none. The exact class counts: a subclass of Kanna is another class."""
    for key, cls in session.CLASSES.items():
        if type(instance) is cls:
            return key
    return None


def _with_replay(sentence: str, folder: Path | None, replay_open: bool) -> str:
    """Choice 7: an "Ended:" sentence names the run folder and says the replay is open, but only when a replay did
    open. The sentence's final full stop is dropped before the folder name."""
    if replay_open and folder is not None and sentence.startswith("Ended:"):
        return f"{sentence.removesuffix('.')} ({folder.name}). Replay is open."
    return sentence


def endedText(code: int | None, text: str, folder: Path | None, replay_open: bool) -> str:
    """The status line for a run that has ended (spec 5.5 with plan choices 3, 6 and 7). `code` and `text` are the
    `ended` event's: with a code, `text` is `f"{reason}/{source}"` (`reason` is stop.json's, or "none" with no
    stop.json; `source` is the first stop trigger, "" for none), and a non-zero code is read before `text`. With no
    code `text` is the exception text. `folder` is the run folder (only its name, and whether it exists, matter);
    `replay_open` is whether a replay opened at the end."""
    if code is None:
        if folder is not None and folder.exists():
            sentence = f"Ended: {text}. See the console and {folder.name}."
            return sentence + (" Replay is open." if replay_open else "")
        return f"Ended: {text}. See the console."
    if code in _WALKER_FAILED_CODES:
        if folder is None:
            return "Ended: the walker failed; see the console."
        sentence = f"Ended: the walker failed; see {folder.name}."
        return sentence + (" Replay is open." if replay_open else "")
    if code in _CODE_TEXTS:
        return _with_replay(_CODE_TEXTS[code], folder, replay_open)
    reason, _, source = text.partition("/")
    if code == 0:
        if reason == "max_laps":
            return _with_replay(_ALL_LAPS_DONE, folder, replay_open)  # whatever the source: the laps were done
        known = {"stopped": _STOPPED_TEXTS, "cancelled": _CANCELLED_TEXTS}.get(reason)
        if known is not None and source in known:
            return _with_replay(known[source], folder, replay_open)
    return f"Ended ({text})."  # a pair no producer sends; nothing raises


def countdownText(first: int, left: int) -> str:
    """The countdown status line when `left` steps remain of `first` (spec 5.2, choice 4): only the last five counts
    so far are shown, so a countdown of up to 60 still fits the line."""
    counts = [str(n) for n in range(first, left - 1, -1)][-5:]
    ending = ". F12 stops." if left == 1 else " ... F12 stops."
    return "Click the game now. Starting in " + " ... ".join(counts) + ending
