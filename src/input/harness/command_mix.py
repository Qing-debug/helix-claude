"""The soak's seeded command mix, as pure functions (W1-5; no clock, no sleep, no link, nothing from Windows).

`soak` re-exports every name here (`soak.Command`, `soak.plan_mix`, `soak.TAP_KEYS`, ...), and `probe` and the tests
read them through `soak`. `_streams(seed)` spawns the three generators of a run (the mix, the timing model, the fault
indexes); `command_mix` draws from the first, in the order it always had: the seeded mix of every seed is pinned by
`tests/input/test_soak.py::test_golden_mix_for_two_fixed_seeds_pins_the_rng_draw_order`.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from src.input.protocol import Key
from src.input.timing import TimingType

ARROWS = (Key.LEFT, Key.RIGHT, Key.UP, Key.DOWN)
TAP_KEYS = ARROWS + (Key.E, Key.SPACE, Key.ALT)  # Alt only alone: a tap (or jump / drop), never in a sequence
WALK_KEYS = (Key.LEFT, Key.RIGHT)  # walking is horizontal; the plan names no walk direction
SEQUENCE_KEYS = ARROWS + (Key.E, Key.SPACE)  # no Alt, so never Alt with Space
MIX = (("tap", 0.25), ("walk", 0.20), ("teleport", 0.25), ("drop", 0.10), ("jump", 0.05), ("sequence", 0.15))
WALK_T_MS = (300.0, 3000.0)  # T ~ U(300, 3000) ms
SEQUENCE_LEN = (2, 5)  # inclusive, uniform, keys drawn with repeats

@dataclass(frozen=True)
class Command:
    action: str  # the InputLayer method: tap, walk, teleport, drop, jump, sequence
    keys: tuple[Key, ...] = ()  # tap key, walk / teleport direction, or the sequence
    intended_ms: float | None = None  # a walk's T


def _streams(seed: int) -> list[np.random.Generator]:
    """Independent generators for the mix, the timing model and the fault indexes, all from one seed."""
    return [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(3)]


def command_mix(rng: np.random.Generator) -> Iterator[Command]:
    """The plan's mix, one command at a time, without end (a `--min-hours` run can outlast N)."""
    names = [a for a, _ in MIX]
    weights = [w for _, w in MIX]
    while True:
        action = names[rng.choice(len(names), p=weights)]
        if action == "tap":
            yield Command(action, (TAP_KEYS[rng.integers(len(TAP_KEYS))],))
        elif action == "walk":
            yield Command(action, (WALK_KEYS[rng.integers(len(WALK_KEYS))],), float(rng.uniform(*WALK_T_MS)))
        elif action == "teleport":
            yield Command(action, (ARROWS[rng.integers(len(ARROWS))],))
        elif action == "sequence":
            n = int(rng.integers(SEQUENCE_LEN[0], SEQUENCE_LEN[1] + 1))
            yield Command(action, tuple(SEQUENCE_KEYS[i] for i in rng.integers(len(SEQUENCE_KEYS), size=n)))
        else:
            yield Command(action)


def plan_mix(seed: int, n: int) -> list[Command]:
    """The first `n` commands of the mix for `seed` (the ones a run issues first)."""
    mix = command_mix(_streams(seed)[0])
    return [next(mix) for _ in range(n)]


def sample_counts(commands: list[Command]) -> Counter:
    """Chosen samples per TimingType that `commands` draw (a clipped sequence hold counts as the HOLD it draws)."""
    counts: Counter = Counter()
    for c in commands:
        counts[TimingType.REACTION] += 1  # every command has a stimulus
        if c.action in ("tap", "jump"):
            counts[TimingType.HOLD] += 1
        elif c.action == "walk":
            counts[TimingType.WALK] += 1
        elif c.action in ("teleport", "drop"):
            counts.update((TimingType.CHORD_OFFSET, TimingType.HOLD, TimingType.RELEASE_LAG))
        elif c.action == "sequence":
            counts[TimingType.HOLD] += len(c.keys)
            counts[TimingType.GAP] += len(c.keys) - 1
    return counts
