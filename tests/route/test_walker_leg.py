"""Tests for src.route.walker.RouteWalker.walk_leg (plan task B12): the one-leg control loop on the FF2 simulator.

The first seven tests are the plan's; the rest pin the stop-flag checks (loop steps 1, 7 and 8), the off-graph
counter and the settle give-up. `RouteWalker._stop` is the private stop flag B13's `stop_now` will set; B12 only
reads it, so these tests set it directly.
"""

import time
from dataclasses import dataclass, replace
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
from src.route.leg_log import LegOutcome, MemorySink
from src.route.params import MovementParams
from src.route.route_graph import RouteGraph
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.world import NoiseConfig, SimParams, SimWorld, load_terrain
from src.route.walker import STALE, RealClock, RouteWalker, WalkerConfig

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
TERRAIN = FIXTURES / "ff2_terrain.json"

TOP_LEFT = (49, 28)
TOP_RIGHT = (101, 24)
MID_RIGHT = (105, 42)
FLOOR_MID = (82, 60)
FLOOR_LEFT = (48, 61)


@dataclass
class FF2:
    rg: RouteGraph
    terrain: object
    rotation: list
    nodes: dict  # minimap position -> Node

    def node(self, xy):
        return self.nodes[xy]


@pytest.fixture(scope="module")
def ff2():
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(CSV))
    rg = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(rg)
    terrain = load_terrain(TERRAIN)
    assert is_successful(terrain)
    nodes = {n.minimap_position: n for n in agent.node_map.values()}
    return FF2(rg.unwrap(), terrain.unwrap(), list(agent.map_rotation_order), nodes)


@dataclass
class Rig:
    walker: RouteWalker
    world: SimWorld
    clock: SimClock
    link: SimLink
    sink: MemorySink


def _world(ff2, start, noise=None):
    noise = NoiseConfig.off() if noise is None else noise
    return SimWorld(ff2.terrain, ff2.rg.reach_h, ff2.rg.reach_v, SimParams(), noise, np.random.default_rng(1), start)


def _walker(ff2, layer, positions, clock, config=None, sink=None):
    config = WalkerConfig(use_stimulus=False) if config is None else config
    sink = MemorySink() if sink is None else sink
    return RouteWalker(ff2.rg, ff2.rotation, layer, positions, clock, sink, np.random.default_rng(0), config)


def _rig(ff2, start, noise=None, config=None, positions=None):
    """The plan's standard rig: SimWorld + SimLink + SimPositionSource (miss_p 0) under one SimClock, a real
    InputLayer, a MemorySink. `positions(clock)` builds a replacement position source."""
    params = SimParams()
    world = _world(ff2, start, noise)
    clock = SimClock()
    link = SimLink(world, clock, params)
    if positions is None:
        positions = SimPositionSource(world, clock, miss_p=0, rng=np.random.default_rng(2))
    else:
        positions = positions(clock)
    layer = InputLayer(link, TimingModel(np.random.default_rng(3)))
    sink = MemorySink()
    return Rig(_walker(ff2, layer, positions, clock, config, sink), world, clock, link, sink)


class FnSource:
    """A position source whose n-th read (0-based) returns fn(n)."""

    def __init__(self, fn):
        self.fn = fn
        self.reads = 0

    def read(self):
        result = self.fn(self.reads)
        self.reads += 1
        return result


class FakeLayer:
    """Stands in for InputLayer: records each call as (kind, stimulus_ns), returns Success(None) and never moves
    the world. `on_call(kind, stimulus_ns)` runs inside every call."""

    def __init__(self, on_call=None):
        self.calls = []
        self.on_call = on_call

    def _call(self, kind, stimulus_ns):
        self.calls.append((kind, stimulus_ns))
        if self.on_call is not None:
            self.on_call(kind, stimulus_ns)
        return Success(None)

    def walk(self, direction, intended_ms, stimulus_ns=None):
        return self._call("walk", stimulus_ns)

    def teleport(self, direction, stimulus_ns=None):
        return self._call("teleport", stimulus_ns)

    def drop(self, stimulus_ns=None):
        return self._call("drop", stimulus_ns)


def _leg(rig, ff2, src_xy, dst_xy, lap=1, leg=0):
    return rig.walker.walk_leg(lap, leg, ff2.node(src_xy), ff2.node(dst_xy))


# --- the plan's tests -------------------------------------------------------------------------------------


def test_1_slope_leg_walks_right_across_the_slope(ff2):
    rig = _rig(ff2, TOP_LEFT)

    rec = _leg(rig, ff2, TOP_LEFT, TOP_RIGHT)

    assert rec.outcome == LegOutcome.REACHED
    assert len(rec.moves) <= 8
    assert any(m.kind == "walk" and m.direction == "RIGHT" and 66 <= m.pos[0] <= 79 for m in rec.moves), rec.moves


def test_2_teleport_down_then_walk_reaches_within_two_px(ff2):
    rig = _rig(ff2, TOP_RIGHT)

    rec = _leg(rig, ff2, TOP_RIGHT, MID_RIGHT)

    assert rec.outcome == LegOutcome.REACHED
    assert len(rec.moves) <= 3
    sx, sy = rec.moves[-1].settled
    assert abs(sx - MID_RIGHT[0]) <= 2 and abs(sy - MID_RIGHT[1]) <= 2
    wx, wy = rig.world.pos
    assert abs(wx - MID_RIGHT[0]) <= 2 and abs(wy - MID_RIGHT[1]) <= 2


@pytest.mark.parametrize("i", range(7))
def test_3_every_rotation_leg_is_reached_with_noise_off(ff2, i):
    src, dst = ff2.rotation[i], ff2.rotation[(i + 1) % len(ff2.rotation)]
    rig = _rig(ff2, src.minimap_position)

    rec = rig.walker.walk_leg(1, i, src, dst)

    assert rec.outcome == LegOutcome.REACHED, rec
    assert (rec.stalls, rec.off_graph) == (0, 0)


def test_4_a_link_that_never_moves_stalls_every_ten_seconds_until_the_timeout(ff2):
    rig = _rig(ff2, FLOOR_MID, noise=replace(NoiseConfig.off(), no_op_p=1),
               config=WalkerConfig(use_stimulus=False, leg_timeout_s=25))

    rec = _leg(rig, ff2, FLOOR_MID, FLOOR_LEFT)

    assert rec.outcome == LegOutcome.TIMED_OUT
    assert rec.stalls == 2
    assert rec.moves and all(m.input == "ok" for m in rec.moves)


def test_5a_a_failed_play_is_logged_and_the_leg_is_still_reached(ff2):
    rig = _rig(ff2, FLOOR_MID)
    rig.link.fail_next = [InputError.ACK_TIMEOUT]

    rec = _leg(rig, ff2, FLOOR_MID, FLOOR_LEFT)

    assert rec.outcome == LegOutcome.REACHED
    assert rec.moves[0].input == "ACK_TIMEOUT"
    assert all(m.input == "ok" for m in rec.moves[1:])


def test_5b_link_lost_ends_the_leg_after_one_entry_and_no_further_play(ff2):
    rig = _rig(ff2, FLOOR_MID)
    rig.link.fail_next = [InputError.LINK_LOST]

    rec = _leg(rig, ff2, FLOOR_MID, FLOOR_LEFT)

    assert rec.outcome == LegOutcome.LINK_DOWN
    assert len(rec.moves) == 1
    assert rec.moves[0].input == "LINK_LOST"
    assert rec.moves[0].settled is None
    assert rig.link.plays == 1


def test_5c_not_connected_also_ends_the_leg_as_link_down(ff2):
    rig = _rig(ff2, FLOOR_MID)
    rig.link.fail_next = [InputError.NOT_CONNECTED]

    rec = _leg(rig, ff2, FLOOR_MID, FLOOR_LEFT)

    assert rec.outcome == LegOutcome.LINK_DOWN
    assert [m.input for m in rec.moves] == ["NOT_CONNECTED"]
    assert rig.link.plays == 1


def test_6_a_miss_and_a_stale_frame_are_skipped_and_only_the_miss_is_counted(ff2):
    target = ff2.node(FLOOR_LEFT)
    script = [Failure("miss"), Failure(STALE), Success(target.minimap_position)]
    rig = _rig(ff2, FLOOR_MID, positions=lambda clock: FnSource(lambda n: script[n]))

    rec = rig.walker.walk_leg(1, 0, ff2.node(FLOOR_MID), target)

    assert rec.outcome == LegOutcome.REACHED
    assert rec.misses == 1
    assert rec.moves == ()
    assert rig.link.plays == 0


def test_7_the_stimulus_is_the_clock_right_after_the_read(ff2):
    clock = SimClock()
    inner = SimPositionSource(_world(ff2, FLOOR_MID), clock, miss_p=0, rng=np.random.default_rng(2))
    read_ns = []

    class AdvancingSource:
        def read(self):
            clock.advance(1_000_000)
            read_ns.append(clock.now_ns())
            return inner.read()

    seen = []  # (stimulus_ns, number of reads made when the layer was called)
    layer = FakeLayer(on_call=lambda kind, stimulus_ns: seen.append((stimulus_ns, len(read_ns))))
    walker = _walker(ff2, layer, AdvancingSource(), clock, WalkerConfig(use_stimulus=True, leg_timeout_s=2.0))

    rec = walker.walk_leg(1, 0, ff2.node(FLOOR_MID), ff2.node(FLOOR_LEFT))

    assert rec.outcome == LegOutcome.TIMED_OUT
    assert len(layer.calls) >= 2
    for stimulus_ns, reads_made in seen:
        assert stimulus_ns == read_ns[reads_made - 1]


def test_7_without_use_stimulus_every_call_gets_none(ff2):
    clock = SimClock()
    source = SimPositionSource(_world(ff2, FLOOR_MID), clock, miss_p=0, rng=np.random.default_rng(2))
    layer = FakeLayer()
    walker = _walker(ff2, layer, source, clock, WalkerConfig(use_stimulus=False, leg_timeout_s=2.0))

    rec = walker.walk_leg(1, 0, ff2.node(FLOOR_MID), ff2.node(FLOOR_LEFT))

    assert rec.outcome == LegOutcome.TIMED_OUT
    assert len(layer.calls) >= 2
    assert all(stimulus_ns is None for _, stimulus_ns in layer.calls)


# --- record fields ----------------------------------------------------------------------------------------


def test_the_record_carries_lap_leg_positions_and_times(ff2):
    rig = _rig(ff2, FLOOR_MID)
    start = rig.clock.now_ns()

    rec = rig.walker.walk_leg(3, 2, ff2.node(FLOOR_MID), ff2.node(FLOOR_LEFT))

    assert (rec.lap, rec.leg) == (3, 2)
    assert rec.src == FLOOR_MID and rec.dst == FLOOR_LEFT
    assert rec.start_ns == start
    assert rec.end_ns == rig.clock.now_ns()
    times = [m.t_ns for m in rec.moves]
    assert rec.start_ns <= times[0] and times == sorted(set(times)) and times[-1] <= rec.end_ns


def test_the_approach_leg_has_no_src(ff2):
    rig = _rig(ff2, FLOOR_MID)

    rec = rig.walker.walk_leg(0, 0, None, ff2.node(FLOOR_LEFT))

    assert rec.src is None
    assert rec.dst == FLOOR_LEFT
    assert rec.outcome == LegOutcome.REACHED


def test_a_move_entry_holds_plain_values_the_leg_log_accepts(ff2):
    rig = _rig(ff2, TOP_LEFT)

    rec = _leg(rig, ff2, TOP_LEFT, TOP_RIGHT)

    assert rec.moves[0].pos == TOP_LEFT
    for m in rec.moves:
        assert type(m.kind) is str and m.kind in ("walk", "teleport", "drop")
        assert (m.direction is None) == (m.kind == "drop")
        assert m.direction is None or (type(m.direction) is str and m.direction.isupper())
        assert (m.walk_ms is not None) == (m.kind == "walk")
        assert type(m.cost_to_go_s) is float and m.cost_to_go_s > 0
        assert type(m.input) is str
    assert is_successful(MemorySink().write_leg(rec))


def test_walk_leg_returns_the_record_and_does_not_write_it(ff2):
    rig = _rig(ff2, FLOOR_MID)

    _leg(rig, ff2, FLOOR_MID, FLOOR_LEFT)

    assert rig.sink.legs == [] and rig.sink.laps == []  # B13's run() writes the records


# --- stop flag (loop steps 1, 7 and 8) --------------------------------------------------------------------


def test_stop_flag_set_before_the_leg_is_stopped_and_nothing_is_read_or_sent(ff2):
    source = FnSource(lambda n: Success(FLOOR_MID))
    rig = _rig(ff2, FLOOR_MID, positions=lambda clock: source)
    rig.walker._stop.set()

    rec = _leg(rig, ff2, FLOOR_MID, FLOOR_LEFT)

    assert rec.outcome == LegOutcome.STOPPED
    assert rec.moves == ()
    assert source.reads == 0
    assert rig.link.plays == 0


def test_stop_flag_set_during_the_read_sends_nothing(ff2):
    def read(n):
        rig.walker._stop.set()
        return Success(FLOOR_MID)

    rig = _rig(ff2, FLOOR_MID, positions=lambda clock: FnSource(read))

    rec = _leg(rig, ff2, FLOOR_MID, FLOOR_LEFT)

    assert rec.outcome == LegOutcome.STOPPED
    assert rec.moves == ()
    assert rig.link.plays == 0


def test_stop_flag_set_while_a_move_plays_records_the_entry_and_stops_before_the_first_poll(ff2):
    source = FnSource(lambda n: Success(FLOOR_MID))
    layer = FakeLayer(on_call=lambda kind, stimulus_ns: walker._stop.set())
    walker = _walker(ff2, layer, source, SimClock())

    rec = walker.walk_leg(1, 0, ff2.node(FLOOR_MID), ff2.node(FLOOR_LEFT))

    assert rec.outcome == LegOutcome.STOPPED
    assert len(rec.moves) == 1 and rec.moves[0].input == "ok" and rec.moves[0].settled is None
    assert len(layer.calls) == 1
    assert source.reads == 1  # the decision read; no settle poll


# --- the other counters and the settle --------------------------------------------------------------------


def test_an_off_graph_position_counts_off_graph_and_is_polled_again(ff2):
    target = ff2.node(FLOOR_LEFT)
    script = [Success((5, 5)), Success((5, 5)), Success(target.minimap_position)]
    rig = _rig(ff2, FLOOR_MID, positions=lambda clock: FnSource(lambda n: script[n]))

    rec = rig.walker.walk_leg(1, 0, ff2.node(FLOOR_MID), target)

    assert rec.outcome == LegOutcome.REACHED
    assert rec.off_graph == 2
    assert rec.misses == 0
    assert rec.moves == ()
    assert rig.link.plays == 0


def test_settle_gives_up_after_settle_timeout_with_the_last_successful_read(ff2):
    clock = SimClock()
    read_times = []

    def read(n):
        read_times.append(clock.now_ns())
        # the decision read sees FLOOR_MID; every later read sees a different y (off the graph), so no two are equal
        return Success(FLOOR_MID if n == 0 else (FLOOR_MID[0], 100 + n))

    layer = FakeLayer()
    walker = _walker(ff2, layer, FnSource(read), clock, WalkerConfig(use_stimulus=False, leg_timeout_s=3.0))

    rec = walker.walk_leg(1, 0, ff2.node(FLOOR_MID), ff2.node(FLOOR_LEFT))

    assert len(layer.calls) == 1
    settled = rec.moves[0].settled
    assert settled is not None
    last = settled[1] - 100  # index of the last read the settle made
    timeout_ns = MovementParams().settle_timeout_s * 1e9
    assert read_times[last] - read_times[0] < timeout_ns <= read_times[last + 1] - read_times[0]


def test_settle_is_none_when_every_read_after_the_move_fails(ff2):
    source = FnSource(lambda n: Success(FLOOR_MID) if n == 0 else Failure("miss"))
    walker = _walker(ff2, FakeLayer(), source, SimClock(), WalkerConfig(use_stimulus=False, leg_timeout_s=3.0))

    rec = walker.walk_leg(1, 0, ff2.node(FLOOR_MID), ff2.node(FLOOR_LEFT))

    assert rec.outcome == LegOutcome.TIMED_OUT
    assert len(rec.moves) == 1
    assert rec.moves[0].settled is None


def test_settle_waits_min_settle_then_polls_every_poll_s_until_two_reads_agree(ff2):
    clock = SimClock()
    read_times = []

    def read(n):
        read_times.append(clock.now_ns())
        return Success(FLOOR_MID)

    walker = _walker(ff2, FakeLayer(), FnSource(read), clock, WalkerConfig(use_stimulus=False, leg_timeout_s=1.0))

    rec = walker.walk_leg(1, 0, ff2.node(FLOOR_MID), ff2.node(FLOOR_LEFT))

    p = MovementParams()
    # read 0 is the decision read, read 1 the first settle read (min_settle_s later), read 2 the second (poll_s later)
    assert read_times[1] - read_times[0] == round(p.min_settle_s * 1e9)
    assert read_times[2] - read_times[1] == round(p.poll_s * 1e9)
    assert rec.moves[0].settled == FLOOR_MID


# --- the clock and the config ------------------------------------------------------------------------------


def test_real_clock_reads_perf_counter_ns_and_sleeps_with_time_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    clock = RealClock()

    before = time.perf_counter_ns()
    now = clock.now_ns()
    after = time.perf_counter_ns()
    clock.sleep(0.25)

    assert type(now) is int and before <= now <= after
    assert slept == [0.25]


def test_walker_config_defaults_match_contract_c10():
    config = WalkerConfig()

    assert (config.use_stimulus, config.leg_timeout_s, config.max_laps) == (True, None, None)
