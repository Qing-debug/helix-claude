"""Tests for the InputLayer facade (plan A task P5; contract C3).

A fake link and a stub timing model stand in for BleLink and TimingModel. The module's clock and sleep are
monkeypatched (C3 constructor unchanged, A-PF m6).
"""
import math
import threading
import time

import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.input import actions, input_layer
from src.input.input_layer import ActionRecord, InputLayer
from src.input.link import InputError
from src.input.protocol import Key
from src.input.timing import Sample, TimingType

from input_fakes import FakeLink, report  # tests/input/input_fakes.py

STIMULUS_NS = 1_000_000_000


class StubModel:
    """Fixed values per method; `draws` counts the values returned, as TimingModel.draws does (C3)."""

    def __init__(self, hold=100.0, gap=150.0, chord_offset=25.0, release_lag=30.0, reaction=230.0, walk=None):
        self._values = dict(hold=hold, gap=gap, chord_offset=chord_offset, release_lag=release_lag,
                            reaction=reaction)
        self._walk = walk
        self.draws = 0

    def _next(self, name):
        self.draws += 1
        return self._values[name]

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
        self.draws += 1
        return float(intended_ms) if self._walk is None else self._walk


class Clock:
    def __init__(self):
        self.now_ns = STIMULUS_NS
        self.events = []

    def clock(self):
        return self.now_ns

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))

    @property
    def sleeps(self):
        return [e[1] for e in self.events if e[0] == "sleep"]


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(input_layer, "_clock", c.clock)
    monkeypatch.setattr(input_layer, "_sleep", c.sleep)
    return c


@pytest.fixture
def link(clock):
    return FakeLink(clock.events)


# (id, call on the layer, the same Plan built straight from the actions module on a fresh stub)
METHODS = [
    ("tap", lambda L, **kw: L.tap(Key.LEFT, **kw), lambda m: actions.tap(m, Key.LEFT)),
    ("walk", lambda L, **kw: L.walk(Key.RIGHT, 500.0, **kw), lambda m: actions.walk(m, Key.RIGHT, 500.0).unwrap()),
    ("teleport", lambda L, **kw: L.teleport(Key.UP, **kw), lambda m: actions.teleport(m, Key.UP)),
    ("drop", lambda L, **kw: L.drop(**kw), lambda m: actions.drop(m)),
    ("jump", lambda L, **kw: L.jump(**kw), lambda m: actions.jump(m)),
    ("sequence", lambda L, **kw: L.sequence([Key.LEFT, Key.E, Key.ALT], **kw),
     lambda m: actions.sequence(m, [Key.LEFT, Key.E, Key.ALT]).unwrap()),
]
METHOD_IDS = [m[0] for m in METHODS]


@pytest.mark.parametrize("name, call, build", METHODS, ids=METHOD_IDS)
def test_each_method_builds_its_plan_and_plays_it(clock, link, name, call, build):
    model = StubModel()
    expected = build(StubModel())

    result = call(InputLayer(link, model))

    assert is_successful(result)
    record = result.unwrap()
    assert isinstance(record, ActionRecord)
    assert record.plan == expected
    assert record.plan.name == name
    assert link.calls == [("play", expected.steps)]
    assert record.report is link.play_result.unwrap()


@pytest.mark.parametrize("name, call, build", METHODS, ids=METHOD_IDS)
def test_without_stimulus_no_reaction_is_drawn_and_nothing_waits(clock, link, name, call, build):
    model = StubModel()
    record = call(InputLayer(link, model)).unwrap()

    assert record.reaction is None
    assert record.waited_ms == 0.0
    assert model.draws == len(record.plan.samples)  # the builder's draws only
    assert clock.sleeps == []


@pytest.mark.parametrize("name, call, build", METHODS, ids=METHOD_IDS)
def test_with_stimulus_it_waits_reaction_minus_elapsed_then_plays(clock, link, name, call, build):
    model = StubModel(reaction=230.0)
    clock.now_ns = STIMULUS_NS + 50_000_000  # 50 ms since the stimulus

    record = call(InputLayer(link, model), stimulus_ns=STIMULUS_NS).unwrap()

    assert record.reaction == Sample(TimingType.REACTION, 230.0, model.draws - 1, None, None, None)
    assert record.reaction.index == len(record.plan.samples)  # drawn after the builder's draws
    assert record.waited_ms == pytest.approx(180.0)
    assert clock.sleeps == [pytest.approx(0.180)]
    assert [e[0] for e in clock.events] == ["sleep", "play"]  # the wait comes before the send


def test_elapsed_past_the_reaction_waits_zero_and_still_records_the_draw(clock, link):
    model = StubModel(reaction=230.0)
    clock.now_ns = STIMULUS_NS + 300_000_000  # 300 ms > R

    record = InputLayer(link, model).tap(Key.E, stimulus_ns=STIMULUS_NS).unwrap()

    assert record.waited_ms == 0.0
    assert clock.sleeps == []
    assert record.reaction.ms == 230.0  # the chosen value is R, not the wait (C4)
    assert len(link.calls) == 1


def test_wait_uses_the_reaction_and_elapsed_exactly(clock, link):
    model = StubModel(reaction=212.5)
    clock.now_ns = STIMULUS_NS + 12_345_678

    record = InputLayer(link, model).jump(stimulus_ns=STIMULUS_NS).unwrap()

    assert record.waited_ms == pytest.approx(212.5 - 12.345678)
    assert clock.sleeps == [pytest.approx((212.5 - 12.345678) / 1000)]


def test_release_all_bypasses_timing(clock, link):
    model = StubModel()
    layer = InputLayer(link, model)

    result = layer.release_all()

    assert result is link.release_result
    assert link.calls == [("release_all",)]
    assert model.draws == 0
    assert clock.sleeps == []


@pytest.mark.parametrize("error", [InputError.ACK_TIMEOUT, InputError.LINK_LOST, InputError.NOT_CONNECTED])
def test_release_all_failure_passes_through(clock, link, error):
    link.release_result = Failure(error)

    result = InputLayer(link, StubModel()).release_all()

    assert result.failure() is error
    assert link.calls == [("release_all",)]


def test_walk_over_the_per_step_cap_is_too_long_without_waiting_or_sending(clock, link):
    model = StubModel(walk=6553.6)  # wire 65 536 > 65 535 (C1)
    clock.now_ns = STIMULUS_NS + 10_000_000

    result = InputLayer(link, model).walk(Key.RIGHT, 6000.0, stimulus_ns=STIMULUS_NS)

    assert result.failure() is InputError.TOO_LONG
    assert link.calls == []
    assert clock.sleeps == []
    assert model.draws == 1  # the walk draw only; no reaction drawn for a plan that is not sent


@pytest.mark.parametrize("intended_ms", [math.nan, -1.0, math.inf, 20_000.0])
def test_walk_with_an_unusable_intended_time_is_too_long(clock, link, intended_ms):
    model = StubModel()

    result = InputLayer(link, model).walk(Key.LEFT, intended_ms, stimulus_ns=STIMULUS_NS)

    assert result.failure() is InputError.TOO_LONG
    assert link.calls == []
    assert clock.sleeps == []
    assert model.draws == 0


def test_sequence_of_nine_keys_is_too_long_without_waiting_or_sending(clock, link):
    model = StubModel()
    keys = [Key.LEFT, Key.RIGHT, Key.UP, Key.DOWN, Key.E, Key.ALT, Key.SPACE, Key.LEFT, Key.RIGHT]
    clock.now_ns = STIMULUS_NS + 10_000_000

    result = InputLayer(link, model).sequence(keys, stimulus_ns=STIMULUS_NS)

    assert result.failure() is InputError.TOO_LONG
    assert link.calls == []
    assert clock.sleeps == []
    assert model.draws == 0


@pytest.mark.parametrize("call", [
    lambda L: L.walk(Key.LEFT | Key.RIGHT, 500.0, stimulus_ns=STIMULUS_NS),
    lambda L: L.sequence([], stimulus_ns=STIMULUS_NS),
    lambda L: L.sequence([Key.LEFT | Key.E], stimulus_ns=STIMULUS_NS),
], ids=["walk_two_direction_bits", "sequence_empty", "sequence_multi_bit_key"])
def test_a_malformed_direction_or_key_list_is_too_long_without_waiting_or_sending(clock, link, call):
    clock.now_ns = STIMULUS_NS + 10_000_000

    result = call(InputLayer(link, StubModel()))

    assert result.failure() is InputError.TOO_LONG
    assert link.calls == []
    assert clock.sleeps == []


def test_sequence_of_eight_keys_plays_sixteen_steps(clock, link):
    keys = [Key.LEFT, Key.RIGHT, Key.UP, Key.DOWN, Key.E, Key.ALT, Key.SPACE, Key.LEFT]

    record = InputLayer(link, StubModel()).sequence(keys, stimulus_ns=STIMULUS_NS).unwrap()

    assert len(link.calls) == 1
    assert len(link.calls[0][1]) == 16
    assert record.plan.steps == link.calls[0][1]


# --- Failure policy (piece 2): one release after an uncertain play failure (PA-adj1b-D1) ---

UNCERTAIN = [InputError.NOTIFY_FAILED, InputError.ACK_TIMEOUT, InputError.WRITE_FAILED]
NO_RELEASE = [InputError.REJECTED, InputError.TOO_LONG, InputError.ABORTED, InputError.LINK_LOST,
              InputError.NOT_CONNECTED]


def test_the_two_error_lists_cover_every_input_error():
    assert sorted(UNCERTAIN + NO_RELEASE) == sorted(InputError)


@pytest.mark.parametrize("release_result", [Success(report(8)), Failure(InputError.ACK_TIMEOUT)],
                         ids=["release_ok", "release_ack_timeout"])
@pytest.mark.parametrize("error", UNCERTAIN, ids=[e.name for e in UNCERTAIN])
@pytest.mark.parametrize("name, call, build", METHODS, ids=METHOD_IDS)
def test_an_uncertain_play_failure_releases_once_after_play_and_returns_the_play_error(
        clock, link, name, call, build, error, release_result):
    link.play_result = Failure(error)
    link.release_result = release_result
    expected = build(StubModel())

    result = call(InputLayer(link, StubModel()))

    assert result.failure() is error  # the release's own result does not replace it
    assert link.calls == [("play", expected.steps), ("release_all",)]  # once, after play, never retried


@pytest.mark.parametrize("error", NO_RELEASE, ids=[e.name for e in NO_RELEASE])
@pytest.mark.parametrize("name, call, build", METHODS, ids=METHOD_IDS)
def test_other_play_failures_send_no_release(clock, link, name, call, build, error):
    link.play_result = Failure(error)
    expected = build(StubModel())

    result = call(InputLayer(link, StubModel()))

    assert result.failure() is error
    assert link.calls == [("play", expected.steps)]


def test_a_successful_play_sends_no_release(clock, link):
    InputLayer(link, StubModel()).tap(Key.E).unwrap()

    assert [c[0] for c in link.calls] == ["play"]


class BlockingLink:
    """`play` blocks until `release_all` is called, then returns ABORTED, as BleLink does (PA-adj2-D1)."""

    def __init__(self):
        self.play_started = threading.Event()
        self._released = threading.Event()
        self.play_calls = 0
        self.release_calls = 0

    def play(self, steps):
        self.play_calls += 1
        self.play_started.set()
        if not self._released.wait(5.0):  # safety bound so a broken layer cannot hang the suite
            return Success(report(7))
        return Failure(InputError.ABORTED)

    def release_all(self):
        self.release_calls += 1
        self._released.set()
        return Success(report(8))


def test_feedback_release_from_another_thread_aborts_the_walk_without_waiting():
    link = BlockingLink()
    layer = InputLayer(link, StubModel())
    walk_result = []
    action_thread = threading.Thread(target=lambda: walk_result.append(layer.walk(Key.RIGHT, 3000.0)))

    action_thread.start()  # thread A: the action thread
    assert link.play_started.wait(2.0)
    time.sleep(0.2)
    release_result = []

    def feedback():  # thread B: the feedback thread
        t0 = time.perf_counter()
        release_result.append((layer.release_all(), time.perf_counter() - t0))

    feedback_thread = threading.Thread(target=feedback)
    feedback_thread.start()
    feedback_thread.join(2.0)
    action_thread.join(2.0)

    assert not feedback_thread.is_alive() and not action_thread.is_alive()
    assert walk_result, "the action thread produced no result"
    assert release_result, "the feedback thread produced no result"
    released, elapsed_s = release_result[0]
    assert is_successful(released)
    assert elapsed_s < 0.1
    assert walk_result[0].failure() is InputError.ABORTED
    assert link.play_calls == 1
    assert link.release_calls == 1  # ABORTED triggers no second release
