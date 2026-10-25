"""Simulated link, clock and position source (plan task B10, contract C11).

`SimLink` duck-types `BleLink` for a real `InputLayer` (DC7): it decodes each key schedule into one `SimWorld` move
and advances a virtual `SimClock` by the link latency plus the schedule's total. `SimPositionSource` reads the
world's position at the clock's time, rounded to minimap pixels, and misses with a set probability.

Where the plan is silent: a play that fails from `fail_next` advances no time and uses no seq; `play` and
`release_all` share one seq counter (as `BleLink` does); a read draws one `rng.random()` every call, even with
`miss_p = 0`; a read rounds with Python's `round()`, so a half goes to the even integer (42.5 reads as 42); two keys
that go down at the same tick are ordered by their step order, as the board applies them; a key never released counts
as released at the schedule's end.
"""

from collections.abc import Sequence

import numpy as np
from returns.result import Failure, Result, Success

from src.input.link import InputError, SendReport
from src.input.protocol import Accepted, Done, DoneStatus, Key, Step
from src.route.sim.world import SimParams, SimWorld

_ARROWS = frozenset({Key.LEFT, Key.RIGHT, Key.UP, Key.DOWN})
_NS_PER_100US = 100_000


class SimClock:
    """Virtual time in ns; `sleep` advances it and never blocks."""

    def __init__(self, start_ns: int = 1_000_000_000) -> None:
        self._now_ns = int(start_ns)

    def now_ns(self) -> int:
        return self._now_ns

    def sleep(self, seconds: float) -> None:
        self.advance(round(seconds * 1e9))

    def advance(self, ns: int) -> None:
        if ns < 0:
            raise ValueError(f"virtual time cannot go backwards, got {ns} ns")
        self._now_ns += int(ns)


def _key_times(steps: Sequence[Step]) -> tuple[dict[Key, tuple[int, int]], dict[Key, int], int]:
    """Each key's first down as (100 us tick from the schedule's start, step index), its last up, and the total."""
    first_down: dict[Key, tuple[int, int]] = {}
    last_up: dict[Key, int] = {}
    t, prev = 0, 0
    for index, step in enumerate(steps):
        t += step.delta_100us
        mask = int(step.mask)
        for key in Key:
            bit = int(key)
            if mask & bit and not prev & bit:
                first_down.setdefault(key, (t, index))
            elif prev & bit and not mask & bit:
                last_up[key] = t
        prev = mask
    return first_down, last_up, t


class SimLink:
    """Duck-types `BleLink.play` / `release_all` for `InputLayer` (G16)."""

    def __init__(self, world: SimWorld, clock: SimClock, params: SimParams) -> None:
        self.world = world
        self.clock = clock
        self.params = params
        self.fail_next: list[InputError] = []  # tests push errors; play pops one and fails without moving
        self.plays = 0                          # every play call, failed ones included
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def play(self, steps: Sequence[Step]) -> Result[SendReport, InputError]:
        self.plays += 1
        if self.fail_next:
            return Failure(self.fail_next.pop(0))
        seq = self._next_seq()
        sent_ns = self.clock.now_ns()
        self.clock.advance(round(self.params.link_latency_s * 1e9))
        written_ns = self.clock.now_ns()
        first_down, last_up, total_100us = _key_times(steps)
        self.clock.advance(total_100us * _NS_PER_100US)
        t_end = self.clock.now_ns()
        self._apply(first_down, last_up, total_100us, t_end)
        return Success(SendReport(seq, sent_ns, written_ns, written_ns, t_end, Accepted(seq, 0),
                                  Done(seq, DoneStatus.OK, None, 0, 0, ())))

    def _apply(self, first_down: dict[Key, tuple[int, int]], last_up: dict[Key, int], total_100us: int,
               t_end: int) -> None:
        """Decode the keys pressed into one world move."""
        pressed = set(first_down)
        arrows = pressed & _ARROWS
        others = pressed - _ARROWS
        if len(arrows) == 1 and others == {Key.E}:
            (arrow,) = arrows
            if first_down[arrow] < first_down[Key.E]:
                self.world.teleport(arrow, t_end)
                return
        elif pressed == {Key.DOWN, Key.ALT} and first_down[Key.DOWN] < first_down[Key.ALT]:
            self.world.drop(t_end)
            return
        elif len(arrows) == 1 and not others:
            (arrow,) = arrows
            held_ms = (last_up.get(arrow, total_100us) - first_down[arrow][0]) / 10
            self.world.walk(arrow, held_ms, t_end)
            return
        self.world.nothing(t_end)

    def release_all(self) -> Result[SendReport, InputError]:
        seq = self._next_seq()
        now = self.clock.now_ns()
        return Success(SendReport(seq, now, now, None, now, None, Done(seq, DoneStatus.OK, None, 0, 0, ())))


class SimPositionSource:
    """The world's position at the clock's time, rounded to ints; `Failure("miss")` with `miss_p`."""

    def __init__(self, world: SimWorld, clock: SimClock, miss_p: float, rng: np.random.Generator) -> None:
        self.world = world
        self.clock = clock
        self.miss_p = miss_p
        self.rng = rng

    def read(self) -> Result[tuple[int, int], str]:
        if self.rng.random() < self.miss_p:
            return Failure("miss")
        x, y = self.world.position_at(self.clock.now_ns())
        return Success((round(x), round(y)))
