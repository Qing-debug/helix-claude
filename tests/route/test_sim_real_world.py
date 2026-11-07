"""Tests for the simulator's real-world knobs (off-graph fix plan, Task 2, OFFP141): the dot's offset, the live
teleport length and mob knockback. The world is the real ground of Fossil Foundry 2 (`ff2_real_ground.json`).

The knobs default to "off", so the seeded runs of the earlier tasks stay bit-identical (`test_defaults_change_nothing`);
the dot's offset defaults to the live 3 px (Task 4), which the position source takes off again with `foot_from_dot`.
"""

import dataclasses
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful

from src.classes.classes import Kanna
from src.input.protocol import Key
from src.route.params import MovementParams
from src.route.position import foot_from_dot
from src.route.sim.link import SimClock, SimPositionSource
from src.route.sim.run import run_sim
from src.route.sim.world import (
    InjectedError, NoiseConfig, SimParams, SimPlatform, SimTerrain, SimWorld, load_terrain,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
REAL_GROUND = FIXTURES / "ff2_real_ground.json"
CSV = FIXTURES / "fossil_foundry_2.csv"
OLD_TERRAIN = FIXTURES / "ff2_terrain.json"
REACH_H = Kanna().max_horizontal_traversal_dist
REACH_V = Kanna().max_vertical_traversal_dist
T_NS = 5_000_000_000
STEP_NS = 50_000_000


@pytest.fixture(scope="module")
def terrain() -> SimTerrain:
    result = load_terrain(REAL_GROUND)
    assert is_successful(result), result
    return result.unwrap()


def _world(terrain, start, *, params=None, noise=None, seed=0):
    return SimWorld(
        terrain, REACH_H, REACH_V, params if params is not None else SimParams(),
        noise if noise is not None else NoiseConfig.off(), np.random.default_rng(seed), start,
    )


def _always_knockback() -> NoiseConfig:
    return dataclasses.replace(NoiseConfig.off(), knockback_p=1.0)


def _curve(effect, t_ns):
    """The knockback's own curve: x and y both linear from start to end over the whole settle."""
    frac = min(1.0, (t_ns - effect.t_end_ns) / (effect.settle_s * 1e9))
    return (effect.start[0] + (effect.end[0] - effect.start[0]) * frac,
            effect.start[1] + (effect.end[1] - effect.start[1]) * frac)


# --- 1. the dot --------------------------------------------------------------------------------------------


def test_read_reports_the_dot(terrain):
    """The world draws the dot 3 px above the foot; the source reports it through `foot_from_dot`, 3 px below."""
    params = SimParams(dot_above_foot_px=3)
    for start, dot, want in (((70, 26.0), (70, 23), (70, 26)), ((100, 42.5), (100, 39), (100, 42))):
        world = _world(terrain, start, params=params)
        source = SimPositionSource(world, SimClock(T_NS), 0.0, np.random.default_rng(0))
        assert (round(start[0]), round(start[1]) - world.params.dot_above_foot_px) == dot  # the dot itself
        assert source.read().unwrap() == want == foot_from_dot(dot)


def test_read_with_default_params_is_unchanged(terrain):
    world = _world(terrain, (100, 42.5))
    source = SimPositionSource(world, SimClock(T_NS), 0.0, np.random.default_rng(0))
    assert source.read().unwrap() == (100, 42)   # round() sends the half to the even integer


# --- 2. the teleport's length -------------------------------------------------------------------------------


def test_side_teleport_extra(terrain):
    world = _world(terrain, (49, 28), params=SimParams(side_teleport_extra_px=2))
    effect = world.teleport(Key.RIGHT, T_NS)
    assert effect.end == (49 + REACH_H + 2, 26.0)
    assert world.pos == (70.0, 26.0)


def test_side_teleport_extra_leaves_vertical_teleports_alone(terrain):
    world = _world(terrain, (101, 24), params=SimParams(side_teleport_extra_px=2))
    assert world.teleport(Key.DOWN, T_NS).end == (101.0, 43.0)


# --- 3. knockback -------------------------------------------------------------------------------------------


def test_knockback_lands_and_moves_through_the_air(terrain):
    world = _world(terrain, (60, 28))
    effect = world.knockback(5, T_NS)
    assert effect.kind == "knockback"
    assert effect.end == (65.0, 28.0)
    assert effect.injected == (InjectedError.KNOCKBACK,)
    assert effect.settle_s == 0.3
    assert effect.t_end_ns == T_NS
    assert world.pos == (65.0, 28.0)
    assert world.position_at(T_NS) == (60.0, 28.0)
    assert world.position_at(T_NS + 150_000_000) == pytest.approx((62.5, 28.0))
    assert world.position_at(T_NS + 300_000_000) == (65.0, 28.0)


def test_a_falling_knockback_changes_every_read_until_it_lands(terrain):
    world = _world(terrain, (40, 28))
    clock = SimClock(T_NS)
    source = SimPositionSource(world, clock, 0.0, np.random.default_rng(0))
    effect = world.knockback(-8, clock.now_ns())   # off the top platform's left end onto the floor
    assert effect.end == (32.0, 61.0)
    assert effect.settle_s == pytest.approx(0.3 + (61 - 28) / SimParams().fall_px_per_s)
    reads = []
    for _ in range(round(effect.settle_s * 1e9 / STEP_NS) + 1):
        reads.append(source.read().unwrap())
        clock.advance(STEP_NS)
    assert reads[0] == (40, 28) and reads[-1] == (32, 61)
    assert all(a != b for a, b in zip(reads, reads[1:])), reads


@pytest.mark.parametrize("dx", [3, 4])
def test_a_slow_flat_shove_reads_equal_one_poll_early(terrain, dx):
    """Plan review N1: the walker settles on two equal reads, so a 3 or 4 px level shove (reads 50 ms apart) can end
    its settle with the character still 1 px short of `world.pos`, as live."""
    world = _world(terrain, (60, 28))
    clock = SimClock(T_NS)
    source = SimPositionSource(world, clock, 0.0, np.random.default_rng(0))
    world.knockback(dx, clock.now_ns())
    reads = []
    for _ in range(7):
        reads.append(source.read().unwrap())
        clock.advance(STEP_NS)
    min_settle_polls = round(MovementParams().min_settle_s * 1e9 / STEP_NS)   # the walker looks from this poll on
    first_settled = next(a for a, b in zip(reads[min_settle_polls:], reads[min_settle_polls + 1:]) if a == b)
    assert first_settled == (60 + dx - 1, 28), reads
    assert world.pos == (60.0 + dx, 28.0)


def test_knockback_without_ground_stays(terrain):
    world = _world(terrain, (60, 28))
    effect = world.knockback(-60, T_NS)   # x = 0, left of every platform
    assert effect.kind == "knockback"
    assert effect.injected == (InjectedError.KNOCKBACK,)
    assert effect.start == effect.end == (60.0, 28.0)
    assert world.pos == (60.0, 28.0)
    assert world.position_at(T_NS + 100_000_000) == (60.0, 28.0)


@pytest.mark.parametrize("move", ["teleport", "walk", "drop"])
def test_knockback_p_one_follows_every_move(terrain, move):
    start = (60, 28) if move == "drop" else (49, 28)
    world = _world(terrain, start, noise=_always_knockback())
    if move == "teleport":
        first = world.teleport(Key.RIGHT, T_NS)
    elif move == "walk":
        first = world.walk(Key.RIGHT, 800, T_NS)
    else:
        first = world.drop(T_NS)
    assert first.kind == move
    assert len(world.effects) == 2 and world.effects[0] is first
    shove = world.effects[-1]
    assert shove.kind == "knockback"
    assert shove.injected == (InjectedError.KNOCKBACK,)
    assert shove.t_end_ns == first.t_end_ns
    assert shove.start == first.end
    assert abs(shove.end[0] - shove.start[0]) in range(3, 9)
    assert world.pos == shove.end
    for t_ns in (T_NS, T_NS + 100_000_000, T_NS + 250_000_000, T_NS + int(shove.settle_s * 1e9), T_NS + 5_000_000_000):
        assert world.position_at(t_ns) == pytest.approx(_curve(shove, t_ns))


def test_no_knockback_after_a_no_op_or_a_nothing(terrain):
    noise = dataclasses.replace(_always_knockback(), no_op_p=1.0)
    world = _world(terrain, (49, 28), noise=noise)
    assert world.teleport(Key.RIGHT, T_NS).injected == (InjectedError.NO_OP,)
    assert world.nothing(T_NS).kind == "none"
    assert [e.kind for e in world.effects] == ["none", "none"]


def test_knockback_is_drawn_from_the_seeded_generator(terrain):
    def shoves(seed):
        world = _world(terrain, (49, 28), noise=_always_knockback(), seed=seed)
        world.teleport(Key.RIGHT, T_NS)
        return world.effects[-1].end

    assert shoves(7) == shoves(7)
    assert len({shoves(seed) for seed in range(10)}) > 1


def test_knockback_rises_at_most_six_px():
    """OFFG-R2 R2-1: a shove lands on the first surface at or below its y minus 6. Ledge B is exactly 6 px above the
    character (reached); ledge C is 7 px above (passed over for the floor). A mutant with any other rise fails one."""
    terrain = SimTerrain((120, 80), (
        SimPlatform("a", True, ((0, 50), (48, 50))),
        SimPlatform("b", True, ((52, 44), (70, 44))),
        SimPlatform("c", True, ((72, 43), (100, 43))),
        SimPlatform("floor", False, ((0, 70), (100, 70))),
    ))
    assert _world(terrain, (45, 50)).knockback(10, T_NS).end == (55.0, 44.0)
    assert _world(terrain, (45, 50)).knockback(30, T_NS).end == (75.0, 70.0)


def test_knockback_up_the_ramp_lands_on_the_ramp(terrain):
    """R2-1 on the real ground: 8 px right of (60, 28) is the ramp at y 27, not a fall to the floor."""
    assert _world(terrain, (60, 28)).knockback(8, T_NS).end == (68.0, 27.0)


def test_knockback_sign_and_size_cover_the_range(terrain):
    """R2-1: over many seeds the shove is every size 3..8 (8 included) on both sides, nothing else."""
    sizes = set()
    for seed in range(200):
        world = _world(terrain, (82, 61), noise=_always_knockback(), seed=seed)
        world.walk(Key.RIGHT, 0, T_NS)
        shove = world.effects[-1]
        assert shove.kind == "knockback"
        sizes.add(shove.end[0] - shove.start[0])
    assert sizes == {-8.0, -7.0, -6.0, -5.0, -4.0, -3.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0}


@pytest.mark.parametrize("move", ["drop", "walk"])
def test_no_knockback_after_a_none_record(terrain, move):
    """R2-1: a drop from the non-droppable floor and a walk from off every surface are "none" records; no shove. (The
    public moves never leave a surface, so the walk case puts the world off the floor's end by hand.)"""
    world = _world(terrain, (82, 61), noise=_always_knockback())
    if move == "walk":
        world.pos = (5.0, 61.0)
    first = world.drop(T_NS) if move == "drop" else world.walk(Key.RIGHT, 400, T_NS)
    assert first.kind == "none"
    assert [effect.kind for effect in world.effects] == ["none"]


# --- 4. the defaults ----------------------------------------------------------------------------------------


def test_defaults_change_nothing():
    result = run_sim(CSV, OLD_TERRAIN, 20, 2)
    assert is_successful(result), result
    report = result.unwrap()
    assert (report.moves, report.no_ops, report.drops, report.teleport_downs) == (366, 25, 21, 23)
    assert round(report.sim_seconds, 4) == 241.7168
