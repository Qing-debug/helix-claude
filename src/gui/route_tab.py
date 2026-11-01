"""The Route tab (spec 5.2-5.5, contract C5 of docs/superpowers/plans/2026-10-31-route-tab.md).

This module is `src.gui`'s one door to the run driver: it imports `src.route.session`, which reaches the input layer
(import-linter contract "gui reaches input only through the run driver"). The pure helpers below hold the words and the
rules the tab shows (settings checks, the class key, the end-of-run texts, the countdown line); none of them touches Tk.
`RouteTab` is the tab itself: the top area, the readiness line, Start, the run's worker thread, the 100 ms drain of its
event queue with the dead-worker watchdog, Stop, and the close handler that waits for a live run's cleanup (RT-8a). The
hosted view (RT-8b), the app-focus flag (RT-9) and the saved-run list (RT-10) are added to it by those tasks.
"""
import math
import queue
import re
import threading
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from returns.pipeline import is_successful
from returns.result import Result

from src.route import session
from src.route.session import FrameHolder, RunEvent, RunSession, RunSettings
from src.route_view.follow import LiveController
from src.route_view.model import RunModel, load_folder
from src.route_view.window import RouteViewWindow

if TYPE_CHECKING:  # gui.py imports RouteTab, so a runtime import of MapleGUI here would be a cycle
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
    if code != 0:
        return f"Ended (exit {code}, {text})."  # a code no producer sends (walk.py:6-9); the code is the useful fact
    return f"Ended ({text})."  # a pair no producer sends; nothing raises


def _unexpectedEndText(folder: Path | None, replay_open: bool) -> str:
    """Spec 5.5's watchdog text, for a worker that ended with no `ended` event: "and <run folder>" only when the
    folder exists, and then choice 7's " Replay is open." when a replay opened."""
    if folder is not None and folder.exists():
        sentence = f"Ended: the run stopped unexpectedly; see the console and {folder.name}."
        return sentence + (" Replay is open." if replay_open else "")
    return "Ended: the run stopped unexpectedly; see the console."


def countdownText(first: int, left: int) -> str:
    """The countdown status line when `left` steps remain of `first` (spec 5.2, choice 4): only the last five counts
    so far are shown, so a countdown of up to 60 still fits the line."""
    counts = [str(n) for n in range(first, left - 1, -1)][-5:]
    ending = ". F12 stops." if left == 1 else " ... F12 stops."
    return "Click the game now. Starting in " + " ... ".join(counts) + ending


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
NONE_TEXT = "(none)"  # the Map and Class line before a map is loaded or a character is chosen
RECORDING_TEXT = f"Recording: on (about {session.RECORD_GB_PER_HOUR:.1f} GB per hour)"  # spec 5.2; information only
# spec 5.2's run states. Connecting's text is also the session's own `status` event (C1).
CONNECTING_TEXT = "Connecting to the board..."
RUNNING_TEXT = "Running. F12 or Stop ends the run. Clicking this app also stops it."
STOPPING_TEXT = "Stopping: releasing keys..."
STOPPING_CONNECT_TEXT = "Stopping: waiting for the board connection attempt to end (up to 30 s)..."

POLL_MS = 100  # the drain's period, and the close handler's (spec 5.5, 5.7)
CLOSE_WAIT_S = 10  # the close handler destroys the window after this even if the run's cleanup has not finished (5.5)
CLOSE_GAVE_UP_TEXT = "The run's cleanup did not finish in 10 s; closing anyway."  # its one console line
RouteState = Literal["not_ready", "ready", "connecting", "countdown", "running", "stopping", "ended"]
_LIVE_STATES: tuple[RouteState, ...] = ("connecting", "countdown", "running", "stopping")  # Start pressed, cleanup not done
_IDLE_STATES: tuple[RouteState, ...] = ("not_ready", "ready", "ended")  # the readiness line is kept up to date here
_STOPPABLE_STATES: tuple[RouteState, ...] = ("connecting", "countdown", "running")

SessionFactory = Callable[[RunSettings, FrameHolder, Callable[[RunEvent], None], Callable[[], bool]], RunSession]


def defaultSession(settings: RunSettings, shared: FrameHolder, on_event: Callable[[RunEvent], None],
                   may_walk: Callable[[], bool]) -> RunSession:
    """The run's session as the app builds it (spec 5.6): its own defaults for everything else, no SIGINT handler."""
    return RunSession(settings, shared, on_event=on_event, may_walk=may_walk, sigint=False)


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
                 frame_checks: bool = True) -> None:
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
        self._frame_checks = frame_checks
        self._head: str | None = None  # git's HEAD, read once by startPolling and handed to every live view and replay
        self._events: queue.Queue[RunEvent] = queue.Queue()  # the session's on_event (the worker's put), drained here
        self._out: Path | None = None  # the run folder of the run in progress or last ended
        self._ended_seen = False  # this run's `ended` event has been drained (the watchdog's test)
        self._countdown_first: int | None = None  # this run's first countdown count (plan choice 4)
        self._written: tuple | None = None  # (inputs, text) of the readiness line's last write (plan choice 8)
        self._closing: tuple[Callable[[], None], float] | None = None  # (destroy, clock at the press) while it waits
        self.state = "not_ready"
        self.view = None
        self.live = None
        self.session: RunSession | None = None  # annotated here: a class-level `session:` would hide the module
        self.worker = None
        self.size_thread = None
        self.app_has_keyboard = threading.Event()  # nothing sets it until RT-9
        self.status_var = tk.StringVar(master=parent)
        self.map_class_var = tk.StringVar(master=parent)
        self.laps_var = tk.StringVar(master=parent, value="")  # empty: until stopped
        self.minutes_var = tk.StringVar(master=parent, value="")  # empty: no limit
        self.countdown_var = tk.StringVar(master=parent, value="5")
        self._build(parent)
        self._refreshReadiness()

    def _build(self, parent: tk.Misc) -> None:
        """The widgets of spec 5.2: the top area, the saved-runs area at the bottom and the container the view will be
        hosted in between them. No dialog, no Toplevel, no Combobox (spec 5.12)."""
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
        self.refresh_button = tk.Button(header, text="Refresh")  # RT-10 gives these two their commands
        self.refresh_button.pack(side=tk.RIGHT)
        self.open_button = tk.Button(header, text="Open")
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
        written = ((fields, csv, character, self._gui.minimap_canvas is not None), text)
        if written != self._written:
            self._written = written
            self.status_var.set(text)
            self.state = "not_ready" if problem else "ready"

    def startPolling(self) -> None:
        """Read git's HEAD once and start the 100 ms drain. Only `MapleGUI.start` calls it, so a test that builds the
        window never runs git or schedules a drain."""
        self._head = self._git_head()
        self._gui.gui.after(POLL_MS, self.drainEvents)

    def drainEvents(self) -> None:
        """One 100 ms step on the Tk thread (spec 5.5, 5.7): the run's queued events, then the watchdog, then the
        readiness refresh (only with no worker and an idle state). It reschedules itself in a `finally`, so a step
        that raises does not end the loop.

        The watchdog (spec 5.5): a worker found dead is read as ended only after one more drain of the queue finds no
        `ended`, so an `ended` put between the first drain and `is_alive()` is still read as itself. A dead worker
        gives the controls back, whether its run ended with an `ended` event or not."""
        try:
            self._drainQueue()
            worker = self.worker
            if worker is not None and not worker.is_alive():
                self._drainQueue()
                if not self._ended_seen:
                    self.state = "ended"
                    replay_open = self._onEnded(None, "")
                    self.status_var.set(_unexpectedEndText(self._out, replay_open))
                self.worker = None
                self._lockControls(False)
            if self.worker is None and self.state in _IDLE_STATES:
                self._refreshReadiness()
        finally:
            self._gui.gui.after(POLL_MS, self.drainEvents)

    def _drainQueue(self) -> None:
        """Show every event queued so far, in order."""
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            self._showEvent(event)

    def _showEvent(self, event: RunEvent) -> None:
        """One event to spec 5.2's state and status line. A `warning` is appended after one space in any state
        (choice 5). `ended` sets the state, asks `_onEnded` whether a replay opened, then writes `endedText` (choice
        7). While Stopping its text stays until the end: an earlier `status` or `countdown` read late changes nothing,
        and a `running` still reaches `_onRunning` (the walk did start)."""
        if event.kind == "warning":
            self.status_var.set(f"{self.status_var.get()} {event.text}")
        elif event.kind == "ended":
            self._ended_seen = True
            self.state = "ended"
            replay_open = self._onEnded(event.code, event.text)
            self.status_var.set(endedText(event.code, event.text, self._out, replay_open))
        elif self.state == "stopping":
            if event.kind == "running":
                self._onRunning()
        elif event.kind == "status":
            self.state = "connecting"
            self.status_var.set(event.text)
        elif event.kind == "countdown":
            left = event.seconds_left if event.seconds_left is not None else 0
            first = self._countdown_first if self._countdown_first is not None else left
            self._countdown_first = first
            self.state = "countdown"
            self.status_var.set(countdownText(first, left))
        elif event.kind == "running":
            self.state = "running"
            self.status_var.set(RUNNING_TEXT)
            self._onRunning()

    def _onRunning(self) -> None:
        """Called once per drained `running`, after the state and the status line are set. RT-8b hosts the live view
        here; in RT-8a it does nothing."""

    def _onEnded(self, code: int | None, text: str) -> bool:
        """Called once per end (an `ended` event's code and text, or `(None, "")` from the watchdog), after the state
        is set and before the status line; True when a replay opened (choice 7). RT-8b opens the replay here; in RT-8a
        none opens."""
        return False

    def isRunLive(self) -> bool:
        """Start pressed and the cleanup not finished: the Setup tab's picture is not redrawn while this holds and the
        Route tab is in front (spec 5.7)."""
        return self.state in _LIVE_STATES

    def onAppClose(self, destroy: Callable[[], None]) -> None:
        """The window's close button (spec 5.5 "Closing the app mid-run"). With no worker the window goes at once.
        With one: `session.stop("window_close")`, "Stopping: releasing keys..." with nothing enabled, then a close
        poll every 100 ms (`_pollClose`) until the worker has ended or 10 s have passed. A second press while it waits
        does nothing, so there is one poll loop."""
        if self._closing is not None:
            return
        if self.worker is None:
            self._closeWindow(destroy)
            return
        self._closing = (destroy, self._clock())
        if self.session is not None:
            self.session.stop("window_close")  # first call wins: after Stop or an end it changes nothing
        self.state = "stopping"
        self.status_var.set(STOPPING_TEXT)
        self.stop_button.configure(state="disabled")
        self._gui.gui.after(POLL_MS, self._pollClose)

    def _pollClose(self) -> None:
        """One close poll: the worker gone (dead, or already cleared by a drain) → the window goes; still alive after
        `CLOSE_WAIT_S` → one console line, and the window goes anyway (the worker is a daemon and dies with the
        process); else the next poll in 100 ms."""
        assert self._closing is not None  # only onAppClose schedules it
        destroy, pressed = self._closing
        worker = self.worker
        if worker is not None and worker.is_alive():
            if self._clock() - pressed < CLOSE_WAIT_S:
                self._gui.gui.after(POLL_MS, self._pollClose)
                return
            print(CLOSE_GAVE_UP_TEXT)
        self._closeWindow(destroy)

    def _closeWindow(self, destroy: Callable[[], None]) -> None:
        """The view first, then its host (RT-4 m3: `RouteViewWindow.destroy()` after its host is gone raises
        TclError): the live view's polling stops, the view goes, then `destroy()`, which runs even if the view
        raised."""
        try:
            if self.live is not None:
                self.live.stop()
            if self.view is not None:
                self.view.destroy()
        finally:
            destroy()

    # -- Start (spec 5.4) ---------------------------------------------------------------------------------------------

    def startRun(self) -> None:
        """The Start button: the field checks, then the six checks of spec 5.4 in order, stopping at the first that
        fails and writing its text to the status line (never a dialog). With all of them passed it builds the run's
        session from the settings, locks the controls, shows Connecting and starts the worker. A press while a worker
        exists (Start greyed out but pressed anyway, or pressed again while Stopping) does nothing: one worker at most.
        Nor does one while the window waits to close (a drain may give Start back before the close poll runs)."""
        if self.worker is not None or self._closing is not None:
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
            shared, self._events.put, self._mayWalk)
        self.session = run
        self._out = out
        self._ended_seen = False
        self._countdown_first = None
        self._lockControls(True)
        self.state = "connecting"
        self.status_var.set(CONNECTING_TEXT)
        self.worker = self._make_thread(target=self._work, args=(run,), name="route-run", daemon=True)
        self.worker.start()

    def _work(self, run: RunSession) -> None:
        """The worker thread's body (spec 5.7): the walk and nothing that touches Tk; its events reach the tab through
        the queue. `run()` turns an exception into its own `ended` event (5.6); one that still escapes is printed here,
        so it never reaches `threading.excepthook`, and the drain's watchdog ends the run on the tab."""
        try:
            run.run()
        except BaseException:
            traceback.print_exc()

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
        """The session's `may_walk` (spec 9): the walk goes on unless the app has the keyboard; RT-9 sets the flag."""
        return not self.app_has_keyboard.is_set()
