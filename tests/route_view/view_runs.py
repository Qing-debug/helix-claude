"""Hand-written runs for the route view tests (route view plan tasks RV-6 to RV-8; PF81 m4).

A flat test helper, imported as `from view_runs import ...` (`tests/CLAUDE.md`, "Layout"); it has no `test_` prefix, so
pytest does not collect it. `G` is the plan's hand-written run on the FF2 map (RV-6, "The hand-written run G"):
steps 1-6 are RV-4's case T1, steps 16-22 its case T3, step 14 its case T13. `make_folder` writes a run folder with the
format of `step_log` (so the lines are V1's), and `make_legs_folder` one with only `session.json` and `legs.jsonl`.
"""

import dataclasses
import hashlib
import json
from pathlib import Path

from src.route.leg_log import LegOutcome, LegRecord, MoveEntry, leg_to_json
from src.route.params import MovementParams
from src.route.step_log import MAP_COPY, STEPS_FILE, RunHeader, event_line, header_line

REPO_ROOT = Path(__file__).resolve().parents[2]
FF2 = REPO_ROOT / "tests" / "fixtures" / "fossil_foundry_2.csv"
FF2_ROTATION = ((113, 60), (82, 60), (48, 61), (58, 46), (49, 28), (101, 24), (105, 42))
GIT_HEAD = "a" * 40

# (ev, seconds, *values in FIELDS order): the table of the plan, one row per step k.
G: list[tuple] = [
    ("leg", 1.000, 0, 5, (49, 28), (101, 24)),
    ("planned", 1.100, (49, 28), "teleport", "RIGHT", None, 5.175, (66, 28)),
    ("sent", 1.331, "ok", 231.0, 228.4, ((0, 2), (310, 18), (810, 2), (280, 0))),
    ("settled", 1.900, (68, 27), False),
    ("planned", 1.950, (68, 27), "walk", "RIGHT", 1375.0, 4.625, (79, 24)),
    ("sent", 2.160, "ok", 210.0, 205.0, ((0, 2), (13750, 0))),
    ("settled", 3.600, (80, 24), False),
    ("planned", 3.650, (80, 24), "walk", "RIGHT", 2625.0, 3.4, (101, 24)),
    ("sent", 3.850, "ok", 195.0, 190.0, ((0, 2), (26250, 0))),
    ("settled", 5.900, None, False),
    ("read_failed", 6.000, "miss"),
    ("read_failed", 6.050, "miss"),
    ("read_failed", 6.100, "miss"),
    ("read_failed", 6.150, "stale"),
    ("no_plan", 6.200, (86, 37), "off-graph: (86, 37)"),
    ("leg_end", 7.000, "timed_out", 3, 1, 0),
    ("leg", 7.100, 0, 6, (101, 24), (105, 42)),
    ("planned", 7.200, (101, 24), "walk", "RIGHT", 500.0, 1.05, (105, 42)),
    ("sent", 7.400, "ok", 200.0, 196.0, ((0, 2), (5000, 0))),
    ("settled", 7.950, (105, 24), False),
    ("planned", 8.000, (105, 24), "drop", None, None, 0.5, (105, 42)),
    ("sent", 8.100, "ok", 150.0, 148.0, ((0, 8), (300, 40), (800, 8), (250, 0))),
    ("settled", 8.600, (105, 42), False),
    ("reached", 8.650, (105, 42)),
    ("leg_end", 8.650, "reached", 0, 0, 0),
    ("end", 9.000, "max_laps", 0),
]


def ns(seconds: float) -> int:
    return round(seconds * 1e9)


def default_header(map_bytes: bytes | None = None, **changes) -> RunHeader:
    data = FF2.read_bytes() if map_bytes is None else map_bytes
    fields = dict(source="walk", clock="perf_counter_ns", map=MAP_COPY, map_sha256=hashlib.sha256(data).hexdigest(),
                  character="kanna", params=dataclasses.asdict(MovementParams()), rotation=FF2_ROTATION,
                  git_head=GIT_HEAD)
    fields.update(changes)
    return RunHeader(**fields)


def event_lines(events: list[tuple]) -> list[bytes]:
    lines = []
    for ev, seconds, *values in events:
        lines.append(event_line((ev, ns(seconds), *values)).unwrap())
    return lines


def make_folder(tmp_path: Path, events: list[tuple] | None = None, header: RunHeader | None = None) -> Path:
    """A run folder: the FF2 fixture as `map.csv` and a `steps.jsonl` of `header` (default) and `events` (default G)."""
    (tmp_path / MAP_COPY).write_bytes(FF2.read_bytes())
    lines = [header_line(header or default_header()).unwrap(), *event_lines(G if events is None else events)]
    (tmp_path / STEPS_FILE).write_bytes(b"".join(lines))
    return tmp_path


def make_legs_folder(tmp_path: Path) -> Path:
    """A folder of `session.json` + `legs.jsonl` (two legs, the second ends `LINK_DOWN`), the map the FF2 fixture."""
    session = {"seed": 1, "params": dataclasses.asdict(MovementParams()), "leg_timeout_s": 30.0, "map": str(FF2),
               "map_sha256": hashlib.sha256(FF2.read_bytes()).hexdigest(), "git_head": GIT_HEAD,
               "start_utc": "2026-10-27T10:00:00+00:00", "class": "kanna", "laps": 1, "minutes": None, "record": False}
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")
    leg0 = LegRecord(
        lap=0, leg=0, src=None, dst=(113, 60), start_ns=1_000_000_000, end_ns=9_000_000_000,
        outcome=LegOutcome.REACHED,
        moves=(MoveEntry(2_000_000_000, (62, 58), "teleport", "RIGHT", None, 3.725, "ok", (82, 58)),
               MoveEntry(5_000_000_000, (82, 58), "walk", "RIGHT", 1500.0, 1.2, "ok", (113, 60))),
        misses=0, off_graph=0, stalls=0)
    leg1 = LegRecord(
        lap=0, leg=1, src=(113, 60), dst=(82, 60), start_ns=10_000_000_000, end_ns=19_000_000_000,
        outcome=LegOutcome.LINK_DOWN,
        moves=(MoveEntry(11_000_000_000, (113, 60), "walk", "LEFT", 900.0, 2.0, "ok", (100, 60)),
               MoveEntry(14_000_000_000, (100, 60), "walk", "LEFT", 800.0, 1.0, "LINK_LOST", None)),
        misses=1, off_graph=2, stalls=3)
    with (tmp_path / "legs.jsonl").open("ab") as f:
        for record in (leg0, leg1):
            f.write((json.dumps(leg_to_json(record)) + "\n").encode())
    return tmp_path
