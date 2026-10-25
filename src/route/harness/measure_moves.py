"""Movement measurement tool (plan task B20): measure Kanna's moves in the game, for the supervised session (B21 step 3).

    python -m src.route.harness.measure_moves --supervised --out data/route/measure/<UTC stamp> [--reps 10]

Each measured move is one `MoveSample`, appended as one JSON object per line to `<out>/measure.jsonl`;
`summarize` turns the samples into the reach, walk-speed and settle-time numbers B21 steps 4 and 5 compare with
`Kanna`, `MovementParams` and `SimParams`.

The procedure (plan B20): start the minimap capture, wait for the first frame and the character, connect the link,
start the stop control (F12, Ctrl+C), then run the phases. Before each phase the tool prints what to do and waits for
Enter, then counts down while the game window is focused:
1. Kanna on the floor near x 80: `reps` teleports alternating RIGHT/LEFT, then `reps` walks of 2000 ms alternating
   RIGHT/LEFT (no prompt between the two).
2. Kanna under open space: `reps` teleports UP; the smallest y read during each settle is its `peak_y`.
3. Kanna on a droppable platform, prompted before every move (she ends below it): `reps // 2` teleports DOWN, then
   `reps // 2` drops.
Each sample is appended to `<out>/measure.jsonl` and printed; at the end `summarize` is printed and written to
`<out>/summary.json`, also after a stop or a failure. One cleanup runs on every exit path, in walk's order:
release_all, link.close, stop control close (`runtime.run_cleanup`).

Exit codes, with the walk CLI's meanings: 0 done, or stopped (F12, Ctrl+C, or stdin closed at a prompt) · 1 the link
went down during a move · 2 usage (no --supervised, a bad --reps, an --out that is a file or already holds
measure.jsonl or summary.json, an --out that cannot be created) · 3 no first frame or no character found · 4 connect
failed · 5 the character icon failed to load · 6 a move failed otherwise · 7 the procedure raised an exception · 9 the
stop control failed to start · 10 the character was lost: no settled position at the start of a phase or after a
skipped move.

Where the plan is silent:
- `MoveSample.kind` is a `MoveKind` value (`"teleport"`, `"walk"`, `"drop"`); `direction` is the `Key` name
  (`"LEFT"`, `"RIGHT"`, `"UP"`, `"DOWN"`, as the leg log's `dir`) or `None` for a drop (B20-D1 ruling B1). Positions
  are minimap pixels, top-left origin.
- The vertical reach of a teleport UP is `before[1] - peak_y` (y grows downwards); a rise of 2 px or less (no peak
  above the start) is left out.
- A move that went 2 px or less along its axis did not happen (a no-op, as the sim's NO_OP) and is left out of the
  reach means and the walk speed; `reach_h_n` shows how many teleports were kept (B20-D1 ruling B2, a plan B20
  amendment).
- `teleport_settle_s` is the median over the flat horizontal teleports kept for the reach; the others include a fall
  (B20-D1 ruling B3).
- `reach_h_sd` is the sample standard deviation (`statistics.stdev`). A key whose samples are missing is `None`
  (`reach_h_sd` also when fewer than 2 horizontal teleports are kept).
- `--reps` must be at least 2, so every phase (including the `reps // 2` teleport-downs and drops) has a sample.
- The default `--out` is `data/route/measure/<UTC stamp>` under the repo root; a used `--out` is refused as the
  walk CLI refuses one (decision B19-D2, `runtime.check_out`).
- Settle (B20-D2): after a move's layer call returns, poll every `_POLL_S` (10 ms) until a successful read shows the
  position first read at least `_STABLE_S` earlier, with no other position read in between; that position is
  `after`, and `settle_s` runs from the layer call's return to the first read of it. A failed read (a miss, or no new
  frame) neither confirms nor resets it, so a gap in the frame feed never settles a move. A move that has not
  settled within `_SETTLE_TIMEOUT_S` gives no sample (a line is printed) and the next move's `before` is settled
  afresh.
- `before` is the previous move's `after`; the first move of a phase, and a move after a skipped one, settles first.
- `held_ms` is the hold the timing model drew for the walk (the plan's `WALK` sample), not the intended 2000 ms.
- Moves are sent without a stimulus (no reaction wait); the timing model's generator is seeded from the OS.
- A stop sets an event that every prompt, countdown, move and settle poll checks, and starts a guard thread that
  sends `release_all` every `_GUARD_S` until the cleanup, so no key stays held when a move was in flight (as the
  walker's stop guard, PB-D4).
- An Enter read within `_stray_s` of a prompt was typed before it (a stray second Enter, buffered by the console):
  it is dropped with a line saying so, and the prompt waits for the next one (B20 review m2).
"""

import argparse
import json
import statistics
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.input.input_layer import ActionRecord, InputLayer
from src.input.link import BleLink, InputError
from src.input.protocol import Key
from src.input.timing import TimingModel, TimingType
from src.route import runtime
from src.route.planner import MoveKind
from src.route.walker import Clock, PositionSource, RealClock

_REPO = Path(__file__).resolve().parents[3]
MEASURE_FILE = "measure.jsonl"
_SUMMARY_FILE = "summary.json"
_OUT_FILES = (MEASURE_FILE, _SUMMARY_FILE)
_FLAT_DY_PX = 2            # a horizontal teleport counts towards the reach only if |dy| <= this (plan B20)
_MIN_REPS = 2
_NO_MOVE_PX = 2            # a move of this or less along its axis did not happen (a no-op; B20-D1 ruling B2)
_HORIZONTAL = frozenset({Key.LEFT.name, Key.RIGHT.name})
_WALK_MS = 2000.0          # plan B20
_POLL_S = 0.01             # settle polling period (plan B20: every 10 ms)
_STABLE_S = 0.5            # a position read unchanged this long is settled (B20-D2)
_SETTLE_TIMEOUT_S = 5.0    # a move not settled within this gives no sample (B20-D2)
_FIRST_FRAME_S = 30.0      # as walk.py
_GUARD_S = 0.05            # the stop guard's release_all period (as the walker's, PB-D4)
_GUARD_JOIN_S = 2.0
_PROMPT_POLL_S = 0.05      # how often a prompt checks for a stop
_LINK_DOWN = frozenset({InputError.LINK_LOST, InputError.NOT_CONNECTED})
_NS_PER_S = 1_000_000_000

_countdown_s = 5.0
_stray_s = 0.3  # an Enter read sooner than this after a prompt was typed before it; no one places Kanna this fast

_FLOOR = ("Put Kanna on the floor near x 80, with open floor on both sides. Measuring horizontal teleports, then "
          "walks.")
_OPEN = "Put Kanna on the floor under open space (nothing above her). Measuring teleports UP."
_PLATFORM = "Put Kanna on a platform she can drop through, with floor below."


@dataclass(frozen=True)
class MoveSample:
    kind: str
    direction: str | None
    before: tuple[int, int]
    after: tuple[int, int]
    peak_y: int | None
    settle_s: float
    held_ms: float | None


def sample_to_json(sample: MoveSample) -> dict:
    """The `measure.jsonl` object of one sample: its fields by name, positions as `[x, y]` lists."""
    return asdict(sample)


def append_sample(path: Path, sample: MoveSample) -> None:
    """Append one sample as one JSON line to `path` (created if missing)."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sample_to_json(sample)) + "\n")


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def summarize(samples: Sequence[MoveSample]) -> dict:
    """The plan's summary keys (B20): horizontal reach mean/sd/n, vertical peak reach, walk speed, settle medians."""
    teleports = [s for s in samples if s.kind == MoveKind.TELEPORT]
    flat = [s for s in teleports if s.direction in _HORIZONTAL and abs(s.after[1] - s.before[1]) <= _FLAT_DY_PX
            and abs(s.after[0] - s.before[0]) > _NO_MOVE_PX]
    reach_h = [abs(s.after[0] - s.before[0]) for s in flat]
    rises = [s.before[1] - s.peak_y for s in teleports if s.direction == Key.UP.name and s.peak_y is not None]
    reach_v = [r for r in rises if r > _NO_MOVE_PX]
    walks = [s for s in samples if s.kind == MoveKind.WALK and s.held_ms is not None
             and abs(s.after[0] - s.before[0]) > _NO_MOVE_PX]
    held_s = sum(s.held_ms for s in walks if s.held_ms is not None) / 1000.0
    walked_px = sum(abs(s.after[0] - s.before[0]) for s in walks)
    return {
        "reach_h_mean": statistics.fmean(reach_h) if reach_h else None,
        "reach_h_sd": statistics.stdev(reach_h) if len(reach_h) >= 2 else None,
        "reach_h_n": len(reach_h),
        "reach_v_peak_mean": statistics.fmean(reach_v) if reach_v else None,
        "walk_px_per_s": walked_px / held_s if held_s > 0 else None,
        "teleport_settle_s": _median([s.settle_s for s in flat]),
        "drop_settle_s": _median([s.settle_s for s in samples if s.kind == MoveKind.DROP]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.route.harness.measure_moves",
                                     description="Measure the character's moves in the game through the board. "
                                                 "F12 stops.")
    parser.add_argument("--supervised", action="store_true", help="required: someone watches the run")
    parser.add_argument("--out", type=Path, default=None,
                        help="measurement folder (default data/route/measure/<UTC stamp>)")
    parser.add_argument("--reps", type=int, default=10, help=f"moves per phase (at least {_MIN_REPS}; default 10)")
    return parser


# ---- seams (tests monkeypatch these) ----------------------------------------------------------------------------------

def _start_capture(shared) -> threading.Thread:
    return runtime.start_capture(shared)


def _wait_first_frame(shared, timeout_s: float) -> Result[None, str]:
    return runtime.wait_first_frame(shared, timeout_s)


def _make_positions(shared) -> PositionSource | Failure[str]:
    return runtime.make_positions(shared)


def _make_link():
    return BleLink()


def _make_clock() -> Clock:
    return RealClock()


def _make_stop_control(on_stop: Callable[[], None]):
    return runtime.StopControl(on_stop)


def _read_line() -> str:
    return input()


# ---- the procedure ----------------------------------------------------------------------------------------------------

class _StopGuard:
    """`stop` is the stop control's `on_stop`: it sets `stopped` and starts a thread that sends `release_all` every
    `_GUARD_S` until `finish()` (as the walker's PB-D4 guard)."""

    def __init__(self, layer: InputLayer) -> None:
        self._layer = layer
        self.stopped = threading.Event()
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def stop(self) -> None:
        self.stopped.set()
        with self._lock:
            if self._thread is not None or self._done.is_set():
                return
            self._thread = threading.Thread(target=self._guard, name="measure-stop-guard", daemon=True)
            self._thread.start()

    def _guard(self) -> None:
        while True:
            self._layer.release_all()
            if self._done.wait(_GUARD_S):
                return

    def finish(self) -> None:
        with self._lock:
            self._done.set()
            thread = self._thread
        if thread is not None:
            thread.join(_GUARD_JOIN_S)
            if thread.is_alive():
                print("measure: the stop guard thread did not end", file=sys.stderr)


@dataclass(frozen=True)
class _Settle:
    pos: tuple[int, int] | None  # the last position read; None when no read succeeded
    settled: bool                # `pos` was read unchanged for _STABLE_S before the timeout
    settle_s: float              # from the start of the settle to the first read of `pos`
    peak_y: int | None           # the smallest y read


def _ask(text: str, stopped: threading.Event) -> bool:
    """Print `text` and wait for Enter. False when a stop comes first or stdin is closed.

    The line is read on a daemon thread so that F12 or Ctrl+C ends the wait at once. A line read within `_stray_s`
    of the prompt was typed before it and does not answer it."""
    print(f"\n{text}\nPress Enter when she is there (F12 stops).", flush=True)
    shown = time.monotonic()
    while True:
        line = _wait_line(stopped)
        if line is None:
            return False
        closed, read_at = line
        if closed or stopped.is_set() or read_at - shown >= _stray_s:
            break
        print("An Enter pressed before this prompt was ignored. Press Enter when she is there.", flush=True)
        shown = time.monotonic()
    if closed and not stopped.is_set():
        print("stdin closed: stopping", file=sys.stderr)
    return not closed and not stopped.is_set()


def _wait_line(stopped: threading.Event) -> tuple[bool, float] | None:
    """Read one line on a daemon thread: (stdin closed, `time.monotonic()` when the read ended), or None when a stop
    comes first."""
    answered = threading.Event()
    result: list[tuple[bool, float]] = []

    def read() -> None:
        closed = False
        try:
            _read_line()
        except (EOFError, OSError):
            closed = True
        result.append((closed, time.monotonic()))
        answered.set()

    threading.Thread(target=read, name="measure-prompt", daemon=True).start()
    while not answered.wait(_PROMPT_POLL_S):
        if stopped.is_set():
            return None
    return result[0]


def _countdown(stopped: threading.Event) -> bool:
    """False when a stop comes during the countdown."""
    print(f"Focus the game window. Starting in {_countdown_s:g} s. F12 stops.", flush=True)
    return not stopped.wait(_countdown_s)


def _held_ms(record: ActionRecord) -> float | None:
    """The walk's drawn hold (its `WALK` sample): how long the board holds the key."""
    return next((s.ms for s in record.plan.samples if s.type is TimingType.WALK), None)


def _direction(key: Key | None) -> str | None:
    return key.name if key is not None else None


class _Session:
    """The phases of plan B20, one move at a time. Exit codes travel as `Failure(code)`; see the module docstring."""

    def __init__(self, layer: InputLayer, positions: PositionSource, clock: Clock, stopped: threading.Event,
                 out: Path, samples: list[MoveSample]) -> None:
        self._layer = layer
        self._positions = positions
        self._clock = clock
        self._stopped = stopped
        self._out = out
        self._samples = samples

    def run(self, reps: int) -> int:
        alternating = [Key.RIGHT if i % 2 == 0 else Key.LEFT for i in range(reps)]
        half = reps // 2
        phases: list[tuple[str, Sequence[tuple[MoveKind, Key | None]]]] = [
            (_FLOOR, [(MoveKind.TELEPORT, k) for k in alternating] + [(MoveKind.WALK, k) for k in alternating]),
            (_OPEN, [(MoveKind.TELEPORT, Key.UP)] * reps),
            *[(f"{_PLATFORM} Teleport DOWN {i + 1} of {half}.", [(MoveKind.TELEPORT, Key.DOWN)]) for i in range(half)],
            *[(f"{_PLATFORM} Drop {i + 1} of {half}.", [(MoveKind.DROP, None)]) for i in range(half)],
        ]
        for prompt, moves in phases:
            done = self._phase(prompt, moves)
            if not is_successful(done):
                return done.failure()
        return 0

    def _phase(self, prompt: str, moves: Sequence[tuple[MoveKind, Key | None]]) -> Result[None, int]:
        if not _ask(prompt, self._stopped) or not _countdown(self._stopped):
            return Failure(0)
        before: tuple[int, int] | None = None
        for kind, key in moves:
            if before is None:
                start = self._before()
                if not is_successful(start):
                    return Failure(start.failure())
                before = start.unwrap()
            if self._stopped.is_set():
                return Failure(0)
            moved = self._move(kind, key, before)
            if not is_successful(moved):
                return Failure(moved.failure())
            before = moved.unwrap()
        return Success(None)

    def _before(self) -> Result[tuple[int, int], int]:
        """Settle where the character stands now. Failure(0) on a stop, Failure(10) when she is not found."""
        settle = self._settle(track_peak=False)
        if settle is None:
            return Failure(0)
        if settle.pos is None or not settle.settled:
            print(f"error: character lost: no position stayed put for {_STABLE_S:g} s within "
                  f"{_SETTLE_TIMEOUT_S:g} s (last read {settle.pos})", file=sys.stderr)
            return Failure(10)
        return Success(settle.pos)

    def _move(self, kind: MoveKind, key: Key | None, before: tuple[int, int]) -> Result[tuple[int, int] | None, int]:
        """Play one move and measure it: Success(after), Success(None) when it gave no sample, or Failure(code)."""
        played = self._play(kind, key)
        if not is_successful(played):
            error = played.failure()
            if self._stopped.is_set():
                return Failure(0)  # ABORTED by the stop's release_all, or a failure while stopping
            print(f"error: {kind.value} {_direction(key)} failed: {error}", file=sys.stderr)
            return Failure(1 if error in _LINK_DOWN else 6)
        upward = kind is MoveKind.TELEPORT and key is Key.UP
        settle = self._settle(track_peak=upward)
        if settle is None:
            return Failure(0)
        if settle.pos is None or not settle.settled:
            print(f"{kind.value} {_direction(key)}: not settled within {_SETTLE_TIMEOUT_S:g} s (last read "
                  f"{settle.pos}); no sample", file=sys.stderr)
            return Success(None)
        sample = MoveSample(kind.value, _direction(key), before, settle.pos, settle.peak_y if upward else None,
                            settle.settle_s, _held_ms(played.unwrap()) if kind is MoveKind.WALK else None)
        append_sample(self._out / MEASURE_FILE, sample)
        self._samples.append(sample)
        peak = f", peak y {sample.peak_y}" if sample.peak_y is not None else ""
        print(f"{sample.kind} {sample.direction}: {before} -> {settle.pos}{peak}, settled in {sample.settle_s:.2f} s")
        return Success(settle.pos)

    def _play(self, kind: MoveKind, key: Key | None) -> Result[ActionRecord, InputError]:
        if kind is MoveKind.WALK and key is not None:
            return self._layer.walk(key, _WALK_MS)
        if kind is MoveKind.TELEPORT and key is not None:
            return self._layer.teleport(key)
        return self._layer.drop()

    def _settle(self, track_peak: bool) -> _Settle | None:
        """Poll until one position has been read for _STABLE_S, or _SETTLE_TIMEOUT_S passes. None on a stop."""
        clock = self._clock
        start_ns = clock.now_ns()
        last: tuple[int, int] | None = None
        since_ns = start_ns
        peak_y: int | None = None
        while True:
            if self._stopped.is_set():
                return None
            now_ns = clock.now_ns()
            read = self._positions.read()
            if is_successful(read):
                pos = read.unwrap()
                if track_peak and (peak_y is None or pos[1] < peak_y):
                    peak_y = pos[1]
                if pos != last:
                    last, since_ns = pos, now_ns
                elif now_ns - since_ns >= _STABLE_S * _NS_PER_S:  # only a fresh read confirms a settle
                    return _Settle(last, True, (since_ns - start_ns) / _NS_PER_S, peak_y)
            if now_ns - start_ns >= _SETTLE_TIMEOUT_S * _NS_PER_S:
                return _Settle(last, False, (since_ns - start_ns) / _NS_PER_S, peak_y)
            clock.sleep(_POLL_S)


def _write_summary(out: Path, samples: Sequence[MoveSample]) -> None:
    summary = summarize(samples)
    text = json.dumps(summary, indent=2)
    (out / _SUMMARY_FILE).write_text(text + "\n", encoding="utf-8")
    print(f"summary of {len(samples)} samples:\n{text}")


def _run_supervised(args: argparse.Namespace, out: Path) -> int:
    """The measuring procedure (plan B20); the steps and the exit codes are in the module docstring."""
    shared = runtime.SharedFrame()
    positions = _make_positions(shared)
    if isinstance(positions, Failure):
        print(f"error: {positions.failure()}", file=sys.stderr)
        return 5
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: cannot create {out}: {exc}", file=sys.stderr)
        return 2

    code = 0
    link = layer = guard = stop = None
    connected = stop_started = ran = False
    samples: list[MoveSample] = []
    try:
        _start_capture(shared)
        first = _wait_first_frame(shared, _FIRST_FRAME_S)
        if not is_successful(first):
            print(f"error: no first frame: {first.failure()}", file=sys.stderr)
            return 3
        found = positions.read()
        if not is_successful(found):
            print(f"error: character not found on the minimap: {found.failure()}", file=sys.stderr)
            return 3

        link = _make_link()
        connect = link.connect()
        if not is_successful(connect):
            print(f"error: connect failed: {connect.failure()}", file=sys.stderr)
            return 4
        connected = True
        layer = InputLayer(link, TimingModel(np.random.default_rng()))
        guard = _StopGuard(layer)
        stop = _make_stop_control(guard.stop)
        stop_started = True
        started = stop.start()
        if not is_successful(started):
            print(f"stop control failed: {started.failure()}", file=sys.stderr)
            return 9

        ran = True
        session = _Session(layer, positions, _make_clock(), guard.stopped, out, samples)
        try:
            code = session.run(args.reps)
        except Exception as exc:
            print(f"measure crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
            code = 7
        if guard.stopped.is_set():
            print(f"stopped ({stop.trigger_source})")
    finally:
        steps: list[tuple[str, Callable[[], object]]] = []
        if guard is not None:
            steps.append(("stop guard", guard.finish))
        if connected and layer is not None:
            steps.append(("release_all", layer.release_all))
        if link is not None:
            steps.append(("link.close", link.close))
        if stop_started and stop is not None:
            steps.append(("stop.close", stop.close))
        if ran:
            steps.append(("summary", lambda: _write_summary(out, samples)))
        runtime.run_cleanup(steps)
    return code


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse error or --help: return its code, do not raise
        return exc.code if isinstance(exc.code, int) else 2

    if not args.supervised:
        print("error: measure_moves sends keys to the game; run it only while you watch, with --supervised",
              file=sys.stderr)
        return 2
    if args.reps < _MIN_REPS:
        print(f"error: --reps must be at least {_MIN_REPS}, got {args.reps}", file=sys.stderr)
        return 2
    out: Path = args.out if args.out is not None else (
        _REPO / "data" / "route" / "measure" / datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ"))
    checked = runtime.check_out(out, _OUT_FILES)
    if not is_successful(checked):
        print(f"error: {checked.failure()}", file=sys.stderr)
        return 2
    return _run_supervised(args, out)


if __name__ == "__main__":
    sys.exit(main())
