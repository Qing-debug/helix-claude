"""The supervised walk CLI (plan task B19): walk the map's rotation in the game through the board, F12 stops.

    python -m src.route.walk --map maps/fossil_foundry_2.csv --supervised [--class kanna] [--laps N] [--minutes M]
                             [--seed S] [--out data/route/<UTC stamp>] [--record]

Exit codes: 0 done · 1 run ended link_down · 2 usage, no --supervised, or an --out that already holds a run (B19-D2)
· 3 no first frame or no character found · 4 connect failed · 5 map, route graph or character icon failed to load
· 6 run() returned a Failure · 7 run() raised an exception · 9 the stop control failed to start · 10 the character
was lost: a leg timed out with no move (B19-D1). 8 is the live link check's own code, never `main`'s.

`main` starts the minimap capture, checks that the character is found, connects the link, builds the walker and the
stop control (F12, Ctrl+C, the `--minutes` timer), writes `session.json`, counts down, then runs the walker. One
`finally` does the cleanup in the plan's order (timer, release_all, recorder, stop.json, leg log, link.close,
stop control close); a failing step prints `cleanup failed: <step>: ...` and never changes the exit code.

Rulings this builds on top of the plan text:
- B19-D1 (lost character): every leg has a `_LEG_TIMEOUT_S` (60 s) timeout, written into `session.json`. A leg that
  times out with no move ends the run: the leg-log wrapper `_LostLegSink` calls `trigger("off_graph")` when the leg
  counted an off-graph position (the walker's `off_graph` counter also counts "no path to the target",
  planner.py:211-213), else `trigger("no_position")` (only misses, or no new frame at all), and then calls
  `walker.stop_now()` itself on the walker thread, so no move follows the lost leg (`trigger` only sets an event,
  runtime.py:64-72). `stop.json` reason is that source and the exit is 10, also when the lost leg was the last leg of
  the last lap. A leg that sent moves and then timed out does not end the run (the walker goes on to the next leg).
- B19-D2 (re-run into a used --out): `main` refuses with exit 2, before capture starts or any file is written, when
  `--out` is an existing file or holds any of `session.json`, `stop.json`, `legs.jsonl`, `laps.jsonl`,
  `frames/index.jsonl`.

Where the plan is silent:
- `_make_positions` returns the position source, or a `Failure` (exit 5) when the character icon does not load.
- Without `--seed` a seed is drawn from the OS (`secrets.randbits(64)`) and written into `session.json`;
  the timing and planner generators are `SeedSequence(seed).spawn(2)`.
- The default `--out` is `data/route/<UTC stamp>` under the repo root, so it does not depend on the working directory.
- An argparse error returns its exit code (2) rather than raising `SystemExit`, so `main` always returns an int.
- A leg log that cannot be opened (a permission error, say) returns 2, like the other bad-`--out` cases.
- `session.json` also holds the class, `--laps`, `--minutes`, `--record` and the walker's leg timeout.
"""

import argparse
import contextlib
import hashlib
import io
import json
import math
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.classes.classes import CharacterClass, Kanna
from src.controller.controller import BuildOutcome, MapleAgent
from src.input.input_layer import InputLayer
from src.input.link import BleLink
from src.input.timing import TimingModel
from src.route import runtime
from src.route.character import CharacterLocator
from src.route.harness.record_minimap import INDEX_NAME, record, shared_frames
from src.route.leg_log import LAPS_FILE, LEGS_FILE, JsonlLegLog, LapRecord, LegOutcome, LegRecord, LegSink
from src.route.params import MovementParams
from src.route.position import MinimapPositionSource
from src.route.route_graph import RouteGraph
from src.route.walker import Clock, PositionSource, RealClock, RouteWalker, RunSummary, WalkerConfig

_REPO = Path(__file__).resolve().parents[2]
_ICON = _REPO / "assets" / "chr" / "chr.png"
_MASK = _REPO / "assets" / "chr" / "chr_mask.png"
_CLASSES: dict[str, type[CharacterClass]] = {"kanna": Kanna}
_LEG_TIMEOUT_S = 60.0      # B19-D1; the same as the simulator's (sim/run.py:63)
_FIRST_FRAME_S = 30.0
_RECORD_FPS = 10.0
_RECORD_JOIN_S = 2.0
_SESSION = "session.json"
_STOP = "stop.json"
_FRAMES = "frames"
# Every file walk writes into --out, in the order the B19-D2 check reports the first one found.
_OUT_FILES = (_SESSION, _STOP, LEGS_FILE, LAPS_FILE, f"{_FRAMES}/{INDEX_NAME}")
_LOST_SOURCES = frozenset({"off_graph", "no_position"})

_countdown_s = 5.0


# ---- seams (tests monkeypatch these) ----------------------------------------------------------------------------------

def _start_capture(shared) -> threading.Thread:
    return runtime.start_capture(shared)


def _wait_first_frame(shared, timeout_s: float) -> Result[None, str]:
    return runtime.wait_first_frame(shared, timeout_s)


def _make_positions(shared) -> PositionSource | Failure[str]:
    located = CharacterLocator.from_files(_ICON, _MASK)
    if not is_successful(located):
        return Failure(f"character icon: {located.failure()}")
    return MinimapPositionSource(shared, located.unwrap())


def _make_link():
    return BleLink()


def _make_clock() -> Clock:
    return RealClock()


def _make_stop_control(on_stop: Callable[[], None]):
    return runtime.StopControl(on_stop)


# ---- helpers ----------------------------------------------------------------------------------------------------------

class _Shared:
    """What the capture thread and the position source need of `SharedInfo` (src/main.py may not be imported here)."""

    def __init__(self) -> None:
        self.minimap_frame_lock = threading.Lock()
        self.minimap_frame = None


class _LostLegSink:
    """Passes every record to `inner`. A leg that timed out with no move ends the run (B19-D1): after the record is
    written it triggers the stop control with `off_graph` or `no_position` and calls `walker.stop_now()` itself."""

    def __init__(self, inner: LegSink) -> None:
        self._inner = inner
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
            print(f"character lost: a leg timed out after {_LEG_TIMEOUT_S:g} s with no move ({source}); "
                  "ending the run", file=sys.stderr)
            if self._trigger is not None:
                self._trigger(source)
            if self._stop_now is not None:
                self._stop_now()  # on this (the walker's) thread, before the walker starts the next leg
        return written

    def write_lap(self, rec: LapRecord) -> Result[None, str]:
        return self._inner.write_lap(rec)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.route.walk",
                                     description="Walk the map's rotation in the game through the board. F12 stops.")
    parser.add_argument("--map", type=Path, required=True, help="map CSV (for example maps/fossil_foundry_2.csv)")
    parser.add_argument("--supervised", action="store_true", help="required: someone watches the run")
    parser.add_argument("--class", dest="character", choices=sorted(_CLASSES), default="kanna")
    parser.add_argument("--laps", type=int, default=None, help="stop after N laps (default: until stopped)")
    parser.add_argument("--minutes", type=float, default=None, help="stop after M minutes of walking")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None, help="run folder (default data/route/<UTC stamp>)")
    parser.add_argument("--record", action="store_true", help="also record the minimap frames into <out>/frames")
    return parser


def _check_out(out: Path) -> Result[None, str]:
    """B19-D2: refuse an --out that is a file or already holds one of walk's files."""
    if out.exists() and not out.is_dir():
        return Failure(f"{out} is not a folder; choose a new --out")
    for name in _OUT_FILES:
        path = out / name
        if path.exists():
            return Failure(f"{path} already exists; choose a new --out")
    return Success(None)


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


def _record_frames(shared, out_dir: Path, stop: threading.Event) -> None:
    recorded = record(shared_frames(shared), out_dir, None, _RECORD_FPS, stop=stop)
    if not is_successful(recorded):
        print(f"record failed: {recorded.failure()}", file=sys.stderr)


# ---- main -------------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse error or --help: return its code, do not raise
        return exc.code if isinstance(exc.code, int) else 2

    if not args.supervised:
        print("error: walk sends keys to the game; run it only while you watch, with --supervised", file=sys.stderr)
        return 2
    minutes = args.minutes
    if minutes is not None and not (math.isfinite(minutes) and 0 < minutes <= threading.TIMEOUT_MAX / 60):
        print(f"error: --minutes must be finite, above 0 and at most {threading.TIMEOUT_MAX / 60:g}, got {minutes!r}",
              file=sys.stderr)
        return 2
    out: Path = args.out if args.out is not None else (
        _REPO / "data" / "route" / datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ"))
    checked = _check_out(out)
    if not is_successful(checked):
        print(f"error: {checked.failure()}", file=sys.stderr)
        return 2

    character = _CLASSES[args.character]()
    route = _load_route(args.map, character)
    if not is_successful(route):
        print(f"error: {route.failure()}", file=sys.stderr)
        return 5
    rg, rotation = route.unwrap()
    shared = _Shared()
    positions = _make_positions(shared)
    if isinstance(positions, Failure):
        print(f"error: {positions.failure()}", file=sys.stderr)
        return 5

    seed = args.seed if args.seed is not None else secrets.randbits(64)
    rng_timing, rng_planner = (np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(2))

    code = 0
    link = layer = log = stop = walker = timer = rec_thread = rec_stop = None
    connected = stop_started = False
    stop_payload: dict | None = None
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
        layer = InputLayer(link, TimingModel(rng_timing))
        opened = JsonlLegLog.open(out)
        if not is_successful(opened):
            print(f"error: {opened.failure()}", file=sys.stderr)
            return 2
        log = opened.unwrap()
        sink = _LostLegSink(log)
        config = WalkerConfig(use_stimulus=True, leg_timeout_s=_LEG_TIMEOUT_S, max_laps=args.laps)
        walker = RouteWalker(rg, rotation, layer, positions, _make_clock(), sink, rng_planner, config)
        stop = _make_stop_control(walker.stop_now)
        sink.arm(stop.trigger, walker.stop_now)
        stop_started = True
        started = stop.start()
        if not is_successful(started):
            print(f"stop control failed: {started.failure()}", file=sys.stderr)
            return 9

        _write_json(out / _SESSION, {
            "seed": seed,
            "params": asdict(rg.params),
            "leg_timeout_s": _LEG_TIMEOUT_S,
            "map": str(args.map),
            "map_sha256": _sha256(args.map),
            "git_head": _git_head(),
            "start_utc": datetime.now(UTC).isoformat(),
            "class": args.character,
            "laps": args.laps,
            "minutes": minutes,
            "record": args.record,
        })
        print(f"Focus the game window. Starting in {_countdown_s:g} s. F12 stops.")
        time.sleep(_countdown_s)

        if args.record:
            rec_stop = threading.Event()
            rec_thread = threading.Thread(target=_record_frames, args=(shared, out / _FRAMES, rec_stop),
                                          name="record", daemon=True)
            rec_thread.start()
        if minutes is not None:
            timer = threading.Timer(60 * minutes, stop.trigger, args=("timer",))
            timer.daemon = True
            timer.name = "walk-timer"
            timer.start()

        error: str | None = None
        summary: RunSummary | None = None
        try:
            ran = walker.run()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"walk crashed: {error}", file=sys.stderr)
            code = 7
        else:
            if is_successful(ran):
                summary = ran.unwrap()
            else:
                error = str(ran.failure())
                print(f"walk failed: {error}", file=sys.stderr)
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
                th.join(_RECORD_JOIN_S)
            steps.append(("record", stop_recorder))
        if stop_payload is not None and stop is not None:
            def write_stop(payload: dict = stop_payload, sc=stop) -> None:
                source = sc.trigger_source
                _write_json(out / _STOP, {**payload, "trigger_ns": sc.trigger_ns, "trigger_source": source})
                print(f"stopped: {payload['reason']}" + (f" ({source})" if source is not None else ""))
            steps.append(("stop.json", write_stop))
        if log is not None:
            steps.append(("leg_log", log.close))
        if link is not None:
            steps.append(("link.close", link.close))
        if stop_started and stop is not None:
            steps.append(("stop.close", stop.close))

        held: BaseException | None = None
        for name, step in steps:
            try:
                step()
            except Exception as exc:
                print(f"cleanup failed: {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            except BaseException as exc:  # KeyboardInterrupt, SystemExit: hold it, keep going
                if held is None:
                    held = exc
        if held is not None:
            raise held  # re-raised after every step ran (plan B19 cleanup)
    return code


if __name__ == "__main__":
    sys.exit(main())
