"""Step log writer (route view plan task RV-2, contract V3): `StepLog`, the walk's and the simulator's step observer,
which appends one `steps.jsonl` line per event from its own thread. The rules it follows are in `step_log`'s docstring;
`step_log` re-exports `StepLog`. It imports the standard library, `returns` and `step_log_format` only (plan hard rule 6).
"""

import math
import os
import queue
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.route.step_log_format import STEPS_FILE, RunHeader, event_line, header_line

_STOP = object()  # the writer thread's sentinel


def _print_error(text: str) -> None:
    """`StepLog`'s `log` default: the line on stderr, as the CLI prints it (its own copy: the step log modules import no
    other module of the route package)."""
    print(text, file=sys.stderr)


class StepLog:
    """The walk's step observer (contract V3; it satisfies `walker.StepObserver` structurally and imports nothing from
    the walker). Each event method only puts a tuple of plain values on a `queue.SimpleQueue`, whose `put` never
    blocks; the daemon thread `step-log` encodes each one with `event_line` and appends it to `steps.jsonl` with one
    `write` and a `flush` (no fsync per line, spec 5.2). After `close` every call is dropped (RV3-D2 A2).

    `frame_ns` (QB283 2d): a source of the stamp of the picture the walker's last good position read came from (the
    session passes `lambda: positions.frame_ns` for a `MinimapPositionSource`; the simulator passes none). With one,
    `planned` and `settled` ask it in their own call, on the walker's thread, where that attribute was just set, and
    queue its answer as the line's last value (`settled` with no position: None, without asking). Without one the
    lines are what they were before Task 6."""

    def __init__(self, *, log: Callable[[str], None] = _print_error,
                 frame_ns: Callable[[], int | None] | None = None) -> None:
        self._log = log  # the app run's log goes to the console box (route tab H71); the CLI's prints on stderr
        self._frame_ns = frame_ns
        self._queue: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._closed = False
        self._file: BinaryIO | None = None
        self._thread: threading.Thread | None = None
        self._failure: str | None = None
        self._close_result: Result[None, str] = Success(None)

    @property
    def failure(self) -> str | None:
        """The first write or encode failure so far, else None."""
        return self._failure

    def start(self, folder: Path, header: RunHeader) -> Result[None, str]:
        """Open `folder/steps.jsonl` exclusively (RV3-D3 A), write the header line and start the writer thread. Call
        once; a `Failure` starts nothing."""
        if self._closed:
            return Failure("step log already closed")
        if self._thread is not None:
            return Failure("step log already started")
        line = header_line(header)
        if not is_successful(line):
            return Failure(line.failure())
        path = Path(folder) / STEPS_FILE
        try:
            handle = path.open("xb")  # "x": fails if the file exists
        except OSError as e:
            return Failure(f"cannot open {path}: {e}")
        try:
            handle.write(line.unwrap())
            handle.flush()
        except OSError as e:
            try:
                handle.close()
            except OSError:
                pass
            message = f"cannot write the run line to {path}: {e}"
            try:
                path.unlink(missing_ok=True)  # this start created it ("x"), so removing it touches nothing else
            except OSError as gone:
                return Failure(f"{message} (and {path} could not be removed: {gone})")
            return Failure(message)
        self._file = handle
        self._thread = threading.Thread(target=self._drain, name="step-log", daemon=True)
        self._thread.start()
        return Success(None)

    def _put(self, *event: object, frame: bool = False, positioned: bool = True) -> None:
        """Queue `event` (`(ev, t_ns, *values)`) unless the log is closed. `frame`: the event takes `frame_ns` last
        (`planned`, `settled`) when there is a stamp source, asked here on the caller's thread after the closed check;
        an event with no position (`positioned` False) gets None without asking."""
        if self._closed:
            return
        if frame and self._frame_ns is not None:
            event = (*event, self._frame_ns() if positioned else None)
        self._queue.put(event)

    def leg(self, t_ns: int, lap: int, leg: int, src: tuple[int, int] | None, dst: tuple[int, int]) -> None:
        self._put("leg", t_ns, lap, leg, src, dst)

    def read_failed(self, t_ns: int, why: str) -> None:
        self._put("read_failed", t_ns, why)

    def no_plan(self, t_ns: int, pos: tuple[int, int], why: str) -> None:
        self._put("no_plan", t_ns, pos, why)

    def reached(self, t_ns: int, pos: tuple[int, int]) -> None:
        self._put("reached", t_ns, pos)

    def stall(self, t_ns: int, best_cost_s: float) -> None:
        # math.inf (no plan yet in this leg) is not strict JSON: it is written as null (plan choice 3)
        self._put("stall", t_ns, None if math.isinf(best_cost_s) else best_cost_s)

    def planned(self, t_ns: int, pos: tuple[int, int], kind: str, direction: str | None, walk_ms: float | None,
                cost_to_go_s: float, toward: tuple[int, int] | None) -> None:
        self._put("planned", t_ns, pos, kind, direction, walk_ms, cost_to_go_s, toward, frame=True)

    def sent(self, t_ns: int, input_name: str, reaction_ms: float | None, waited_ms: float | None,
             keys: tuple[tuple[int, int], ...]) -> None:
        self._put("sent", t_ns, input_name, reaction_ms, waited_ms, keys)

    def settled(self, t_ns: int, pos: tuple[int, int] | None, stopped: bool) -> None:
        self._put("settled", t_ns, pos, stopped, frame=True, positioned=pos is not None)

    def leg_end(self, t_ns: int, outcome: str, misses: int, off_graph: int, stalls: int) -> None:
        self._put("leg_end", t_ns, outcome, misses, off_graph, stalls)

    def end(self, t_ns: int, reason: str | None, code: int | None) -> None:
        """The run's last event (not a walker event: the walk's or the simulator's cleanup puts it)."""
        self._put("end", t_ns, reason, code)

    def close(self, join_s: float = 2.0) -> Result[None, str]:
        """Mark the log closed (every later call is dropped), end the writer thread and wait for it at most `join_s`.
        Idempotent; never raises. Before `start` it discards the queued events and returns `Success`."""
        if self._closed:
            return self._close_result
        self._closed = True
        thread = self._thread
        if thread is None:
            self._queue = queue.SimpleQueue()
            return self._close_result
        self._queue.put(_STOP)
        thread.join(join_s)  # join returns None either way; is_alive() tells a timeout (threading.py docstring)
        if self._failure is not None:
            self._close_result = Failure(self._failure)
        elif thread.is_alive():
            self._close_result = Failure(f"writer still busy after {join_s} s")
        return self._close_result

    def _drain(self) -> None:
        """The writer thread: one line per event until the sentinel, then flush, fsync and close the file. It catches
        `Exception` around each line, so it never dies with events left in the queue."""
        while True:
            event = self._queue.get()
            if event is _STOP:
                break
            try:
                self._write(event)
            except Exception as e:
                self._fail(f"{type(e).__name__}: {e}")
        handle = self._file
        if handle is None:
            return
        try:
            handle.flush()
            os.fsync(handle.fileno())
        except Exception as e:
            self._fail(f"{type(e).__name__}: {e}")
        finally:
            # closed in every path: on a full disk the flush above fails with the line still buffered, and the
            # BufferedWriter's own close then raises too but still closes the OS file
            try:
                handle.close()
            except Exception as e:
                self._fail(f"{type(e).__name__}: {e}")

    def _write(self, event: object) -> None:
        if self._failure is not None:
            return  # after the first failure the thread drains the queue without writing
        line = event_line(event if isinstance(event, tuple) else ())
        if not is_successful(line):
            self._fail(line.failure())
            return
        handle = self._file
        if handle is None:
            raise ValueError("the step log was not started")
        handle.write(line.unwrap())
        handle.flush()

    def _fail(self, message: str) -> None:
        """Keep the first failure and report it once through `log`."""
        if self._failure is None:
            self._failure = message
            self._log(f"step log write failed (not reported again this run): {message}")
