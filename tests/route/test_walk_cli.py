"""Plan task B19: the runtime stop control and capture helpers (`src/route/runtime.py`) and the walk CLI (`walk.py`).

No test here installs a real hook except the Windows-only one marked below, and none sends a key."""

import json
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.classes.classes import Kanna
from src.input import input_layer
from src.input.link import InputError
from src.route import runtime, walk
from src.route.character import CharacterLocator
from src.route.leg_log import LegOutcome, LegRecord, MemorySink, MoveEntry
from src.route.runtime import StopControl
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.world import NoiseConfig, SimParams, SimWorld, load_terrain
from src.route.walker import RouteWalker, RunSummary


class Remove:
    """The `remove()` callable a fake `_install_hook` returns; counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


@pytest.fixture
def hook(monkeypatch):
    """Replace the real hook with a recording fake; yields its `Remove`."""
    remove = Remove()
    monkeypatch.setattr(StopControl, "_install_hook", lambda self: remove)
    return remove


@pytest.fixture
def saved_sigint():
    """Safety net on top of what close() does: restore the SIGINT handler the test started with."""
    before = signal.getsignal(signal.SIGINT)
    try:
        yield before
    finally:
        signal.signal(signal.SIGINT, before)  # pyright: ignore[reportArgumentType]


def _worker_alive() -> bool:
    return any(t.name == "stop-control" and t.is_alive() for t in threading.enumerate())


def test_trigger_returns_at_once_while_on_stop_blocks(hook, saved_sigint):
    entered = threading.Event()

    def on_stop() -> None:
        entered.set()
        time.sleep(1.0)

    stop = StopControl(on_stop)
    assert is_successful(stop.start())
    try:
        t0 = time.perf_counter()
        stop.trigger()
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.010
        assert entered.wait(1.0)
    finally:
        stop.close()


def test_trigger_twice_calls_on_stop_once(hook, saved_sigint):
    calls: list[int] = []
    done = threading.Event()

    def on_stop() -> None:
        calls.append(1)
        done.set()

    stop = StopControl(on_stop)
    assert is_successful(stop.start())
    try:
        stop.trigger()
        stop.trigger()
        assert done.wait(1.0)
    finally:
        stop.close()
    assert calls == [1]


def test_first_trigger_keeps_its_source_and_time(hook, saved_sigint):
    stop = StopControl(lambda: None)
    stop.trigger("sigint")
    first_ns = stop.trigger_ns
    stop.trigger()
    assert stop.trigger_source == "sigint"
    assert first_ns is not None and stop.trigger_ns == first_ns


def test_sigint_handler_triggers_on_stop_once(hook, saved_sigint):
    ran = threading.Event()
    calls: list[int] = []

    def on_stop() -> None:
        calls.append(1)
        ran.set()

    stop = StopControl(on_stop)
    assert is_successful(stop.start())
    try:
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler) and handler is not saved_sigint
        handler(signal.SIGINT, None)
        assert stop.trigger_source == "sigint"  # set synchronously
        assert ran.wait(1.0)
    finally:
        stop.close()
    assert calls == [1]


def test_ctrl_c_while_the_main_thread_holds_the_lock_does_not_deadlock(hook, saved_sigint):
    """B19-R M1: the SIGINT handler runs on the main thread, also while main holds the lock (walk's lost-leg sink calls
    trigger() on main; close() runs on main). The non-blocking acquire fails fast on a plain Lock instead of hanging."""
    ran = threading.Event()
    stop = StopControl(ran.set)
    assert is_successful(stop.start())
    try:
        with stop._lock:  # pyright: ignore[reportPrivateUsage]
            assert stop._lock.acquire(blocking=False)  # pyright: ignore[reportPrivateUsage]
            stop._lock.release()  # pyright: ignore[reportPrivateUsage]
            signal.raise_signal(signal.SIGINT)  # the handler runs here, on this thread, inside the lock
            assert stop.trigger_source == "sigint"
        assert ran.wait(1.0)
    finally:
        stop.close()


def test_close_without_trigger_undoes_start(hook, saved_sigint):
    calls: list[int] = []
    stop = StopControl(lambda: calls.append(1))
    assert is_successful(stop.start())
    assert _worker_alive()
    stop.close()
    assert calls == []
    assert not _worker_alive()
    assert hook.calls == 1
    assert signal.getsignal(signal.SIGINT) is saved_sigint
    stop.close()
    assert hook.calls == 1


def test_close_before_start_does_nothing(hook, saved_sigint):
    stop = StopControl(lambda: None)
    stop.close()
    assert signal.getsignal(signal.SIGINT) is saved_sigint
    assert hook.calls == 0


def test_close_keeps_a_handler_installed_after_start(hook, saved_sigint):
    def x(signum, frame) -> None:
        pass

    stop = StopControl(lambda: None)
    assert is_successful(stop.start())
    signal.signal(signal.SIGINT, x)
    stop.close()
    assert signal.getsignal(signal.SIGINT) is x


def test_second_close_keeps_a_handler_installed_after_the_first(hook, saved_sigint):
    def x(signum, frame) -> None:
        pass

    stop = StopControl(lambda: None)
    assert is_successful(stop.start())
    stop.close()
    signal.signal(signal.SIGINT, x)
    stop.close()
    assert signal.getsignal(signal.SIGINT) is x


def test_close_off_the_main_thread_ends_worker_and_hook_but_leaves_sigint(hook, saved_sigint):
    stop = StopControl(lambda: None)
    assert is_successful(stop.start())
    installed = signal.getsignal(signal.SIGINT)
    errors: list[BaseException] = []

    def close() -> None:
        try:
            stop.close()
        except BaseException as exc:
            errors.append(exc)

    th = threading.Thread(target=close)
    th.start()
    th.join(10)
    assert errors == []
    assert signal.getsignal(signal.SIGINT) is installed
    assert not _worker_alive()
    assert hook.calls == 1


def test_previous_handler_none_is_restored_as_default_int_handler(hook, saved_sigint, monkeypatch):
    real_getsignal = signal.getsignal
    real_signal = signal.signal

    def s(signum, frame) -> None:  # the sentinel that stands for "a handler installed from C"
        pass

    real_signal(signal.SIGINT, s)
    try:
        def getsignal(signum):
            value = real_getsignal(signum)
            return None if value is s else value

        def set_signal(signum, handler):
            previous = real_signal(signum, handler)
            return None if previous is s else previous

        monkeypatch.setattr(signal, "getsignal", getsignal)
        monkeypatch.setattr(signal, "signal", set_signal)
        stop = StopControl(lambda: None)
        assert is_successful(stop.start())
        stop.close()
        monkeypatch.undo()
        assert real_getsignal(signal.SIGINT) is signal.default_int_handler
    finally:
        real_signal(signal.SIGINT, saved_sigint)


def test_start_fails_when_the_hook_cannot_be_installed(monkeypatch, saved_sigint):
    def failing_install(self):
        raise OSError("SetWindowsHookExW failed")

    monkeypatch.setattr(StopControl, "_install_hook", failing_install)
    before = {t.ident for t in threading.enumerate()}
    stop = StopControl(lambda: None)
    result = stop.start()
    assert not is_successful(result)
    assert "SetWindowsHookExW failed" in result.failure()
    assert result.failure().startswith("hook: ")
    assert signal.getsignal(signal.SIGINT) is saved_sigint
    assert {t.ident for t in threading.enumerate()} <= before
    stop.close()  # nothing was installed, so there is no remove() to call; it must not raise


def test_start_off_the_main_thread_fails_and_close_removes_the_hook(hook, saved_sigint):
    stop = StopControl(lambda: None)
    results: list[object] = []
    th = threading.Thread(target=lambda: results.append(stop.start()))
    th.start()
    th.join(10)
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, Failure) and str(result.failure()).startswith("sigint: ")
    assert signal.getsignal(signal.SIGINT) is saved_sigint
    assert not _worker_alive()
    stop.close()
    assert hook.calls == 1


def test_trigger_before_start_calls_on_stop_once_started(hook, saved_sigint):
    ran = threading.Event()
    stop = StopControl(ran.set)
    stop.trigger("maple_check")
    assert is_successful(stop.start())
    try:
        assert ran.wait(1.0)
    finally:
        stop.close()


def _shared(frame=None) -> SimpleNamespace:
    return SimpleNamespace(minimap_frame_lock=threading.Lock(), minimap_frame=frame)


def test_wait_first_frame_succeeds_once_a_frame_arrives():
    shared = _shared()

    def later() -> None:
        time.sleep(0.1)
        with shared.minimap_frame_lock:
            shared.minimap_frame = np.zeros((4, 4, 3), np.uint8)

    threading.Thread(target=later, daemon=True).start()
    assert runtime.wait_first_frame(shared, 5.0) == Success(None)


def test_wait_first_frame_times_out():
    t0 = time.monotonic()
    result = runtime.wait_first_frame(_shared(), 0.1)
    assert not is_successful(result)
    assert time.monotonic() - t0 < 2.0


@pytest.mark.skipif(sys.platform != "win32", reason="installs a real WH_KEYBOARD_LL hook; sends no key")
def test_real_hook_installs_and_removes():
    remove = runtime._install_keyboard_hook(0x7B, lambda: None)
    assert any(t.name == "stop-hook" and t.is_alive() for t in threading.enumerate())
    remove()
    assert not any(t.name == "stop-hook" and t.is_alive() for t in threading.enumerate())


# ---- the walk CLI (src/route/walk.py) ---------------------------------------------------------------------------------
# Every test below runs `main` over the simulator's seams (SimWorld, SimLink, SimClock); nothing reaches a board.

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "fossil_foundry_2.csv"
TERRAIN = REPO / "tests" / "fixtures" / "ff2_terrain.json"
REAL_MAKE_POSITIONS = walk._make_positions
OFF_GRAPH = Success((500, 500))  # far outside the minimap: the planner cannot attach it


class LinkWrap:
    """B10's SimLink plus `connect` and `close`, recording calls into the shared `log`."""

    def __init__(self, inner: SimLink, log: list[str]) -> None:
        self.inner = inner
        self.log = log
        self.connect_result: Result[None, str] = Success(None)
        self.plays = 0
        self.release_calls = 0
        self.close_calls = 0
        self.reject = False      # play() fails with REJECTED (no move) while set
        self.on_release = None   # called at each release_all, before it may raise
        self.on_close = None
        self.release_raises: BaseException | None = None
        self.close_raises: BaseException | None = None

    def connect(self):
        return self.connect_result

    def play(self, steps):
        self.plays += 1
        if self.reject:
            return Failure(InputError.REJECTED)
        return self.inner.play(steps)

    def release_all(self):
        self.release_calls += 1
        self.log.append("release_all")
        if self.on_release is not None:
            self.on_release()
        if self.release_raises is not None:
            raise self.release_raises
        return self.inner.release_all()

    def close(self) -> None:
        self.close_calls += 1
        self.log.append("link.close")
        if self.on_close is not None:
            self.on_close()
        if self.close_raises is not None:
            raise self.close_raises


class FakeStop:
    """The stop control's surface. `trigger` keeps the first source and time; with `calls_on_stop` it then calls the
    `on_stop` it was built with, as the real worker would."""

    def __init__(self, on_stop, log: list[str], start_result, calls_on_stop: bool, link: LinkWrap) -> None:
        self.on_stop = on_stop
        self.log = log
        self.start_result = start_result
        self.calls_on_stop = calls_on_stop
        self.link = link
        self.trigger_ns: int | None = None
        self.trigger_source: str | None = None
        self.triggers: list[str] = []
        self.plays_at_trigger: int | None = None
        self.close_calls = 0

    def start(self):
        return self.start_result

    def trigger(self, source: str = "hotkey") -> None:
        self.triggers.append(source)
        if self.trigger_source is not None:
            return
        self.trigger_ns = time.perf_counter_ns()
        self.trigger_source = source
        self.plays_at_trigger = self.link.plays
        if self.calls_on_stop:
            self.on_stop()

    def close(self) -> None:
        self.close_calls += 1
        self.log.append("stop.close")


class LostAfter:
    """Delegates to `inner` until `lost` is set (or after `reads` reads), then returns `lost_read` for ever."""

    def __init__(self, inner, lost_read, reads: int | None = None) -> None:
        self.inner = inner
        self.lost_read = lost_read
        self.reads = reads
        self.lost = False

    def read(self):
        if self.lost:
            return self.lost_read
        if self.reads is not None:
            self.reads -= 1
            if self.reads <= 0:
                self.lost = True
        return self.inner.read()


def _alive_names() -> set[str]:
    return {t.name for t in threading.enumerate() if t.is_alive()}


def _alive_idents() -> set[int | None]:
    return {t.ident for t in threading.enumerate() if t.is_alive()}


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def sim(monkeypatch, tmp_path):
    """The end-to-end seams of plan B19: FF2 terrain, noise off, start at (113, 60), one SimClock everywhere."""
    rg, rotation = walk._load_route(FIXTURE, Kanna()).unwrap()
    clock = SimClock()
    params = SimParams()
    world = SimWorld(load_terrain(TERRAIN).unwrap(), rg.reach_h, rg.reach_v, params, NoiseConfig.off(),
                     np.random.default_rng(0), (113.0, 60.0))
    env = SimpleNamespace(log=[], stops=[], out=tmp_path / "run", clock=clock, world=world, rotation=rotation,
                          capture_calls=0, link_calls=0, stop_start=Success(None), stop_calls_on_stop=False,
                          frame=None)
    env.link = LinkWrap(SimLink(world, clock, params), env.log)
    env.positions = SimPositionSource(world, clock, 0.0, np.random.default_rng(1))

    def start_capture(shared) -> None:
        env.capture_calls += 1
        if env.frame is not None:
            with shared.minimap_frame_lock:
                shared.minimap_frame = env.frame

    def make_link():
        env.link_calls += 1
        return env.link

    def make_stop(on_stop):
        stop = FakeStop(on_stop, env.log, env.stop_start, env.stop_calls_on_stop, env.link)
        env.stops.append(stop)
        return stop

    monkeypatch.setattr(walk, "_start_capture", start_capture)
    monkeypatch.setattr(walk, "_wait_first_frame", lambda shared, timeout_s: Success(None))
    monkeypatch.setattr(walk, "_make_positions", lambda shared: env.positions)
    monkeypatch.setattr(walk, "_make_link", make_link)
    monkeypatch.setattr(walk, "_make_clock", lambda: clock)
    monkeypatch.setattr(walk, "_make_stop_control", make_stop)
    monkeypatch.setattr(walk, "_countdown_s", 0)
    monkeypatch.setattr(input_layer, "_clock", clock.now_ns)
    monkeypatch.setattr(input_layer, "_sleep", clock.sleep)

    def main(*extra: str) -> int:
        code = walk.main(["--map", str(FIXTURE), "--supervised", "--out", str(env.out), *extra])
        assert isinstance(code, int)
        return code

    env.main = main
    return env


def _patch_walk_leg(monkeypatch, before) -> None:
    """Wrap the frozen `RouteWalker.walk_leg` so a test can act just before leg (lap, leg) starts."""
    original = RouteWalker.walk_leg

    def walk_leg(self, lap, leg, src, dst):
        before(lap, leg)
        return original(self, lap, leg, src, dst)

    monkeypatch.setattr(RouteWalker, "walk_leg", walk_leg)


def _raise_kaboom(self):
    raise RuntimeError("kaboom")


def test_main_refuses_without_supervised(sim, capsys):
    assert walk.main(["--map", str(FIXTURE), "--out", str(sim.out)]) == 2
    assert "--supervised" in capsys.readouterr().err
    assert sim.capture_calls == 0


def test_connect_failure_exits_4_without_release_all(sim):
    sim.link.connect_result = Failure("no board")
    assert sim.main("--laps", "1") == 4
    assert sim.link.release_calls == 0
    assert sim.link.close_calls == 1
    assert not (sim.out / "stop.json").exists()


def test_end_to_end_over_the_simulator(sim):
    assert sim.main("--laps", "1") == 0
    assert len(_lines(sim.out / "legs.jsonl")) == 1 + len(sim.rotation) == 8
    assert len(_lines(sim.out / "laps.jsonl")) == 1
    session = _json(sim.out / "session.json")
    assert session["leg_timeout_s"] == 60.0 and session["map"] == str(FIXTURE) and len(session["map_sha256"]) == 64
    assert isinstance(session["seed"], int)
    assert sim.link.release_calls >= 1 and sim.link.close_calls >= 1
    assert _json(sim.out / "stop.json") == {"reason": "max_laps", "trigger_ns": None, "trigger_source": None}
    assert sim.stops[0].close_calls == 1


# (a)-(c) failure exits (PB-B19)

def test_a_run_failure_exits_6(sim, monkeypatch, capsys):
    monkeypatch.setattr(RouteWalker, "run", lambda self: Failure("boom"))
    assert sim.main("--laps", "1") == 6
    assert "walk failed: boom" in capsys.readouterr().err
    assert _json(sim.out / "stop.json") == {"reason": "error", "error": "boom", "trigger_ns": None,
                                            "trigger_source": None}
    assert sim.link.release_calls >= 1 and sim.link.close_calls >= 1


def test_b_run_raising_exits_7(sim, monkeypatch, capsys):
    monkeypatch.setattr(RouteWalker, "run", _raise_kaboom)
    assert sim.main("--laps", "1") == 7
    assert "walk crashed: RuntimeError: kaboom" in capsys.readouterr().err
    stop = _json(sim.out / "stop.json")
    assert stop["reason"] == "error" and stop["error"] == "RuntimeError: kaboom"
    assert sim.link.release_calls >= 1 and sim.link.close_calls >= 1


def test_c_link_down_summary_exits_1(sim, monkeypatch, capsys):
    monkeypatch.setattr(RouteWalker, "run", lambda self: Success(RunSummary(0, 0, "link_down")))
    assert sim.main("--laps", "1") == 1
    err = capsys.readouterr().err
    assert "walk failed" not in err and "walk crashed" not in err
    assert _json(sim.out / "stop.json")["reason"] == "link_down"


# (d) exit 3, (e) exit 5

def test_d_no_first_frame_exits_3(sim, monkeypatch):
    monkeypatch.setattr(walk, "_wait_first_frame", lambda shared, timeout_s: Failure("timeout"))
    assert sim.main("--laps", "1") == 3
    assert sim.link_calls == 0
    assert not (sim.out / "stop.json").exists()


def test_d_character_not_found_exits_3(sim):
    sim.positions = SimpleNamespace(read=lambda: Failure("miss"))
    assert sim.main("--laps", "1") == 3
    assert sim.link_calls == 0
    assert not (sim.out / "stop.json").exists()


def test_e_missing_map_exits_5(sim, tmp_path):
    code = walk.main(["--map", str(tmp_path / "nope.csv"), "--supervised", "--out", str(sim.out)])
    assert code == 5
    assert sim.link_calls == 0


def test_e_character_icon_failure_exits_5(sim, monkeypatch):
    monkeypatch.setattr(walk, "_make_positions", REAL_MAKE_POSITIONS)
    monkeypatch.setattr(CharacterLocator, "from_files", staticmethod(lambda *args: Failure("no icon")))
    assert sim.main("--laps", "1") == 5
    assert sim.link_calls == 0


# (f) the --minutes timer

def test_f_timer_stops_the_run(sim):
    sim.stop_calls_on_stop = True
    t0 = time.monotonic()
    assert sim.main("--minutes", "0.02") == 0
    assert time.monotonic() - t0 < 10.0
    stop = _json(sim.out / "stop.json")
    assert stop["reason"] == "stopped" and stop["trigger_source"] == "timer" and stop["trigger_ns"] is not None


def test_f_timer_is_cancelled_and_joined_when_the_laps_end_first(sim):
    sim.stop_calls_on_stop = True
    before = _alive_idents()
    assert sim.main("--laps", "1", "--minutes", "60") == 0
    assert sim.stops[0].triggers == []
    assert _json(sim.out / "stop.json")["trigger_source"] is None
    assert _alive_idents() <= before


# (h) the --minutes range

@pytest.mark.parametrize("minutes", ["0", "-1", "nan", "inf", "1e6"])
def test_h_minutes_out_of_range_exits_2(sim, minutes):
    assert sim.main("--minutes", minutes) == 2
    assert sim.capture_calls == 0


def test_h_half_a_minute_is_accepted(sim):
    assert sim.main("--laps", "1", "--minutes", "0.5") == 0


# (i) --record (B19-record-D1)

def test_i_record_saves_the_shared_frame_once(sim):
    sim.frame = np.zeros((4, 6, 3), np.uint8)
    assert sim.main("--laps", "1", "--record") == 0
    assert len((sim.out / "frames" / "index.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert (sim.out / "frames" / "frame_000000.png").exists()
    assert "record" not in _alive_names()


def test_i_record_with_a_run_failure(sim, monkeypatch):
    sim.frame = np.zeros((4, 6, 3), np.uint8)
    monkeypatch.setattr(RouteWalker, "run", lambda self: Failure("boom"))
    assert sim.main("--laps", "1", "--record") == 6
    assert "record" not in _alive_names()


def test_i_record_never_starts_when_connect_fails(sim):
    sim.frame = np.zeros((4, 6, 3), np.uint8)
    sim.link.connect_result = Failure("x")
    assert sim.main("--laps", "1", "--record") == 4
    assert not (sim.out / "frames").exists()


# (j) cleanup order (PB-amend2-D1, B19-record-D1)

def test_j_cleanup_order(sim):
    sim.frame = np.zeros((4, 6, 3), np.uint8)
    timer_alive: list[bool] = []
    at_close: list[tuple[bool, bool]] = []
    sim.link.on_release = lambda: timer_alive.append("walk-timer" in _alive_names())
    sim.link.on_close = lambda: at_close.append(("record" in _alive_names(), (sim.out / "stop.json").exists()))
    assert sim.main("--laps", "1", "--minutes", "60", "--record") == 0
    assert timer_alive[-1] is False
    assert at_close == [(False, True)]
    assert sim.log[-3:] == ["release_all", "link.close", "stop.close"]


def test_j_cleanup_order_when_run_raises(sim, monkeypatch):
    monkeypatch.setattr(RouteWalker, "run", _raise_kaboom)
    assert sim.main("--laps", "1", "--minutes", "60", "--record") == 7
    assert sim.log[-3:] == ["release_all", "link.close", "stop.close"]


# (k) cleanup raises (PB-amend2c-D1)

def test_k_link_close_raising_keeps_the_exit_code(sim, capsys):
    sim.link.close_raises = RuntimeError("x")
    assert sim.main("--laps", "1") == 0
    assert "cleanup failed: link.close: RuntimeError: x" in capsys.readouterr().err
    assert sim.stops[0].close_calls == 1


def test_k_link_close_raising_after_a_run_failure(sim, monkeypatch, capsys):
    sim.link.close_raises = RuntimeError("x")
    monkeypatch.setattr(RouteWalker, "run", lambda self: Failure("boom"))
    assert sim.main("--laps", "1") == 6
    err = capsys.readouterr().err
    assert "walk failed: boom" in err and "cleanup failed: link.close: RuntimeError: x" in err


def test_k_link_close_raising_after_run_raised(sim, monkeypatch, capsys):
    sim.link.close_raises = RuntimeError("x")
    monkeypatch.setattr(RouteWalker, "run", _raise_kaboom)
    assert sim.main("--laps", "1") == 7
    err = capsys.readouterr().err
    assert "walk crashed: RuntimeError: kaboom" in err and "cleanup failed: link.close: RuntimeError: x" in err


def test_k_release_all_and_close_both_raising(sim, monkeypatch, capsys):
    original = RouteWalker.run

    def run(self):
        try:
            return original(self)
        finally:
            sim.link.release_raises = RuntimeError("r")

    monkeypatch.setattr(RouteWalker, "run", run)
    sim.link.close_raises = RuntimeError("c")
    assert sim.main("--laps", "1") == 0
    failed = [line for line in capsys.readouterr().err.splitlines() if line.startswith("cleanup failed:")]
    assert failed == ["cleanup failed: release_all: RuntimeError: r", "cleanup failed: link.close: RuntimeError: c"]


def test_k_keyboard_interrupt_in_a_step_is_reraised_after_the_last_step(sim):
    sim.link.close_raises = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        walk.main(["--map", str(FIXTURE), "--supervised", "--out", str(sim.out), "--laps", "1"])
    assert sim.stops[0].close_calls == 1


# (l) the stop control fails to start (PB-amend2c-D2)

def test_l_stop_control_start_failure_exits_9(sim, capsys):
    sim.frame = np.zeros((4, 6, 3), np.uint8)
    sim.stop_start = Failure("hook")
    before = _alive_idents()
    assert sim.main("--laps", "1", "--record", "--minutes", "60") == 9
    assert "stop control failed: hook" in capsys.readouterr().err
    assert sim.link.plays == 0
    assert sim.link.release_calls >= 1 and sim.link.close_calls == 1
    assert sim.stops[0].close_calls == 1
    for name in ("stop.json", "session.json", "frames"):
        assert not (sim.out / name).exists(), name
    assert _alive_idents() <= before
    assert not {"walk-timer", "record"} & _alive_names()


# B19-D1 (ruling, as Hong: A widened): a leg that times out with no move ends the run, exit 10

def _leg(outcome: LegOutcome, moves: int = 0, misses: int = 0, off_graph: int = 0) -> LegRecord:
    entry = MoveEntry(1, (0, 0), "walk", "LEFT", 100.0, 1.0, "ok", (0, 0))
    return LegRecord(1, 0, (0, 0), (1, 1), 0, 1, outcome, (entry,) * moves, misses, off_graph, 0)


@pytest.mark.parametrize(("rec", "expected"), [
    (_leg(LegOutcome.TIMED_OUT, off_graph=3, misses=2), ["off_graph", "stop_now"]),
    (_leg(LegOutcome.TIMED_OUT, misses=5), ["no_position", "stop_now"]),
    (_leg(LegOutcome.TIMED_OUT), ["no_position", "stop_now"]),  # no new frame at all: every read was stale
    (_leg(LegOutcome.TIMED_OUT, moves=2, off_graph=3), []),      # moves were sent: the walker goes on
    (_leg(LegOutcome.REACHED), []),
])
def test_lost_leg_sink_triggers_then_stops_the_walker(rec, expected):
    inner = MemorySink()
    calls: list[str] = []
    sink = walk._LostLegSink(inner)
    sink.arm(calls.append, lambda: calls.append("stop_now"))
    assert sink.write_leg(rec) == Success(None)
    assert inner.legs == [rec]
    assert calls == expected


def test_lost_on_the_approach_leg_exits_10_no_position(sim):
    sim.positions = LostAfter(sim.positions, Failure("miss"), reads=1)  # main's pre-check read succeeds
    assert sim.main("--laps", "1") == 10
    legs = _lines(sim.out / "legs.jsonl")
    assert legs[0]["outcome"] == "timed_out" and legs[0]["moves"] == [] and legs[0]["misses"] > 0
    assert [leg["outcome"] for leg in legs[1:]] == ["stopped"]
    stop = _json(sim.out / "stop.json")
    assert stop["reason"] == "no_position" and stop["trigger_source"] == "no_position"
    assert stop["trigger_ns"] is not None


def test_lost_mid_lap_off_graph_exits_10_and_sends_no_move_after_the_trigger(sim, monkeypatch, capsys):
    positions = LostAfter(sim.positions, OFF_GRAPH)
    sim.positions = positions

    def before(lap: int, leg: int) -> None:
        if (lap, leg) == (1, 2):
            positions.lost = True

    _patch_walk_leg(monkeypatch, before)
    assert sim.main("--laps", "1") == 10  # the fake stop control never calls on_stop: the sink stops the walker
    stop = sim.stops[0]
    assert stop.triggers == ["off_graph"] and sim.link.plays == stop.plays_at_trigger
    legs = _lines(sim.out / "legs.jsonl")
    assert legs[-2]["outcome"] == "timed_out" and legs[-2]["off_graph"] > 0 and legs[-2]["moves"] == []
    assert legs[-1]["outcome"] == "stopped" and legs[-1]["moves"] == []
    assert _json(sim.out / "stop.json")["reason"] == "off_graph"
    assert "stopped: off_graph (off_graph)" in capsys.readouterr().out


def test_lost_on_the_last_leg_of_the_last_lap_exits_10(sim, monkeypatch):
    positions = LostAfter(sim.positions, Failure("miss"))
    sim.positions = positions
    last = len(sim.rotation) - 1

    def before(lap: int, leg: int) -> None:
        if (lap, leg) == (1, last):
            positions.lost = True

    _patch_walk_leg(monkeypatch, before)
    assert sim.main("--laps", "1") == 10
    legs = _lines(sim.out / "legs.jsonl")
    assert len(legs) == 1 + len(sim.rotation) and legs[-1]["outcome"] == "timed_out"
    assert _lines(sim.out / "laps.jsonl")[0]["all_reached"] is False
    assert _json(sim.out / "stop.json")["reason"] == "no_position"


def test_a_leg_with_moves_that_times_out_does_not_end_the_run(sim, monkeypatch):
    def before(lap: int, leg: int) -> None:
        sim.link.reject = (lap, leg) == (1, 0)

    _patch_walk_leg(monkeypatch, before)
    assert sim.main("--laps", "1") == 0
    legs = _lines(sim.out / "legs.jsonl")
    assert legs[1]["outcome"] == "timed_out" and len(legs[1]["moves"]) > 0
    assert len(legs) == 1 + len(sim.rotation)
    assert sim.stops[0].triggers == []
    assert _json(sim.out / "stop.json")["reason"] == "max_laps"


# B19-D2 (ruling, as Hong: A widened): an --out that already holds a run is refused, exit 2

@pytest.mark.parametrize("name", ["session.json", "stop.json", "legs.jsonl", "laps.jsonl", "frames/index.jsonl"])
def test_out_holding_a_walk_file_is_refused(sim, capsys, name):
    path = sim.out / name
    path.parent.mkdir(parents=True)
    path.write_text("old\n", encoding="utf-8")
    listing = sorted(p.relative_to(sim.out).as_posix() for p in sim.out.rglob("*"))
    assert sim.main("--laps", "1", "--record") == 2
    assert f"{path} already exists; choose a new --out" in capsys.readouterr().err
    assert sim.capture_calls == 0
    assert sorted(p.relative_to(sim.out).as_posix() for p in sim.out.rglob("*")) == listing
    assert path.read_text(encoding="utf-8") == "old\n"


def test_out_naming_a_file_is_refused(sim, capsys):
    sim.out.write_text("not a folder\n", encoding="utf-8")
    assert sim.main("--laps", "1") == 2
    assert "is not a folder" in capsys.readouterr().err
    assert sim.capture_calls == 0


def test_lost_leg_with_the_stop_controls_own_stop_also_firing_exits_10(sim, monkeypatch):
    """B19-R M4(b): the stop control's on_stop (walker.stop_now) fires inside trigger, then the sink's own stop_now."""
    sim.stop_calls_on_stop = True
    positions = LostAfter(sim.positions, OFF_GRAPH)
    sim.positions = positions

    def before(lap: int, leg: int) -> None:
        if (lap, leg) == (1, 2):
            positions.lost = True

    _patch_walk_leg(monkeypatch, before)
    assert sim.main("--laps", "1") == 10
    stop = sim.stops[0]
    assert stop.triggers == ["off_graph"] and sim.link.plays == stop.plays_at_trigger
    legs = _lines(sim.out / "legs.jsonl")
    assert legs[-2]["outcome"] == "timed_out" and legs[-2]["moves"] == []
    assert legs[-1]["outcome"] == "stopped" and legs[-1]["moves"] == []
    assert _json(sim.out / "stop.json")["reason"] == "off_graph"
    assert sim.link.release_calls >= 1 and sim.link.close_calls == 1 and stop.close_calls == 1


# B19-R M2: a seed numpy rejects

def test_negative_seed_exits_2_before_capture(sim, capsys):
    assert sim.main("--laps", "1", "--seed", "-1") == 2
    assert "--seed must be a non-negative integer, got -1" in capsys.readouterr().err
    assert sim.capture_calls == 0 and sim.link_calls == 0
    assert not sim.out.exists()


def test_a_seed_above_64_bits_is_accepted(sim):
    assert sim.main("--laps", "1", "--seed", str(2**70)) == 0
    assert _json(sim.out / "session.json")["seed"] == 2**70


# B19-R M4(a): the leg log cannot be opened after connect

def test_leg_log_open_failure_exits_2_with_keys_released_and_link_closed(sim, monkeypatch, capsys):
    monkeypatch.setattr(walk.JsonlLegLog, "open", classmethod(lambda cls, directory: Failure("cannot open")))
    assert sim.main("--laps", "1") == 2
    assert "error: cannot open" in capsys.readouterr().err
    assert sim.link.plays == 0
    assert sim.link.release_calls >= 1 and sim.link.close_calls == 1
    assert sim.stops == []  # the stop control is never built
    assert sim.log[-2:] == ["release_all", "link.close"]
    assert not (sim.out / "stop.json").exists() and not (sim.out / "session.json").exists()
