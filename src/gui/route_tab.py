"""The Route tab (spec 5.2-5.5, contract C5 of docs/superpowers/plans/2026-10-31-route-tab.md).

This module is `src.gui`'s one door to the run driver: it imports `src.route.session`, which reaches the input layer
(import-linter contract "gui reaches input only through the run driver"). The pure helpers below hold the words and the
rules the tab shows (settings checks, the class key, the end-of-run texts, the countdown line); none of them touches Tk.
`RouteTab` is the tab itself: the top area, the readiness line, Start, the run's worker thread, the 100 ms drain of its
event queue with the dead-worker watchdog, Stop, the close handler that waits for a live run's cleanup (RT-8a), the
hosted route view: the run's live view (RT-8b), kept in place and frozen at the end with a "Back to live minimap" button
(no replay opens, H68, H69, RT-8c), and the idle live minimap shown when no view is (`src/gui/live_minimap.py`, RT-8c,
H70). Every line a run in the app prints (the worker's traceback, the session's lines, the live view's and the idle
minimap's errors) goes to the Setup tab's console box, not the terminal, through `_toConsole` (H71; RDO110-D3 A). The
app-focus flag and its stop (RT-9), and the saved-run list (spec 5.11, RT-10): its rows (`savedRuns`, `formatSize`),
filled by `startPolling` and Refresh, with each folder's size summed off the Tk thread (`folderSize`, the
`saved-run-sizes` thread) and drained like the run's events, and Open, which hosts the selected run's replay in place of
the idle minimap or a kept run view.
"""
import contextlib
import gc
import json
import math
import os
import queue
import re
import threading
import time
import tkinter as tk
import traceback
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple

from returns.pipeline import is_successful
from returns.result import Result

from src.gui.live_minimap import LiveMinimap
from src.route import runtime, session
from src.route.leg_log import LAPS_FILE, LEGS_FILE
from src.route.session import FrameHolder, RunEvent, RunSession, RunSettings
from src.route.step_log import STEPS_FILE
from src.route_view.follow import LiveController
from src.route_view.model import RunModel, load_folder
from src.route_view.window import RouteViewWindow

if TYPE_CHECKING:  # gui.py imports RouteTab, so a runtime import of MapleGUI here would be a cycle
    import numpy as np

    from src.gui.gui import MapleGUI

LAPS_ERROR = "Laps must be a whole number of 1 or more"
MINUTES_ERROR = f"Minutes must be a number above 0 and at most {threading.TIMEOUT_MAX / 60:g}"
COUNTDOWN_ERROR = "Countdown must be a whole number of seconds from 1 to 60"

_WHOLE_NUMBER = re.compile(r"[0-9]+")  # ASCII digits only: int() would also take "+2", "1_0" and other Unicode digits
_DECIMAL_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]*)?|\.[0-9]+")  # Minutes: ASCII digits and at most one point (RT-7-S1 M2)

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
_CONSOLE_BOX = "the console box on the Setup tab"  # where the details are (H71: not the terminal)


def _wholeNumber(text: str) -> int | None:
    """`text` (already stripped) as an int when it is ASCII digits only, else None. Past Python's int-digit limit (4300
    digits by default) int() raises ValueError; that is None too, so the 100 ms drain never raises (RT-7-S1 M1)."""
    if not _WHOLE_NUMBER.fullmatch(text):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def validateSettings(laps: str, minutes: str, countdown: str
                     ) -> tuple[tuple[int | None, float | None, int] | None, str | None]:
    """The three field strings (spec 5.3) as `((laps, minutes, countdown), None)`, or `(None, error text)` naming the
    first bad field. Spaces around a value are ignored; an empty Laps or Minutes means no limit (None)."""
    laps = laps.strip()
    laps_value = None
    if laps:
        laps_value = _wholeNumber(laps)
        if laps_value is None or laps_value < 1:
            return None, LAPS_ERROR
    minutes = minutes.strip()
    minutes_value = None
    if minutes:
        minutes_value = float(minutes) if _DECIMAL_NUMBER.fullmatch(minutes) else math.nan
        # nan fails both comparisons; digits past the float range ("9" * 400) read as inf and fail the upper one
        if not (math.isfinite(minutes_value) and 0 < minutes_value <= threading.TIMEOUT_MAX / 60):
            return None, MINUTES_ERROR
    countdown_value = _wholeNumber(countdown.strip())
    if countdown_value is None or not 1 <= countdown_value <= 60:
        return None, COUNTDOWN_ERROR
    return (laps_value, minutes_value, countdown_value), None


def classKey(instance: object) -> str | None:
    """The key of `instance`'s class in `session.CLASSES` (what the run takes, spec 5.3), or None when the class has
    none. The exact class counts: a subclass of Kanna is another class."""
    for key, cls in session.CLASSES.items():
        if type(instance) is cls:
            return key
    return None


def endedText(code: int | None, text: str, folder: Path | None) -> str:
    """The status line for a run that has ended (spec 5.5 with plan choices 3 and 6; choice 7's folder name and
    " Replay is open." went with H68, RT-8c). `code` and `text` are the `ended` event's: with a code, `text` is
    `f"{reason}/{source}"` (`reason` is stop.json's, or "none" with no stop.json; `source` is the first stop trigger,
    "" for none), and a non-zero code is read before `text`. With no code `text` is the exception text. `folder` is the
    run folder: only the 5.5 texts that name it use it (codes 6 and 7, and the "stopped unexpectedly" text when the
    folder exists); every other text stands alone, with no folder name (ruling orch 110)."""
    if code is None:
        if folder is not None and folder.exists():
            return f"Ended: {text}. See {_CONSOLE_BOX} and {folder.name}."
        return f"Ended: {text}. See {_CONSOLE_BOX}."
    if code in _WALKER_FAILED_CODES:
        if folder is None:
            return f"Ended: the walker failed; see {_CONSOLE_BOX}."
        return f"Ended: the walker failed; see {folder.name}."
    if code in _CODE_TEXTS:
        return _CODE_TEXTS[code]
    reason, _, source = text.partition("/")
    if code == 0:
        if reason == "max_laps":
            return _ALL_LAPS_DONE  # whatever the source: the laps were done
        known = {"stopped": _STOPPED_TEXTS, "cancelled": _CANCELLED_TEXTS}.get(reason)
        if known is not None and source in known:
            return known[source]
    if code != 0:
        return f"Ended (exit {code}, {text})."  # a code no producer sends (walk.py:6-9); the code is the useful fact
    return f"Ended ({text})."  # a pair no producer sends; nothing raises


def _unexpectedEndText(folder: Path | None) -> str:
    """Spec 5.5's watchdog text, for a worker that ended with no `ended` event: "and <run folder>" only when the
    folder exists."""
    if folder is not None and folder.exists():
        return f"Ended: the run stopped unexpectedly; see {_CONSOLE_BOX} and {folder.name}."
    return f"Ended: the run stopped unexpectedly; see {_CONSOLE_BOX}."


def countdownText(first: int, left: int) -> str:
    """The countdown status line when `left` steps remain of `first` (spec 5.2, choice 4): only the last five counts
    so far are shown, so a countdown of up to 60 still fits the line."""
    counts = [str(n) for n in range(first, left - 1, -1)][-5:]
    ending = ". F12 stops." if left == 1 else " ... F12 stops."
    return "Click the game now. Starting in " + " ... ".join(counts) + ending


# spec 5.11 and plan choice 13: the saved-run list
SIZE_PENDING_TEXT = "size ..."  # a row's size until the size thread has summed its folder
_UNREAD = "?"  # a field of a row that cannot be read
_SESSION_FILE = "session.json"  # the run's settings, as `session.py` writes them (`_SESSION` there)
# H72's console line ("Replay not opened: <the reason>") for an Open that would otherwise say nothing; the reason's
# words are RT-10's (RT-10-D1, provisional)
NO_ROW_SELECTED_TEXT = "Replay not opened: no saved run is selected"


class SavedRun(NamedTuple):
    """One run of the saved-run list: its folder and its row's label without the size (choice 13)."""
    folder: Path
    label: str


def savedRuns(run_root: Path) -> list[SavedRun]:
    """The run folders directly under `run_root` that hold `steps.jsonl` or `legs.jsonl` (spec 5.11), newest first
    (their names are UTC stamps), each labelled `<local start time>  <map file stem>  <N> laps` (plan choice 13): the
    start time from `session.json`'s `start_utc` in local time, the map from its `map`, N the lines of `laps.jsonl`,
    and `?` for what cannot be read. A missing or unreadable `run_root` gives an empty list. No sizes: the size
    thread sums those. Never raises."""
    try:
        children = list(run_root.iterdir())
    except OSError:
        return []
    runs = [child for child in children if (child / STEPS_FILE).is_file() or (child / LEGS_FILE).is_file()]
    return [SavedRun(folder, _runLabel(folder)) for folder in sorted(runs, key=lambda folder: folder.name, reverse=True)]


def _runLabel(folder: Path) -> str:
    start = map_stem = laps = _UNREAD
    try:
        info = json.loads((folder / _SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing, unreadable, not UTF-8 or not JSON
        info = None
    if isinstance(info, dict):
        start = _localStart(info.get("start_utc"))
        map_path = info.get("map")
        if isinstance(map_path, str) and Path(map_path).stem:
            map_stem = Path(map_path).stem
    try:
        laps = str((folder / LAPS_FILE).read_bytes().count(b"\n"))  # whole lines: one per lap done
    except OSError:
        pass
    return f"{start}  {map_stem}  {laps} {'lap' if laps == '1' else 'laps'}"


def _localStart(value: object) -> str:
    """`start_utc` (an ISO time; one with no zone is read as UTC) as local `%Y-%m-%d %H:%M`, else `?`."""
    if not isinstance(value, str):
        return _UNREAD
    try:
        when = datetime.fromisoformat(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return when.astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):  # not a time, or out of the local clock's range
        return _UNREAD


def folderSize(folder: Path) -> int:
    """The bytes of every file under `folder`, summed with `os.scandir` (spec 5.11; no Tk: the size thread runs it).
    An entry or a folder that cannot be read (OSError) is skipped; links are not followed."""
    total = 0
    pending = [os.fspath(folder)]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def formatSize(n: int) -> str:
    """Plan choice 13: `<n> MB` (2^20 bytes, rounded) below 1000 MB, else `<x.y> GB` (2^30 bytes), as Explorer."""
    megabytes = round(n / 2**20)
    if megabytes < 1000:
        return f"{megabytes} MB"
    return f"{n / 2**30:.1f} GB"


# ---- the tab ------------------------------------------------------------------------------------------------------

# spec 5.2 (the Ready row) and 5.4 (the checks). The texts of checks 3 and 4 are the plan's (RT-7); the rest are the spec's.
READY_TEXT = "Ready. Press Start, then click the game."
NO_MAP_TEXT = "Load a map on the Setup tab first."
NO_CLASS_TEXT = "Choose a character on the Setup tab first."
NO_KEY_TEXT = "This character cannot walk a route yet."
EDITOR_OPEN_TEXT = "Close the map editor before Start."
NO_MINIMAP_TEXT = ("The minimap has not been found yet. Show the game with its minimap on the captured monitor "
                   "(the second monitor, not the main display).")
NO_CHARACTER_TEXT = ("The character was not found on the minimap. Check that the character is on the minimap, then "
                     "press Start again.")
RUN_FOLDER_EXISTS_TEXT = "A run folder for this second already exists. Press Start again."
# RT-FIX-1 (H82): a walk that sends keys is refused while Helix is not elevated, because F12 would not stop it
NOT_ELEVATED_START_TEXT = f"Not started: {runtime.NOT_ELEVATED_TEXT}"
NONE_TEXT = "(none)"  # the Map and Class line before a map is loaded or a character is chosen
RECORDING_TEXT = f"Recording: on (about {session.RECORD_GB_PER_HOUR:.1f} GB per hour)"  # spec 5.2; information only
# spec 5.2's run states. Connecting's text is also the session's own `status` event (C1).
CONNECTING_TEXT = "Connecting to the board..."
RUNNING_TEXT = "Running. F12 or Stop ends the run. Clicking this app also stops it."
STOPPING_TEXT = "Stopping: releasing keys..."
STOPPING_CONNECT_TEXT = "Stopping: waiting for the board connection attempt to end (up to 30 s)..."
# spec 5.5 "Closing the app mid-run" (H79): the status line while the window waits for the run's cleanup. The first is
# Hong's mockup word for word (QB182); the second says what the wait is when the run is still connecting.
CLOSING_TEXT = "Closing - waiting for the run to finish cleaning up..."
CLOSING_CONNECT_TEXT = "Closing - waiting for the board connection attempt to end (up to 30 s)..."
BACK_TEXT = "Back to live minimap"  # spec 5.2, H69: shown with a kept run view

POLL_MS = 100  # the drain's period, and the close handler's (spec 5.5, 5.7)
CLOSE_WAIT_S = 10  # a press on the close button this long after the first asks "Close anyway?" (5.5, H79)
# Python's cycle collector runs on whichever thread allocates past its threshold. On the worker it could free a Tk
# object (a Variable's or an image's `__del__` calls Tcl), which waits for the Tk thread with no timeout and can deadlock
# it on a lock both take (the event queue's). So while a worker lives automatic collection is off and every collection
# runs on the Tk thread: one just before the worker starts, then one every GC_EVERY_POLLS drains (RT-8a I1). The
# objects that exist at Start are frozen (`gc.freeze()`) until the worker is cleared, so the periodic collection walks
# only what the run made since, not the whole app (about 1.5 ms instead of about 23 ms; ruling orch 108).
GC_EVERY_POLLS = 10  # about once a second at POLL_MS
_REPO = Path(__file__).resolve().parents[2]  # the repo root the run model checks the map and the code against
RouteState = Literal["not_ready", "ready", "connecting", "countdown", "running", "stopping", "ended"]
_LIVE_STATES: tuple[RouteState, ...] = ("connecting", "countdown", "running", "stopping")  # Start pressed, cleanup not done
_IDLE_STATES: tuple[RouteState, ...] = ("not_ready", "ready", "ended")  # the readiness line is kept up to date here
_STOPPABLE_STATES: tuple[RouteState, ...] = ("connecting", "countdown", "running")

RunLog = Callable[[str, bool], None]  # one line a run prints and its error flag (H71; RDO110-D3 A)
SessionFactory = Callable[[RunSettings, FrameHolder, Callable[[RunEvent], None], Callable[[], bool], RunLog], RunSession]


def defaultSession(settings: RunSettings, shared: FrameHolder, on_event: Callable[[RunEvent], None],
                   may_walk: Callable[[], bool], log: RunLog) -> RunSession:
    """The run's session as the app builds it (spec 5.6): its own defaults for everything else, no SIGINT handler, and
    its lines to `log` (the tab's line queue, so they reach the console box; H71, RDO110-D3 A)."""
    return RunSession(settings, shared, on_event=on_event, may_walk=may_walk, sigint=False, log=log)


def _free_space_text(run_root: Path) -> str:
    return (f"Less than {session.START_FREE_BYTES / 2**30:g} GB free on {session.drive_name(run_root)}. "
            "Delete old runs in data/route, then press Start.")


class RouteTab:
    """The Route tab's top area (spec 5.2), its readiness line and Start up to the run's session (spec 5.3, 5.4).

    Everything here runs on the Tk thread. `parent` is the Notebook page the tab fills; `gui` is the `MapleGUI` whose
    Setup tab holds the map and the class (`controller.agent`, spec 5.3). The keywords are the seams C5 names: tests
    and the simulator tool pass fakes, and nothing here patches a module name. The constructor calls no `after`, no git
    and no disk probe; `MapleGUI.start` calls `startPolling()`, and Start does the rest."""

    state: RouteState
    status_var: tk.StringVar
    start_button: tk.Button
    stop_button: tk.Button
    open_button: tk.Button
    refresh_button: tk.Button
    laps_var: tk.StringVar
    minutes_var: tk.StringVar
    countdown_var: tk.StringVar
    runs_list: tk.Listbox
    view_frame: tk.Frame
    back_button: tk.Button
    live_minimap: LiveMinimap
    view: RouteViewWindow | None
    live: LiveController | None
    worker: threading.Thread | None
    app_has_keyboard: threading.Event
    size_thread: threading.Thread | None

    def __init__(self, parent: tk.Misc, gui: "MapleGUI", *, make_session: SessionFactory = defaultSession,
                 run_root: Path = session.RUN_ROOT, disk_free: Callable[[Path], int] = session.free_bytes,
                 new_out: Callable[[], Path] = session.new_out,
                 make_thread: Callable[..., threading.Thread] = threading.Thread,
                 clock: Callable[[], float] = time.monotonic,
                 make_view: Callable[..., RouteViewWindow] = RouteViewWindow,
                 make_live: Callable[..., LiveController] = LiveController,
                 load_run: Callable[..., Result[RunModel, str]] = load_folder,
                 git_head: Callable[[], str | None] = session._git_head,
                 make_minimap: Callable[..., LiveMinimap] = LiveMinimap,
                 frame_checks: bool = True, sends_keys: bool = True,
                 elevated: Callable[[], bool] = runtime.is_elevated) -> None:
        self._gui = gui
        self._make_session = make_session
        self._run_root = run_root
        self._disk_free = disk_free
        self._new_out = new_out
        self._make_thread = make_thread  # RT-8a: builds the run's worker only
        self._clock = clock  # RT-8a: the close handler's 10 s wait
        self._make_view = make_view  # RT-8b, RT-10: the hosted view and the replays
        self._make_live = make_live
        self._load_run = load_run
        self._git_head = git_head
        self._make_minimap = make_minimap  # RT-8c: the idle live minimap (H70)
        self._frame_checks = frame_checks
        self._sends_keys = sends_keys  # False only for the sim tool, whose SimLink sends no key (RT-FIX-1)
        self._elevated = elevated  # RT-FIX-1: `runtime.is_elevated`; a fake in the tests
        self._head: str | None = None  # git's HEAD, read once by startPolling and handed to every live view and replay
        self._events: queue.Queue[RunEvent] = queue.Queue()  # the session's on_event (the worker's put), drained here
        # Lines for the console box from other threads (the worker's traceback, the session's `log`), drained here
        # before the events (spec 5.5 "Where the details go", H71)
        self._lines: queue.SimpleQueue[tuple[str, bool]] = queue.SimpleQueue()
        self._out: Path | None = None  # the run folder of the run in progress or last ended
        self._ended_seen = False  # this run's `ended` event has been drained (the watchdog's test)
        self._countdown_first: int | None = None  # this run's first countdown count (plan choice 4)
        self._written: tuple | None = None  # (inputs, text) of the readiness line's last write (plan choice 8)
        self._closing: tuple[Callable[[], None], float] | None = None  # (destroy, clock at the press) while it waits
        self._asking = False  # the "Close anyway?" box is open: the close poll must not destroy the window under it
        self._collector_off = False  # Start turned automatic garbage collection off and nothing has turned it back on
        self._polls_alive = 0  # drains that found this run's worker alive (the periodic collection's count)
        self._saved: list[SavedRun] = []  # the saved-run list's runs, one per row, in its order (RT-10)
        # (generation, folder, bytes) from the size thread, drained here; a fill bumps the generation (RT-10)
        self._size_results: queue.SimpleQueue[tuple[int, Path, int]] = queue.SimpleQueue()
        self._size_generation = 0
        self.state = "not_ready"
        self.view = None
        self.live = None
        self.session: RunSession | None = None  # annotated here: a class-level `session:` would hide the module
        self.worker = None
        self.size_thread = None
        self.app_has_keyboard = threading.Event()  # spec 9: set and cleared by rereadFocus on the Tk thread only
        self.status_var = tk.StringVar(master=parent)
        self.map_class_var = tk.StringVar(master=parent)
        self.laps_var = tk.StringVar(master=parent, value="")  # empty: until stopped
        self.minutes_var = tk.StringVar(master=parent, value="")  # empty: no limit
        self.countdown_var = tk.StringVar(master=parent, value="5")
        self._build(parent)
        self._showMinimap()
        self._refreshReadiness()

    def _build(self, parent: tk.Misc) -> None:
        """The widgets of spec 5.2: the top area, the saved-runs area at the bottom and the container the view will be
        hosted in between them, with the idle live minimap's canvas built (not packed) in it, and the "Back to live
        minimap" button in a frame of its own, packed between the top area and the view only while a kept run view is
        shown (`_showBack`). No dialog, no Toplevel, no Combobox (spec 5.12)."""
        top = tk.Frame(parent)
        top.pack(side=tk.TOP, fill=tk.X, padx=8, pady=6)
        tk.Label(top, textvariable=self.map_class_var, anchor="w").pack(side=tk.TOP, fill=tk.X)

        fields = tk.Frame(top)
        fields.pack(side=tk.TOP, fill=tk.X, pady=4)
        tk.Label(fields, text="Laps").pack(side=tk.LEFT)
        self.laps_entry = tk.Entry(fields, textvariable=self.laps_var, width=6)
        self.laps_entry.pack(side=tk.LEFT, padx=4)
        tk.Label(fields, text="(empty = until stopped)").pack(side=tk.LEFT, padx=(0, 16))
        tk.Label(fields, text="Minutes").pack(side=tk.LEFT)
        self.minutes_entry = tk.Entry(fields, textvariable=self.minutes_var, width=6)
        self.minutes_entry.pack(side=tk.LEFT, padx=4)
        tk.Label(fields, text="(empty = no limit)").pack(side=tk.LEFT, padx=(0, 16))
        tk.Label(fields, text="Countdown").pack(side=tk.LEFT)
        self.countdown_spinbox = tk.Spinbox(fields, from_=1, to=60, width=4, textvariable=self.countdown_var)
        self.countdown_spinbox.pack(side=tk.LEFT, padx=4)
        tk.Label(fields, text="s").pack(side=tk.LEFT)

        buttons = tk.Frame(top)
        buttons.pack(side=tk.TOP, fill=tk.X, pady=4)
        self.start_button = tk.Button(buttons, text="Start", command=self.startRun)
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = tk.Button(buttons, text="Stop", state="disabled", command=self.stopRun)
        self.stop_button.pack(side=tk.LEFT, padx=8)
        self.recording_label = tk.Label(buttons, text=RECORDING_TEXT)
        self.recording_label.pack(side=tk.RIGHT)

        status = tk.Frame(top)
        status.pack(side=tk.TOP, fill=tk.X)
        tk.Label(status, text="Status:").pack(side=tk.LEFT, anchor="n")
        tk.Label(status, textvariable=self.status_var, anchor="w", justify=tk.LEFT, wraplength=1000
                 ).pack(side=tk.LEFT, fill=tk.X, padx=4)

        saved = tk.Frame(parent)
        saved.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        header = tk.Frame(saved)
        header.pack(side=tk.TOP, fill=tk.X)
        tk.Label(header, text="Saved runs").pack(side=tk.LEFT)
        self.refresh_button = tk.Button(header, text="Refresh", command=self.refreshSavedRuns)
        self.refresh_button.pack(side=tk.RIGHT)
        self.open_button = tk.Button(header, text="Open", command=self.openSavedRun)
        self.open_button.pack(side=tk.RIGHT, padx=8)
        listing = tk.Frame(saved)
        listing.pack(side=tk.TOP, fill=tk.X)
        self.runs_list = tk.Listbox(listing, height=4, exportselection=False)
        scrollbar = tk.Scrollbar(listing, orient=tk.VERTICAL, command=self.runs_list.yview)
        self.runs_list.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.runs_list.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.view_frame = tk.Frame(parent)
        self.view_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.live_minimap = self._make_minimap(self.view_frame, self._gui, report=self._errorToConsole)
        self._back_frame = tk.Frame(parent)  # packed only while shown, so the view area loses no height otherwise
        self.back_button = tk.Button(self._back_frame, text=BACK_TEXT, command=self.backToLive)
        self.back_button.pack(side=tk.LEFT)

    # -- what the Setup tab holds ------------------------------------------------------------------------------------

    def _mapAndClass(self) -> tuple[Path | None, object | None]:
        """The loaded CSV's path and the chosen character class instance (spec 5.3; `CSV_file` is the one truth for
        "a map is loaded", src/controller/CLAUDE.md)."""
        controller = self._gui.controller
        assert controller is not None  # the app always has one (the default None is for tests)
        agent = controller.agent
        return agent.CSVParser.CSV_file, agent.characterClass

    def _minimapFound(self) -> bool:
        """Check 3 without the console line `MapleGUI._minimapReady()` prints (plan choice 8)."""
        return self._gui.minimap_canvas is not None

    def _firstMissing(self, minimap_found: Callable[[], bool]) -> str | None:
        """Checks 1-3 of spec 5.4 (map and class, editor closed, minimap found): the text of the first that fails, or
        None. `minimap_found` is `_minimapFound` for the readiness line and `MapleGUI._minimapReady` for Start."""
        csv, character = self._mapAndClass()
        if csv is None:
            return NO_MAP_TEXT
        if character is None:
            return NO_CLASS_TEXT
        if classKey(character) is None:
            return NO_KEY_TEXT
        editor = self._gui.map_editor
        if editor is not None and editor.window.winfo_exists():
            return EDITOR_OPEN_TEXT
        if self._frame_checks and not minimap_found():
            return NO_MINIMAP_TEXT
        return None

    # -- readiness (spec 5.2 Not ready / Ready, plan choice 8) ---------------------------------------------------------

    def _refreshReadiness(self) -> None:
        """Set Start from the field checks and checks 1-3; write the status line only when what it is computed from
        or its text changed since the last write, so a Start refusal (checks 4-6) or an Ended text stays until a
        field, the map, the class or the minimap state changes."""
        csv, character = self._mapAndClass()
        self.map_class_var.set(f"Map: {csv.name if csv is not None else NONE_TEXT}   "
                               f"Class: {type(character).__name__ if character is not None else NONE_TEXT}")
        fields = (self.laps_var.get(), self.minutes_var.get(), self.countdown_var.get())
        problem = validateSettings(*fields)[1] or self._firstMissing(self._minimapFound)
        text = problem or READY_TEXT
        self.start_button.configure(state="disabled" if problem else "normal")
        written = (self._readinessInputs(), text)
        if written != self._written:
            self._written = written
            self.status_var.set(text)
            self.state = "not_ready" if problem else "ready"

    def _readinessInputs(self) -> tuple:
        """What the readiness line is computed from (the three fields, the map, the class, whether the minimap area
        exists): `_refreshReadiness` and `startRun` both record it in `_written`, so the two cannot drift."""
        csv, character = self._mapAndClass()
        fields = (self.laps_var.get(), self.minutes_var.get(), self.countdown_var.get())
        return fields, csv, character, self._gui.minimap_canvas is not None

    def startPolling(self) -> None:
        """Read git's HEAD once, fill the saved-run list (RT-10) and start the 100 ms drain. Only `MapleGUI.start`
        calls it, so a test that builds the window never runs git, lists `data/route` or schedules a drain."""
        self._head = self._git_head()
        self.refreshSavedRuns()
        self._gui.gui.after(POLL_MS, self.drainEvents)

    def drainEvents(self) -> None:
        """One 100 ms step on the Tk thread (spec 5.5, 5.7): the run's queued console lines (`_drainLines`), its queued
        events, the saved-run sizes queued so far (`_drainSizes`, RT-10), then the watchdog, then the
        readiness refresh (only with no worker, no close waiting and an idle state), then one redraw of the idle live
        minimap while it is shown and the Route tab is in front (spec 5.2). It reschedules itself in a `finally`, so a
        step that raises does not end the loop.

        The watchdog (spec 5.5): a worker found dead is read as ended only after one more drain of the queue finds no
        `ended`, so an `ended` put between the first drain and `is_alive()` is still read as itself. A dead worker is
        cleared (automatic garbage collection comes back on) and gives the controls back, whether its run ended with
        an `ended` event or not, except while the window waits to close: then nothing comes back (RT-8a m1). While the
        worker lives, every `GC_EVERY_POLLS`-th step collects garbage here, on the Tk thread (RT-8a I1)."""
        try:
            self._drainLines()
            self._drainQueue()
            self._drainSizes()
            worker = self.worker
            if worker is not None and not worker.is_alive():
                try:
                    self._drainLines()
                    self._drainQueue()
                    if not self._ended_seen:
                        self._endWithoutEnded()
                finally:  # an `_onEnded` that raises must not leave the worker set and the collector off for good
                    self._clearWorker()
                    if self._closing is None:
                        self._lockControls(False)
            elif worker is not None:
                self._polls_alive += 1
                if self._polls_alive % GC_EVERY_POLLS == 0:
                    gc.collect()
            if self.worker is None and self._closing is None and self.state in _IDLE_STATES:
                self._refreshReadiness()
            if self._minimapShown() and not self._gui.isSetupTabShown():
                self.live_minimap.draw()
        finally:
            self._gui.gui.after(POLL_MS, self.drainEvents)

    def _endWithoutEnded(self) -> None:
        """The run's end on the tab when no `ended` came (the watchdog, or a worker that could not start): Ended,
        `_onEnded(None, "")`, then spec 5.5's "stopped unexpectedly" text."""
        self.state = "ended"
        self._onEnded(None, "")
        self._say(_unexpectedEndText(self._out))

    def _clearWorker(self) -> None:
        """The worker has ended or never started: forget it and, if Start turned automatic garbage collection off,
        unfreeze what Start froze, collect it here on the Tk thread (a view kept from Start and destroyed during the
        run is garbage the drain's collections skipped; since H70 Start drops that view before its own collection, so
        what is left is anything frozen at Start that becomes cyclic garbage during the run) and turn automatic
        collection back on, once per run (RT-8a I1, ruling orch 108)."""
        self.worker = None
        if self._collector_off:
            self._collector_off = False
            gc.unfreeze()
            gc.collect()
            gc.enable()

    def _say(self, text: str) -> None:
        """Write the status line for a drained event or the watchdog, except while the window waits to close: the
        close's Stopping text stays until the window goes (RT-8a m1)."""
        if self._closing is None:
            self.status_var.set(text)

    def _toConsole(self, text: str, error: bool) -> None:
        """The tab's one writer of the app's console box (spec 5.5 "Where the details go", H71), on the Tk thread:
        `text` without its trailing newlines, as one entry of the Setup tab's console. `error` marks an error line,
        where a colour goes later (Hong's note to H71); there are no colours yet, so it changes nothing now."""
        del error  # no colours yet (H71)
        self._gui._printToGUIConsole(text.rstrip("\n"))

    def _errorToConsole(self, text: str) -> None:
        """An error line from the Tk thread (the live view's `report`, the idle minimap's) into the console box."""
        self._toConsole(text, True)

    def _postLine(self, text: str, error: bool) -> None:
        """The session's `log`, from any thread: the line waits on the tab's queue for the drain (the worker never
        touches Tk)."""
        self._lines.put((text, error))

    def _drainLines(self) -> None:
        """Write every queued console line into the box, in order."""
        while True:
            try:
                text, error = self._lines.get_nowait()
            except queue.Empty:
                return
            self._toConsole(text, error)

    def _drainQueue(self) -> None:
        """Show every event queued so far, in order. Before an `ended` is shown the queued console lines are written
        again: the run puts its last lines before `ended`, and one may have arrived after this step's line drain
        (RDO110-R m2)."""
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            if event.kind == "ended":
                self._drainLines()
            self._showEvent(event)

    def _showEvent(self, event: RunEvent) -> None:
        """One event to spec 5.2's state and status line. A `warning` is appended after one space in any state
        (choice 5). `ended` sets the state, calls `_onEnded` (the run's view is kept, frozen), then writes `endedText`.
        While Stopping its text stays until the end: an earlier `status` or `countdown` read late changes nothing,
        and a `running` still reaches `_onRunning` (the walk did start). While the window waits to close no event
        writes the status line (`_say`)."""
        if event.kind == "warning":
            self._say(f"{self.status_var.get()} {event.text}")
        elif event.kind == "ended":
            self._ended_seen = True
            self.state = "ended"
            self._onEnded(event.code, event.text)
            self._say(endedText(event.code, event.text, self._out))
        elif self.state == "stopping":
            if event.kind == "running":
                self._onRunning()
        elif event.kind == "status":
            self.state = "connecting"
            self._say(event.text)
        elif event.kind == "countdown":
            left = event.seconds_left if event.seconds_left is not None else 0
            first = self._countdown_first if self._countdown_first is not None else left
            self._countdown_first = first
            self.state = "countdown"
            self._say(countdownText(first, left))
        elif event.kind == "running":
            self.state = "running"
            self._say(RUNNING_TEXT)
            self._onRunning()

    def _onRunning(self) -> None:
        """Called once per drained `running`, after the state and the status line are set: the run's live view (spec
        5.2 Hosting, 5.8). Any view shown so far goes first, its controller stopped, and the idle live minimap Start
        showed is hidden (H78: Hong's answer A to RDO110-D1); then a new view is built in `view_frame` on
        the run folder, with a `LiveController` whose first poll runs now and which then polls every 100 ms (its error
        lines go to the console box). Built
        here and not at the countdown: the steps start now, and a view built earlier would say it has had no step for
        10 s during a countdown of up to 60 s."""
        out = self._out
        if out is None or self._closing is not None:
            return
        self._dropView()
        self._hideMinimap()
        view = self._make_view(self.view_frame, out, None, live=True)
        self.view = view
        live = self._make_live(self._gui.gui, view, out, repo_root=_REPO, current_git_head=self._head,
                               live_frame=self._liveFrame, report=self._errorToConsole)
        self.live = live
        live.tick()

    def _liveFrame(self) -> "np.ndarray | None":
        """The live view's picture (spec 5.8, plan choice 12): the shared minimap frame, read under its lock and
        returned as the reference; the view scales it after the lock is released."""
        shared = self._gui.sharedInfo
        if shared is None:
            return None
        with shared.minimap_frame_lock:
            return shared.minimap_frame

    def _onEnded(self, code: int | None, text: str) -> None:
        """Called once per end (an `ended` event's code and text, or `(None, "")` from the watchdog), after the state
        is set and before the status line. No replay opens (H68): the run's live view stays in place, frozen (H69).
        `ended` comes after the walk's cleanup, so the run's files are complete: the live view reads the lines no tick
        has read yet (`update()`), then its controller stops. The view stays live, so it shows no replay bar and keeps
        its last live picture, panel and why lines; its title's mode reads `ended: <reason>` once its `end` line was
        read (`follow.py`), else `ended`. Then "Back to live minimap" shows. A run that ended before `running` has no
        run view, so the idle minimap Start showed stays, with no button (H78: Hong's answer A to
        RDO110-D1). While the window waits to close nothing changes: the close takes the view down (RT-8a fix round)."""
        live, view = self.live, self.view
        if self._closing is not None or live is None or view is None:
            return
        live.update()
        live.stop()
        self.live = None
        if not live.ended:
            view.set_mode("ended")
        self._showBack()

    def backToLive(self) -> None:
        """The "Back to live minimap" button (spec 5.2, H69, RDO110-D2 answer A): the kept run view or the saved run's
        replay goes and the idle live minimap shows; the button hides with the view (`_dropView`). The status line keeps
        the run's Ended text."""
        self._showMinimap()

    def _showBack(self) -> None:
        """Show "Back to live minimap" above the view area, while a kept run view is shown (H69) or a saved run's replay
        (RDO110-D2, answer A; RT-10's Open hosts its replay the same way and calls this)."""
        if not self._backShown():
            self._back_frame.pack(side=tk.TOP, fill=tk.X, padx=8, before=self.view_frame)

    def _backShown(self) -> bool:
        return self._back_frame.winfo_manager() == "pack"

    def _showMinimap(self) -> None:
        """Show the idle live minimap (spec 5.2, H70): any hosted view goes (and with it the Back button), then the
        minimap's canvas is packed."""
        self._dropView()
        if not self._minimapShown():
            self.live_minimap.canvas.pack(side=tk.TOP, anchor=tk.NW)

    def _hideMinimap(self) -> None:
        """Hide the idle live minimap before a view takes the view area; it is not drawn while hidden."""
        self.live_minimap.canvas.pack_forget()

    def _minimapShown(self) -> bool:
        return self.live_minimap.canvas.winfo_manager() == "pack"

    def _dropView(self) -> None:
        """Stop the live view's polling and destroy the hosted view, if there is one; no view is left in `view_frame`,
        and the Back button, which shows only with a kept view or a replay, hides."""
        live, view = self.live, self.view
        self.live = None
        self.view = None
        self._back_frame.pack_forget()
        if live is not None:
            live.stop()
        if view is not None:
            view.destroy()

    def isRunLive(self) -> bool:
        """Start pressed and the cleanup not finished: the Setup tab's picture is not redrawn while this holds and the
        Route tab is in front (spec 5.7)."""
        return self.state in _LIVE_STATES

    def onAppClose(self, destroy: Callable[[], None]) -> None:
        """The window's close button (spec 5.5 "Closing the app mid-run", H79). With no worker the window goes at once.
        With one: `session.stop("window_close")`, a status line that says what the window waits for (`CLOSING_TEXT`)
        with nothing enabled, then a close poll every 100 ms (`_pollClose`) that destroys the window the moment the
        worker has ended, however long that takes. A press while it waits is `_closePressedAgain`."""
        if self._closing is not None:
            self._closePressedAgain()
            return
        if self.worker is None:
            self._closeWindow(destroy)
            return
        self._closing = (destroy, self._clock())
        if self.session is not None:
            self.session.stop("window_close")  # first call wins: after Stop or an end it changes nothing
        text = CLOSING_CONNECT_TEXT if self.state == "connecting" else CLOSING_TEXT
        self.state = "stopping"
        self.status_var.set(text)
        self.stop_button.configure(state="disabled")
        self._gui.gui.after(POLL_MS, self._pollClose)

    def _closePressedAgain(self) -> None:
        """A press on the close button while the window already waits (H79): within `CLOSE_WAIT_S` of the first press, or
        once the worker has ended (the poll closes the window within 100 ms), it does nothing, so there is one poll
        loop and one stop. Later it asks "Close anyway?" through `MapleGUI.askCloseAnyway`: Yes destroys the window
        now (the worker is a daemon and dies with the process, so automatic garbage collection stays off and frozen);
        No changes nothing, and the next press asks again. While the box is open a press does nothing, so there is
        never a nested box: a program that posts `WM_CLOSE` to the main window reaches here inside the box's own loop
        (RDO110-5-R m1)."""
        assert self._closing is not None
        if self._asking:
            return
        destroy, pressed = self._closing
        if self._clock() - pressed < CLOSE_WAIT_S or not self._workerAlive():
            return
        self._asking = True
        try:
            close_anyway = self._gui.askCloseAnyway()
        finally:
            self._asking = False
        if close_anyway:
            self._closeWindow(destroy)

    def _workerAlive(self) -> bool:
        worker = self.worker
        return worker is not None and worker.is_alive()

    def _pollClose(self) -> None:
        """One close poll: the worker gone (dead, or already cleared by a drain) → the window goes; else the next poll in
        100 ms, with no time limit (H79). While the "Close anyway?" box is open the window is not destroyed under it:
        the poll waits for the answer first (the box's loop runs it: Tk timers run inside `askyesno`, RDO110-5-R)."""
        assert self._closing is not None  # only onAppClose schedules it
        destroy, _ = self._closing
        if self._asking or self._workerAlive():
            self._gui.gui.after(POLL_MS, self._pollClose)
            return
        self._clearWorker()
        self._closeWindow(destroy)

    def _closeWindow(self, destroy: Callable[[], None]) -> None:
        """The view first, then its host (RT-4 m3: `RouteViewWindow.destroy()` after its host is gone raises
        TclError): the live view's polling stops, the view goes, then `destroy()`, which runs even if the view
        raised."""
        try:
            self._dropView()
        finally:
            destroy()

    # -- Start (spec 5.4) ---------------------------------------------------------------------------------------------

    def startRun(self) -> None:
        """The Start button: the field checks, then the six checks of spec 5.4 in order, stopping at the first that
        fails and writing its text to the status line (never a dialog). With all of them passed it builds the run's
        session from the settings, reads the app-focus flag once (spec 9), locks the controls, shows Connecting and
        starts the worker. A press while a worker exists (Start greyed out but pressed anyway, or pressed again while
        Stopping) does nothing: one worker at most. Nor does one while the window waits to close (a drain may give
        Start back before the close poll runs).

        Just before the worker starts, garbage is collected here and automatic collection is turned off until the
        worker is cleared (RT-8a I1). A worker that cannot be built or started ends the run on the tab at once, with
        the watchdog's text and the controls back, and the error goes on to Tk's callback handler (RT-8a m2). Once
        every check has passed, the view shown before Start (the last run's kept view) goes with its Back button and
        the idle live minimap shows until `running` (spec 5.2, H69, H70).

        A walk that sends keys is refused first of all while Helix is not running as administrator (RT-FIX-1, H82): its
        F12 stop would not hear F12 with MapleStory in front. The refusal goes to the status line and to the console
        box, and changes nothing else (`sends_keys=False`, the sim tool, skips it)."""
        if self.worker is not None or self._closing is not None:
            return
        if self._sends_keys and not self._elevated():
            self.status_var.set(NOT_ELEVATED_START_TEXT)
            self._errorToConsole(NOT_ELEVATED_START_TEXT)
            return
        settings, error = validateSettings(self.laps_var.get(), self.minutes_var.get(), self.countdown_var.get())
        if settings is None:
            self.status_var.set(error or "")
            return
        refusal = self._firstMissing(self._gui._minimapReady)
        if refusal is None and self._frame_checks and self._gui._findCharacter() is None:
            refusal = NO_CHARACTER_TEXT
        if refusal is None and self._disk_free(self._run_root) < session.START_FREE_BYTES:
            refusal = _free_space_text(self._run_root)
        if refusal is not None:
            self.status_var.set(refusal)
            return
        out = self._new_out()
        if not is_successful(session.check_out(out)):
            self.status_var.set(RUN_FOLDER_EXISTS_TEXT)
            return
        laps, minutes, countdown = settings
        csv, character = self._mapAndClass()
        key = classKey(character)
        shared = self._gui.sharedInfo
        assert csv is not None and key is not None and shared is not None  # checks 1-2 passed; the app has a holder
        run = self._make_session(
            RunSettings(map_csv=csv, character=key, laps=laps, minutes=minutes, countdown_s=countdown, seed=None,
                        out=out),
            shared, self._events.put, self._mayWalk, self._postLine)
        self.session = run
        self._written = (self._readinessInputs(), READY_TEXT)  # the inputs this Start used
        self._out = out
        self._ended_seen = False
        self._countdown_first = None
        self._polls_alive = 0
        self.rereadFocus()  # spec 9: the flag is read once at Start (still idle here, and no worker: no stop)
        self._lockControls(True)
        self._showMinimap()  # before the collection below, so the dropped view is collected on this thread
        self.state = "connecting"
        self.status_var.set(CONNECTING_TEXT)
        try:
            worker = self._make_thread(target=self._work, args=(run,), name="route-run", daemon=True)
            gc.collect()
            gc.disable()
            gc.freeze()
            self._collector_off = True
            worker.start()
        except BaseException:
            self._clearWorker()
            self._endWithoutEnded()
            self._lockControls(False)
            raise
        self.worker = worker

    def _work(self, run: RunSession) -> None:
        """The worker thread's body (spec 5.7): the walk and nothing that touches Tk; its events reach the tab through
        the queue. `run()` turns an exception into its own `ended` event (5.6); one that still escapes is caught here, so
        it never reaches `threading.excepthook`: its traceback goes on the line queue for the console box, not to the
        terminal (H71), and the drain's watchdog ends the run on the tab."""
        try:
            run.run()
        except BaseException:
            self._postLine(traceback.format_exc(), True)

    def stopRun(self) -> None:
        """The Stop button (spec 5.5): `session.stop("button")`, which returns at once, then Stopping with nothing
        enabled (5.2). Its text says when the board connection attempt has to end first (5.6)."""
        run = self.session
        if run is None or self.worker is None or self.state not in _STOPPABLE_STATES:
            return
        run.stop("button")
        text = STOPPING_CONNECT_TEXT if self.state == "connecting" else STOPPING_TEXT
        self.state = "stopping"
        self.status_var.set(text)
        self.stop_button.configure(state="disabled")

    def _lockControls(self, locked: bool) -> None:
        """Spec 5.2's Enabled column and 5.1's Setup lock: during a run only Stop; at the end the settings and the
        saved runs come back, Stop goes grey and Start is left to the readiness refresh that follows in the drain."""
        self._gui.setRunLocked(locked)
        state: Literal["normal", "disabled"] = "disabled" if locked else "normal"
        for widget in (self.laps_entry, self.minutes_entry, self.countdown_spinbox, self.open_button,
                       self.refresh_button, self.runs_list):
            widget.configure(state=state)
        if locked:
            self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal" if locked else "disabled")

    def _mayWalk(self) -> bool:
        """The session's `may_walk` (spec 9), called on the worker at the countdown's end: the walk starts unless the
        app has the keyboard. It only reads the flag (a `threading.Event`, set and cleared by `rereadFocus` on the Tk
        thread) and makes no Tk call."""
        return not self.app_has_keyboard.is_set()

    def rereadFocus(self) -> None:
        """The app-focus re-read (spec 9, H64), on the Tk thread only: the root's `<FocusIn>` and `<FocusOut>` bindings
        schedule it with `after_idle` (`MapleGUI._setupGUI`) and Start calls it once. `focus_get()` reads Tk's own
        record of which of our widgets has the keyboard (no Windows call): a widget sets `app_has_keyboard`, None
        clears it. A KeyError from it means the record names a widget Tk made itself (a Combobox drop-down's list,
        which tkinter's `nametowidget` does not know), still one of ours. Focus moving between two of our widgets
        reads a widget both times, so it is no rise. On a rise (clear, then set) in the countdown or while walking the
        user clicked the app: `session.stop("app_focus")`, which returns at once, after the flag is set (a worker
        that reads the flag after the stop sees it set); the state and the status line wait for the `ended`, so a
        press on Stop that follows still sends `button` (H73). Connecting is left alone: no key is sent yet, and
        the countdown's end reads the flag (`_mayWalk`). But a rise that finds the tab still connecting first shows
        the events queued so far (`_drainQueue`) and tests the state again (RT-9-R m1): a drain step that blocked the
        Tk thread for the whole countdown schedules the next step only once it returns, so the click's idle re-read
        runs first, while the worker's `countdown` and `running` still wait on the queue; the state is tested again
        even if showing them raises."""
        try:
            has_keyboard = self._gui.gui.focus_get() is not None
        except KeyError:
            has_keyboard = True
        rose = has_keyboard and not self.app_has_keyboard.is_set()
        if has_keyboard:
            self.app_has_keyboard.set()
        else:
            self.app_has_keyboard.clear()
        run = self.session
        if not rose or run is None or self.worker is None:
            return
        try:
            if self.state == "connecting":
                self._drainQueue()
        finally:
            if self.state in ("countdown", "running"):
                run.stop("app_focus")

    # -- saved runs (spec 5.11, RT-10) ----------------------------------------------------------------------------------

    def refreshSavedRuns(self) -> None:
        """Refresh, and the first fill from `startPolling` (spec 5.11): list the run folders again (`savedRuns`, on
        the Tk thread: one directory listing and two small reads per run), every row ending "size ...", and start a
        `saved-run-sizes` daemon thread that sums their sizes off the Tk thread. Each fill is a new generation: what an
        older size thread still posts is dropped (`_drainSizes`), and that thread stops before its next folder. The
        thread is always a real `threading.Thread`, never `make_thread` (the run's worker only: a test's inline thread
        would sum on the Tk thread), kept in `size_thread`."""
        self._size_generation += 1
        generation = self._size_generation
        self._saved = savedRuns(self._run_root)
        with self._editableList() as listbox:
            listbox.delete(0, tk.END)
            listbox.insert(tk.END, *(f"{run.label}  {SIZE_PENDING_TEXT}" for run in self._saved))
        thread = threading.Thread(target=self._sumSizes, args=(generation, [run.folder for run in self._saved]),
                                  name="saved-run-sizes", daemon=True)
        self.size_thread = thread
        thread.start()

    def _sumSizes(self, generation: int, folders: list[Path]) -> None:
        """The size thread's body: no Tk. Each folder's `folderSize` goes on `_size_results` as `(generation, folder,
        bytes)`; that queue is a `queue.SimpleQueue`, whose `put` runs in C and holds no lock the Tk thread's drain
        waits on. Before each folder it reads the tab's generation (an int only the Tk thread sets) and ends once a
        newer fill has begun."""
        for folder in folders:
            if generation != self._size_generation:
                return
            self._size_results.put((generation, folder, folderSize(folder)))

    def _drainSizes(self) -> None:
        """Write each queued size of the current generation into its row; an older generation's are dropped."""
        while True:
            try:
                generation, folder, size = self._size_results.get_nowait()
            except queue.Empty:
                return
            if generation != self._size_generation:
                continue
            for index, run in enumerate(self._saved):
                if run.folder == folder:
                    with self._editableList() as listbox:
                        selected = index in listbox.curselection()
                        listbox.delete(index)
                        listbox.insert(index, f"{run.label}  {formatSize(size)}")
                        if selected:
                            listbox.selection_set(index)
                    break

    @contextlib.contextmanager
    def _editableList(self) -> Iterator[tk.Listbox]:
        """The list, enabled for one edit and set back after it: a disabled Listbox ignores insert, delete and selection
        changes (Tk), and a run disables it while sizes may still arrive."""
        listbox = self.runs_list
        state = listbox.cget("state")
        listbox.configure(state="normal")
        try:
            yield listbox
        finally:
            listbox.configure(state=state)

    def openSavedRun(self) -> None:
        """Open (spec 5.11): load the selected run with `load_run` (`load_folder`, with the HEAD `startPolling` read)
        and host it as a replay in place of the idle live minimap or a kept run view (spec 5.2, H69, H70): the shown
        view goes (`_dropView`), the minimap is hidden, the replay is built in `view_frame` and "Back to live minimap"
        shows over it (`_showBack`, RDO110-D2 answer A). The status line keeps its text. A load that fails puts its
        error on the status line and leaves the view as it was (so no load is silent, H72); with no row selected
        nothing loads and the console box gets H72's one line. While the run's worker exists it does nothing, even if
        called directly (the list and Open are greyed out then): `load_folder` redirects stdout process-wide, as the
        run's map load on the worker does, and two overlapping redirects leave stdout on a dead buffer (DBG115). Start
        drops a replay before its worker starts, so no run ends while one is shown (`_onEnded` never sees it)."""
        if self.worker is not None:
            return
        selected = self.runs_list.curselection()
        if not selected:
            self._toConsole(NO_ROW_SELECTED_TEXT, True)
            return
        folder = self._saved[selected[0]].folder
        loaded = self._load_run(folder, repo_root=_REPO, current_git_head=self._head)
        if not is_successful(loaded):
            self.status_var.set(loaded.failure())
            return
        self._dropView()
        self._hideMinimap()
        self.view = self._make_view(self.view_frame, folder, loaded.unwrap(), live=False)
        self._showBack()
