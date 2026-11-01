"""The Route tab (spec 5.2-5.5, contract C5 of docs/superpowers/plans/2026-10-31-route-tab.md).

This module is `src.gui`'s one door to the run driver: it imports `src.route.session`, which reaches the input layer
(import-linter contract "gui reaches input only through the run driver"). The pure helpers below hold the words and the
rules the tab shows (settings checks, the class key, the end-of-run texts, the countdown line); none of them touches Tk.
`RouteTab` is the tab itself: the top area, the readiness line and Start up to the point where the run's session is
built. The worker, the event queue, Stop and the close wait (RT-8a), the hosted view (RT-8b), the app-focus flag (RT-9)
and the saved-run list (RT-10) are added to it by those tasks.
"""
import math
import queue
import re
import threading
import time
import tkinter as tk
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

POLL_MS = 100  # the drain's period (spec 5.5, 5.7)
RouteState = Literal["not_ready", "ready", "connecting", "countdown", "running", "stopping", "ended"]
_LIVE_STATES: tuple[RouteState, ...] = ("connecting", "countdown", "running", "stopping")  # Start pressed, cleanup not done
_IDLE_STATES: tuple[RouteState, ...] = ("not_ready", "ready", "ended")  # the readiness line is kept up to date here

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
        self._events: queue.Queue[RunEvent] = queue.Queue()  # the session's on_event; RT-8a drains it
        self._written: tuple | None = None  # (inputs, text) of the readiness line's last write (plan choice 8)
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
        self.stop_button = tk.Button(buttons, text="Stop", state="disabled")  # RT-8a gives it its command
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
        """One 100 ms step; it reschedules itself. RT-7: the readiness refresh while idle. RT-8a adds the queue
        drain and the dead-worker watchdog before it."""
        if self.state in _IDLE_STATES:
            self._refreshReadiness()
        self._gui.gui.after(POLL_MS, self.drainEvents)

    def isRunLive(self) -> bool:
        """Start pressed and the cleanup not finished: the Setup tab's picture is not redrawn while this holds and the
        Route tab is in front (spec 5.7)."""
        return self.state in _LIVE_STATES

    def onAppClose(self, destroy: Callable[[], None]) -> None:
        """The window's close button. RT-7 has no run to wait for, so the window goes at once; RT-8a makes it cancel a
        live run and wait for the cleanup."""
        destroy()

    # -- Start (spec 5.4) ---------------------------------------------------------------------------------------------

    def startRun(self) -> None:
        """The Start button: the field checks, then the six checks of spec 5.4 in order, stopping at the first that
        fails and writing its text to the status line (never a dialog). With all of them passed it builds the run's
        session from the settings. RT-8a continues from there: lock the controls, show "Connecting to the board..."
        and start the worker."""
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
        self.session = self._make_session(
            RunSettings(map_csv=csv, character=key, laps=laps, minutes=minutes, countdown_s=countdown, seed=None,
                        out=out),
            shared, self._events.put, self._mayWalk)

    def _mayWalk(self) -> bool:
        """The session's `may_walk` (spec 9): the walk goes on unless the app has the keyboard; RT-9 sets the flag."""
        return not self.app_has_keyboard.is_set()
