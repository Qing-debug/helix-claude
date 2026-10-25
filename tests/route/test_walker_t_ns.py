"""Pins decision B12-D1 (docs/decisions.md): `MoveEntry.t_ns` is the clock reading taken right after the position read
the move was planned from, and with `use_stimulus` on it is the very value passed to the input layer as `stimulus_ns`.

Feature D reads send time minus `t_ns` as the reaction time. `test_walker_leg.py` (frozen) checks the layer's
`stimulus_ns` only; this file reads `t_ns` itself. The clock here moves on at every reading and the fake layer's call
takes time, so a `t_ns` taken anywhere else (before the read, after the call, from a second reading) differs.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Success

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.route.leg_log import LegOutcome, MemorySink
from src.route.params import MovementParams
from src.route.route_graph import RouteGraph
from src.route.sim.link import SimClock, SimPositionSource
from src.route.sim.world import NoiseConfig, SimParams, SimWorld, load_terrain
from src.route.walker import RouteWalker, WalkerConfig

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
TERRAIN = FIXTURES / "ff2_terrain.json"

FLOOR_MID = (82, 60)
FLOOR_LEFT = (48, 61)


class TickClock(SimClock):
    """A SimClock whose every reading moves time on by 1 us, so no two readings are equal."""

    def now_ns(self) -> int:
        now = super().now_ns()
        self.advance(1_000)
        return now

    def peek(self) -> int:
        """The reading the next `now_ns` returns, without taking it."""
        return SimClock.now_ns(self)


class RecordingSource:
    """Advances the clock 1 ms per read and records the reading the walker takes right after it."""

    def __init__(self, clock: TickClock, inner: SimPositionSource) -> None:
        self._clock = clock
        self._inner = inner
        self.read_ns: list[int] = []

    def read(self):
        self._clock.advance(1_000_000)
        result = self._inner.read()
        self.read_ns.append(self._clock.peek())  # what the walker's next `now_ns()` returns
        return result


class TimedLayer:
    """Stands in for InputLayer: records (stimulus_ns, reads made so far) per call, takes 5 ms, never moves the world."""

    def __init__(self, clock: TickClock, source: RecordingSource) -> None:
        self._clock = clock
        self._source = source
        self.calls: list[tuple[int | None, int]] = []

    def _call(self, stimulus_ns):
        self.calls.append((stimulus_ns, len(self._source.read_ns)))
        self._clock.advance(5_000_000)
        return Success(None)

    def walk(self, direction, intended_ms, stimulus_ns=None):
        return self._call(stimulus_ns)

    def teleport(self, direction, stimulus_ns=None):
        return self._call(stimulus_ns)

    def drop(self, stimulus_ns=None):
        return self._call(stimulus_ns)


@dataclass
class Rig:
    walker: RouteWalker
    layer: TimedLayer
    source: RecordingSource
    nodes: dict


@pytest.fixture(scope="module")
def ff2():
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(CSV))
    rg = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(rg)
    terrain = load_terrain(TERRAIN)
    assert is_successful(terrain)
    nodes = {n.minimap_position: n for n in agent.node_map.values()}
    return rg.unwrap(), terrain.unwrap(), list(agent.map_rotation_order), nodes


def _rig(ff2, use_stimulus: bool) -> Rig:
    rg, terrain, rotation, nodes = ff2
    world = SimWorld(terrain, rg.reach_h, rg.reach_v, SimParams(), NoiseConfig.off(), np.random.default_rng(1),
                     FLOOR_MID)
    clock = TickClock()
    source = RecordingSource(clock, SimPositionSource(world, clock, miss_p=0, rng=np.random.default_rng(2)))
    layer = TimedLayer(clock, source)
    config = WalkerConfig(use_stimulus=use_stimulus, leg_timeout_s=2.0)
    walker = RouteWalker(rg, rotation, layer, source, clock, MemorySink(), np.random.default_rng(0), config)
    return Rig(walker, layer, source, nodes)


def _walk_the_leg(rig: Rig):
    rec = rig.walker.walk_leg(1, 0, rig.nodes[FLOOR_MID], rig.nodes[FLOOR_LEFT])
    # the layer never moves the world, so the leg times out after several moves
    assert rec.outcome == LegOutcome.TIMED_OUT
    assert len(rec.moves) >= 2
    assert len(rec.moves) == len(rig.layer.calls)
    return rec


def test_t_ns_is_the_stimulus_the_layer_was_given(ff2):
    rig = _rig(ff2, use_stimulus=True)

    rec = _walk_the_leg(rig)

    assert [m.t_ns for m in rec.moves] == [stimulus_ns for stimulus_ns, _ in rig.layer.calls]


def test_t_ns_is_the_clock_right_after_the_read_the_move_was_planned_from(ff2):
    rig = _rig(ff2, use_stimulus=True)

    rec = _walk_the_leg(rig)

    assert [m.t_ns for m in rec.moves] == [rig.source.read_ns[reads_made - 1] for _, reads_made in rig.layer.calls]


def test_t_ns_is_still_the_read_time_when_no_stimulus_is_passed(ff2):
    rig = _rig(ff2, use_stimulus=False)

    rec = _walk_the_leg(rig)

    assert all(stimulus_ns is None for stimulus_ns, _ in rig.layer.calls)
    assert [m.t_ns for m in rec.moves] == [rig.source.read_ns[reads_made - 1] for _, reads_made in rig.layer.calls]
