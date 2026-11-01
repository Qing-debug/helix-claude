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
from types import SimpleNamespace

import pytest
from PIL import Image
from returns.result import Failure

import src.gui.gui as gui_module
import src.gui.route_tab as route_tab_module
import test_gui_startup
import tk_bans
from src.classes.classes import Kanna
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
    assert endedText(code, text, None, False) == expected


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
    assert endedText(code, text, folder, False) == expected


@pytest.mark.parametrize("code", [6, 7])
@pytest.mark.parametrize("text", ["none/", "stopped/hotkey", "x"])
def test_ended_text_walker_failed_names_the_folder_once(code, text, folder):
    assert endedText(code, text, folder, False) == f"Ended: the walker failed; see {folder.name}."
    assert endedText(code, text, folder, True) == f"Ended: the walker failed; see {folder.name}. Replay is open."


def test_ended_text_suffix_follows_replay_open_only(folder):
    (folder / "steps.jsonl").write_text("{}\n", encoding="utf-8")
    assert endedText(0, "stopped/hotkey", folder, True) == f"Ended: you pressed F12 ({folder.name}). Replay is open."
    assert (endedText(0, "cancelled/button", folder, True)
            == f"Ended: cancelled before the walk (you pressed Stop) ({folder.name}). Replay is open.")
    assert (endedText(0, "max_laps/", folder, True)
            == f"Ended: all laps done ({folder.name}). Replay is open.")
    assert (endedText(1, "link_down/", folder, True)
            == f"Ended: the board link was lost ({folder.name}). Replay is open.")
    # the folder holds a step log, but no replay opened: no suffix and no name (choice 7)
    assert endedText(0, "stopped/hotkey", folder, False) == "Ended: you pressed F12."
    assert endedText(0, "cancelled/button", folder, False) == "Ended: cancelled before the walk (you pressed Stop)."


@pytest.mark.parametrize("code, text", [
    (0, "stopped/app_focus"), (0, "cancelled/app_focus"), (0, "cancelled/countdown_focus"), (0, "stopped/window_close"),
    (0, "xyz/abc"), (0, "none/"), (2, "none/"), (3, "none/"), (4, "none/"), (5, "none/"), (9, "none/"), (11, "none/"),
])
def test_ended_text_other_prefixes_take_no_suffix(code, text, folder):
    assert endedText(code, text, folder, True) == endedText(code, text, folder, False)
    assert endedText(code, text, folder, True) == endedText(code, text, None, False)


def test_ended_text_unexpected_stop(folder, tmp_path):
    error = "the run stopped unexpectedly: RuntimeError: x"
    assert (endedText(None, error, folder, False)
            == f"Ended: the run stopped unexpectedly: RuntimeError: x. See the console and {folder.name}.")
    assert (endedText(None, error, folder, True)
            == f"Ended: the run stopped unexpectedly: RuntimeError: x. See the console and {folder.name}."
               " Replay is open.")
    gone = tmp_path / "never_made"  # an exception before the leg log leaves no folder
    assert endedText(None, error, gone, False) == "Ended: the run stopped unexpectedly: RuntimeError: x. See the console."
    assert endedText(None, error, None, False) == "Ended: the run stopped unexpectedly: RuntimeError: x. See the console."


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


class UnkeyedClass(Kanna):
    """A class `session.CLASSES` has no key for (classKey counts the exact class)."""


def _boom(*args):
    raise AssertionError("called before Start")


class FakeSession:
    """What the fake `make_session` builds: the factory's four arguments; `run()` posts `script` through `on_event`,
    then raises `raises` when set; `stop(source)` only records."""

    def __init__(self, settings, shared, on_event, may_walk):
        self.settings, self.shared, self.on_event, self.may_walk = settings, shared, on_event, may_walk
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

        def fake_session(settings, shared, on_event, may_walk):
            made = FakeSession(settings, shared, on_event, may_walk)
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
    assert tab.view_frame.winfo_children() == []
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


def test_may_walk_is_true_until_rt9(make_rig):
    rig = make_rig()
    rig.tab.startRun()
    may_walk = rig.sessions[0].may_walk
    assert may_walk() is True  # nothing sets app_has_keyboard until RT-9
    rig.tab.app_has_keyboard.set()
    assert may_walk() is False
    rig.tab.app_has_keyboard.clear()
    assert may_walk() is True


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

    replay = False

    def __init__(self, *args, **kwargs):
        self.calls = []
        super().__init__(*args, **kwargs)

    def _onRunning(self):
        self.calls.append(("running", self.state, self.status_var.get()))

    def _onEnded(self, code, text):
        self.calls.append(("ended", code, text, self.state, self.status_var.get()))
        return self.replay


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
        assert tab.status_var.get() == f"Ended: the run stopped unexpectedly; see the console and {rig.out.name}."
    else:
        assert tab.status_var.get() == "Ended: the run stopped unexpectedly; see the console."
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

    err = capsys.readouterr().err
    assert "Traceback" in err and "RuntimeError: boom in run" in err
    assert hooked == []

    rig.tab.drainEvents()

    assert rig.tab.state == "ended" and rig.tab.worker is None
    assert rig.tab.status_var.get() == "Ended: the run stopped unexpectedly; see the console."
    assert (_state(rig.tab.start_button), _state(rig.tab.stop_button)) == ("normal", "disabled")


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
    assert tab.status_var.get() == "Ended: the run stopped unexpectedly; see the console."
    assert tab.calls == [("ended", None, "", "ended", CONNECTING)]  # `_onEnded` once, as for the watchdog
    assert _state(rig.gui.load_map_button) == "normal"
    assert [_state(w) for w in _run_controls(tab)] == ["normal"] * 6 and _state(tab.stop_button) == "disabled"
    assert gc.isenabled()
    assert [name for name, _ in gc_calls] == ([] if failing == "make_thread"
                                              else ["collect", "disable", "freeze", "unfreeze", "collect", "enable"])
    assert gc.get_freeze_count() == 0

    tab.drainEvents()  # the readiness refresh gives Start back and keeps the Ended text (choice 8)
    assert _state(tab.start_button) == "normal"
    assert tab.status_var.get() == "Ended: the run stopped unexpectedly; see the console."


class EndRaisesTab(SpyTab):
    """`_onEnded` raises, as a replay that cannot be built could (RT-8a fix re-review minor)."""

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


@pytest.mark.parametrize("replay", [False, True])
def test_hooks_called_once_per_event(make_rig, replay):
    rig = make_rig(cls=SpyTab)
    tab = rig.tab
    tab.replay = replay
    made, thread = _start(rig)
    rig.out.mkdir(parents=True)

    made.on_event(_event("running", "Running."))
    tab.drainEvents()
    assert tab.calls == [("running", "running", RUNNING)]  # after the state and the status line were set

    made.on_event(_event("ended", "stopped/hotkey", code=0))
    tab.drainEvents()
    assert tab.calls[1:] == [("ended", 0, "stopped/hotkey", "ended", RUNNING)]  # before the status line was set
    suffix = f" ({rig.out.name}). Replay is open." if replay else "."
    assert tab.status_var.get() == "Ended: you pressed F12" + suffix  # choice 7 only when the hook says so

    thread.finish()
    tab.drainEvents()
    tab.drainEvents()
    assert len(tab.calls) == 2  # one call per event, none from the watchdog once `ended` was seen


@pytest.mark.parametrize("replay", [False, True])
def test_hooks_watchdog_end_calls_on_ended_once(make_rig, replay):
    rig = make_rig(cls=SpyTab)
    tab = rig.tab
    tab.replay = replay
    _, thread = _start(rig)
    rig.out.mkdir(parents=True)
    tab.stopRun()
    thread.finish()

    tab.drainEvents()
    tab.drainEvents()

    assert tab.calls == [("ended", None, "", "ended", STOPPING_CONNECT)]  # before the watchdog's text
    assert tab.status_var.get() == (f"Ended: the run stopped unexpectedly; see the console and {rig.out.name}."
                                    + (" Replay is open." if replay else ""))


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
    assert made._sigint is False  # no SIGINT handler in the app (spec 5.6)
    defaults = inspect.signature(RunSession).parameters  # and nothing else: the session's own defaults
    for name in ("make_positions", "wait_first_frame", "make_link", "make_clock", "make_stop", "start_capture", "sleep"):
        assert getattr(made, "_" + name) is defaults[name].default, name


def test_ended_text_code_10_with_a_replay(folder):
    assert (endedText(10, "off_graph/", folder, True)
            == f"Ended: the character was lost (exit 10) ({folder.name}). Replay is open.")
    assert endedText(10, "no_position/", folder, False) == "Ended: the character was lost (exit 10)."


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
    called = _record_banned_calls(monkeypatch)

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

    assert called == []
    tab.runs_list.focus()  # the patches are seen through an instance and an alias
    rig.gui.gui.attributes("-topmost", True)
    assert called == ["Misc.focus", "Wm.attributes"]


def test_route_tab_source_has_no_focus_or_dialog_names():
    assert tk_bans.focus_problems(inspect.getsource(route_tab_module)) == []


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


# -- RT-8b: the hosted live view and its replay at the end (spec 5.2 Hosting, 5.8) ----------------------------------------

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


def _replayed_run(rig, *, snapshot=False):
    """A whole run whose replay is open at its end (the live view read the `end` line)."""
    made, thread = _start(rig)
    _write_run(rig.outs[-1], G, snapshot=snapshot)
    _post(rig, made, _event("running", "Running."))
    _finish(rig, made, thread)
    assert rig.tab.view is not None and rig.tab.view.live is False


def _one_view(tab):
    """`view_frame` holds exactly the widgets of one view, `tab.view`'s."""
    v = tab.view
    return v is not None and set(tab.view_frame.winfo_children()) == {v.title_label, v.warn_label, v.main,
                                                                       v.why_frame, v.bar, v.status_label}


def _bar_states(view):
    controls = [view.btn_first, view.btn_prev, view.btn_next, view.btn_last, view.unit_menu, view.slider,
                *view.play_buttons.values(), view.lap_entry, view.leg_entry, *view.scale_buttons.values()]
    return [_state(c) for c in controls]


def test_view_rebuilt_per_run_and_replay(make_rig, tmp_path):
    rig = _hosting_rig(make_rig, tmp_path)
    tab = rig.tab
    for run in range(2):
        made, thread = _start(rig)
        _write_run(rig.outs[run], G[:5])
        _post(rig, made, _event("running", "Running."))
        assert tab.view is rig.views[run] and tab.live is rig.lives[run] and tab.view.live and _one_view(tab)
        _append(rig.outs[run], G[5:])
        _finish(rig, made, thread)
        assert tab.view is rig.views[run] and tab.view.live is False and tab.view.folder == rig.outs[run]
        assert tab.live is None and _one_view(tab) and tab.status_var.get().endswith("Replay is open.")
    assert rig.log == ["live0.stop", "view0.destroy", "live1.stop"] and len(rig.views) == 2 and rig.loads == []


def test_final_steps_reach_the_replay_when_ended_drains_before_a_tick(make_rig, tmp_path):
    """Plan review RA3 I5: `ended` drained before the next 100 ms tick still shows the run's last steps."""
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
    assert view is rig.views[0] and view.live is False and _one_view(tab) and rig.loads == []
    assert view.model is not None and len(view.model.events) == len(full.events) == len(G)
    assert tab.status_var.get().endswith("Replay is open.")


@pytest.mark.parametrize("case", ["stop_connecting", "kept_replay", "live_no_end", "live_no_end_load_fails",
                                  "legs_only"])
def test_end_without_a_live_view(make_rig, tmp_path, case):
    rig = _hosting_rig(make_rig, tmp_path, load_fails=case == "live_no_end_load_fails")
    tab = rig.tab
    if case == "kept_replay":
        _replayed_run(rig)  # (waits for Hong: RTF98-6a-D1) the plan's default: this replay goes too
    views, loads, log = len(rig.views), len(rig.loads), len(rig.log)
    made, thread = _start(rig)
    out = rig.outs[-1]
    out.mkdir(parents=True)
    if case in ("stop_connecting", "kept_replay"):
        tab.stopRun()
        (out / "stop.json").write_text("{}", encoding="utf-8")
        _finish(rig, made, thread, "cancelled/button")
        assert len(rig.views) == views and len(rig.loads) == loads
        assert tab.view is None and tab.view_frame.winfo_children() == []
        assert tab.status_var.get() == "Ended: cancelled before the walk (you pressed Stop)."
        assert rig.log[log:] == (["view0.destroy"] if case == "kept_replay" else [])
        return
    if case == "legs_only":
        view_runs.make_legs_folder(out)
    else:
        _write_run(out, G[:-1])  # no `end` line
        _post(rig, made, _event("running", "Running."))
        assert tab.live is not None and tab.view.live
    _finish(rig, made, thread, "stopped/button")
    assert rig.loads == [out] and tab.live is None
    assert rig.log == ([] if case == "legs_only" else ["live0.stop", "view0.destroy"])
    if case == "live_no_end_load_fails":
        assert tab.view is None and tab.view_frame.winfo_children() == []
        assert tab.status_var.get() == endedText(0, "stopped/button", out, False)
    else:
        assert tab.view is rig.views[-1] and tab.view.live is False and tab.view.model is not None and _one_view(tab)
        assert tab.status_var.get() == endedText(0, "stopped/button", out, True)


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


def test_previous_replay_kept_with_its_bar_greyed_until_running(make_rig, tmp_path, monkeypatch):
    """(waits for Hong: RTF98-4b-D1) the plan's default A'; answer B replaces this test."""
    rig = _hosting_rig(make_rig, tmp_path)
    tab = rig.tab
    _replayed_run(rig)
    kept, calls = tab.view, []
    real = kept.set_bar_enabled
    monkeypatch.setattr(kept, "set_bar_enabled", lambda enabled: (calls.append(enabled), real(enabled))[1])
    agent = rig.gui.controller.agent
    agent.CSVParser.CSV_file = None
    tab.startRun()  # refused by a check (no map loaded): no session, so nothing could give the bar back
    assert len(rig.sessions) == 1 and calls == [] and set(_bar_states(kept)) == {"normal"}
    agent.CSVParser.CSV_file = rig.csv

    made, _ = _start(rig)
    assert calls == [False] and tab.view is kept and _one_view(tab) and set(_bar_states(kept)) == {"disabled"}
    for event in (_event("status", CONNECTING), _event("countdown", seconds_left=1)):
        _post(rig, made, event)
        assert tab.view is kept and _one_view(tab) and set(_bar_states(kept)) == {"disabled"}
    index = kept.index
    assert kept.model is not None and index != kept.model.find_leg(0, 6)  # leg 6 starts at another step
    kept.lap_var.set("0")
    kept.leg_var.set("6")
    kept.go_to_leg()  # Enter in a greyed-out entry (RR3 m8)
    assert kept.index == index

    _write_run(rig.outs[-1], G[:3])
    _post(rig, made, _event("running", "Running."))
    assert rig.log[-1] == "view0.destroy" and tab.view is rig.views[-1] and tab.view.live and _one_view(tab)
    assert calls == [False]


def test_no_window_style_or_focus_call_with_the_hosted_view(make_rig, tmp_path, monkeypatch):
    rig = _hosting_rig(make_rig, tmp_path, spy=False)  # the default view, controller and loader
    tab = rig.tab
    called = _record_banned_calls(monkeypatch)
    made, thread = _start(rig)
    out = rig.outs[0]
    _write_run(out, G[:5], snapshot=True)
    _post(rig, made, _event("running", "Running."))
    live = tab.live
    for chunk in (G[5:10], G[10:20]):
        _append(out, chunk)
        live.tick()  # a 100 ms tick
    _finish(rig, made, thread, "stopped/button")  # no `end` line: the replay is loaded from the folder
    assert tab.view is not None and tab.view.live is False and tab.status_var.get().endswith("Replay is open.")
    _replayed_run(rig)  # and one whose live view turns into the replay
    assert called == []


def test_no_replay_opens_while_a_close_waits(make_rig, tmp_path):
    """RT-8a fix round [fyi]: `_onEnded` still runs while a close waits; it opens nothing (the close takes the view
    down)."""
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
    assert rig.log == [] and tab.status_var.get() == STOPPING
    _run_close_poll(rig)
    assert destroyed == [1] and rig.log == ["live0.stop", "view0.destroy"]


@pytest.mark.parametrize("cyclic", [False, True])
def test_the_kept_replays_image_is_freed_on_the_tk_thread(make_rig, tmp_path, cyclic):
    """The GC guard covers the hosted view (RT-8a I1, ruling orch 108): the kept replay, frozen at Start with the rest,
    is destroyed at `running`; its image (a Tcl image whose `__del__` calls Tcl) is freed on the Tk thread by the
    time the run's end has been drained, also when only the cycle collector can free it."""
    rig = _hosting_rig(make_rig, tmp_path, spy=False)
    tab = rig.tab
    _replayed_run(rig, snapshot=True)
    photo = tab.view.shown_photo
    assert photo is not None and tab.view.shown_background == SNAPSHOT
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
