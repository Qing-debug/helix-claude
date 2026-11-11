"""Route-tab spec 5.6 and 8: `RunSession` (src/route/session.py) over the simulator's seams.

Every run here uses the simulator's `SimPositionSource` and `SimLink`, a fake first-frame wait, a fake stop control,
`SimClock` and a recording `sleep`; the autouse `_no_real_blelink` fails any test that would build a real `BleLink`.
The small fakes are imported flat from test_walk_cli.py (RTB-PF m8)."""

import inspect
import math
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace

import numpy as np
import pytest
from returns.result import Failure, Success
from test_walk_cli import (
    BELOW_THE_SESSION,
    FIXTURE,
    REPO,
    TERRAIN,
    FakeStop,
    LinkWrap,
    LostAfter,
    _alive_names,
    _json,
    _lines,
    _patch_walk_leg,
    _raise_kaboom,
    break_below_the_session,
)

from src.classes.classes import Kanna
from src.input import input_layer
from src.input.link import BleLink
from src.route import runtime, session
from src.route.harness import record_minimap
from src.route.harness.record_minimap import INDEX_NAME
from src.route.session import RunEvent, RunSession, RunSettings
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.world import NoiseConfig, SimParams, SimWorld, load_terrain
from src.route.walker import RouteWalker, RunSummary


@pytest.fixture(autouse=True)
def _no_real_blelink(monkeypatch):
    """A test that forgets `make_link=` fails here instead of scanning for the board. The class is patched, not
    `session.BleLink`: the `make_link=BleLink` default was bound when `RunSession.__init__` was defined. `pytest.fail`
    raises `Failed`, a `BaseException`, so `run()`'s `except Exception` paths do not swallow it."""
    def refuse(self, *args, **kwargs) -> None:
        pytest.fail("real BleLink in a test")

    monkeypatch.setattr(BleLink, "__init__", refuse)


class HookLink(LinkWrap):
    """`LinkWrap` whose `connect` first calls `on_connect` (a Stop pressed while the board connects)."""

    def __init__(self, inner: SimLink, log: list[str]) -> None:
        super().__init__(inner, log)
        self.on_connect: Callable[[], None] | None = None

    def connect(self):
        if self.on_connect is not None:
            self.on_connect()
        return super().connect()


class HookStop(FakeStop):
    """`FakeStop` whose `start` first calls `on_start` (a Stop between the last flag check and the start)."""

    on_start: Callable[[], None] | None = None

    def start(self, sigint: bool = True):
        if self.on_start is not None:
            self.on_start()
        return super().start(sigint)


class LaterStop(HookStop):
    """`HookStop` built with `calls_on_stop=False`: like the real `StopControl` (runtime.py `_work`), `trigger` sets
    `trigger_source` at once and `on_stop` runs later on another thread, here when `deliver()` is called or at
    `close()` (the real worker's last chance, `_end_worker`)."""

    delivered = False

    def deliver(self) -> None:
        if self.trigger_source is not None and not self.delivered:
            self.delivered = True
            worker = threading.Thread(target=self.on_stop, name="stop-worker")
            worker.start()
            worker.join()

    def close(self) -> None:
        self.deliver()
        super().close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """FF2 terrain, noise off, start at (113, 60), one SimClock everywhere, one lap, no countdown, a shared frame set.
    The fake stop control calls `on_stop` from `trigger`; with `env.later_stop` it calls it later from another thread,
    as the real worker does (`LaterStop`). `RouteWalker.run` counts its calls."""
    rg, rotation, _ropes = session._load_route(FIXTURE, Kanna()).unwrap()
    clock = SimClock()
    params = SimParams()
    world = SimWorld(load_terrain(TERRAIN).unwrap(), rg.reach_h, rg.reach_v, params, NoiseConfig.off(),
                     np.random.default_rng(0), (113.0, 60.0))
    e = SimpleNamespace(log=[], calls=[], stops=[], events=[], sleeps=[], walks=0, out=tmp_path / "run",
                        rotation=rotation, shared=runtime.SharedFrame(), map_csv=FIXTURE, laps=1, countdown_s=0.0,
                        first=Success(None), stop_start=Success(None), on_start=None, on_sleep=None, may_walk=None,
                        make_clock=lambda: clock, session=None, later_stop=False, on_event=None)
    e.shared.minimap_frame = np.zeros((4, 6, 3), np.uint8)
    e.link = HookLink(SimLink(world, clock, params), e.log)
    e.positions = SimPositionSource(world, clock, 0.0, np.random.default_rng(1))

    def make_positions(shared):
        e.calls.append("make_positions")
        return e.positions

    def start_capture(shared) -> None:
        e.calls.append("start_capture")

    def wait_first_frame(shared, timeout_s):
        e.calls.append("wait_first_frame")
        return e.first

    def make_stop(on_stop, *, log):
        if e.later_stop:
            stop = LaterStop(on_stop, e.log, e.stop_start, False, e.link)
        else:
            stop = HookStop(on_stop, e.log, e.stop_start, True, e.link)
        stop.on_start = e.on_start
        stop.log_line = log
        e.stops.append(stop)
        return stop

    def sleep(seconds: float) -> None:
        e.sleeps.append(seconds)
        if e.on_sleep is not None:
            e.on_sleep(len(e.sleeps))

    real_run = RouteWalker.run

    def counting_run(self):
        e.walks += 1
        return real_run(self)

    monkeypatch.setattr(RouteWalker, "run", counting_run)
    monkeypatch.setattr(input_layer, "_clock", clock.now_ns)
    monkeypatch.setattr(input_layer, "_sleep", clock.sleep)

    def build() -> RunSession:
        settings = RunSettings(map_csv=e.map_csv, character="kanna", laps=e.laps, minutes=None,
                               countdown_s=e.countdown_s, seed=1, out=e.out)
        on_event = e.on_event if e.on_event is not None else e.events.append
        lines = getattr(e, "lines", None)  # a test that sets `env.lines` gets the run's lines there (RT-8c piece 4)
        logged = {} if lines is None else {"log": lambda text, error: lines.append((text, error))}
        e.session = RunSession(settings, e.shared, on_event=on_event, make_positions=make_positions,
                               wait_first_frame=wait_first_frame, make_link=lambda: e.link, make_clock=e.make_clock,
                               make_stop=make_stop, start_capture=start_capture, may_walk=e.may_walk, sleep=sleep,
                               **logged)
        return e.session

    def run() -> int:
        code = build().run()
        assert isinstance(code, int)
        return code

    e.build = build
    e.run = run
    return e


def _kinds(events: list[RunEvent]) -> list[str]:
    return [event.kind for event in events]


def _ended(events: list[RunEvent]) -> RunEvent:
    """The one `ended` event, which is the last event posted."""
    ended = [event for event in events if event.kind == "ended"]
    assert len(ended) == 1 and events[-1] is ended[0]
    return ended[0]


def _no_run_thread_left() -> bool:
    return not ({"record", "route-stop-guard", "walk-timer", "step-log"} & _alive_names())


# ---- events and the countdown --------------------------------------------------------------------------------------

def test_events_in_order(env):
    env.countdown_s = 5.0
    assert env.run() == 0
    assert _kinds(env.events) == ["status"] + ["countdown"] * 5 + ["running", "ended"]
    assert [event.seconds_left for event in env.events if event.kind == "countdown"] == [5, 4, 3, 2, 1]
    ended = _ended(env.events)
    assert ended.code == 0 and ended.text == "max_laps/"
    assert env.sleeps == [1.0] * 5
    assert env.walks == 1


def test_route_load_finishes_before_the_first_event(env, monkeypatch):
    """`_load_route` swaps stdout; the Tk thread swaps it too once it sees an event, so no event may come first (DBG115)."""
    real_load = session._load_route
    seen: list[int] = []

    def load(map_csv, character):
        result = real_load(map_csv, character)
        seen.append(len(env.events))  # the events posted by the time the load returns
        return result

    monkeypatch.setattr(session, "_load_route", load)
    assert env.run() == 0
    assert seen == [0]


# ---- stop(): the cancel flag before StopControl.start(), trigger(source) after ---------------------------------------

def test_stop_during_connect_cancels(env):
    window: list[int] = []

    def on_connect() -> None:
        window.append(time.perf_counter_ns())
        env.session.stop("button")
        window.append(time.perf_counter_ns())
        env.session.stop("window_close")  # the flag keeps its first call only (spec 5.6)

    env.link.on_connect = on_connect
    assert env.run() == 0
    stop_json = _json(env.out / "stop.json")
    assert stop_json == {"reason": "cancelled", "trigger_ns": stop_json["trigger_ns"], "trigger_source": "button"}
    assert window[0] <= stop_json["trigger_ns"] <= window[1]
    assert env.walks == 0 and env.stops == []
    assert env.log == ["release_all", "link.close"]  # release_all is the first cleanup call on the link
    ended = _ended(env.events)
    assert ended.code == 0 and ended.text == "cancelled/button"


def test_no_real_blelink_guard():
    with pytest.raises(pytest.fail.Exception):
        BleLink()


def test_stop_right_after_start(env, capsys):
    run_session = env.build()
    before = time.perf_counter_ns()
    run_session.stop("button")
    after = time.perf_counter_ns()
    assert run_session.run() == 0
    stop_json = _json(env.out / "stop.json")
    assert stop_json == {"reason": "cancelled", "trigger_ns": stop_json["trigger_ns"], "trigger_source": "button"}
    assert before <= stop_json["trigger_ns"] <= after
    assert [path.name for path in env.out.iterdir()] == ["stop.json"]
    assert capsys.readouterr().err == ""
    assert env.log == [] and env.stops == []
    assert _ended(env.events).text == "cancelled/button"


def test_stop_between_last_check_and_stop_control_start(env):
    env.countdown_s = 3.0
    window: list[int] = []

    def on_start() -> None:
        window.append(time.perf_counter_ns())
        env.session.stop("button")
        window.append(time.perf_counter_ns())

    env.on_start = on_start
    assert env.run() == 0
    assert _kinds(env.events).count("countdown") == 1  # the countdown sees the trigger at its first check
    stop_json = _json(env.out / "stop.json")
    assert stop_json["reason"] == "cancelled" and stop_json["trigger_source"] == "button"
    assert window[0] <= stop_json["trigger_ns"] <= window[1]  # the flag's stamp, not the later trigger's
    assert env.stops[0].triggers == ["button"]
    assert env.walks == 0
    assert _no_run_thread_left()


def test_flag_set_while_connect_fails(env):
    env.link.connect_result = Failure("x")
    env.link.on_connect = lambda: env.session.stop("button")
    assert env.run() == 4  # the step's own failure wins
    assert not (env.out / "stop.json").exists()
    ended = _ended(env.events)
    assert ended.code == 4 and ended.text == "none/"


def test_stop_during_countdown(env):
    env.countdown_s = 5.0

    def on_sleep(n: int) -> None:
        if n == 2:
            env.session.stop("button")

    env.on_sleep = on_sleep
    assert env.run() == 0
    assert _kinds(env.events).count("countdown") == 2
    stop_json = _json(env.out / "stop.json")
    assert stop_json == {"reason": "cancelled", "trigger_ns": env.stops[0].trigger_ns, "trigger_source": "button"}
    end = _lines(env.out / "steps.jsonl")[-1]
    assert (end["ev"], end["reason"], end["code"]) == ("end", "cancelled", 0)
    assert env.walks == 0
    assert env.log[-3:] == ["release_all", "link.close", "stop.close"]
    assert _no_run_thread_left()  # the stop never reached a walker that will not run (no stop guard left)


@pytest.mark.parametrize("source", ["button", "window_close", "app_focus"])
def test_stop_while_walking(env, monkeypatch, source):
    def before(lap: int, leg: int) -> None:
        if (lap, leg) == (1, 1):
            env.session.stop(source)

    _patch_walk_leg(monkeypatch, before)
    assert env.run() == 0
    stop_json = _json(env.out / "stop.json")
    assert stop_json["trigger_source"] == source and stop_json["reason"] == "stopped"
    assert env.stops[0].triggers == [source]
    assert _ended(env.events).text == f"{stop_json['reason']}/{source}"
    assert env.log[-3:] == ["release_all", "link.close", "stop.close"]
    assert _no_run_thread_left()


def test_may_walk_false_is_countdown_focus(env):
    asked: list[bool] = []

    def may_walk() -> bool:
        asked.append(False)
        return False

    env.countdown_s = 2.0
    env.may_walk = may_walk
    assert env.run() == 0
    assert asked == [False]  # asked once, when the countdown ends
    stop_json = _json(env.out / "stop.json")
    assert stop_json == {"reason": "cancelled", "trigger_ns": env.stops[0].trigger_ns,
                         "trigger_source": "countdown_focus"}
    assert env.walks == 0
    assert _ended(env.events).text == "cancelled/countdown_focus"
    assert _no_run_thread_left()


def test_may_walk_true_walks(env):
    env.may_walk = lambda: True
    assert env.run() == 0
    assert env.walks == 1
    assert _json(env.out / "stop.json")["reason"] == "max_laps"


# ---- the real stop control's later on_stop (RT-3 review m4, RT-3-S1) -------------------------------------------------

def _cancelled_before_the_walk(env, capsys, source: str) -> None:
    assert _kinds(env.events) == ["status", "ended"]  # countdown 0: no countdown event, no `running`
    stop_json = _json(env.out / "stop.json")
    assert (stop_json["reason"], stop_json["trigger_source"]) == ("cancelled", source)
    assert env.walks == 0
    assert not (env.out / "frames").exists()  # no recorder
    assert _ended(env.events).text == f"cancelled/{source}"
    assert capsys.readouterr().out.splitlines()[-1] == f"stopped: cancelled ({source})"
    assert env.stops[0].delivered  # on_stop ran at the latest in the cleanup, with no walker handed over
    assert _no_run_thread_left()


def test_stop_pending_at_start_with_no_countdown(env, capsys):
    """RT-3-S1 (kept): a stop pending when the stop control starts, with a 0 s countdown, cancels at the gate even
    though `on_stop` has not run yet; `may_walk` is not asked."""
    asked: list[bool] = []

    def may_walk() -> bool:
        asked.append(True)
        return True

    env.later_stop = True
    env.may_walk = may_walk
    env.on_start = lambda: env.session.stop("button")
    assert env.run() == 0
    assert asked == []
    _cancelled_before_the_walk(env, capsys, "button")


def test_may_walk_false_with_no_countdown(env, capsys):
    """`may_walk()` False triggers `countdown_focus`; the gate cancels before the later `on_stop` has run."""
    asked: list[bool] = []

    def may_walk() -> bool:
        asked.append(False)
        return False

    env.later_stop = True
    env.may_walk = may_walk
    assert env.run() == 0
    assert asked == [False]
    _cancelled_before_the_walk(env, capsys, "countdown_focus")


def test_on_stop_after_the_gates_trigger_check_cancels(env, monkeypatch, capsys):
    """Review m4: a stop lands after the gate read `trigger_source`, and the worker runs `on_stop` before the gate's
    second check; the run cancels and the walker is never handed over."""
    real = RunSession._stop_asked_now

    def stop_lands_first(self) -> bool:
        self.stop("button")
        env.stops[-1].deliver()
        return real(self)

    env.later_stop = True
    monkeypatch.setattr(RunSession, "_stop_asked_now", stop_lands_first)
    assert env.run() == 0
    assert env.session._walking is None
    _cancelled_before_the_walk(env, capsys, "button")


def test_stop_after_the_gate_stops_the_walk_at_its_first_check(env, monkeypatch):
    """A stop between the countdown's end and `walker.run()` (here while `running` is posted): `on_stop` finds no
    walker yet, so `_hand_over` stops it; the walk starts and ends at its first check, `stopped/<source>`."""
    def on_event(event: RunEvent) -> None:
        env.events.append(event)
        if event.kind == "running":
            env.session.stop("button")

    env.on_event = on_event
    assert env.run() == 0
    assert env.walks == 1
    assert env.stops[0].plays_at_trigger == env.link.plays  # no move played after the stop
    stop_json = _json(env.out / "stop.json")
    assert (stop_json["reason"], stop_json["trigger_source"]) == ("stopped", "button")
    assert _ended(env.events).text == "stopped/button"
    assert _no_run_thread_left()


@pytest.mark.parametrize("when", ["before_the_error", "in_the_cleanup"])
def test_error_before_the_walk_leaves_no_stop_guard(env, monkeypatch, when):
    """Review m3: an exception after the countdown but before `walker.run()`, with a stop landing before it or in the
    cleanup before `stop.close`, leaves no `route-stop-guard` thread (the walker is handed over only at `run()`)."""
    def on_event(event: RunEvent) -> None:
        env.events.append(event)
        if event.kind == "running":
            if when == "before_the_error":
                env.session.stop("button")
            raise RuntimeError("tab gone")

    real_close = env.link.close

    def close() -> None:
        if when == "in_the_cleanup":
            env.session.stop("button")
        real_close()

    monkeypatch.setattr(env.link, "close", close)
    env.on_event = on_event
    with pytest.raises(RuntimeError, match="tab gone"):
        env.build().run()
    assert _ended(env.events).code is None
    assert env.walks == 0
    assert env.stops[0].triggers == ["button"]
    assert _no_run_thread_left()


@pytest.mark.parametrize("stop_before_run", [True, False])
def test_failed_stop_json_still_ends_with_its_reason(env, monkeypatch, capsys, stop_before_run):
    """Review m2: `stop.json`'s reason and source reach `ended` even when writing the file fails."""
    real_write = session._write_json

    def write(path, payload) -> None:
        if path.name == "stop.json":
            raise OSError("disk full")
        real_write(path, payload)

    monkeypatch.setattr(session, "_write_json", write)
    run_session = env.build()
    if stop_before_run:
        run_session.stop("button")
    assert run_session.run() == 0
    assert _ended(env.events).text == ("cancelled/button" if stop_before_run else "max_laps/")
    assert "cleanup failed: stop.json: OSError: disk full\n" in capsys.readouterr().err


# ---- the order inside run(), exit codes and recording ----------------------------------------------------------------

@pytest.mark.parametrize("broken", [None, "map", "icon"])
def test_start_capture_order(env, tmp_path, broken):
    if broken == "map":
        env.map_csv = tmp_path / "nope.csv"
    elif broken == "icon":
        env.positions = Failure("no icon")
    code = env.run()
    if broken is None:
        assert code == 0
        assert env.calls == ["make_positions", "start_capture", "wait_first_frame"]
    else:
        assert code == 5
        assert "start_capture" not in env.calls
    assert _ended(env.events).code == code


_REASONS = {0: "max_laps", 1: "link_down", 6: "error", 10: "no_position"}


@pytest.mark.parametrize("case", ["0", "1", "2", "3-frame", "3-character", "4", "5", "6", "9", "10"])
def test_exit_codes(env, monkeypatch, tmp_path, case):
    expected = int(case.split("-")[0])
    if case == "1":
        monkeypatch.setattr(RouteWalker, "run", lambda self: Success(RunSummary(0, 0, "link_down")))
    elif case == "2":
        monkeypatch.setattr(session.JsonlLegLog, "open", classmethod(lambda cls, directory: Failure("cannot open")))
    elif case == "3-frame":
        env.first = Failure("timeout")
    elif case == "3-character":
        env.positions = SimpleNamespace(read=lambda: Failure("miss"))
    elif case == "4":
        env.link.connect_result = Failure("no board")
    elif case == "5":
        env.map_csv = tmp_path / "nope.csv"
    elif case == "6":
        monkeypatch.setattr(RouteWalker, "run", lambda self: Failure("boom"))  # as test_i_record_with_a_run_failure
    elif case == "9":
        env.stop_start = Failure("no hook")
    elif case == "10":
        env.positions = LostAfter(env.positions, Failure("miss"), reads=1)  # run()'s own read succeeds
    assert env.run() == expected
    ended = _ended(env.events)
    assert ended.code == expected
    if expected in _REASONS:
        source = "no_position" if expected == 10 else ""
        assert ended.text == f"{_REASONS[expected]}/{source}"
        assert _json(env.out / "stop.json")["reason"] == _REASONS[expected]
    else:
        assert ended.text == "none/"
        assert not (env.out / "stop.json").exists()
    assert _no_run_thread_left()


def test_off_map_run_exits_10(env, monkeypatch):
    """Off-graph fix Task 7 (QB213, QB215 "Console line only"): a walker that ends `off_map` makes the walk exit 10
    (the character was lost), `stop.json` reason `off_map` and `ended` text `off_map/`. The walker's own line is the
    console line: the session adds no error line of its own, only the `stopped: off_map` line every stop.json write
    gets (the run's last line)."""
    env.lines = []
    monkeypatch.setattr(RouteWalker, "run", lambda self: Success(RunSummary(0, 3, "off_map")))
    assert env.run() == 10
    ended = _ended(env.events)
    assert ended.code == 10 and ended.text == "off_map/"
    stop_json = _json(env.out / "stop.json")
    assert stop_json["reason"] == "off_map"
    assert stop_json["trigger_source"] is None and stop_json["trigger_ns"] is None  # no stop control trigger
    end = _lines(env.out / "steps.jsonl")[-1]
    assert (end["ev"], end["reason"], end["code"]) == ("end", "off_map", 10)  # the step log ends with the same code
    assert [source for stop in env.stops for source in stop.triggers] == []
    assert [text for text, error in env.lines if error] == []
    assert env.lines[-1] == ("stopped: off_map", False)
    assert _no_run_thread_left()


def test_recording_always_on(env):
    assert env.run() == 0
    assert len(_lines(env.out / "frames" / "index.jsonl")) == 1


@pytest.mark.slow
def test_session_imports_no_tk():
    code = ("import sys; sys.path.insert(0, '.'); import src.route.session; "
            "sys.exit(1 if 'tkinter' in sys.modules else 0)")
    done = subprocess.run([sys.executable, "-I", "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


# ---- run() never ends without an `ended` event -----------------------------------------------------------------------

def test_walker_exception_posts_one_ended_with_code_7(env, monkeypatch):
    monkeypatch.setattr(RouteWalker, "run", _raise_kaboom)
    assert env.run() == 7
    ended = _ended(env.events)
    assert ended.code == 7 and ended.text == "error/"


def test_exception_outside_the_walk_posts_ended_none_and_reraises(env):
    def broken_clock():
        raise RuntimeError("boom")

    env.make_clock = broken_clock
    with pytest.raises(RuntimeError, match="boom"):
        env.build().run()
    ended = _ended(env.events)
    assert ended.code is None and ended.text == "the run stopped unexpectedly: RuntimeError: boom"
    assert env.log == ["release_all", "link.close"]  # the cleanup still ran, keys first


# ---- the recorder wrapper (spec 5.6 "Recording is always on") --------------------------------------------------------

def _warnings(events: list[RunEvent]) -> list[str]:
    return [event.text for event in events if event.kind == "warning"]


def _low_space_text(out_dir) -> str:
    """Built from the constant, as C1 says, so RT-13 part B changes the constant only."""
    return (f"Recording stopped: less than {session.RECORD_FLOOR_BYTES / 2**30:g} GB free on "
            f"{session.drive_name(out_dir)}. The walk goes on.")


class NewFrames:
    """A `shared` whose `minimap_frame` is a new array on every read, so `record` saves a frame at every poll."""

    def __init__(self) -> None:
        self.minimap_frame_lock = threading.Lock()

    @property
    def minimap_frame(self) -> np.ndarray:
        return np.zeros((4, 6, 3), np.uint8)


def test_recorder_oserror_is_one_warning(env, monkeypatch, capsys):
    def full_disk(*args, **kwargs) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(record_minimap.cv2, "imwrite", full_disk)  # the env's frame is set: record saves at once
    assert env.run() == 0
    assert _warnings(env.events) == ["Recording stopped: OSError: disk full. The walk goes on."]
    assert env.walks == 1
    assert "record failed: OSError: disk full\n" in capsys.readouterr().err  # RT-3-S2 (kept)


def test_record_guarded_failure_is_one_warning(tmp_path, capsys):
    out_dir = tmp_path / "frames"
    out_dir.mkdir()
    (out_dir / INDEX_NAME).write_text("", encoding="utf-8")  # record refuses to overwrite: a Failure
    events: list[RunEvent] = []
    session._record_guarded(NewFrames(), out_dir, threading.Event(), events.append)
    assert len(events) == 1 and events[0].kind == "warning"
    assert events[0].text.startswith("Recording stopped: ") and events[0].text.endswith(". The walk goes on.")
    assert "already exists" in events[0].text
    err = capsys.readouterr().err  # the old line, word for word (RT-3-S2)
    assert err.startswith("record failed: ") and "already exists" in err and err.count("\n") == 1


def test_record_guarded_stops_below_the_floor(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(session, "DISK_CHECK_FRAMES", 3)
    monkeypatch.setattr(session, "RECORD_FPS", 1000.0)
    monkeypatch.setattr(session, "free_bytes", lambda path: session.RECORD_FLOOR_BYTES - 1)
    out_dir = tmp_path / "run" / "frames"
    events: list[RunEvent] = []
    session._record_guarded(NewFrames(), out_dir, threading.Event(), events.append)  # nothing else sets the event
    assert len(_lines(out_dir / INDEX_NAME)) == 3  # the 3rd frame is saved before record's stop check
    assert [(event.kind, event.text) for event in events] == [("warning", _low_space_text(out_dir))]
    assert capsys.readouterr().err == (f"record stopped: less than {session.RECORD_FLOOR_BYTES / 2**30:g} GB free on "
                                       f"{session.drive_name(out_dir)}\n")  # RT-3-S2 (kept)


def test_disk_check_constants():
    assert session.DISK_CHECK_FRAMES == 600 and session.RECORD_FPS == 10.0


def test_recorder_numbers_follow_the_measured_rate():
    """Choice 15 (RT-13-R): the refusal is max(2 GB, 4 h of recording) and the floor max(1 GB, 2 h), each rounded up to
    0.5 GB, with the rate being the one-decimal constant the tab shows. The other recorder tests build their texts from
    the constants, so only this one pins the numbers."""
    rate = session.RECORD_GB_PER_HOUR
    assert rate > 0 and round(rate, 1) == rate
    refusal = math.ceil(max(2.0, 4 * rate) * 2) / 2
    floor = math.ceil(max(1.0, 2 * rate) * 2) / 2
    assert session.START_FREE_BYTES == refusal * 2**30
    assert session.RECORD_FLOOR_BYTES == floor * 2**30


def test_low_space_in_a_run(env, monkeypatch):
    monkeypatch.setattr(session, "DISK_CHECK_FRAMES", 1)
    monkeypatch.setattr(session, "free_bytes", lambda path: session.RECORD_FLOOR_BYTES - 1)
    assert env.run() == 0
    assert _warnings(env.events) == [_low_space_text(env.out / "frames")]
    assert env.walks == 1  # the walk goes on


def test_free_bytes_uses_the_nearest_existing_parent(tmp_path):
    assert abs(session.free_bytes(tmp_path / "a" / "b") - shutil.disk_usage(tmp_path).free) < 100 * 2**20


# ---- RT-8c piece 4: the run's lines (H71; RDO110-D3 A) -------------------------------------------------------------


def test_session_log_default_prints_as_before(capsys):
    """The CLI passes no `log`: the default prints each line as before, an error line on stderr and the rest on stdout
    (walk's tests are the oracle for the words)."""
    assert inspect.signature(RunSession).parameters["log"].default is session._print_line
    session._print_line("walk failed: x", True)
    session._print_line("stopped: max_laps", False)
    assert capsys.readouterr() == ("stopped: max_laps\n", "walk failed: x\n")


def test_session_lines_go_to_log(env, tmp_path, capsys):
    """With `log` given (the Route tab's), each line the run prints goes to it with its error flag and nothing to the
    terminal: the cancel's `stopped:` line, a failed first frame's `error:` line and the recorder's failure line."""
    env.lines = []
    run_session = env.build()
    run_session.stop("button")
    assert run_session.run() == 0
    assert env.lines == [("stopped: cancelled (button)", False)]
    env.lines = []
    env.first = Failure("no frame")
    assert env.run() == 3
    assert env.lines == [("error: no first frame: no frame", True)]
    out_dir = tmp_path / "frames_log"
    out_dir.mkdir()
    (out_dir / INDEX_NAME).write_text("", encoding="utf-8")  # record refuses to overwrite: a Failure
    lines: list[tuple[str, bool]] = []
    session._record_guarded(NewFrames(), out_dir, threading.Event(), lambda event: None,
                            lambda text, error: lines.append((text, error)))
    assert len(lines) == 1 and lines[0][0].startswith("record failed: ") and lines[0][1] is True
    assert capsys.readouterr() == ("", "")


def test_lines_below_the_session_go_to_log(env, monkeypatch, capsys):
    """RT-8c piece 4b (H71; ruling orch 111): with `log` given, the lines printed below the session (the walker's two,
    the step log's, run_cleanup's and the stop control's) reach it as error lines, in order, and none the terminal.
    The CLI's side, word for word on stderr, is `test_k_lines_below_the_session_print_on_stderr_as_before`."""
    env.lines = []
    break_below_the_session(monkeypatch, env.link)
    assert env.run() == 0
    stop = env.stops[0]
    assert stop.log_line is not None
    stop.log_line("stop control: close hook failed: RuntimeError: y")
    assert [(text, error) for text, error in env.lines if error] == [(text, True) for text in BELOW_THE_SESSION]
    assert ("stopped: max_laps", False) in env.lines
    assert capsys.readouterr() == ("", "")


def test_session_hands_its_error_line_to_each_component(env, monkeypatch):
    """The session builds the walker, the step log and the stop control, and calls run_cleanup, with one `log`: its
    error line, which is its own `log` with the error flag set."""
    env.lines = []
    seen: dict[str, object] = {}
    real_cleanup = runtime.run_cleanup

    def cleanup(steps, *, log):
        seen["run_cleanup"] = log
        real_cleanup(steps, log=log)

    monkeypatch.setattr(runtime, "run_cleanup", cleanup)

    def run(self):
        seen["walker"] = self._log_line
        seen["step_log"] = vars(self._observer)["_log"]  # the observer is the session's StepLog
        return Success(RunSummary(0, 0, "max_laps"))

    monkeypatch.setattr(RouteWalker, "run", run)
    made = env.build()
    assert made.run() == 0
    seen["stop_control"] = env.stops[0].log_line
    assert seen == dict.fromkeys(("run_cleanup", "walker", "step_log", "stop_control"), made._error_line)
    made._error_line("x")
    assert env.lines[-1] == ("x", True)


# ---- the map's rope file (rope fix, Task 6, H88, C11) -----------------------------------------------------------------

def _copy_map(tmp_path, *, ropes_text=None):
    """FF2's map copied to a tmp folder, with `ropes_text` as the rope file beside it (none: no file)."""
    csv = tmp_path / "map.csv"
    shutil.copy(FIXTURE, csv)
    if ropes_text is not None:
        csv.with_suffix(".ropes.json").write_text(ropes_text, encoding="utf-8")
    return csv


def test_a_bad_rope_file_refuses_the_walk_before_anything_starts(env, tmp_path):
    env.map_csv = _copy_map(tmp_path, ropes_text="not json")
    env.lines = []

    assert env.run() == 5
    assert env.calls == [] and env.walks == 0   # no positions, no capture, no link: refused with the map load
    assert _ended(env.events).code == 5
    texts = [text for text, error in env.lines if error]
    assert any("map.ropes.json" in text and "the file is not JSON" in text and "How to fix it" in text for text in texts)
    assert not (env.out / "legs.jsonl").exists()


def test_a_map_without_a_rope_file_starts_as_today(env, tmp_path, monkeypatch):
    seen = []
    real_init = RouteWalker.__init__

    def spy(self, *args, **kwargs):
        seen.append(kwargs["ropes"])
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(RouteWalker, "__init__", spy)
    env.map_csv = _copy_map(tmp_path)

    assert env.run() == 0
    assert env.walks == 1 and seen[0].at((106, 49)) is None   # an empty table: no zone anywhere


def test_the_walker_gets_the_zones_of_the_rope_file(env, monkeypatch):
    seen = []
    real_init = RouteWalker.__init__

    def spy(self, *args, **kwargs):
        seen.append(kwargs["ropes"])
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(RouteWalker, "__init__", spy)

    assert env.run() == 0
    assert [seen[0].at((106, 49)), seen[0].at((46, 35)), seen[0].at((90, 35))] == [1, 0, None]   # FF2's two ropes
