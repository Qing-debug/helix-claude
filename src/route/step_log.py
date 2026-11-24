"""Step log (route view plan task RV-1, contract V1): the line format of `steps.jsonl`, its readers and its writer.

The code is in three modules, and this one re-exports their public names, so callers import them from here:
`step_log_format` (the file names of a run folder, the event table `FIELDS`, the run header, the line encoders
`header_line` and `event_line` and the line decoder `parse_line`), `step_log_reader` (the incremental reader for a file
that is still being written, `StepTail`, and the whole-folder reader `read_run`, with its legs-only fallback for a
folder that has `session.json` and `legs.jsonl` but no `steps.jsonl`) and `step_log_writer` (the writer `StepLog`, plan
task RV-2, contract V3). They import the standard library and `returns` only, besides each other (plan hard rule 6;
`step_log` is in the bottom layer of import-linter contract f).

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
- `planned` and `settled` lines may end with a `frame_ns` key (`EXTRA_FIELDS`, WALKFIX Task 6): the stamp of the minimap
  picture the line's position was read from (`time.perf_counter_ns()` when Windows handed it over, the clock of
  `t_ns`), an int, or null on a `settled` that has no position. `VERSION` stays 1: the key is optional, so a reader
  of the old format ignores it (as it ignores any key it does not know) and this one reads an old line without it
  (`data` then has no `frame_ns` key, not a null one). `event_line` takes it as one value more than `FIELDS` has and
  refuses one that is not an int or None; `parse_line` makes a `frame_ns` that is neither an int nor null a bad line.
  A `StepLog` given no stamp source writes the old lines byte for byte.
- `StepLog`: the writer thread itself flushes, fsyncs and closes `steps.jsonl` when it reaches the sentinel, so `close`
  never touches a file that a slow writer is still writing; when `close` gives up waiting, the thread still finishes
  the queue and closes the file. `close` reports the first write or encode failure if there was one, else "writer
  still busy" if the join timed out, and a repeated `close` returns the first one's result. A second `start` is a
  `Failure` ("step log already started"); a header that cannot be encoded or written is a `Failure` too, and a
  `steps.jsonl` that `start` created for it is removed again, so a retry into the same folder is not refused as
  "exists". The writer closes the file even when its last flush fails (a full disk).
"""

from src.route.step_log_format import (
    EVENTS,
    EXTRA_FIELDS,
    FIELDS,
    MAP_COPY,
    SNAPSHOT,
    STEPS_FILE,
    VERSION,
    RunHeader,
    RunSteps,
    StepEvent,
    event_line,
    header_line,
    parse_line,
)
from src.route.step_log_reader import StepTail, read_run
from src.route.step_log_writer import StepLog

__all__ = [
    "EVENTS",
    "EXTRA_FIELDS",
    "FIELDS",
    "MAP_COPY",
    "SNAPSHOT",
    "STEPS_FILE",
    "VERSION",
    "RunHeader",
    "RunSteps",
    "StepEvent",
    "StepLog",
    "StepTail",
    "event_line",
    "header_line",
    "parse_line",
    "read_run",
]
