"""The basics the walker shares with what feeds it (cleanup piece W1-6, R30; moved out of `walker.py` unchanged).

`STALE`, the `Clock`, `PositionSource` and `StepObserver` protocols and `RealClock` were defined in `walker.py`; the
position source (`position.py`), the walk's runtime (`runtime.py`) and the measurement tool (`harness/measure_moves.py`)
import them from here, so they no longer reach into the walker for them. `walker.py` imports them back, so
`walker.STALE`, `walker.Clock`, `walker.PositionSource`, `walker.StepObserver` and `walker.RealClock` are the same
objects as these (`tests/route/test_ports.py`). This module imports the standard library and `returns` only.
"""

import time
from typing import Protocol

from returns.result import Result

STALE = "stale"  # the PositionSource Failure that means "no new frame yet"; any other Failure is a miss


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
