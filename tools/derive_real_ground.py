"""Derive the real-ground and live-read fixtures from Hong's saved Fossil Foundry 2 runs (off-graph fix plan, Task 1).

    python tools/derive_real_ground.py --runs DIR --terrain-out PATH --reads-out PATH [--layout PATH]

`DIR` holds the run folders (data/route). Of each run folder the tool opens only `frames/*.png`, `steps.jsonl` and
`map.csv` (a fixed list of names; it never lists a run folder's other files and never touches `keys.jsonl`, the
keylog), and it writes only the two out paths.

`--terrain-out` is the ground the character really stands on, in `sim.world.load_terrain`'s schema, in the map's own
reference: the real ground row minus 3 (FOOT_FROM_GROUND), the row the map editor's nodes sit on. The platform names,
`droppable` and x-extents come from `--layout` (default tests/fixtures/ff2_terrain.json). Ground per x:

  1. each run's median frame, in grey; the first row at or above GROUND_GREY under the platform's line (the candidate);
     the runs' candidates are combined by their (low) median (a column most runs see no bright row in is covered);
  2. bright objects are drawn over the ground in places (the top platform at x 48-56 and 79-96, the floor at x 37,
     49-55, 70-85 and others; OFFD140 section 1): a covered column is not read from the picture. On a flat platform
     (the layout's line varies by at most FLAT_PX) the ground is one row, the most common candidate, and every column
     with another candidate is covered; on a sloped one the covered columns are OBJECT_COLUMNS (the top platform,
     from the plan). For a covered column the ground is the standing reads' mode (dot y + 6 = DOT_TO_GROUND_PX; at
     least MIN_STANDING_READS reads within STANDING_BAND_PX rows of the expected row) or else the expected row (the flat
     level, or the line between the clean columns on either side of the object);
  3. only change points are kept: a run of equal rows keeps its first and last x.

`--reads-out` is the positions the planner logged, with no key data: per run the events `leg` (the leg's dst),
`no_plan`, `planned`, `reached`, `settled` ([t_ms, ev, x, y], dot-centre pixels) and `read_failed` (x, y null), t_ms
from the run's first timed event; and `side_teleports`, [x_from, x_to] for each planned teleport LEFT or RIGHT with the
next `settled` position of the same leg (a stop leaves `settled` with no position: no pair), kept when the character
moved in the commanded direction. There is no |dy| filter: it would drop the steepest ramp landings.

Exit 0 when both files are written; exit 2 with one `error: ...` line on stderr for a usage problem, no run folder
with frames (a run folder without frames is only skipped), run folders whose map.csv nodes differ, a platform with no
bright ground row, a `leg` event without its `to`, or an unreadable input.
"""
import argparse
import ast
import csv
import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

STEPS = "steps.jsonl"
MAP = "map.csv"
FRAMES = "frames"
FRAME_GLOB = "*.png"
DEFAULT_LAYOUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ff2_terrain.json"

GROUND_GREY = 180            # the plan's threshold (OFFD140 section 1)
# the node row is the real ground row minus 3 (Hong rests the 3 px node circle on the ground)
FOOT_FROM_GROUND = 3
DOT_TO_GROUND_PX = 6         # the dot's centre sits 6 px above the ground (+6 in 1642 of 2214 frames, +5 and +7 else)
SCAN_ABOVE_PX = 2            # the scan for a column starts this far above the platform's line there ...
SCAN_BELOW_PX = 12           # ... and stops this far below it
FLAT_PX = 1                  # a platform whose line varies by at most this many rows is flat
# Sloped platforms: where a bright object covers the ground. The plan names 48-56 and 79-96 on the top platform; the
# object's fringe, a dimmer row on the ground row itself, runs on to x 62.
OBJECT_COLUMNS = {"top": ((48, 62), (79, 96))}
MIN_STANDING_READS = 2
STANDING_BAND_PX = 2
STANDING_EVENTS = ("settled", "reached", "planned", "no_plan")   # positions read once the character is at rest
READ_EVENTS = ("no_plan", "planned", "reached", "settled")
SIDE_DIRECTIONS = {"LEFT": -1, "RIGHT": 1}


@dataclass(frozen=True)
class Platform:
    name: str
    droppable: bool
    points: tuple[tuple[int, int], ...]

    def line_y(self, x: int) -> float:
        """The node line (the layout's polyline) at x."""
        for (x0, y0), (x1, y1) in zip(self.points, self.points[1:]):
            if x0 <= x <= x1:
                return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        raise ValueError(f"x={x} is outside platform {self.name!r}")

    @property
    def x_range(self) -> range:
        return range(self.points[0][0], self.points[-1][0] + 1)


class DeriveError(Exception):
    """A usage problem or an unreadable input; main() prints it and exits 2."""


def _load_layout(path: Path) -> tuple[list[int], list[Platform]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        size = [int(v) for v in data["minimap_size"]]
        platforms = [
            Platform(p["name"], bool(p["droppable"]), tuple((int(x), int(y)) for x, y in p["points"]))
            for p in data["platforms"]
        ]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise DeriveError(f"cannot read the layout {path}: {error}") from error
    return size, platforms


def _run_folders(root: Path) -> list[Path]:
    try:
        runs = sorted(p for p in root.iterdir() if p.is_dir() and (p / STEPS).is_file())
    except OSError as error:
        raise DeriveError(f"cannot list {root}: {error}") from error
    if not runs:
        raise DeriveError(f"no run folder with a {STEPS} under {root}")
    return runs


def _read_nodes(run: Path) -> list[tuple[int, int]]:
    try:
        with (run / MAP).open(newline="", encoding="utf-8") as f:
            rows = csv.DictReader(line for line in f if not line.startswith("#"))
            return [tuple(ast.literal_eval(row["pos_in_minimap"])) for row in rows]
    except (OSError, ValueError, SyntaxError, KeyError) as error:
        raise DeriveError(f"cannot read {run / MAP}: {error}") from error


def _read_events(run: Path) -> list[dict]:
    """The step log's events; a torn or foreign line is skipped, as the route view's reader does."""
    events = []
    try:
        text = (run / STEPS).read_text(encoding="utf-8")
    except OSError as error:
        raise DeriveError(f"cannot read {run / STEPS}: {error}") from error
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("ev"), str):
            events.append(event)
    return events


def _median_frame(run: Path) -> np.ndarray | None:
    files = sorted((run / FRAMES).glob(FRAME_GLOB))
    if not files:
        return None
    grey = []
    for f in files:
        frame = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise DeriveError(f"cannot read the frame {f}")
        grey.append(frame)
    return np.median(np.stack(grey), axis=0).astype(np.uint8)


def _first_bright(frame: np.ndarray, x: int, start: int, stop: int) -> int | None:
    for row in range(max(start, 0), min(stop, frame.shape[0])):
        if frame[row, x] >= GROUND_GREY:
            return row
    return None


def _candidates(frames: Sequence[np.ndarray], platform: Platform) -> dict[int, int | None]:
    """Per x: the first bright row under the platform's line in each run's median frame, combined by the median."""
    out: dict[int, int | None] = {}
    for x in platform.x_range:
        line = round(platform.line_y(x))
        rows = [_first_bright(f, x, line - SCAN_ABOVE_PX, line + SCAN_BELOW_PX) for f in frames]
        found = [r for r in rows if r is not None]
        # a dim pixel: most runs see none
        out[x] = statistics.median_low(found) if len(found) * 2 > len(rows) else None
    return out


def _standing_reads(events_by_run: Iterable[list[dict]]) -> dict[int, list[int]]:
    """x -> the dot's y + 6 at every read made once the character was at rest.

    Many polls at one position are one stand, so a read equal to the one before it in the same run is not counted."""
    reads: dict[int, list[int]] = {}
    for events in events_by_run:
        last = None
        for e in events:
            pos = e.get("pos")
            if e["ev"] in STANDING_EVENTS and isinstance(pos, list) and len(pos) == 2 and pos != last:
                reads.setdefault(pos[0], []).append(pos[1] + DOT_TO_GROUND_PX)
            if e["ev"] in STANDING_EVENTS and isinstance(pos, list):
                last = pos
    return reads


def _expected(columns: set[int], clean: dict[int, int]) -> dict[int, int]:
    """The ground row at each covered column: the line between the clean columns on either side of it."""
    xs = sorted(clean)
    out = {}
    for x in columns:
        left = [c for c in xs if c < x]
        right = [c for c in xs if c > x]
        if not left or not right:
            out[x] = clean[left[-1] if left else right[0]]
            continue
        x0, x1 = left[-1], right[0]
        out[x] = round(clean[x0] + (clean[x1] - clean[x0]) * (x - x0) / (x1 - x0))
    return out


def ground_rows(
    platform: Platform, frames: Sequence[np.ndarray], standing: dict[int, list[int]]
) -> tuple[dict[int, int], list[int]]:
    """The real ground row per x of the platform, and the columns a bright object covered."""
    candidates = _candidates(frames, platform)
    ys = [y for _x, y in platform.points]
    if max(ys) - min(ys) <= FLAT_PX:
        found = Counter(c for c in candidates.values() if c is not None)
        if not found:
            raise DeriveError(f"platform {platform.name!r}: no bright ground row under its line in any frame")
        level = found.most_common(1)[0][0]
        clean = {x: level for x, c in candidates.items() if c == level}
    else:
        spans = OBJECT_COLUMNS.get(platform.name, ())
        clean = {
            x: c for x, c in candidates.items()
            if c is not None and not any(a <= x <= b for a, b in spans)
        }
        if not clean:
            raise DeriveError(f"platform {platform.name!r}: no bright ground row under its line in any frame")
    covered = [x for x in platform.x_range if x not in clean]
    expected = _expected(set(covered), clean)
    ground = dict(clean)
    for x in covered:
        near = [r for r in standing.get(x, []) if abs(r - expected[x]) <= STANDING_BAND_PX]
        ground[x] = Counter(near).most_common(1)[0][0] if len(near) >= MIN_STANDING_READS else expected[x]
    return dict(sorted(ground.items())), covered


def change_points(rows: dict[int, int]) -> list[list[int]]:
    """[x, y] for the first and last x of every run of equal rows."""
    xs = sorted(rows)
    keep = [
        x for i, x in enumerate(xs)
        if i in (0, len(xs) - 1) or rows[xs[i - 1]] != rows[x] or rows[xs[i + 1]] != rows[x]
    ]
    return [[x, rows[x]] for x in keep]


def build_terrain(
    size: list[int], platforms: Sequence[Platform], medians: Sequence[np.ndarray], events_by_run: list[list[dict]],
    source: str,
) -> tuple[dict, dict[str, list[int]]]:
    standing = _standing_reads(events_by_run)
    out_platforms, covered = [], {}
    for platform in platforms:
        ground, hidden = ground_rows(platform, medians, standing)
        covered[platform.name] = hidden
        foot = {x: row - FOOT_FROM_GROUND for x, row in ground.items()}
        out_platforms.append({"name": platform.name, "droppable": platform.droppable, "points": change_points(foot)})
    return {"_source": source, "minimap_size": size, "platforms": out_platforms}, covered


def _timed_events(events: list[dict]) -> list[dict]:
    return [e for e in events if isinstance(e.get("t_ns"), int)]


def run_reads(events: list[dict]) -> tuple[list[list], list[list[int]]]:
    """One run's [t_ms, ev, x, y] rows and its [x_from, x_to] side teleports."""
    timed = _timed_events(events)
    if not timed:
        return [], []
    t0 = timed[0]["t_ns"]
    rows: list[list] = []
    pairs: list[list[int]] = []
    pending: dict | None = None
    for e in timed:
        ev, t_ms, pos = e["ev"], round((e["t_ns"] - t0) / 1e6), None
        if ev == "leg":
            pending = None
            if not isinstance(e.get("to"), list):
                raise DeriveError(f"a `leg` event without its `to`: {e}")
            rows.append([t_ms, ev, *e["to"]])
        elif ev == "read_failed":
            rows.append([t_ms, ev, None, None])
        elif ev in READ_EVENTS:
            pos = e.get("pos")
            if pos is not None:                      # a settled that follows a stop has no position
                rows.append([t_ms, ev, pos[0], pos[1]])
            if ev == "planned":
                move = SIDE_DIRECTIONS.get(e.get("dir")) if e.get("kind") == "teleport" else None
                pending = e if move is not None else None
            elif ev == "settled" and pending is not None:
                if pos is not None:
                    step = pos[0] - pending["pos"][0]
                    if step * SIDE_DIRECTIONS[pending["dir"]] > 0:
                        pairs.append([pending["pos"][0], pos[0]])
                pending = None
    return rows, pairs


def build_reads(names: list[str], events_by_run: list[list[dict]], source: str) -> dict:
    runs, side = [], []
    for name, events in zip(names, events_by_run):
        rows, pairs = run_reads(events)
        runs.append({"run": name, "events": rows})
        side.extend(pairs)
    return {"_source": source, "runs": runs, "side_teleports": side}


def _write_terrain(path: Path, terrain: dict) -> None:
    """One platform per line, like tests/fixtures/ff2_terrain.json."""
    head = json.dumps({k: terrain[k] for k in ("_source", "minimap_size")})[:-1]
    platforms = (",\n" + "  ").join(json.dumps(p) for p in terrain["platforms"])
    path.write_text(f'{head},\n "platforms": [\n  {platforms}]}}\n', encoding="utf-8")


def _write_reads(path: Path, reads: dict) -> None:
    """One event per line."""
    def run_text(run: dict) -> str:
        events = (",\n" + "    ").join(json.dumps(e, separators=(",", ":")) for e in run["events"])
        return f'{{"run": {json.dumps(run["run"])}, "events": [\n    {events}]}}'

    head = json.dumps({"_source": reads["_source"]})[:-1]
    runs = (",\n" + "  ").join(run_text(r) for r in reads["runs"])
    side = json.dumps(reads["side_teleports"], separators=(",", ":"))
    path.write_text(f'{head},\n "runs": [\n  {runs}],\n "side_teleports": {side}}}\n', encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derive the FF2 real-ground and live-read fixtures from saved runs.")
    parser.add_argument("--runs", required=True, type=Path, help="folder that holds the run folders (data/route)")
    parser.add_argument("--terrain-out", required=True, type=Path)
    parser.add_argument("--reads-out", required=True, type=Path)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT, help="platform names, droppable, x-extents")
    args = parser.parse_args(argv)
    try:
        size, platforms = _load_layout(args.layout)
        runs = _run_folders(args.runs)
        nodes = {tuple(sorted(_read_nodes(r))) for r in runs}
        if len(nodes) != 1:
            raise DeriveError("the run folders do not all hold the same map.csv nodes")
        events_by_run = [_read_events(r) for r in runs]
        medians = [m for m in (_median_frame(r) for r in runs) if m is not None]
        if not medians:
            raise DeriveError("no run folder holds frames")
        names = [r.name for r in runs]
        source = (
            f"derived by tools/derive_real_ground.py from {len(runs)} saved FF2 runs ({names[0]} .. {names[-1]}); "
            f"{len(medians)} with frames"
        )
        terrain, covered = build_terrain(
            size, platforms, medians, events_by_run,
            source + f"; ground in the map's foot reference (real ground row - {FOOT_FROM_GROUND}), change points only",
        )
        reads = build_reads(
            names, events_by_run,
            source + "; positions are the logged dot centres; side_teleports are [x_from, x_to] of planned LEFT/RIGHT "
            "teleports and the next settled position of the same leg; no key data",
        )
    except DeriveError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    _write_terrain(args.terrain_out, terrain)
    _write_reads(args.reads_out, reads)
    side = [abs(b - a) for a, b in reads["side_teleports"]]
    print(f"{len(runs)} runs, {len(medians)} with frames; terrain -> {args.terrain_out}; reads -> {args.reads_out}")
    print(f"columns covered by an object: {covered}")
    print(f"side teleports: {len(side)}, median |dx| {statistics.median(side) if side else None}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
