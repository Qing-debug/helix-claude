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

Soak driver (second half). `plan_mix` is the seeded command mix, `run_soak` the driver (MapleStory check at start
and every 60 s, `wait_for_click` before connect (the operator clicks the sink; up to START_WAIT_S, then a clean
abort), the focus policy before each command, `--min-hours` on H3's connected span, faults at seeded
command indexes with their `faults.jsonl` rows, `release_all()` on every abort and on Ctrl-C), `main` the CLI:

    python -m src.input.harness.soak --commands 10000 --seed 1 [--min-hours H] [--out DIR] [--short]
                                     [--faults reset_board=5,bluetooth_off_on=5]
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import logging
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.input.harness import faults, listener
from src.input.input_layer import ActionRecord, InputLayer
from src.input.link import BleLink, InputError, SessionInfo
from src.input.protocol import Key, Pong, encode_play
from src.input.timing import TimingModel, TimingType

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
# Start wait (orchestrator ruling 18): an agent-launched sink cannot take the foreground itself, so the run waits for
# the operator's click. 120 s bounds the time the listener blocks the bot keys if nobody clicks.
START_WAIT_S = 120.0
START_POLL_S = 0.25
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


def wait_for_click(sink: Sink, tick: Callable[[], None] = lambda: None, timeout_s: float = START_WAIT_S) -> bool:
    """Before the first command: one bring-to-foreground try; if the sink is not in front, prompt the operator to
    click it and poll GetForegroundWindow() every START_POLL_S until it is, or `timeout_s` passed (False).
    `tick()` runs on every poll (the driver's 60 s MapleStory check); an exception from it ends the wait."""
    if sink.bring_sink_to_foreground():
        return True
    print(f"\n>>> CLICK the window titled '{listener.SINK_TITLE}' now, then press no keys. Waiting up to "
          f"{timeout_s:g} s; nothing is sent to the board before the click. The bot keys (arrows, E, Space, left "
          f"Alt) are blocked system-wide until the run ends.\n", file=sys.stderr, flush=True)
    start_ns = _clock()
    deadline_ns = start_ns + int(timeout_s * 1e9)
    while _clock() < deadline_ns:
        _sleep(START_POLL_S)
        tick()
        if sink.sink_is_foreground():
            print(f"sink in front after {(_clock() - start_ns) / 1e9:.1f} s; starting", file=sys.stderr, flush=True)
            return True
    return False


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


# ---- command mix (plan H2) -----------------------------------------------------------------------

ARROWS = (Key.LEFT, Key.RIGHT, Key.UP, Key.DOWN)
TAP_KEYS = ARROWS + (Key.E, Key.SPACE, Key.ALT)  # Alt only alone: a tap (or jump / drop), never in a sequence
WALK_KEYS = (Key.LEFT, Key.RIGHT)  # walking is horizontal; the plan names no walk direction
SEQUENCE_KEYS = ARROWS + (Key.E, Key.SPACE)  # no Alt, so never Alt with Space
MIX = (("tap", 0.25), ("walk", 0.20), ("teleport", 0.25), ("drop", 0.10), ("jump", 0.05), ("sequence", 0.15))
WALK_T_MS = (300.0, 3000.0)  # T ~ U(300, 3000) ms
SEQUENCE_LEN = (2, 5)  # inclusive, uniform, keys drawn with repeats

KS_TYPES = (TimingType.HOLD, TimingType.GAP, TimingType.CHORD_OFFSET, TimingType.RELEASE_LAG, TimingType.REACTION,
            TimingType.WALK)
MIN_SAMPLES_PER_TYPE = 1000  # per KS type, for a run H4 judges (A-PF I6)


@dataclass(frozen=True)
class Command:
    action: str  # the InputLayer method: tap, walk, teleport, drop, jump, sequence
    keys: tuple[Key, ...] = ()  # tap key, walk / teleport direction, or the sequence
    intended_ms: float | None = None  # a walk's T


def _streams(seed: int) -> list[np.random.Generator]:
    """Independent generators for the mix, the timing model and the fault indexes, all from one seed."""
    return [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(3)]


def command_mix(rng: np.random.Generator) -> Iterator[Command]:
    """The plan's mix, one command at a time, without end (a `--min-hours` run can outlast N)."""
    names = [a for a, _ in MIX]
    weights = [w for _, w in MIX]
    while True:
        action = names[rng.choice(len(names), p=weights)]
        if action == "tap":
            yield Command(action, (TAP_KEYS[rng.integers(len(TAP_KEYS))],))
        elif action == "walk":
            yield Command(action, (WALK_KEYS[rng.integers(len(WALK_KEYS))],), float(rng.uniform(*WALK_T_MS)))
        elif action == "teleport":
            yield Command(action, (ARROWS[rng.integers(len(ARROWS))],))
        elif action == "sequence":
            n = int(rng.integers(SEQUENCE_LEN[0], SEQUENCE_LEN[1] + 1))
            yield Command(action, tuple(SEQUENCE_KEYS[i] for i in rng.integers(len(SEQUENCE_KEYS), size=n)))
        else:
            yield Command(action)


def plan_mix(seed: int, n: int) -> list[Command]:
    """The first `n` commands of the mix for `seed` (the ones a run issues first)."""
    mix = command_mix(_streams(seed)[0])
    return [next(mix) for _ in range(n)]


def sample_counts(commands: list[Command]) -> Counter:
    """Chosen samples per TimingType that `commands` draw (a clipped sequence hold counts as the HOLD it draws)."""
    counts: Counter = Counter()
    for c in commands:
        counts[TimingType.REACTION] += 1  # every command has a stimulus
        if c.action in ("tap", "jump"):
            counts[TimingType.HOLD] += 1
        elif c.action == "walk":
            counts[TimingType.WALK] += 1
        elif c.action in ("teleport", "drop"):
            counts.update((TimingType.CHORD_OFFSET, TimingType.HOLD, TimingType.RELEASE_LAG))
        elif c.action == "sequence":
            counts[TimingType.HOLD] += len(c.keys)
            counts[TimingType.GAP] += len(c.keys) - 1
    return counts


def dispatch(layer: InputLayer, cmd: Command, stimulus_ns: int) -> Result[ActionRecord, InputError]:
    if cmd.action == "tap":
        return layer.tap(cmd.keys[0], stimulus_ns)
    if cmd.action == "walk":
        return layer.walk(cmd.keys[0], cmd.intended_ms, stimulus_ns)
    if cmd.action == "teleport":
        return layer.teleport(cmd.keys[0], stimulus_ns)
    if cmd.action == "drop":
        return layer.drop(stimulus_ns)
    if cmd.action == "jump":
        return layer.jump(stimulus_ns)
    return layer.sequence(cmd.keys, stimulus_ns)


# ---- faults ----------------------------------------------------------------------------------------

FAULT_KINDS = ("reset_board", "bluetooth_off_on")
BT_OFF_SECONDS = 5.0  # V2 step 4's fault soak runs bluetooth_off_on(5) (plan Task V2)

# Seam: the tests replace these with fakes. Each takes a fresh FaultStamps and fills it (faults.py).
INJECTORS: dict[str, Callable[[faults.FaultStamps], Result[None, str]]] = {
    "reset_board": lambda stamps: faults.reset_board(stamps),
    "bluetooth_off_on": lambda stamps: faults.bluetooth_off_on(BT_OFF_SECONDS, stamps),
}


def fault_indexes(seed: int, n: int, counts: dict[str, int]) -> dict[int, str]:
    """Seeded, distinct command indexes in 1..n-1 (the fault is injected just before that command) -> kind."""
    pool = _streams(seed)[2].permutation(np.arange(1, n))
    out: dict[int, str] = {}
    pos = 0
    for kind in FAULT_KINDS:
        k = counts.get(kind, 0)
        for idx in pool[pos:pos + k]:
            out[int(idx)] = kind
        pos += k
    return out


def fault_row(kind: str, stamps: faults.FaultStamps, link_faults: list[tuple[int, InputError]],
              failure: tuple[int, InputError] | None) -> dict:
    """A C5 `faults.jsonl` row. `failure` is (returned ns, InputError) of the first command Failure after the
    injection, if any. A null `injected_ns` nulls `fault_seen_ns`, `failure_ns`, `reported_ns`, `error` (G2)."""
    row = {"injected_ns": stamps.injected_ns, "kind": kind, "reported_ns": None, "error": None,
           "launch_ns": stamps.launch_ns, "fault_seen_ns": None, "failure_ns": None}
    injected = stamps.injected_ns
    if injected is None:
        return row
    seen = next(((ns, err) for ns, err in link_faults if ns > injected), None)
    failed = failure if failure is not None and failure[0] > injected else None
    first = min((x for x in (seen, failed) if x is not None), key=lambda x: x[0], default=None)
    row.update(fault_seen_ns=seen[0] if seen else None, failure_ns=failed[0] if failed else None,
               reported_ns=first[0] if first else None, error=str(first[1]) if first else None)
    return row


# ---- MapleStory check (hard rule 2) ----------------------------------------------------------------

PROCESS_CHECK_PERIOD_NS = 60 * 1_000_000_000


def _process_names() -> Result[list[str], str]:
    """Image names of every running process (`tasklist /FO CSV /NH`)."""
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
                             timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return Failure(f"tasklist failed: {e}")
    if out.returncode != 0:
        return Failure(f"tasklist exited {out.returncode}: {out.stderr.strip()}")
    return Success([row[0] for row in csv.reader(out.stdout.splitlines()) if row])


def maple_check() -> tuple[bool, str]:
    """(ok, result text). Not ok if a process name contains 'maple' (any case) or the list cannot be read."""
    names = _process_names()
    if not is_successful(names):
        return False, f"process check failed: {names.failure()}"
    found = [n for n in names.unwrap() if "maple" in n.lower()]
    if found:
        return False, f"MapleStory running: {found}"
    return True, "no MapleStory process"


# ---- the driver --------------------------------------------------------------------------------------

EXIT_OK, EXIT_ABORT, EXIT_REFUSED, EXIT_INTERRUPTED = 0, 1, 2, 130
NS_PER_H = 3600 * 1_000_000_000


@dataclass(frozen=True)
class SoakConfig:
    commands: int
    out: Path
    seed: int
    short: bool = False
    min_hours: float = 0.0
    faults: dict[str, int] = field(default_factory=dict)


class _Abort(Exception):
    pass


class _SoakSink(Sink, Protocol):
    def stop(self) -> None: ...


def _check_processes(writer: RunWriter) -> None:
    ok, text = maple_check()
    writer.session["process_check"] = text
    if not ok:
        raise _Abort(text)


def _command(writer: RunWriter, sink: Sink, layer: InputLayer, seq: int, cmd: Command) -> CommandOutcome:
    return run_command(writer, sink, seq, cmd.action, lambda s: dispatch(layer, cmd, s), cmd.intended_ms)


def _fault_then_command(writer: RunWriter, sink: Sink, layer: InputLayer, link: BleLink, seq: int, cmd: Command,
                        kind: str) -> CommandOutcome:
    """Inject, run the next command, write the faults.jsonl row, then close() + connect() (PA-rev3a-D2)."""
    stamps = faults.FaultStamps()
    outcome = None
    try:
        injected = INJECTORS[kind](stamps)
        if not is_successful(injected):
            print(f"fault {kind} before seq {seq}: {injected.failure()}", file=sys.stderr, flush=True)
        outcome = _command(writer, sink, layer, seq, cmd)
    finally:  # every injection attempt writes its entry (C5 G2)
        failure = None
        if outcome is not None and outcome.result is not None and not is_successful(outcome.result):
            failure = (outcome.returned_ns, outcome.result.failure())
        writer.write_fault(fault_row(kind, stamps, link.faults(), failure))
    if outcome.focus_lost:
        return outcome
    link.close()
    reconnected = link.connect()
    if not is_successful(reconnected):
        raise _Abort(f"reconnect after {kind} failed: {reconnected.failure()}")
    return outcome


def run_soak(cfg: SoakConfig, link: BleLink, start_sink: Callable[[Path], _SoakSink]) -> int:
    """One soak run into `cfg.out`; returns the exit code. `start_sink(run_dir)` starts H1's listener."""
    mix_rng, timing_rng, _ = _streams(cfg.seed)
    layer = InputLayer(link, TimingModel(timing_rng))
    fault_at = fault_indexes(cfg.seed, cfg.commands, cfg.faults)
    target_ns = cfg.min_hours * NS_PER_H
    sink = None
    ended = False
    with RunWriter(cfg.out, link, seed=cfg.seed) as writer:  # opened before connect(): link.log gets its WARNINGs
        try:
            _check_processes(writer)  # before the listener blocks any key and before connect (hard rule 2)
            last_check_ns = _clock()

            def periodic_check() -> None:  # every 60 s from the start check on, the start wait included
                nonlocal last_check_ns
                if _clock() - last_check_ns >= PROCESS_CHECK_PERIOD_NS:
                    _check_processes(writer)
                    last_check_ns = _clock()

            try:
                sink = start_sink(writer.run_dir)
            except OSError as e:
                raise _Abort(f"listener did not start: {e}") from e
            if not wait_for_click(sink, periodic_check):  # before connect: a timeout leaves the board untouched
                raise _Abort(f"the sink did not take the foreground within {START_WAIT_S:g} s (no click)")
            connected = link.connect()
            if not is_successful(connected):
                raise _Abort(f"connect failed: {connected.failure()}")
            writer.record_connect(connected.unwrap())
            mix = command_mix(mix_rng)
            seq, first_stimulus_ns, last_done_ns = 0, None, None

            def span_short() -> bool:  # H3's connected span; none yet (no Success) is not "short": stop at N
                return (target_ns > 0 and first_stimulus_ns is not None and last_done_ns is not None
                        and last_done_ns - first_stimulus_ns < target_ns)

            while seq < cfg.commands or span_short():
                periodic_check()
                cmd = next(mix)
                if seq in fault_at:
                    outcome = _fault_then_command(writer, sink, layer, link, seq, cmd, fault_at[seq])
                else:
                    outcome = _command(writer, sink, layer, seq, cmd)
                if outcome.focus_lost:
                    raise _Abort(f"FOCUS_LOST at seq {seq}: the sink was not back after {FOCUS_RECHECKS} s")
                if first_stimulus_ns is None:
                    first_stimulus_ns = outcome.row["stimulus_ns"]
                if outcome.row["done_ns"] is not None:
                    last_done_ns = outcome.row["done_ns"]
                seq += 1
            ended = True
            print(f"soak done: {seq} commands -> {writer.run_dir}", flush=True)
            return EXIT_OK
        except _Abort as e:
            print(f"soak aborted: {e}", file=sys.stderr, flush=True)
            return EXIT_ABORT
        except KeyboardInterrupt:
            print("soak interrupted (Ctrl-C)", file=sys.stderr, flush=True)
            return EXIT_INTERRUPTED
        finally:
            if not ended:  # any abort, Ctrl-C or unexpected error: lift every key first
                released = layer.release_all()
                if not is_successful(released):
                    print(f"release_all: {released.failure()}", file=sys.stderr, flush=True)
            link.close()
            if sink is not None:
                sink.stop()


# ---- CLI ---------------------------------------------------------------------------------------------

# Seams: the tests replace these with fakes.
_new_link: Callable[[], BleLink] = BleLink


def _start_sink(run_dir: Path) -> _SoakSink:
    return listener.start(run_dir)


def _faults_arg(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in text.split(","):
        kind, sep, n = part.partition("=")
        if kind not in FAULT_KINDS or not sep or not n.isdigit():
            raise argparse.ArgumentTypeError(f"{part!r}: expected <kind>=<count>, kind one of {FAULT_KINDS}")
        out[kind] = int(n)
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m src.input.harness.soak",
                                description="Soak the input layer on the board; C5 logs in the run dir.")
    p.add_argument("--commands", type=int, required=True, help="commands to issue (at least)")
    p.add_argument("--min-hours", type=float, default=0.0,
                   help="also keep going until H3's connected span (first stimulus -> last done) reaches H hours")
    p.add_argument("--seed", type=int, default=None, help="mix, timing and fault seed (default: a fresh one, logged)")
    p.add_argument("--out", default=None, help="run directory (default data/soak/<YYYYmmdd-HHMMSS>)")
    p.add_argument("--short", action="store_true",
                   help=f"allow a run with fewer than {MIN_SAMPLES_PER_TYPE} samples per KS type (dry run, fault "
                        "soak; H4 does not judge it)")
    p.add_argument("--faults", type=_faults_arg, default={},
                   help="e.g. reset_board=5,bluetooth_off_on=5: injected before seeded random command indexes")
    return p.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> SoakConfig:
    seed = args.seed if args.seed is not None else int(np.random.SeedSequence().entropy)
    out = Path(args.out) if args.out else Path("data/soak") / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return SoakConfig(commands=args.commands, out=out, seed=seed, short=args.short, min_hours=args.min_hours,
                      faults=args.faults)


def validate(cfg: SoakConfig) -> str | None:
    """Why the run is refused, or None. Checked before any link, listener or run dir exists."""
    if cfg.commands < 1:
        return f"--commands must be at least 1, got {cfg.commands}"
    if not cfg.short:
        counts = sample_counts(plan_mix(cfg.seed, cfg.commands))
        low = {str(t): counts[t] for t in KS_TYPES if counts[t] < MIN_SAMPLES_PER_TYPE}
        if low:
            return (f"refused: --commands {cfg.commands} gives fewer than {MIN_SAMPLES_PER_TYPE} chosen samples for "
                    f"{low}; pass --short for a dry run or fault soak (H4 does not judge it)")
    n_faults = sum(cfg.faults.values())
    if n_faults > cfg.commands - 1:
        return f"refused: {n_faults} faults need at least {n_faults + 1} commands (never before the first)"
    return None


def main(argv: list[str] | None = None) -> int:
    cfg = config_from_args(parse_args(argv))
    refusal = validate(cfg)
    if refusal is not None:
        print(refusal, file=sys.stderr, flush=True)
        return EXIT_REFUSED
    print(f"soak: {cfg.commands} commands, min {cfg.min_hours:g} h, seed {cfg.seed}, faults {cfg.faults or 'none'}"
          f" -> {cfg.out}", flush=True)
    return run_soak(cfg, _new_link(), _start_sink)


if __name__ == "__main__":
    sys.exit(main())
