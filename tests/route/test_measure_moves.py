"""Movement measurement tool (plan task B20): `MoveSample`, `summarize`, the jsonl line and `main`'s checks (piece 1),
and the measuring procedure behind `--supervised` (piece 2), driven by the simulator's `SimLink` or a scripted link
and position source.

All synthetic: no board, no game, no screen capture, no keyboard hook.
"""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from returns.result import Failure, Result, Success

from src.classes.classes import Kanna
from src.input.link import InputError, SendReport
from src.input.protocol import Done, DoneStatus, Key
from src.route import walk as walk_cli
from src.route.harness import measure_moves
from src.route.harness.measure_moves import MEASURE_FILE, MoveSample, append_sample, main, summarize
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.world import NoiseConfig, SimParams, SimWorld, load_terrain

SUMMARY_KEYS = {"reach_h_mean", "reach_h_sd", "reach_h_n", "reach_v_peak_mean", "walk_px_per_s",
                "teleport_settle_s", "drop_settle_s"}


def teleport(direction: str, dx: int, dy: int = 0, settle_s: float = 0.4, peak_y: int | None = None,
             start: tuple[int, int] = (80, 100)) -> MoveSample:
    return MoveSample("teleport", direction, start, (start[0] + dx, start[1] + dy), peak_y, settle_s, None)


def walk(direction: str, dx: int, held_ms: float = 2000.0, start: tuple[int, int] = (80, 100)) -> MoveSample:
    return MoveSample("walk", direction, start, (start[0] + dx, start[1]), None, 0.2, held_ms)


def drop(dy: int, settle_s: float) -> MoveSample:
    return MoveSample("drop", None, (80, 60), (80, 60 + dy), None, settle_s, None)


# ---- the plan's four cases ------------------------------------------------------------------------------------------

def test_alternating_19_and_20_teleports_give_mean_19_5_over_10():
    samples = [teleport("RIGHT", 19) if i % 2 == 0 else teleport("LEFT", -20) for i in range(10)]
    summary = summarize(samples)
    assert summary["reach_h_n"] == 10
    assert summary["reach_h_mean"] == pytest.approx(19.5)


def test_teleport_with_dy_5_is_excluded_from_reach():
    samples = [teleport("RIGHT", 19), teleport("LEFT", -20), teleport("RIGHT", 30, dy=5)]
    summary = summarize(samples)
    assert summary["reach_h_n"] == 2
    assert summary["reach_h_mean"] == pytest.approx(19.5)


def test_walks_of_16_px_over_2000_ms_give_8_px_per_s():
    samples = [walk("RIGHT", 16), walk("LEFT", -16), walk("RIGHT", 16)]
    assert summarize(samples)["walk_px_per_s"] == pytest.approx(8.0)


def test_main_without_supervised_returns_2(tmp_path, capsys):
    assert main(["--out", str(tmp_path / "m")]) == 2
    assert "--supervised" in capsys.readouterr().err
    assert not (tmp_path / "m").exists()


# ---- summarize, beyond the plan's cases -----------------------------------------------------------------------------

def test_summary_has_exactly_the_plan_keys():
    assert set(summarize([])) == SUMMARY_KEYS


def test_empty_samples_give_none_and_zero_n():
    assert summarize([]) == {key: (0 if key == "reach_h_n" else None) for key in SUMMARY_KEYS}


def test_dy_2_is_kept_and_dy_3_is_not():
    summary = summarize([teleport("RIGHT", 19, dy=2), teleport("LEFT", -19, dy=-2), teleport("RIGHT", 19, dy=3)])
    assert summary["reach_h_n"] == 2


def test_vertical_and_downward_teleports_do_not_count_as_horizontal_reach():
    summary = summarize([teleport("RIGHT", 20), teleport("UP", 0, dy=-1), teleport("DOWN", 0, dy=1)])
    assert summary["reach_h_n"] == 1


def test_reach_sd_is_the_sample_sd_and_none_below_two():
    assert summarize([teleport("RIGHT", 19)])["reach_h_sd"] is None
    summary = summarize([teleport("RIGHT", 18), teleport("LEFT", -20)])
    assert summary["reach_h_sd"] == pytest.approx(2 ** 0.5)


def test_vertical_peak_reach_is_rise_to_smallest_y_of_up_teleports_with_peak():
    samples = [teleport("UP", 0, dy=-20, peak_y=100 - 22), teleport("UP", 0, dy=-20, peak_y=100 - 24),
               teleport("UP", 0, dy=0, peak_y=None), teleport("RIGHT", 20, peak_y=10)]
    assert summarize(samples)["reach_v_peak_mean"] == pytest.approx(23.0)


def test_walks_without_held_time_are_left_out_of_speed():
    samples = [walk("RIGHT", 16), MoveSample("walk", "LEFT", (80, 100), (40, 100), None, 0.2, None)]
    assert summarize(samples)["walk_px_per_s"] == pytest.approx(8.0)


def test_settle_times_are_medians_per_kind():
    samples = [teleport("RIGHT", 19, settle_s=0.3), teleport("LEFT", -19, settle_s=0.9),
               teleport("DOWN", 0, dy=10, settle_s=0.5), drop(15, 0.6), drop(15, 0.8), walk("RIGHT", 16)]
    summary = summarize(samples)
    assert summary["teleport_settle_s"] == pytest.approx(0.6)  # the DOWN teleport is not a flat horizontal one (B3)
    assert summary["drop_settle_s"] == pytest.approx(0.7)


def test_teleport_settle_is_the_median_of_flat_horizontal_teleports():
    samples = [teleport("RIGHT", 19, settle_s=0.4), teleport("LEFT", -19, settle_s=0.4),
               *[teleport("UP", 0, peak_y=77, settle_s=0.8) for _ in range(3)], teleport("DOWN", 0, dy=10, settle_s=0.6)]
    assert summarize(samples)["teleport_settle_s"] == pytest.approx(0.4)


def test_directions_are_key_names():
    samples = [teleport(Key.RIGHT.name, 19), teleport(Key.LEFT.name, -19), teleport(Key.UP.name, 0, peak_y=77)]
    summary = summarize(samples)
    assert summary["reach_h_n"] == 2
    assert summary["reach_v_peak_mean"] == pytest.approx(23.0)


def test_moves_that_did_not_happen_are_left_out_of_reach_and_walk_speed():
    samples = [teleport("RIGHT", 19), teleport("LEFT", -19), teleport("RIGHT", 0), teleport("LEFT", -2),
               *[teleport("UP", 0, peak_y=p) for p in (77, 100, 101, 98)],
               walk("RIGHT", 16), walk("LEFT", 0)]
    summary = summarize(samples)
    assert (summary["reach_h_n"], summary["reach_h_mean"]) == (2, pytest.approx(19.0))
    assert summary["reach_v_peak_mean"] == pytest.approx(23.0)
    assert summary["walk_px_per_s"] == pytest.approx(8.0)


# ---- measure.jsonl ---------------------------------------------------------------------------------------------------

def test_append_sample_writes_one_json_object_per_line(tmp_path):
    path = tmp_path / MEASURE_FILE
    first, second = teleport("UP", 0, dy=-20, peak_y=77), walk("LEFT", -16)
    append_sample(path, first)
    append_sample(path, second)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [
        {"kind": "teleport", "direction": "UP", "before": [80, 100], "after": [80, 80], "peak_y": 77,
         "settle_s": 0.4, "held_ms": None},
        {"kind": "walk", "direction": "LEFT", "before": [80, 100], "after": [64, 100], "peak_y": None,
         "settle_s": 0.2, "held_ms": 2000.0},
    ]


# ---- main's checks ---------------------------------------------------------------------------------------------------

@pytest.fixture
def ran(monkeypatch):
    """Replace the procedure, so a test can see whether main got as far as starting it."""
    calls: list[tuple[int, object]] = []

    def fake_run(args, out):
        calls.append((args.reps, out))
        return 0

    monkeypatch.setattr(measure_moves, "_run_supervised", fake_run)
    return calls


def test_supervised_with_a_fresh_out_starts_the_procedure(tmp_path, ran):
    out = tmp_path / "m"
    assert main(["--supervised", "--out", str(out), "--reps", "4"]) == 0
    assert ran == [(4, out)]


def test_reps_defaults_to_10(tmp_path, ran):
    assert main(["--supervised", "--out", str(tmp_path / "m")]) == 0
    assert ran[0][0] == 10


@pytest.mark.parametrize("reps", ["1", "0", "-3"])
def test_reps_below_2_returns_2(tmp_path, ran, capsys, reps):
    assert main(["--supervised", "--out", str(tmp_path / "m"), "--reps", reps]) == 2
    assert "--reps" in capsys.readouterr().err
    assert ran == []


def test_non_integer_reps_returns_2(tmp_path, ran):
    assert main(["--supervised", "--out", str(tmp_path / "m"), "--reps", "ten"]) == 2
    assert ran == []


def test_out_holding_measure_jsonl_is_refused(tmp_path, ran, capsys):
    (tmp_path / MEASURE_FILE).write_text("", encoding="utf-8")
    assert main(["--supervised", "--out", str(tmp_path)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert ran == []


def test_out_that_is_a_file_is_refused(tmp_path, ran, capsys):
    path = tmp_path / "file.txt"
    path.write_text("x", encoding="utf-8")
    assert main(["--supervised", "--out", str(path)]) == 2
    assert "not a folder" in capsys.readouterr().err
    assert ran == []


def test_default_out_is_a_stamped_folder_under_data_route_measure(ran):
    assert main(["--supervised"]) == 0
    out = ran[0][1]
    assert out.parent == measure_moves._REPO / "data" / "route" / "measure"  # noqa: SLF001
    assert not out.exists()


def test_out_holding_summary_json_is_refused(tmp_path, ran, capsys):
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    assert main(["--supervised", "--out", str(tmp_path)]) == 2
    assert "summary.json already exists" in capsys.readouterr().err
    assert ran == []


# ---- the procedure (piece 2), over fakes ------------------------------------------------------------------------------

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fossil_foundry_2.csv"
TERRAIN = Path(__file__).resolve().parents[1] / "fixtures" / "ff2_terrain.json"
GUARD = "measure-stop-guard"


def _ok_report() -> SendReport:
    return SendReport(1, 0, 0, None, 0, None, Done(1, DoneStatus.OK, None, 0, 0, ()))


class Scripted:
    """A link and a position source on one SimClock. Each play takes 30 ms; then the reads follow the next entry of
    `moves`: a list of (seconds after the play returned, position or Failure), or None to stay where she is."""

    def __init__(self, clock: SimClock, start: tuple[int, int], moves) -> None:
        self.clock = clock
        self.moves = list(moves)
        self.points: list = [(0.0, start)]
        self.end_ns = clock.now_ns()

    def play(self, steps):
        self.clock.advance(30_000_000)
        self.end_ns = self.clock.now_ns()
        nxt = self.moves.pop(0) if self.moves else None
        self.points = nxt if nxt is not None else [(0.0, self.points[-1][1])]
        return Success(_ok_report())

    def release_all(self):
        return Success(_ok_report())

    def read(self):
        elapsed = (self.clock.now_ns() - self.end_ns) / 1e9
        point = self.points[0][1]
        for t, p in self.points:
            if t <= elapsed:
                point = p
        return point if isinstance(point, Failure) else Success(point)


class RigLink:
    """`inner`'s play/release_all plus connect/close, logging into the shared `log`."""

    def __init__(self, inner, log: list[str]) -> None:
        self.inner = inner
        self.log = log
        self.connect_result: Result[None, str] = Success(None)
        self.play_result = None  # a Failure here is returned instead of playing
        self.on_play = None      # called with the play count before each play
        self.plays: list[tuple] = []

    def connect(self):
        return self.connect_result

    def play(self, steps):
        self.plays.append(tuple(steps))
        if self.on_play is not None:
            self.on_play(len(self.plays))
        if self.play_result is not None:
            return self.play_result
        return self.inner.play(steps)

    def release_all(self):
        self.log.append("release_all")
        return self.inner.release_all()

    def close(self) -> None:
        self.log.append("link.close")


class FakeStop:
    """The stop control's surface; `trigger` keeps the first source and calls `on_stop`, as the real worker would."""

    def __init__(self, on_stop, log: list[str], start_result) -> None:
        self.on_stop = on_stop
        self.log = log
        self.start_result = start_result
        self.trigger_ns: int | None = None
        self.trigger_source: str | None = None

    def start(self):
        return self.start_result

    def trigger(self, source: str = "hotkey") -> None:
        if self.trigger_source is None:
            self.trigger_source = source
            self.on_stop()

    def close(self) -> None:
        self.log.append("stop.close")


@pytest.fixture
def rig(monkeypatch, tmp_path):
    env = SimpleNamespace(log=[], clock=SimClock(), out=tmp_path / "m", prompts=0, stops=[], on_prompt=None,
                          stop_start=Success(None), first_frame=Success(None), link=None, positions=None, links=0)

    def use(inner, positions) -> None:
        env.link = RigLink(inner, env.log)
        env.positions = positions

    def use_sim(start=(113.0, 60.0)) -> None:
        rg, _ = walk_cli._load_route(FIXTURE, Kanna()).unwrap()  # noqa: SLF001
        params = SimParams()
        world = SimWorld(load_terrain(TERRAIN).unwrap(), rg.reach_h, rg.reach_v, params, NoiseConfig.off(),
                         np.random.default_rng(0), start)
        use(SimLink(world, env.clock, params), SimPositionSource(world, env.clock, 0.0, np.random.default_rng(1)))

    def use_script(moves, start=(80, 100)) -> Scripted:
        scripted = Scripted(env.clock, start, moves)
        use(scripted, scripted)
        return scripted

    def make_link():
        env.links += 1
        return env.link

    def make_stop(on_stop):
        stop = FakeStop(on_stop, env.log, env.stop_start)
        env.stops.append(stop)
        return stop

    def read_line() -> str:
        env.prompts += 1
        if env.on_prompt is not None:
            env.on_prompt(env.prompts)
        return ""

    monkeypatch.setattr(measure_moves, "_start_capture", lambda shared: None)
    monkeypatch.setattr(measure_moves, "_wait_first_frame", lambda shared, timeout_s: env.first_frame)
    monkeypatch.setattr(measure_moves, "_make_positions", lambda shared: env.positions)
    monkeypatch.setattr(measure_moves, "_make_link", make_link)
    monkeypatch.setattr(measure_moves, "_make_clock", lambda: env.clock)
    monkeypatch.setattr(measure_moves, "_make_stop_control", make_stop)
    monkeypatch.setattr(measure_moves, "_read_line", read_line)
    monkeypatch.setattr(measure_moves, "_countdown_s", 0)

    def run(reps: int = 2) -> int:
        code = main(["--supervised", "--out", str(env.out), "--reps", str(reps)])
        assert GUARD not in {t.name for t in threading.enumerate() if t.is_alive()}
        return code

    env.use_sim, env.use_script, env.run = use_sim, use_script, run
    return env


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _samples(lines: list[dict]) -> list[MoveSample]:
    return [MoveSample(**{**d, "before": tuple(d["before"]), "after": tuple(d["after"])}) for d in lines]


def _cleanup_order(log: list[str]) -> list[str]:
    """The log from the last release_all on, so the stop guard's earlier releases do not matter."""
    last = max(i for i, e in enumerate(log) if e == "release_all")
    return log[last:]


def test_procedure_over_the_simulator_runs_every_phase_in_order(rig, capsys):
    rig.use_sim()
    assert rig.run(4) == 0
    lines = _lines(rig.out / MEASURE_FILE)
    assert [(d["kind"], d["direction"]) for d in lines] == (
        [("teleport", "RIGHT"), ("teleport", "LEFT")] * 2 + [("walk", "RIGHT"), ("walk", "LEFT")] * 2
        + [("teleport", "UP")] * 4 + [("teleport", "DOWN")] * 2 + [("drop", None)] * 2)
    assert len(rig.link.plays) == 16
    assert rig.prompts == 1 + 1 + 2 + 2  # floor, open space, then one before each teleport-down and each drop
    assert all((d["held_ms"] is not None) == (d["kind"] == "walk") for d in lines)
    assert all((d["peak_y"] is not None) == (d["direction"] == "UP") for d in lines)
    assert all(d["before"] == prev["after"] for prev, d in zip(lines[:7], lines[1:8], strict=True))
    summary = json.loads((rig.out / "summary.json").read_text(encoding="utf-8"))
    assert summary == summarize(_samples(lines))
    assert summary["reach_h_n"] == 4 and summary["reach_h_mean"] is not None  # directions match summarize (B1)
    assert _cleanup_order(rig.log) == ["release_all", "link.close", "stop.close"]
    assert "summary of 16 samples" in capsys.readouterr().out


def test_samples_carry_the_settled_position_settle_time_peak_and_drawn_hold(rig):
    right = [(0.0, (80, 100)), (0.05, (99, 100))]
    left = [(0.0, (99, 100)), (0.1, (79, 100))]
    up = [(0.0, (79, 100)), (0.03, (79, 90)), (0.06, (79, 77)), (0.09, (79, 84)), (0.2, (79, 100))]
    rig.use_script([right, left, None, None, up])
    assert rig.run(2) == 0
    lines = _lines(rig.out / MEASURE_FILE)
    assert lines[0] == {"kind": "teleport", "direction": "RIGHT", "before": [80, 100], "after": [99, 100],
                        "peak_y": None, "settle_s": pytest.approx(0.05), "held_ms": None}
    assert lines[1]["before"] == [99, 100] and lines[1]["after"] == [79, 100]
    assert lines[1]["settle_s"] == pytest.approx(0.1)
    walk_steps = rig.link.plays[2]
    assert lines[2]["kind"] == "walk"
    assert lines[2]["held_ms"] == pytest.approx(walk_steps[1].delta_100us / 10, abs=0.05)
    assert lines[4] == {"kind": "teleport", "direction": "UP", "before": [79, 100], "after": [79, 100],
                        "peak_y": 77, "settle_s": pytest.approx(0.2), "held_ms": None}
    assert summarize(_samples(lines))["reach_v_peak_mean"] == pytest.approx(23.0)


def test_a_move_that_does_not_settle_gives_no_sample_and_the_next_before_is_settled_afresh(rig, capsys):
    jitter = [(i * 0.1, (80 + i % 2, 100)) for i in range(60)] + [(6.0, (90, 100))]
    rig.use_script([jitter])
    assert rig.run(2) == 0
    lines = _lines(rig.out / MEASURE_FILE)
    assert len(rig.link.plays) == 8 and len(lines) == 7
    assert (lines[0]["direction"], lines[0]["before"]) == ("LEFT", [90, 100])
    assert "no sample" in capsys.readouterr().err


def test_character_lost_after_a_move_exits_10_after_cleanup(rig, capsys):
    rig.use_script([[(0.0, Failure("miss"))]])
    assert rig.run(2) == 10
    assert "character lost" in capsys.readouterr().err
    assert len(rig.link.plays) == 1
    assert _cleanup_order(rig.log) == ["release_all", "link.close", "stop.close"]
    assert json.loads((rig.out / "summary.json").read_text(encoding="utf-8"))["reach_h_n"] == 0


def test_f12_at_a_prompt_stops_without_sending_a_key(rig, capsys):
    rig.use_script([])
    rig.on_prompt = lambda n: rig.stops[0].trigger("hotkey")
    assert rig.run(2) == 0
    assert rig.link.plays == []
    assert "stopped (hotkey)" in capsys.readouterr().out
    assert _cleanup_order(rig.log) == ["release_all", "link.close", "stop.close"]
    assert not (rig.out / MEASURE_FILE).exists()
    assert (rig.out / "summary.json").exists()


def test_a_stop_during_a_move_sends_no_further_move_and_releases(rig):
    rig.use_script([])
    rig.link.on_play = lambda n: rig.stops[0].trigger("sigint") if n == 3 else None
    assert rig.run(2) == 0
    assert len(rig.link.plays) == 3
    assert len(_lines(rig.out / MEASURE_FILE)) == 2  # the move in flight when the stop came gives no sample
    assert rig.log.count("release_all") >= 2  # the stop guard's and the cleanup's
    assert _cleanup_order(rig.log) == ["release_all", "link.close", "stop.close"]


def test_stdin_closed_at_a_prompt_stops(rig, monkeypatch, capsys):
    rig.use_script([])

    def closed() -> str:
        raise EOFError

    monkeypatch.setattr(measure_moves, "_read_line", closed)
    assert rig.run(2) == 0
    assert rig.link.plays == []
    assert "stdin closed" in capsys.readouterr().err


@pytest.mark.parametrize(("error", "code"), [(InputError.LINK_LOST, 1), (InputError.NOT_CONNECTED, 1),
                                             (InputError.REJECTED, 6), (InputError.ACK_TIMEOUT, 6)])
def test_a_failed_move_exits_with_walks_code(rig, error, code):
    rig.use_script([])
    rig.link.play_result = Failure(error)
    assert rig.run(2) == code
    assert _cleanup_order(rig.log)[-2:] == ["link.close", "stop.close"]
    assert "release_all" in rig.log


def test_an_exception_in_the_procedure_exits_7_after_cleanup(rig, capsys):
    rig.use_script([])

    def boom(n: int) -> None:
        raise RuntimeError("kaboom")

    rig.link.on_play = boom
    assert rig.run(2) == 7
    assert "kaboom" in capsys.readouterr().err
    assert _cleanup_order(rig.log) == ["release_all", "link.close", "stop.close"]


def test_connect_failure_exits_4_without_release_all(rig):
    rig.use_script([])
    rig.link.connect_result = Failure("no board")
    assert rig.run(2) == 4
    assert rig.log == ["link.close"]
    assert rig.stops == []


def test_stop_control_failure_exits_9_and_releases(rig):
    rig.use_script([])
    rig.stop_start = Failure("hook: nope")
    assert rig.run(2) == 9
    assert rig.link.plays == []
    assert rig.log == ["release_all", "link.close", "stop.close"]
    assert not (rig.out / "summary.json").exists()


def test_no_first_frame_exits_3_before_connecting(rig):
    rig.use_script([])
    rig.first_frame = Failure("no frame")
    assert rig.run(2) == 3
    assert rig.links == 0


def test_character_not_found_exits_3_before_connecting(rig):
    rig.use_script([], start=Failure("miss"))
    assert rig.run(2) == 3
    assert rig.links == 0


def test_icon_failure_exits_5_before_capture(rig, monkeypatch):
    monkeypatch.setattr(measure_moves, "_make_positions", lambda shared: Failure("character icon: missing"))
    assert rig.run(2) == 5
    assert rig.links == 0
