"""Tests for src/input/actions.py (plan A, Task P3; contracts C1, C3, C4)."""
import itertools
import math
import random

import numpy as np
import pytest
from returns.pipeline import is_successful

from src.input.actions import Plan, drop, jump, sequence, tap, teleport, walk
from src.input.protocol import Key, Step, transitions, validate_schedule
from src.input.timing import TimingModel, TimingType

ALL_KEYS = [Key.LEFT, Key.RIGHT, Key.UP, Key.DOWN, Key.E, Key.ALT, Key.SPACE]
SEEDS = range(1000)


class StubModel:
    """Fixed values per method (scalar, or a list consumed in order); counts calls in `draws`."""

    def __init__(self, start_draws=0, **values):
        self.draws = start_draws
        self.walk_args = []
        self._src = {}
        for name in ("hold", "gap", "chord_offset", "release_lag", "reaction", "walk"):
            v = values.get(name, 100.0)
            self._src[name] = iter(v) if isinstance(v, list) else itertools.repeat(v)

    def _next(self, name):
        self.draws += 1
        return next(self._src[name])

    def hold(self):
        return self._next("hold")

    def gap(self):
        return self._next("gap")

    def chord_offset(self):
        return self._next("chord_offset")

    def release_lag(self):
        return self._next("release_lag")

    def reaction(self):
        return self._next("reaction")

    def walk(self, intended_ms):
        self.walk_args.append(intended_ms)
        return self._next("walk")


def wire(ms):
    return round(ms * 10)


def masks(plan):
    return [s.mask for s in plan.steps]


def deltas(plan):
    return [s.delta_100us for s in plan.steps]


def span_sum(plan, sample):
    return sum(s.delta_100us for s in plan.steps[sample.step_from + 1 : sample.step_to + 1])


def assert_plan_ok(plan):
    """Valid schedule, and every sample's span in `steps` matches its wire value (C4)."""
    assert is_successful(validate_schedule(plan.steps)), plan
    for s in plan.samples:
        assert s.wire_100us == round(s.ms * 10)
        assert 0 <= s.step_from <= s.step_to < len(plan.steps)
        if s.type is TimingType.HOLD_CLIPPED:
            assert span_sum(plan, s) <= s.wire_100us, (s, plan)
        else:
            assert span_sum(plan, s) == s.wire_100us, (s, plan)


# ---------------------------------------------------------------- single-key / chord actions


def test_tap_is_down_then_up_after_the_drawn_hold():
    m = StubModel(hold=116.04)
    p = tap(m, Key.E)
    assert isinstance(p, Plan)
    assert p.name == "tap"
    assert p.steps == (Step(0, Key.E), Step(1160, Key(0)))
    (s,) = p.samples
    assert s.type is TimingType.HOLD
    assert s.ms == 116.04  # the raw float, not rounded
    assert s.wire_100us == 1160
    assert (s.step_from, s.step_to) == (0, 1)
    assert m.draws == 1


def test_sample_index_is_the_draws_before_the_draw():
    m = StubModel(start_draws=7, hold=100.0)
    (s,) = tap(m, Key.E).samples
    assert s.index == 7
    assert m.draws == 8


def test_teleport_is_arrow_down_then_e_with_release_lag():
    m = StubModel(start_draws=3, chord_offset=25.0, hold=110.0, release_lag=12.5)
    p = teleport(m, Key.LEFT)
    assert p.name == "teleport"
    assert p.steps == (
        Step(0, Key.LEFT),
        Step(250, Key.LEFT | Key.E),
        Step(1100, Key.LEFT),
        Step(125, Key(0)),
    )
    assert [s.type for s in p.samples] == [
        TimingType.CHORD_OFFSET,
        TimingType.HOLD,
        TimingType.RELEASE_LAG,
    ]
    assert [s.ms for s in p.samples] == [25.0, 110.0, 12.5]
    assert [s.index for s in p.samples] == [3, 4, 5]
    assert [(s.step_from, s.step_to) for s in p.samples] == [(0, 1), (1, 2), (2, 3)]
    assert m.draws == 6
    assert_plan_ok(p)


def test_teleport_with_zero_release_lag_keeps_one_key_per_step():
    p = teleport(StubModel(chord_offset=25.0, hold=110.0, release_lag=0.0), Key.RIGHT)
    assert masks(p) == [Key.RIGHT, Key.RIGHT | Key.E, Key.RIGHT, Key(0)]
    assert deltas(p) == [0, 250, 1100, 0]
    assert_plan_ok(p)


def test_drop_is_down_then_alt():
    p = drop(StubModel(chord_offset=30.0, hold=100.0, release_lag=20.0))
    assert p.name == "drop"
    assert masks(p) == [Key.DOWN, Key.DOWN | Key.ALT, Key.DOWN, Key(0)]
    assert deltas(p) == [0, 300, 1000, 200]
    assert [s.type for s in p.samples] == [
        TimingType.CHORD_OFFSET,
        TimingType.HOLD,
        TimingType.RELEASE_LAG,
    ]
    assert_plan_ok(p)


def test_jump_is_a_tap_of_alt():
    p = jump(StubModel(hold=90.0))
    assert p.name == "jump"
    assert p.steps == (Step(0, Key.ALT), Step(900, Key(0)))
    assert [s.type for s in p.samples] == [TimingType.HOLD]
    assert_plan_ok(p)


# ---------------------------------------------------------------- walk


def test_walk_returns_success_with_one_walk_sample():
    m = StubModel(start_draws=2, walk=1034.56)
    r = walk(m, Key.RIGHT, 1000)
    assert is_successful(r)
    p = r.unwrap()
    assert m.walk_args == [1000]
    assert p.name == "walk"
    assert p.steps == (Step(0, Key.RIGHT), Step(10346, Key(0)))  # round(10345.6)
    (s,) = p.samples
    assert s.type is TimingType.WALK
    assert s.ms == 1034.56
    assert (s.index, s.wire_100us, s.step_from, s.step_to) == (2, 10346, 0, 1)
    assert_plan_ok(p)


def test_walk_at_the_per_step_cap_succeeds():
    r = walk(StubModel(walk=6553.5), Key.LEFT, 6500)
    assert is_successful(r)
    assert r.unwrap().steps[1].delta_100us == 65535


def test_walk_over_the_per_step_cap_is_a_failure_not_an_exception():
    r = walk(StubModel(walk=6553.6), Key.LEFT, 6500)
    assert not is_successful(r)
    assert "per-step delta" in r.failure()


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf"), -math.inf])
def test_walk_with_a_nonsense_intended_ms_is_a_failure_without_drawing(bad):
    m = StubModel(walk=500.0)
    r = walk(m, Key.LEFT, bad)  # the real model raises ValueError on these (A-P2)
    assert not is_successful(r)
    assert m.draws == 0
    assert m.walk_args == []


def test_walk_with_the_real_model_rejects_nonsense_without_raising():
    r = walk(TimingModel(np.random.default_rng(1)), Key.LEFT, float("nan"))
    assert not is_successful(r)


# ---------------------------------------------------------------- sequence


def test_sequence_of_8_keys_is_16_steps():
    keys = [ALL_KEYS[i % 7] for i in range(8)]
    r = sequence(StubModel(hold=100.0, gap=300.0), keys)
    assert is_successful(r)
    assert len(r.unwrap().steps) == 16
    assert_plan_ok(r.unwrap())


def test_sequence_of_9_keys_is_a_failure_before_any_draw():
    m = StubModel()
    r = sequence(m, [Key.LEFT] * 9)
    assert not is_successful(r)
    assert "more than 8 keys" in r.failure()
    assert m.draws == 0


def test_empty_sequence_is_a_failure_not_an_exception():
    assert not is_successful(sequence(StubModel(), []))


def test_sequence_spaces_key_downs_by_gap_and_logs_samples_in_draw_order():
    m = StubModel(hold=[100.0, 110.0, 120.0], gap=[300.0, 310.0])
    p = sequence(m, [Key.LEFT, Key.UP, Key.RIGHT]).unwrap()
    assert p.name == "sequence"
    # downs at 0, 3000, 6100; each key's up 1000/1100/1200 after its own down
    assert masks(p) == [
        Key.LEFT,
        Key(0),
        Key.UP,
        Key(0),
        Key.RIGHT,
        Key(0),
    ]
    assert deltas(p) == [0, 1000, 2000, 1100, 2000, 1200]
    assert [(s.type, s.index) for s in p.samples] == [
        (TimingType.HOLD, 0),
        (TimingType.GAP, 1),
        (TimingType.HOLD, 2),
        (TimingType.GAP, 3),
        (TimingType.HOLD, 4),
    ]
    assert m.draws == 5
    assert_plan_ok(p)


def test_sequence_with_overlapping_different_keys_keeps_one_key_per_step():
    # LEFT is held 200 ms while UP goes down at 100 ms.
    m = StubModel(hold=[200.0, 50.0], gap=[100.0])
    p = sequence(m, [Key.LEFT, Key.UP]).unwrap()
    assert masks(p) == [Key.LEFT, Key.LEFT | Key.UP, Key.LEFT, Key(0)]
    assert deltas(p) == [0, 1000, 500, 500]
    assert_plan_ok(p)


def test_sequence_puts_an_up_before_a_down_on_the_same_tick():
    # LEFT's up (0.1 s hold) lands exactly on UP's down (0.1 s gap).
    p = sequence(StubModel(hold=[100.0, 60.0], gap=[100.0]), [Key.LEFT, Key.UP]).unwrap()
    assert masks(p) == [Key.LEFT, Key(0), Key.UP, Key(0)]
    assert deltas(p) == [0, 1000, 0, 600]
    gap_sample = p.samples[1]
    assert gap_sample.type is TimingType.GAP
    assert (gap_sample.step_from, gap_sample.step_to) == (0, 2)
    assert span_sum(p, gap_sample) == gap_sample.wire_100us
    assert_plan_ok(p)


def test_teleport_puts_ups_in_creation_order_on_the_same_tick():
    # Both releases fall on one tick: E first (key 2), then the arrow.
    p = teleport(StubModel(chord_offset=10.0, hold=100.0, release_lag=0.0), Key.UP)
    assert masks(p)[2:] == [Key.UP, Key(0)]
    assert_plan_ok(p)


def test_sequence_clips_a_repeated_key_and_logs_hold_clipped():
    # RIGHT, RIGHT: first RIGHT's 200 ms hold reaches past (gap 170 ms - 15 ms).
    m = StubModel(hold=[100.0, 110.0, 200.0, 90.0], gap=[150.0, 160.0, 170.0])
    p = sequence(m, [Key.LEFT, Key.UP, Key.RIGHT, Key.RIGHT]).unwrap()
    types = [s.type for s in p.samples]
    assert types == [
        TimingType.HOLD,
        TimingType.GAP,
        TimingType.HOLD,
        TimingType.GAP,
        TimingType.HOLD_CLIPPED,
        TimingType.GAP,
        TimingType.HOLD,
    ]
    clipped = p.samples[4]
    assert clipped.ms == 200.0  # the drawn float, unclipped
    assert clipped.wire_100us == 2000
    assert span_sum(p, clipped) == 1550  # 170 ms - 15 ms
    assert span_sum(p, clipped) < clipped.wire_100us
    assert_plan_ok(p)
    # the second RIGHT's down follows the clipped up by exactly 15 ms
    i = clipped.step_to
    assert masks(p)[i] & Key.RIGHT == 0
    assert p.steps[i + 1].delta_100us == 150
    assert masks(p)[i + 1] & Key.RIGHT


def test_sequence_does_not_clip_a_repeated_key_whose_hold_ends_early_enough():
    m = StubModel(hold=[100.0, 90.0], gap=[200.0])
    p = sequence(m, [Key.RIGHT, Key.RIGHT]).unwrap()
    assert [s.type for s in p.samples] == [TimingType.HOLD, TimingType.GAP, TimingType.HOLD]
    # exactly on the 15 ms boundary is not "past" it
    m = StubModel(hold=[185.0, 90.0], gap=[200.0])
    p = sequence(m, [Key.RIGHT, Key.RIGHT]).unwrap()
    assert p.samples[0].type is TimingType.HOLD
    assert_plan_ok(p)


def test_sequence_clips_a_non_adjacent_repeat_too():
    # [RIGHT, LEFT, RIGHT]: RIGHT's 500 ms hold would still be down at the second RIGHT (200 ms).
    m = StubModel(hold=[500.0, 100.0, 100.0], gap=[100.0, 100.0])
    p = sequence(m, [Key.RIGHT, Key.LEFT, Key.RIGHT]).unwrap()
    assert p.samples[0].type is TimingType.HOLD_CLIPPED
    assert span_sum(p, p.samples[0]) == 1850
    assert_plan_ok(p)


# ---------------------------------------------------------------- property tests, real model


@pytest.mark.parametrize("name", ["tap", "teleport", "drop", "jump"])
def test_fixed_actions_validate_for_1000_seeds(name):
    for seed in SEEDS:
        m = TimingModel(np.random.default_rng(seed))
        p = {
            "tap": lambda: tap(m, ALL_KEYS[seed % 7]),
            "teleport": lambda: teleport(m, ALL_KEYS[seed % 4]),
            "drop": lambda: drop(m),
            "jump": lambda: jump(m),
        }[name]()
        assert_plan_ok(p)
        assert m.draws == len(p.samples)
        assert [s.index for s in p.samples] == list(range(len(p.samples)))


def test_walks_validate_for_1000_seeds():
    for seed in SEEDS:
        rnd = random.Random(seed)
        m = TimingModel(np.random.default_rng(seed))
        r = walk(m, ALL_KEYS[seed % 4], rnd.uniform(300, 3000))
        assert is_successful(r), (seed, r)
        assert_plan_ok(r.unwrap())
        assert m.draws == 1


def test_sequences_validate_for_1000_seeds():
    for seed in SEEDS:
        rnd = random.Random(seed)
        keys = [rnd.choice(ALL_KEYS) for _ in range(rnd.randint(1, 8))]
        m = TimingModel(np.random.default_rng(seed))
        r = sequence(m, keys)
        assert is_successful(r), (seed, keys, r)
        p = r.unwrap()
        assert_plan_ok(p)
        assert len(p.steps) == 2 * len(keys)
        assert m.draws == len(p.samples)
        assert [s.index for s in p.samples] == list(range(len(p.samples)))


def test_sequences_of_two_keys_exercise_the_clip_rule_and_still_validate():
    clipped = 0
    for seed in SEEDS:
        rnd = random.Random(seed)
        keys = [rnd.choice([Key.LEFT, Key.RIGHT]) for _ in range(8)]
        m = TimingModel(np.random.default_rng(seed))
        p = sequence(m, keys).unwrap()
        assert_plan_ok(p)
        clipped += sum(s.type is TimingType.HOLD_CLIPPED for s in p.samples)
    assert clipped > 0


def test_plans_are_deterministic_for_a_seed():
    def make():
        return sequence(TimingModel(np.random.default_rng(5)), ALL_KEYS[:5]).unwrap()

    assert make() == make()


def test_transitions_of_a_plan_match_its_key_events():
    p = tap(StubModel(hold=100.0), Key.E)
    assert transitions(p.steps) == [(0, Key.E, True), (1000, Key.E, False)]
