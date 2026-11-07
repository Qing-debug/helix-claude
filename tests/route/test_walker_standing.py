"""Tests for the walker's still rule and the off-map stop (off-graph fix plan, Task 6, OFFP141; Hong's QB213, QB215).

The rule: while the character cannot be placed on the map (the strict rules A1-A3 fail and the planner has no plan),
the walker times how long it has read the same position in a row. At `standing_s` (0.5 s) the position is attached to
the nearest platform line within `standing_band_px` (6 px), and the walker plans from that point; with no line that
close it logs one line and ends the run (outcome `off_map`). A position that changes, a miss, a plan, or a move sent
restarts the timer; a STALE read does not.

Walker unit tests use a `SimClock` and a scripted position source (`fn(elapsed_ns) -> Result`) on the Fossil Foundry 2
fixture map, with a recording layer that never moves anything. The sim scenarios run the real `InputLayer`, `SimLink`
and `SimWorld` on a small map written to a tmp folder (the dot 3 px above the foot, no noise).
"""

import dataclasses
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.input.input_layer import InputLayer
from src.input.timing import TimingModel
from src.route.leg_log import LegOutcome, MemorySink
from src.route.locate import attach, attach_standing
from src.route.params import MovementParams
from src.route.planner import next_move
from src.route.route_graph import RouteGraph
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.run import REAL_GROUND_NOISE, REAL_GROUND_SIM, REAL_GROUND_TERRAIN, judge, run_sim
from src.route.sim.world import NoiseConfig, SimParams, SimPlatform, SimTerrain, SimWorld
from src.route.walker import STALE, RouteWalker, WalkerConfig, _Stillness

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
STANDING_NS = round(MovementParams().standing_s * 1e9)
POLL_NS = round(MovementParams().poll_s * 1e9)
S = 1_000_000_000

TOP_RIGHT = (101, 24)
FLOOR_MID = (82, 60)

OFF_MAP_LINE = "the character is standing at ({x}, {y}), more than 6 px from any platform of the map; ending the run"


# --- rigs ----------------------------------------------------------------------------------------------------


def _write_map(path: Path, points) -> Path:
    rows = ["# minimap_size=162,82",
            "nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,"
            "is_left_platform_edge,is_right_platform_edge"]
    rows += [f'blue_node,"({x}, {y})",FALSE,FALSE,FALSE,FALSE' for x, y in points]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _load(csv: Path):
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(csv))
    built = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(built)
    rg = built.unwrap()
    return SimpleNamespace(rg=rg, nodes={n.minimap_position: n for n in agent.node_map.values()},
                           rotation=list(agent.map_rotation_order))


@pytest.fixture(scope="module")
def ff2():
    return _load(CSV)


def steps(*segments):
    """`fn(elapsed_ns)` for a scripted source: each segment is (from_s, result); the last one started wins."""
    table = [(round(from_s * S), result) for from_s, result in segments]

    def fn(elapsed_ns):
        current = table[0][1]
        for start, result in table:
            if elapsed_ns >= start:
                current = result
        return current

    return fn


class ScriptedSource:
    """A position source: a read returns `fn(ns since the leg began)`; every read is logged as (t_ns, result)."""

    def __init__(self, clock, fn):
        self.clock, self.fn, self.on_read = clock, fn, None
        self.t0 = clock.now_ns()
        self.reads = []

    def read(self):
        elapsed = self.clock.now_ns() - self.t0
        if self.on_read is not None:
            self.on_read(elapsed)
        result = self.fn(elapsed)
        self.reads.append((self.clock.now_ns(), result))
        return result


class RecordingLayer:
    """Stands in for `InputLayer`: logs each play as (t_ns, kind) and moves nothing."""

    def __init__(self, clock):
        self.clock, self.plays, self.releases = clock, [], 0

    def _play(self, kind):
        self.plays.append((self.clock.now_ns(), kind))
        return Success(SimpleNamespace(reaction=None, waited_ms=None, plan=SimpleNamespace(steps=())))

    def walk(self, direction, intended_ms, stimulus_ns=None):
        return self._play("walk")

    def teleport(self, direction, stimulus_ns=None):
        return self._play("teleport")

    def drop(self, stimulus_ns=None):
        return self._play("drop")

    def release_all(self):
        self.releases += 1


class Rec:
    """A step observer that keeps every event as (name, t_ns, *args)."""

    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(t_ns, *args):
            self.events.append((name, t_ns, *args))

        return record

    def of(self, name):
        return [e for e in self.events if e[0] == name]


def _rig(world, fn, *, leg_timeout_s=2.0, stop_at_s=None, layer=None, clock=None, source=None):
    """A walker over `world` (an FF2 namespace from `_load`), a scripted source `fn`, a recording layer, an observer."""
    clock = SimClock() if clock is None else clock
    source = ScriptedSource(clock, fn) if source is None else source
    layer = RecordingLayer(clock) if layer is None else layer
    rec, lines = Rec(), []
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=leg_timeout_s)
    walker = RouteWalker(world.rg, world.rotation, layer, source, clock, MemorySink(), np.random.default_rng(0), config,
                         observer=rec, log=lines.append)
    if stop_at_s is not None:
        source.on_read = lambda elapsed: walker._stop.set() if elapsed >= round(stop_at_s * S) else None
    return SimpleNamespace(walker=walker, clock=clock, source=source, layer=layer, rec=rec, lines=lines)


def _leg(ff2, rig, dst_xy):
    return rig.walker.walk_leg(1, 0, None, ff2.nodes[dst_xy])


# --- 1. the walker's still rule ------------------------------------------------------------------------------


def test_a_fall_never_attaches(ff2):
    """OFFG-R5 fall guard. A slow fall above the top ramp: every position fails the strict attach and passes the loose
    one (the 5 recorded mid-air reads are like this), the character is never still for 0.5 s, so nothing is sent."""
    cycle = [(70, 21), (70, 22), (70, 23), (71, 23), (71, 22), (71, 21), (72, 21), (72, 22), (72, 23)]
    for pos in cycle:
        assert not is_successful(attach(ff2.rg, pos)), pos
        assert is_successful(attach_standing(ff2.rg, pos)), pos
    segments, t = [], 0.0
    for i, pos in enumerate(cycle * 2):
        segments.append((t, Success(pos)))
        if i == 1:  # a long hold: 10 reads, 0.45 s between the first and the last, just under standing_s
            t += 0.50
        elif i == 3:  # another, with a STALE read inside it (9 successful reads, 10 polls)
            segments += [(t + 0.20, Failure(STALE)), (t + 0.25, Success(pos))]
            t += 0.50
        else:
            t += 0.15
    rig = _rig(ff2, steps(*segments), leg_timeout_s=2.0)

    record = _leg(ff2, rig, TOP_RIGHT)

    assert record.outcome == LegOutcome.TIMED_OUT
    assert rig.layer.plays == [] and rig.rec.of("planned") == [] and record.moves == ()
    assert len(rig.rec.of("no_plan")) > 30 and len(rig.rec.of("read_failed")) >= 1
    runs, last = [], None   # the reads of one position in a row (a STALE read does not end the run)
    for t, result in rig.source.reads:
        if result == Failure(STALE):
            continue
        if result != last:
            runs.append([])
        runs[-1].append(t)
        last = result
    long_runs = [run for run in runs if len(run) > 3]
    assert sorted(len(run) for run in long_runs) == [9, 10]
    assert all(run[-1] - run[0] == 9 * POLL_NS < STANDING_NS for run in long_runs)  # 0.45 s, the most that never fires


def test_still_half_second_attaches_and_moves(ff2):
    rig = _rig(ff2, steps((0, Success((70, 22)))), leg_timeout_s=1.0)

    record = _leg(ff2, rig, TOP_RIGHT)

    first = record.moves[0]
    assert first.pos == (70, 27)
    assert rig.rec.of("planned")[0][2] == (70, 27)
    first_no_plan = rig.rec.of("no_plan")[0]
    assert first_no_plan[2] == (70, 22)
    assert STANDING_NS <= first.t_ns - first_no_plan[1] < STANDING_NS + POLL_NS


def test_a_new_position_restarts_the_timer(ff2):
    rig = _rig(ff2, steps((0, Success((70, 22))), (0.4, Success((71, 22)))), leg_timeout_s=1.5)

    record = _leg(ff2, rig, TOP_RIGHT)

    t0 = rig.source.t0
    assert record.moves[0].pos == (71, 26)  # the line at x 71 is y 26.46
    assert round(0.4 * S) + STANDING_NS <= rig.layer.plays[0][0] - t0 < round(0.4 * S) + STANDING_NS + POLL_NS


def test_a_miss_restarts_and_stale_does_not(ff2):
    stale = _rig(ff2, steps((0, Success((70, 22))), (0.2, Failure(STALE)), (0.3, Success((70, 22)))), leg_timeout_s=1.0)
    _leg(ff2, stale, TOP_RIGHT)
    assert stale.rec.of("read_failed")[0][2] == STALE
    assert STANDING_NS <= stale.layer.plays[0][0] - stale.source.t0 < STANDING_NS + POLL_NS

    miss = _rig(ff2, steps((0, Success((70, 22))), (0.3, Failure("miss")), (0.35, Success((70, 22)))),
                leg_timeout_s=1.5)
    _leg(ff2, miss, TOP_RIGHT)
    after_miss = round(0.35 * S)
    assert after_miss + STANDING_NS <= miss.layer.plays[0][0] - miss.source.t0 < after_miss + STANDING_NS + POLL_NS


def test_standing_off_the_map_ends_the_run(ff2):
    """(90, 35): no strict attach ((90, 40) would attach by A3 to mid-right's end), and no line within 6 px."""
    assert not is_successful(attach(ff2.rg, (90, 35)))
    assert not is_successful(attach_standing(ff2.rg, (90, 35)))
    rig = _rig(ff2, steps((0, Success((90, 35)))), leg_timeout_s=30.0)

    result = rig.walker.run()

    assert is_successful(result)
    summary = result.unwrap()
    assert (summary.laps_completed, summary.legs, summary.reason) == (0, 1, "off_map")
    leg_end = rig.rec.of("leg_end")[0]
    assert leg_end[2] == "off_map"
    first_no_plan = rig.rec.of("no_plan")[0][1]
    assert STANDING_NS <= leg_end[1] - first_no_plan < STANDING_NS + POLL_NS
    assert rig.lines == [OFF_MAP_LINE.format(x=90, y=35)]
    assert rig.layer.plays == [] and rig.layer.releases >= 1
    assert rig.source.reads[-1][0] == leg_end[1]  # no read, so nothing, after the stop


def test_the_off_map_line_names_the_params_band(ff2):
    """The band in the line comes from `standing_band_px`, not a literal."""
    rg = ff2.rg
    narrow = RouteGraph(rg.graph, rg.terrain, rg.align_px, rg.reach_h, rg.reach_v,
                        dataclasses.replace(rg.params, standing_band_px=2))
    world = SimpleNamespace(rg=narrow, nodes=ff2.nodes, rotation=ff2.rotation)
    rig = _rig(world, steps((0, Success((70, 22)))), leg_timeout_s=5.0)  # 4.77 px off the ramp: outside a 2 px band

    record = _leg(ff2, rig, TOP_RIGHT)

    assert record.outcome == LegOutcome.OFF_MAP
    assert rig.lines == ["the character is standing at (70, 22), more than 2 px from any platform of the map; "
                         "ending the run"]


@pytest.mark.parametrize("pos", [(81, 60), (90, 60)], ids=["A1 near the end node", "A3 beyond the end"])
def test_no_path_is_never_off_map(tmp_path, pos):
    """Review Focus 2: a character the strict rules place, with no path to the target, is not "off the map"."""
    world = _load(_write_map(tmp_path / "two.csv", [(40, 60), (80, 60), (40, 10), (80, 10)]))
    dst = world.nodes[(80, 10)]
    anchor = attach(world.rg, pos)
    assert is_successful(anchor)
    assert not is_successful(attach_standing(world.rg, pos))  # outside every segment: the loose attach cannot see it
    planned = next_move(world.rg, pos, dst, np.random.default_rng(0))
    assert not is_successful(planned) and "no path" in planned.failure()
    rig = _rig(world, steps((0, Success(pos))), leg_timeout_s=2.0)

    record = rig.walker.walk_leg(1, 0, None, dst)

    assert record.outcome == LegOutcome.TIMED_OUT
    assert rig.lines == [] and rig.layer.plays == [] and rig.rec.of("planned") == []
    assert record.off_graph > 20  # it polled and waited as it does today


def test_each_loose_move_waits_again(ff2):
    """Plan review M3: after a loose move that changed nothing, another 0.5 s of the same position is needed."""
    rig = _rig(ff2, steps((0, Success((70, 22)))), leg_timeout_s=3.0)

    _leg(ff2, rig, TOP_RIGHT)

    plays, settled = [t for t, _ in rig.layer.plays], [e[1] for e in rig.rec.of("settled")]
    assert len(plays) >= 3 and len(settled) >= len(plays) - 1
    for settle_end, next_play in zip(settled, plays[1:]):
        assert next_play - settle_end >= STANDING_NS


def test_stop_wins_during_the_wait(ff2):
    rig = _rig(ff2, steps((0, Success((70, 22)))), leg_timeout_s=5.0, stop_at_s=0.3)

    record = _leg(ff2, rig, TOP_RIGHT)

    assert record.outcome == LegOutcome.STOPPED
    assert rig.layer.plays == [] and rig.lines == []


def test_loose_attach_on_the_target_is_reached(ff2):
    """Review Focus 5: 5 px over the floor at the target's x is the target once the character has stood there."""
    assert not is_successful(attach(ff2.rg, (82, 55)))
    rig = _rig(ff2, steps((0, Success((82, 55)))), leg_timeout_s=5.0)

    record = _leg(ff2, rig, FLOOR_MID)

    assert record.outcome == LegOutcome.REACHED
    assert rig.layer.plays == [] and record.moves == ()
    reached = rig.rec.of("reached")[0]
    assert reached[2] == FLOOR_MID
    assert STANDING_NS <= reached[1] - rig.rec.of("no_plan")[0][1] < STANDING_NS + POLL_NS


def test_stillness_unit():
    still = _Stillness()
    assert still.see((1, 1), 10 * S) == 0.0
    assert still.see((1, 1), 10 * S + 400_000_000) == 0.4
    assert still.see((1, 2), 10 * S + 450_000_000) == 0.0   # a new position restarts it
    assert still.see((1, 2), 10 * S + 950_000_000) == 0.5
    still.reset()
    assert still.see((1, 2), 11 * S) == 0.0                  # even the same position starts again after a reset


# --- 2. the simulated world ----------------------------------------------------------------------------------


def _sim_rig(tmp_path, nodes, platforms, start, dst, *, shove_dx=None, leg_timeout_s=120.0):
    """The real walker, `InputLayer`, `SimLink` and `SimWorld` (dot 3 px above the foot, no noise) on a map with
    `nodes` and a world made of `platforms`. `shove_dx`: after the first move a mob shoves the character that far,
    recorded at the move's own end, as `SimWorld` injects its knockbacks."""
    world = _load(_write_map(tmp_path / "sim.csv", nodes))
    sim_params = SimParams(dot_above_foot_px=3)
    terrain = SimTerrain((162, 82), tuple(SimPlatform(*p) for p in platforms))
    sim = SimWorld(terrain, world.rg.reach_h, world.rg.reach_v, sim_params, NoiseConfig.off(),
                   np.random.default_rng(1), start)
    shoves = []
    if shove_dx is not None:
        for name in ("walk", "teleport", "drop"):
            def shoving(*args, _real=getattr(sim, name)):
                effect = _real(*args)
                if not shoves:
                    shoves.append(sim.knockback(shove_dx, effect.t_end_ns))
                return effect

            setattr(sim, name, shoving)
    clock = SimClock()
    reads = []

    class Logged:
        def __init__(self):
            self.inner = SimPositionSource(sim, clock, miss_p=0, rng=np.random.default_rng(2))

        def read(self):
            result = self.inner.read()
            reads.append((clock.now_ns(), result.unwrap()))
            return result

    layer = InputLayer(SimLink(sim, clock, sim_params), TimingModel(np.random.default_rng(3)))
    rig = _rig(world, None, leg_timeout_s=leg_timeout_s, layer=layer, clock=clock, source=Logged())
    return SimpleNamespace(rig=rig, sim=sim, world=world, shoves=shoves, reads=reads, dst=world.nodes[dst])


def test_sim_bump_is_walked(tmp_path):
    """The world's ground is 5 px below the map's line between x 61 and 100: the strict attach never places the
    character there. Standing 0.5 s and the loose attach walk it to a target in the middle of the bump."""
    ground = ((10, 50), (60, 50), (61, 55), (100, 55), (101, 50), (150, 50))
    sim = _sim_rig(tmp_path, [(x, 50) for x in range(20, 141, 15)], [("floor", False, ground)],
                   start=(20, 50), dst=(80, 50))

    record = sim.rig.walker.walk_leg(1, 0, None, sim.dst)

    assert record.outcome == LegOutcome.REACHED, (record.outcome, sim.sim.pos)
    assert sim.sim.pos[1] == 55  # it stands on the bump
    assert sim.rig.rec.of("no_plan"), "the walker never had to stand and wait"


def _off_the_end(tmp_path, drop_px):
    """The map's one platform ends at x 100; a shove after the first move (a teleport to x 99) carries the character
    8 px right, off the end, onto a world-only ledge `drop_px` below the platform: ground the map does not have."""
    ledge = ((101, 50 + drop_px), (140, 50 + drop_px))   # the main platform ends at x 100
    return _sim_rig(tmp_path, [(x, 50) for x in range(20, 96, 15)],
                    [("main", True, ((10, 50), (100, 50))), ("ledge", True, ledge)],
                    start=(80, 50), dst=(95, 50), shove_dx=8)


def test_sim_knockback_onto_an_unmapped_ledge_stops(tmp_path):
    """A mob shoves the character off the platform's end onto ground 10 px below the map's line: nothing to attach to
    within 6 px, so the run ends 0.5 s after the walker's first read on the ledge, with no key sent after the shove."""
    sim = _off_the_end(tmp_path, 10)

    record = sim.rig.walker.walk_leg(1, 0, None, sim.dst)

    shove = sim.shoves[0]
    assert shove.end[0] > 100 and shove.end[1] == 60.0   # off the platform's end, onto the ledge
    assert record.outcome == LegOutcome.OFF_MAP
    landing_ns = shove.t_end_ns + round(shove.settle_s * S)
    assert [e for e in sim.rig.rec.of("planned") if e[1] > shove.t_end_ns] == []
    assert len(sim.rig.rec.of("sent")) == 1  # only the move before the shove
    leg_end = sim.rig.rec.of("leg_end")[0]
    last_spell = [e for e in sim.rig.rec.of("no_plan") if e[1] >= landing_ns]
    assert STANDING_NS <= leg_end[1] - last_spell[0][1] < STANDING_NS + POLL_NS
    assert sim.rig.lines == [OFF_MAP_LINE.format(x=round(shove.end[0]), y=60)]


def test_sim_knockback_through_the_air_sends_nothing(tmp_path):
    """Review Focus 3: the shove FALLS (20 px, 0.63 s in the air), so every read differs while it lasts. The walker is
    in its settle then; it plans and plays nothing until the character has landed and stood 0.5 s."""
    sim = _off_the_end(tmp_path, 20)

    record = sim.rig.walker.walk_leg(1, 0, None, sim.dst)

    shove = sim.shoves[0]
    landing_ns = shove.t_end_ns + round(shove.settle_s * S)
    assert shove.settle_s == pytest.approx(0.3 + 20 / 60.0)
    in_air = [pos for t, pos in sim.reads if shove.t_end_ns <= t < landing_ns]
    assert len(in_air) >= 8
    assert all(a != b for a, b in zip(in_air, in_air[1:])), in_air  # the reads changed at every poll
    assert [e for e in sim.rig.rec.of("planned") if e[1] > shove.t_end_ns] == []
    assert len(sim.rig.rec.of("sent")) == 1
    assert record.outcome == LegOutcome.OFF_MAP  # and then it stood on ground the map does not have


# --- 3. the rule alone on the real ground --------------------------------------------------------------------


def test_sim_real_ground_rule_alone(ff2):
    """Plan review I2b. The dot 6 px above the foot leaves the reads 3 px high after part 1's shift, exactly what
    today's walker saw live, so only part 2 (this task) can save the top slope."""
    rec = Rec()
    sim_params = dataclasses.replace(REAL_GROUND_SIM, dot_above_foot_px=6)

    result = run_sim(CSV, REAL_GROUND_TERRAIN, 20, 2, noise=REAL_GROUND_NOISE, sim_params=sim_params, observer=rec)

    assert is_successful(result), result
    report = result.unwrap()
    checks = judge(report, 0)
    assert all(check.ok for check in checks), [check for check in checks if not check.ok]
    assert [e for e in rec.of("leg_end") if e[2] == "off_map"] == []
    # a loose attach: a `planned` at the snapped point of a position read in `no_plan` for >= standing_s (a landing has
    # the same event pattern but fails both tests; OFFP141-RR N2)
    loose, spell_pos, spell_t0 = 0, None, 0
    for name, t_ns, *args in rec.events:
        if name == "no_plan":
            if args[0] != spell_pos:
                spell_pos, spell_t0 = args[0], t_ns
        elif name == "read_failed":
            if args[0] != STALE:
                spell_pos = None
        elif name == "planned":
            if spell_pos is not None and t_ns - spell_t0 >= STANDING_NS:
                snapped = attach_standing(ff2.rg, spell_pos)
                loose += is_successful(snapped) and snapped.unwrap() == args[0] != spell_pos
            spell_pos = None
        elif name != "stall":
            spell_pos = None
    print(f"loose attaches on the real ground, dot 6, seed 2, 20 laps: {loose} (knockbacks {report.knockbacks})")
    assert loose > 0
