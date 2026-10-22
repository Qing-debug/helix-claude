"""Action builders (plan C3 / C4): turn timing draws into key schedules. Pure; no I/O.

Each builder draws from a `TimingModel`, quantises every drawn float to 100 us (`round(ms * 10)`) and lays the
key transitions on one integer timeline, so the steps of a Plan always add up to the logged wire values. A Sample
points at the two steps of `Plan.steps` that its interval spans.
"""
import math
from collections.abc import Sequence
from dataclasses import dataclass

from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.input.protocol import Key, Step, validate_schedule
from src.input.timing import Sample, TimingModel, TimingType

MAX_SEQUENCE_KEYS = 8  # two steps per key; protocol.MAX_STEPS = 16
CLIP_MARGIN_100US = 150  # 15 ms: a repeated key is lifted at least this long before its next down (C4)

_DOWN, _UP = True, False


@dataclass(frozen=True)
class Plan:
    name: str
    steps: tuple[Step, ...]
    samples: tuple[Sample, ...]


def _wire(ms: float) -> int:
    return round(ms * 10)


def _draw(model: TimingModel, method, *args) -> tuple[float, int]:
    """Call a model draw; return (value, Sample.index = draws before it, A-PF-D1)."""
    ms = method(*args)
    return float(ms), model.draws - 1


class _Builder:
    """Collects key events (absolute 100 us ticks) and the intervals that become Samples."""

    def __init__(self, model: TimingModel) -> None:
        self._model = model
        self._events: list[tuple[int, Key, bool]] = []
        self._spans: list[tuple[TimingType, float, int, int, int]] = []  # type, ms, index, from_ev, to_ev

    def event(self, t: int, key: Key, is_down: bool) -> int:
        self._events.append((t, key, is_down))
        return len(self._events) - 1

    def span(self, t: TimingType, ms: float, index: int, ev_from: int, ev_to: int) -> None:
        self._spans.append((t, ms, index, ev_from, ev_to))

    def build(self, name: str) -> Plan:
        # Time order; at one tick an up goes before a down (so a key is never seen down twice); otherwise
        # creation order.
        order = sorted(range(len(self._events)), key=lambda i: (self._events[i][0], self._events[i][2], i))
        pos = {ev: step for step, ev in enumerate(order)}
        steps: list[Step] = []
        state, prev_t = 0, 0
        for ev in order:
            t, key, is_down = self._events[ev]
            state = (state | int(key)) if is_down else (state & ~int(key))
            steps.append(Step(t - prev_t, Key(state)))
            prev_t = t
        samples = tuple(
            Sample(t, ms, index, _wire(ms), pos[f], pos[to]) for t, ms, index, f, to in self._spans
        )
        return Plan(name, tuple(steps), samples)


def _hold_plan(name: str, model: TimingModel, key: Key) -> Plan:
    b = _Builder(model)
    ms, i = _draw(model, model.hold)
    down = b.event(0, key, _DOWN)
    up = b.event(_wire(ms), key, _UP)
    b.span(TimingType.HOLD, ms, i, down, up)
    return b.build(name)


def _chord_plan(name: str, model: TimingModel, first: Key, second: Key) -> Plan:
    """`first` down, `second` down after CHORD_OFFSET, `second` up after HOLD, `first` up after RELEASE_LAG."""
    b = _Builder(model)
    c_ms, c_i = _draw(model, model.chord_offset)
    h_ms, h_i = _draw(model, model.hold)
    r_ms, r_i = _draw(model, model.release_lag)
    t_second_down = _wire(c_ms)
    t_second_up = t_second_down + _wire(h_ms)
    first_down = b.event(0, first, _DOWN)
    second_down = b.event(t_second_down, second, _DOWN)
    second_up = b.event(t_second_up, second, _UP)
    first_up = b.event(t_second_up + _wire(r_ms), first, _UP)
    b.span(TimingType.CHORD_OFFSET, c_ms, c_i, first_down, second_down)
    b.span(TimingType.HOLD, h_ms, h_i, second_down, second_up)
    b.span(TimingType.RELEASE_LAG, r_ms, r_i, second_up, first_up)
    return b.build(name)


def tap(model: TimingModel, key: Key) -> Plan:
    return _hold_plan("tap", model, key)


def jump(model: TimingModel) -> Plan:
    return _hold_plan("jump", model, Key.ALT)


def teleport(model: TimingModel, direction: Key) -> Plan:
    return _chord_plan("teleport", model, direction, Key.E)


def drop(model: TimingModel) -> Plan:
    return _chord_plan("drop", model, Key.DOWN, Key.ALT)


def walk(model: TimingModel, direction: Key, intended_ms: float) -> Result[Plan, str]:
    # TimingModel.walk raises ValueError on these (A-P2); a builder returns a Failure instead.
    if not (math.isfinite(intended_ms) and intended_ms >= 0):
        return Failure(f"walk: intended_ms must be finite and >= 0, got {intended_ms!r}")
    b = _Builder(model)
    ms, i = _draw(model, model.walk, intended_ms)
    down = b.event(0, direction, _DOWN)
    up = b.event(_wire(ms), direction, _UP)
    b.span(TimingType.WALK, ms, i, down, up)
    plan = b.build("walk")
    checked = validate_schedule(plan.steps)  # C1 per-step cap; returned unchanged
    if not is_successful(checked):
        return checked
    return Success(plan)


def sequence(model: TimingModel, keys: Sequence[Key]) -> Result[Plan, str]:
    """Press `keys` in order: key downs GAP apart, each held HOLD; a repeat is lifted early (C4 clip rule)."""
    if len(keys) > MAX_SEQUENCE_KEYS:
        return Failure(
            f"sequence: more than {MAX_SEQUENCE_KEYS} keys ({len(keys)}); "
            "each key takes a down and an up step and a PLAY has at most 16"
        )
    # Draw order: hold_0, gap_0, hold_1, gap_1, ..., hold_last.
    holds: list[tuple[float, int]] = []
    gaps: list[tuple[float, int]] = []
    for n in range(len(keys)):
        holds.append(_draw(model, model.hold))
        if n < len(keys) - 1:
            gaps.append(_draw(model, model.gap))
    down_t = [0]
    for ms, _ in gaps:
        down_t.append(down_t[-1] + _wire(ms))

    b = _Builder(model)
    down_ev = [b.event(down_t[n], key, _DOWN) for n, key in enumerate(keys)]
    for n, key in enumerate(keys):
        h_ms, h_i = holds[n]
        up_t = down_t[n] + _wire(h_ms)
        kind = TimingType.HOLD
        # C4 says "key i+1 equals key i"; the same lift-early applies to the next later press of the same key,
        # otherwise the key would go down while still down and the schedule would be invalid (DECISION, see notes).
        nxt = next((j for j in range(n + 1, len(keys)) if keys[j] == key), None)
        if nxt is not None and up_t > down_t[nxt] - CLIP_MARGIN_100US:
            up_t = down_t[nxt] - CLIP_MARGIN_100US
            kind = TimingType.HOLD_CLIPPED
        up_ev = b.event(up_t, key, _UP)
        b.span(kind, h_ms, h_i, down_ev[n], up_ev)
        if n < len(keys) - 1:
            g_ms, g_i = gaps[n]
            b.span(TimingType.GAP, g_ms, g_i, down_ev[n], down_ev[n + 1])
    plan = b.build("sequence")
    checked = validate_schedule(plan.steps)
    if not is_successful(checked):
        return checked
    return Success(plan)
