"""Tests for the simulator's sideways teleport landing (walk-fluid plan Task 4, Hong's QB282 for item 4c): the simulated
world lands a sideways teleport by the planner's rule (`RouteGraph.side_landing`, Task 3) with its own copy of the numbers.

A character standing on a platform is stopped by the end of that platform: it lands at the end, or, when another
platform starts within the teleport's length in that direction and within `side_teleport_land_dy_px` of the end's
height, it carries onto that platform. A character off every platform (in the air, on a rope) lands as it did before.

The world has no map marks, so every platform end stops a teleport here; the planner's rule needs a marked end
(`RouteGraph.is_stop_end`). The shared cases below use marked ends only.

Terrain: A (0, 50)-(40, 50), B (48, 52)-(90, 52), C (45, 70)-(90, 70); reach 19 (Kanna).
"""

import dataclasses

import numpy as np
import pytest
from test_platform_ends import Built, M_b_at

from src.classes.classes import Kanna
from src.controller.rope_file import ON_ROPE_DX, RopeMark
from src.input.protocol import Key
from src.route.params import MovementParams
from src.route.sim.world import NoiseConfig, SimParams, SimPlatform, SimTerrain, SimWorld

REACH_H = Kanna().max_horizontal_traversal_dist
REACH_V = Kanna().max_vertical_traversal_dist
T_NS = 5_000_000_000

A = SimPlatform("A", True, ((0.0, 50.0), (40.0, 50.0)))
B = SimPlatform("B", True, ((48.0, 52.0), (90.0, 52.0)))
C = SimPlatform("C", True, ((45.0, 70.0), (90.0, 70.0)))


def _world(platforms, start, *, params=None, noise=None, seed=0, ropes=()):
    return SimWorld(SimTerrain((120, 100), tuple(platforms)), REACH_H, REACH_V,
                    params if params is not None else SimParams(),
                    noise if noise is not None else NoiseConfig.off(), np.random.default_rng(seed), start,
                    ropes=ropes)


def _lands(world, direction=Key.RIGHT):
    return world.teleport(direction, T_NS).end


def test_the_setting_is_a_copy_of_the_planners():
    assert SimParams().side_teleport_land_dy_px == 4.0
    assert SimParams().side_teleport_land_dy_px == MovementParams().side_teleport_land_dy_px


def test_teleport_within_the_platform_goes_full_reach():
    world = _world((A, B, C), (10, 50))
    assert _lands(world) == (29.0, 50.0) and world.pos == (29.0, 50.0)


def test_teleport_carries_onto_a_same_height_platform():
    """From (30, 50) B starts 18 px away, 2 px lower: the teleport (to x 49) lands on B, and falls the 2 px."""
    world = _world((A, B, C), (30, 50))
    effect = world.teleport(Key.RIGHT, T_NS)
    assert effect.end == (49.0, 52.0)
    assert effect.settle_s == pytest.approx(SimParams().teleport_settle_s + 2 / SimParams().fall_px_per_s)


def test_teleport_stops_at_the_end_when_the_next_platform_is_far():
    """From (25, 50) B starts 23 px away: the full reach (x 44) is past A's end and B is out of reach."""
    assert _lands(_world((A, B, C), (25, 50))) == (40.0, 50.0)


def test_teleport_stops_at_the_end_above_a_lower_platform():
    """B removed: C is 10 px away from (35, 50) but 20 px lower. Stop at the end, never fall to y 70."""
    assert _lands(_world((A, C), (35, 50))) == (40.0, 50.0)


def test_platform_6_px_higher_does_not_count():
    higher = SimPlatform("B", True, ((48.0, 44.0), (90.0, 44.0)))
    assert _lands(_world((A, higher), (30, 50))) == (40.0, 50.0)
    four_higher = SimPlatform("B", True, ((48.0, 46.0), (90.0, 46.0)))
    assert _lands(_world((A, four_higher), (30, 50))) == (49.0, 46.0)      # 4 px is within the setting


def test_the_range_is_the_setting():
    higher = SimPlatform("B", True, ((48.0, 44.0), (90.0, 44.0)))
    wide = dataclasses.replace(SimParams(), side_teleport_land_dy_px=6.0)
    assert _lands(_world((A, higher), (30, 50), params=wide)) == (49.0, 44.0)
    narrow = dataclasses.replace(SimParams(), side_teleport_land_dy_px=1.0)
    assert _lands(_world((A, B, C), (30, 50), params=narrow)) == (40.0, 50.0)   # B is 2 px lower: out of range


def test_teleport_to_the_left_is_the_mirror():
    """On B (48 to 90) going left: A's right end is (40, 50)."""
    assert _lands(_world((A, B, C), (55, 52)), Key.LEFT) == (36.0, 50.0)    # A's end 15 px away: carries onto A
    assert _lands(_world((A, B, C), (60, 52)), Key.LEFT) == (48.0, 52.0)    # A's end 20 px away: stops at B's left end
    assert _lands(_world((A, B, C), (80, 52)), Key.LEFT) == (61.0, 52.0)    # within B: full reach


def test_the_nearest_platform_wins_and_a_tie_goes_to_the_first():
    near = SimPlatform("near", True, ((46.0, 51.0), (90.0, 51.0)))
    far = SimPlatform("far", True, ((48.0, 50.0), (90.0, 50.0)))
    assert _lands(_world((A, far, near), (30, 50))) == (49.0, 51.0)         # 16 px beats 18 px
    twin = SimPlatform("twin", True, ((46.0, 49.0), (90.0, 49.0)))
    assert _lands(_world((A, near, twin), (30, 50))) == (49.0, 51.0)        # equal distance: the first listed


def test_a_short_teleport_uses_its_own_length_for_the_distance():
    """A negative `side_teleport_extra_px` (Task 5's short-teleport scenario) makes the length 18: B 18 px away is in
    reach, B 19 px away is not (a reach-long test would carry it)."""
    short = dataclasses.replace(SimParams(), side_teleport_extra_px=-1.0)
    assert _lands(_world((A, B, C), (30, 50), params=short)) == (48.0, 52.0)
    assert _lands(_world((A, B, C), (29, 50), params=short)) == (40.0, 50.0)
    assert _lands(_world((A, B, C), (10, 50), params=short)) == (28.0, 50.0)


def test_an_overshoot_lengthens_the_teleport_and_its_reach():
    over = dataclasses.replace(NoiseConfig.off(), overshoot_p=1.0, overshoot_px=(3, 3))
    assert _lands(_world((A, B, C), (10, 50), noise=over)) == (32.0, 50.0)
    assert _lands(_world((A, B, C), (25, 50), noise=over)) == (40.0, 50.0)   # B 23 px away, length 22
    assert _lands(_world((A, B, C), (27, 50), noise=over)) == (49.0, 52.0)   # B 21 px away: carries, to x 27 + 22


@pytest.mark.parametrize("seed", range(8))
def test_the_landing_offset_never_leaves_the_platform(seed):
    """The landing offset (1-2 px either way) is clamped into the platform the teleport lands on: no read in the air."""
    always = dataclasses.replace(NoiseConfig.off(), landing_p=1.0)
    at_the_end = _world((A, B, C), (25, 50), noise=always, seed=seed).teleport(Key.RIGHT, T_NS).end
    assert at_the_end[1] == 50.0 and 38.0 <= at_the_end[0] <= 40.0
    onto_b = _world((A, B, C), (30, 50), noise=always, seed=seed).teleport(Key.RIGHT, T_NS).end
    assert onto_b[1] == 52.0 and 48.0 <= onto_b[0] <= 51.0
    within = _world((A, B, C), (10, 50), noise=always, seed=seed).teleport(Key.RIGHT, T_NS).end
    assert within[1] == 50.0 and within[0] in (27.0, 28.0, 30.0, 31.0)


def test_off_platform_teleport_keeps_the_old_rule():
    """In the air, and on a rope: no platform end stops it. It lands on the first surface at or below its y minus 6 at
    x + reach, here C (y 70), where a character standing on A would be stopped at (40, 50)."""
    world = _world((A, C), (30, 50))
    world.pos = (30.0, 45.0)
    effect = world.teleport(Key.RIGHT, T_NS)
    assert effect.end == (49.0, 70.0)
    assert effect.settle_s == pytest.approx(0.35 + 25 / 60)   # the 25 px fall from y 45 to 70 (Task 4 review m3)

    rope = RopeMark(30 - ON_ROPE_DX, 50, 70)
    roped = _world((A, C), (30, 50), ropes=(rope,), noise=dataclasses.replace(NoiseConfig.off(), rope_grab_depth_px=(3, 3)))
    roped.drop(T_NS)
    assert roped.on_rope == 0 and roped.pos == (30.0, 53.0)
    assert _lands(roped) == (49.0, 70.0)
    assert roped.on_rope is None


def test_a_rope_keeps_the_old_rule_even_where_its_hang_point_is_on_a_platform_surface():
    """Task 4 review m2: the rope's top is 1 px above A's surface and the character grabs it 1 px below the top, so it
    hangs at (30, 50), on A's line. It is on the rope, not on A: no end of A stops the teleport, it lands on C (y 70) as
    before. Judged by the position alone it would be stopped at (40, 50)."""
    rope = RopeMark(30 - ON_ROPE_DX, 49, 70)
    roped = _world((A, C), (30, 50), ropes=(rope,), noise=dataclasses.replace(NoiseConfig.off(), rope_grab_depth_px=(1, 1)))
    roped.drop(T_NS)
    assert roped.on_rope == 0 and roped.pos == (30.0, 50.0)
    assert roped._platform_at(roped.pos) == 0     # the position alone says "on A"
    effect = roped.teleport(Key.RIGHT, T_NS)
    assert effect.end == (49.0, 70.0)
    assert effect.settle_s == pytest.approx(0.35 + 20 / 60)
    assert roped.on_rope is None


def test_the_old_landing_offset_still_moves_an_off_platform_teleport_first():
    """From the air the offset is added before the surface is looked up, as before: a platform that starts at x 50 is
    missed by the 1 px offset that puts the landing at x 48."""
    edge = SimPlatform("edge", True, ((49.0, 60.0), (90.0, 60.0)))
    offset = dataclasses.replace(NoiseConfig.off(), landing_p=1.0, landing_px=(1, 1))
    ends = set()
    for seed in range(12):
        world = _world((A, edge), (30, 50), noise=offset, seed=seed)
        world.pos = (30.0, 45.0)
        ends.add(world.teleport(Key.RIGHT, T_NS).end)
    assert ends == {(50.0, 60.0), (30.0, 45.0)}   # +1 lands on the platform; -1 (x 48) finds no surface and stays


def test_no_platform_beyond_the_end_keeps_the_character_at_the_end():
    assert _lands(_world((A,), (30, 50))) == (40.0, 50.0)


def test_a_platform_behind_the_character_is_not_a_target():
    """A platform whose near end is behind the end of the platform the character stands on never counts."""
    behind = SimPlatform("behind", True, ((-30.0, 50.0), (-5.0, 50.0)))
    assert _lands(_world((behind, A), (30, 50))) == (40.0, 50.0)


# --- the planner's rule, on the same cases ------------------------------------------------------------------------


@pytest.mark.parametrize("name, px, sign, b_y", [
    ("A", 33, +1, 52),   # B starts 17 px away, 2 px lower: carries onto B (52)
    ("A", 31, +1, 52),   # B starts exactly the reach (19 px) away: carries onto B, at x 50 (T4R m1: the exact-A launch)
    ("A", 30, +1, 52),   # B starts 20 px away: stops at A's end (40)
    ("A", 29, +1, 52),   # B 21 px away: stops at A's end
    ("A", 28, +1, 52),   # 22 px away: stops at A's end
    ("A", 33, +1, 54),   # B 4 px lower, the setting's edge: carries onto B (54)
    ("A", 33, +1, 56),   # B 6 px lower, out of range: stops at A's end, never falls to it (T4R m1)
    ("B", 55, -1, 52),   # A's end 15 px away, 2 px higher: carries onto A (36)
    ("B", 60, -1, 52),   # A's end 20 px away: stops at B's left end (50)
    ("B", 66, -1, 52),   # 26 px away: stops at B's left end
])
def test_the_sim_and_the_planner_land_the_same_cases(name, px, sign, b_y):
    """Map M of `test_platform_ends` (A (24, 50)-(40, 50) marked at its right end, B (50, `b_y`)-(66, `b_y`) marked at its
    left end, `b_y` 52 in M) and the same two platforms as a sim terrain: `RouteGraph.side_landing` and
    `SimWorld._side_target` give the same x and the same platform, on the marked ends (the sim stops at an unmarked end too,
    the planner does not). Distance equal to the reach and a height difference at and past the setting are cases here, so
    a `<` for `<=` or a missing height test in either rule fails this test (Task 4 review m1)."""
    built = Built(M_b_at(b_y))
    planner_index = {"A": built.p(24), "B": built.p(50)}
    sim_platforms = (SimPlatform("A", True, ((24.0, 50.0), (40.0, 50.0))),
                     SimPlatform("B", True, ((50.0, float(b_y)), (66.0, float(b_y)))))
    y = 50.0 if name == "A" else float(b_y)
    world = _world(sim_platforms, (px, y))
    target = world._side_target(float(px), y, sign, REACH_H)
    assert target is not None
    sim_name = sim_platforms[world._platform_at(target)].name if world._platform_at(target) is not None else None
    x_plan, p_plan = built.rg.side_landing(planner_index[name], px, sign)
    assert (target[0], sim_name) == (float(x_plan), {v: k for k, v in planner_index.items()}[p_plan])
