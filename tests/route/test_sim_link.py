"""Tests for the simulated link, clock and position source (plan task B10)."""

import re
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure

import src.route.sim.link as link_module
from src.input.input_layer import InputLayer
from src.input.link import InputError
from src.input.protocol import Accepted, Done, Key, Step
from src.input.timing import TimingModel, TimingType
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.world import NoiseConfig, SimParams, SimTerrain, SimWorld, load_terrain

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "ff2_terrain.json"
REACH_H = 19
REACH_V = 23
LATENCY_NS = 30_000_000


@pytest.fixture(scope="module")
def terrain() -> SimTerrain:
    result = load_terrain(FIXTURE)
    assert is_successful(result), result
    return result.unwrap()


def _setup(terrain, start):
    params = SimParams()
    world = SimWorld(terrain, REACH_H, REACH_V, params, NoiseConfig.off(), np.random.default_rng(0), start)
    clock = SimClock()
    link = SimLink(world, clock, params)
    layer = InputLayer(link, TimingModel(np.random.default_rng(3)))
    return world, clock, link, layer


def _total_ns(steps) -> int:
    return sum(s.delta_100us for s in steps) * 100_000


# --- 1. decoding through a real InputLayer --------------------------------------------------------------


def test_teleport_down_through_input_layer(terrain):
    world, _, _, layer = _setup(terrain, (101, 24))
    result = layer.teleport(Key.DOWN)
    assert is_successful(result), result
    assert world.pos == pytest.approx((101, 42.5))
    assert world.effects[-1].kind == "teleport"


def test_drop_through_input_layer(terrain):
    world, _, _, layer = _setup(terrain, (49, 28))
    result = layer.drop()
    assert is_successful(result), result
    assert world.effects[-1].kind == "drop"
    assert world.pos[1] == pytest.approx(45.1)


def test_walk_through_input_layer_uses_quantised_schedule(terrain):
    world, _, _, layer = _setup(terrain, (82, 60))
    result = layer.walk(Key.RIGHT, 1000.0)
    assert is_successful(result), result
    walk_sample = next(s for s in result.unwrap().plan.samples if s.type == TimingType.WALK)
    expected_dx = walk_sample.wire_100us / 10 / 1000 * SimParams().walk_px_per_s
    assert world.effects[-1].kind == "walk"
    assert world.pos[0] - 82 == pytest.approx(expected_dx, abs=1e-6)


@pytest.mark.parametrize("action", ["tap_e", "jump"])
def test_tap_and_jump_move_nothing(terrain, action):
    world, _, _, layer = _setup(terrain, (82, 60))
    result = layer.tap(Key.E) if action == "tap_e" else layer.jump()
    assert is_successful(result), result
    assert world.pos == (82.0, 60.0)
    assert world.effects[-1].kind == "none"
    assert world.effects[-1].injected == ()


def test_play_report_stamps_and_seq(terrain):
    _, _, link, layer = _setup(terrain, (82, 60))
    first = layer.tap(Key.E).unwrap().report
    second = layer.jump().unwrap().report
    assert (first.seq, second.seq) == (1, 2)
    for report in (first, second):
        assert report.sent_ns <= report.written_ns <= report.accepted_ns <= report.done_ns
        assert report.accepted == Accepted(report.seq, 0)
        assert report.done == Done(report.seq, 0, None, 0, 0, ())
    assert link.plays == 2


def test_two_arrows_or_teleport_with_e_first_is_nothing(terrain):
    params = SimParams()
    world = SimWorld(terrain, REACH_H, REACH_V, params, NoiseConfig.off(), np.random.default_rng(0), (82, 60))
    link = SimLink(world, SimClock(), params)
    # E down before the arrow: not a teleport.
    link.play([Step(0, Key.E), Step(50, Key.E | Key.RIGHT), Step(800, Key.RIGHT), Step(50, Key(0))])
    # Two arrows: not a walk.
    link.play([Step(0, Key.LEFT), Step(50, Key.LEFT | Key.RIGHT), Step(800, Key.RIGHT), Step(50, Key(0))])
    assert [e.kind for e in world.effects] == ["none", "none"]
    assert world.pos == (82.0, 60.0)


# --- 2. virtual time ------------------------------------------------------------------------------------


def test_clock_advances_by_latency_plus_schedule_total(terrain):
    world, clock, _, layer = _setup(terrain, (82, 60))
    for act in (lambda: layer.teleport(Key.RIGHT), lambda: layer.walk(Key.LEFT, 500.0), layer.drop):
        before = clock.now_ns()
        record = act().unwrap()
        assert clock.now_ns() - before == LATENCY_NS + _total_ns(record.plan.steps)
        assert world.effects[-1].t_end_ns == clock.now_ns()
        assert record.report.done_ns == clock.now_ns()


def test_clock_sleep_and_advance():
    clock = SimClock()
    assert clock.now_ns() == 1_000_000_000
    clock.sleep(0.25)
    assert clock.now_ns() == 1_250_000_000
    clock.advance(7)
    assert clock.now_ns() == 1_250_000_007
    assert SimClock(start_ns=5).now_ns() == 5


def test_release_all_advances_nothing(terrain):
    world, clock, link, _ = _setup(terrain, (82, 60))
    before = clock.now_ns()
    result = link.release_all()
    assert is_successful(result), result
    report = result.unwrap()
    assert clock.now_ns() == before
    assert report.accepted_ns is None and report.accepted is None
    assert report.seq == 1
    assert world.effects == []


# --- 3. injected link failures --------------------------------------------------------------------------


def test_fail_next_fails_one_play_then_works(terrain):
    world, _, link, layer = _setup(terrain, (101, 24))
    releases = []
    real_release_all = link.release_all

    def spy_release_all():
        result = real_release_all()
        releases.append(result)
        return result

    link.release_all = spy_release_all
    link.fail_next = [InputError.ACK_TIMEOUT]

    failed = layer.teleport(Key.DOWN)
    assert failed == Failure(InputError.ACK_TIMEOUT)
    assert world.pos == (101.0, 24.0)
    assert world.effects == []
    assert len(releases) == 1 and is_successful(releases[0])
    assert link.fail_next == []

    worked = layer.teleport(Key.DOWN)
    assert is_successful(worked), worked
    assert world.pos == pytest.approx((101, 42.5))
    assert len(releases) == 1
    assert link.plays == 2


# --- 4. position source ---------------------------------------------------------------------------------


def test_position_source_during_and_after_fall(terrain):
    world, clock, _, layer = _setup(terrain, (49, 28))
    source = SimPositionSource(world, clock, 0.0, np.random.default_rng(1))
    assert layer.drop().unwrap()
    effect = world.effects[-1]
    assert effect.end == pytest.approx((49, 45.1))
    settle_ns = int(effect.settle_s * 1e9)
    for frac in (0.25, 0.5, 0.75):
        clock.advance(int(settle_ns * frac) - (clock.now_ns() - effect.t_end_ns))
        read = source.read()
        assert is_successful(read), read
        x, y = read.unwrap()
        assert x == 49
        assert 28 < y < 45
    clock.advance(settle_ns)
    assert source.read().unwrap() == (49, round(45.1))


def test_position_source_returns_ints(terrain):
    world, clock, _, _ = _setup(terrain, (82, 60))
    x, y = SimPositionSource(world, clock, 0.0, np.random.default_rng(1)).read().unwrap()
    assert (x, y) == (82, 60)
    assert type(x) is int and type(y) is int


def test_position_source_always_misses_with_miss_p_one(terrain):
    world, clock, _, _ = _setup(terrain, (82, 60))
    source = SimPositionSource(world, clock, 1.0, np.random.default_rng(1))
    assert all(source.read() == Failure("miss") for _ in range(50))


def test_link_source_has_no_teleport_reach_literals():
    """Hard rule 4: no 19 / 23 literal in the module."""
    source = Path(link_module.__file__).read_text(encoding="utf-8")
    assert re.search(r"(?<![\w.])(19|23)(?![\w.])", source) is None
