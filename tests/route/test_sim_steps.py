"""Tests for the simulator's step log (route view plan task RV-3; spec 7 checks S1, S2, S4 and S8).

Tests (1)-(10) are the plan's, numbered as in the plan: the report is unchanged without a folder or an observer, a
recording observer changes nothing, `--out` writes a `steps.jsonl` that matches `legs.jsonl` and a `map.csv` the route
graph can be rebuilt from, a link-down run ends cleanly, `observer` and `out_dir` together are refused, a folder that
already has `steps.jsonl` is refused untouched, and the observer costs the walker thread under 20 us per event. The last
five tests pin what the plan leaves open: an unregistered character class, a step log that cannot start, a walker that
raises, an `end` that raises, a failing close.
"""

import ast
import contextlib
import csv
import hashlib
import io
import json
import shutil
import statistics
import threading
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from returns.pipeline import is_successful
from returns.result import Failure

import src.route.sim.run as run_module
from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.input.link import InputError
from src.route.params import MovementParams
from src.route.route_graph import RouteGraph
from src.route.sim.link import SimLink
from src.route.sim.run import main, run_sim
from src.route.sim.world import NoiseConfig
from src.route.step_log import MAP_COPY, STEPS_FILE, StepLog, read_run
from src.route.walker import RouteWalker

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
TERRAIN = FIXTURES / "ff2_terrain.json"

V2_METHODS = ("leg", "read_failed", "no_plan", "reached", "stall", "planned", "sent", "settled", "leg_end")
OFFSETS = NoiseConfig(node_offset_px=2)
BOTH_NOISES = pytest.mark.parametrize("noise", [NoiseConfig(), OFFSETS], ids=["plain", "node_offsets"])
# The report of the 20-lap seed-1 run before this task. laps_completed 20, legs_reached 141 and moves 379 are the
# plan's (spec 7); the rest were measured on the base commit 7f2735d. The two floats are ns counts over 1e9.
PINNED_REPORT = dict(
    laps_requested=20, laps_completed=20, legs=141, legs_reached=141, stalls=0, timeouts=0, link_down=0,
    known_cases={}, single_error_legs=43, single_error_within_3=43, multi_error_legs=9, multi_error_within_bound=9,
    moves=379, no_ops=22, drops=25, teleport_downs=16,
    sim_seconds=pytest.approx(298.5323, rel=1e-9), mean_lap_s=pytest.approx(14.926615, rel=1e-9),
)
# The same for NoiseConfig(node_offset_px=2): the shifted copy's draws must not move (moves 356 is the count RV-5 uses).
PINNED_OFFSET_REPORT = dict(
    PINNED_REPORT, single_error_legs=42, single_error_within_3=42, multi_error_legs=4, multi_error_within_bound=4,
    moves=356, no_ops=19, drops=23, teleport_downs=19,
    sim_seconds=pytest.approx(314.927, rel=1e-9), mean_lap_s=pytest.approx(15.74635, rel=1e-9),
)


def _run(laps=20, seed=1, **kwargs):
    result = run_sim(CSV, TERRAIN, laps, seed, **kwargs)
    assert is_successful(result), result
    return result.unwrap()


def _pos(value):
    return None if value is None else tuple(value)


def _blue_nodes(csv_path):
    """The blue-node positions of a map CSV in file order: the rotation."""
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    rows = csv.DictReader(line for line in lines if not line.startswith("#"))
    return tuple(ast.literal_eval(row["pos_in_minimap"]) for row in rows if row["nodetype"] == "blue_node")


def _load(csv_path):
    agent = MapleAgent(characterClass=Kanna())
    with contextlib.redirect_stdout(io.StringIO()):  # the CSV parser prints its progress
        loaded = agent.loadMap(csv_path)
    assert is_successful(loaded), loaded
    return agent


def _rotation(csv_path):
    return tuple(node.minimap_position for node in _load(csv_path).map_rotation_order)


def _edges(agent, params):
    built = RouteGraph.build(agent.pathGraph, params)
    assert is_successful(built), built
    graph = built.unwrap().graph
    return {(u.minimap_position, v.minimap_position, data["kind"], data["cost_s"])
            for u, v, data in graph.edges(data=True)}


def _files(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir()}


class RecordingObserver:
    """A `StepObserver` that records every call as (method name, arguments); any public name is a recording method."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args):
            self.calls.append((name, args))

        return record


def _assert_group(rec, group):
    """One `legs.jsonl` leg record against its `leg` ... `leg_end` group of events (spec S4)."""
    first, *middle, last = group
    assert first.ev == "leg" and last.ev == "leg_end"
    assert all(e.ev != "leg" for e in middle)
    assert first.t_ns == rec["start_ns"]
    assert first.data == {"lap": rec["lap"], "leg": rec["leg"], "from": _pos(rec["from"]), "to": _pos(rec["to"])}

    moves = rec["moves"]
    expected = []
    for i in range(len(moves)):
        expected += ["planned", "sent"]
        if not (rec["outcome"] == "link_down" and i == len(moves) - 1):  # that move never settled
            expected.append("settled")
    steps = [e for e in middle if e.ev in ("planned", "sent", "settled")]
    assert [e.ev for e in steps] == expected, (rec["lap"], rec["leg"])
    it = iter(steps)
    for i, move in enumerate(moves):
        planned, sent = next(it), next(it)
        assert planned.t_ns == move["t_ns"]
        assert planned.data["pos"] == _pos(move["pos"])
        for key in ("kind", "dir", "walk_ms", "cost_to_go_s"):
            assert planned.data[key] == move[key], key
        assert sent.data["input"] == move["input"]
        if rec["outcome"] == "link_down" and i == len(moves) - 1:
            assert move["settled"] is None
        else:
            assert next(it).data["pos"] == _pos(move["settled"])

    # the counts and the reached event: the leg record's numbers are the events' numbers
    assert sum(e.ev == "read_failed" and e.data["why"] != "stale" for e in middle) == rec["misses"]
    assert sum(e.ev == "no_plan" for e in middle) == rec["off_graph"]
    assert sum(e.ev == "stall" for e in middle) == rec["stalls"]
    assert sum(e.ev == "reached" for e in middle) == (rec["outcome"] == "reached")
    assert last.t_ns == rec["end_ns"]
    assert last.data == {k: rec[k] for k in ("outcome", "misses", "off_graph", "stalls")}


def _assert_matches_leg_log(out, reason="max_laps"):
    """The folder's `steps.jsonl` against its `legs.jsonl`, leg by leg; returns (RunSteps, leg records)."""
    read = read_run(out)
    assert is_successful(read), read
    run = read.unwrap()
    legs = [json.loads(line) for line in (out / "legs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert run.origin == "steps" and run.ended and run.bad_lines == 0
    *body, end = run.events
    assert end.ev == "end" and end.data == {"reason": reason, "code": None}
    at = 0
    for rec in legs:
        stop = next((i for i in range(at, len(body)) if body[i].ev == "leg_end"), None)
        assert stop is not None, f"lap {rec['lap']} leg {rec['leg']} has no leg_end"
        _assert_group(rec, body[at:stop + 1])
        at = stop + 1
    assert at == len(body), "events outside the leg groups"
    return run, legs


def _assert_header(header, rotation):
    assert header.source == "sim" and header.clock == "sim_ns" and header.map == MAP_COPY
    assert header.wall_ns is None and header.perf_ns is None and header.git_head is None
    assert header.character == "kanna"
    assert header.rotation == rotation


# (1)
def test_1_without_a_folder_or_an_observer_the_report_is_the_one_from_before_the_change():
    assert asdict(_run(laps=20, seed=1, out_dir=None, observer=None)) == PINNED_REPORT


# (2)
@BOTH_NOISES
def test_2_a_recording_observer_changes_nothing(noise):
    observer = RecordingObserver()
    watched = _run(noise=noise, observer=observer)
    assert watched == _run(noise=noise)
    names = [name for name, _ in observer.calls]
    assert set(names) <= set(V2_METHODS)  # `end` and `close` are for the StepLog that `out_dir` creates, not for this
    assert names[0] == "leg" and names[-1] == "leg_end"
    assert names.count("leg") == names.count("leg_end") == watched.legs  # the recovery replays got no observer
    assert names.count("planned") == names.count("sent") == watched.moves


# (3)
def test_3_a_plain_run_writes_a_steps_log_that_matches_the_leg_log(tmp_path):
    out = tmp_path / "run"
    report = _run(out_dir=out)
    assert asdict(report) == PINNED_REPORT  # the folder changes nothing in the run
    run, legs = _assert_matches_leg_log(out)
    _assert_header(run.header, _blue_nodes(CSV))
    assert len(legs) == report.legs
    assert sum(len(rec["moves"]) for rec in legs) == report.moves


# (4)
def test_4_node_offsets_write_the_shifted_copy_and_draw_nothing_for_it(tmp_path):
    out = tmp_path / "run"
    report = _run(noise=OFFSETS, out_dir=out)
    assert report == _run(noise=OFFSETS)
    assert asdict(report) == PINNED_OFFSET_REPORT
    copy = out / MAP_COPY
    shifted = _rotation(copy)
    assert shifted != _blue_nodes(CSV)  # the nodes are moved: this is not the --map file
    run, legs = _assert_matches_leg_log(out)
    _assert_header(run.header, shifted)
    assert {tuple(rec["to"]) for rec in legs} == set(shifted)
    assert run.header.map_sha256 == hashlib.sha256(copy.read_bytes()).hexdigest()


# (5)
def test_5_the_map_copy_of_a_plain_run_is_the_map_file(tmp_path):
    out = tmp_path / "run"
    _run(laps=1, out_dir=out)
    copy = (out / MAP_COPY).read_bytes()
    assert copy == CSV.read_bytes()
    assert read_run(out).unwrap().header.map_sha256 == hashlib.sha256(copy).hexdigest()


# (6)
def test_6_the_route_graph_can_be_rebuilt_from_the_folder(tmp_path):
    out = tmp_path / "run"
    _run(laps=1, out_dir=out)
    header = read_run(out).unwrap().header
    rebuilt = _edges(_load(out / MAP_COPY), MovementParams(**header.params))
    original = _edges(_load(CSV), MovementParams())
    assert rebuilt == original and rebuilt


# (7)
def test_7_a_link_that_dies_ends_the_run_with_link_down_and_no_settled(tmp_path, monkeypatch):
    made = []

    class DyingLink(SimLink):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.dies = not made  # the first instance is the run's link; the recovery replays build more
            made.append(self)

        def play(self, steps):
            if self.dies and self.plays == 29:  # the call about to be made is the 30th
                self.fail_next.append(InputError.LINK_LOST)
            return super().play(steps)

    monkeypatch.setattr(run_module, "SimLink", DyingLink)
    out = tmp_path / "run"
    report = _run(out_dir=out)
    assert report.link_down == 1 and report.laps_completed < report.laps_requested
    run, legs = _assert_matches_leg_log(out, reason="link_down")
    assert legs[-1]["outcome"] == "link_down" and legs[-1]["moves"][-1]["input"] == "LINK_LOST"
    assert [e.ev for e in run.events[-4:]] == ["planned", "sent", "leg_end", "end"]


# (8)
def test_8_observer_and_out_dir_together_are_refused_and_create_nothing(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    observer = RecordingObserver()
    result = run_sim(CSV, TERRAIN, 1, 1, out_dir=out, observer=observer)
    assert not is_successful(result)
    assert result.failure() == "give observer or out_dir, not both"
    assert list(out.iterdir()) == [] and observer.calls == []


# (9)
def test_9_a_folder_that_has_steps_jsonl_is_refused_and_left_untouched(tmp_path, monkeypatch, capsys):
    out = tmp_path / "run"
    _run(laps=1, out_dir=out)
    before = _files(out)
    assert set(before) == {STEPS_FILE, MAP_COPY, "legs.jsonl", "laps.jsonl"}
    capsys.readouterr()

    def no_leg_log(*args, **kwargs):
        raise AssertionError("the leg log was opened before the folder was refused")

    monkeypatch.setattr(run_module.JsonlLegLog, "open", no_leg_log)

    code = main(["--laps", "1", "--seed", "1", "--out", str(out)])
    captured = capsys.readouterr()
    assert code == 2 and captured.out == ""
    assert captured.err.startswith("error: ") and f"{STEPS_FILE} already exists" in captured.err
    assert _files(out) == before

    result = run_sim(CSV, TERRAIN, 1, 1, out_dir=out)
    assert not is_successful(result) and f"{STEPS_FILE} already exists" in result.failure()
    assert _files(out) == before

    shutil.rmtree(out)  # raises if a handle is still open (Windows): the refused run opened no leg log
    assert not out.exists()


# (10)
@pytest.mark.slow
def test_10_the_observer_costs_the_walker_thread_under_20_us_an_event(tmp_path, monkeypatch):
    """Spec S8. This times the observer's own work (the `StepLog` method that queues the tuple), not the argument
    building in `walker.py` (a tuple of at most 16 pairs, V2)."""
    made = []

    class TimingStepLog(StepLog):
        def __init__(self):
            super().__init__()
            self.event_ns = []
            self.close_ns = None
            self.writers = []
            made.append(self)

        def start(self, folder, header):
            before = set(threading.enumerate())
            result = super().start(folder, header)
            self.writers = [t for t in threading.enumerate() if t not in before]
            return result

        def close(self, join_s=2.0):
            began = time.perf_counter_ns()
            result = super().close(join_s)
            self.close_ns = time.perf_counter_ns() - began
            return result

    def timed(name):
        def method(self, *args):
            began = time.perf_counter_ns()
            getattr(super(TimingStepLog, self), name)(*args)
            self.event_ns.append(time.perf_counter_ns() - began)

        return method

    for name in (*V2_METHODS, "end"):
        setattr(TimingStepLog, name, timed(name))
    monkeypatch.setattr(run_module, "StepLog", TimingStepLog)

    out = tmp_path / "run"
    _run(laps=20, out_dir=out)
    (log,) = made
    median_us = statistics.median(log.event_ns) / 1000
    print(f"S8: median {median_us:.2f} us per event over {len(log.event_ns)} events")
    assert median_us < 20
    assert log.close_ns < 1e9  # close returned within 1 s of the run's end
    assert len(log.writers) == 1 and not log.writers[0].is_alive()
    run = read_run(out).unwrap()
    assert len(run.events) == len(log.event_ns) and run.bad_lines == 0  # every queued event is in the file


# The choices the plan leaves open ------------------------------------------------------------------------------------

def test_a_character_class_without_a_registry_key_is_refused_before_a_folder_is_made(tmp_path):
    class Other(Kanna):
        pass

    out = tmp_path / "run"
    result = run_sim(CSV, TERRAIN, 1, 1, out_dir=out, character=Other())
    assert not is_successful(result) and "Other" in result.failure()
    assert not out.exists()


def test_a_step_log_that_cannot_start_closes_the_leg_log_and_fails_the_run(tmp_path, monkeypatch):
    class CannotStart(StepLog):
        def start(self, folder, header):
            return Failure("no steps.jsonl for you")

    closed = []
    leg_log_close = run_module.JsonlLegLog.close
    monkeypatch.setattr(run_module, "StepLog", CannotStart)
    monkeypatch.setattr(run_module.JsonlLegLog, "close", lambda self: (closed.append(self), leg_log_close(self)))
    out = tmp_path / "run"
    result = run_sim(CSV, TERRAIN, 1, 1, out_dir=out)
    assert not is_successful(result) and result.failure() == "no steps.jsonl for you"
    assert len(closed) == 1
    shutil.rmtree(out)


def test_a_walker_that_raises_still_ends_and_closes_the_step_log_and_not_a_passed_observer(tmp_path, monkeypatch):
    class Raising(RouteWalker):
        def run(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(run_module, "RouteWalker", Raising)
    out = tmp_path / "run"
    with pytest.raises(RuntimeError, match="boom"):
        run_sim(CSV, TERRAIN, 1, 1, out_dir=out)
    run = read_run(out).unwrap()
    assert [(e.ev, e.data) for e in run.events] == [("end", {"reason": None, "code": None})]
    shutil.rmtree(out)  # the leg log and the step log are both closed

    observer = RecordingObserver()
    with pytest.raises(RuntimeError, match="boom"):
        run_sim(CSV, TERRAIN, 1, 1, observer=observer)
    assert observer.calls == []


def test_an_end_step_log_that_raises_still_closes_the_leg_log(tmp_path, monkeypatch):
    closed = []
    leg_log_close = run_module.JsonlLegLog.close
    end_step_log = run_module._end_step_log

    def raising_end(step_log, t_ns, ran):
        end_step_log(step_log, t_ns, ran)  # the step log is really ended and closed; then stderr "breaks"
        raise OSError("broken pipe")

    monkeypatch.setattr(run_module, "_end_step_log", raising_end)
    monkeypatch.setattr(run_module.JsonlLegLog, "close", lambda self: (closed.append(self), leg_log_close(self)))
    out = tmp_path / "run"
    with pytest.raises(OSError, match="broken pipe"):
        run_sim(CSV, TERRAIN, 1, 1, out_dir=out)
    assert len(closed) == 1
    shutil.rmtree(out)  # raises if a handle is still open (Windows)


def test_a_failed_close_is_printed_and_the_report_is_still_returned(tmp_path, monkeypatch, capsys):
    class FailingClose(StepLog):
        def close(self, join_s=2.0):
            super().close(join_s)
            return Failure("disk on fire")

    monkeypatch.setattr(run_module, "StepLog", FailingClose)
    report = _run(laps=1, out_dir=tmp_path / "run")
    assert "disk on fire" in capsys.readouterr().err
    assert report == _run(laps=1)
