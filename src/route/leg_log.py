"""Leg and lap log (plan task B11, contract C9): the records the walker writes and the JSONL file Feature D reads.

`JsonlLegLog` appends one JSON object per line to `legs.jsonl` and `laps.jsonl` in one directory. Each record is
written with one `write` call, then `flush()`, then `os.fsync()` when `fsync` is true, so a crash loses at most the
record being written. A write that cannot happen (the log is closed, the disk fails, a field is not JSON-serialisable)
returns `Failure(str)` and never raises; a record that cannot be serialised writes nothing.

Where the plan is silent: the directory (and its parents) is created if missing, `legs.jsonl` and `laps.jsonl` are
opened for append in binary mode so `\\n` is never turned into `\\r\\n` on Windows, `JsonlLegLog(...)` itself raises
`OSError` when it cannot open (use `open` for a `Result`), `close` is idempotent, floats are written as `json.dumps`
writes them (a `nan` or `inf` would be written as the bare words `NaN` / `Infinity`; the planner's costs are finite),
and a last line left torn by a crash is not repaired when the file is opened again.
"""

import contextlib
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Protocol

from returns.result import Failure, Result, Success

LEGS_FILE = "legs.jsonl"
LAPS_FILE = "laps.jsonl"


class LegOutcome(StrEnum):
    REACHED = "reached"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"
    LINK_DOWN = "link_down"


@dataclass(frozen=True)
class MoveEntry:
    t_ns: int
    pos: tuple[int, int]
    kind: str
    direction: str | None       # `Key.name`, e.g. "RIGHT"; never `str(Key)`
    walk_ms: float | None
    cost_to_go_s: float
    input: str                  # "ok" or the `InputError` name
    settled: tuple[int, int] | None


@dataclass(frozen=True)
class LegRecord:
    lap: int
    leg: int
    src: tuple[int, int] | None  # None for the approach leg (lap 0, leg 0)
    dst: tuple[int, int]
    start_ns: int
    end_ns: int
    outcome: LegOutcome
    moves: tuple[MoveEntry, ...]
    misses: int
    off_graph: int
    stalls: int


@dataclass(frozen=True)
class LapRecord:
    lap: int
    start_ns: int
    end_ns: int
    legs: int
    all_reached: bool


class LegSink(Protocol):
    def write_leg(self, rec: LegRecord) -> Result[None, str]: ...

    def write_lap(self, rec: LapRecord) -> Result[None, str]: ...


class MemorySink:
    """Keeps the records in `.legs` and `.laps`, in write order; never fails."""

    def __init__(self) -> None:
        self.legs: list[LegRecord] = []
        self.laps: list[LapRecord] = []

    def write_leg(self, rec: LegRecord) -> Result[None, str]:
        self.legs.append(rec)
        return Success(None)

    def write_lap(self, rec: LapRecord) -> Result[None, str]:
        self.laps.append(rec)
        return Success(None)


def _move_to_json(m: MoveEntry) -> dict:
    return {
        "t_ns": m.t_ns,
        "pos": list(m.pos),
        "kind": m.kind,
        "dir": m.direction,
        "walk_ms": m.walk_ms,
        "cost_to_go_s": m.cost_to_go_s,
        "input": m.input,
        "settled": None if m.settled is None else list(m.settled),
    }


def leg_to_json(rec: LegRecord) -> dict:
    return {
        "type": "leg",
        "lap": rec.lap,
        "leg": rec.leg,
        "from": None if rec.src is None else list(rec.src),
        "to": list(rec.dst),
        "start_ns": rec.start_ns,
        "end_ns": rec.end_ns,
        "outcome": rec.outcome.value,
        "moves": [_move_to_json(m) for m in rec.moves],
        "misses": rec.misses,
        "off_graph": rec.off_graph,
        "stalls": rec.stalls,
    }


def lap_to_json(rec: LapRecord) -> dict:
    return {
        "type": "lap",
        "lap": rec.lap,
        "start_ns": rec.start_ns,
        "end_ns": rec.end_ns,
        "duration_s": (rec.end_ns - rec.start_ns) / 1e9,
        "legs": rec.legs,
        "all_reached": rec.all_reached,
    }


class JsonlLegLog:
    """Appends leg and lap records to `legs.jsonl` and `laps.jsonl` in `directory` (see the module docstring)."""

    def __init__(self, directory: Path, fsync: bool = True) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self._fsync = fsync
        self._closed = False
        self._legs: BinaryIO = (directory / LEGS_FILE).open("ab")
        try:
            self._laps: BinaryIO = (directory / LAPS_FILE).open("ab")
        except OSError:
            self._legs.close()
            raise

    @classmethod
    def open(cls, directory: Path, fsync: bool = True) -> Result["JsonlLegLog", str]:
        try:
            return Success(cls(directory, fsync))
        except OSError as e:
            return Failure(f"cannot open the leg log in {directory}: {e}")

    def write_leg(self, rec: LegRecord) -> Result[None, str]:
        return self._append(self._legs, leg_to_json(rec))

    def write_lap(self, rec: LapRecord) -> Result[None, str]:
        return self._append(self._laps, lap_to_json(rec))

    def close(self) -> None:
        """Close both files. Every record was flushed when written, so a failing close loses nothing."""
        self._closed = True
        for handle in (self._legs, self._laps):
            with contextlib.suppress(OSError):
                handle.close()

    def _append(self, handle: BinaryIO, obj: dict) -> Result[None, str]:
        if self._closed:
            return Failure("the leg log is closed")
        try:
            line = (json.dumps(obj) + "\n").encode("utf-8")  # serialise first: a bad record writes nothing
            handle.write(line)
            handle.flush()
            if self._fsync:
                os.fsync(handle.fileno())
        except (OSError, TypeError, ValueError) as e:
            return Failure(f"cannot write the {obj.get('type', 'record')} record: {e}")
        return Success(None)
