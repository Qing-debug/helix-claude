"""Step log readers (route view contract V1): `StepTail`, the incremental reader of a `steps.jsonl` that is still being
written, and `read_run`, the whole-folder reader with its legs-only fallback (`session.json` plus `legs.jsonl`). The
rules they follow are in `step_log`'s docstring; `step_log` re-exports `StepTail` and `read_run`. It imports the standard
library, `returns` and `step_log_format` only (plan hard rule 6).
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.route.step_log_format import (
    _LEGS_FILE,
    _SESSION_FILE,
    STEPS_FILE,
    RunHeader,
    RunSteps,
    StepEvent,
    _is_int,
    _opt_str,
    _position,
    _reject_constant,
    _req_str,
    parse_line,
)

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
