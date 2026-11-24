"""Step log format (route view contract V1): the file names of a run folder, the event table, the run header and the
line encoders and decoder of `steps.jsonl`. The format's rules, and where the plan is silent, are in `step_log`'s
docstring; `step_log` re-exports this module's public names, so callers import them from there. It imports the standard
library and `returns` only (plan hard rule 6).
"""

import json
import reprlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from returns.result import Failure, Result, Success

STEPS_FILE = "steps.jsonl"
MAP_COPY = "map.csv"
SNAPSHOT = "minimap.png"
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
# Optional keys after an event's FIELDS (QB283 2d, WALKFIX Task 6): `frame_ns` is the stamp of the minimap picture the
# line's position was read from, `time.perf_counter_ns()` when Windows handed that picture over, the clock of `t_ns`
# and of the keys. The writer adds them only when it was given a stamp source; an old run folder has none.
EXTRA_FIELDS: dict[str, tuple[str, ...]] = {"planned": ("frame_ns",), "settled": ("frame_ns",)}

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
    # the FIELDS of `ev` (positions as tuple[int, int] | None, "keys" as tuple of int pairs), and its EXTRA_FIELDS that
    # the line has (`frame_ns`: int | None)
    data: Mapping[str, Any]


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
    if name == "frame_ns" and not (value is None or _is_int(value)):
        return f"frame_ns must be an int or null, got {reprlib.repr(value)}"
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
    """One JSON line for `(ev, t_ns, *values in FIELDS[ev] order)`, with the event's `EXTRA_FIELDS` values after them
    when it has any (`planned` and `settled` take `frame_ns` last: an int or None); `Failure` for an unknown `ev`, a
    wrong number of values, a non-int `t_ns` (null only for `settled`), a position or `keys` or `frame_ns` that is not
    of the shape `parse_line` takes, or a value that is not strict JSON. Never raises."""
    if not isinstance(event, (tuple, list)) or len(event) < 2:
        return Failure("an event is a tuple (ev, t_ns, *values)")
    ev, t_ns, *values = event
    fields = FIELDS.get(ev) if isinstance(ev, str) else None
    if fields is None:
        return Failure(f"unknown event {reprlib.repr(ev)}")  # reprlib: a plain repr of a deep value raises
    extras = EXTRA_FIELDS.get(ev, ())
    if len(values) not in (len(fields), len(fields) + len(extras)):
        takes = f"{len(fields)} values ({', '.join(fields)})"
        if extras:
            takes += f" or {len(fields) + len(extras)} with {', '.join(extras)} last"
        return Failure(f"event {ev!r} takes {takes}, got {len(values)}")
    if not _t_ns_ok(ev, t_ns):
        return Failure(f"event {ev!r}: t_ns must be an int, got {reprlib.repr(t_ns)}")
    names = (*fields, *extras)[:len(values)]
    for name, value in zip(names, values):
        error = _shape_error(name, value)
        if error is not None:
            return Failure(f"event {ev!r}: {error}")
    obj: dict[str, Any] = {"ev": ev, "t_ns": t_ns}
    obj.update(zip(names, values))
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
    if name == "frame_ns" and not (value is None or _is_int(value)):
        raise ValueError("frame_ns must be an int or null")
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
    for name in EXTRA_FIELDS.get(ev, ()):  # optional: a line from before Task 6 has none, and gets none in `data`
        if name in obj:
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
