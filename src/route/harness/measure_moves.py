"""Movement measurement tool (plan task B20): measure Kanna's moves in the game, for the supervised session (B21 step 3).

    python -m src.route.harness.measure_moves --supervised --out data/route/measure/<UTC stamp> [--reps 10]

Each measured move is one `MoveSample`, appended as one JSON object per line to `<out>/measure.jsonl`;
`summarize` turns the samples into the reach, walk-speed and settle-time numbers B21 steps 4 and 5 compare with
`Kanna`, `MovementParams` and `SimParams`.

Exit codes: 2 usage (no --supervised, a bad --reps, an --out that is a file or already holds measure.jsonl).

Where the plan is silent:
- `MoveSample.kind` is a `MoveKind` value (`"teleport"`, `"walk"`, `"drop"`); `direction` is the lowercase `Key`
  name (`"left"`, `"right"`, `"up"`, `"down"`) or `None` for a drop. Positions are minimap pixels, top-left origin.
- The vertical reach of a teleport UP is `before[1] - peak_y` (y grows downwards).
- `reach_h_sd` is the sample standard deviation (`statistics.stdev`). A key whose samples are missing is `None`
  (`reach_h_sd` also when fewer than 2 horizontal teleports are kept).
- `--reps` must be at least 2, so every phase (including the `reps // 2` teleport-downs and drops) has a sample.
- The default `--out` is `data/route/measure/<UTC stamp>` under the repo root; a used `--out` is refused as the
  walk CLI refuses one (decision B19-D2, `runtime.check_out`).
"""

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from returns.pipeline import is_successful

from src.route import runtime
from src.route.planner import MoveKind

_REPO = Path(__file__).resolve().parents[3]
MEASURE_FILE = "measure.jsonl"
_OUT_FILES = (MEASURE_FILE,)
_FLAT_DY_PX = 2            # a horizontal teleport counts towards the reach only if |dy| <= this (plan B20)
_MIN_REPS = 2
_HORIZONTAL = frozenset({"left", "right"})


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
    reach_h = [abs(s.after[0] - s.before[0]) for s in teleports
               if s.direction in _HORIZONTAL and abs(s.after[1] - s.before[1]) <= _FLAT_DY_PX]
    reach_v = [s.before[1] - s.peak_y for s in teleports if s.direction == "up" and s.peak_y is not None]
    walks = [s for s in samples if s.kind == MoveKind.WALK and s.held_ms is not None]
    held_s = sum(s.held_ms for s in walks if s.held_ms is not None) / 1000.0
    walked_px = sum(abs(s.after[0] - s.before[0]) for s in walks)
    return {
        "reach_h_mean": statistics.fmean(reach_h) if reach_h else None,
        "reach_h_sd": statistics.stdev(reach_h) if len(reach_h) >= 2 else None,
        "reach_h_n": len(reach_h),
        "reach_v_peak_mean": statistics.fmean(reach_v) if reach_v else None,
        "walk_px_per_s": walked_px / held_s if held_s > 0 else None,
        "teleport_settle_s": _median([s.settle_s for s in teleports]),
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


def _run_supervised(args: argparse.Namespace, out: Path) -> int:
    """The measuring procedure (B20 piece 2). Not built yet: refuses, so nothing is sent."""
    print("error: the measuring procedure is not built yet (B20 piece 2)", file=sys.stderr)
    return 2


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
