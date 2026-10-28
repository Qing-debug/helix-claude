"""Plan Task RV-9: `walk --view`, the snapshot, the map copy, the step log, and the viewer start and ready wait
(contract V7). Every walk runs through the B19 rig of `test_walk_cli.py` (fake capture, link, clock and stop control)
and a fake viewer installed as `walk._start_viewer`: no process starts and no window opens."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
import test_walk_cli
from returns.result import Failure, Success
from test_walk_cli import FIXTURE, _alive_names, _json, _lines

from src.route import walk
from src.route.step_log import MAP_COPY, SNAPSHOT, STEPS_FILE, VIEW_CLAIM, VIEW_LOG, VIEW_READY, StepLog, read_run
from src.route.walker import RouteWalker, RunSummary

sim = test_walk_cli.sim  # the B19 rig's fixture, reused as it is (bound by assignment, so no parameter shadows an import)

LATE = "route view did not start within {s:g} s; walking without it (it opens when the walk ends), see view.log"
OLD_NAMES = ("session.json", "stop.json", "legs.jsonl", "laps.jsonl", "frames/index.jsonl")
NEW_NAMES = ("steps.jsonl", "map.csv", "minimap.png", "view.log", "view.claim", "view.ready")


def _frame() -> np.ndarray:
    return np.arange(72, dtype=np.uint8).reshape(4, 6, 3)  # asymmetric: a channel swap shows


class FakeProc:
    """What the walk may use of the viewer's Popen: `poll()` only (it counts its calls)."""

    def __init__(self, code: int | None) -> None:
        self.code = code
        self.polls = 0

    def poll(self) -> int | None:
        self.polls += 1
        return self.code

    def kill(self) -> None:
        raise AssertionError("the walk killed the viewer")

    def terminate(self) -> None:
        raise AssertionError("the walk terminated the viewer")

    def wait(self, timeout: float | None = None) -> int:
        raise AssertionError("the walk waited on the viewer")


class FakeViewer:
    """Stands in for `walk._start_viewer`; `mode` is ready, never, claim_only, exits, ready_then_exits or fails."""

    def __init__(self, env, mode: str) -> None:
        self.env = env
        self.mode = mode
        self.outs: list[Path] = []
        self.proc: FakeProc | None = None

    def __call__(self, out: Path):
        self.outs.append(out)
        self.env.log.append("viewer")
        if self.mode == "fails":
            return Failure("no python")
        proc = self.proc = FakeProc(0 if self.mode == "exits" else None)
        if self.mode == "claim_only":
            (out / VIEW_CLAIM).open("x").write("viewer")
        if self.mode in ("ready", "ready_then_exits"):
            threading.Thread(target=self._ready, args=(out, proc), name="fake-viewer", daemon=True).start()
        return proc

    def _ready(self, out: Path, proc: FakeProc) -> None:
        time.sleep(0.1)
        with open(out / VIEW_CLAIM, "x") as claim:
            claim.write("viewer")
        print("FAKE READY")
        (out / "view.ready.tmp").write_text(str(os.getpid()), encoding="utf-8")
        os.replace(out / "view.ready.tmp", out / VIEW_READY)
        if self.mode == "ready_then_exits":
            proc.code = 0


def _viewer(monkeypatch, env, mode: str) -> FakeViewer:
    fake = FakeViewer(env, mode)
    monkeypatch.setattr(walk, "_start_viewer", fake)
    return fake


def _spy_start(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    real = StepLog.start

    def start(self, folder, header):
        calls.append((folder, header))
        return real(self, folder, header)

    monkeypatch.setattr(StepLog, "start", start)
    return calls


def _fail_start(monkeypatch) -> None:
    monkeypatch.setattr(StepLog, "start", lambda self, folder, header: Failure("disk full"))


def _unavailable(err: str) -> list[str]:
    return [line for line in err.splitlines() if line.startswith("step log unavailable:")]


def _timed_wait(monkeypatch) -> list[tuple[float, int]]:
    """Wrap the real `_wait_for_viewer`: each call's (seconds, the proc's poll count when it returned)."""
    spans: list[tuple[float, int]] = []
    real = walk._wait_for_viewer

    def wait(out, proc):
        t0 = time.monotonic()
        ready = real(out, proc)
        spans.append((time.monotonic() - t0, proc.polls))
        return ready

    monkeypatch.setattr(walk, "_wait_for_viewer", wait)
    return spans


def _all_reached(out: Path) -> bool:
    legs = _lines(out / "legs.jsonl")
    return len(legs) == 8 and all(leg["outcome"] == "reached" for leg in legs)


def _raise_runtime(self):
    raise RuntimeError("kaboom")


# 1 ----------------------------------------------------------------------------------------------------------------

def test_1_view_turns_record_on(sim, monkeypatch):
    sim.frame = _frame()
    fake = _viewer(monkeypatch, sim, "ready")
    assert sim.main("--laps", "1", "--view") == 0
    assert _json(sim.out / "session.json")["record"] is True
    assert len(_lines(sim.out / "frames" / "index.jsonl")) >= 1
    assert fake.outs == [sim.out]


def test_1_without_view_or_record_nothing_is_recorded(sim):
    sim.frame = _frame()
    assert sim.main("--laps", "1") == 0
    assert _json(sim.out / "session.json")["record"] is False
    assert not (sim.out / "frames").exists()


# 2 ----------------------------------------------------------------------------------------------------------------

def test_2_every_walk_writes_the_three_files_and_the_walker_gets_the_observer(sim, monkeypatch):
    sim.frame = _frame()
    fake = _viewer(monkeypatch, sim, "ready")
    wall_before = time.time_ns()
    assert sim.main("--laps", "1") == 0
    wall_after = time.time_ns()
    out = sim.out
    run = read_run(out).unwrap()
    assert (run.origin, run.ended, run.bad_lines) == ("steps", True, 0)
    session = _json(out / "session.json")
    head = run.header
    assert (head.source, head.clock, head.map, head.character) == ("walk", "perf_counter_ns", "map.csv", "kanna")
    assert head.map_sha256 == session["map_sha256"] and dict(head.params) == session["params"]
    assert head.rotation == tuple(n.minimap_position for n in sim.rotation)
    assert head.git_head == session["git_head"]
    assert isinstance(head.wall_ns, int) and wall_before <= head.wall_ns <= wall_after
    events = run.events
    first_t = events[0].t_ns
    assert isinstance(head.perf_ns, int) and first_t is not None and head.perf_ns <= first_t
    assert events[0].ev == "leg" and events[-1].ev == "end"
    assert dict(events[-1].data) == {"reason": "max_laps", "code": 0}
    legs = _lines(out / "legs.jsonl")
    assert sum(e.ev == "leg" for e in events) == len(legs) == 8
    moves = [move for leg in legs for move in leg["moves"]]
    planned = [e for e in events if e.ev == "planned"]
    assert len(planned) == len(moves) > 0
    assert [(e.t_ns, e.data["pos"], e.data["kind"], e.data["dir"], e.data["walk_ms"], e.data["cost_to_go_s"])
            for e in planned] == [(m["t_ns"], tuple(m["pos"]), m["kind"], m["dir"], m["walk_ms"], m["cost_to_go_s"])
                                  for m in moves]
    assert (out / MAP_COPY).read_bytes() == FIXTURE.read_bytes()
    snapshot = cv2.imread(str(out / SNAPSHOT))
    assert snapshot is not None and np.array_equal(cv2.cvtColor(snapshot, cv2.COLOR_BGR2RGB), sim.frame)
    assert not (out / "minimap.png.tmp").exists()
    assert fake.outs == [] and "viewer" not in sim.log
    assert not any((out / name).exists() for name in (VIEW_CLAIM, VIEW_READY, VIEW_LOG))
    assert "step-log" not in _alive_names()


# 3 ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("run", "code", "reason"), [
    (lambda self: Failure("boom"), 6, "error"),
    (_raise_runtime, 7, "error"),
    (lambda self: Success(RunSummary(0, 0, "link_down")), 1, "link_down"),
], ids=["failure", "raises", "link_down"])
def test_3_end_code_and_reason_per_way_out(sim, monkeypatch, run, code, reason):
    sim.frame = _frame()
    monkeypatch.setattr(RouteWalker, "run", run)
    assert sim.main("--laps", "1") == code
    lines = _lines(sim.out / STEPS_FILE)
    assert [line["ev"] for line in lines] == ["run", "end"]
    assert (lines[-1]["reason"], lines[-1]["code"]) == (reason, code)


# 4 ----------------------------------------------------------------------------------------------------------------

def test_4_an_exception_leaving_main_ends_the_step_log_with_null_reason_and_code(sim, monkeypatch):
    sim.frame = _frame()

    def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(RouteWalker, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        walk.main(["--map", str(FIXTURE), "--supervised", "--out", str(sim.out), "--laps", "1"])
    last = _lines(sim.out / STEPS_FILE)[-1]
    assert (last["ev"], last["reason"], last["code"]) == ("end", None, None)
    assert {"release_all", "stop.close", "link.close"} <= set(sim.log)


# 5 ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("failing", "code"), [("connect", 4), ("stop control", 9)])
def test_5_no_step_log_before_the_stop_control_starts(sim, monkeypatch, failing, code):
    sim.frame = _frame()
    starts = _spy_start(monkeypatch)
    if failing == "connect":
        sim.link.connect_result = Failure("x")
    else:
        sim.stop_start = Failure("x")
    assert sim.main("--laps", "1") == code
    assert not any((sim.out / name).exists() for name in (STEPS_FILE, MAP_COPY, SNAPSHOT))
    assert starts == []


# 6 ----------------------------------------------------------------------------------------------------------------

def test_6_out_files_are_the_five_old_names_then_the_six_new_ones():
    assert walk._OUT_FILES == OLD_NAMES + NEW_NAMES


@pytest.mark.parametrize("name", NEW_NAMES)
def test_6_out_holding_a_new_file_is_refused(sim, capsys, name):
    path = sim.out / name
    path.parent.mkdir(parents=True)
    path.write_text("old\n", encoding="utf-8")
    listing = sorted(p.relative_to(sim.out).as_posix() for p in sim.out.rglob("*"))
    assert sim.main("--laps", "1", "--view") == 2
    assert f"{path} already exists; choose a new --out" in capsys.readouterr().err
    assert sim.capture_calls == 0
    assert sorted(p.relative_to(sim.out).as_posix() for p in sim.out.rglob("*")) == listing
    assert path.read_text(encoding="utf-8") == "old\n"


# 7 ----------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NO_WINDOW and CREATE_NEW_PROCESS_GROUP are Windows only")
def test_7_the_spawn_arguments(tmp_path, monkeypatch):
    calls: list[tuple[list[str], dict]] = []

    class Recorder:
        def __init__(self, args, **kwargs) -> None:
            calls.append((args, kwargs))

    monkeypatch.setattr(walk.subprocess, "Popen", Recorder)
    proc = walk._start_viewer(tmp_path)
    assert isinstance(proc, Recorder) and len(calls) == 1
    args, kwargs = calls[0]
    assert args == [sys.executable, "-u", "-m", "src.route_view", "--follow", str(tmp_path), "--ready",
                    str(tmp_path / "view.ready")]
    # no shell=True and no startupinfo: the child then gets no STARTF_USESHOWWINDOW (RV-8 review)
    assert set(kwargs) == {"cwd", "creationflags", "stdin", "stdout", "stderr"}
    assert kwargs["cwd"] == walk._REPO
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    assert kwargs["creationflags"] == flags == 134217728 | 512
    assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["stderr"] is subprocess.STDOUT
    log = kwargs["stdout"]
    assert os.fspath(log.name) == str(tmp_path / "view.log") and log.mode == "ab" and log.closed


def test_7_a_popen_oserror_is_a_failure_and_the_log_handle_is_closed(tmp_path, monkeypatch):
    handles = []

    class Raiser:
        def __init__(self, args, **kwargs) -> None:
            handles.append(kwargs["stdout"])
            raise OSError("x")

    monkeypatch.setattr(walk.subprocess, "Popen", Raiser)
    failed = walk._start_viewer(tmp_path)
    assert isinstance(failed, Failure) and "x" in failed.failure()
    if sys.platform == "win32":
        assert len(handles) == 1 and handles[0].closed


# 8 ----------------------------------------------------------------------------------------------------------------

def test_8_the_ready_wait_with_the_viewer_on_time(sim, monkeypatch, capsys):
    sim.frame = _frame()
    _viewer(monkeypatch, sim, "ready")
    assert sim.main("--laps", "1", "--view") == 0
    out, err = capsys.readouterr()
    assert out.index("FAKE READY") < out.index("Focus the game window")
    assert "route view did not start" not in err
    assert (sim.out / VIEW_CLAIM).read_text(encoding="utf-8") == "viewer"  # the viewer's claim, not the walk's


# 9 ----------------------------------------------------------------------------------------------------------------

def test_9a_a_step_log_that_cannot_start_walks_on_without_the_view(sim, monkeypatch, capsys):
    sim.frame = _frame()
    fake = _viewer(monkeypatch, sim, "ready")
    _fail_start(monkeypatch)
    closes = []
    real_close = StepLog.close

    def close(self, *args, **kwargs):
        closes.append(self)
        print("CLOSED")
        return real_close(self, *args, **kwargs)

    monkeypatch.setattr(StepLog, "close", close)
    assert sim.main("--laps", "1", "--view") == 0
    out, err = capsys.readouterr()
    lines = _unavailable(err)
    assert len(lines) == 1 and "disk full" in lines[0] and "walking without the route view" in lines[0]
    assert fake.outs == []
    assert not any((sim.out / name).exists() for name in (VIEW_CLAIM, VIEW_READY, VIEW_LOG, STEPS_FILE))
    assert (sim.out / MAP_COPY).exists() and (sim.out / SNAPSHOT).exists()
    assert len(_lines(sim.out / "legs.jsonl")) == 8
    assert len(closes) == 1 and out.index("CLOSED") < out.index("Focus the game window")


def test_9b_no_first_frame_to_save(sim, monkeypatch, capsys):
    sim.frame = None
    fake = _viewer(monkeypatch, sim, "ready")
    starts = _spy_start(monkeypatch)
    assert sim.main("--laps", "1", "--view") == 0
    lines = _unavailable(capsys.readouterr().err)
    assert len(lines) == 1 and "no first frame to save" in lines[0]
    assert starts == [] and fake.outs == []
    assert (sim.out / MAP_COPY).exists()
    assert not (sim.out / SNAPSHOT).exists() and not (sim.out / "minimap.png.tmp").exists()


def test_9c_a_map_copy_that_cannot_be_written(sim, monkeypatch, capsys):
    sim.frame = _frame()
    real = Path.write_bytes

    def write_bytes(self, data):
        if self.name == MAP_COPY:
            raise OSError("locked")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    assert sim.main("--laps", "1") == 0
    lines = _unavailable(capsys.readouterr().err)
    assert len(lines) == 1 and "locked" in lines[0]
    assert (sim.out / SNAPSHOT).exists() and not (sim.out / STEPS_FILE).exists()


def test_9d_observer_calls_after_a_failed_start_are_dropped(sim, monkeypatch):
    sim.frame = _frame()
    _fail_start(monkeypatch)
    logs: list[StepLog] = []
    real = StepLog.planned

    def planned(self, *args, **kwargs):
        logs.append(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(StepLog, "planned", planned)
    assert sim.main("--laps", "1") == 0
    assert logs  # the walker kept calling its observer
    assert logs[0]._queue.qsize() == 0


# 10-13 --------------------------------------------------------------------------------------------------------------

@pytest.mark.slow
def test_10_a_viewer_never_ready(sim, monkeypatch, capsys):
    sim.frame = _frame()
    _viewer(monkeypatch, sim, "never")
    monkeypatch.setattr(walk, "_VIEW_READY_S", 0.3)
    monkeypatch.setattr(walk, "_VIEW_POLL_S", 0.01)
    spans = _timed_wait(monkeypatch)
    assert sim.main("--laps", "1", "--view") == 0
    assert len(spans) == 1 and 0.3 <= spans[0][0] < 1.5
    assert (sim.out / VIEW_CLAIM).read_bytes() == b""  # the walk's claim
    assert capsys.readouterr().err.count(LATE.format(s=0.3)) == 1


@pytest.mark.slow
def test_11_claim_taken_and_no_view_ready(sim, monkeypatch, capsys):
    sim.frame = _frame()
    _viewer(monkeypatch, sim, "claim_only")
    monkeypatch.setattr(walk, "_VIEW_READY_S", 0.2)
    monkeypatch.setattr(walk, "_VIEW_CLAIM_EXTRA_S", 0.3)
    monkeypatch.setattr(walk, "_VIEW_POLL_S", 0.01)
    spans = _timed_wait(monkeypatch)
    assert sim.main("--laps", "1", "--view") == 0
    assert len(spans) == 1 and 0.5 <= spans[0][0] < 2.0
    assert (sim.out / VIEW_CLAIM).read_text(encoding="utf-8") == "viewer"
    assert capsys.readouterr().err.count(LATE.format(s=0.2)) == 1


def test_12a_a_viewer_that_exits_at_once_costs_one_line_not_the_walk(sim, monkeypatch, capsys):
    sim.frame = _frame()
    _viewer(monkeypatch, sim, "exits")
    monkeypatch.setattr(walk, "_VIEW_READY_S", 5.0)
    t0 = time.monotonic()
    code = sim.main("--laps", "1", "--view")
    assert code == 0 and time.monotonic() - t0 < 2.0
    assert (sim.out / VIEW_CLAIM).read_bytes() == b""
    assert capsys.readouterr().err.count(LATE.format(s=5.0)) == 1
    assert _all_reached(sim.out)


def test_12b_after_the_wait_the_walk_never_looks_at_the_viewer_again(sim, monkeypatch):
    sim.frame = _frame()
    fake = _viewer(monkeypatch, sim, "ready_then_exits")
    spans = _timed_wait(monkeypatch)
    assert sim.main("--laps", "1", "--view") == 0
    assert fake.proc is not None and len(spans) == 1 and spans[0][1] == fake.proc.polls
    assert _all_reached(sim.out)


def test_13_a_viewer_that_cannot_start(sim, monkeypatch, capsys):
    sim.frame = _frame()
    _viewer(monkeypatch, sim, "fails")
    waits = []
    monkeypatch.setattr(walk, "_wait_for_viewer", lambda out, proc: waits.append(proc))
    assert sim.main("--laps", "1", "--view") == 0
    assert "route view could not start: no python; walking without it" in capsys.readouterr().err
    assert not (sim.out / VIEW_CLAIM).exists() and waits == []


# 14 ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("crash", [False, True], ids=["done", "run_raises"])
def test_14_cleanup_order(sim, monkeypatch, crash):
    sim.frame = _frame()
    _viewer(monkeypatch, sim, "ready")
    real = StepLog.close

    def close(self, *args, **kwargs):
        sim.log.append(("step_log", (sim.out / "stop.json").exists()))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(StepLog, "close", close)
    if crash:
        monkeypatch.setattr(RouteWalker, "run", _raise_runtime)
    assert sim.main("--laps", "1", "--view") == (7 if crash else 0)
    assert [entry for entry in sim.log if isinstance(entry, tuple)] == [("step_log", True)]
    at = sim.log.index(("step_log", True))
    assert "release_all" in sim.log[:at] and "link.close" in sim.log[at + 1:]
