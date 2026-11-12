"""Walker: one leg of the route as a closed loop (plan task B12, contract C10; design choice DC4).

`RouteWalker.walk_leg` reads the character's position, asks the planner for the next move toward the leg's target,
plays it through the input layer, waits for the character to settle, and repeats from the measured position until
the target is reached, the leg times out, the stop flag is set, or the link is down. It also counts position misses,
off-graph positions and stalls (no new best cost-to-go for `stall_s`). `run` cycles the rotation in laps and writes
the leg and lap records; `stop_now` stops it from another thread (plan task B13, contract C10, decision PB-D4).

Where the plan is silent:
- `MoveEntry.t_ns` is the clock reading taken right after the position read that the move was planned from, the same
  reading that is passed as the layer's `stimulus_ns` when `use_stimulus` is on.
- The settle window (`settle_timeout_s`) is measured from the moment the move's layer call returns, so it includes
  the `min_settle_s` sleep; polling stops once that much time has passed.
- In the settle, a failed read (a miss or a stale frame) is skipped: it is not counted in `misses`, and "two
  consecutive successful reads" compares each successful read with the previous successful read of the same settle.
- A leg's stop flag is checked before every settle poll, not during the `min_settle_s` sleep, so a stop is noticed
  within `min_settle_s`.
- `walk_leg` returns its `LegRecord` and does not write it: `run` writes the leg records to the sink.
- `run` is one-shot: the stop flag and the "run finished" flag are never cleared, so a walker runs once. A stop
  before `run` starts ends it at the first leg.
- `run` releases the keys once on every return path, the empty-rotation `Failure` included (nothing was sent, so the
  call is harmless), and also when an exception leaves it.
- A sink `Failure` is reported through `log` (by default on stderr) once per run, the first one, whether it came from
  `write_leg` or `write_lap`.
- A lap record's `start_ns` is its first leg's `start_ns` and its `end_ns` is its last leg's `end_ns`; a leg that
  ends `STOPPED`, `LINK_DOWN` or `OFF_MAP` is written to the sink and counted in `RunSummary.legs`, its lap is not.
- `max_laps` of 0 (or less) walks the approach leg only and returns reason `"max_laps"`.
- The still rule (off-graph fix, QB213/QB215): while the character cannot be placed on the map, `_Stillness` times how
  long the same position has been read in a row (by the clock; one new position or one miss restarts it, a STALE read
  does not). At `standing_s` the position is attached to the nearest platform line within `standing_band_px`
  (`locate.attach_standing`) and the walker plans from that point; with no line that close it logs one line and ends
  the leg `OFF_MAP` (the run ends too, as for `STOPPED`). A character the strict rules place (`locate.attach`) never
  starts the timer, whether or not the planner finds a path, and every move sent restarts it.
- The rope rule (rope fix, H88, H93): a read that neither the planner nor `locate.attach` places, inside a marked rope's
  zone (`RopeZones.at`, one dict lookup, asked only for such a read), held `rope_still_s` gets `planner.rope_move`'s
  moves in order: blinks to the nearer platform in reach, then one jump off; a move sent restarts the 0.1 s hold. With
  the order used up the leg ends `OFF_MAP` with one line. A rope move is not progress (`best` is unchanged) and never
  counts toward a stall by itself; its order restarts when the read is placed by `next_move` or `attach`, or is on
  another rope. A read outside every zone follows the still rule above, unchanged.
- The step observer (route view, contract V2) is optional: it is called at the V2 points of `walk_leg` on the walker's
  thread, and after its first exception it is reported once through `log` and not called again this run. With no
  observer nothing else happens: each call site is one `is not None` test.
"""

import math
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.controller.node import Node
from src.input.input_layer import ActionRecord, InputLayer
from src.input.link import InputError
from src.route.leg_log import LapRecord, LegOutcome, LegRecord, LegSink, MoveEntry
from src.route.locate import attach, attach_standing
from src.route.moves import execute
from src.route.planner import Decision, next_move, rope_move
from src.route.ropes import RopeZones
from src.route.route_graph import RouteGraph

STALE = "stale"  # the PositionSource Failure that means "no new frame yet"; any other Failure is a miss

_NS_PER_S = 1_000_000_000
_LINK_DOWN = frozenset({InputError.LINK_LOST, InputError.NOT_CONNECTED})
_ENDS_THE_RUN = (LegOutcome.STOPPED, LegOutcome.LINK_DOWN, LegOutcome.OFF_MAP)
_GUARD_PERIOD_S = 0.05  # the stop guard sends release_all this often, in wall time (PB-D4)
_GUARD_JOIN_S = 1.0     # how long run waits for the stop guard to end


def _print_error(text: str) -> None:
    """`RouteWalker`'s `log` default: the line on stderr, as the CLI prints it."""
    print(text, file=sys.stderr)


class Clock(Protocol):
    def now_ns(self) -> int: ...

    def sleep(self, seconds: float) -> None: ...


class PositionSource(Protocol):
    def read(self) -> Result[tuple[int, int], str]: ...  # Failure(STALE) = no new frame; any other Failure = miss


class StepObserver(Protocol):
    """What `walk_leg` reports as it goes (route view, contract V2): plain values, called on the walker's thread."""

    def leg(self, t_ns: int, lap: int, leg: int, src: tuple[int, int] | None, dst: tuple[int, int]) -> None: ...

    def read_failed(self, t_ns: int, why: str) -> None: ...

    def no_plan(self, t_ns: int, pos: tuple[int, int], why: str) -> None: ...

    def reached(self, t_ns: int, pos: tuple[int, int]) -> None: ...

    def stall(self, t_ns: int, best_cost_s: float) -> None: ...  # math.inf before the leg's first plan

    def planned(self, t_ns: int, pos: tuple[int, int], kind: str, direction: str | None, walk_ms: float | None,
                cost_to_go_s: float, toward: tuple[int, int] | None) -> None: ...

    def sent(self, t_ns: int, input_name: str, reaction_ms: float | None, waited_ms: float | None,
             keys: tuple[tuple[int, int], ...]) -> None: ...

    def settled(self, t_ns: int, pos: tuple[int, int] | None, stopped: bool) -> None: ...

    def leg_end(self, t_ns: int, outcome: str, misses: int, off_graph: int, stalls: int) -> None: ...


class _Stillness:
    """How long the same position has been read in a row. `see` takes a read and the clock's time for it and returns
    the seconds since this position was first read in the current run of it; a different position starts a new run at
    0, and so does the first read after `reset`. Time, not poll counts: the walker's poll period is not the rule."""

    def __init__(self) -> None:
        self._pos: tuple[int, int] | None = None
        self._since_ns = 0

    def see(self, pos: tuple[int, int], t_ns: int) -> float:
        if pos != self._pos:
            self._pos = pos
            self._since_ns = t_ns
        return (t_ns - self._since_ns) / _NS_PER_S

    def reset(self) -> None:
        self._pos = None


class RealClock:
    """`now_ns` is `time.perf_counter_ns`, the clock `InputLayer` measures a stimulus on; `sleep` is `time.sleep`."""

    def now_ns(self) -> int:
        return time.perf_counter_ns()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True)
class WalkerConfig:
    use_stimulus: bool = True  # pass stimulus_ns to InputLayer (runtime); the simulator uses False
    leg_timeout_s: float | None = None
    max_laps: int | None = None


@dataclass(frozen=True)
class RunSummary:
    laps_completed: int
    legs: int
    reason: str  # "max_laps" | "stopped" | "link_down" | "off_map"


class RouteWalker:
    def __init__(
        self,
        rg: RouteGraph,
        rotation: Sequence[Node],
        layer: InputLayer,
        positions: PositionSource,
        clock: Clock,
        sink: LegSink,
        rng: np.random.Generator,
        config: WalkerConfig = WalkerConfig(),
        *,
        observer: StepObserver | None = None,
        log: Callable[[str], None] = _print_error,
        ropes: RopeZones = RopeZones.EMPTY,
    ) -> None:
        self._rg = rg
        self._rotation = tuple(rotation)
        self._layer = layer
        self._positions = positions
        self._clock = clock
        self._sink = sink
        self._rng = rng
        self._config = config
        self._stop = threading.Event()  # set by stop_now; walk_leg only reads it
        self._finished = threading.Event()  # "run finished": set once run is about to return
        self._lock = threading.Lock()  # makes "start the stop guard" and "set run finished" one step each
        self._guard: threading.Thread | None = None
        self._sink_failure_printed = False
        self._observer = observer
        self._log_line = log  # the app run's log goes to the console box (route tab H71); `_log` is the sink check
        self._ropes = ropes  # the map's rope zones (H88); `RopeZones.EMPTY` for a map with no rope file

    def walk_leg(self, lap: int, leg: int, src: Node | None, dst: Node) -> LegRecord:
        """Walk from the character's current position to `dst`; the record says how the leg ended."""
        params = self._rg.params
        clock = self._clock
        timeout_ns = None if self._config.leg_timeout_s is None else self._config.leg_timeout_s * _NS_PER_S
        stall_ns = params.stall_s * _NS_PER_S
        start_ns = clock.now_ns()
        if self._observer is not None:
            self._emit(self._observer.leg, start_ns, lap, leg, None if src is None else src.minimap_position,
                       dst.minimap_position)
        best = math.inf
        last_progress = start_ns
        entries: list[MoveEntry] = []
        misses = off_graph = stalls = 0
        still = _Stillness()  # the character cannot be placed on the map: how long it has read the same
        rope_at: int | None = None  # the rope the last rope move was for (an index into the zones), None before one
        rope_tries = 0  # how many moves of rope_move's order have been sent for it
        sent_from: tuple[int, int] | None = None  # the read the last move was sent from; the same read again: no-op

        while True:
            if self._stop.is_set():  # 1
                outcome = LegOutcome.STOPPED
                break
            now = clock.now_ns()
            if timeout_ns is not None and now - start_ns > timeout_ns:  # 2
                outcome = LegOutcome.TIMED_OUT
                break
            if now - last_progress > stall_ns:  # 3
                stalls += 1
                last_progress = now
                if self._observer is not None:
                    self._emit(self._observer.stall, now, best)

            read = self._positions.read()  # 4
            read_ns = clock.now_ns()
            if not is_successful(read):
                if read.failure() != STALE:
                    misses += 1
                    still.reset()
                if self._observer is not None:
                    self._emit(self._observer.read_failed, read_ns, read.failure())
                clock.sleep(params.poll_s)
                continue
            pos = read_pos = read.unwrap()

            planned = next_move(self._rg, pos, dst, self._rng)  # 5
            on_rope = False  # the move below is a rope move: it is not the planner's, and it is not progress
            rope = None  # the marked rope this pass gives the rope rule's move for, if any
            if is_successful(planned):
                if pos == sent_from:  # the move sent from this very read left it the same: it did nothing (C10, H93)
                    rope = self._ropes.at(pos)  # asked only after a no-op: a placed read that moved costs no lookup
                if rope is None:
                    still.reset()
                    rope_at, rope_tries = None, 0
            else:
                off_graph += 1
                if self._observer is not None:
                    self._emit(self._observer.no_plan, read_ns, pos, planned.failure())
                if is_successful(attach(self._rg, pos)):  # placed by A1-A3, but no path: waiting is as today
                    still.reset()
                    rope_at, rope_tries = None, 0
                    clock.sleep(params.poll_s)
                    continue
                rope = self._ropes.at(pos)  # only now: a read the map places never asks the zones
            if rope is not None:  # on a marked rope (H88), or a placed read there that a move left unchanged (C10)
                if still.see(pos, read_ns) < params.rope_still_s:
                    clock.sleep(params.poll_s)
                    continue
                if rope != rope_at:
                    rope_at, rope_tries = rope, 0
                mark = self._ropes.mark(rope)
                rope_choice = rope_move(mark, pos, dst, rope_tries, self._rg.reach_v, params.rope_blink_tries)
                if rope_choice is None and is_successful(planned):
                    # the order is used up, but the map places this read: the run goes on (ROPE158-C10B-p2-D1). The order
                    # starts again and the planner's move is sent; a read the map cannot place still ends the run below.
                    rope_at, rope_tries = None, 0
                elif rope_choice is None:
                    self._log_line(f"the character is on the rope at x {mark.x}, at ({pos[0]}, {pos[1]}), and "
                                   "could not get off it; ending the run")
                    outcome = LegOutcome.OFF_MAP
                    break
                else:
                    rope_tries += 1
                    planned = Success(Decision(rope_choice, best if math.isfinite(best) else 0.0, None))
                    on_rope = True
            elif not is_successful(planned):
                if still.see(pos, read_ns) < params.standing_s:
                    clock.sleep(params.poll_s)
                    continue
                snapped = attach_standing(self._rg, pos)  # standing where the map cannot place it
                if not is_successful(snapped):
                    self._log_line(f"the character is {snapped.failure()}; ending the run")
                    outcome = LegOutcome.OFF_MAP
                    break
                pos = snapped.unwrap()
                rope_at, rope_tries = None, 0  # standing on a platform line: off the rope, so its order starts again
                planned = next_move(self._rg, pos, dst, self._rng)
                if not is_successful(planned):
                    clock.sleep(params.poll_s)
                    continue
            decision = planned.unwrap()
            move = decision.move
            if move is None:
                outcome = LegOutcome.REACHED
                if self._observer is not None:
                    self._emit(self._observer.reached, read_ns, pos)
                break

            if on_rope:  # 6: a rope move is never progress, and it never trips the stall count on its own
                last_progress = now
            elif decision.cost_to_go_s < best - params.progress_eps_s:
                best = decision.cost_to_go_s
                last_progress = now

            if self._stop.is_set():  # 7
                outcome = LegOutcome.STOPPED
                break
            if self._observer is not None:
                self._emit(
                    self._observer.planned, read_ns, pos, move.kind.value,
                    None if move.direction is None else move.direction.name, move.walk_ms, decision.cost_to_go_s,
                    None if decision.toward is None else decision.toward.minimap_position,
                )
            stimulus_ns = read_ns if self._config.use_stimulus else None
            sent = execute(self._layer, move, stimulus_ns)
            failure = None if is_successful(sent) else sent.failure()
            still.reset()  # a move was sent: a no-op needs another 0.5 s in a row before the next loose attach
            # the read, not a snapped point: the next read is compared with it. A send that failed pressed no key, so
            # the same read after it is not a move that did nothing (C10 option B, ROPE159-C10B-F1 M3)
            sent_from = read_pos if failure is None else None
            if self._observer is not None:
                self._emit(self._observer.sent, clock.now_ns(), *_sent_values(sent))
            entry = MoveEntry(
                t_ns=read_ns,
                pos=pos,
                kind=move.kind.value,
                direction=None if move.direction is None else move.direction.name,
                walk_ms=move.walk_ms,
                cost_to_go_s=decision.cost_to_go_s,
                input="ok" if failure is None else failure.name,
                settled=None,
            )
            if failure in _LINK_DOWN:
                entries.append(entry)
                outcome = LegOutcome.LINK_DOWN
                break

            settled, stopped = self._settle()  # 8
            if self._observer is not None:
                self._emit(self._observer.settled, clock.now_ns(), settled, stopped)
            entries.append(replace(entry, settled=settled))
            if stopped:
                outcome = LegOutcome.STOPPED
                break

        record = LegRecord(
            lap=lap,
            leg=leg,
            src=None if src is None else src.minimap_position,
            dst=dst.minimap_position,
            start_ns=start_ns,
            end_ns=clock.now_ns(),
            outcome=outcome,
            moves=tuple(entries),
            misses=misses,
            off_graph=off_graph,
            stalls=stalls,
        )
        if self._observer is not None:
            self._emit(self._observer.leg_end, record.end_ns, record.outcome.value, record.misses, record.off_graph,
                       record.stalls)
        return record

    def run(self) -> Result[RunSummary, str]:
        """Walk the approach leg (lap 0), then laps 1, 2, ... of the rotation until stopped, the link is down or
        `max_laps` laps are done. Returns `Failure` for an empty rotation."""
        try:
            if not self._rotation:
                return Failure("rotation is empty: there is no first node to walk to")
            return Success(self._run_laps())
        finally:
            try:
                self._layer.release_all()
            finally:
                with self._lock:
                    self._finished.set()
                    guard = self._guard
                if guard is not None:
                    guard.join(timeout=_GUARD_JOIN_S)

    def stop_now(self) -> None:
        """Stop the run from any thread, any number of times: the walker sees the flag at its next check, the keys
        are released now (cutting a `play` in flight), and a daemon guard thread keeps releasing them every 50 ms
        until `run` has finished, so a move that starts inside `InputLayer`'s reaction wait is cut too (PB-D4)."""
        self._stop.set()
        self._layer.release_all()
        with self._lock:
            if self._guard is None and not self._finished.is_set():
                self._guard = threading.Thread(target=self._guard_loop, name="route-stop-guard", daemon=True)
                self._guard.start()

    def _guard_loop(self) -> None:
        while True:
            time.sleep(_GUARD_PERIOD_S)
            if self._finished.is_set():
                return
            self._layer.release_all()

    def _run_laps(self) -> RunSummary:
        rotation = self._rotation
        n = len(rotation)
        self._sink_failure_printed = False
        legs = laps = 0

        record = self.walk_leg(0, 0, None, rotation[0])
        self._log(self._sink.write_leg(record))
        legs += 1
        if record.outcome in _ENDS_THE_RUN:
            return RunSummary(laps, legs, _reason(record))

        lap = 1
        while self._config.max_laps is None or lap <= self._config.max_laps:
            records: list[LegRecord] = []
            for i in range(n):
                record = self.walk_leg(lap, i, rotation[i], rotation[(i + 1) % n])
                records.append(record)
                self._log(self._sink.write_leg(record))
                legs += 1
                if record.outcome in _ENDS_THE_RUN:
                    return RunSummary(laps, legs, _reason(record))
            all_reached = all(r.outcome == LegOutcome.REACHED for r in records)
            self._log(self._sink.write_lap(LapRecord(lap, records[0].start_ns, records[-1].end_ns, n, all_reached)))
            laps += 1
            lap += 1
        return RunSummary(laps, legs, "max_laps")

    def _log(self, written: Result[None, str]) -> None:
        """A sink `Failure` is reported once per run and the walk goes on."""
        if not is_successful(written) and not self._sink_failure_printed:
            self._sink_failure_printed = True
            self._log_line(f"leg log write failed (not reported again this run): {written.failure()}")

    def _emit(self, call: Callable[..., None], *args: object) -> None:
        """Call one observer method. Its first exception is reported through `log` once and the observer is dropped
        for the rest of the run (plan choice 2), so an observer bug never ends a walk."""
        try:
            call(*args)
        except Exception as exc:
            self._observer = None
            self._log_line(f"route view observer failed, not called again this run: {type(exc).__name__}: {exc}")

    def _settle(self) -> tuple[tuple[int, int] | None, bool]:
        """Wait for the character to stop moving after a move.

        Sleeps `min_settle_s`, then polls every `poll_s` until two consecutive successful reads are equal (that
        position is the result) or `settle_timeout_s` has passed since the settle began (the last successful read, or
        None). Returns `(settled, stopped)`; `stopped` is True when the stop flag was set before a poll.
        """
        params = self._rg.params
        clock = self._clock
        start_ns = clock.now_ns()
        timeout_ns = params.settle_timeout_s * _NS_PER_S
        clock.sleep(params.min_settle_s)
        last: tuple[int, int] | None = None
        while clock.now_ns() - start_ns < timeout_ns:
            if self._stop.is_set():
                return last, True
            read = self._positions.read()
            if is_successful(read):
                current = read.unwrap()
                if current == last:
                    return current, False
                last = current
            clock.sleep(params.poll_s)
        return last, False


def _reason(record: LegRecord) -> str:
    """The `RunSummary.reason` for a leg that ends the run."""
    if record.outcome == LegOutcome.STOPPED:
        return "stopped"
    return "off_map" if record.outcome == LegOutcome.OFF_MAP else "link_down"


def _sent_values(
    sent: Result[ActionRecord, InputError],
) -> tuple[str, float | None, float | None, tuple[tuple[int, int], ...]]:
    """The `sent` observer values after `execute` (contract V2): `input`, `reaction_ms`, `waited_ms` and `keys`. On
    success they are `"ok"`, the reaction draw's ms (None without a stimulus), the wait and the played steps as
    `(delta_100us, mask)`; on failure the error's name, None, None and `()`."""
    if not is_successful(sent):
        return sent.failure().name, None, None, ()
    rec = sent.unwrap()
    reaction_ms = None if rec.reaction is None else rec.reaction.ms
    return "ok", reaction_ms, rec.waited_ms, tuple((step.delta_100us, int(step.mask)) for step in rec.plan.steps)
