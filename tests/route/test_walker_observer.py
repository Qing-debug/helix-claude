"""Tests for the walker's optional step observer (route view plan task RV-2, contract V2; spec 5.2): the code-shape
rule, no change without (or with) an observer, event order and values against the leg records, stall and no_plan
events, the guard that drops a failing observer, and positional construction."""

import ast
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.input.input_layer import InputLayer
from src.input.link import InputError
from src.input.timing import TimingModel
from src.route import walker as walker_module
from src.route.leg_log import LegOutcome, MemorySink
from src.route.params import MovementParams
from src.route.planner import next_move
from src.route.route_graph import RouteGraph
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.world import NoiseConfig, SimParams, SimWorld, load_terrain
from src.route.walker import STALE, RouteWalker, WalkerConfig

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
TERRAIN = FIXTURES / "ff2_terrain.json"
FLOOR_MID = (82, 60)

SIM = WalkerConfig(use_stimulus=False, leg_timeout_s=120.0, max_laps=3)
EVENTS = ("leg", "read_failed", "no_plan", "reached", "stall", "planned", "sent", "settled", "leg_end")


@dataclass
class FF2:
    agent: MapleAgent
    rg: RouteGraph
    terrain: object
    rotation: list


@pytest.fixture(scope="module")
def ff2():
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(CSV))
    rg = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(rg)
    terrain = load_terrain(TERRAIN)
    assert is_successful(terrain)
    return FF2(agent, rg.unwrap(), terrain.unwrap(), list(agent.map_rotation_order))


class RecordingObserver:
    """Keeps `(name, args)` per call, in call order."""

    def __init__(self):
        self.calls = []

    def leg(self, t_ns, lap, leg, src, dst):
        self.calls.append(("leg", (t_ns, lap, leg, src, dst)))

    def read_failed(self, t_ns, why):
        self.calls.append(("read_failed", (t_ns, why)))

    def no_plan(self, t_ns, pos, why):
        self.calls.append(("no_plan", (t_ns, pos, why)))

    def reached(self, t_ns, pos):
        self.calls.append(("reached", (t_ns, pos)))

    def stall(self, t_ns, best_cost_s):
        self.calls.append(("stall", (t_ns, best_cost_s)))

    def planned(self, t_ns, pos, kind, direction, walk_ms, cost_to_go_s, toward):
        self.calls.append(("planned", (t_ns, pos, kind, direction, walk_ms, cost_to_go_s, toward)))

    def sent(self, t_ns, input_name, reaction_ms, waited_ms, keys):
        self.calls.append(("sent", (t_ns, input_name, reaction_ms, waited_ms, keys)))

    def settled(self, t_ns, pos, stopped):
        self.calls.append(("settled", (t_ns, pos, stopped)))

    def leg_end(self, t_ns, outcome, misses, off_graph, stalls):
        self.calls.append(("leg_end", (t_ns, outcome, misses, off_graph, stalls)))


class SpyLink:
    """Wraps a SimLink and records the steps of every play, in order."""

    def __init__(self, sim_link):
        self.sim = sim_link
        self.plays = []

    def play(self, steps):
        self.plays.append(tuple(steps))
        return self.sim.play(steps)

    def release_all(self):
        return self.sim.release_all()


@dataclass
class Rig:
    walker: RouteWalker
    sink: MemorySink
    link: SimLink
    spy: SpyLink
    rngs: tuple


def _rig(ff2, observer=None, *, noise=None, rg=None, config=SIM, positions=None, seed=1):
    """A sim rig as `sim/run.py:_replay_moves` builds one, with the four generators from one seed (as `_rngs`)."""
    rng_timing, rng_planner, rng_world, rng_positions = (
        np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(4))
    noise = NoiseConfig.off() if noise is None else noise
    rg = ff2.rg if rg is None else rg
    sim_params = SimParams()
    world = SimWorld(ff2.terrain, rg.reach_h, rg.reach_v, sim_params, noise, rng_world, FLOOR_MID)
    clock = SimClock()
    link = SimLink(world, clock, sim_params)
    spy = SpyLink(link)
    layer = InputLayer(spy, TimingModel(rng_timing))
    source = SimPositionSource(world, clock, noise.miss_p, rng_positions) if positions is None else positions
    sink = MemorySink()
    walker = RouteWalker(rg, ff2.rotation, layer, source, clock, sink, rng_planner, config, observer=observer)
    return Rig(walker, sink, link, spy, (rng_timing, rng_planner, rng_world, rng_positions))


def _per_leg(calls):
    """The calls split into one list per leg, from its `leg` call to its `leg_end` call."""
    legs, current = [], None
    for name, args in calls:
        if name == "leg":
            assert current is None, "a leg call before the previous leg_end"
            current = []
        assert current is not None, f"{name} outside a leg"
        current.append((name, args))
        if name == "leg_end":
            legs.append(current)
            current = None
    assert current is None, "the last leg has no leg_end"
    return legs


def _graph_positions(rg):
    return {node.minimap_position for node in rg.graph.nodes}


# ---- 1. code shape -----------------------------------------------------------------------------------------------


def _is_observer_test(node):
    """`self._observer is not None`."""
    return (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.IsNot)
            and isinstance(node.left, ast.Attribute) and node.left.attr == "_observer"
            and isinstance(node.left.value, ast.Name) and node.left.value.id == "self"
            and isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value is None)


def _is_emit(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_emit"
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "self")


def test_1_every_observer_call_is_alone_in_an_observer_branch():
    tree = ast.parse(Path(walker_module.__file__).read_text(encoding="utf-8"))
    walker_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RouteWalker")
    walk_leg = next(n for n in walker_class.body if isinstance(n, ast.FunctionDef) and n.name == "walk_leg")

    branches = [n for n in ast.walk(tree) if isinstance(n, ast.If) and _is_observer_test(n.test)]
    emits = [n for n in ast.walk(tree) if _is_emit(n)]
    assert len(branches) == 9 and len(emits) == 9
    in_branch = set()
    events = []
    for branch in branches:
        assert not branch.orelse
        assert len(branch.body) == 1, "the observer branch holds the _emit call and nothing else"
        stmt = branch.body[0]
        assert isinstance(stmt, ast.Expr) and _is_emit(stmt.value)
        in_branch.add(id(stmt.value))
        target = stmt.value.args[0]
        assert isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute)
        assert target.value.attr == "_observer"
        events.append(target.attr)
    assert sorted(events) == sorted(EVENTS)
    assert all(id(e) in in_branch for e in emits)
    branch_nodes = {id(n) for b in branches for n in ast.walk(b)}
    assert {id(b) for b in branches} <= {id(n) for n in ast.walk(walk_leg)}  # all nine are in walk_leg

    now_ns = [n for n in ast.walk(walk_leg)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "now_ns"]
    outside = [n for n in now_ns if id(n) not in branch_nodes]
    assert len(outside) == 4  # start_ns, now, read_ns and the record's end_ns: the walker's own reads
    assert len(now_ns) == 6  # plus sent's and settled's, inside their branches


# ---- 2. S2 at walker level: the observer changes nothing ---------------------------------------------------------


def _states(rngs):
    return [r.bit_generator.state for r in rngs]


@pytest.mark.parametrize("noise", [NoiseConfig(), NoiseConfig(node_offset_px=0, miss_p=0.2)], ids=["default", "miss"])
def test_2_an_observer_changes_no_record_and_no_draw(ff2, noise):
    plain = _rig(ff2, noise=noise)
    observer = RecordingObserver()
    watched = _rig(ff2, observer, noise=noise)
    assert is_successful(plain.walker.run()) and is_successful(watched.walker.run())
    assert watched.sink.legs == plain.sink.legs and watched.sink.laps == plain.sink.laps
    assert len(plain.sink.laps) == 3
    assert _states(watched.rngs) == _states(plain.rngs)
    if noise.miss_p == 0.2:
        assert any(name == "read_failed" for name, _ in observer.calls)  # the miss case does report misses


# ---- 3. event order per leg --------------------------------------------------------------------------------------


def _check_order(legs, records):
    assert len(legs) == len(records)
    for calls, record in zip(legs, records):
        names = [name for name, _ in calls]
        assert names[0] == "leg" and names[-1] == "leg_end"
        middle = names[1:-1]
        assert set(middle) <= {"read_failed", "no_plan", "stall", "reached", "planned", "sent", "settled"}
        planned_at = [i for i, name in enumerate(middle) if name == "planned"]
        for k, i in enumerate(planned_at):
            assert middle[i + 1] == "sent"
            last = k == len(planned_at) - 1
            if last and record.outcome == LegOutcome.LINK_DOWN:
                assert i + 2 == len(middle)  # no settled after a link-down send
            else:
                assert middle[i + 2] == "settled"
        assert names.count("sent") == names.count("planned") == len(record.moves)
        if record.outcome == LegOutcome.REACHED:
            assert names[-2] == "reached" and names.count("reached") == 1
        else:
            assert "reached" not in names


@pytest.mark.parametrize("noise", [NoiseConfig.off(), NoiseConfig(), NoiseConfig(miss_p=0.2)],
                         ids=["off", "default", "miss"])
def test_3_event_order_per_leg(ff2, noise):
    observer = RecordingObserver()
    rig = _rig(ff2, observer, noise=noise)
    assert is_successful(rig.walker.run())
    _check_order(_per_leg(observer.calls), rig.sink.legs)


def test_3_a_link_down_leg_has_no_settled_after_its_last_send(ff2):
    observer = RecordingObserver()
    rig = _rig(ff2, observer)
    rig.link.fail_next.append(InputError.LINK_LOST)
    ran = rig.walker.run()
    assert ran == Success(walker_module.RunSummary(0, 1, "link_down"))
    assert rig.sink.legs[0].outcome == LegOutcome.LINK_DOWN
    _check_order(_per_leg(observer.calls), rig.sink.legs)
    assert [name for name, _ in observer.calls] == ["leg", "planned", "sent", "leg_end"]
    assert observer.calls[2][1][1:] == ("LINK_LOST", None, None, ())


class StopOnSecondRead:
    """Wraps a position source; its second read (the settle's first poll) calls `stop_now` on the walker."""

    def __init__(self, source, walker):
        self.source = source
        self.walker = walker
        self.reads = 0

    def read(self):
        self.reads += 1
        if self.reads == 2:
            self.walker.stop_now()
        return self.source.read()


def test_3_a_stop_in_the_settle_gives_a_settled_with_stopped_true(ff2):
    observer = RecordingObserver()
    rig = _rig(ff2, observer)
    rig.walker._positions = StopOnSecondRead(rig.walker._positions, rig.walker)
    assert rig.walker.run() == Success(walker_module.RunSummary(0, 1, "stopped"))
    assert rig.sink.legs[0].outcome == LegOutcome.STOPPED
    _check_order(_per_leg(observer.calls), rig.sink.legs)
    assert [name for name, _ in observer.calls] == ["leg", "planned", "sent", "settled", "leg_end"]
    assert observer.calls[3][1][1:] == (rig.sink.legs[0].moves[0].settled, True)


# ---- 4. values -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("noise", [NoiseConfig(), NoiseConfig(miss_p=0.2)], ids=["default", "miss"])
def test_4_values_match_the_leg_records_and_the_played_steps(ff2, noise):
    observer = RecordingObserver()
    rig = _rig(ff2, observer, noise=noise)
    assert is_successful(rig.walker.run())
    nodes = _graph_positions(ff2.rg)
    plays = iter(rig.spy.plays)
    for calls, record in zip(_per_leg(observer.calls), rig.sink.legs, strict=True):
        assert calls[0][1] == (record.start_ns, record.lap, record.leg, record.src, record.dst)
        assert calls[-1][1] == (record.end_ns, record.outcome.value, record.misses, record.off_graph, record.stalls)
        planned = [args for name, args in calls if name == "planned"]
        sent = [args for name, args in calls if name == "sent"]
        settled = [args for name, args in calls if name == "settled"]
        for p, s, entry in zip(planned, sent, record.moves, strict=True):
            assert p[:6] == (entry.t_ns, entry.pos, entry.kind, entry.direction, entry.walk_ms, entry.cost_to_go_s)
            assert p[6] in nodes
            assert s[1] == entry.input == "ok"
            steps = next(plays)
            assert s[2:] == (None, 0.0, tuple((step.delta_100us, int(step.mask)) for step in steps))
            assert all(type(v) is int for pair in s[4] for v in pair)
        for st, entry in zip(settled, record.moves, strict=True):
            assert st[1] == entry.settled and st[2] is False
        misses = [args for name, args in calls if name == "read_failed" and args[1] != STALE]
        assert len(misses) == record.misses
        assert all(args[1] == "miss" for args in misses)
        assert sum(name == "no_plan" for name, _ in calls) == record.off_graph
        assert sum(name == "stall" for name, _ in calls) == record.stalls
    assert next(plays, None) is None


# ---- 5. stall -----------------------------------------------------------------------------------------------------


def _stall_rg(ff2):
    built = RouteGraph.build(ff2.agent.pathGraph, MovementParams(stall_s=1.0))
    assert is_successful(built)
    return built.unwrap()


def test_5_a_stall_carries_the_walkers_best_cost(ff2):
    rg = _stall_rg(ff2)
    observer = RecordingObserver()
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=5.0)
    rig = _rig(ff2, observer, noise=NoiseConfig(no_op_p=1.0, overshoot_p=0.0, landing_p=0.0, miss_p=0.0), rg=rg,
               config=config)
    record = rig.walker.walk_leg(0, 0, None, ff2.rotation[0])
    assert record.outcome == LegOutcome.TIMED_OUT and record.stalls >= 2
    best = math.inf
    stalls = []
    for name, args in observer.calls:
        if name == "stall":
            stalls.append(args[1])
            assert args[1] == best
        elif name == "planned" and args[5] < best - rg.params.progress_eps_s:
            best = args[5]
    assert len(stalls) == record.stalls and all(math.isfinite(b) for b in stalls)


class AlwaysMiss:
    def read(self):
        return Failure("miss")


def test_5_a_stall_before_the_first_plan_is_inf(ff2):
    observer = RecordingObserver()
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=2.5)
    rig = _rig(ff2, observer, rg=_stall_rg(ff2), config=config, positions=AlwaysMiss())
    record = rig.walker.walk_leg(0, 0, None, ff2.rotation[0])
    stalls = [args for name, args in observer.calls if name == "stall"]
    assert record.stalls == len(stalls) == 2
    assert all(args[1] == math.inf for args in stalls)
    assert "planned" not in [name for name, _ in observer.calls]


# ---- 6. no_plan ---------------------------------------------------------------------------------------------------


class Fixed:
    def __init__(self, pos):
        self.pos = pos

    def read(self):
        return Success(self.pos)


def test_6_an_off_graph_position_gives_no_plan_with_the_planners_message(ff2):
    off = (0, 0)
    expected = next_move(ff2.rg, off, ff2.rotation[0], np.random.default_rng(0))
    assert not is_successful(expected)
    observer = RecordingObserver()
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=0.5)
    rig = _rig(ff2, observer, config=config, positions=Fixed(off))
    record = rig.walker.walk_leg(0, 0, None, ff2.rotation[0])
    no_plan = [args for name, args in observer.calls if name == "no_plan"]
    assert record.off_graph == len(no_plan) > 0
    assert all(args[1:] == (off, expected.failure()) for args in no_plan)


# ---- 7. guard -----------------------------------------------------------------------------------------------------


class BoomOnPlanned(RecordingObserver):
    def planned(self, t_ns, pos, kind, direction, walk_ms, cost_to_go_s, toward):
        super().planned(t_ns, pos, kind, direction, walk_ms, cost_to_go_s, toward)
        raise RuntimeError("boom")


def test_7_a_failing_observer_is_reported_once_and_never_called_again(ff2, capsys):
    plain = _rig(ff2)
    assert is_successful(plain.walker.run())
    capsys.readouterr()
    observer = BoomOnPlanned()
    rig = _rig(ff2, observer)
    assert is_successful(rig.walker.run())
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1
    assert "route view observer failed" in err[0] and "boom" in err[0] and "RuntimeError" in err[0]
    assert [name for name, _ in observer.calls] == ["leg", "planned"]
    assert rig.sink.legs == plain.sink.legs and rig.sink.laps == plain.sink.laps


# ---- 8. construction -----------------------------------------------------------------------------------------------


def test_8_positional_construction_still_works_and_the_observer_is_keyword_only(ff2):
    rig = _rig(ff2)
    w = rig.walker
    args = (ff2.rg, ff2.rotation, w._layer, w._positions, w._clock, MemorySink(), np.random.default_rng(0), SIM)
    plain = RouteWalker(*args)
    assert is_successful(plain.run())
    with pytest.raises(TypeError):
        RouteWalker(*args, RecordingObserver())  # type: ignore[misc]
