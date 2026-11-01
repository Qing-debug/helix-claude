"""The walk's run driver (route-tab spec 5.6): `RunSession.run()` is the body `walk.main` had, behind seams.

`python -m src.route.walk` (walk.py, argument parsing and the seams) and the app's Route tab both run a `RunSession`.
`run()` loads the route, starts the capture when it was given one, checks the first frame and the character, connects
the link, builds the walker and the stop control (F12, Ctrl+C, the `--minutes` timer), writes the run files, counts
down, records the minimap, then runs the walker. One `finally` does the cleanup in the plan's order (timer,
release_all, recorder, stop.json, step log, leg log, link.close, stop control close); a failing step prints `cleanup
failed: <step>: ...` and never changes the exit code. Exit codes are walk.py's.

Rulings this builds on top of the plan text:
- B19-D1 (lost character): every leg has a `LEG_TIMEOUT_S` (60 s) timeout, written into `session.json`. A leg that
  times out with no move ends the run: the leg-log wrapper `_LostLegSink` calls `trigger("off_graph")` when the leg
  counted an off-graph position (the walker's `off_graph` counter also counts "no path to the target",
  planner.py:211-213), else `trigger("no_position")` (only misses, or no new frame at all), and then calls
  `walker.stop_now()` itself on the walker thread, so no move follows the lost leg (`trigger` only sets an event,
  runtime.py:81-89). `stop.json` reason is that source and the exit is 10, also when the lost leg was the last leg of
  the last lap. A leg that sent moves and then timed out does not end the run (the walker goes on to the next leg).
- B19-D2 (re-run into a used --out): the CLI refuses with exit 2, before capture starts or any file is written, when
  `--out` is an existing file or holds any of `OUT_FILES`'s eight names (`check_out`).

Where the plan is silent:
- `make_positions` returns the position source, or a `Failure` (exit 5) when the character icon does not load.
- Without a seed one is drawn from the OS (`secrets.randbits(64)`) and written into `session.json`; the timing and
  planner generators are `SeedSequence(seed).spawn(2)`.
- A leg log that cannot be opened (a permission error, say) returns 2, like the other bad-`--out` cases.
- `session.json` also holds the class, `laps`, `minutes`, `record` (always true: every walk records, H62) and the
  walker's leg timeout.
- Every walk writes `map.csv` (a byte copy of the map), `minimap.png` (the first frame it waited for) and
  `steps.jsonl` (the step log, the walker's observer) once the stop control has started (spec 5.9 step 1).
- A step-1 failure prints one line (`step log unavailable: ...; walking without the route view`) and walks on with
  no step log; the files that did write stay (RV3-D2 A, A1).
- In the step log's `end` line, `code` is null when an exception leaves `run()`.

Every line the session prints goes through its `log` keyword (route tab H71, RDO110-D3 A): the default,
`_print_line`, prints it as walk always has (an error line on stderr, the rest on stdout), so the CLI is unchanged; the
Route tab passes its own, which queues the line for the app's console box. The components below the session (runtime's
`run_cleanup` and stop control, the walker, the step log) get `_error_line`, the same `log` with the error flag set,
through their own `log` keyword (RT-8c piece 4b, ruling orch 111).
"""

import contextlib
import hashlib
import io
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import cv2
import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.classes.classes import CharacterClass, Kanna
from src.controller.controller import BuildOutcome, MapleAgent
from src.input.input_layer import InputLayer, Link
from src.input.link import BleLink
from src.input.timing import TimingModel
from src.route import runtime
from src.route.harness.record_minimap import INDEX_NAME, record, shared_frames
from src.route.leg_log import LAPS_FILE, LEGS_FILE, JsonlLegLog, LapRecord, LegOutcome, LegRecord, LegSink
from src.route.params import MovementParams
from src.route.route_graph import RouteGraph
from src.route.step_log import MAP_COPY, SNAPSHOT, STEPS_FILE, RunHeader, StepLog
from src.route.walker import Clock, PositionSource, RealClock, RouteWalker, RunSummary, WalkerConfig

_REPO = Path(__file__).resolve().parents[2]
RUN_ROOT = _REPO / "data" / "route"
CLASSES: dict[str, type[CharacterClass]] = {"kanna": Kanna}
LEG_TIMEOUT_S = 60.0      # B19-D1; the same as the simulator's (sim/run.py:63)
FIRST_FRAME_S = 30.0
RECORD_FPS = 10.0
RECORD_JOIN_S = 2.0
DISK_CHECK_FRAMES = 600
START_FREE_BYTES = 2 * 2**30     # spec 5.4 check 5; provisional until RT-13
RECORD_FLOOR_BYTES = 1 * 2**30   # spec 5.6; provisional until RT-13
RECORD_GB_PER_HOUR = 0.5         # spec 5.2's Recording line; provisional until RT-13 measures it
_SESSION = "session.json"
_STOP = "stop.json"
_FRAMES = "frames"
# Every file a run writes into its folder, in the order the B19-D2 check reports the first one found (RV-9's step log,
# map copy and snapshot after the old ones, so the message for an old run folder is unchanged).
OUT_FILES = (_SESSION, _STOP, LEGS_FILE, LAPS_FILE, f"{_FRAMES}/{INDEX_NAME}", STEPS_FILE, MAP_COPY, SNAPSHOT)
_LOST_SOURCES = frozenset({"off_graph", "no_position"})


class RunLink(Link, Protocol):
    """What `run()` needs of a link: `InputLayer`'s two methods (`Link`) plus connect and close (`BleLink`)."""

    def connect(self) -> Result[object, str]: ...

    def close(self) -> None: ...


class FrameHolder(Protocol):
    """`runtime.SharedFrame` and the app's `SharedInfo` match. Read-only members, so a narrower holder still matches."""

    @property
    def minimap_frame_lock(self) -> threading.Lock: ...

    @property
    def minimap_frame(self) -> object: ...


class MakeStop(Protocol):
    """How `run()` builds its stop control: `runtime.StopControl` matches; `log` is the session's error line."""

    def __call__(self, on_stop: Callable[[], None], *, log: Callable[[str], None]) -> runtime.StopControl: ...


@dataclass(frozen=True)
class RunSettings:
    map_csv: Path
    character: str              # a key of CLASSES; written to session.json and RunHeader
    laps: int | None
    minutes: float | None
    countdown_s: float
    seed: int | None            # None: drawn from the OS, written to session.json
    out: Path


@dataclass(frozen=True)
class RunEvent:
    kind: Literal["status", "countdown", "running", "warning", "ended"]
    text: str
    seconds_left: int | None = None
    code: int | None = None


def new_out() -> Path:
    return RUN_ROOT / datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")


def check_out(out: Path) -> Result[None, str]:
    """B19-D2: refuse an --out that is a file or already holds one of the run's files."""
    return runtime.check_out(out, OUT_FILES)


def free_bytes(path: Path) -> int:
    """Free bytes on the drive of the nearest existing ancestor of `path` (the folder may not exist yet)."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def drive_name(path: Path) -> str:
    """`path.resolve().anchor` without the slash: "C:" for C:\\... and for a relative path (its own anchor is "")."""
    return path.resolve().anchor.rstrip("\\/")


def _print_line(text: str, error: bool) -> None:
    """`RunSession`'s default `log`: one line, on stderr when `error`, else on stdout (walk's output as before)."""
    print(text, file=sys.stderr if error else sys.stdout)


class _LostLegSink:
    """Passes every record to `inner`. A leg that timed out with no move ends the run (B19-D1): after the record is
    written it triggers the stop control with `off_graph` or `no_position` and calls `walker.stop_now()` itself."""

    def __init__(self, inner: LegSink, log: Callable[[str, bool], None] = _print_line) -> None:
        self._inner = inner
        self._log = log
        self._trigger: Callable[[str], None] | None = None
        self._stop_now: Callable[[], None] | None = None

    def arm(self, trigger: Callable[[str], None], stop_now: Callable[[], None]) -> None:
        """`trigger` is the stop control's `trigger`, `stop_now` the walker's; set once both exist."""
        self._trigger = trigger
        self._stop_now = stop_now

    def write_leg(self, rec: LegRecord) -> Result[None, str]:
        written = self._inner.write_leg(rec)
        if rec.outcome == LegOutcome.TIMED_OUT and not rec.moves:
            source = "off_graph" if rec.off_graph else "no_position"
            self._log(f"character lost: a leg timed out after {LEG_TIMEOUT_S:g} s with no move ({source}); "
                      "ending the run", True)
            if self._trigger is not None:
                self._trigger(source)
            if self._stop_now is not None:
                self._stop_now()  # on this (the walker's) thread, before the walker starts the next leg
        return written

    def write_lap(self, rec: LapRecord) -> Result[None, str]:
        return self._inner.write_lap(rec)


def _load_route(map_csv: Path, character: CharacterClass) -> Result[tuple[RouteGraph, tuple], str]:
    agent = MapleAgent(characterClass=character)
    with contextlib.redirect_stdout(io.StringIO()):  # the CSV parser prints its progress
        loaded = agent.loadMap(map_csv)
    if not is_successful(loaded):
        return Failure(f"cannot load map {map_csv}: {loaded.failure()}")
    if loaded.unwrap() is not BuildOutcome.BUILT or agent.pathGraph is None:
        return Failure(f"map {map_csv} loaded but no graph was built: {loaded.unwrap().name}")
    rotation = tuple(agent.map_rotation_order)
    if not rotation:
        return Failure(f"map {map_csv} has no blue node: the rotation is empty")
    built = RouteGraph.build(agent.pathGraph, MovementParams())
    if not is_successful(built):
        return Failure(f"route graph: {built.failure()}")
    return Success((built.unwrap(), rotation))


def _git_head() -> str | None:
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_REPO, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    head = done.stdout.strip()
    return head if done.returncode == 0 and head else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_run_files(out: Path, map_path: Path, frame: np.ndarray | None, step_log: StepLog,
                     header: Callable[[str], RunHeader]) -> Result[None, str]:
    """Spec 5.9 step 1 minus session.json (V7 step 4): `map.csv` (the bytes of `map_path`), then `minimap.png`
    (`frame` is RGB, record_minimap.py:96; written to `minimap.png.tmp`, then `os.replace`), each tried even when the
    other failed, so one that writes stays (RV3-D2 A1); only when both wrote, `step_log.start(out, header(sha256 of
    the copied bytes))`. Every error joins the Failure with "; "; never raises."""
    errors: list[str] = []
    sha = ""
    try:
        data = map_path.read_bytes()
        (out / MAP_COPY).write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
    except Exception as exc:
        errors.append(f"{MAP_COPY}: {type(exc).__name__}: {exc}")
    try:
        if frame is None:  # only possible through the test seams
            errors.append("no first frame to save")
        else:
            ok, png = cv2.imencode(".png", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))  # (retval, buf)
            if not ok:
                errors.append(f"{SNAPSHOT}: cv2.imencode failed")
            else:
                tmp = out / f"{SNAPSHOT}.tmp"
                tmp.write_bytes(png.tobytes())
                os.replace(tmp, out / SNAPSHOT)
    except Exception as exc:
        errors.append(f"{SNAPSHOT}: {type(exc).__name__}: {exc}")
    if errors:
        return Failure("; ".join(errors))
    try:
        return step_log.start(out, header(sha))
    except Exception as exc:
        return Failure(f"{STEPS_FILE}: {type(exc).__name__}: {exc}")


def _record_guarded(shared: FrameHolder, out_dir: Path, stop: threading.Event,
                    on_event: Callable[[RunEvent], None], log: Callable[[str, bool], None] = _print_line) -> None:
    """The recorder thread's target (spec 5.6, H62): `record` the shared frames into `out_dir` until `stop` is set.
    An exception or a `Failure` from `record` ends the recording with one `warning` event, and so does free space
    below `RECORD_FLOOR_BYTES` on the run folder's drive, checked every `DISK_CHECK_FRAMES` polls: that check sets
    `stop`, so `record` saves that poll's frame and returns. The walk goes on. `record` is not edited; the constants
    and `free_bytes` are read from this module when called (the tests patch them). Its one line goes to `log`."""
    read = shared_frames(shared)
    polls = 0
    low_on: str | None = None  # the drive, once its free space fell below the floor

    def frames() -> np.ndarray | None:
        nonlocal polls, low_on
        polls += 1
        if polls % DISK_CHECK_FRAMES == 0 and free_bytes(out_dir) < RECORD_FLOOR_BYTES:
            low_on = drive_name(out_dir)
            stop.set()
        return read()

    try:
        recorded = record(frames, out_dir, None, RECORD_FPS, stop=stop)
    except Exception as exc:  # e.g. OSError from index.write on a full disk (record_minimap.py:99-100)
        reason = f"{type(exc).__name__}: {exc}"
        log(f"record failed: {reason}", True)
    else:
        if not is_successful(recorded):
            reason = str(recorded.failure())
            log(f"record failed: {reason}", True)
        elif low_on is not None:
            reason = f"less than {RECORD_FLOOR_BYTES / 2**30:g} GB free on {low_on}"
            log(f"record stopped: {reason}", True)
        else:
            return
    on_event(RunEvent("warning", f"Recording stopped: {reason}. The walk goes on."))


class RunSession:
    """One run, from the route load to the cleanup. `run()` blocks and returns the walk's exit code."""

    def __init__(self, settings: RunSettings, shared: FrameHolder, *,
                 on_event: Callable[[RunEvent], None],
                 make_positions: Callable[[FrameHolder], PositionSource | Failure[str]] = runtime.make_positions,
                 wait_first_frame: Callable[[FrameHolder, float], Result[None, str]] = runtime.wait_first_frame,
                 make_link: Callable[[], RunLink] = BleLink, make_clock: Callable[[], Clock] = RealClock,
                 make_stop: MakeStop = runtime.StopControl,
                 start_capture: Callable[[FrameHolder], object] | None = None,
                 may_walk: Callable[[], bool] | None = None,
                 sleep: Callable[[float], None] = time.sleep, sigint: bool = False,
                 log: Callable[[str, bool], None] = _print_line) -> None:
        self._settings = settings
        self._shared = shared
        self._on_event = on_event
        self._make_positions = make_positions
        self._wait_first_frame = wait_first_frame
        self._make_link = make_link
        self._make_clock = make_clock
        self._make_stop = make_stop
        self._start_capture = start_capture
        self._may_walk = may_walk
        self._sleep = sleep
        self._sigint = sigint
        self._log = log  # every line the run prints, with its error flag (route tab H71, RDO110-D3 A)
        # Spec 5.6: one lock for the cancel flag (its stamp and source), the "started" bit and the hand-over of the
        # walker to `_on_stop`. Reentrant: `run()` triggers the stop control under it, and a stop control whose
        # `trigger` calls `on_stop` on the caller's thread (the tests' fake) then re-enters it from `_on_stop`.
        self._stop_lock = threading.RLock()
        self._cancel: tuple[int, str] | None = None        # (perf_counter_ns, source) of the first stop() before start
        self._control: runtime.StopControl | None = None   # the "started" bit: the stop control once start() succeeded
        self._stop_asked = False                           # the stop control's on_stop has run
        self._walking: RouteWalker | None = None           # the walker, once the walk begins
        # stop.json's reason and trigger source, once the cleanup's stop.json step ran (even if its write failed)
        self._stopped: tuple[str, str | None] | None = None

    def stop(self, source: str) -> None:
        """Ask the run to stop; any thread, returns at once (spec 5.6). Before `StopControl.start()` has succeeded
        this sets the cancel flag (first call only: its source and `perf_counter_ns` stamp), which `run()` checks
        after each step; from then on it is `trigger(source)` on the started stop control. Both under one lock, so a
        call never falls between the two paths."""
        with self._stop_lock:
            control = self._control
            if control is None:
                if self._cancel is None:
                    self._cancel = (time.perf_counter_ns(), source)
                return
        control.trigger(source)

    def _flagged(self) -> bool:
        with self._stop_lock:
            return self._cancel is not None

    def _error_line(self, text: str) -> None:
        """The `log` the walker, the step log, the stop control and `run_cleanup` get: the session's own, as an error."""
        self._log(text, True)

    def _on_stop(self) -> None:
        """The stop control's `on_stop`. It stops the walker only once `_hand_over` has handed it over:
        `walker.stop_now()` starts a guard thread that ends only when `walker.run()` has finished (walker.py:278-287),
        and a run cancelled before the walk, or left by an exception before `walker.run()`, never calls it."""
        with self._stop_lock:
            self._stop_asked = True
            walker = self._walking
        if walker is not None:
            walker.stop_now()

    def _stop_asked_now(self) -> bool:
        """`on_stop` has run: the countdown's end cancels (the real stop control runs it later than `trigger`)."""
        with self._stop_lock:
            return self._stop_asked

    def _hand_over(self, walker: RouteWalker) -> None:
        """Hand `walker` to `_on_stop`, right before `walker.run()`. If `on_stop` ran since the countdown's end, stop
        the walker here instead, so `run()` stops at its first check. Under the lock, exactly one of the two calls
        `stop_now`."""
        with self._stop_lock:
            self._walking = walker
            asked = self._stop_asked
        if asked:
            walker.stop_now()

    def _stop_stamp(self, stop: runtime.StopControl | None) -> tuple[int | None, str | None]:
        """stop.json's `trigger_ns` and `trigger_source`: the cancel flag's if it was set (spec 5.6), else the stop
        control's as they read now."""
        with self._stop_lock:
            flag = self._cancel
        if flag is not None:
            return flag
        if stop is None:
            return None, None
        return stop.trigger_ns, stop.trigger_source

    def run(self) -> int:
        """Everything from the route load to the cleanup; returns the walk's exit code. Posts exactly one `ended`
        event, after the cleanup: with the code and `"<stop.json reason, or none>/<trigger source, or empty>"` (plan
        choice 3), or with `code=None` and the exception's text when one leaves `run()`, which re-raises it."""
        code: int | None = None
        text = ""
        try:
            code = self._run()
            reason, source = self._stopped if self._stopped is not None else ("none", None)
            text = f"{reason}/{source or ''}"
            return code
        except BaseException as exc:
            code = None
            text = f"the run stopped unexpectedly: {type(exc).__name__}: {exc}"
            raise
        finally:
            self._on_event(RunEvent("ended", text, code=code))

    def _run(self) -> int:
        s = self._settings
        out = s.out
        shared = self._shared
        code = 0
        link = layer = log = stop = walker = timer = rec_thread = rec_stop = None
        connected = stop_started = False
        stop_payload: dict | None = None
        run_log: tuple[StepLog, Clock] | None = None  # the step log and its clock, once step 1 started it
        raised = False  # an exception is leaving run() (not one its caller is handling: sys.exc_info() would see that)
        try:
            # A stop() before StopControl.start() (the cancel flag) is checked after each step; it ends the run with
            # exit 0 and the cancel payload, unless the step itself failed (spec 5.6).
            character = CLASSES[s.character]()
            route = _load_route(s.map_csv, character)
            if not is_successful(route):
                self._log(f"error: {route.failure()}", True)
                return 5
            rg, rotation = route.unwrap()
            seed = s.seed if s.seed is not None else secrets.randbits(64)
            rng_timing, rng_planner = (np.random.default_rng(x) for x in np.random.SeedSequence(seed).spawn(2))
            positions = self._make_positions(shared)
            if isinstance(positions, Failure):
                self._log(f"error: {positions.failure()}", True)
                return 5
            if self._flagged():
                stop_payload = {"reason": "cancelled"}
                return 0

            if self._start_capture is not None:
                self._start_capture(shared)
            first = self._wait_first_frame(shared, FIRST_FRAME_S)
            if not is_successful(first):
                self._log(f"error: no first frame: {first.failure()}", True)
                return 3
            if self._flagged():
                stop_payload = {"reason": "cancelled"}
                return 0
            first_frame = shared_frames(shared)()  # the frame just waited for, held for minimap.png (spec 5.2)
            found = positions.read()
            if not is_successful(found):
                self._log(f"error: character not found on the minimap: {found.failure()}", True)
                return 3
            if self._flagged():
                stop_payload = {"reason": "cancelled"}
                return 0

            self._on_event(RunEvent("status", "Connecting to the board..."))
            link = self._make_link()
            connect = link.connect()  # not interrupted: a stop() meanwhile takes effect when it returns
            if not is_successful(connect):
                self._log(f"error: connect failed: {connect.failure()}", True)
                return 4
            connected = True
            layer = InputLayer(link, TimingModel(rng_timing))
            if self._flagged():  # after `connected` and `layer`, so the cleanup releases the keys first
                stop_payload = {"reason": "cancelled"}
                return 0
            opened = JsonlLegLog.open(out)
            if not is_successful(opened):
                self._log(f"error: {opened.failure()}", True)
                return 2
            log = opened.unwrap()
            if self._flagged():
                stop_payload = {"reason": "cancelled"}
                return 0
            sink = _LostLegSink(log, self._log)
            config = WalkerConfig(use_stimulus=True, leg_timeout_s=LEG_TIMEOUT_S, max_laps=s.laps)
            clock = self._make_clock()
            step_log = StepLog(log=self._error_line)  # no I/O; every walk passes it (V7 step 3)
            walker = RouteWalker(rg, rotation, layer, positions, clock, sink, rng_planner, config, observer=step_log,
                                 log=self._error_line)
            stop = self._make_stop(self._on_stop, log=self._error_line)
            sink.arm(stop.trigger, walker.stop_now)
            if self._flagged():
                stop_payload = {"reason": "cancelled"}
                return 0
            stop_started = True
            started = stop.start(sigint=self._sigint)
            if not is_successful(started):
                self._log(f"stop control failed: {started.failure()}", True)
                return 9
            with self._stop_lock:  # the "started" bit; a stop() after the last flag check reaches the countdown
                self._control = stop
                if self._cancel is not None:
                    stop.trigger(self._cancel[1])

            # Spec 5.9 step 1 (V7 step 4); from here on the only returns are the cancel at the countdown's end and
            # `return code`.
            head = _git_head()
            wrote = _write_run_files(out, s.map_csv, first_frame, step_log, lambda sha: RunHeader(
                source="walk", clock="perf_counter_ns", map=MAP_COPY, map_sha256=sha, character=s.character,
                params=asdict(rg.params), rotation=tuple(n.minimap_position for n in rotation), git_head=head,
                wall_ns=time.time_ns(), perf_ns=clock.now_ns()))  # RV4-D2 A: read back to back, wall clock first
            if is_successful(wrote):
                run_log = (step_log, clock)
            else:  # RV3-D2 A with A1: no step log; the exit code does not change
                self._log(f"step log unavailable: {wrote.failure()}; walking without the route view", True)
                step_log.close()  # at once: from here every observer call is dropped (V3, A2)
            _write_json(out / _SESSION, {
                "seed": seed,
                "params": asdict(rg.params),
                "leg_timeout_s": LEG_TIMEOUT_S,
                "map": str(s.map_csv),
                "map_sha256": _sha256(s.map_csv),
                "git_head": head,
                "start_utc": datetime.now(UTC).isoformat(),
                "class": s.character,
                "laps": s.laps,
                "minutes": s.minutes,
                "record": True,
            })
            self._log(f"Focus the game window. Starting in {s.countdown_s:g} s. F12 stops.", False)
            remaining = s.countdown_s
            for left in range(math.ceil(s.countdown_s), 0, -1):
                self._on_event(RunEvent("countdown", f"Starting in {left} s", seconds_left=left))
                pause = min(1.0, remaining)
                self._sleep(pause)
                remaining -= pause
                if stop.trigger_source is not None:
                    break
            if stop.trigger_source is None and self._may_walk is not None and not self._may_walk():
                stop.trigger("countdown_focus")  # section 9: the app still had the keyboard
            # The countdown's last check, also at 0 s (no step). `trigger_source` covers a trigger whose `on_stop` has
            # not run yet (the real stop control runs it later, on its worker); `_stop_asked_now` covers the reverse.
            if stop.trigger_source is not None or self._stop_asked_now():
                stop_payload = {"reason": "cancelled"}  # stopped before the walk: no recorder, timer or walk
                return 0

            rec_stop = threading.Event()
            rec_thread = threading.Thread(target=_record_guarded,
                                          args=(shared, out / _FRAMES, rec_stop, self._on_event, self._log),
                                          name="record", daemon=True)
            rec_thread.start()
            if s.minutes is not None:
                timer = threading.Timer(60 * s.minutes, stop.trigger, args=("timer",))
                timer.daemon = True
                timer.name = "walk-timer"
                timer.start()
            self._on_event(RunEvent("running", "Running."))

            error: str | None = None
            summary: RunSummary | None = None
            self._hand_over(walker)  # last, so an exception before walker.run() leaves no stop guard behind
            try:
                ran = walker.run()
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self._log(f"walk crashed: {error}", True)
                code = 7
            else:
                if is_successful(ran):
                    summary = ran.unwrap()
                else:
                    error = str(ran.failure())
                    self._log(f"walk failed: {error}", True)
                    code = 6

            if error is not None:
                stop_payload = {"reason": "error", "error": error}
            elif summary is not None and summary.reason == "link_down":
                stop_payload = {"reason": "link_down"}
                code = 1
            elif summary is not None and stop.trigger_source in _LOST_SOURCES:
                stop_payload = {"reason": stop.trigger_source}
                code = 10
            elif summary is not None:
                stop_payload = {"reason": summary.reason}
                code = 0
        except BaseException:
            raised = True
            raise
        finally:
            steps: list[tuple[str, Callable[[], object]]] = []
            if timer is not None:
                def cancel_timer(t: threading.Timer = timer) -> None:
                    t.cancel()
                    t.join()
                steps.append(("timer", cancel_timer))
            if connected and layer is not None:
                steps.append(("release_all", layer.release_all))
            if rec_thread is not None and rec_stop is not None:
                def stop_recorder(th: threading.Thread = rec_thread, ev: threading.Event = rec_stop) -> None:
                    ev.set()
                    th.join(RECORD_JOIN_S)
                steps.append(("record", stop_recorder))
            if stop_payload is not None:
                def write_stop(payload: dict = stop_payload, sc: runtime.StopControl | None = stop) -> None:
                    trigger_ns, source = self._stop_stamp(sc)
                    self._stopped = (payload["reason"], source)  # first: `ended` keeps them if the write fails
                    out.mkdir(parents=True, exist_ok=True)  # a cancel can come before the leg log made the folder
                    _write_json(out / _STOP, {**payload, "trigger_ns": trigger_ns, "trigger_source": source})
                    self._log(f"stopped: {payload['reason']}" + (f" ({source})" if source is not None else ""), False)
                steps.append(("stop.json", write_stop))
            if run_log is not None:  # on the walker's thread, after run() returned (RV-2: calls racing close are dropped)
                def end_step_log(sl: StepLog = run_log[0], clk: Clock = run_log[1], payload: dict | None = stop_payload,
                                 exit_code: int | None = None if raised else code) -> None:
                    sl.end(clk.now_ns(), payload["reason"] if payload is not None else None, exit_code)
                    closed = sl.close()
                    if not is_successful(closed):
                        self._log(f"step log: {closed.failure()}", True)
                steps.append(("step_log", end_step_log))
            if log is not None:
                steps.append(("leg_log", log.close))
            if link is not None:
                steps.append(("link.close", link.close))
            if stop_started and stop is not None:
                steps.append(("stop.close", stop.close))
            runtime.run_cleanup(steps, log=self._error_line)  # re-raises a held KeyboardInterrupt after every step ran (plan B19 cleanup)
        return code
