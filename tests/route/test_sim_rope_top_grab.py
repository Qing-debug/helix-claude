"""A scripted top-grab recovery on the right rope (walk-fluid Task 4, the carried review item T1R m1).

`test_sim_rope.py::test_a_run_that_reaches_a_placed_rope_read_recovers_through_the_rope_rule` (seed 4, grabs ending 1 or 2
px below the rope's top) used to hold a rope-rule move at a grab read the map places: a teleport that did nothing once left
the read where it was, and the walker took the rope rule. Since QB266 (walk-fluid Task 1) the seeded story has only
foot-of-rope no-ops, and no seed gives a top-read one again (seeds 0-41 checked in the review), so that test no longer
covers the top-grab recovery. This file scripts it: no seed, one planned no-op.

The character stands on the mid-right platform over the right rope and drops: it grabs the rope 1 px (or 2 px) below its
top, at (106, 44) (or (106, 45)), a row the map places. The walker is sent to (113, 60) and plans a teleport down toward
(105, 60) (a down move on a platform node's line); the world makes that first move a no-op (scripted, not drawn). The read
is unchanged and placed, so the walker takes the rope rule: a `planned` event with `toward` None, a teleport up off the
rope (the sim lands it on the platform above, y 23). The leg then goes on and reaches.
"""

import numpy as np
import pytest
from test_sim_rope import CSV, DEFAULT_TERRAIN, READ_X, RIGHT_ROPE, ROPES, _above_rope, _terrain, _with
from test_walker_standing import Rec, _load

from src.input.input_layer import InputLayer
from src.input.timing import TimingModel
from src.route.leg_log import LegOutcome, MemorySink
from src.route.ropes import RopeZones
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.world import InjectedError, NoiseConfig, SimParams
from src.route.walker import RouteWalker, WalkerConfig


@pytest.mark.parametrize("depth", [1, 2])
def test_a_scripted_no_op_at_a_top_grab_read_takes_the_rope_rule_and_the_leg_reaches(depth, monkeypatch):
    ff2 = _load(CSV)
    terrain = _terrain(DEFAULT_TERRAIN)
    world = _above_rope(terrain, noise=_with(NoiseConfig.off(), rope_grab_depth_px=(depth, depth)))
    clock = SimClock()
    world.drop(clock.now_ns())
    clock.advance(1_000_000_000)                  # the grab has settled: the first read is where the character hangs
    grab = (float(READ_X), float(RIGHT_ROPE.top_y + depth))
    assert world.on_rope == 1 and world.pos == grab
    moves = iter([True])                           # the walker's first move does nothing, every later one does its work
    monkeypatch.setattr(world, "_no_op", lambda: next(moves, False))
    layer = InputLayer(SimLink(world, clock, SimParams()), TimingModel(np.random.default_rng(1)))
    positions = SimPositionSource(world, clock, 0.0, np.random.default_rng(2))
    rec = Rec()
    walker = RouteWalker(ff2.rg, ff2.rotation, layer, positions, clock, MemorySink(), np.random.default_rng(3),
                         WalkerConfig(use_stimulus=False, leg_timeout_s=60.0), observer=rec,
                         ropes=RopeZones.build(ROPES, ff2.rg.params))
    began_ns = clock.now_ns()
    leg = walker.walk_leg(1, 0, None, ff2.nodes[(113, 60)])
    assert leg.outcome == LegOutcome.REACHED
    assert (clock.now_ns() - began_ns) / 1e9 < 10
    read = (READ_X, RIGHT_ROPE.top_y + depth)
    planned = [(e[2], e[3], e[4], e[7]) for e in rec.of("planned")]
    assert planned[0] == (read, "teleport", "DOWN", (105, 60))     # the planner's move from the placed grab read
    assert planned[1] == (read, "teleport", "UP", None)            # the rope rule, from the same read: no hop to aim at
    assert [p for p in planned if p[3] is None] == [planned[1]]    # and it is the only rope move
    assert [e.kind for e in world.effects][:3] == ["drop", "none", "teleport"]    # the grab, the no-op, the rope rule's blink
    assert world.effects[1].injected == (InjectedError.NO_OP,) and world.effects[1].start == world.effects[1].end == grab
    assert world.on_rope is None
