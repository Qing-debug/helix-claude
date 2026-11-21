"""WALKFIX Task 7 (QB283 2a): the walker wakes on each new picture instead of polling every `poll_s`.

The rig is `test_walker_standing`'s (a fake clock, a recording layer that moves nothing, the step observer) with a
`Pictures` fake in place of its scripted source: a picture arrives every 4 ms of fake time and shows what the script says
for it; `read` is `Failure(STALE)` until a picture newer than the last one read exists; `wait(timeout_s)` moves the fake
clock to the next picture, or by `timeout_s`, whichever comes first, and not at all when a newer picture is already there
(what `MinimapPositionSource.wait_new` does). The walker's own work takes no fake time, so every instant below is a sleep
or a wait. Pictures are at fake times `t0 + k * 4 ms`; "u" in the comments is the time since `t0`, which is the leg's start.

What the tests pin: each wait of `walk_leg` and of `_settle` ends at the next picture when the walker is given a waiter
(`wait_frame`), a successful read in the settle is still followed by `poll_s` of sleeping (WALKFIX170-D1 A, Review Focus 6),
the still and rope timers count the clock and not the reads, and a walker with no waiter sleeps `poll_s` as it always did
(Review Focus 8). The real-thread side (`wait_new`, the holders' condition, the session's wiring) is in
`test_frame_wait_walker.py`.
"""

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success
from test_walker_rope import RIGHT_ROPE_READ, ZONES, RopeLayer
from test_walker_standing import CSV, POLL_NS, STANDING_NS, Rec, RecordingLayer, _load, _write_map

from src.route.leg_log import LegOutcome, MemorySink
from src.route.params import MovementParams
from src.route.sim.link import SimClock
from src.route.walker import STALE, RouteWalker, WalkerConfig

S = 1_000_000_000
PERIOD_NS = 4_000_000   # a picture every 4 ms
PARAMS = MovementParams()
MIN_SETTLE_NS = round(PARAMS.min_settle_s * S)
ROPE_STILL_NS = round(PARAMS.rope_still_s * S)
TOP_RIGHT = (101, 24)
FLOOR = [(40, 60), (58, 60)]   # one platform, two nodes 18 px apart: the walker goes right
CUT_OFF = [(40, 60), (80, 60), (40, 10), (80, 10)]   # two platforms with no way between them


class SleepClock(SimClock):
    """A `SimClock` that keeps every `sleep(seconds)` it was asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        super().sleep(seconds)


class Pictures:
    """The position source and the waiter. `shown(k)` is what picture `k` (at `t0 + k * 4 ms`) shows: a Result."""

    def __init__(self, clock, shown) -> None:
        self.clock, self.shown, self.t0 = clock, shown, clock.now_ns()
        self.newest_read = -1
        self.reads: list[tuple[int, int, object]] = []   # (t_ns, newest picture at that time, result)
        self.waits: list[tuple[int, float, int]] = []    # (t_ns before, timeout_s, t_ns after)

    def _newest(self) -> int:
        return (self.clock.now_ns() - self.t0) // PERIOD_NS

    def read(self):
        k = self._newest()
        if k <= self.newest_read:
            result = Failure(STALE)
        else:
            self.newest_read = k
            result = self.shown(k)
        self.reads.append((self.clock.now_ns(), k, result))
        return result

    def wait(self, timeout_s: float) -> None:
        before = self.clock.now_ns()
        if self._newest() <= self.newest_read:   # nothing newer than the last read yet: wait for it, or time out
            next_picture = self.t0 + (self._newest() + 1) * PERIOD_NS
            self.clock.advance(min(next_picture - before, round(timeout_s * S)))
        self.waits.append((before, timeout_s, self.clock.now_ns()))

    def read_times(self, *, after_ns: int = 0) -> list[int]:
        """The fake-clock time of each successful read from `after_ns` after `t0` on."""
        return [t - self.t0 for t, _, result in self.reads if is_successful(result) and t - self.t0 >= after_ns]


@pytest.fixture(scope="module")
def ff2():
    return _load(CSV)


@pytest.fixture(scope="module")
def floor(tmp_path_factory):
    return _load(_write_map(tmp_path_factory.mktemp("wake") / "floor.csv", FLOOR))


@pytest.fixture(scope="module")
def cut_off(tmp_path_factory):
    return _load(_write_map(tmp_path_factory.mktemp("wake") / "cut_off.csv", CUT_OFF))


def _rig(world, shown, *, waiter=True, leg_timeout_s=1.0, ropes=None, layer_of=RecordingLayer):
    clock = SleepClock()
    pictures = Pictures(clock, shown)
    layer, rec, lines = layer_of(clock), Rec(), []
    extra = {} if ropes is None else {"ropes": ropes}
    if waiter:
        extra["wait_frame"] = pictures.wait
    walker = RouteWalker(world.rg, world.rotation, layer, pictures, clock, MemorySink(), np.random.default_rng(0),
                         WalkerConfig(use_stimulus=False, leg_timeout_s=leg_timeout_s), observer=rec, log=lines.append,
                         **extra)
    return walker, pictures, clock, layer, rec, lines


def _leg(world, rig, dst_xy):
    return rig[0].walk_leg(1, 0, None, world.nodes[dst_xy])


def _gaps_after_a_settle(rec) -> list[int]:
    """For each `settled` event with a `planned` event after it: the time from the one to the other."""
    planned = [e[1] for e in rec.of("planned")]
    return [min(p for p in planned if p >= s[1]) - s[1] for s in rec.of("settled") if any(p >= s[1] for p in planned)]


# --- the first read after a settle --------------------------------------------------------------------------------------


def test_first_read_after_a_settle_waits_for_the_next_picture(floor):
    """The settle's last read took the newest picture, so the walker's next read is STALE. It used to sleep 50 ms before
    the one that plans; with a waiter the planned event comes at the next picture, 4 ms after the settled one at most."""
    rig = _rig(floor, lambda k: Success((40, 60)))
    record = _leg(floor, rig, FLOOR[1])
    _, pictures, _, layer, rec, _ = rig

    gaps = _gaps_after_a_settle(rec)
    assert record.outcome == LegOutcome.TIMED_OUT and len(layer.plays) >= 3
    assert len(gaps) >= 3 and all(0 < gap <= PERIOD_NS for gap in gaps), gaps
    assert pictures.waits and all(timeout_s == PARAMS.poll_s for _, timeout_s, _ in pictures.waits)
    assert all(after - before <= PERIOD_NS for before, _, after in pictures.waits)   # every wait ended at a picture


def test_without_a_waiter_the_walker_sleeps_poll_s(floor):
    """Review Focus 8: no waiter, the same rig, and the walker sleeps exactly as it did before Task 7: `min_settle_s`, then
    `poll_s` after the settle's first read, then `poll_s` after the STALE read that follows the settle."""
    rig = _rig(floor, lambda k: Success((40, 60)), waiter=False)
    _leg(floor, rig, FLOOR[1])
    _, pictures, clock, layer, rec, _ = rig

    assert pictures.waits == []
    assert clock.sleeps[:9] == [PARAMS.min_settle_s, PARAMS.poll_s, PARAMS.poll_s] * 3
    assert len(layer.plays) >= 3
    gaps = _gaps_after_a_settle(rec)
    assert gaps and all(gap == POLL_NS for gap in gaps), gaps   # the 50 ms the waiter takes away


# --- the settle ---------------------------------------------------------------------------------------------------------


def _walking(k: int):
    """The character walks right, one px per 80 ms, and stops after 250 ms: x 40 until u = 160 ms, 41 until 240, then 42."""
    u_ms = k * PERIOD_NS // 1_000_000
    return Success((40 + (u_ms >= 160) + (u_ms >= 240), 60))


def test_settle_keeps_its_reads_poll_s_apart(floor):
    """Review Focus 6, WALKFIX170-D1 A. Pictures every 4 ms, the position changes every 80 ms: two reads 4 ms apart are
    equal almost always, so a settle that read on every picture would end at the first pair, mid-walk. The settle reads at
    u = 150, 200, 250 and 300 ms (each after a `poll_s` sleep); the first two pairs straddle a change (at 160 and at 240),
    the third is equal, so it ends at 300 ms on the position the character stopped on, with its two equal reads 50 ms apart."""
    rig = _rig(floor, _walking, leg_timeout_s=0.4)
    record = _leg(floor, rig, FLOOR[1])
    _, pictures, _, _, _, _ = rig

    assert pictures.read_times(after_ns=MIN_SETTLE_NS)[:4] == [150 * 1_000_000, 200 * 1_000_000, 250 * 1_000_000,
                                                               300 * 1_000_000]
    first = record.moves[0]
    assert first.pos == (40, 60) and first.settled == (42, 60)   # not (40, 60) or (41, 60), where the character passed
    equal_reads = [t for t, _, result in pictures.reads if result == Success((42, 60))][:2]
    assert equal_reads[1] - equal_reads[0] >= POLL_NS


def test_settle_waits_for_the_next_picture_after_a_miss(floor):
    """After a failed read the settle waits for the next picture (the wait is the one that used to be a sleep), and the
    read after it is compared with the last successful read, which is more than `poll_s` older."""
    def shown(k):   # the picture at u = 200 ms is a miss
        return Failure("miss") if k == 50 else Success((40, 60))

    rig = _rig(floor, shown, leg_timeout_s=0.3)
    record = _leg(floor, rig, FLOOR[1])
    _, pictures, _, _, _, _ = rig

    reads = [(t - pictures.t0, result) for t, _, result in pictures.reads if t - pictures.t0 >= MIN_SETTLE_NS]
    assert [(t // 1_000_000, result) for t, result in reads[:3]] == [(150, Success((40, 60))), (200, Failure("miss")),
                                                                     (204, Success((40, 60)))]
    assert record.moves[0].settled == (40, 60)
    assert reads[2][0] - reads[0][0] >= POLL_NS


# --- the other waits ----------------------------------------------------------------------------------------------------


def test_a_placed_read_with_no_path_is_read_again_at_every_picture(cut_off):
    """Walker.py's "placed but no path" wait: (81, 60) is placed, the target is on the other platform. The walker looks at
    each picture (a read 4 ms after the one before), where it looked every 50 ms."""
    rig = _rig(cut_off, lambda k: Success((81, 60)), leg_timeout_s=0.5)
    _leg(cut_off, rig, (80, 10))
    _, pictures, _, layer, rec, _ = rig

    times = [e[1] for e in rec.of("no_plan")]
    assert layer.plays == [] and len(times) > 100
    assert {b - a for a, b in zip(times, times[1:])} == {PERIOD_NS}


def test_the_still_timer_counts_time_not_reads(ff2):
    """`standing_s` is 0.5 s of the clock. With a read at every picture there are 125 reads in it (not the 10 a 50 ms poll
    makes), and the walker still waits the whole half second before it attaches the character to the platform."""
    rig = _rig(ff2, lambda k: Success((70, 22)), leg_timeout_s=1.0)
    record = _leg(ff2, rig, TOP_RIGHT)
    _, _, _, _, rec, _ = rig

    first = record.moves[0]
    assert first.pos == (70, 27)
    first_no_plan = rec.of("no_plan")[0][1]
    assert STANDING_NS <= first.t_ns - first_no_plan < STANDING_NS + PERIOD_NS
    assert len([e for e in rec.of("no_plan") if e[1] < first.t_ns]) > 100


def test_the_rope_timer_counts_time_not_reads(ff2):
    """The same for `rope_still_s` (0.1 s): a read on rope 107 that cannot be placed gets its rope move after 0.1 s of the
    clock, with 25 reads in between."""
    rig = _rig(ff2, lambda k: Success(RIGHT_ROPE_READ), ropes=ZONES, leg_timeout_s=0.5, layer_of=RopeLayer)
    record = _leg(ff2, rig, (113, 60))
    _, _, _, layer, rec, _ = rig

    assert layer.keys[0][0] == "teleport"   # a rope move: the leg's first key
    first_no_plan = rec.of("no_plan")[0][1]
    first_move = rec.of("planned")[0][1]
    assert ROPE_STILL_NS <= first_move - first_no_plan < ROPE_STILL_NS + PERIOD_NS
    assert len([e for e in rec.of("no_plan") if e[1] < first_move]) > 20
    assert record.moves[0].t_ns == first_move
