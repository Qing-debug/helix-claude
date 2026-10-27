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
- A sink `Failure` is printed to stderr once per run, the first one, whether it came from `write_leg` or `write_lap`.
- A lap record's `start_ns` is its first leg's `start_ns` and its `end_ns` is its last leg's `end_ns`; a leg that
  ends `STOPPED` or `LINK_DOWN` is written to the sink and counted in `RunSummary.legs`, its lap is not.
- `max_laps` of 0 (or less) walks the approach leg only and returns reason `"max_laps"`.
- The step observer (route view, contract V2) is optional: it is called at the V2 points of `walk_leg` on the walker's
  thread, and after its first exception it is reported once on stderr and not called again this run. With no observer
  nothing else happens: each call site is one `is not None` test.
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
from src.route.moves import execute
from src.route.planner import next_move
from src.route.route_graph import RouteGraph

STALE = "stale"  # the PositionSource Failure that means "no new frame yet"; any other Failure is a miss

_NS_PER_S = 1_000_000_000
_LINK_DOWN = frozenset({InputError.LINK_LOST, InputError.NOT_CONNECTED})
_GUARD_PERIOD_S = 0.05  # the stop guard sends release_all this often, in wall time (PB-D4)
_GUARD_JOIN_S = 1.0     # how long run waits for the stop guard to end


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
    reason: str  # "max_laps" | "stopped" | "link_down"


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
                if self._observer is not None:
                    self._emit(self._observer.read_failed, read_ns, read.failure())
                clock.sleep(params.poll_s)
                continue
            pos = read.unwrap()

            planned = next_move(self._rg, pos, dst, self._rng)  # 5
            if not is_successful(planned):
                off_graph += 1
                if self._observer is not None:
                    self._emit(self._observer.no_plan, read_ns, pos, planned.failure())
                clock.sleep(params.poll_s)
                continue
            decision = planned.unwrap()
            move = decision.move
            if move is None:
                outcome = LegOutcome.REACHED
                if self._observer is not None:
                    self._emit(self._observer.reached, read_ns, pos)
                break

            if decision.cost_to_go_s < best - params.progress_eps_s:  # 6
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
        if record.outcome in (LegOutcome.STOPPED, LegOutcome.LINK_DOWN):
            return RunSummary(laps, legs, _reason(record))

        lap = 1
        while self._config.max_laps is None or lap <= self._config.max_laps:
            records: list[LegRecord] = []
            for i in range(n):
                record = self.walk_leg(lap, i, rotation[i], rotation[(i + 1) % n])
                records.append(record)
                self._log(self._sink.write_leg(record))
                legs += 1
                if record.outcome in (LegOutcome.STOPPED, LegOutcome.LINK_DOWN):
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
            print(f"leg log write failed (not reported again this run): {written.failure()}", file=sys.stderr)

    def _emit(self, call: Callable[..., None], *args: object) -> None:
        """Call one observer method. Its first exception is printed to stderr once and the observer is dropped for the
        rest of the run (plan choice 2), so an observer bug never ends a walk."""
        try:
            call(*args)
        except Exception as exc:
            self._observer = None
            print(f"route view observer failed, not called again this run: {type(exc).__name__}: {exc}",
                  file=sys.stderr)

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
    return "stopped" if record.outcome == LegOutcome.STOPPED else "link_down"


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
