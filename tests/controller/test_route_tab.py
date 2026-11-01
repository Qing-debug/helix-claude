"""The Route tab (spec 5.2-5.5, contract C5): the pure helpers (settings validation, class key, run texts, countdown
text), then the tab itself on `make_gui`'s withdrawn root, built on fakes: no board, no game, no git, no disk probe.

Same real-Tk-root setup as test_gui_startup.py (skipped where Tk cannot open a display; `after` only records)."""
import gc
import inspect
import sys
import threading
import tkinter as tk
import weakref
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
import types
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from returns.result import Failure

import src.gui.gui as gui_module
import src.gui.live_minimap as live_minimap_module
import src.gui.route_tab as route_tab_module
import test_gui_character_buttons
import test_gui_startup
import tk_bans
from src.classes.classes import Kanna
from src.gui.live_minimap import LiveMinimap
from src.gui.route_tab import RouteTab, classKey, countdownText, defaultSession, endedText, validateSettings
from src.route import session
from src.route.session import RECORD_GB_PER_HOUR, START_FREE_BYTES, RunEvent, RunSession, RunSettings
from src.route.step_log import SNAPSHOT, STEPS_FILE
from src.route_view.follow import LiveController
from src.route_view.model import load_folder
from src.route_view.window import RouteViewWindow
from test_gui_startup import _console, _frame, _open_window, _shared

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "route_view"))  # the route view's flat run-folder helpers
import view_runs  # noqa: E402

make_gui = test_gui_startup.make_gui  # the fixture, shared (no conftest.py under tests/)

LAPS_ERROR = "Laps must be a whole number of 1 or more"
MINUTES_ERROR = f"Minutes must be a number above 0 and at most {threading.TIMEOUT_MAX / 60:g}"
COUNTDOWN_ERROR = "Countdown must be a whole number of seconds from 1 to 60"
RUN_NAME = "2026-10-31T140231Z"


@pytest.mark.parametrize("laps, minutes, countdown, expected", [
    ("", "", "5", (None, None, 5)),
    (" 2 ", "1.5", "60", (2, 1.5, 60)),
    ("1", " 0.5 ", " 1 ", (1, 0.5, 1)),
    ("", repr(threading.TIMEOUT_MAX / 60), "5", (None, threading.TIMEOUT_MAX / 60, 5)),  # "at most": the top is valid
    ("007", "2.", "05", (7, 2.0, 5)),  # leading zeros; a point with no digits after it (RT-7-S1 M2)
    ("", ".5", "5", (None, 0.5, 5)),
])
def test_validate_settings_accepts(laps, minutes, countdown, expected):
    assert validateSettings(laps, minutes, countdown) == (expected, None)


# "\uff12" fullwidth 2, "\u0663" Arabic-Indic 3; past Python's 4300-digit int() limit is refused, not raised (RT-7-S1 M1)
@pytest.mark.parametrize("laps", ["0", "-1", "1.5", "abc", "1_0", "+2", "²", "\uff12", "\u0663", pytest.param("9" * 4301, id="9x4301"),
                                  pytest.param("0" * 4300 + "1", id="0x4300+1")])
def test_validate_settings_table_laps(laps):
    assert validateSettings(laps, "", "5") == (None, LAPS_ERROR)


@pytest.mark.parametrize("minutes", ["0", "-1", "abc", "nan", "inf", "1e400", "1_0", str(threading.TIMEOUT_MAX / 60 * 1.01),
                                     "+2", "1e1", "1e-300", "\uff12", "\u0663", "."])  # ASCII digits, one point (M2)
def test_validate_settings_table_minutes(minutes):
    assert validateSettings("", minutes, "5") == (None, MINUTES_ERROR)


@pytest.mark.parametrize("countdown", ["0", "61", "", "2.5", "-3", "abc", "+5", "\uff15", pytest.param("9" * 4301, id="9x4301")])
def test_validate_settings_table_countdown(countdown):
    assert validateSettings("", "", countdown) == (None, COUNTDOWN_ERROR)


def test_validate_settings_names_the_first_bad_field():
    assert validateSettings("0", "abc", "0") == (None, LAPS_ERROR)
    assert validateSettings("1", "abc", "0") == (None, MINUTES_ERROR)


def test_class_key_is_kanna():
    assert classKey(Kanna()) == "kanna"
    assert classKey(object()) is None


@pytest.fixture
def folder(tmp_path):
    made = tmp_path / RUN_NAME
    made.mkdir()
    return made


@pytest.mark.parametrize("code, text, expected", [
    (0, "max_laps/", "Ended: all laps done."),  # the natural end: no trigger
    (0, "max_laps/hotkey", "Ended: all laps done."),  # a stop that lands as the last lap ends still did the laps
    (0, "max_laps/button", "Ended: all laps done."),
    (0, "stopped/timer", "Ended: the time limit was reached."),
    (0, "stopped/hotkey", "Ended: you pressed F12."),
    (0, "stopped/button", "Ended: you pressed Stop."),
    (0, "stopped/app_focus", "Stopped: you clicked the app. Press Start to run again."),
    (0, "cancelled/app_focus", "Stopped: you clicked the app. Press Start to run again."),
    (0, "stopped/window_close", ""),
    (0, "cancelled/window_close", ""),
    (0, "cancelled/hotkey", "Ended: cancelled before the walk (you pressed F12)."),
    (0, "cancelled/button", "Ended: cancelled before the walk (you pressed Stop)."),
    (0, "cancelled/countdown_focus",
     "Not started: click the game first. Press Start, then click the game before the countdown ends."),
    (0, "none/", "Ended (none/)."),  # choice 3's "none" comes only with codes 2-5 and 9, which map by code
    (0, "stopped/", "Ended (stopped/)."),
    (0, "xyz/abc", "Ended (xyz/abc)."),
    (11, "none/", "Ended (exit 11, none/)."),  # a non-zero code no producer sends: the code is shown (RT-7-S1 S1-a)
    (8, "stopped/hotkey", "Ended (exit 8, stopped/hotkey)."),
])
def test_ended_text_table(code, text, expected):
    assert endedText(code, text, None) == expected


@pytest.mark.parametrize("code, expected", [
    (1, "Ended: the board link was lost."),
    (2, "Could not start: the run folder could not be written."),
    (3, "Could not start: no minimap frame or character found."),
    (4, "Could not start: the board did not connect."),
    (5, "Could not start: the map, route or character icon did not load."),
    (9, "Could not start: the F12 stop key could not be installed."),
    (10, "Ended: the character was lost (exit 10)."),
])
@pytest.mark.parametrize("text", ["none/", "max_laps/", "stopped/hotkey", "link_down/", "anything"])
def test_ended_text_maps_a_nonzero_code_first(code, expected, text, folder):
    assert endedText(code, text, folder) == expected


@pytest.mark.parametrize("code", [6, 7])
@pytest.mark.parametrize("text", ["none/", "stopped/hotkey", "x"])
def test_ended_text_walker_failed_names_the_folder_once(code, text, folder):
    assert endedText(code, text, folder) == f"Ended: the walker failed; see {folder.name}."


@pytest.mark.parametrize("code, text", [
    (0, "stopped/hotkey"), (0, "stopped/button"), (0, "stopped/timer"), (0, "max_laps/"), (0, "max_laps/hotkey"),
    (0, "cancelled/hotkey"), (0, "cancelled/button"), (1, "link_down/"), (10, "off_graph/"), (10, "no_position/"),
    (0, "stopped/app_focus"), (0, "cancelled/app_focus"), (0, "cancelled/countdown_focus"), (0, "stopped/window_close"),
    (0, "xyz/abc"), (0, "none/"), (2, "none/"), (3, "none/"), (4, "none/"), (5, "none/"), (9, "none/"), (11, "none/"),
])
def test_ended_texts_never_name_the_folder(code, text, folder):
    """H68 removed choice 7 and ruling orch 110 the folder's name: no text gets the name or " Replay is open.", even
    with a step log in the folder; only spec 5.5's own texts that name it (codes 6 and 7, "stopped unexpectedly") do."""
    (folder / "steps.jsonl").write_text("{}\n", encoding="utf-8")
    shown = endedText(code, text, folder)
    assert shown == endedText(code, text, None) and folder.name not in shown and "Replay" not in shown
    assert endedText(0, "stopped/hotkey", folder) == "Ended: you pressed F12."


def test_ended_text_unexpected_stop(folder, tmp_path):
    """Spec 5.5 (H71): the details are in the app's console box, and the text names it so."""
    error = "the run stopped unexpectedly: RuntimeError: x"
    alone = "Ended: the run stopped unexpectedly: RuntimeError: x. See the console box on the Setup tab."
    assert endedText(None, error, folder) == ("Ended: the run stopped unexpectedly: RuntimeError: x. See the console box "
                                              f"on the Setup tab and {folder.name}.")
    gone = tmp_path / "never_made"  # an exception before the leg log leaves no folder
    assert endedText(None, error, gone) == alone
    assert endedText(None, error, None) == alone


@pytest.mark.parametrize("code", [6, 7])
def test_ended_text_walker_failed_without_a_folder(code):
    """Spec 5.5 (H71): codes 6 and 7 with no run folder point at the console box."""
    assert endedText(code, "error/", None) == "Ended: the walker failed; see the console box on the Setup tab."


def test_countdown_text():
    assert countdownText(5, 5) == "Click the game now. Starting in 5 ... F12 stops."
    assert countdownText(5, 1) == "Click the game now. Starting in 5 ... 4 ... 3 ... 2 ... 1. F12 stops."
    assert countdownText(5, 3) == "Click the game now. Starting in 5 ... 4 ... 3 ... F12 stops."
    # a long countdown shows only the last five counts so far (choice 4)
    assert countdownText(60, 50) == "Click the game now. Starting in 54 ... 53 ... 52 ... 51 ... 50 ... F12 stops."
    assert countdownText(60, 1) == countdownText(5, 1)


# ---- the tab ------------------------------------------------------------------------------------------------------

READY = "Ready. Press Start, then click the game."
NO_MAP = "Load a map on the Setup tab first."
NO_CLASS = "Choose a character on the Setup tab first."
NO_KEY = "This character cannot walk a route yet."
EDITOR_OPEN = "Close the map editor before Start."
NO_MINIMAP = ("The minimap has not been found yet. Show the game with its minimap on the captured monitor "
              "(the second monitor, not the main display).")
NO_CHARACTER = ("The character was not found on the minimap. Check that the character is on the minimap, then press "
                "Start again.")
RUN_FOLDER_EXISTS = "A run folder for this second already exists. Press Start again."
DRIVE_TEXT = (f"Less than {START_FREE_BYTES / 2**30:g} GB free on {{drive}}. "
              "Delete old runs in data/route, then press Start.")
HEAD = "0123abc"
# spec 5.5's watchdog texts (H71): the console box, and the run folder when it exists
UNEXPECTED = "Ended: the run stopped unexpectedly; see the console box on the Setup tab."
UNEXPECTED_AND = "Ended: the run stopped unexpectedly; see the console box on the Setup tab and {}."


class UnkeyedClass(Kanna):
    """A class `session.CLASSES` has no key for (classKey counts the exact class)."""


def _boom(*args):
    raise AssertionError("called before Start")


class FakeSession:
    """What the fake `make_session` builds: the factory's five arguments (`log` is the tab's line logger, RT-8c piece
    4); `run()` posts `script` through `on_event`, then raises `raises` when set; `stop(source)` only records."""

    def __init__(self, settings, shared, on_event, may_walk, log):
        self.settings, self.shared, self.on_event, self.may_walk, self.log = settings, shared, on_event, may_walk, log
        self.script, self.raises, self.stops = [], None, []

    def run(self):
        for event in self.script:
            self.on_event(event)
        if self.raises is not None:
            raise self.raises
        return 0

    def stop(self, source):
        self.stops.append(source)


class FakeThread:
    """The fake `make_thread`'s product: `start()` runs nothing but notes whether automatic garbage collection was on;
    `finish()` runs the worker's body on the test's thread and leaves the thread dead; `alive_check`, when set,
    answers `is_alive()` instead."""

    def __init__(self, *, target, args=(), name=None, daemon=None):
        self.target, self.args, self.name, self.daemon = target, args, name, daemon
        self.started, self.alive, self.alive_check = 0, False, None
        self.collector_on_at_start = None

    def start(self):
        self.started += 1
        self.alive = True
        self.collector_on_at_start = gc.isenabled()

    def is_alive(self):
        return self.alive_check() if self.alive_check is not None else self.alive

    def finish(self):
        self.target(*self.args)
        self.alive = False


@pytest.fixture
def make_rig(make_gui, monkeypatch, tmp_path):
    """`make(frame=True, drain=True, cls=RouteTab, **keywords)`: a real window with a `cls` tab on fakes (`keywords`
    replace the defaults: `make_session`, `make_thread`, `run_root`, `disk_free`, `new_out`, `git_head`, ...), a map and
    a class chosen, the minimap area built (`frame=False`: no frame, so no minimap area) and the character found, after
    one drain. So the tab is Ready, and a test breaks one thing. `rig.sessions` records the `FakeSession`s the fake
    `make_session` built and `rig.threads` the `FakeThread`s; `rig.free` is what the default `disk_free` reports."""
    def make(*, frame=True, drain=True, cls=RouteTab, **keywords):
        run_root = tmp_path / "route"
        sessions, threads = [], []

        def fake_session(settings, shared, on_event, may_walk, log):
            made = FakeSession(settings, shared, on_event, may_walk, log)
            sessions.append(made)
            return made

        def fake_thread(**options):
            made = FakeThread(**options)
            threads.append(made)
            return made

        rig = SimpleNamespace(sessions=sessions, threads=threads, run_root=run_root, out=run_root / RUN_NAME,
                              csv=tmp_path / "fossil_foundry_2.csv", free=10 * START_FREE_BYTES)
        options = dict(make_session=fake_session, make_thread=fake_thread, run_root=run_root,
                       disk_free=lambda path: rig.free, new_out=lambda: rig.out, git_head=lambda: HEAD)
        options.update(keywords)
        built = []

        def factory(parent, owner):
            built.append(cls(parent, owner, **options))
            return built[0]

        gui, shared, scheduled = make_gui(frame=_frame() if frame else None, route_tab_factory=factory)
        rig.gui, rig.tab, rig.shared, rig.scheduled = gui, built[0], shared, scheduled
        rig.scheduled_by_the_build = list(scheduled)
        agent = gui.controller.agent
        agent.CSVParser.CSV_file = rig.csv
        agent.characterClass = Kanna()
        monkeypatch.setattr(gui, "_findCharacter", lambda: (10, 10))
        gui._updateCanvas()  # builds the minimap area when there is a frame
        if drain:
            rig.tab.drainEvents()
        return rig

    return make


@pytest.fixture(autouse=True)
def _collector_back_on():
    """Start turns automatic garbage collection off until the drain clears the worker (RT-8a I1); a test that leaves
    its fake worker alive must not leave it off, or the objects Start froze frozen, for the tests after it."""
    yield
    gc.unfreeze()
    gc.enable()


MAIN = threading.main_thread().name  # the test's thread stands for the Tk thread


@pytest.fixture
def gc_calls(monkeypatch):
    """Every `gc.collect`, `gc.disable`, `gc.enable`, `gc.freeze` and `gc.unfreeze` call during the test as (name,
    thread name); the real function still runs."""
    calls = []
    for name in ("collect", "disable", "enable", "freeze", "unfreeze"):
        def spy(*args, _real=getattr(gc, name), _name=name, **kwargs):
            calls.append((_name, threading.current_thread().name))
            return _real(*args, **kwargs)
        monkeypatch.setattr(gc, name, spy)
    return calls


def _toplevels(gui):
    return [w for w in gui.gui.winfo_children() if isinstance(w, tk.Toplevel)]


def _break(rig, what):
    """Break one thing in a Ready rig."""
    gui, agent = rig.gui, rig.gui.controller.agent
    if what == "no_map":
        agent.CSVParser.CSV_file = None
    elif what == "no_class":
        agent.characterClass = None
    elif what == "no_key":
        agent.characterClass = UnkeyedClass()
    elif what == "editor_open":
        gui.map_editor = SimpleNamespace(window=SimpleNamespace(winfo_exists=lambda: True))
    elif what == "no_character":
        gui._findCharacter = lambda: None
    elif what == "no_disk":
        rig.free = START_FREE_BYTES - 1
    elif what == "folder_exists":
        rig.out.mkdir(parents=True)
        (rig.out / "session.json").write_text("{}")  # a run that already wrote its folder this second
    else:
        raise AssertionError(what)


def test_default_factory_builds_a_route_tab(make_gui):
    gui, _, _ = make_gui()
    assert isinstance(gui.route_tab, RouteTab)
    assert gui.route_tab_frame.winfo_children()  # the tab's widgets are in the Route tab's frame


def test_constructor_creates_every_c5_attribute_and_touches_nothing_outside_tk(make_rig):
    rig = make_rig(drain=False, git_head=_boom, disk_free=_boom, new_out=_boom, make_session=_boom)
    tab, gui = rig.tab, rig.gui
    assert rig.scheduled_by_the_build == []  # no `after`: only MapleGUI.start's startPolling schedules
    assert gui.route_tab is tab
    assert isinstance(tab.status_var, tk.StringVar) and tab.status_var.get() == NO_MAP
    assert tab.state == "not_ready"
    for button in (tab.start_button, tab.stop_button, tab.open_button, tab.refresh_button):
        assert isinstance(button, tk.Button)
    assert isinstance(tab.runs_list, tk.Listbox) and isinstance(tab.view_frame, tk.Frame)
    assert (tab.laps_var.get(), tab.minutes_var.get(), tab.countdown_var.get()) == ("", "", "5")
    assert (tab.view, tab.live, tab.session, tab.worker, tab.size_thread) == (None, None, None, None, None)
    assert isinstance(tab.app_has_keyboard, threading.Event) and not tab.app_has_keyboard.is_set()
    assert str(tab.stop_button.cget("state")) == "disabled"
    assert isinstance(tab.live_minimap, LiveMinimap)
    assert tab.view_frame.winfo_children() == tab.view_frame.pack_slaves() == [tab.live_minimap.canvas]  # 5.2
    classes = []
    stack = list(gui.route_tab_frame.winfo_children())
    while stack:
        widget = stack.pop()
        classes.append(widget.winfo_class())
        stack.extend(widget.winfo_children())
    assert "TCombobox" not in classes and "Toplevel" not in classes  # spec 5.12: no drop-down, no window


def test_map_and_class_line(make_rig):
    rig = make_rig()
    assert rig.tab.map_class_var.get() == "Map: fossil_foundry_2.csv   Class: Kanna"
    rig.gui.controller.agent.CSVParser.CSV_file = None
    rig.gui.controller.agent.characterClass = None
    rig.tab.drainEvents()
    assert rig.tab.map_class_var.get() == "Map: (none)   Class: (none)"


def test_recording_line(make_rig):
    rig = make_rig()
    assert rig.tab.recording_label.cget("text") == f"Recording: on (about {RECORD_GB_PER_HOUR:.1f} GB per hour)"


def test_ready_and_not_ready_rows(make_rig):
    rig = make_rig()
    tab = rig.tab
    assert tab.status_var.get() == READY and tab.state == "ready"
    assert str(tab.start_button.cget("state")) == "normal"
    assert str(tab.stop_button.cget("state")) == "disabled"

    rig.gui.controller.agent.CSVParser.CSV_file = None
    tab.drainEvents()

    assert tab.status_var.get() == NO_MAP and tab.state == "not_ready"
    assert str(tab.start_button.cget("state")) == "disabled"


@pytest.mark.parametrize("field, value, error", [
    ("laps_var", "0", LAPS_ERROR), ("laps_var", "abc", LAPS_ERROR),
    ("minutes_var", "-1", MINUTES_ERROR), ("minutes_var", "nan", MINUTES_ERROR),
    ("countdown_var", "61", COUNTDOWN_ERROR), ("countdown_var", "", COUNTDOWN_ERROR),
    pytest.param("laps_var", "9" * 4301, LAPS_ERROR, id="laps_var-9x4301"),
    pytest.param("countdown_var", "9" * 4301, COUNTDOWN_ERROR, id="countdown_var-9x4301"),  # no drain raises (M1)
])
def test_an_invalid_field_turns_start_off_and_its_text_is_the_status_line(make_rig, field, value, error):
    rig = make_rig()
    tab = rig.tab
    getattr(tab, field).set(value)

    tab.drainEvents()

    assert tab.status_var.get() == error and tab.state == "not_ready"
    assert str(tab.start_button.cget("state")) == "disabled"
    tab.startRun()  # a press that gets through anyway builds nothing
    assert tab.status_var.get() == error and rig.sessions == []

    getattr(tab, field).set({"laps_var": "", "minutes_var": "", "countdown_var": "5"}[field])
    tab.drainEvents()

    assert tab.status_var.get() == READY and str(tab.start_button.cget("state")) == "normal"


@pytest.mark.parametrize("what, text", [
    ("no_map", NO_MAP), ("no_class", NO_CLASS), ("no_key", NO_KEY), ("editor_open", EDITOR_OPEN),
    ("no_character", NO_CHARACTER), ("no_disk", "disk"), ("folder_exists", RUN_FOLDER_EXISTS),
])
def test_six_refusals(make_rig, what, text):
    rig = make_rig()
    _break(rig, what)
    rig.tab.startRun()

    if text == "disk":
        text = DRIVE_TEXT.format(drive=session.drive_name(rig.run_root))
    assert rig.tab.status_var.get() == text
    assert rig.sessions == []  # a refusal builds no session
    assert _toplevels(rig.gui) == []  # and opens no window


def test_refusal_for_no_minimap(make_rig):
    rig = make_rig(frame=False)
    rig.tab.startRun()
    assert rig.tab.status_var.get() == NO_MINIMAP
    assert rig.sessions == [] and _toplevels(rig.gui) == []


def test_the_disk_check_asks_about_the_run_root(make_rig):
    asked = []

    def disk_free(path):
        asked.append(path)
        return START_FREE_BYTES  # exactly 2 GB passes: the text says "Less than"

    rig = make_rig(disk_free=disk_free)
    rig.tab.startRun()
    assert asked == [rig.run_root] and len(rig.sessions) == 1


def test_checks_stop_at_the_first_failure_in_spec_order(make_rig, monkeypatch):
    rig = make_rig(frame=False)
    gui, agent, tab = rig.gui, rig.gui.controller.agent, rig.tab
    searched = []
    monkeypatch.setattr(gui, "_findCharacter", lambda: searched.append(1))  # None: the character is not found
    _break(rig, "folder_exists")
    _break(rig, "editor_open")
    _break(rig, "no_disk")
    agent.CSVParser.CSV_file = None
    agent.characterClass = None

    def give_a_frame():
        rig.shared.minimap_frame = _frame()
        gui._updateCanvas()

    steps = [
        (NO_MAP, lambda: setattr(agent.CSVParser, "CSV_file", rig.csv)),
        (NO_CLASS, lambda: setattr(agent, "characterClass", UnkeyedClass())),
        (NO_KEY, lambda: setattr(agent, "characterClass", Kanna())),
        (EDITOR_OPEN, lambda: setattr(gui, "map_editor", None)),
        (NO_MINIMAP, give_a_frame),
        (NO_CHARACTER, lambda: monkeypatch.setattr(gui, "_findCharacter", lambda: (1, 1))),
        (DRIVE_TEXT.format(drive=session.drive_name(rig.run_root)), lambda: setattr(rig, "free", START_FREE_BYTES)),
        (RUN_FOLDER_EXISTS, lambda: None),
    ]
    for text, fix in steps:
        tab.startRun()
        assert tab.status_var.get() == text
        assert rig.sessions == []
        fix()
    assert searched == [1]  # the character search ran once: check 4, only after checks 1-3 passed


def test_settings_reach_the_session(make_rig):
    rig = make_rig()
    tab = rig.tab
    tab.laps_var.set(" 2 ")
    tab.countdown_var.set("5")

    tab.startRun()

    assert len(rig.sessions) == 1 and tab.session is rig.sessions[0]
    made = rig.sessions[0]
    assert made.settings == RunSettings(map_csv=rig.csv, character="kanna", laps=2, minutes=None, countdown_s=5,
                                        seed=None, out=rig.out)
    assert made.shared is rig.gui.sharedInfo  # the capture thread's frame holder, handed to the run as it is
    assert callable(made.on_event)


def test_minutes_and_countdown_reach_the_session(make_rig):
    rig = make_rig()
    rig.tab.minutes_var.set("1.5")
    rig.tab.countdown_var.set("60")
    rig.tab.startRun()
    settings = rig.sessions[0].settings
    assert (settings.laps, settings.minutes, settings.countdown_s) == (None, 1.5, 60)


def test_may_walk_reads_only_the_flag(make_rig, monkeypatch):
    """Spec 9: the factory's `may_walk` is `not app_has_keyboard.is_set()`; the worker calls it, so it makes no Tk call
    (a Tk focus read off the Tk thread raises here)."""
    rig = make_rig()
    rig.tab.startRun()
    may_walk = rig.sessions[0].may_walk
    tk_thread = threading.current_thread()
    real_focus_get = tk.Misc.focus_get

    def focus_get_on_the_tk_thread_only(self):
        if threading.current_thread() is not tk_thread:
            raise AssertionError("focus_get called off the Tk thread")
        return real_focus_get(self)

    monkeypatch.setattr(tk.Misc, "focus_get", focus_get_on_the_tk_thread_only)
    answers, errors = [], []

    def read_on_a_worker():
        try:
            answers.append(may_walk())
        except BaseException as error:
            errors.append(error)

    for flag_set in (True, False, True):
        if flag_set:
            rig.tab.app_has_keyboard.set()
        else:
            rig.tab.app_has_keyboard.clear()
        assert may_walk() is (not flag_set)
        worker = threading.Thread(target=read_on_a_worker)
        worker.start()
        worker.join(5)
    assert errors == [] and answers == [False, True, False]


@pytest.mark.parametrize("focused, has_keyboard", [(True, True), (False, False)])
def test_start_reads_the_flag_once_from_focus_get(make_rig, monkeypatch, focused, has_keyboard):
    """Spec 9: at Start the flag is read once from Tk's focus record; Start is no rise that stops the run."""
    rig = make_rig()
    tab = rig.tab
    reads = []

    def focus_get():
        reads.append(tab.state)
        return tab.start_button if focused else None

    if not focused:
        tab.app_has_keyboard.set()  # a stale set flag is cleared by the read
    monkeypatch.setattr(rig.gui.gui, "focus_get", focus_get)
    made, _ = _start(rig)
    assert reads == ["ready"]  # once, before Connecting
    assert tab.app_has_keyboard.is_set() is has_keyboard
    assert made.may_walk() is (not has_keyboard)
    assert made.stops == [] and tab.state == "connecting"


def test_frame_checks_off_skips_the_minimap_and_character_checks(make_rig, monkeypatch):
    rig = make_rig(frame=False, frame_checks=False)
    monkeypatch.setattr(rig.gui, "_findCharacter", _boom)
    assert rig.gui.minimap_canvas is None
    assert rig.tab.status_var.get() == READY  # no minimap and still Ready
    rig.tab.startRun()
    assert len(rig.sessions) == 1
    assert _console(rig.gui).strip() == ""  # `_minimapReady()` was not asked either


@pytest.mark.parametrize("what", ["no_character", "no_disk", "folder_exists"])
def test_refusal_text_survives_the_next_drain(make_rig, what):
    rig = make_rig()
    tab = rig.tab
    assert tab.status_var.get() == READY
    _break(rig, what)
    tab.startRun()
    refusal = tab.status_var.get()
    assert refusal != READY

    tab.drainEvents()
    tab.drainEvents()

    assert tab.status_var.get() == refusal
    assert str(tab.start_button.cget("state")) == "normal"  # checks 1-3 still pass: Start stays usable

    tab.laps_var.set("3")
    tab.drainEvents()

    assert tab.status_var.get() == READY


def test_ended_text_survives_the_next_drain(make_rig):
    rig = make_rig()
    tab = rig.tab
    tab.state = "ended"
    tab.status_var.set("Ended: you pressed F12.")

    tab.drainEvents()
    tab.drainEvents()

    assert tab.status_var.get() == "Ended: you pressed F12." and tab.state == "ended"
    assert str(tab.start_button.cget("state")) == "normal"

    rig.gui.controller.agent.CSVParser.CSV_file = rig.csv.with_name("other.csv")  # the map changes
    tab.drainEvents()

    assert tab.status_var.get() == READY and tab.state == "ready"


def test_editor_opening_and_closing_changes_the_readiness_text(make_rig):
    rig = make_rig()
    _break(rig, "editor_open")
    rig.tab.drainEvents()
    assert rig.tab.status_var.get() == EDITOR_OPEN and str(rig.tab.start_button.cget("state")) == "disabled"
    rig.gui.map_editor = None
    rig.tab.drainEvents()
    assert rig.tab.status_var.get() == READY


def test_readiness_does_not_print_to_the_console(make_rig):
    rig = make_rig(frame=False)
    assert rig.gui.minimap_canvas is None
    for _ in range(10):
        rig.tab.drainEvents()
    assert _console(rig.gui).strip() == ""
    assert rig.tab.status_var.get() == NO_MINIMAP

    rig.tab.startRun()  # Start calls `_minimapReady()`, which says so in the Setup console once

    assert _console(rig.gui).count(gui_module.MINIMAP_NOT_FOUND_MESSAGE) == 1
    assert rig.tab.status_var.get() == NO_MINIMAP


def test_drain_reschedules_itself_and_polling_reads_git_once(make_rig):
    heads = []

    def git_head():
        heads.append(1)
        return HEAD

    rig = make_rig(git_head=git_head)
    tab, scheduled = rig.tab, rig.scheduled
    assert heads == []  # the constructor and the first drains never ask git

    tab.startPolling()

    assert heads == [1] and tab._head == HEAD
    assert scheduled[-1] == (100, tab.drainEvents)

    scheduled.clear()
    tab.drainEvents()
    tab.drainEvents()

    assert scheduled == [(100, tab.drainEvents)] * 2
    assert heads == [1]  # git ran once, not per drain


def test_is_run_live_follows_the_state(make_rig):
    tab = make_rig().tab
    for state, live in [("not_ready", False), ("ready", False), ("connecting", True), ("countdown", True),
                        ("running", True), ("stopping", True), ("ended", False)]:
        tab.state = state
        assert tab.isRunLive() is live, state


def test_updating_the_setup_canvas_asks_the_tab_if_a_run_is_live(make_rig):
    rig = make_rig()
    rig.gui._updateCanvas()  # calls route_tab.isRunLive(); an AttributeError here is what RT-6's guard would hide
    assert rig.gui.minimap_canvas is not None


def test_mapleGUI_start_starts_the_tabs_polling_before_the_mainloop(monkeypatch):
    events = []

    class Spy:
        def __init__(self, parent, owner):
            events.append("built")

        def startPolling(self):
            events.append("startPolling")

        def isRunLive(self):
            return False

    gui = _open_window(_shared(), route_tab_factory=Spy)
    try:
        gui.gui.withdraw()
        monkeypatch.setattr(gui.gui, "after", lambda ms, func: None)
        monkeypatch.setattr(gui.gui, "mainloop", lambda: events.append("mainloop"))

        gui.start()

        assert events == ["built", "startPolling", "mainloop"]
    finally:
        gui.gui.destroy()


def test_mapleGUI_start_with_no_route_tab(monkeypatch):
    gui = _open_window(_shared(), route_tab_factory=None)
    try:
        gui.gui.withdraw()
        monkeypatch.setattr(gui.gui, "after", lambda ms, func: None)
        monkeypatch.setattr(gui.gui, "mainloop", lambda: None)
        gui.start()
        assert gui.route_tab is None
    finally:
        gui.gui.destroy()


# ---- RT-8a: the worker, the event queue, the watchdog and Stop (spec 5.5, 5.7) ----------------------------------

CONNECTING = "Connecting to the board..."
RUNNING = "Running. F12 or Stop ends the run. Clicking this app also stops it."
STOPPING = "Stopping: releasing keys..."
STOPPING_CONNECT = "Stopping: waiting for the board connection attempt to end (up to 30 s)..."


def _event(kind, text="", **extra):
    return RunEvent(kind=kind, text=text, **extra)


def _state(widget):
    return str(widget.cget("state"))


def _run_controls(tab):
    """Spec 5.2's Enabled column apart from Start and Stop: the settings and the saved runs."""
    return (tab.laps_entry, tab.minutes_entry, tab.countdown_spinbox, tab.open_button, tab.refresh_button,
            tab.runs_list)


def _start(rig):
    rig.tab.startRun()
    return rig.sessions[-1], rig.threads[-1]


class SpyTab(RouteTab):
    """Records the two hooks RT-8b fills, with the state and the status line as they were at the call."""

    def __init__(self, *args, **kwargs):
        self.calls = []
        super().__init__(*args, **kwargs)

    def _onRunning(self):
        self.calls.append(("running", self.state, self.status_var.get()))

    def _onEnded(self, code, text):
        self.calls.append(("ended", code, text, self.state, self.status_var.get()))


def test_controls_locked_during_run_and_back_after(make_rig, monkeypatch):
    rig = make_rig()
    tab = rig.tab
    locks = []
    lock = rig.gui.setRunLocked
    monkeypatch.setattr(rig.gui, "setRunLocked", lambda locked: (locks.append(locked), lock(locked)))

    made, thread = _start(rig)

    assert locks == [True] and _state(rig.gui.load_map_button) == "disabled"  # the Setup tab's lock ran (5.1)
    assert (thread.target, thread.args, thread.name, thread.daemon) == (tab._work, (made,), "route-run", True)
    assert thread.started == 1 and tab.worker is thread
    assert (tab.state, tab.status_var.get()) == ("connecting", CONNECTING)
    assert [_state(w) for w in _run_controls(tab)] == ["disabled"] * 6
    assert (_state(tab.start_button), _state(tab.stop_button)) == ("disabled", "normal")
    tab.drainEvents()  # readiness gives nothing back during a run
    assert _state(tab.start_button) == "disabled"

    made.on_event(_event("ended", "stopped/button", code=0))
    tab.drainEvents()  # `ended` read while the worker is still returning: the controls wait for the worker's end

    assert (tab.state, tab.status_var.get()) == ("ended", "Ended: you pressed Stop.")
    assert locks == [True] and _state(tab.start_button) == "disabled"

    thread.finish()
    tab.drainEvents()

    assert locks == [True, False] and tab.worker is None
    assert [_state(w) for w in _run_controls(tab)] == ["normal"] * 6
    assert (_state(tab.start_button), _state(tab.stop_button)) == ("normal", "disabled")  # the Ready row's controls
    assert (tab.state, tab.status_var.get()) == ("ended", "Ended: you pressed Stop.")  # the text stays (choice 8)


def test_start_twice_builds_one_session(make_rig):
    rig = make_rig()
    rig.tab.startRun()
    rig.tab.startRun()
    assert len(rig.sessions) == 1 and len(rig.threads) == 1 and rig.threads[0].started == 1
    rig.tab.stopRun()
    rig.tab.startRun()  # Start again while Stopping: still one
    rig.sessions[0].on_event(_event("ended", "cancelled/button", code=0))
    rig.tab.drainEvents()
    rig.tab.startRun()  # and after `ended` while the worker is still returning
    assert len(rig.sessions) == 1 and len(rig.threads) == 1


def test_events_drained_into_the_status_line(make_rig):
    rig = make_rig()
    tab = rig.tab
    made, _ = _start(rig)
    steps = [
        (_event("status", CONNECTING), "connecting", CONNECTING),
        (_event("countdown", seconds_left=5), "countdown", "Click the game now. Starting in 5 ... F12 stops."),
        (_event("countdown", seconds_left=4), "countdown", "Click the game now. Starting in 5 ... 4 ... F12 stops."),
        (_event("running", "Running."), "running", RUNNING),
        (_event("warning", "Recording stopped: x. The walk goes on."), "running",
         RUNNING + " Recording stopped: x. The walk goes on."),  # appended after one space (choice 5)
        (_event("ended", "max_laps/", code=0), "ended", "Ended: all laps done."),
        (_event("warning", "Recording stopped: y. The walk goes on."), "ended",
         "Ended: all laps done. Recording stopped: y. The walk goes on."),  # a late warning, appended to Ended too
    ]
    for event, state, text in steps:
        made.on_event(event)
        tab.drainEvents()
        assert (tab.state, tab.status_var.get()) == (state, text), event


def test_events_queued_together_are_read_in_one_drain(make_rig):
    rig = make_rig()
    made, _ = _start(rig)
    for event in [_event("status", CONNECTING), _event("countdown", seconds_left=2), _event("countdown", seconds_left=1),
                  _event("running", "Running."), _event("warning", "W."), _event("ended", "stopped/timer", code=0)]:
        made.on_event(event)
    rig.tab.drainEvents()
    assert (rig.tab.state, rig.tab.status_var.get()) == ("ended", "Ended: the time limit was reached.")


def test_stop_button(make_rig):
    rig = make_rig()
    tab = rig.tab
    made, _ = _start(rig)

    tab.stop_button.invoke()  # Stop's command; while connecting the connection attempt has to end first (5.6)

    assert made.stops == ["button"]
    assert (tab.state, tab.status_var.get()) == ("stopping", STOPPING_CONNECT)
    assert _state(tab.stop_button) == "disabled"  # Stopping: nothing is enabled (5.2)
    made.on_event(_event("countdown", seconds_left=5))  # put before the stop landed: Stopping's text stays
    made.on_event(_event("warning", "Recording stopped: x. The walk goes on."))
    tab.drainEvents()
    assert (tab.state, tab.status_var.get()) == ("stopping", STOPPING_CONNECT + " Recording stopped: x. The walk goes on.")
    tab.stopRun()  # pressed again while Stopping: nothing more is sent
    assert made.stops == ["button"]


@pytest.mark.parametrize("kind", ["countdown", "running"])
def test_stop_button_after_connecting(make_rig, kind):
    rig = make_rig()
    made, _ = _start(rig)
    made.on_event(_event(kind, seconds_left=3) if kind == "countdown" else _event(kind, "Running."))
    rig.tab.drainEvents()
    assert rig.tab.state == kind

    rig.tab.stopRun()

    assert made.stops == ["button"]
    assert (rig.tab.state, rig.tab.status_var.get()) == ("stopping", STOPPING)


def test_stop_with_no_run_does_nothing(make_rig):
    rig = make_rig()
    rig.tab.stopRun()
    assert rig.tab.state == "ready" and rig.tab.status_var.get() == READY


@pytest.mark.parametrize("folder_made", [True, False])
def test_worker_that_ends_without_ended(make_rig, folder_made):
    rig = make_rig()
    tab = rig.tab
    made, thread = _start(rig)
    if folder_made:
        rig.out.mkdir(parents=True)
    tab.drainEvents()
    assert tab.state == "connecting"  # alive and silent: nothing to show

    thread.finish()  # run() returns with nothing posted
    tab.drainEvents()

    if folder_made:
        assert tab.status_var.get() == UNEXPECTED_AND.format(rig.out.name)
    else:
        assert tab.status_var.get() == UNEXPECTED
    assert tab.state == "ended" and tab.worker is None
    assert (_state(tab.start_button), _state(tab.stop_button)) == ("normal", "disabled")
    assert [_state(w) for w in _run_controls(tab)] == ["normal"] * 6


def test_worker_that_ends_without_ended_race_order(make_rig):
    rig = make_rig(cls=SpyTab)
    tab = rig.tab
    made, thread = _start(rig)
    made.on_event(_event("running", "Running."))
    tab.drainEvents()

    def ended_then_dead():  # alive at the drain; the worker posts `ended` and returns before is_alive() is asked
        made.on_event(_event("ended", "stopped/hotkey", code=0))
        return False

    thread.alive_check = ended_then_dead
    tab.drainEvents()

    assert tab.status_var.get() == "Ended: you pressed F12."  # that event's 5.5 text, not the watchdog's
    assert [call for call in tab.calls if call[0] == "ended"] == [("ended", 0, "stopped/hotkey", "ended", RUNNING)]
    assert tab.worker is None and _state(tab.start_button) == "normal"


def test_worker_wrapper_keeps_exceptions_off_the_thread(make_rig, monkeypatch, capsys):
    """An exception that escapes `run()` is caught on the worker (never `threading.excepthook`) and its traceback goes
    to the app's console box through the tab's line queue, not to the terminal (H71)."""
    hooked = []
    monkeypatch.setattr(threading, "excepthook", hooked.append)

    def raising_session(*args):
        made = FakeSession(*args)
        made.raises = RuntimeError("boom in run")
        return made

    rig = make_rig(make_session=raising_session, make_thread=threading.Thread)  # a real thread this time
    rig.tab.startRun()  # Start's own collection guard keeps the collector off the worker (I1)
    worker = rig.tab.worker
    assert isinstance(worker, threading.Thread) and worker.name == "route-run" and worker.daemon
    worker.join(10)
    assert not worker.is_alive()

    assert capsys.readouterr().err == ""  # nothing on the terminal (H71)
    assert hooked == []
    assert "boom in run" not in _console(rig.gui)  # the worker never touches Tk: the traceback waits for the drain

    rig.tab.drainEvents()

    console = _console(rig.gui)
    assert "Traceback (most recent call last)" in console and "in raising_session" not in console
    assert console.endswith("RuntimeError: boom in run\n\n") and not console.endswith("\n\n\n")  # one line end
    assert rig.tab.state == "ended" and rig.tab.worker is None
    assert rig.tab.status_var.get() == UNEXPECTED
    assert (_state(rig.tab.start_button), _state(rig.tab.stop_button)) == ("normal", "disabled")


class ConsoleSpyTab(RouteTab):
    """Records each console-box line (`_toConsole`) and each run event shown, in the order they happen."""

    def __init__(self, *args, **kwargs):
        self.order = []
        super().__init__(*args, **kwargs)

    def _toConsole(self, text, error):
        self.order.append(("console", text, error))
        super()._toConsole(text, error)

    def _showEvent(self, event):
        self.order.append(("event", event.kind))
        super()._showEvent(event)


def test_run_lines_reach_the_console_box(make_rig):
    """H71, RDO110-D3 A: the session's lines (its `log`, called on the worker or another run thread) wait on the tab's
    line queue, and the drain writes them into the console box in order, before the run events it drains."""
    rig = make_rig(cls=ConsoleSpyTab)
    tab = rig.tab
    made, _thread = _start(rig)
    made.on_event(_event("ended", "error/", code=6))
    poster = threading.Thread(target=lambda: (made.log("walk failed: boom", True), made.log("stopped: error", False)))
    poster.start()
    poster.join()
    before = _console(rig.gui)
    assert "walk failed" not in before  # nothing touches Tk off its thread

    tab.order.clear()
    tab.drainEvents()

    assert tab.order == [("console", "walk failed: boom", True), ("console", "stopped: error", False),
                         ("event", "ended")]
    assert _console(rig.gui) == before.removesuffix("\n") + "walk failed: boom\nstopped: error\n\n"


class LateLineTab(ConsoleSpyTab):
    """Runs `late` once, right after the first `_drainLines` of a step returns: what the worker does between that drain
    and the step's read of the event queue."""

    late = None

    def _drainLines(self):
        super()._drainLines()
        late, self.late = self.late, None
        if late is not None:
            late()


def test_a_line_put_just_before_ended_is_written_before_the_end_shows(make_rig):
    """RDO110-R m2: the worker puts its last line and then `ended`; if both land after the step's line drain but before
    its event read, the line is still written to the box before the `ended` is shown (not a step, 100 ms, later)."""
    rig = make_rig(cls=LateLineTab)
    tab = rig.tab
    made, _thread = _start(rig)
    tab.late = lambda: (made.log("cleanup failed: link.close: RuntimeError: x", True),
                        made.on_event(_event("ended", "error/", code=6)))
    tab.order.clear()

    tab.drainEvents()

    assert tab.order == [("console", "cleanup failed: link.close: RuntimeError: x", True), ("event", "ended")]


def test_lines_below_the_session_reach_the_console_box(make_rig, capsys):
    """RT-8c piece 4b (ruling orch 111): the session hands its error line to the walker, the step log, the stop control
    and run_cleanup (test_session.py pins that); called on a run thread, it reaches the console box as an error line at
    the next drain, and nothing reaches the terminal."""
    rig = make_rig(cls=ConsoleSpyTab, make_session=defaultSession)
    tab = rig.tab
    tab.startRun()
    made = tab.session
    assert isinstance(made, RunSession)
    poster = threading.Thread(target=made._error_line, args=("cleanup failed: link.close: RuntimeError: x",))
    poster.start()
    poster.join()
    assert "cleanup failed" not in _console(rig.gui)  # nothing touches Tk off its thread

    tab.order.clear()
    tab.drainEvents()

    assert tab.order == [("console", "cleanup failed: link.close: RuntimeError: x", True)]
    assert _console(rig.gui).endswith("cleanup failed: link.close: RuntimeError: x\n\n")
    assert capsys.readouterr().err == ""


# Garbage collection during a run (RT-8a I1, ruling RT-8a-review-D1 A): a collection on the worker that frees a Tk
# object would call Tcl from it, so automatic collection is off while a worker lives and every collection runs on the
# Tk thread: one just before the worker starts, one in the drain about every second.

def _end_run(rig, made, thread, how):
    """End a started run one of the ways a run ends."""
    tab = rig.tab
    if how == "ended":
        made.on_event(_event("ended", "max_laps/", code=0))
    elif how == "stop":
        tab.stopRun()
        made.on_event(_event("ended", "stopped/button", code=0))
    elif how == "session_error":
        made.raises = RuntimeError("boom in run")  # escapes run(): `_work` prints it, the watchdog ends the run
    else:
        tab.onAppClose(lambda: None)
    thread.finish()
    if how != "close":
        tab.drainEvents()  # "close_drain_first": the drain clears the worker before the close poll runs
    if how.startswith("close"):
        _run_close_poll(rig)


@pytest.mark.parametrize("how", ["ended", "stop", "session_error", "close", "close_drain_first"])
def test_collector_is_off_during_a_run_and_on_once_after_it(make_rig, gc_calls, capsys, how):
    rig = make_rig()
    made, thread = _start(rig)

    assert gc_calls == [("collect", MAIN), ("disable", MAIN), ("freeze", MAIN)]  # Tk thread, before the worker starts
    assert thread.collector_on_at_start is False and not gc.isenabled()
    assert gc.get_freeze_count() > 0  # what exists at Start is skipped by the drain's collections (ruling orch 108)

    _end_run(rig, made, thread, how)

    assert gc.isenabled() and gc_calls[3:] == [("unfreeze", MAIN), ("collect", MAIN), ("enable", MAIN)]
    assert gc.get_freeze_count() == 0
    rig.tab.drainEvents()
    rig.tab.drainEvents()
    assert gc_calls[3:] == [("unfreeze", MAIN), ("collect", MAIN), ("enable", MAIN)]  # exactly once
    if not how.startswith("close"):
        _, again = _start(rig)  # a later run turns it off again
        assert again.collector_on_at_start is False
        assert gc_calls[6:] == [("collect", MAIN), ("disable", MAIN), ("freeze", MAIN)]


def test_drain_collects_about_every_second_while_the_worker_lives(make_rig, gc_calls):
    rig = make_rig()
    _, thread = _start(rig)
    gc_calls.clear()

    for _ in range(9):
        rig.tab.drainEvents()
    assert gc_calls == []
    rig.tab.drainEvents()  # the 10th 100 ms poll
    assert gc_calls == [("collect", MAIN)]
    for _ in range(10):
        rig.tab.drainEvents()
    assert gc_calls == [("collect", MAIN)] * 2

    thread.finish()
    for _ in range(20):
        rig.tab.drainEvents()
    assert gc_calls == [("collect", MAIN)] * 2 + [("unfreeze", MAIN), ("collect", MAIN), ("enable", MAIN)]  # no worker: no more


class _Garbage:
    """An object in a reference cycle (it holds itself), so only the cycle collector frees it."""

    def __init__(self):
        self.cycle = self


def test_garbage_frozen_at_start_is_freed_on_the_tk_thread_when_the_worker_is_cleared(make_rig):
    """RDO110-R m1: what exists at Start is frozen (the drain's collections skip it), so cyclic garbage that was
    reachable at Start and is dropped during the run is freed only by `_clearWorker`'s collection after the unfreeze,
    on the Tk thread (RT-8a I1, ruling orch 108). With that collection removed, nothing frees it."""
    rig = make_rig()
    freed = []
    junk = _Garbage()
    weakref.finalize(junk, lambda: freed.append(threading.current_thread().name))
    _, thread = _start(rig)  # Start's own collection runs before the freeze, and `junk` is still reachable: it is frozen
    del junk  # now cyclic garbage in the permanent generation
    for _ in range(route_tab_module.GC_EVERY_POLLS):
        rig.tab.drainEvents()  # the periodic collection walks only what the run made since Start
    assert freed == []

    thread.finish()
    rig.tab.drainEvents()

    assert freed == [MAIN] and rig.tab.worker is None and gc.isenabled()


@pytest.fixture
def collections():
    """The name of the thread each garbage collection ran on, automatic or called, during the test."""
    seen = []

    def record(phase, info):
        if phase == "start":
            seen.append(threading.current_thread().name)

    gc.callbacks.append(record)
    yield seen
    gc.callbacks.remove(record)


def test_no_garbage_collection_runs_on_a_real_worker(make_rig, collections):
    def churning_session(*args):
        made = FakeSession(*args)

        def run():
            for _ in range(200_000):  # reference cycles: with the collector on, ~100 collections on this thread
                cycle = []
                cycle.append(cycle)
            made.on_event(_event("ended", "max_laps/", code=0))

        made.run = run
        return made

    rig = make_rig(make_session=churning_session, make_thread=threading.Thread)
    rig.tab.startRun()
    rig.tab.worker.join(10)
    assert not rig.tab.worker.is_alive()
    rig.tab.drainEvents()

    assert "route-run" not in collections and MAIN in collections  # Start's collection ran, on the Tk thread
    assert rig.tab.worker is None and gc.isenabled()


class UnstartableThread(FakeThread):
    def start(self):
        raise RuntimeError("can't start new thread")


def _no_thread(**options):
    raise RuntimeError("can't start new thread")


@pytest.mark.parametrize("failing", ["make_thread", "start"])
def test_a_worker_that_cannot_start_ends_the_run(make_rig, gc_calls, failing):
    """RT-8a m2: the tab leaves Connecting with the watchdog's text and every control back; the error still reaches
    Tk's callback handler (the console)."""
    make_thread = _no_thread if failing == "make_thread" else UnstartableThread
    rig = make_rig(cls=SpyTab, make_thread=make_thread)
    tab = rig.tab

    with pytest.raises(RuntimeError, match="can't start new thread"):
        tab.startRun()

    assert tab.worker is None and tab.state == "ended"
    assert tab.status_var.get() == UNEXPECTED
    assert tab.calls == [("ended", None, "", "ended", CONNECTING)]  # `_onEnded` once, as for the watchdog
    assert _state(rig.gui.load_map_button) == "normal"
    assert [_state(w) for w in _run_controls(tab)] == ["normal"] * 6 and _state(tab.stop_button) == "disabled"
    assert gc.isenabled()
    assert [name for name, _ in gc_calls] == ([] if failing == "make_thread"
                                              else ["collect", "disable", "freeze", "unfreeze", "collect", "enable"])
    assert gc.get_freeze_count() == 0

    tab.drainEvents()  # the readiness refresh gives Start back and keeps the Ended text (choice 8)
    assert _state(tab.start_button) == "normal"
    assert tab.status_var.get() == UNEXPECTED


class EndRaisesTab(SpyTab):
    """`_onEnded` raises, as a call on the kept view could (RT-8a fix re-review minor)."""

    def _onEnded(self, code, text):
        super()._onEnded(code, text)
        raise RuntimeError("replay boom")


def test_a_dead_worker_is_cleared_when_on_ended_raises(make_rig, gc_calls):
    rig = make_rig(cls=EndRaisesTab)
    tab = rig.tab
    _, thread = _start(rig)
    thread.finish()  # dead with no `ended`: the watchdog ends the run and `_onEnded` raises

    with pytest.raises(RuntimeError, match="replay boom"):
        tab.drainEvents()

    assert tab.worker is None and tab.state == "ended"
    assert gc.isenabled() and gc.get_freeze_count() == 0 and gc_calls[-3:] == [("unfreeze", MAIN), ("collect", MAIN), ("enable", MAIN)]
    assert [_state(w) for w in _run_controls(tab)] == ["normal"] * 6 and _state(tab.stop_button) == "disabled"
    assert rig.scheduled[-1][1] == tab.drainEvents  # the loop goes on
    tab.drainEvents()  # nothing left to end: `_onEnded` is not called again, and Start comes back
    assert len(tab.calls) == 1 and _state(tab.start_button) == "normal"


def test_hooks_called_once_per_event(make_rig):
    rig = make_rig(cls=SpyTab)
    tab = rig.tab
    made, thread = _start(rig)
    rig.out.mkdir(parents=True)

    made.on_event(_event("running", "Running."))
    tab.drainEvents()
    assert tab.calls == [("running", "running", RUNNING)]  # after the state and the status line were set

    made.on_event(_event("ended", "stopped/hotkey", code=0))
    tab.drainEvents()
    assert tab.calls[1:] == [("ended", 0, "stopped/hotkey", "ended", RUNNING)]  # before the status line was set
    assert tab.status_var.get() == "Ended: you pressed F12."  # no folder name (H68, ruling orch 110)

    thread.finish()
    tab.drainEvents()
    tab.drainEvents()
    assert len(tab.calls) == 2  # one call per event, none from the watchdog once `ended` was seen


def test_hooks_watchdog_end_calls_on_ended_once(make_rig):
    rig = make_rig(cls=SpyTab)
    tab = rig.tab
    _, thread = _start(rig)
    rig.out.mkdir(parents=True)
    tab.stopRun()
    thread.finish()

    tab.drainEvents()
    tab.drainEvents()

    assert tab.calls == [("ended", None, "", "ended", STOPPING_CONNECT)]  # before the watchdog's text
    assert tab.status_var.get() == UNEXPECTED_AND.format(rig.out.name)


def test_drain_reschedules_when_its_body_raises(make_rig, monkeypatch):
    rig = make_rig()
    tab = rig.tab

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(tab, "_refreshReadiness", boom)
    rig.scheduled.clear()
    with pytest.raises(RuntimeError):
        tab.drainEvents()
    assert rig.scheduled == [(100, tab.drainEvents)]  # the 100 ms loop goes on (RT-7 M3)


def test_setup_canvas_loop_reschedules_when_its_body_raises(make_rig, monkeypatch):
    rig = make_rig()

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(rig.tab, "isRunLive", boom)
    rig.scheduled.clear()
    with pytest.raises(RuntimeError):
        rig.gui._updateCanvas()
    assert rig.scheduled == [(10, rig.gui._updateCanvas)]  # the 10 ms loop goes on (RT-6 m3)


def test_default_session_builds_the_run_from_the_tabs_settings(make_rig):
    rig = make_rig(make_session=defaultSession)
    tab = rig.tab
    tab.laps_var.set("3")
    tab.minutes_var.set("2.5")
    tab.countdown_var.set("7")

    tab.startRun()

    made = tab.session
    assert isinstance(made, RunSession) and rig.threads[0].args == (made,)
    assert made._settings == RunSettings(map_csv=rig.csv, character="kanna", laps=3, minutes=2.5, countdown_s=7,
                                         seed=None, out=rig.out)
    assert made._shared is rig.gui.sharedInfo
    assert made._on_event == tab._events.put and made._may_walk == tab._mayWalk
    assert made._log == tab._postLine  # RDO110-D3 A: the run's lines go to the tab's line queue
    assert made._sigint is False  # no SIGINT handler in the app (spec 5.6)
    defaults = inspect.signature(RunSession).parameters  # and nothing else: the session's own defaults
    for name in ("make_positions", "wait_first_frame", "make_link", "make_clock", "make_stop", "start_capture", "sleep"):
        assert getattr(made, "_" + name) is defaults[name].default, name


# ---- RT-8a: closing the app (spec 5.5 "Closing the app mid-run") and the Tk bans (spec 5.12) ----------------------

CLOSE_GAVE_UP = "The run's cleanup did not finish in 10 s; closing anyway."


def _close_polls(rig):
    return [entry for entry in rig.scheduled if entry[1] == rig.tab._pollClose]


def _run_close_poll(rig):
    """Run the one pending close poll, as the root's `after` would 100 ms later."""
    polls = _close_polls(rig)
    assert polls == [(100, rig.tab._pollClose)]  # one close poll pending at any time
    rig.scheduled.remove(polls[0])
    polls[0][1]()


def _hosted_view(monkeypatch, tab, calls):
    """Fakes for the view RT-8b hosts (`tab.view`) and its LiveController (`tab.live`) that log their teardown."""
    monkeypatch.setattr(tab, "live", SimpleNamespace(stop=lambda: calls.append("live.stop")))
    monkeypatch.setattr(tab, "view", SimpleNamespace(destroy=lambda: calls.append("view.destroy")))


def test_close_with_no_run_destroys_at_once(make_rig, capsys):
    rig = make_rig()
    destroyed = []
    rig.tab.onAppClose(lambda: destroyed.append(1))
    assert destroyed == [1] and _close_polls(rig) == []
    assert capsys.readouterr().out == ""


def test_close_takes_the_view_down_before_the_window(make_rig, monkeypatch):
    rig = make_rig()
    calls = []
    _hosted_view(monkeypatch, rig.tab, calls)
    rig.tab.onAppClose(lambda: calls.append("host"))
    assert calls == ["live.stop", "view.destroy", "host"]  # RT-4 m3: the view's destroy() after its host raises


def test_close_destroys_the_window_even_when_the_view_raises(make_rig, monkeypatch):
    rig = make_rig()
    calls = []

    def bad_destroy():
        raise tk.TclError('bad window path name ".!frame"')

    monkeypatch.setattr(rig.tab, "view", SimpleNamespace(destroy=bad_destroy))
    with pytest.raises(tk.TclError):
        rig.tab.onAppClose(lambda: calls.append("host"))
    assert calls == ["host"]


@pytest.mark.parametrize("drain_first", [False, True])
def test_close_during_countdown_cancels_then_destroys(make_rig, monkeypatch, capsys, drain_first):
    rig = make_rig()
    tab = rig.tab
    made, thread = _start(rig)
    made.on_event(_event("countdown", seconds_left=5))
    tab.drainEvents()
    calls = []
    _hosted_view(monkeypatch, tab, calls)

    def destroy():
        calls.append("host")

    tab.onAppClose(destroy)

    assert made.stops == ["window_close"]
    assert (tab.state, tab.status_var.get()) == ("stopping", STOPPING)
    assert _state(tab.stop_button) == "disabled" and calls == [] and len(_close_polls(rig)) == 1

    tab.onAppClose(destroy)  # pressed again while it waits: one poll loop, nothing more sent
    assert made.stops == ["window_close"] and len(_close_polls(rig)) == 1

    _run_close_poll(rig)  # the worker is still in its cleanup
    assert calls == [] and len(_close_polls(rig)) == 1
    made.on_event(_event("warning", "Recording stopped: x. The walk goes on."))
    tab.drainEvents()
    assert tab.status_var.get() == STOPPING  # the close's text stays until the window goes (RT-8a m1)

    made.on_event(_event("ended", "cancelled/window_close", code=0))
    thread.finish()
    if drain_first:  # the drain finds the worker dead before the close poll does; nothing comes back (RT-8a m1)
        tab.drainEvents()
        assert tab.worker is None and tab.status_var.get() == STOPPING
        assert _state(rig.gui.load_map_button) == "disabled" and _state(tab.start_button) == "disabled"
        assert [_state(w) for w in _run_controls(tab)] == ["disabled"] * 6
    tab.startRun()  # no new run while the window waits to close
    assert len(rig.sessions) == 1 and len(rig.threads) == 1

    _run_close_poll(rig)

    assert calls == ["live.stop", "view.destroy", "host"] and _close_polls(rig) == []
    assert capsys.readouterr().out == ""  # no console line: the cleanup finished in time


def test_close_destroys_after_10_s(make_rig, capsys, gc_calls):
    now = [100.0]
    rig = make_rig(clock=lambda: now[0])
    tab = rig.tab
    made, thread = _start(rig)  # a worker that never ends
    destroyed = []

    tab.onAppClose(lambda: destroyed.append(1))

    for seconds in (100.1, 105.0, 109.9):
        now[0] = seconds
        _run_close_poll(rig)
        assert destroyed == [], seconds
    assert capsys.readouterr().out == ""
    now[0] = 110.0
    _run_close_poll(rig)

    assert destroyed == [1] and _close_polls(rig) == []
    assert capsys.readouterr().out == CLOSE_GAVE_UP + "\n"  # one console line
    assert made.stops == ["window_close"] and thread.is_alive()  # never joined: a daemon, it dies with the process
    assert not gc.isenabled() and ("enable", MAIN) not in gc_calls  # left off: the process is exiting (RT-8a I1)
    assert ("unfreeze", MAIN) not in gc_calls


_MISC_CALLS = ("focus_set", "focus", "focus_force", "tkraise", "lift", "grab_set")
_WM_CALLS = ("wm_attributes", "attributes", "wm_resizable", "resizable", "wm_overrideredirect", "overrideredirect",
             "wm_transient", "transient", "wm_iconbitmap", "iconbitmap", "wm_iconphoto", "iconphoto")


def _record_banned_calls(monkeypatch):
    """Patch spec 5.12's banned calls on the classes and the dialog modules; the list they append their names to."""
    called = []

    def recorder(name):
        def record(*args, **kwargs):
            called.append(name)
        return record

    targets = [(tk.Misc, name) for name in _MISC_CALLS] + [(tk.Wm, name) for name in _WM_CALLS]
    targets.append((tk.Toplevel, "__init__"))
    for module in (messagebox, filedialog, simpledialog):
        functions = [name for name, _ in inspect.getmembers(module, inspect.isfunction)]
        assert functions, module.__name__
        targets += [(module, name) for name in functions]
    for owner, name in targets:
        monkeypatch.setattr(owner, name, recorder(f"{owner.__name__}.{name}"))
    return called


def test_no_window_style_or_focus_call_during_a_run(make_rig, monkeypatch):
    """Spec 5.12 rules 2-3 at run time: patched on the classes, so a call on any widget and through any alias is seen
    (`tk.Misc.focus is tk.Misc.focus_set`, yet each name is its own class attribute)."""
    rig = make_rig()
    tab = rig.tab
    _route_tab_in_front(rig)  # so the drains draw the idle minimap
    called = _record_banned_calls(monkeypatch)

    tab.drainEvents()
    made, thread = _start(rig)
    for event in [_event("status", CONNECTING), _event("countdown", seconds_left=2),
                  _event("countdown", seconds_left=1), _event("running", "Running."),
                  _event("warning", "Recording stopped: x. The walk goes on.")]:
        made.on_event(event)
        tab.drainEvents()
    tab.stop_button.invoke()
    tab.onAppClose(lambda: None)
    made.on_event(_event("ended", "stopped/button", code=0))
    thread.finish()
    tab.drainEvents()
    _run_close_poll(rig)

    assert called == [] and len(tab.live_minimap.canvas.find_withtag("bg")) == 1
    tab.runs_list.focus()  # the patches are seen through an instance and an alias
    rig.gui.gui.attributes("-topmost", True)
    assert called == ["Misc.focus", "Wm.attributes"]


@pytest.mark.parametrize("module", [route_tab_module, live_minimap_module], ids=["route_tab", "live_minimap"])
def test_route_tab_source_has_no_focus_or_dialog_names(module):
    """Spec 5.12's source scan covers the tab and the idle minimap it hosts, which is drawn during Connecting and the
    countdown (RDO110-R m3)."""
    assert tk_bans.focus_problems(inspect.getsource(module)) == []


@pytest.mark.parametrize("snippet, problem", [
    ("w = tk.Toplevel(root)", "line 1: .Toplevel"),
    ("w = Toplevel(root)", "line 1: Toplevel"),
    ("from tkinter import Toplevel", "line 1: from tkinter import Toplevel"),
    ("box = ttk.Combobox(root)", "line 1: .Combobox"),
    ("box = Combobox(root)", "line 1: Combobox"),
    ("from tkinter.ttk import Combobox", "line 1: from tkinter.ttk import Combobox"),
    ("root.iconbitmap('a.ico')", "line 1: .iconbitmap(...)"),
    ("root.iconphoto(False, image)", "line 1: .iconphoto(...)"),
    ("root.wm_iconbitmap('a.ico')", "line 1: .wm_iconbitmap(...)"),
    ("root.wm_iconphoto(False, image)", "line 1: .wm_iconphoto(...)"),
    ("entry.focus()", "line 1: .focus(...)"),
    ("flags = ('-toolwindow', 1)", "line 1: '-toolwindow'"),
    ("flags = ('-fullscreen', 1)", "line 1: '-fullscreen'"),
])
def test_tk_bans_find_each_added_entry(snippet, problem):
    assert tk_bans.focus_problems(snippet) == [problem]


def test_tk_bans_keep_the_view_tests_entries_and_allow_the_focus_reads():
    planted = "\n".join(["w.focus_force()", "w.attributes('-topmost', True)", "import tkinter.messagebox",
                         "from tkinter import filedialog", "from tkinter.simpledialog import askstring",
                         "root.tk.call('wm', 'resizable', root, 1, 1)", "w.overrideredirect(True)", "w.lift()",
                         "w.transient(root)", "w.grab_set()", "w.tkraise()", "w.focus_set()", "w.resizable(0, 0)"])
    found = tk_bans.focus_problems(planted)  # test_10's planted sample (tests/route_view/test_view_window.py)
    assert len(found) == 14, found
    # spec 9's reaction reads Tk's own focus: a bare `.focus` attribute, focus_get() and the focus events stay allowed
    assert tk_bans.focus_problems("handler = w.focus\nw.focus_get()\nw.bind('<FocusIn>', f)") == []


# -- RT-8b: the hosted live view; RT-8c: kept, frozen, at the end (spec 5.2 Hosting, 5.8; H68, H69) -----------------------

G = view_runs.G  # a run of two legs, 26 steps, ending with the `end` line


def _hosting_rig(make_rig, tmp_path, *, spy=True, load_fails=False, **keywords):
    """A rig whose every Start gets a new run folder (`rig.outs`, newest last), as the app's `new_out` does. With `spy`
    the view, the live controller and the replay loader are the defaults wrapped in spies: `rig.views` and `rig.lives`
    hold what was built, `rig.live_kwargs` the keywords of each `make_live`, `rig.loads` the folders `load_run` was
    asked for and `rig.log` every teardown ("view<i>.destroy", "live<i>.stop"); `load_fails` makes `load_run` fail."""
    outs, views, lives, live_kwargs, loads, log = [], [], [], [], [], []

    def new_out():
        outs.append(tmp_path / "runs" / f"run{len(outs)}")
        return outs[-1]

    def make_view(*args, **kwargs):
        view = RouteViewWindow(*args, **kwargs)
        i, real = len(views), view.destroy
        views.append(view)
        view.destroy = lambda: (log.append(f"view{i}.destroy"), real())[1]
        return view

    def make_live(*args, **kwargs):
        live = LiveController(*args, **kwargs)
        i, real = len(lives), live.stop
        lives.append(live)
        live_kwargs.append(kwargs)
        live.stop = lambda: (log.append(f"live{i}.stop"), real())[1]
        return live

    def load_run(folder, **kwargs):
        loads.append(folder)
        return Failure("unreadable") if load_fails else load_folder(folder, **kwargs)

    if spy:
        keywords = dict(make_view=make_view, make_live=make_live, load_run=load_run) | keywords
    rig = make_rig(new_out=new_out, **keywords)
    rig.outs, rig.views, rig.lives, rig.live_kwargs, rig.loads, rig.log = outs, views, lives, live_kwargs, loads, log
    return rig


def _write_run(folder, events, *, snapshot=False):
    """The run folder as the walk leaves it so far: the map, the header and `events`; `snapshot`: a `minimap.png`."""
    folder.mkdir(parents=True, exist_ok=True)
    view_runs.make_folder(folder, events)
    if snapshot:
        Image.new("RGB", (180, 90), (40, 80, 120)).save(folder / SNAPSHOT)


def _append(folder, events):
    with open(folder / STEPS_FILE, "ab") as f:
        f.write(b"".join(view_runs.event_lines(events)))


def _post(rig, made, *events):
    for event in events:
        made.on_event(event)
        rig.tab.drainEvents()


def _finish(rig, made, thread, text="max_laps/", code=0):
    """The walk's cleanup is done: `ended`, the worker returns, one drain reads both."""
    made.on_event(_event("ended", text, code=code))
    thread.finish()
    rig.tab.drainEvents()


def _kept_run(rig):
    """A whole run whose live view is kept, frozen, at its end (it read the `end` line), with the Back button."""
    made, thread = _start(rig)
    _write_run(rig.outs[-1], G)
    _post(rig, made, _event("running", "Running."))
    _finish(rig, made, thread)
    assert rig.tab.view is not None and rig.tab.view.live and rig.tab.live is None and rig.tab._backShown()


def _packed(tab):
    """What `view_frame` shows: its packed widgets (the idle minimap's canvas stays a child while hidden)."""
    return tab.view_frame.pack_slaves()


def _one_view(tab):
    """`view_frame` holds exactly the widgets of one view, `tab.view`'s, and the idle minimap's canvas, hidden."""
    v = tab.view
    return v is not None and tab.live_minimap.canvas not in _packed(tab) and set(tab.view_frame.winfo_children()) == {
        v.title_label, v.warn_label, v.main, v.why_frame, v.bar, v.status_label, tab.live_minimap.canvas}


class FakeMinimap:
    """The fake `make_minimap`'s product: a real, empty Canvas in the parent; `draw()` only counts."""

    def __init__(self, parent, gui, report=None):
        self.canvas = tk.Canvas(parent)
        self.draws = 0

    def draw(self):
        self.draws += 1


def _route_tab_in_front(rig):
    rig.gui.notebook.select(rig.gui.route_tab_frame)


def test_view_rebuilt_per_run_and_kept_at_its_end(make_rig, tmp_path):
    rig = _hosting_rig(make_rig, tmp_path)
    tab = rig.tab
    for run in range(2):
        made, thread = _start(rig)
        _write_run(rig.outs[run], G[:5])
        _post(rig, made, _event("running", "Running."))
        assert tab.view is rig.views[run] and tab.live is rig.lives[run] and tab.view.live and _one_view(tab)
        _append(rig.outs[run], G[5:])
        _finish(rig, made, thread)
        assert tab.view is rig.views[run] and tab.view.live and tab.view.folder == rig.outs[run]
        assert tab.live is None and _one_view(tab) and tab.status_var.get() == "Ended: all laps done."
    assert rig.log == ["live0.stop", "view0.destroy", "live1.stop"] and len(rig.views) == 2 and rig.loads == []


def test_final_steps_reach_the_frozen_view_when_ended_drains_before_a_tick(make_rig, tmp_path):
    """Plan review RA3 I5: `ended` drained before the next 100 ms tick still shows the run's last steps, in the kept
    view (H69)."""
    rig = _hosting_rig(make_rig, tmp_path)
    tab = rig.tab
    made, thread = _start(rig)
    out = rig.outs[0]
    _write_run(out, G[:3])
    _post(rig, made, _event("running", "Running."))
    live = tab.live
    assert [f for _, f in rig.scheduled if f == live.tick] == [live.tick]  # queued, and never run here
    _append(out, G[3:])
    _finish(rig, made, thread)
    full = load_folder(out, repo_root=view_runs.REPO_ROOT, current_git_head=None).unwrap()
    view = tab.view
    assert view is rig.views[0] and view.live and _one_view(tab) and rig.loads == []
    assert view.model is not None and len(view.model.events) == len(full.events) == len(G)
    assert view.index == len(G) - 1 and tab.status_var.get() == "Ended: all laps done."


@pytest.mark.parametrize("end_line", [True, False])
def test_end_freezes_the_live_view(make_rig, tmp_path, end_line):
    """Spec 5.2 (H68, H69): at the end no replay opens and nothing is loaded; the run's live view stays in
    `view_frame`, frozen: still live (no replay bar), its last live picture kept, its controller stopped, its title's
    mode `ended: <reason>` (`ended` when no `end` line was read), and "Back to live minimap" shows between the top
    area and the view."""
    rig = _hosting_rig(make_rig, tmp_path)
    tab = rig.tab
    made, thread = _start(rig)
    out = rig.outs[0]
    _write_run(out, G[:5])
    _post(rig, made, _event("running", "Running."))
    view = tab.view
    assert view is not None and view.shown_background == "live" and not tab._backShown()
    _append(out, G[5:] if end_line else G[5:-1])
    _finish(rig, made, thread, "stopped/button")
    assert tab.view is view and view is rig.views[0] and view.live is True and view.bar.winfo_manager() != "pack"
    assert tab.live is None and rig.log == ["live0.stop"] and rig.loads == [] and _one_view(tab)
    assert view.title_label.cget("text").split(" · ")[1] == ("ended: max_laps" if end_line else "ended")
    assert view.shown_background == "live" and len(view.model.events) == len(G) - (0 if end_line else 1)
    assert tab.status_var.get() == "Ended: you pressed Stop."
    assert tab._backShown() and tab.back_button.cget("text") == "Back to live minimap"
    slaves = tab.view_frame.master.pack_slaves()
    assert slaves.index(tab._back_frame) == slaves.index(tab.view_frame) - 1


@pytest.mark.parametrize("case", ["stop_connecting", "kept_view", "legs_only"])
def test_end_without_a_live_view(make_rig, tmp_path, case):
    """A run that ends before `running` has no run view to keep: the idle minimap Start showed stays, with no Back
    button, and nothing is loaded, even with a leg log in the folder (H68; waits for Hong: RDO110-D1)."""
    rig = _hosting_rig(make_rig, tmp_path)
    tab = rig.tab
    if case == "kept_view":
        _kept_run(rig)  # Start drops this run's kept view (H69, H70)
    views, log = len(rig.views), len(rig.log)
    made, thread = _start(rig)
    out = rig.outs[-1]
    out.mkdir(parents=True)
    if case == "legs_only":
        view_runs.make_legs_folder(out)
    tab.stopRun()
    (out / "stop.json").write_text("{}", encoding="utf-8")
    _finish(rig, made, thread, "cancelled/button")
    assert len(rig.views) == views and rig.loads == []
    assert tab.view is None and _packed(tab) == [tab.live_minimap.canvas] and not tab._backShown()
    assert tab.status_var.get() == "Ended: cancelled before the walk (you pressed Stop)."
    assert rig.log[log:] == (["view0.destroy"] if case == "kept_view" else [])


def test_back_to_live(make_rig, tmp_path):
    """Spec 5.2 (H69): "Back to live minimap" drops the kept view and shows the idle live minimap, drawn again from
    the next drain on; the button hides and the Ended text stays."""
    rig = _hosting_rig(make_rig, tmp_path, make_minimap=FakeMinimap)
    tab = rig.tab
    minimap = tab.live_minimap
    _route_tab_in_front(rig)
    _kept_run(rig)
    ended = tab.status_var.get()
    draws = minimap.draws
    tab.drainEvents()
    assert minimap.draws == draws  # not drawn while the kept view is shown
    tab.back_button.invoke()
    assert tab.view is None and tab.live is None and _packed(tab) == [minimap.canvas] and not tab._backShown()
    assert rig.log == ["live0.stop", "view0.destroy"] and tab.status_var.get() == ended
    tab.drainEvents()
    assert minimap.draws == draws + 1 and tab.status_var.get() == ended == "Ended: all laps done."


def _host_replay(rig, folder):
    """A saved run's replay hosted as RT-10's Open will host it: the idle minimap hidden, the replay in `view_frame`,
    the Back button shown (`_showBack`). Open is not built yet (RT-10), so this stands in for it."""
    tab = rig.tab
    _write_run(folder, G)
    tab._hideMinimap()
    tab.view = tab._make_view(tab.view_frame, folder, None, live=False)
    tab._showBack()
    return tab.view


def test_back_over_a_saved_runs_replay(make_rig, tmp_path):
    """RDO110-D2, answer A (Hong, QB155): "Back to live minimap" shows above a saved run's replay as above a kept run
    view; it drops the replay and shows the idle live minimap, drawn again from the next drain on."""
    rig = _hosting_rig(make_rig, tmp_path, make_minimap=FakeMinimap)
    tab = rig.tab
    minimap = tab.live_minimap
    _route_tab_in_front(rig)
    view = _host_replay(rig, tmp_path / "saved")
    assert _one_view(tab) and tab.view is view and not view.live and tab._backShown()
    assert tab.back_button.cget("text") == "Back to live minimap"
    draws = minimap.draws
    tab.drainEvents()
    assert minimap.draws == draws  # hidden under the replay: not drawn
    tab.back_button.invoke()
    assert tab.view is None and tab.live is None and _packed(tab) == [minimap.canvas] and not tab._backShown()
    assert rig.log == ["view0.destroy"]
    tab.drainEvents()
    assert minimap.draws == draws + 1


def test_start_leaves_a_saved_runs_replay(make_rig, tmp_path):
    """RDO110-D2, answer A: Start, once its checks pass, drops a saved run's replay and its Back button, as it drops a
    kept run view (H70)."""
    rig = _hosting_rig(make_rig, tmp_path, make_minimap=FakeMinimap)
    tab = rig.tab
    minimap = tab.live_minimap
    _route_tab_in_front(rig)
    _host_replay(rig, tmp_path / "saved")
    _start(rig)
    assert rig.log == ["view0.destroy"] and tab.view is None and _packed(tab) == [minimap.canvas]
    assert not tab._backShown()


def test_live_view_reads_the_shared_frame(make_rig, tmp_path):
    rig = _hosting_rig(make_rig, tmp_path)
    made, _ = _start(rig)
    _post(rig, made, _event("running", "Running."))
    live_frame = rig.live_kwargs[0]["live_frame"]
    frame, seen = object(), []

    class Lock:
        held = False

        def __enter__(self):
            Lock.held = True

        def __exit__(self, *exc):
            Lock.held = False

    class Holder:
        minimap_frame_lock = Lock()

        @property
        def minimap_frame(self):
            seen.append(Lock.held)
            return frame

    rig.gui.sharedInfo = Holder()
    assert live_frame() is frame and seen == [True] and not Lock.held  # read under the lock, returned as is
    assert rig.live_kwargs[0]["current_git_head"] == rig.tab._head


def test_live_view_built_at_running_not_at_the_countdown(make_rig, tmp_path):
    rig = _hosting_rig(make_rig, tmp_path)
    tab = rig.tab
    made, _ = _start(rig)
    _post(rig, made, _event("status", CONNECTING), _event("countdown", seconds_left=2),
          _event("countdown", seconds_left=1))
    assert rig.views == [] and tab.view is None
    _post(rig, made, _event("running", "Running."))
    assert len(rig.views) == 1 and tab.view is rig.views[0] and tab.view.live and _one_view(tab)


def test_idle_tab_shows_the_minimap(make_rig, monkeypatch):
    """Spec 5.2 (H70): with no run view and no replay the view area shows the idle live minimap (the default
    `LiveMinimap`), drawn on every drain while the Route tab is in front."""
    rig = make_rig()
    tab = rig.tab
    minimap = tab.live_minimap
    assert tab.view is None and _packed(tab) == [minimap.canvas]
    draws, real = [], minimap.draw
    monkeypatch.setattr(minimap, "draw", lambda: (draws.append(1), real())[1])
    _route_tab_in_front(rig)
    tab.drainEvents()
    tab.drainEvents()
    assert len(draws) == 2 and len(minimap.canvas.find_withtag("bg")) == 1  # the shared frame, drawn


def test_minimap_not_drawn_while_the_setup_tab_is_shown(make_rig):
    rig = make_rig(make_minimap=FakeMinimap)
    tab, gui = rig.tab, rig.gui
    assert gui.isSetupTabShown() and tab.live_minimap.draws == 0  # make_rig drained once, with Setup in front
    _route_tab_in_front(rig)
    tab.drainEvents()
    gui.notebook.select(gui.setup_tab)
    tab.drainEvents()
    tab.drainEvents()
    assert tab.live_minimap.draws == 1


def test_start_leaves_a_kept_view(make_rig, tmp_path):
    """Spec 5.2 (H69, H70): Start, once its checks pass, drops the last run's kept view and its Back button; the idle
    live minimap shows, and is drawn, through Connecting and the countdown; at `running` the run's live view replaces
    it (waits for Hong: RDO110-D1; answer A is built). A refused Start drops nothing."""
    rig = _hosting_rig(make_rig, tmp_path, make_minimap=FakeMinimap)
    tab = rig.tab
    minimap = tab.live_minimap
    _route_tab_in_front(rig)
    _kept_run(rig)
    kept = tab.view
    assert _one_view(tab) and rig.log == ["live0.stop"] and tab._backShown()
    draws = minimap.draws
    tab.drainEvents()
    assert minimap.draws == draws  # hidden under the kept view: not drawn
    agent = rig.gui.controller.agent
    agent.CSVParser.CSV_file = None
    tab.startRun()  # refused by a check (no map loaded)
    assert len(rig.sessions) == 1 and tab.view is kept and _one_view(tab) and tab._backShown()
    agent.CSVParser.CSV_file = rig.csv

    made, _ = _start(rig)
    assert rig.log == ["live0.stop", "view0.destroy"] and tab.view is None and _packed(tab) == [minimap.canvas]
    assert not tab._backShown()
    for event in (_event("status", CONNECTING), _event("countdown", seconds_left=1)):
        draws = minimap.draws
        _post(rig, made, event)
        assert tab.view is None and _packed(tab) == [minimap.canvas] and minimap.draws == draws + 1
    _write_run(rig.outs[-1], G[:3])
    draws = minimap.draws
    _post(rig, made, _event("running", "Running."))
    assert tab.view is rig.views[-1] and tab.view.live and _one_view(tab) and minimap.draws == draws


def test_minimap_hidden_from_running_to_the_end(make_rig, tmp_path):
    """Spec 5.7: hidden from `running` to the end of the run, the minimap adds no redraw while the bot walks; nor
    while the run's kept view is shown after it (H69)."""
    rig = _hosting_rig(make_rig, tmp_path, make_minimap=FakeMinimap)
    tab = rig.tab
    minimap = tab.live_minimap
    _route_tab_in_front(rig)
    made, thread = _start(rig)
    _write_run(rig.outs[0], G[:5])
    _post(rig, made, _event("running", "Running."))
    draws = minimap.draws
    assert _one_view(tab)
    _append(rig.outs[0], G[5:10])
    _post(rig, made, _event("warning", "Recording stopped: x. The walk goes on."))
    tab.stop_button.invoke()
    tab.drainEvents()
    assert minimap.draws == draws and _one_view(tab)
    _append(rig.outs[0], G[10:])
    _finish(rig, made, thread, "stopped/button")
    assert minimap.draws == draws and _one_view(tab) and tab.view.live and tab.live is None
    tab.drainEvents()
    assert minimap.draws == draws


def test_no_window_style_or_focus_call_with_the_hosted_view(make_rig, tmp_path, monkeypatch):
    rig = _hosting_rig(make_rig, tmp_path, spy=False)  # the default view, controller, loader and minimap
    tab = rig.tab
    _route_tab_in_front(rig)
    rig.gui.controller.agent.CSVParser.CSV_file = test_gui_character_buttons.FIXTURE_MAP  # nodes on the minimap
    called = _record_banned_calls(monkeypatch)
    tab.drainEvents()
    made, thread = _start(rig)
    out = rig.outs[0]
    _write_run(out, G[:5], snapshot=True)
    _post(rig, made, _event("running", "Running."))
    live = tab.live
    for chunk in (G[5:10], G[10:20]):
        _append(out, chunk)
        live.tick()  # a 100 ms tick
    _finish(rig, made, thread, "stopped/button")  # no `end` line: the view is kept as it is
    assert tab.view is not None and tab.view.live and tab.status_var.get() == "Ended: you pressed Stop."
    tab.back_button.invoke()  # back to the idle minimap, drawn on the next drain
    tab.drainEvents()
    _kept_run(rig)  # and one whose live view read its `end` line
    canvas = tab.live_minimap.canvas
    assert called == [] and len(canvas.find_withtag("bg")) == 1 and canvas.find_withtag("node")


def test_no_replay_opens_while_a_close_waits(make_rig, tmp_path):
    """RT-8a fix round [fyi]: `_onEnded` still runs while a close waits; it changes nothing and shows no Back button
    (the close takes the view down)."""
    rig = _hosting_rig(make_rig, tmp_path)
    tab = rig.tab
    made, thread = _start(rig)
    _write_run(rig.outs[0], G[:5])
    _post(rig, made, _event("running", "Running."))
    destroyed = []
    tab.onAppClose(lambda: destroyed.append(1))
    _append(rig.outs[0], G[5:])
    _finish(rig, made, thread, "stopped/window_close")
    assert rig.loads == [] and len(rig.views) == 1 and tab.view is rig.views[0] and tab.view.live
    assert not tab._backShown()
    assert rig.log == [] and tab.status_var.get() == STOPPING
    _run_close_poll(rig)
    assert destroyed == [1] and rig.log == ["live0.stop", "view0.destroy"]


def test_no_live_view_is_built_while_a_close_waits(make_rig, tmp_path):
    """RT-8b review m3: a `running` drained while the window waits to close builds no live view and starts no polling
    (the close would only tear it down), like `_onEnded` opens nothing then."""
    rig = _hosting_rig(make_rig, tmp_path)
    tab = rig.tab
    made, thread = _start(rig)
    _post(rig, made, _event("status", CONNECTING))
    destroyed = []
    tab.onAppClose(lambda: destroyed.append(1))
    _post(rig, made, _event("running", "Running."))
    assert rig.views == [] and tab.view is None and tab.live is None and rig.log == []
    assert tab.status_var.get() == STOPPING
    _finish(rig, made, thread, "stopped/window_close")
    _run_close_poll(rig)
    assert destroyed == [1] and rig.log == []


@pytest.mark.parametrize("cyclic", [False, True])
def test_the_kept_views_image_is_freed_on_the_tk_thread(make_rig, tmp_path, cyclic):
    """The GC guard covers the hosted view (RT-8a I1, ruling orch 108): the last run's view kept before Start is
    destroyed by Start (H69, H70), just before the collection that precedes the freeze; its image (a Tcl image whose
    `__del__` calls Tcl) is freed on the Tk thread by the time the run's end has been drained, also when only the cycle
    collector can free it."""
    rig = _hosting_rig(make_rig, tmp_path, spy=False)
    tab = rig.tab
    _kept_run(rig)
    photo = tab.view.shown_photo
    assert photo is not None and tab.view.shown_background == "live"
    name, freed = str(photo), []
    weakref.finalize(photo, lambda: freed.append(threading.current_thread().name))
    if cyclic:
        tab.view.cycle = tab.view  # garbage only the cycle collector can free, as any view in a reference cycle
    del photo
    made, thread = _start(rig)
    _write_run(rig.outs[-1], G[:3])
    _post(rig, made, _event("running", "Running."))
    _finish(rig, made, thread)
    assert freed == [MAIN] and name not in tab.view_frame.tk.splitlist(tab.view_frame.tk.call("image", "names"))


# ---- RT-8c piece 4: the live view's and the idle minimap's lines in the console box (H71; RDO110-D3 A) ------------


def test_live_view_lines_reach_the_console_box(make_rig, tmp_path):
    """The live view's `report` (its "follow error: ..." lines) is the tab's console-box writer with the error flag; it
    runs on the Tk thread, so it writes at once."""
    rig = _hosting_rig(make_rig, tmp_path, cls=ConsoleSpyTab)
    tab = rig.tab
    made, thread = _start(rig)
    _write_run(rig.outs[-1], G)
    _post(rig, made, _event("running", "Running."))
    tab.order.clear()
    rig.live_kwargs[0]["report"]("follow error: RuntimeError: x")
    assert tab.order == [("console", "follow error: RuntimeError: x", True)]
    assert "follow error: RuntimeError: x\n" in _console(rig.gui)
    _finish(rig, made, thread)


def test_a_minimap_error_is_one_console_line(make_rig):
    """RDO110-2 [review]: `LiveMinimap.draw()` never raises; its error goes to the console box once through the tab's
    writer (the same error on the next drain is not written again)."""
    rig = make_rig(cls=ConsoleSpyTab)
    tab = rig.tab
    _route_tab_in_front(rig)
    rig.shared.minimap_frame = np.zeros((2, 2, 2, 2), dtype=np.uint8)  # Image.fromarray cannot read it
    tab.order.clear()
    tab.drainEvents()
    tab.drainEvents()
    lines = [entry for entry in tab.order if entry[0] == "console"]
    assert len(lines) == 1 and lines[0][1].startswith("live minimap error: ") and lines[0][2] is True


# The app-focus flag and the app_focus stop (spec 9, H64; RT-9). The root's `focus_get` is patched: in the withdrawn
# test root Tk's focus record stays empty, and RT-6's SendMessage test covers the real record.

def _focus_on(rig, monkeypatch):
    """Patch the root's `focus_get`; returns a setter: `focus(True)` names a widget of ours, `focus(False)` None."""
    current = {"widget": None}
    monkeypatch.setattr(rig.gui.gui, "focus_get", lambda: current["widget"])

    def focus(has):
        current["widget"] = rig.tab.stop_button if has else None
    return focus


def _real_after_idle(rig, monkeypatch):
    """Put the root's real `after` back over `make_gui`'s recorder, so `after_idle` (which calls `after('idle', ...)`)
    really schedules and `update_idletasks()` runs it; returns the root."""
    root = rig.gui.gui
    monkeypatch.setattr(root, "after", types.MethodType(tk.Misc.after, root))
    return root


def _reread_twice_each(tab, focus, values):
    for value in values:
        focus(value)
        tab.rereadFocus()
        tab.rereadFocus()


def _to_state(rig, made, kind):
    made.on_event(_event("countdown", seconds_left=3) if kind == "countdown" else _event("running", "Running."))
    rig.tab.drainEvents()
    assert rig.tab.state == kind


def test_focus_rise_during_countdown_stops_once(make_rig, monkeypatch):
    _rise_stops_once(make_rig, monkeypatch, "countdown")


def test_focus_rise_while_running_stops_once(make_rig, monkeypatch):
    _rise_stops_once(make_rig, monkeypatch, "running")


def _rise_stops_once(make_rig, monkeypatch, kind):
    """None then a widget, two re-reads each, is one rise and one `stop("app_focus")`; the state and the status line
    stay (the `ended` that follows shows the text)."""
    rig = make_rig()
    tab = rig.tab
    focus = _focus_on(rig, monkeypatch)
    made, _ = _start(rig)
    _to_state(rig, made, kind)
    status = tab.status_var.get()
    _reread_twice_each(tab, focus, [False, True])
    assert made.stops == ["app_focus"]
    assert tab.app_has_keyboard.is_set() and made.may_walk() is False
    assert (tab.state, tab.status_var.get()) == (kind, status)
    _reread_twice_each(tab, focus, [True])  # still the app: no second rise
    assert made.stops == ["app_focus"]
    # The same through the root's binding: each generated event schedules one re-read, which update_idletasks runs.
    # `focus_get` stays patched: a generated <FocusIn> does not change Tk's focus record (plan review RA3 M6, I2).
    root = _real_after_idle(rig, monkeypatch)
    focus(False)
    root.event_generate("<FocusOut>")
    root.update_idletasks()
    assert not tab.app_has_keyboard.is_set() and made.stops == ["app_focus"]
    focus(True)
    root.event_generate("<FocusIn>")
    root.event_generate("<FocusIn>")
    root.update_idletasks()
    assert tab.app_has_keyboard.is_set() and made.stops == ["app_focus", "app_focus"]  # one more: one rise


def test_focus_rise_does_nothing_before_start_while_connecting_and_after_end(make_rig, monkeypatch):
    rig = make_rig()
    tab = rig.tab
    focus = _focus_on(rig, monkeypatch)
    _reread_twice_each(tab, focus, [False, True, False, True])  # before Start: the flag follows, nothing to stop
    assert tab.session is None and tab.app_has_keyboard.is_set() and tab.state == "ready"
    focus(False)
    made, thread = _start(rig)
    assert tab.state == "connecting"
    _reread_twice_each(tab, focus, [True, False, True])  # connecting: no key sent yet; the countdown's end reads it
    assert made.stops == [] and tab.app_has_keyboard.is_set()
    _reread_twice_each(tab, focus, [False])
    made.on_event(_event("ended", "stopped/button", code=0))
    thread.finish()
    tab.drainEvents()
    assert tab.state == "ended" and tab.worker is None
    _reread_twice_each(tab, focus, [True, False, True])  # after the end
    assert made.stops == []


def _idle_real_timers_recorded(rig, monkeypatch):
    """Make `after_idle` (`after('idle', ...)`) really schedule, so `update_idletasks()` runs it, while `after(ms, ...)`
    stays `make_gui`'s recorder: no timer is ever due here, as the drain's next step is not yet due when the Tk thread
    comes back from a stall (RT-9-R m1). Returns the root."""
    root = rig.gui.gui
    record = root.after

    def after(ms, func):
        return tk.Misc.after(root, ms, func) if ms == "idle" else record(ms, func)
    monkeypatch.setattr(root, "after", after)
    return root


@pytest.mark.parametrize("queued", [("countdown",), ("countdown", "running")])
def test_focus_rise_with_the_runs_events_still_queued_stops(make_rig, monkeypatch, queued):
    """RT-9-R m1: one drain step blocks the Tk thread for the whole countdown (here the idle minimap's draw, the step's
    last part, after its queue drain), the worker's `countdown` (and `running`) wait on the queue, and the user clicks
    the app meanwhile. The drain's next step is scheduled in its `finally`, after the stall, so it is not due when the
    Tk thread comes back, and Tcl runs the click's idle re-read first (`Tcl_DoOneEvent` runs idle callbacks only when
    no event is queued; a timer becomes an event only once due): the re-read finds the tab still `connecting`. It
    drains the queue first, so it tests the run's real state, and the click stops the run (spec 9)."""
    rig = make_rig(make_minimap=FakeMinimap)
    tab = rig.tab
    _route_tab_in_front(rig)
    focus = _focus_on(rig, monkeypatch)
    made, _ = _start(rig)
    assert tab.state == "connecting" and not tab.app_has_keyboard.is_set()
    root = _idle_real_timers_recorded(rig, monkeypatch)

    def stalled_draw():  # the worker goes on and the user clicks the app while the Tk thread is blocked here
        for kind in queued:
            made.on_event(_event("countdown", seconds_left=3) if kind == "countdown" else _event("running", "Running."))
        focus(True)
        root.event_generate("<FocusIn>")

    monkeypatch.setattr(tab.live_minimap, "draw", stalled_draw)
    rig.scheduled.clear()
    tab.drainEvents()
    assert tab.state == "connecting" and made.stops == []
    assert rig.scheduled == [(route_tab_module.POLL_MS, tab.drainEvents)]  # the next drain: recorded, never run here
    root.update_idletasks()  # the click's re-read runs before the next drain
    assert made.stops == ["app_focus"] and tab.app_has_keyboard.is_set()
    assert tab.state == queued[-1]
    assert (tab.view is not None) == (queued[-1] == "running")  # a drained `running` still builds the live view


def test_focus_rise_stops_even_if_showing_the_queued_running_raises(make_rig, monkeypatch):
    """RT-9-R m1: the re-read's drain shows a queued `running` (the state, then `_onRunning`); a view that fails to
    build raises out of the re-read, on to Tk's callback handler, but only after the click's stop."""
    rig = make_rig()
    tab = rig.tab
    focus = _focus_on(rig, monkeypatch)
    made, _ = _start(rig)
    made.on_event(_event("countdown", seconds_left=3))
    made.on_event(_event("running", "Running."))

    def broken_view():
        raise RuntimeError("view")

    monkeypatch.setattr(tab, "_onRunning", broken_view)
    focus(True)
    with pytest.raises(RuntimeError, match="view"):
        tab.rereadFocus()
    assert made.stops == ["app_focus"] and tab.state == "running"


@pytest.mark.parametrize("text, expected", [
    ("stopped/app_focus", "Stopped: you clicked the app. Press Start to run again."),
    ("cancelled/app_focus", "Stopped: you clicked the app. Press Start to run again."),
    ("cancelled/countdown_focus",
     "Not started: click the game first. Press Start, then click the game before the countdown ends."),
])
def test_app_focus_texts(make_rig, text, expected):
    """H73 (Hong answered A); spec 5.5's `app_focus` and `countdown_focus` rows, on the status line after `ended`."""
    assert endedText(0, text, None) == expected
    rig = make_rig()
    made, thread = _start(rig)
    made.on_event(_event("ended", text, code=0))
    thread.finish()
    rig.tab.drainEvents()
    assert rig.tab.status_var.get() == expected


def test_focus_rise_then_stop_keeps_app_focus(make_rig, monkeypatch):
    """H73 (Hong answered A): a press on Stop while walking is first a click on the app; the rise's stop lands first,
    Stop still sends its own, and the session keeps the first trigger (`runtime.py:6`), so the click text shows."""
    rig = make_rig()
    tab = rig.tab
    focus = _focus_on(rig, monkeypatch)
    made, thread = _start(rig)
    _to_state(rig, made, "running")
    _reread_twice_each(tab, focus, [False, True])
    tab.stopRun()
    assert made.stops == ["app_focus", "button"]
    assert (tab.state, tab.status_var.get()) == ("stopping", STOPPING)
    made.on_event(_event("ended", "stopped/app_focus", code=0))
    thread.finish()
    tab.drainEvents()
    assert tab.status_var.get() == "Stopped: you clicked the app. Press Start to run again."


def test_focus_on_a_widget_tkinter_did_not_make_is_the_app(make_rig, monkeypatch):
    """`focus_get()` raises KeyError when Tk's record names a widget Tk made itself (a Combobox drop-down's list,
    tkinter's `nametowidget`): that is still one of our widgets, so the app has the keyboard."""
    rig = make_rig()
    tab = rig.tab
    focus = _focus_on(rig, monkeypatch)
    made, _ = _start(rig)
    _to_state(rig, made, "countdown")

    def popdown_focus():
        raise KeyError("popdown")

    focus(False)
    tab.rereadFocus()
    monkeypatch.setattr(rig.gui.gui, "focus_get", popdown_focus)
    tab.rereadFocus()
    assert tab.app_has_keyboard.is_set() and made.stops == ["app_focus"]


def test_focus_moving_between_our_widgets_is_not_a_click(make_rig, monkeypatch):
    """Spec 9: focus moving between two of our widgets is a <FocusOut> and a <FocusIn> in a row; both re-reads see a
    widget, so there is no rise and no stop. Every widget of the window has the root's tag in its bindtags, so the
    root's binding sees both; the events are generated on the root because, in the withdrawn test root, Tk delivers a
    generated focus event on a child widget to no binding at all (tried, RT-9)."""
    rig = make_rig()
    tab = rig.tab
    made, _ = _start(rig)
    _to_state(rig, made, "running")
    reads = []

    def focus_get():
        reads.append(1)
        return tab.minutes_entry

    monkeypatch.setattr(rig.gui.gui, "focus_get", focus_get)
    tab.app_has_keyboard.set()  # the app had the keyboard
    root = _real_after_idle(rig, monkeypatch)
    assert "." in tab.laps_entry.bindtags() and "." in tab.minutes_entry.bindtags()
    root.event_generate("<FocusOut>")
    root.event_generate("<FocusIn>")
    root.update_idletasks()
    assert len(reads) == 2  # both events reached the root's binding and each re-read ran
    assert made.stops == [] and tab.app_has_keyboard.is_set()


def test_focus_bindings_with_a_route_tab_schedule_one_reread_each(make_rig):
    """Pre-flight m6: with a tab, <FocusIn> and <FocusOut> on the root each schedule one `after_idle` re-read
    (`make_gui`'s recorder sees `after('idle', ...)`), added to the root's bindings."""
    rig = make_rig()
    root = rig.gui.gui
    assert root.bind("<FocusIn>") and root.bind("<FocusOut>")
    rig.scheduled.clear()
    root.event_generate("<FocusIn>")
    root.event_generate("<FocusOut>")
    assert rig.scheduled == [("idle", rig.tab.rereadFocus), ("idle", rig.tab.rereadFocus)]


def test_no_focus_binding_without_a_route_tab(make_gui):
    """Pre-flight m6: with the factory None there is no tab, so nothing is bound and a focus event schedules nothing."""
    gui, _, scheduled = make_gui(route_tab_factory=None)
    root = gui.gui
    assert gui.route_tab is None
    assert root.bind("<FocusIn>") == "" and root.bind("<FocusOut>") == ""
    scheduled.clear()
    root.event_generate("<FocusIn>")
    root.event_generate("<FocusOut>")
    assert scheduled == []
