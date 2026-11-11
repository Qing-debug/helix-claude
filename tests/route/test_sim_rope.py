"""Tests for the simulator's ropes (rope fix plan, Tasks 5-6, H88, H93): the rope physics in `SimWorld`, the `jump_off`
decode in `SimLink`, the rope file read by `run_sim`, and the run in which the walker gets off the rope it grabbed.

The ropes are Fossil Foundry 2's two, read from `tests/fixtures/fossil_foundry_2.ropes.json` (Task 2), on each terrain
fixture. A character on a rope reads 1 px left of the rope's column (`ON_ROPE_DX`), so the right rope is at x 106.
"""

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful
from test_walker_standing import Rec

from src.classes.classes import Kanna
from src.controller.rope_file import ON_ROPE_DX, read_ropes, rope_file_for
from src.input.input_layer import InputLayer
from src.input.protocol import Key, Step
from src.input.timing import TimingModel
import src.route.sim.run as sim_run
from src.route.sim.link import SimClock, SimLink
from src.route.sim.run import REAL_GROUND_NOISE, REAL_GROUND_SIM, judge, run_sim
from src.route.sim.world import (
    InjectedError, NoiseConfig, SimParams, SimTerrain, SimWorld, load_terrain,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
DEFAULT_TERRAIN = FIXTURES / "ff2_terrain.json"
REAL_TERRAIN = FIXTURES / "ff2_real_ground.json"
REACH_H = Kanna().max_horizontal_traversal_dist
REACH_V = Kanna().max_vertical_traversal_dist
T_NS = 5_000_000_000

ROPES = read_ropes(rope_file_for(CSV)).unwrap().ropes
LEFT_ROPE, RIGHT_ROPE = ROPES            # x 46 and x 107
READ_X = RIGHT_ROPE.x + ON_ROPE_DX       # 106: where a character on the right rope reads

TERRAINS = pytest.mark.parametrize("terrain_path", [DEFAULT_TERRAIN, REAL_TERRAIN], ids=["default", "real_ground"])


def _terrain(path: Path) -> SimTerrain:
    result = load_terrain(path)
    assert is_successful(result), result
    return result.unwrap()


def _surface(terrain: SimTerrain, name: str, x: float) -> float:
    y = next(p for p in terrain.platforms if p.name == name).surface_y(x)
    assert y is not None
    return y


def _floor(terrain: SimTerrain, x: float) -> float:
    return _surface(terrain, "floor", x)


def _world(terrain, start, *, noise=None, seed=0, ropes=ROPES, params=None):
    return SimWorld(terrain, REACH_H, REACH_V, params if params is not None else SimParams(),
                    noise if noise is not None else NoiseConfig.off(), np.random.default_rng(seed), start,
                    ropes=ropes)


def _above_rope(terrain, x=READ_X, **kwargs) -> SimWorld:
    """A world standing on the mid-right platform at x, over the right rope."""
    return _world(terrain, (x, _surface(terrain, "mid-right", x)), **kwargs)


def _grabbed(terrain, seed=0) -> SimWorld:
    """A world on the right rope: a drop over it grabbed it."""
    world = _above_rope(terrain, seed=seed)
    world.drop(T_NS)
    assert world.on_rope == 1
    return world


def _with(noise: NoiseConfig, **changes) -> NoiseConfig:
    return dataclasses.replace(noise, **changes)


# --- 1. grabbing the rope with a drop ---------------------------------------------------------------------------


@TERRAINS
def test_drop_over_the_rope_grabs_it(terrain_path):
    terrain = _terrain(terrain_path)
    world = _above_rope(terrain)
    effect = world.drop(T_NS)
    assert world.on_rope == 1                                  # the index of the right rope in the marks
    assert world.pos[0] == READ_X
    assert RIGHT_ROPE.top_y < world.pos[1] < RIGHT_ROPE.bottom_y
    assert world.rope_grabs == 1
    assert effect.kind == "drop" and effect.injected == ()      # the live mechanic, not noise
    assert effect.settle_s == SimParams().drop_settle_s

    off = _above_rope(terrain, x=102)                          # 4 px off the rope: the drop lands on the floor
    off.drop(T_NS)
    assert off.on_rope is None and off.rope_grabs == 0
    assert off.pos == pytest.approx((102, _floor(terrain, 102)))


@TERRAINS
def test_a_drop_grabs_within_two_px_of_the_rope_and_not_three(terrain_path):
    terrain = _terrain(terrain_path)
    for x, grabs in ((104, True), (108, True), (103, False), (109, False)):
        world = _above_rope(terrain, x=x)
        world.drop(T_NS)
        assert (world.on_rope == 1) is grabs, x


@TERRAINS
def test_the_grab_depth_is_three_to_nine_px_below_the_rope_top(terrain_path):
    terrain = _terrain(terrain_path)
    depths = set()
    for seed in range(60):
        world = _above_rope(terrain, seed=seed)
        world.drop(T_NS)
        depths.add(world.pos[1] - RIGHT_ROPE.top_y)
    assert depths <= set(range(3, 10)) and len(depths) > 3


@TERRAINS
def test_the_left_rope_is_grabbed_from_the_top_platform(terrain_path):
    terrain = _terrain(terrain_path)
    read_x = LEFT_ROPE.x + ON_ROPE_DX
    world = _world(terrain, (read_x, _surface(terrain, "top", read_x)))
    world.drop(T_NS)
    assert world.on_rope == 0 and world.pos[0] == read_x
    assert LEFT_ROPE.top_y < world.pos[1] < LEFT_ROPE.bottom_y


@TERRAINS
def test_a_drop_from_a_platform_that_is_not_the_rope_top_never_grabs(terrain_path):
    """The top platform of the right rope's column is 20 px above the rope's top: its drop lands on mid-right."""
    terrain = _terrain(terrain_path)
    world = _world(terrain, (READ_X, _surface(terrain, "top", READ_X)))
    world.drop(T_NS)
    assert world.on_rope is None and world.rope_grabs == 0


@TERRAINS
def test_drops_on_a_rope_climb_down(terrain_path):
    terrain = _terrain(terrain_path)
    world = _grabbed(terrain)
    x = world.pos[0]
    climbs = 0
    while world.on_rope is not None:
        before = world.pos
        effect = world.drop(T_NS + climbs)
        assert effect.kind == "drop" and effect.injected == ()
        climbs += 1
        assert climbs < 40
        if world.on_rope is not None:
            assert world.pos == (x, before[1] + SimParams().rope_climb_px)
    assert world.pos == pytest.approx((x, _floor(terrain, x)))     # at the bottom it stands on the floor
    assert climbs >= 2 and world.rope_grabs == 1


@TERRAINS
@pytest.mark.parametrize("rope", [0, 1], ids=["left", "right"])
def test_the_climb_leaves_the_rope_at_the_first_surface_below_it(terrain_path, rope):
    """ROPE153-R5 m1: the default terrain's floor at the right rope is 60.0 and `bottom_y` is 61 (left: mid-left 45.0,
    `bottom_y` 46), so a climb that reaches that surface used to stay on the rope for one more drop."""
    terrain = _terrain(terrain_path)
    mark = ROPES[rope]
    read_x = mark.x + ON_ROPE_DX
    start = _surface(terrain, "top" if rope == 0 else "mid-right", read_x)
    world = _world(terrain, (read_x, start), noise=_with(NoiseConfig.off(), rope_grab_depth_px=(5, 5)))
    world.drop(T_NS)
    assert world.on_rope == rope and world.pos[1] == mark.top_y + 5
    y0 = world.pos[1]
    landing = terrain.surface_below(read_x, y0)
    assert landing is not None
    drops = 0
    while world.on_rope is not None:
        world.drop(T_NS + drops)
        drops += 1
        assert drops < 40
    assert world.pos == pytest.approx((read_x, landing[1]))
    assert drops == math.ceil((landing[1] - y0) / SimParams().rope_climb_px)    # no extra drop at the surface


# --- 2. getting off the rope ------------------------------------------------------------------------------------


@TERRAINS
def test_moves_off_the_rope(terrain_path):
    terrain = _terrain(terrain_path)
    air, fall = SimParams().jump_air_s, SimParams().fall_px_per_s

    world = _grabbed(terrain)
    world.teleport(Key.DOWN, T_NS)
    assert world.on_rope is None and world.pos == pytest.approx((READ_X, _floor(terrain, READ_X)))

    world = _grabbed(terrain)
    y = world.pos[1]
    world.teleport(Key.UP, T_NS)
    landing = terrain.surface_below(READ_X, y - REACH_V)
    assert landing is not None
    assert world.on_rope is None and world.pos == pytest.approx((READ_X, landing[1]))

    world = _grabbed(terrain)
    y = world.pos[1]
    effect = world.jump_off(Key.LEFT, T_NS)
    assert effect.kind == "jump_off"
    assert world.on_rope is None and world.pos == pytest.approx((READ_X - 4, _floor(terrain, READ_X - 4)))
    assert effect.settle_s == pytest.approx(air + (world.pos[1] - y) / fall)

    world = _grabbed(terrain)
    world.jump_off(Key.RIGHT, T_NS)
    assert world.on_rope is None and world.pos == pytest.approx((READ_X + 4, _floor(terrain, READ_X + 4)))

    world = _grabbed(terrain)
    before = world.pos
    effect = world.walk(Key.RIGHT, 500, T_NS)
    assert effect.kind == "none" and world.pos == before and world.on_rope == 1

    world = _grabbed(terrain)
    world.teleport(Key.LEFT, T_NS)                       # a sideways teleport leaves the rope too
    assert world.on_rope is None


def test_jump_off_only_goes_left_or_right():
    terrain = _terrain(DEFAULT_TERRAIN)
    world = _grabbed(terrain)
    before = world.pos
    for key in (Key.UP, Key.DOWN, Key.E):
        effect = world.jump_off(key, T_NS)
        assert effect.kind == "none" and effect.injected == ()
    assert world.pos == before and world.on_rope == 1


def test_jump_off_from_a_platform_lands_on_the_first_surface_at_or_below_six_up():
    terrain = _terrain(DEFAULT_TERRAIN)
    world = _above_rope(terrain)
    effect = world.jump_off(Key.RIGHT, T_NS)
    assert effect.kind == "jump_off"
    assert world.pos == pytest.approx((READ_X + 4, _surface(terrain, "mid-right", READ_X + 4)))
    assert world.on_rope is None and world.rope_grabs == 0


def test_jump_off_with_no_surface_stays():
    terrain = _terrain(DEFAULT_TERRAIN)
    world = _world(terrain, (113, _surface(terrain, "top", 113)))      # the right end of the top platform
    # Set directly on purpose: no public move leaves the character off every platform, and the constructor's start check
    # refuses such a point. Nothing at x 204 or 196 on any platform, so a jump off has no landing.
    world.pos = (200.0, 23.0)
    effect = world.jump_off(Key.RIGHT, T_NS)
    assert effect.kind == "jump_off" and world.pos == (200.0, 23.0)


def test_a_knockback_that_lands_leaves_the_rope():
    terrain = _terrain(DEFAULT_TERRAIN)
    world = _grabbed(terrain)
    world.knockback(5, T_NS)
    assert world.on_rope is None and world.pos[0] == READ_X + 5


# --- 3. the draws -----------------------------------------------------------------------------------------------


@TERRAINS
def test_no_op_never_grabs(terrain_path):
    terrain = _terrain(terrain_path)
    noise = _with(NoiseConfig.off(), no_op_p=1.0, rope_grab_p=1.0)
    world = _above_rope(terrain, noise=noise)
    before = world.pos
    effect = world.drop(T_NS)
    assert effect.kind == "none" and effect.injected == (InjectedError.NO_OP,)
    assert world.rope_grabs == 0 and world.on_rope is None and world.pos == before
    effect = world.teleport(Key.DOWN, T_NS)
    assert effect.injected == (InjectedError.NO_OP,)
    assert world.rope_grabs == 0 and world.on_rope is None and world.pos == before


@TERRAINS
def test_teleport_down_grab_needs_the_knob(terrain_path):
    terrain = _terrain(terrain_path)

    world = _above_rope(terrain)                         # rope_grab_p == 0: no grab and no draw
    plain = _above_rope(terrain, ropes=())
    effect = world.teleport(Key.DOWN, T_NS)
    plain.teleport(Key.DOWN, T_NS)
    assert world.on_rope is None and world.rope_grabs == 0 and effect.injected == ()
    assert world.pos == pytest.approx((READ_X, _floor(terrain, READ_X)))
    assert world.rng.bit_generator.state == plain.rng.bit_generator.state

    sure = _with(NoiseConfig.off(), rope_grab_p=1.0)
    world = _above_rope(terrain, noise=sure)
    effect = world.teleport(Key.DOWN, T_NS)
    assert effect.kind == "teleport" and effect.injected == (InjectedError.ROPE_GRAB,)
    assert world.on_rope == 1 and world.rope_grabs == 1 and world.pos[0] == READ_X
    assert RIGHT_ROPE.top_y < world.pos[1] < RIGHT_ROPE.bottom_y

    world = _above_rope(terrain, x=100, noise=sure)      # not over the rope: no draw, today's landing
    plain = _above_rope(terrain, x=100, noise=sure, ropes=())
    world.teleport(Key.DOWN, T_NS)
    plain.teleport(Key.DOWN, T_NS)
    assert world.on_rope is None and world.pos == plain.pos
    assert world.rng.bit_generator.state == plain.rng.bit_generator.state


@TERRAINS
def test_a_grab_replaces_the_landing_draws(terrain_path):
    terrain = _terrain(terrain_path)
    noise = _with(NoiseConfig.off(), rope_grab_p=1.0, landing_p=1.0)
    for seed in range(20):
        world = _above_rope(terrain, noise=noise, seed=seed)
        effect = world.teleport(Key.DOWN, T_NS)
        assert effect.injected == (InjectedError.ROPE_GRAB,) and world.pos[0] == READ_X


@TERRAINS
def test_a_grab_never_starts_on_a_rope(terrain_path):
    terrain = _terrain(terrain_path)
    world = _grabbed(terrain, seed=3)
    world.noise = _with(NoiseConfig.off(), rope_grab_p=1.0)
    world.teleport(Key.DOWN, T_NS)
    assert world.rope_grabs == 1 and world.on_rope is None


# --- 4. the link ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key, dx", [(Key.RIGHT, 4), (Key.LEFT, -4)])
def test_link_decodes_jump_off(key, dx):
    terrain = _terrain(DEFAULT_TERRAIN)
    world = _above_rope(terrain)
    layer = InputLayer(SimLink(world, SimClock(), SimParams()), TimingModel(np.random.default_rng(3)))
    result = layer.jump_off(key)
    assert is_successful(result), result
    assert [e.kind for e in world.effects] == ["jump_off"]
    assert world.pos == pytest.approx((READ_X + dx, _surface(terrain, "mid-right", READ_X + dx)))


def test_link_still_reads_a_drop_as_before():
    terrain = _terrain(DEFAULT_TERRAIN)
    world = _above_rope(terrain)
    layer = InputLayer(SimLink(world, SimClock(), SimParams()), TimingModel(np.random.default_rng(3)))
    assert is_successful(layer.drop())
    assert [e.kind for e in world.effects] == ["drop"] and world.on_rope == 1


def _press(first: Key, second: Key) -> list[Step]:
    """`first` down, then `second` joins it, then both up: the two keys' first downs are in that order."""
    return [Step(10, first), Step(10, first | second), Step(10, Key(0))]


@pytest.mark.parametrize("steps, kind", [
    (_press(Key.RIGHT, Key.ALT), "jump_off"),     # the control: the arrow first is a jump off
    (_press(Key.ALT, Key.RIGHT), "none"),         # ROPE153-R5 m2: Alt first is not
    (_press(Key.ALT, Key.LEFT), "none"),
    (_press(Key.UP, Key.ALT), "none"),            # Up+Alt is not a jump off, in either order
    (_press(Key.ALT, Key.UP), "none"),
], ids=["right-then-alt", "alt-then-right", "alt-then-left", "up-then-alt", "alt-then-up"])
def test_link_reads_only_an_arrow_then_alt_as_a_jump_off(steps, kind):
    terrain = _terrain(DEFAULT_TERRAIN)
    world = _above_rope(terrain)
    before = world.pos
    link = SimLink(world, SimClock(), SimParams())
    assert is_successful(link.play(steps))
    assert [e.kind for e in world.effects] == [kind]
    assert (world.pos != before) == (kind == "jump_off") and world.on_rope is None


# --- 5. run_sim -------------------------------------------------------------------------------------------------


SMOKE = (366, 25, 0, 44, 237.9668)       # moves, no-ops, drops, teleports down, simulated seconds (Task 1)
REAL = (352, 22, 0, 42, 237.7692)        # the same on the real ground (Task 1)


def _figures(report):
    return (report.moves, report.no_ops, report.drops, report.teleport_downs, round(report.sim_seconds, 4))


def test_defaults_change_nothing_here(tmp_path):
    """The rope file is present and the knobs are off: Task 1's figures, no grab, though the walker now knows the zones.
    The same with the file hidden. The real ground's preset has the rope knob on since Task 6 (its own figures are
    pinned in `test_sim_real_ground.py`), so its half of this test turns the knob off."""
    result = run_sim(CSV, DEFAULT_TERRAIN, 20, 2)
    assert is_successful(result), result
    assert _figures(result.unwrap()) == SMOKE and result.unwrap().rope_grabs == 0

    real = run_sim(CSV, REAL_TERRAIN, 20, 2, noise=_with(REAL_GROUND_NOISE, rope_grab_p=0.0), sim_params=REAL_GROUND_SIM)
    assert is_successful(real), real
    assert _figures(real.unwrap()) == REAL and real.unwrap().rope_grabs == 0

    hidden = tmp_path / CSV.name                         # a copy of the map with no rope file beside it
    hidden.write_bytes(CSV.read_bytes())
    assert not rope_file_for(hidden).exists()
    again = run_sim(hidden, DEFAULT_TERRAIN, 20, 2)
    assert is_successful(again), again
    assert dataclasses.asdict(again.unwrap()) == dataclasses.asdict(result.unwrap())


def test_a_bad_rope_file_is_the_runs_failure(tmp_path):
    bad = tmp_path / CSV.name
    bad.write_bytes(CSV.read_bytes())
    rope_file_for(bad).write_text("not json", encoding="utf-8")
    result = run_sim(bad, DEFAULT_TERRAIN, 1, 2)
    assert not is_successful(result)
    assert "cannot be used" in result.failure() and rope_file_for(bad).name in result.failure()


def test_node_offsets_do_not_move_the_marks():
    """The offset copy of the map is made in a temporary folder with no rope file: the marks still come from the original
    map's file, where they were measured, so a grab still happens on a run with node offsets. The knob is 0.5, not 1.0
    (Task 5's value): with 1.0 and offsets of 2 px, `next_move` places a rope read at (106, 48) on the map, the zones
    are never asked, the planned walk does nothing on a rope, and the leg times out."""
    result = run_sim(CSV, DEFAULT_TERRAIN, 3, 2, noise=NoiseConfig(node_offset_px=2, rope_grab_p=0.5))
    assert is_successful(result), result
    assert result.unwrap().rope_grabs >= 1


def test_a_recovery_replay_that_starts_on_a_rope_is_not_recovered_and_not_a_crash(monkeypatch):
    """ROPE154-R6 m2: with node offsets of 2 px and the grab knob at 1.0 (the mix the test above says times out), a leg
    ends with the character still on a rope, and the next leg's noise-off replay starts there. A world cannot start off
    every platform surface, so that replay used to raise ValueError out of `run_sim`. Now it has no replay, and its leg
    counts as a leg with errors that did not recover."""
    replays = []
    real = sim_run._replay_moves

    def spy(*args):
        replays.append(real(*args))
        return replays[-1]

    monkeypatch.setattr(sim_run, "_replay_moves", spy)
    result = run_sim(CSV, DEFAULT_TERRAIN, 3, 2, noise=NoiseConfig(node_offset_px=2, rope_grab_p=1.0))
    assert is_successful(result), result
    report = result.unwrap()
    assert replays.count(None) >= 1
    not_recovered = ((report.single_error_legs - report.single_error_within_3)
                     + (report.multi_error_legs - report.multi_error_within_bound))
    assert not_recovered >= replays.count(None)


# --- 6. the rope rule on a run: the walker gets off the rope it grabbed -------------------------------------------


GRAB_SEED = 3     # seed 2 (Task 5's) grabs 5 times and walks every leg, but its draws give 16/360 no-ops: 4.44%, under the 5% bar


def _grab_run(**kwargs):
    return run_sim(CSV, DEFAULT_TERRAIN, 20, GRAB_SEED, noise=NoiseConfig(rope_grab_p=0.2), **kwargs)


@pytest.mark.slow
def test_rope_grab_run_passes():
    result = _grab_run()
    assert is_successful(result), result
    report = result.unwrap()
    assert report.rope_grabs >= 1
    failed = [c for c in judge(report, 0) if not c.ok]
    assert not failed, failed


@pytest.mark.slow
def test_rope_grab_run_recovers():
    """Task 5's before-proof, turned round: with no rope known, a grab that ended more than 6 px from every platform line
    (Task 5's seed 2: the 4th, in lap 14 on leg 6, (105, 42) -> (113, 60), at (106, 52)) ended its leg `off_map`. Now the
    walker knows the rope: no leg ends `off_map`, and at least one move is a rope move, a `planned` event with `toward`
    None (a rope read is off the graph, so there is no hop to aim at)."""
    rec = Rec()
    result = _grab_run(observer=rec)
    assert is_successful(result), result
    report = result.unwrap()
    assert report.rope_grabs >= 1
    assert report.off_map == 0
    assert [e for e in rec.of("leg_end") if e[2] == "off_map"] == []
    assert [e for e in rec.of("planned") if e[7] is None]


def test_jump_offs_are_the_walkers_jump_off_moves():
    """Task 7: `SimReport.jump_offs` counts the walker's moves of kind `jump_off`. Half the moves doing nothing makes a
    blink up from a rope fail twice now and then, so the order reaches its jump off (seed 1: 1 grab, 1 jump off); the count
    equals the observer's `planned` events of that kind, and the quiet default run has none."""
    rec = Rec()
    result = run_sim(CSV, DEFAULT_TERRAIN, 5, 1, noise=NoiseConfig(no_op_p=0.5, rope_grab_p=0.5), observer=rec)
    assert is_successful(result), result
    report = result.unwrap()
    assert (report.rope_grabs, report.jump_offs, report.off_map, report.laps_completed) == (1, 1, 0, 5)
    assert report.jump_offs == len([e for e in rec.of("planned") if e[3] == "jump_off"])
    quiet = run_sim(CSV, DEFAULT_TERRAIN, 2, 1).unwrap()
    assert quiet.jump_offs == 0
