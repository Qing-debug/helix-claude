"""Plan Task RV-9 as RT-2a left it (route-tab spec 5.13: the walk no longer starts a viewer): the snapshot, the map copy
and the step log of a walk (contract V7). Every walk runs through the B19 rig of `test_walk_cli.py` (fake capture, link,
clock and stop control)."""

import time
from pathlib import Path

import cv2
import numpy as np
import pytest
import test_walk_cli
from returns.result import Failure, Success
from test_walk_cli import FIXTURE, _alive_names, _json, _lines

from src.route import session, walk
from src.route.harness.record_minimap import INDEX_NAME
from src.route.step_log import MAP_COPY, SNAPSHOT, STEPS_FILE, StepLog, read_run
from src.route.walker import RouteWalker, RunSummary
from src.route_view import model

sim = test_walk_cli.sim  # the B19 rig's fixture, reused as it is (bound by assignment, so no parameter shadows an import)

OLD_NAMES = ("session.json", "stop.json", "legs.jsonl", "laps.jsonl", "frames/index.jsonl")
NEW_NAMES = ("steps.jsonl", "map.csv", "minimap.png")


def _frame() -> np.ndarray:
    return np.arange(72, dtype=np.uint8).reshape(4, 6, 3)  # asymmetric: a channel swap shows


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


def _raise_runtime(self):
    raise RuntimeError("kaboom")


# 2 ----------------------------------------------------------------------------------------------------------------

def test_2_every_walk_writes_the_three_files_and_the_walker_gets_the_observer(sim):
    sim.frame = _frame()
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

def test_6_out_files_are_the_five_old_names_then_the_three_new_ones():
    assert session.OUT_FILES == OLD_NAMES + NEW_NAMES


def test_6_the_viewer_reads_the_frames_folder_and_index_the_walk_writes():
    """`route_view` may not import the harness, so `model.py` repeats the names `session` (`_FRAMES`) and the recorder
    (`INDEX_NAME`) use. A rename on the writer's side would leave the live view with no frames and nothing else red."""
    assert (model.FRAMES_DIR, model.FRAME_INDEX) == (session._FRAMES, INDEX_NAME) == ("frames", "index.jsonl")


@pytest.mark.parametrize("name", NEW_NAMES)
def test_6_out_holding_a_new_file_is_refused(sim, capsys, name):
    path = sim.out / name
    path.parent.mkdir(parents=True)
    path.write_text("old\n", encoding="utf-8")
    listing = sorted(p.relative_to(sim.out).as_posix() for p in sim.out.rglob("*"))
    assert sim.main("--laps", "1") == 2
    assert f"{path} already exists; choose a new --out" in capsys.readouterr().err
    assert sim.capture_calls == 0
    assert sorted(p.relative_to(sim.out).as_posix() for p in sim.out.rglob("*")) == listing
    assert path.read_text(encoding="utf-8") == "old\n"


# 9 ----------------------------------------------------------------------------------------------------------------

def test_9a_a_step_log_that_cannot_start_walks_on_without_the_view(sim, monkeypatch, capsys):
    sim.frame = _frame()
    _fail_start(monkeypatch)
    closes = []
    real_close = StepLog.close

    def close(self, *args, **kwargs):
        closes.append(self)
        print("CLOSED")
        return real_close(self, *args, **kwargs)

    monkeypatch.setattr(StepLog, "close", close)
    assert sim.main("--laps", "1") == 0
    out, err = capsys.readouterr()
    lines = _unavailable(err)
    assert len(lines) == 1 and "disk full" in lines[0] and "walking without the route view" in lines[0]
    assert not (sim.out / STEPS_FILE).exists()
    assert (sim.out / MAP_COPY).exists() and (sim.out / SNAPSHOT).exists()
    assert len(_lines(sim.out / "legs.jsonl")) == 8
    assert len(closes) == 1 and out.index("CLOSED") < out.index("Focus the game window")


def test_9b_no_first_frame_to_save(sim, monkeypatch, capsys):
    sim.frame = None
    starts = _spy_start(monkeypatch)
    assert sim.main("--laps", "1") == 0
    lines = _unavailable(capsys.readouterr().err)
    assert len(lines) == 1 and "no first frame to save" in lines[0]
    assert starts == []
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


# 14 ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("crash", [False, True], ids=["done", "run_raises"])
def test_14_cleanup_order(sim, monkeypatch, crash):
    sim.frame = _frame()
    real = StepLog.close

    def close(self, *args, **kwargs):
        sim.log.append(("step_log", (sim.out / "stop.json").exists()))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(StepLog, "close", close)
    if crash:
        monkeypatch.setattr(RouteWalker, "run", _raise_runtime)
    assert sim.main("--laps", "1") == (7 if crash else 0)
    assert [entry for entry in sim.log if isinstance(entry, tuple)] == [("step_log", True)]
    at = sim.log.index(("step_log", True))
    assert "release_all" in sim.log[:at] and "link.close" in sim.log[at + 1:]
