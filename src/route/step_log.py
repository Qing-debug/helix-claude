"""Step log (route view plan task RV-1, contract V1): the line format of `steps.jsonl` and its readers.

This file holds the format only: the file names of a run folder, the event table (`FIELDS`), the run header, the line
encoders (`header_line`, `event_line`), the line decoder (`parse_line`), the incremental reader for a file that is still
being written (`StepTail`) and the whole-folder reader (`read_run`, with its legs-only fallback for a folder that has
`session.json` and `legs.jsonl` but no `steps.jsonl`), and the writer (`StepLog`, plan task RV-2, contract V3). It
imports the standard library and `returns` only (plan hard rule 6; it joins the bottom layer of import-linter contract f).

One JSON object per line, strict JSON (`allow_nan=False`, default separators), UTF-8, ended by `\\n`; the writer
appends in binary mode so `\\n` stays `\\n` on Windows (as `leg_log.py`). A reader strips a `\\r` before the `\\n`. A
line that has no `\\n` yet is not complete: `StepTail` leaves it for the next poll and `read_run` ignores it (a torn
last line is not counted in `bad_lines`). A line that is complete but cannot be parsed is skipped and counted in
`bad_lines`. One run per file: a `run` line anywhere but line 1 is a bad line.

Where the plan is silent (decided here, each local and reversible):
- `parse_line` accepts extra keys in a line and ignores them (so a later writer may add keys); a missing field of an
  event, or a missing key of the `run` header (except `wall_ns` and `perf_ns`), is a `Failure` naming it.
- `parse_line` refuses a `run` line whose `version` is not `VERSION`, and a `t_ns` that is not an int (null only for a
  `settled`, which is the legs-only fallback's); `event_line` refuses the same, so the two stay symmetric.
- Positions (`pos`, `toward`, `from`, `to`) must be `[x, y]` ints or null, and `keys` a list of `[delta, mask]` int
  pairs; any other shape is a `Failure` (for `parse_line`, a bad line for the readers).
- A blank line counts as a bad line. A `StepTail` whose file shrinks below its offset returns `[]` and waits.
- `read_run` on a `steps.jsonl` needs a `run` header as the first line; if line 1 is bad or is not a header, or is not
  complete, it is a `Failure` naming the file.
- Legs-only: the header's `clock` is `"perf_counter_ns"` (the walk's real clock); `git_head` and `map_sha256` come from
  `session.json` when present (null when not); a `session.json` without `class`, `params` or `map` is a `Failure`
  naming the key and the file. Only a `leg` line of `legs.jsonl` makes events; any other line is a bad line.
- Legs-only events per leg: `leg`, then per move a `planned` (`toward` null) and a `settled` (`t_ns` null,
  `stopped` false, `pos` the move's `settled`), then `leg_end`. The `settled` after the last move of a `LINK_DOWN` leg
  is left out when the move's `settled` is null (the move never completed); nothing else is left out. There is no
  `sent` and no `end` event (`ended` is True).
- `StepLog`: the writer thread itself flushes, fsyncs and closes `steps.jsonl` when it reaches the sentinel, so `close`
  never touches a file that a slow writer is still writing; when `close` gives up waiting, the thread still finishes
  the queue and closes the file. `close` reports the first write or encode failure if there was one, else "writer
  still busy" if the join timed out, and a repeated `close` returns the first one's result. A second `start` is a
  `Failure` ("step log already started"); a header that cannot be encoded or written is a `Failure` too (a file that
  was created stays, so a retry into the same folder fails as "exists").
"""

import json
import math
import os
import queue
import reprlib
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

STEPS_FILE = "steps.jsonl"
MAP_COPY = "map.csv"
SNAPSHOT = "minimap.png"
VIEW_LOG = "view.log"
VIEW_CLAIM = "view.claim"
VIEW_READY = "view.ready"
VERSION = 1

# Field names after "ev" and "t_ns", in this order, per event (spec 5.2 table). "run" has no t_ns.
FIELDS: dict[str, tuple[str, ...]] = {
    "leg": ("lap", "leg", "from", "to"),
    "read_failed": ("why",),
    "no_plan": ("pos", "why"),
    "reached": ("pos",),
    "stall": ("best_cost_s",),
    "planned": ("pos", "kind", "dir", "walk_ms", "cost_to_go_s", "toward"),
    "sent": ("input", "reaction_ms", "waited_ms", "keys"),
    "settled": ("pos", "stopped"),
    "leg_end": ("outcome", "misses", "off_graph", "stalls"),
    "end": ("reason", "code"),
}
EVENTS = tuple(FIELDS)  # every event name except "run"

_RUN = "run"
_LEGS_FILE = "legs.jsonl"
_SESSION_FILE = "session.json"
_POSITION_FIELDS = frozenset({"pos", "toward", "from", "to"})
_HEADER_KEYS = ("ev", "version", "source", "clock", "map", "map_sha256", "class", "params", "rotation", "git_head",
                "wall_ns", "perf_ns")


@dataclass(frozen=True)
class RunHeader:
    """Line 1 of steps.jsonl; the JSON key order is `_HEADER_KEYS` (`character` is the key `class`)."""

    source: str
    clock: str  # "perf_counter_ns" | "sim_ns"
    map: str  # "map.csv" for walk and sim; the session's map path for a legs-only folder
    map_sha256: str | None
    character: str
    params: Mapping[str, Any]
    rotation: tuple[tuple[int, int], ...] | None  # None for a legs-only folder (the viewer takes it from the map)
    git_head: str | None
    wall_ns: int | None = None  # time.time_ns() and the run clock's now_ns(), read back to back (RV4-D2 A);
    perf_ns: int | None = None  # None for sim and legs-only headers
    version: int = VERSION


@dataclass(frozen=True)
class StepEvent:
    ev: str  # one of EVENTS
    t_ns: int | None  # None only for a legs-only `settled`
    data: Mapping[str, Any]  # the FIELDS of `ev`; positions as tuple[int, int] | None, "keys" as tuple of int pairs


@dataclass(frozen=True)
class RunSteps:
    header: RunHeader
    events: tuple[StepEvent, ...]
    origin: str  # "steps" (steps.jsonl) | "legs" (legs-only fallback)
    ended: bool  # the last event is "end" (always True for "legs")
    bad_lines: int


# ---------------------------------------------------------------------------------------------------------------
# encoding


def _encode(obj: dict[str, Any]) -> Result[bytes, str]:
    try:
        return Success((json.dumps(obj, allow_nan=False) + "\n").encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as e:
        return Failure(f"cannot encode the {obj.get('ev', '?')} line: {e}")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int_pair(value: object) -> bool:
    return isinstance(value, (tuple, list)) and len(value) == 2 and _is_int(value[0]) and _is_int(value[1])


def _shape_error(name: str, value: object) -> str | None:
    """Why `value` cannot be field `name` (the shapes `parse_line` accepts), or None when it can."""
    if name in _POSITION_FIELDS and not (value is None or _int_pair(value)):
        return f"{name} must be [x, y] ints or null, got {reprlib.repr(value)}"
    if name == "keys" and not (isinstance(value, (tuple, list)) and all(_int_pair(pair) for pair in value)):
        return f"keys must be a list of [delta, mask] int pairs, got {reprlib.repr(value)}"
    return None


def header_line(header: RunHeader) -> Result[bytes, str]:
    """One JSON line ending `b"\\n"` for the run header; `Failure` (never a raise) if it cannot be encoded."""
    try:
        rotation = None if header.rotation is None else [list(p) for p in header.rotation]
        obj: dict[str, Any] = {
            "ev": _RUN,
            "version": header.version,
            "source": header.source,
            "clock": header.clock,
            "map": header.map,
            "map_sha256": header.map_sha256,
            "class": header.character,
            "params": dict(header.params),
            "rotation": rotation,
            "git_head": header.git_head,
            "wall_ns": header.wall_ns,
            "perf_ns": header.perf_ns,
        }
    except (TypeError, ValueError) as e:
        return Failure(f"cannot encode the run line: {e}")
    return _encode(obj)


def event_line(event: tuple) -> Result[bytes, str]:
    """One JSON line for `(ev, t_ns, *values in FIELDS[ev] order)`; `Failure` for an unknown `ev`, a wrong number of
    values, a non-int `t_ns` (null only for `settled`), a position or `keys` that is not ints of the shape
    `parse_line` takes, or a value that is not strict JSON. Never raises."""
    if not isinstance(event, (tuple, list)) or len(event) < 2:
        return Failure("an event is a tuple (ev, t_ns, *values)")
    ev, t_ns, *values = event
    fields = FIELDS.get(ev) if isinstance(ev, str) else None
    if fields is None:
        return Failure(f"unknown event {reprlib.repr(ev)}")  # reprlib: a plain repr of a deep value raises
    if len(values) != len(fields):
        return Failure(f"event {ev!r} takes {len(fields)} values ({', '.join(fields)}), got {len(values)}")
    if not _t_ns_ok(ev, t_ns):
        return Failure(f"event {ev!r}: t_ns must be an int, got {reprlib.repr(t_ns)}")
    for name, value in zip(fields, values):
        error = _shape_error(name, value)
        if error is not None:
            return Failure(f"event {ev!r}: {error}")
    obj: dict[str, Any] = {"ev": ev, "t_ns": t_ns}
    obj.update(zip(fields, values))
    return _encode(obj)


def _t_ns_ok(ev: str, t_ns: object) -> bool:
    return _is_int(t_ns) or (t_ns is None and ev == "settled")


# ---------------------------------------------------------------------------------------------------------------
# decoding


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not strict JSON")


def _position(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, list) and len(value) == 2 and _is_int(value[0]) and _is_int(value[1]):
        return (value[0], value[1])
    raise ValueError(f"expected [x, y] or null, got {value!r}")


def _key_pairs(value: Any) -> tuple[tuple[int, int], ...]:
    if isinstance(value, list):
        pairs = [_position(item) for item in value]
        if all(pair is not None for pair in pairs):
            return tuple(pair for pair in pairs if pair is not None)
    raise ValueError(f"expected a list of [delta, mask] pairs, got {value!r}")


def _field_value(name: str, value: Any) -> Any:
    if name in _POSITION_FIELDS:
        return _position(value)
    if name == "keys":
        return _key_pairs(value)
    return value


def _opt_str(obj: Mapping[str, Any], key: str) -> str | None:
    value = obj[key]
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"{key} must be a string or null")


def _req_str(obj: Mapping[str, Any], key: str) -> str:
    value = obj[key]
    if isinstance(value, str):
        return value
    raise ValueError(f"{key} must be a string")


def _opt_int(obj: Mapping[str, Any], key: str) -> int | None:
    value = obj.get(key)
    if value is None or _is_int(value):
        return value
    raise ValueError(f"{key} must be an int or null")


def _header_from_json(obj: Mapping[str, Any]) -> RunHeader:
    for key in _HEADER_KEYS:
        if key not in ("wall_ns", "perf_ns") and key not in obj:
            raise ValueError(f"the run line has no {key!r}")
    version = obj["version"]
    if version != VERSION or not _is_int(version):
        raise ValueError(f"unsupported steps.jsonl version {version!r} (this reader reads {VERSION})")
    params = obj["params"]
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    raw_rotation = obj["rotation"]
    rotation: tuple[tuple[int, int], ...] | None = None
    if raw_rotation is not None:
        if not isinstance(raw_rotation, list):
            raise ValueError("rotation must be a list or null")
        points = [_position(item) for item in raw_rotation]
        if any(point is None for point in points):
            raise ValueError("rotation holds a null position")
        rotation = tuple(point for point in points if point is not None)
    return RunHeader(
        source=_req_str(obj, "source"),
        clock=_req_str(obj, "clock"),
        map=_req_str(obj, "map"),
        map_sha256=_opt_str(obj, "map_sha256"),
        character=_req_str(obj, "class"),
        params=params,
        rotation=rotation,
        git_head=_opt_str(obj, "git_head"),
        wall_ns=_opt_int(obj, "wall_ns"),
        perf_ns=_opt_int(obj, "perf_ns"),
        version=version,
    )


def _event_from_json(ev: str, obj: Mapping[str, Any]) -> StepEvent:
    if "t_ns" not in obj:
        raise ValueError(f"the {ev} line has no 't_ns'")
    t_ns = obj["t_ns"]
    if not _t_ns_ok(ev, t_ns):
        raise ValueError(f"the {ev} line has t_ns {t_ns!r}, not an int")
    data: dict[str, Any] = {}
    for name in FIELDS[ev]:
        if name not in obj:
            raise ValueError(f"the {ev} line has no {name!r}")
        data[name] = _field_value(name, obj[name])
    return StepEvent(ev, t_ns, data)


def parse_line(line: bytes) -> Result[RunHeader | StepEvent, str]:
    """Decode one line (a trailing `\\r\\n` or `\\n` is stripped). `Failure` for anything that is not a valid `run`
    header or event line. Never raises."""
    try:
        text = line.rstrip(b"\r\n").decode("utf-8")
        obj = json.loads(text, parse_constant=_reject_constant)
        if not isinstance(obj, dict):
            return Failure("the line is not a JSON object")
        if "ev" not in obj:
            return Failure("the line has no 'ev'")
        ev = obj["ev"]
        if ev == _RUN:
            return Success(_header_from_json(obj))
        if not isinstance(ev, str) or ev not in FIELDS:
            return Failure(f"unknown event {ev!r}")
        return Success(_event_from_json(ev, obj))
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as e:  # JSONDecodeError is a ValueError
        return Failure(f"bad line: {e}")


# ---------------------------------------------------------------------------------------------------------------
# readers


class StepTail:
    """Reads the complete new lines of a `steps.jsonl` since the last `poll`; remembers a byte offset, so a poll reads
    only what was appended. Never raises."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._offset = 0
        self._line_no = 0
        self.bad_lines = 0

    def poll(self) -> list[RunHeader | StepEvent]:
        try:
            with self._path.open("rb") as handle:
                handle.seek(self._offset)
                data = handle.read()
        except OSError:  # a missing file (or one that cannot be read right now) gives nothing yet
            return []
        end = data.rfind(b"\n") + 1  # 0 when there is no complete line
        if end == 0:
            return []
        self._offset += end
        out: list[RunHeader | StepEvent] = []
        for raw in data[:end].split(b"\n")[:-1]:
            self._line_no += 1
            parsed = parse_line(raw)
            if not is_successful(parsed):
                self.bad_lines += 1
                continue
            item = parsed.unwrap()
            if isinstance(item, RunHeader) and self._line_no != 1:
                self.bad_lines += 1  # a second `run` line counts as bad: one run per file
                continue
            out.append(item)
        return out


def read_run(folder: Path) -> Result[RunSteps, str]:
    """Read a run folder: `steps.jsonl` if it is there, else `session.json` + `legs.jsonl` (events rebuilt from the
    leg log). A `Failure` names the folder (or file) and what is missing. Never raises."""
    folder = Path(folder)
    steps = folder / STEPS_FILE
    legs = folder / _LEGS_FILE
    try:
        if steps.is_file():
            return _read_steps(steps)
        if legs.is_file():
            return _read_legs(folder)
    except OSError as e:
        return Failure(f"cannot read the run in {folder}: {e}")
    return Failure(f"{folder} has neither {STEPS_FILE} nor {_LEGS_FILE}")


def _read_steps(path: Path) -> Result[RunSteps, str]:
    tail = StepTail(path)
    items = tail.poll()
    if not items or not isinstance(items[0], RunHeader):
        return Failure(f"{path}: the first line is not a complete 'run' header")
    events = tuple(item for item in items[1:] if isinstance(item, StepEvent))
    ended = bool(events) and events[-1].ev == "end"
    return Success(RunSteps(items[0], events, "steps", ended, tail.bad_lines))


def _read_legs(folder: Path) -> Result[RunSteps, str]:
    session_path = folder / _SESSION_FILE
    if not session_path.is_file():
        return Failure(f"{folder} has {_LEGS_FILE} but no {_SESSION_FILE}")
    try:
        session = json.loads(session_path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as e:  # RecursionError: nested too deep
        return Failure(f"{session_path} is not valid JSON: {e}")
    if not isinstance(session, dict):
        return Failure(f"{session_path} is not a JSON object")
    for key in ("class", "params", "map"):
        if key not in session:
            return Failure(f"{session_path} has no {key!r}")
    try:
        if not isinstance(session["params"], dict):
            raise ValueError("params must be an object")
        header = RunHeader(
            source="legs",
            clock="perf_counter_ns",
            map=_req_str(session, "map"),
            map_sha256=_opt_str(session, "map_sha256") if "map_sha256" in session else None,
            character=_req_str(session, "class"),
            params=session["params"],
            rotation=None,
            git_head=_opt_str(session, "git_head") if "git_head" in session else None,
        )
    except ValueError as e:
        return Failure(f"{session_path}: {e}")

    events: list[StepEvent] = []
    bad = 0
    data = (folder / _LEGS_FILE).read_bytes()
    for raw in data[: data.rfind(b"\n") + 1].split(b"\n")[:-1]:  # a torn last line is ignored
        try:
            obj = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
            if not isinstance(obj, dict) or obj.get("type") != "leg":
                raise ValueError("not a leg record")
            events.extend(_leg_events(obj))
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, RecursionError):
            bad += 1
    return Success(RunSteps(header, tuple(events), "legs", True, bad))


def _leg_events(leg: Mapping[str, Any]) -> list[StepEvent]:
    """The events of one `leg_log.leg_to_json` record. Raises on a malformed record (the caller counts it)."""
    dst = _position(leg["to"])
    if dst is None:
        raise ValueError("a leg record's `to` must be a position")
    out = [StepEvent("leg", leg["start_ns"], {
        "lap": leg["lap"], "leg": leg["leg"], "from": _position(leg["from"]), "to": dst,
    })]
    moves = leg["moves"]
    if not isinstance(moves, list):
        raise ValueError("moves must be a list")
    for index, move in enumerate(moves):
        settled = _position(move["settled"])
        out.append(StepEvent("planned", move["t_ns"], {
            "pos": _position(move["pos"]), "kind": move["kind"], "dir": move["dir"], "walk_ms": move["walk_ms"],
            "cost_to_go_s": move["cost_to_go_s"], "toward": None,
        }))
        if settled is None and leg["outcome"] == "link_down" and index == len(moves) - 1:
            continue
        out.append(StepEvent("settled", None, {"pos": settled, "stopped": False}))
    out.append(StepEvent("leg_end", leg["end_ns"], {
        "outcome": leg["outcome"], "misses": leg["misses"], "off_graph": leg["off_graph"], "stalls": leg["stalls"],
    }))
    for event in out:
        if event.t_ns is not None and not _is_int(event.t_ns):
            raise ValueError("t_ns must be an int")
    return out


# ---------------------------------------------------------------------------------------------------------------
# writer

_STOP = object()  # the writer thread's sentinel


class StepLog:
    """The walk's step observer (contract V3; it satisfies `walker.StepObserver` structurally and imports nothing from
    the walker). Each event method only puts a tuple of plain values on a `queue.SimpleQueue`, whose `put` never
    blocks; the daemon thread `step-log` encodes each one with `event_line` and appends it to `steps.jsonl` with one
    `write` and a `flush` (no fsync per line, spec 5.2). After `close` every call is dropped (RV3-D2 A2)."""

    def __init__(self) -> None:
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
            return Failure(f"cannot write the run line to {path}: {e}")
        self._file = handle
        self._thread = threading.Thread(target=self._drain, name="step-log", daemon=True)
        self._thread.start()
        return Success(None)

    def leg(self, t_ns: int, lap: int, leg: int, src: tuple[int, int] | None, dst: tuple[int, int]) -> None:
        if self._closed:
            return
        self._queue.put(("leg", t_ns, lap, leg, src, dst))

    def read_failed(self, t_ns: int, why: str) -> None:
        if self._closed:
            return
        self._queue.put(("read_failed", t_ns, why))

    def no_plan(self, t_ns: int, pos: tuple[int, int], why: str) -> None:
        if self._closed:
            return
        self._queue.put(("no_plan", t_ns, pos, why))

    def reached(self, t_ns: int, pos: tuple[int, int]) -> None:
        if self._closed:
            return
        self._queue.put(("reached", t_ns, pos))

    def stall(self, t_ns: int, best_cost_s: float) -> None:
        if self._closed:
            return
        # math.inf (no plan yet in this leg) is not strict JSON: it is written as null (plan choice 3)
        self._queue.put(("stall", t_ns, None if math.isinf(best_cost_s) else best_cost_s))

    def planned(self, t_ns: int, pos: tuple[int, int], kind: str, direction: str | None, walk_ms: float | None,
                cost_to_go_s: float, toward: tuple[int, int] | None) -> None:
        if self._closed:
            return
        self._queue.put(("planned", t_ns, pos, kind, direction, walk_ms, cost_to_go_s, toward))

    def sent(self, t_ns: int, input_name: str, reaction_ms: float | None, waited_ms: float | None,
             keys: tuple[tuple[int, int], ...]) -> None:
        if self._closed:
            return
        self._queue.put(("sent", t_ns, input_name, reaction_ms, waited_ms, keys))

    def settled(self, t_ns: int, pos: tuple[int, int] | None, stopped: bool) -> None:
        if self._closed:
            return
        self._queue.put(("settled", t_ns, pos, stopped))

    def leg_end(self, t_ns: int, outcome: str, misses: int, off_graph: int, stalls: int) -> None:
        if self._closed:
            return
        self._queue.put(("leg_end", t_ns, outcome, misses, off_graph, stalls))

    def end(self, t_ns: int, reason: str | None, code: int | None) -> None:
        """The run's last event (not a walker event: the walk's or the simulator's cleanup puts it)."""
        if self._closed:
            return
        self._queue.put(("end", t_ns, reason, code))

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
        try:
            handle = self._file
            if handle is not None:
                handle.flush()
                os.fsync(handle.fileno())
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
        """Keep the first failure and print it once."""
        if self._failure is None:
            self._failure = message
            print(f"step log write failed (not reported again this run): {message}", file=sys.stderr)
