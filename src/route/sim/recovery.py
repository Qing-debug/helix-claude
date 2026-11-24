"""The simulator's recovery metric (split out of `sim/run.py`, CLEAN222-W1-2; `run_sim` calls `_recovery`).

Every leg whose world effects carry an injected error is replayed in a fresh noise-off world from the position the leg
began at (same terrain, same route graph, offsets included), and `extra = moves - nominal`, where nominal is the
replay's move count. A leg may take 3 extra moves per error, and for each error that is a no-op sent from a read inside
a rope zone the whole rope order too, `rope_blink_tries` blinks and the one jump off: the walker takes that order after
such a no-op, also on a read the map places (H93, ROPE158-C10B-D1), so the no-op costs the move that did nothing plus
the order, and the leg's bound is 3 + `rope_blink_tries` + 1 for it (`_extra_allowed`).

Where the plan is silent:
- A leg's injected errors are counted one per error, not one per move: a move with both an overshoot and a landing
  offset carries two.
- "Seeded from the leg's number": a replay's rngs are `SeedSequence(i).spawn(4)` (timing, planner, world, positions,
  as the run's, `_rngs`), where i is the leg's index in the run's leg log (0 is the approach leg). Every leg is replayed
  whatever its outcome, the approach leg included.

`_rngs`, `_LEG_TIMEOUT_S` and `_MarkingSink` are shared with `run_sim`, which imports them from here. Tests patch
`_replay_moves` and `RouteWalker` here, where `_recovery` and `_replay_moves` look them up (tests/CLAUDE.md).
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from returns.pipeline import is_successful
from returns.result import Result

from src.controller.node import Node
from src.controller.rope_file import RopeMark
from src.input.input_layer import InputLayer
from src.input.timing import TimingModel
from src.route.leg_log import LapRecord, LegRecord, LegSink, MemorySink
from src.route.locate import attach
from src.route.ropes import RopeZones
from src.route.route_graph import RouteGraph
from src.route.sim.link import SimClock, SimLink, SimPositionSource, read_of
from src.route.sim.world import InjectedError, MoveEffect, NoiseConfig, SimParams, SimTerrain, SimWorld
from src.route.walker import RouteWalker, WalkerConfig

_LEG_TIMEOUT_S = 60.0
_EXTRA_PER_ERROR = 3          # each injected error is allowed this many extra moves (a no-op that starts the rope order: and its moves)


class _MarkingSink:
    """Passes every record on to `inner`. At each `write_leg` it also keeps the record, `len(world.effects)` and
    `world.pos`: the effects between two consecutive leg writes belong to that leg, and the walker starts the next
    leg right after the write, so the position kept is where the next leg begins."""

    def __init__(self, inner: LegSink, world: SimWorld) -> None:
        self._inner = inner
        self._world = world
        self.legs: list[LegRecord] = []
        self.laps: list[LapRecord] = []
        self.marks: list[int] = []
        self.ends: list[tuple[float, float]] = []

    def write_leg(self, rec: LegRecord) -> Result[None, str]:
        self.legs.append(rec)
        self.marks.append(len(self._world.effects))
        self.ends.append(self._world.pos)
        return self._inner.write_leg(rec)

    def write_lap(self, rec: LapRecord) -> Result[None, str]:
        self.laps.append(rec)
        return self._inner.write_lap(rec)


def _rngs(seed: int) -> tuple[np.random.Generator, ...]:
    """Four independent generators: timing, planner, world, positions."""
    return tuple(np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(4))


def _leg_nodes(rotation: Sequence[Node], rec: LegRecord) -> tuple[Node | None, Node]:
    """The (src, dst) nodes of a leg record: lap 0 is the approach to rotation[0], leg i of a lap is rotation[i] ->
    rotation[(i + 1) mod n]."""
    if rec.lap == 0:
        return None, rotation[0]
    n = len(rotation)
    return rotation[rec.leg], rotation[(rec.leg + 1) % n]


def _replay_moves(rg: RouteGraph, rotation: Sequence[Node], terrain: SimTerrain, sim_params: SimParams,
                  rec: LegRecord, number: int, start: tuple[float, float], ropes: Sequence[RopeMark]) -> int | None:
    """The moves the leg takes in a fresh noise-off world from `start` (the nominal count); None when `start` is on no
    platform surface, which is where the previous leg left the character hanging on a rope: a world cannot start there,
    so the leg has no replay (ROPE R6 m2)."""
    rng_timing, rng_planner, rng_world, rng_positions = _rngs(number)
    off = NoiseConfig.off()
    try:
        world = SimWorld(terrain, rg.reach_h, rg.reach_v, sim_params, off, rng_world, start, ropes)
    except ValueError:    # the only one the constructor raises: start is not on a platform surface
        return None
    clock = SimClock()
    layer = InputLayer(SimLink(world, clock, sim_params), TimingModel(rng_timing))
    positions = SimPositionSource(world, clock, off.miss_p, rng_positions)
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=_LEG_TIMEOUT_S)
    walker = RouteWalker(rg, rotation, layer, positions, clock, MemorySink(), rng_planner, config,
                         ropes=RopeZones.build(ropes, rg.params))
    src, dst = _leg_nodes(rotation, rec)
    return len(walker.walk_leg(rec.lap, rec.leg, src, dst).moves)


def _rope_order_moves(effects: Sequence[MoveEffect], zones: RopeZones, rg: RouteGraph, sim_params: SimParams) -> int:
    """The most moves the walker's rope order can take after the no-ops in `effects` that start it (H93, ROPE158-C10B,
    ROPE159-C10B-F1 M1). A no-op starts the order when the read before the move, `read_of` the effect's start, is in a rope
    zone and the map places it (`attach`): the planner sent the move and the read stayed the same. A read the map does not
    place is the character on the rope, where the order runs anyway and the no-op was one of its own moves: it starts
    nothing. The order from a read is `rope_blink_tries` blinks and the jump off when the nearer end of the mark is within
    `reach_v` of it, the jump off alone when it is not (`planner.rope_move`'s rule; Hong: with no platform close up or down
    a blink does not move the character). The effect does not say which move it was, so a no-op on a rope move at a placed
    read counts as a start too, and the first blink that works is not credited: an upper bound."""
    total = 0
    for e in effects:
        if InjectedError.NO_OP not in e.injected:
            continue
        read = read_of(e.start, sim_params)
        zone = zones.at(read)
        if zone is None or not is_successful(attach(rg, read)):
            continue
        mark = zones.mark(zone)
        gap = min(read[1] - mark.top_y, mark.bottom_y - read[1])
        total += rg.params.rope_blink_tries + 1 if gap <= rg.reach_v else 1
    return total


def _extra_allowed(errors: int, order_moves: int) -> int:
    """The extra moves a leg with `errors` injected errors may take and still count as recovered: `_EXTRA_PER_ERROR` for
    each, and `order_moves` more for the rope order that the no-ops among them start (`_rope_order_moves`; ROPE158-C10B-D1,
    option B, tightened by ROPE159-C10B-F1 M1). A leg whose one error is a no-op that starts the full order (a platform end
    in reach) may take 3 + `blink_tries` + 1; with no platform in reach 3 + 1; a no-op that starts none is held to 3."""
    return _EXTRA_PER_ERROR * errors + order_moves


@dataclass(frozen=True)
class _Recovery:
    single: int = 0
    single_within: int = 0
    multi: int = 0
    multi_within: int = 0


def _recovery(rg: RouteGraph, rotation: Sequence[Node], terrain: SimTerrain, sim_params: SimParams,
              sink: _MarkingSink, effects: Sequence[MoveEffect], start: tuple[float, float],
              ropes: Sequence[RopeMark]) -> _Recovery:
    single = single_within = multi = multi_within = 0
    zones = RopeZones.build(ropes, rg.params)
    for i, rec in enumerate(sink.legs):
        first = sink.marks[i - 1] if i else 0
        errors = sum(len(e.injected) for e in effects[first:sink.marks[i]])
        if errors == 0:
            continue
        allowed = _extra_allowed(errors, _rope_order_moves(effects[first:sink.marks[i]], zones, rg, sim_params))
        began = sink.ends[i - 1] if i else start
        nominal = _replay_moves(rg, rotation, terrain, sim_params, rec, i, began, ropes)
        # a leg with no replay counts as a leg with errors that did not recover
        extra = None if nominal is None else len(rec.moves) - nominal
        if errors == 1:
            single += 1
            single_within += extra is not None and extra <= allowed
        else:
            multi += 1
            multi_within += extra is not None and extra <= allowed
    return _Recovery(single, single_within, multi, multi_within)
