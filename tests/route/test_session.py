"""Route-tab spec 5.6 and 8: `RunSession` (src/route/session.py) over the simulator's seams.

Every run here uses the simulator's `SimPositionSource` and `SimLink`, a fake first-frame wait, a fake stop control,
`SimClock` and a recording `sleep`; the autouse `_no_real_blelink` fails any test that would build a real `BleLink`.
The small fakes are imported flat from test_walk_cli.py (RTB-PF m8)."""

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


@pytest.fixture
def env(monkeypatch, tmp_path):
    """FF2 terrain, noise off, start at (113, 60), one SimClock everywhere, one lap, no countdown, a shared frame set.
    The fake stop control calls `on_stop` from `trigger`, as the real worker does; `RouteWalker.run` counts its calls."""
    rg, rotation = session._load_route(FIXTURE, Kanna()).unwrap()
    clock = SimClock()
    params = SimParams()
    world = SimWorld(load_terrain(TERRAIN).unwrap(), rg.reach_h, rg.reach_v, params, NoiseConfig.off(),
                     np.random.default_rng(0), (113.0, 60.0))
    e = SimpleNamespace(log=[], calls=[], stops=[], events=[], sleeps=[], walks=0, out=tmp_path / "run",
                        rotation=rotation, shared=runtime.SharedFrame(), map_csv=FIXTURE, laps=1, countdown_s=0.0,
                        first=Success(None), stop_start=Success(None), on_start=None, on_sleep=None, may_walk=None,
                        make_clock=lambda: clock, session=None)
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

    def make_stop(on_stop):
        stop = HookStop(on_stop, e.log, e.stop_start, True, e.link)
        stop.on_start = e.on_start
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
        e.session = RunSession(settings, e.shared, on_event=e.events.append, make_positions=make_positions,
                               wait_first_frame=wait_first_frame, make_link=lambda: e.link, make_clock=e.make_clock,
                               make_stop=make_stop, start_capture=start_capture, may_walk=e.may_walk, sleep=sleep)
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


def test_recorder_oserror_is_one_warning(env, monkeypatch):
    def full_disk(*args, **kwargs) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(record_minimap.cv2, "imwrite", full_disk)  # the env's frame is set: record saves at once
    assert env.run() == 0
    assert _warnings(env.events) == ["Recording stopped: OSError: disk full. The walk goes on."]
    assert env.walks == 1


def test_record_guarded_failure_is_one_warning(tmp_path):
    out_dir = tmp_path / "frames"
    out_dir.mkdir()
    (out_dir / INDEX_NAME).write_text("", encoding="utf-8")  # record refuses to overwrite: a Failure
    events: list[RunEvent] = []
    session._record_guarded(NewFrames(), out_dir, threading.Event(), events.append)
    assert len(events) == 1 and events[0].kind == "warning"
    assert events[0].text.startswith("Recording stopped: ") and events[0].text.endswith(". The walk goes on.")
    assert "already exists" in events[0].text


def test_record_guarded_stops_below_the_floor(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "DISK_CHECK_FRAMES", 3)
    monkeypatch.setattr(session, "RECORD_FPS", 1000.0)
    monkeypatch.setattr(session, "free_bytes", lambda path: session.RECORD_FLOOR_BYTES - 1)
    out_dir = tmp_path / "run" / "frames"
    events: list[RunEvent] = []
    session._record_guarded(NewFrames(), out_dir, threading.Event(), events.append)  # nothing else sets the event
    assert len(_lines(out_dir / INDEX_NAME)) == 3  # the 3rd frame is saved before record's stop check
    assert [(event.kind, event.text) for event in events] == [("warning", _low_space_text(out_dir))]


def test_disk_check_constants():
    assert session.DISK_CHECK_FRAMES == 600 and session.RECORD_FPS == 10.0


def test_low_space_in_a_run(env, monkeypatch):
    monkeypatch.setattr(session, "DISK_CHECK_FRAMES", 1)
    monkeypatch.setattr(session, "free_bytes", lambda path: session.RECORD_FLOOR_BYTES - 1)
    assert env.run() == 0
    assert _warnings(env.events) == [_low_space_text(env.out / "frames")]
    assert env.walks == 1  # the walk goes on


def test_free_bytes_uses_the_nearest_existing_parent(tmp_path):
    assert abs(session.free_bytes(tmp_path / "a" / "b") - shutil.disk_usage(tmp_path).free) < 100 * 2**20
