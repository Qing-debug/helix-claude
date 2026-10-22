"""Soak run writer and focus policy (plan A, Task H2; C5 log format). Reused by V1 (A-PF-D3).

This module's first half is importable on its own: the C5 run writer (`RunWriter`, `command_row`,
`focus_lost_row`, `pong_row`) and the focus policy (`check_focus`, `run_command`). The soak driver and its CLI
build on them.

Run writer. `RunWriter(run_dir, link)` opens `<run dir>/` and, used as a context manager, closes it on every exit
path (normal end, abort, Ctrl-C):
- `commands.jsonl`, `pongs.jsonl`, `link_faults.jsonl`, `faults.jsonl`: created empty on open; every line ends in LF
  (opened with ``newline="\\n"``, A-D3-LF). `drain()` moves `link.take_pongs()` into `pongs.jsonl` and the
  `link.faults()` entries not yet logged into `link_faults.jsonl`; `run_command` calls it after every command and
  `close()` once more.
- `link.log`: a `logging.FileHandler` on logger ``src.input.link``, level WARNING, a filter that stamps
  ``record.perf_ns = time.perf_counter_ns()`` (filters run in `Handler.handle`, in the logging thread, before
  `emit`), format ``%(created).6f %(perf_ns)d %(levelname)s %(message)s``. Attached on open, so open the writer
  before `connect()`; removed and closed by `close()`.
- `session.json`: written by `close()` with its end ns; `focus_pauses` is ``[]`` when there was no pause.

Focus policy (PA-rev m15). `run_command` checks the sink is the foreground before it sets `stimulus_ns`. If not, it
sends nothing and pauses: once a second, at most 10 times, it repeats the bring-to-foreground call (H1's
`Listener.bring_sink_to_foreground()`, whose return value is a fresh `GetForegroundWindow()` check). Sink back: the
pause's ``[start_ns, end_ns]`` goes to `focus_pauses` and the command runs. Not back: the command's row is the
`FOCUS_LOST` row and the caller takes the abort path (`release_all()`, close the writer, non-zero exit).
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from returns.pipeline import is_successful
from returns.result import Result

from src.input.input_layer import ActionRecord
from src.input.link import InputError, SessionInfo
from src.input.protocol import Pong, encode_play

# Seams the tests monkeypatch (as input_layer.py); _clock is perf_counter_ns, the clock of every C5 file.
_clock = time.perf_counter_ns
_sleep = time.sleep

COMMANDS_FILE = "commands.jsonl"
PONGS_FILE = "pongs.jsonl"
LINK_FAULTS_FILE = "link_faults.jsonl"
FAULTS_FILE = "faults.jsonl"
SESSION_FILE = "session.json"
LINK_LOG_FILE = "link.log"
LINK_LOGGER = "src.input.link"
LINK_LOG_FORMAT = "%(created).6f %(perf_ns)d %(levelname)s %(message)s"

FOCUS_LOST = "FOCUS_LOST"  # a harness status, not an InputError (C5)
FOCUS_RECHECKS = 10
FOCUS_RECHECK_PERIOD_S = 1.0
CI_UNIT_MS = 1.25  # PONG itvl is in 1.25 ms units (C1)

COMMAND_FIELDS = ("seq", "action", "intended_ms", "stimulus_ns", "steps", "samples", "reaction_ms", "reaction_index",
                  "waited_ms", "sent_ns", "written_ns", "accepted_ns", "done_ns", "t0_us", "late_us", "status",
                  "error", "bytes")
_PONG_FIELDS = ("itvl_units", "latency", "timeout_units", "mtu")


# ---- C5 rows ---------------------------------------------------------------------------------

def pong_flags(pong: Pong) -> int:
    """C1 flags rebuilt from Pong's bools: bit0 bonded, bit1 encrypted, bit2 authenticated, bit3 subscribed."""
    return (int(pong.bonded) | int(pong.encrypted) << 1 | int(pong.authenticated) << 2
            | int(pong.subscribed) << 3)


def _pong_fields(pong: Pong) -> dict:
    out = {k: getattr(pong, k) for k in _PONG_FIELDS}
    out["flags"] = pong_flags(pong)
    out["board_us"] = pong.board_us
    return out


def pong_row(qpc_ns: int, pong: Pong) -> dict:
    """A C5 `pongs.jsonl` row."""
    return {"qpc_ns": qpc_ns, **_pong_fields(pong)}


def _empty_row(seq: int, action: str, intended_ms: float | None) -> dict:
    row = dict.fromkeys(COMMAND_FIELDS)
    row.update(seq=seq, action=action, intended_ms=intended_ms)
    return row


def command_row(seq: int, action: str, stimulus_ns: int | None, result: Result[ActionRecord, InputError],
                intended_ms: float | None = None) -> dict:
    """A C5 `commands.jsonl` row for an InputLayer call. `intended_ms` is the T passed to walk() (None otherwise).

    Success: `status` "OK", `error` null. Failure: `status` and `error` are the InputError; everything but `seq`,
    `action`, `intended_ms` and `stimulus_ns` is null (C5). `bytes` is the PLAY command as sent, in hex
    (re-encoded from the plan's steps and the report's seq)."""
    row = _empty_row(seq, action, intended_ms)
    row["stimulus_ns"] = stimulus_ns
    if not is_successful(result):
        row["status"] = row["error"] = str(result.failure())
        return row
    rec: ActionRecord = result.unwrap()
    rep = rec.report
    row.update(
        steps=[[s.delta_100us, int(s.mask)] for s in rec.plan.steps],
        samples=[{"type": str(s.type), "chosen_ms": s.ms, "wire_100us": s.wire_100us, "index": s.index,
                  "step_from": s.step_from, "step_to": s.step_to} for s in rec.plan.samples],
        reaction_ms=rec.reaction.ms if rec.reaction is not None else None,
        reaction_index=rec.reaction.index if rec.reaction is not None else None,
        waited_ms=rec.waited_ms,
        sent_ns=rep.sent_ns, written_ns=rep.written_ns, accepted_ns=rep.accepted_ns, done_ns=rep.done_ns,
        t0_us=rep.done.t0_us, late_us=list(rep.done.late_us),
        status="OK", error=None,
        bytes=encode_play(rep.seq, rec.plan.steps).unwrap().hex(),
    )
    return row


def focus_lost_row(seq: int, action: str, intended_ms: float | None = None) -> dict:
    """The C5 row of a command never sent because the sink did not get the foreground back."""
    row = _empty_row(seq, action, intended_ms)
    row["status"] = row["error"] = FOCUS_LOST
    return row


def git_head() -> str | None:
    """`git rev-parse HEAD` of this repo, or None if git is unavailable."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


# ---- the run writer ----------------------------------------------------------------------------

class _Link(Protocol):
    def take_pongs(self) -> list[tuple[int, Pong]]: ...
    def faults(self) -> list[tuple[int, InputError]]: ...


def _stamp_perf_ns(record: logging.LogRecord) -> bool:
    record.perf_ns = time.perf_counter_ns()
    return True


class RunWriter:
    """Writes one run's C5 files into `run_dir`. Use as a context manager (or call `open()` / `close()`).

    `session` is the dict written to `session.json`; callers set `process_check` (and anything else C5 lists) on
    it. `record_connect()` fills `mtu`, `pong`, `ci_ms` from the first successful `connect()`."""

    def __init__(self, run_dir: str | Path, link: _Link, *, run_id: str | None = None, seed: int | None = None,
                 git_sha: str | None = None) -> None:
        self.run_dir = Path(run_dir)
        self._link = link
        self.session: dict = {
            "run_id": run_id if run_id is not None else self.run_dir.name,
            "seed": seed,
            "git_sha": git_sha if git_sha is not None else git_head(),
            "mtu": None, "pong": None, "ci_ms": None,
            "start_ns": None, "end_ns": None,
            "process_check": None,
            "focus_pauses": [],
        }
        self._files: dict[str, object] = {}
        self._faults_logged = 0
        self._handler: logging.FileHandler | None = None
        self._logger_level: int | None = None
        self._closed = False

    def __enter__(self) -> RunWriter:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        for name in (COMMANDS_FILE, PONGS_FILE, LINK_FAULTS_FILE, FAULTS_FILE):
            self._files[name] = open(self.run_dir / name, "w", encoding="utf-8", newline="\n")
        self._attach_link_log()
        self.session["start_ns"] = _clock()

    def _attach_link_log(self) -> None:
        handler = logging.FileHandler(self.run_dir / LINK_LOG_FILE, encoding="utf-8")
        handler.setLevel(logging.WARNING)
        handler.addFilter(_stamp_perf_ns)
        handler.setFormatter(logging.Formatter(LINK_LOG_FORMAT))
        logger = logging.getLogger(LINK_LOGGER)
        if logger.getEffectiveLevel() > logging.WARNING:  # let WARNING records reach the handler
            self._logger_level = logger.level
            logger.setLevel(logging.WARNING)
        logger.addHandler(handler)
        self._handler = handler

    def _detach_link_log(self) -> None:
        if self._handler is None:
            return
        logger = logging.getLogger(LINK_LOGGER)
        logger.removeHandler(self._handler)
        self._handler.close()
        self._handler = None
        if self._logger_level is not None:
            logger.setLevel(self._logger_level)
            self._logger_level = None

    def _write(self, name: str, row: dict) -> None:
        fh = self._files[name]
        fh.write(json.dumps(row) + "\n")
        fh.flush()

    def write_command(self, row: dict) -> None:
        self._write(COMMANDS_FILE, row)

    def write_fault(self, row: dict) -> None:
        """Append one `faults.jsonl` row, as built by the caller (C5)."""
        self._write(FAULTS_FILE, row)

    def record_connect(self, info: SessionInfo) -> None:
        if self.session["mtu"] is not None:
            return
        self.session.update(mtu=info.mtu, pong=_pong_fields(info.pong), ci_ms=info.pong.itvl_units * CI_UNIT_MS)

    def add_focus_pause(self, start_ns: int, end_ns: int) -> None:
        self.session["focus_pauses"].append([start_ns, end_ns])

    def drain(self) -> None:
        """Pongs taken since the last drain → pongs.jsonl; new faults() entries → link_faults.jsonl."""
        for ns, pong in self._link.take_pongs():
            self._files[PONGS_FILE].write(json.dumps(pong_row(ns, pong)) + "\n")
        entries = self._link.faults()
        for ns, error in entries[self._faults_logged:]:
            self._files[LINK_FAULTS_FILE].write(json.dumps({"qpc_ns": ns, "error": str(error)}) + "\n")
        self._faults_logged = max(self._faults_logged, len(entries))
        self._files[PONGS_FILE].flush()
        self._files[LINK_FAULTS_FILE].flush()

    def close(self) -> None:
        """Drain, write session.json (end ns = now), remove link.log's handler, close the files. Safe twice."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._files:
                self.drain()
        finally:
            try:
                self.session["end_ns"] = _clock()
                with open(self.run_dir / SESSION_FILE, "w", encoding="utf-8", newline="\n") as fh:
                    json.dump(self.session, fh, indent=2)
                    fh.write("\n")
            finally:
                self._detach_link_log()
                for fh in self._files.values():
                    fh.close()


# ---- focus policy ----------------------------------------------------------------------------

class Sink(Protocol):
    """H1's Listener: both calls check GetForegroundWindow() (bring_sink_to_foreground returns the re-check)."""

    def sink_is_foreground(self) -> bool: ...
    def bring_sink_to_foreground(self) -> bool: ...


@dataclass(frozen=True)
class FocusCheck:
    ok: bool  # the sink is the foreground: the command may run
    pause: tuple[int, int] | None  # (start_ns, end_ns) of a pause that ended with the sink back


def check_focus(sink: Sink) -> FocusCheck:
    """The focus check before a command, with the pause and re-checks if the sink is not in front."""
    if sink.sink_is_foreground():
        return FocusCheck(True, None)
    start_ns = _clock()
    for _ in range(FOCUS_RECHECKS):
        _sleep(FOCUS_RECHECK_PERIOD_S)
        if sink.bring_sink_to_foreground():
            return FocusCheck(True, (start_ns, _clock()))
    return FocusCheck(False, None)


@dataclass(frozen=True)
class CommandOutcome:
    row: dict  # the commands.jsonl row written
    result: Result[ActionRecord, InputError] | None  # None: FOCUS_LOST, nothing sent
    returned_ns: int | None  # perf_counter_ns right after the InputLayer call returned (None: FOCUS_LOST)

    @property
    def focus_lost(self) -> bool:
        return self.result is None


def run_command(writer: RunWriter, sink: Sink, seq: int, action: str,
                call: Callable[[int], Result[ActionRecord, InputError]],
                intended_ms: float | None = None) -> CommandOutcome:
    """Focus policy, then one InputLayer call, then its row and a drain.

    `call(stimulus_ns)` makes the InputLayer call, e.g. ``lambda s: layer.walk(Key.LEFT, t, stimulus_ns=s)``.
    On `FOCUS_LOST` the row is written and nothing is sent; the caller then takes the abort path."""
    focus = check_focus(sink)
    if focus.pause is not None:
        writer.add_focus_pause(*focus.pause)
    if not focus.ok:
        row = focus_lost_row(seq, action, intended_ms)
        writer.write_command(row)
        writer.drain()
        return CommandOutcome(row, None, None)
    stimulus_ns = _clock()
    result = call(stimulus_ns)
    returned_ns = _clock()
    row = command_row(seq, action, stimulus_ns, result, intended_ms)
    writer.write_command(row)
    writer.drain()
    return CommandOutcome(row, result, returned_ns)
