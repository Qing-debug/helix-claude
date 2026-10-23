"""Tests for the simulated world (plan task B9): terrain loading, movement physics and injected errors."""

import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful

from src.input.protocol import Key
from src.route.sim.world import (
    InjectedError,
    NoiseConfig,
    SimParams,
    SimTerrain,
    SimWorld,
    load_terrain,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "ff2_terrain.json"
REACH_H = 19
REACH_V = 23
T_NS = 5_000_000_000


@pytest.fixture(scope="module")
def terrain() -> SimTerrain:
    result = load_terrain(FIXTURE)
    assert is_successful(result), result
    return result.unwrap()


def _world(terrain, start, noise=None, seed=0):
    return SimWorld(
        terrain, REACH_H, REACH_V, SimParams(), noise if noise is not None else NoiseConfig.off(),
        np.random.default_rng(seed), start,
    )


# --- 1. load_terrain -------------------------------------------------------------------------------------


def test_load_fixture(terrain):
    assert terrain.minimap_size == (162, 82)
    assert len(terrain.platforms) == 4
    top = next(p for p in terrain.platforms if p.name == "top")
    assert top.surface_y(72) == pytest.approx(26.1538, abs=1e-3)
    assert top.surface_y(37.9) is None
    assert top.surface_y(113.1) is None


def _write(tmp_path, data) -> Path:
    path = tmp_path / "terrain.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_points_not_strictly_increasing_is_failure(tmp_path):
    data = {"minimap_size": [10, 10], "platforms": [
        {"name": "a", "droppable": True, "points": [[1, 5], [3, 5], [3, 6]]}]}
    result = load_terrain(_write(tmp_path, data))
    assert not is_successful(result)


@pytest.mark.parametrize("missing", ["minimap_size", "platforms"])
def test_missing_top_level_key_is_failure(tmp_path, missing):
    data = {"minimap_size": [10, 10], "platforms": [
        {"name": "a", "droppable": True, "points": [[1, 5], [3, 5]]}]}
    del data[missing]
    result = load_terrain(_write(tmp_path, data))
    assert not is_successful(result)
    assert missing in result.failure()


@pytest.mark.parametrize("missing", ["name", "droppable", "points"])
def test_missing_platform_key_is_failure(tmp_path, missing):
    platform = {"name": "a", "droppable": True, "points": [[1, 5], [3, 5]]}
    del platform[missing]
    result = load_terrain(_write(tmp_path, {"minimap_size": [10, 10], "platforms": [platform]}))
    assert not is_successful(result)
    assert missing in result.failure()


def test_surface_below_picks_smallest_y_at_or_below(terrain):
    index, y = terrain.surface_below(101, 25)
    assert terrain.platforms[index].name == "mid-right"
    assert y == pytest.approx(42.5)
    assert terrain.surface_below(20, 0) is None


# --- 2. noise-off physics --------------------------------------------------------------------------------


def test_teleport_down_onto_offset_platform(terrain):
    world = _world(terrain, (101, 24))
    effect = world.teleport(Key.DOWN, T_NS)
    assert effect.kind == "teleport"
    assert effect.end == pytest.approx((101, 42.5))
    assert effect.settle_s == pytest.approx(0.35)
    assert world.pos == pytest.approx((101, 42.5))


def test_teleport_up_onto_platform_above(terrain):
    world = _world(terrain, (48, 61))
    assert world.teleport(Key.UP, T_NS).end == pytest.approx((48, 45))


def test_teleport_up_with_nothing_in_reach_stays(terrain):
    world = _world(terrain, (86, 60))
    assert world.teleport(Key.UP, T_NS).end == pytest.approx((86, 60))


def test_teleport_left_off_platform_falls_to_floor(terrain):
    world = _world(terrain, (58, 46))
    effect = world.teleport(Key.LEFT, T_NS)
    y_floor = 60 + 2 / 11
    assert effect.end == pytest.approx((39, y_floor))
    assert effect.settle_s == pytest.approx(0.35 + (y_floor - 46) / 60)


def test_walk_right_up_slope(terrain):
    world = _world(terrain, (66, 28))
    effect = world.walk(Key.RIGHT, 1625, T_NS)
    assert effect.kind == "walk"
    assert effect.end == pytest.approx((79, 24))
    assert effect.settle_s == pytest.approx(0)


def test_walk_off_platform_end_falls_without_crossing_gap(terrain):
    world = _world(terrain, (74, 45))
    effect = world.walk(Key.RIGHT, 2000, T_NS)  # 16 px: would reach x 90, past mid-left's end at 80
    assert effect.end == pytest.approx((80, 60))
    assert effect.settle_s == pytest.approx(15 / 60)


def test_walk_up_or_down_does_nothing(terrain):
    world = _world(terrain, (82, 60))
    for key in (Key.UP, Key.DOWN):
        effect = world.walk(key, 500, T_NS)
        assert effect.kind == "none"
        assert world.pos == (82, 60)


def test_drop_from_non_droppable_floor_does_nothing(terrain):
    world = _world(terrain, (48, 61))
    effect = world.drop(T_NS)
    assert effect.kind == "none"
    assert effect.injected == ()
    assert world.pos == (48, 61)


def test_drop_through_droppable_platform(terrain):
    world = _world(terrain, (49, 28))
    effect = world.drop(T_NS)
    assert effect.kind == "drop"
    assert effect.end == pytest.approx((49, 45.1))
    assert effect.settle_s == pytest.approx(0.25 + 17.1 / 60)


def test_nothing_keeps_position(terrain):
    world = _world(terrain, (82, 60))
    effect = world.nothing(T_NS)
    assert effect.kind == "none"
    assert effect.start == effect.end == (82, 60)
    assert effect.settle_s == 0
    assert world.effects == [effect]


def test_position_at_follows_last_effect(terrain):
    world = _world(terrain, (49, 28))
    assert world.position_at(0) == (49, 28)
    effect = world.drop(T_NS)
    settle_ns = int(effect.settle_s * 1e9)
    assert world.position_at(T_NS - 1) == (49, 28)
    mid = world.position_at(T_NS + settle_ns // 2)
    assert mid[0] == pytest.approx(49)
    assert mid[1] == pytest.approx((28 + 45.1) / 2, abs=1e-3)
    assert world.position_at(T_NS + settle_ns + 1) == pytest.approx((49, 45.1))


def test_start_off_any_surface_is_value_error(terrain):
    with pytest.raises(ValueError):
        _world(terrain, (82, 50))


# --- 3. injected errors ----------------------------------------------------------------------------------


def test_no_op_always(terrain):
    noise = dataclasses.replace(NoiseConfig.off(), no_op_p=1)
    world = _world(terrain, (49, 28), noise)
    moves = [
        lambda t: world.teleport(Key.RIGHT, t),
        lambda t: world.teleport(Key.DOWN, t),
        lambda t: world.teleport(Key.UP, t),
        lambda t: world.walk(Key.LEFT, 1000, t),
        lambda t: world.drop(t),
    ]
    for i, move in enumerate(moves):
        effect = move(T_NS + i)
        assert effect.kind == "none"
        assert effect.injected == (InjectedError.NO_OP,)
        assert effect.settle_s == 0
        assert world.pos == (49, 28)


def test_overshoot_adds_px_in_direction(terrain):
    noise = dataclasses.replace(NoiseConfig.off(), overshoot_p=1, overshoot_px=(3, 3))
    world = _world(terrain, (82, 60), noise)
    effect = world.teleport(Key.RIGHT, T_NS)
    assert effect.end[0] == pytest.approx(104)
    assert effect.injected == (InjectedError.OVERSHOOT,)


def test_landing_offsets_end_x_by_exactly_one(terrain):
    noise = dataclasses.replace(NoiseConfig.off(), landing_p=1, landing_px=(1, 1))
    clean = _world(terrain, (82, 60)).teleport(Key.RIGHT, T_NS)
    for seed in range(10):
        noisy = _world(terrain, (82, 60), noise, seed).teleport(Key.RIGHT, T_NS)
        assert abs(noisy.end[0] - clean.end[0]) == pytest.approx(1)
        assert noisy.injected == (InjectedError.LANDING,)


def test_off_zeroes_every_probability():
    off = NoiseConfig.off()
    assert (off.no_op_p, off.overshoot_p, off.landing_p, off.miss_p) == (0, 0, 0, 0)


# --- 4. no-op rate ---------------------------------------------------------------------------------------


def test_no_op_share_with_defaults(terrain):
    world = _world(terrain, (82, 60), NoiseConfig(), seed=7)
    no_ops = 0
    for i in range(10_000):
        effect = world.teleport(Key.RIGHT if i % 2 == 0 else Key.LEFT, T_NS + i * 1_000_000_000)
        no_ops += InjectedError.NO_OP in effect.injected
    assert 0.05 <= no_ops / 10_000 <= 0.07


# --- 5. determinism --------------------------------------------------------------------------------------


def _run(terrain, seed):
    world = _world(terrain, (82, 60), NoiseConfig(), seed)
    for i in range(300):
        t = T_NS + i * 1_000_000_000
        match i % 5:
            case 0:
                world.teleport(Key.RIGHT, t)
            case 1:
                world.teleport(Key.LEFT, t)
            case 2:
                world.teleport(Key.UP, t)
            case 3:
                world.drop(t)
            case 4:
                world.walk(Key.LEFT, 800, t)
    return world.effects


def test_same_seed_gives_identical_effects(terrain):
    first = _run(terrain, 11)
    assert first == _run(terrain, 11)
    assert first != _run(terrain, 12)
