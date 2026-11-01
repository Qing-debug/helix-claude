"""The Route tab (spec 5.2-5.5, contract C5): the pure helpers (settings validation, class key, run texts, countdown
text), then the tab itself on `make_gui`'s withdrawn root, built on fakes: no board, no game, no git, no disk probe.

Same real-Tk-root setup as test_gui_startup.py (skipped where Tk cannot open a display; `after` only records)."""
import inspect
import threading
import tkinter as tk
from types import SimpleNamespace

import pytest

import src.gui.gui as gui_module
import test_gui_startup
from src.classes.classes import Kanna
from src.gui.route_tab import RouteTab, classKey, countdownText, defaultSession, endedText, validateSettings
from src.route import session
from src.route.session import RECORD_GB_PER_HOUR, START_FREE_BYTES, RunEvent, RunSession, RunSettings
from test_gui_startup import _console, _frame, _open_window, _shared

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
    """The fake `make_thread`'s product: `start()` runs nothing; `finish()` runs the worker's body on the test's thread
    and leaves the thread dead; `alive_check`, when set, answers `is_alive()` instead."""

    def __init__(self, *, target, args=(), name=None, daemon=None):
        self.target, self.args, self.name, self.daemon = target, args, name, daemon
        self.started, self.alive, self.alive_check = 0, False, None

    def start(self):
        self.started += 1
        self.alive = True

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


def test_app_close_destroys_at_once(make_rig):
    destroyed = []
    make_rig().tab.onAppClose(lambda: destroyed.append(1))
    assert destroyed == [1]


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
    rig.tab.startRun()
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
