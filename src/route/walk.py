"""The supervised walk CLI (plan task B19): walk the map's rotation in the game through the board, F12 stops.

    python -m src.route.walk --map maps/fossil_foundry_2.csv --supervised [--class kanna] [--laps N] [--minutes M]
                             [--seed S] [--out data/route/<UTC stamp>] [--record]

Exit codes: 0 done · 1 run ended link_down · 2 usage (a --laps below 1, a bad --minutes or --seed), no --supervised, an --out that
already holds a run (B19-D2), or a leg log that cannot be opened · 3 no first frame or no character found · 4 connect
failed · 5 map, route graph or character icon failed to load · 6 run() returned a Failure · 7 run() raised an exception · 9 the stop control failed to start · 10 the character
was lost: a leg timed out with no move (B19-D1) · 11 Helix is not running as administrator, so F12 could not stop the
walk with MapleStory in front (RT-FIX-1, H82; the CLI does not relaunch itself, RT-FIX-1b). 8 is the live link check's own
code, never `main`'s.

`main` checks its arguments (exit 2 before anything starts), then that the process is elevated (RT-FIX-1, H82; if it is
not it prints `runtime.NOT_ELEVATED_CLI_TEXT` and returns 11, whatever `HELIX_NO_ELEVATE` says: a relaunch would take the
walk's output and exit code with its closing window, RT-FIX-1b), then runs `session.RunSession` (session.py) over the seams
below; the walk's body, its cleanup order and its rulings (B19-D1, B19-D2) are documented there. The seams are passed
at call time, read from this module's globals when `main` runs, so a test's `setattr(walk, ...)` reaches the run.

Where the plan is silent:
- Without `--seed` a seed is drawn from the OS (`secrets.randbits(64)`) and written into `session.json`; a seed numpy
  rejects (a negative one) returns 2 before anything starts.
- The default `--out` is `session.new_out()`, `data/route/<UTC stamp>` under the repo root, so it does not depend on the
  working directory.
- An argparse error returns its exit code (2) rather than raising `SystemExit`, so `main` always returns an int.
- `--record` is accepted and does nothing: every walk records (H62).
"""

import argparse
import math
import secrets
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result

from src.input.link import BleLink
from src.route import runtime, session
from src.route.walker import Clock, PositionSource, RealClock

_countdown_s = 5.0


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


def _make_stop_control(on_stop: Callable[[], None], *, log: Callable[[str], None]):
    return runtime.StopControl(on_stop, log=log)


def _is_elevated() -> bool:
    return runtime.is_elevated()


# ---- main -------------------------------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.route.walk",
                                     description="Walk the map's rotation in the game through the board. F12 stops.")
    parser.add_argument("--map", type=Path, required=True, help="map CSV (for example maps/fossil_foundry_2.csv)")
    parser.add_argument("--supervised", action="store_true", help="required: someone watches the run")
    parser.add_argument("--class", dest="character", choices=sorted(session.CLASSES), default="kanna")
    parser.add_argument("--laps", type=int, default=None, help="stop after N laps, at least 1 (default: until stopped)")
    parser.add_argument("--minutes", type=float, default=None, help="stop after M minutes of walking")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None, help="run folder (default data/route/<UTC stamp>)")
    parser.add_argument("--record", action="store_true", help="accepted for older commands; every walk records")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse error or --help: return its code, do not raise
        return exc.code if isinstance(exc.code, int) else 2

    if not args.supervised:
        print("error: walk sends keys to the game; run it only while you watch, with --supervised", file=sys.stderr)
        return 2
    if args.laps is not None and args.laps < 1:  # B19-R M3: a typo used to walk one approach leg
        print(f"error: --laps must be at least 1, got {args.laps}", file=sys.stderr)
        return 2
    minutes = args.minutes
    if minutes is not None and not (math.isfinite(minutes) and 0 < minutes <= threading.TIMEOUT_MAX / 60):
        print(f"error: --minutes must be finite, above 0 and at most {threading.TIMEOUT_MAX / 60:g}, got {minutes!r}",
              file=sys.stderr)
        return 2
    seed = args.seed if args.seed is not None else secrets.randbits(64)
    try:
        np.random.SeedSequence(seed)
    except (ValueError, TypeError) as exc:  # numpy rejects a negative seed
        print(f"error: --seed must be a non-negative integer, got {seed!r} ({exc})", file=sys.stderr)
        return 2
    out: Path = args.out if args.out is not None else session.new_out()
    checked = session.check_out(out)
    if not is_successful(checked):
        print(f"error: {checked.failure()}", file=sys.stderr)
        return 2

    if not _is_elevated():  # no relaunch here (RT-FIX-1b): the operator starts this from an administrator terminal
        print(f"error: {runtime.NOT_ELEVATED_CLI_TEXT}", file=sys.stderr)
        return 11

    settings = session.RunSettings(map_csv=args.map, character=args.character, laps=args.laps, minutes=minutes,
                                   countdown_s=_countdown_s, seed=seed, out=out)
    return session.RunSession(
        settings, runtime.SharedFrame(), on_event=lambda event: None, make_positions=_make_positions,
        wait_first_frame=_wait_first_frame, make_link=_make_link, make_clock=_make_clock,
        make_stop=_make_stop_control, start_capture=_start_capture, sigint=True).run()


if __name__ == "__main__":
    sys.exit(main())
