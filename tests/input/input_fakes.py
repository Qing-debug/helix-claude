"""Shared fakes and helpers for the input tests. Test modules import them with `from input_fakes import ...` (pytest puts
this directory on sys.path; there is no conftest).

`FakeClock`, `pong()`, `report()`, `FakeLink`, `patch_clock()`, `write_jsonl()`, `write_run()` and `focus_lost_row()`
are shared. Copies that differed only in a parameter (`pong`'s itvl, board_us and flags) became one function with that
parameter. A fake that behaves differently stays in its own file: test_input_layer.py `Clock` (fixed time, logs sleeps),
the test_session_starts.py / test_soak_v2_faults.py link fakes (connect/close, keys.jsonl writes, link drops),
test_soak.py `DriverLink` and `FakeSink`, test_probe.py `ProbeLink`.
"""
import json
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest
from returns.result import Result, Success

from src.input.input_layer import Link
from src.input.link import InputError, SendReport
from src.input.protocol import Done, Pong, Step

S = 1_000_000_000

# The commands.jsonl row's fields in order (soak.COMMAND_FIELDS; test_soak.py pins that list).
COMMAND_KEYS = ("seq", "action", "intended_ms", "stimulus_ns", "steps", "samples", "reaction_ms", "reaction_index",
                "waited_ms", "sent_ns", "written_ns", "accepted_ns", "done_ns", "t0_us", "late_us", "status", "error",
                "bytes")


class FakeClock:
    """A `_clock` / `_sleep` pair on one fake time: every `clock()` read moves time on 1 µs (strictly increasing
    stamps), `sleep(s)` moves it on `s` seconds and records `s` in `sleeps`."""

    def __init__(self, start_ns=S):
        self.now = start_ns
        self.sleeps: list[float] = []

    def clock(self):
        self.now += 1_000  # strictly increasing stamps
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += int(s * 1e9)


def patch_clock(monkeypatch: pytest.MonkeyPatch, clock: FakeClock, *modules: ModuleType) -> None:
    """Point `_clock` and `_sleep` of each module at `clock`. The modules are named at each call site, because a
    module that reads the clock has to be patched by name (tests/CLAUDE.md, "Patch the module where ...")."""
    for mod in modules:
        monkeypatch.setattr(mod, "_clock", clock.clock)
        monkeypatch.setattr(mod, "_sleep", clock.sleep)


def pong(itvl=12, board_us=1, *, bonded=True, encrypted=True, authenticated=False, subscribed=True, mtu=247):
    """A PONG that passes `BleLink.connect()`'s checks: bonded, encrypted, subscribed, mtu 247. The defaults are the
    session and fault fakes'; the probe and soak tests pass their own `board_us` and flags."""
    return Pong(seq=1, itvl_units=itvl, latency=0, timeout_units=100, mtu=mtu, bonded=bonded, encrypted=encrypted,
                authenticated=authenticated, subscribed=subscribed, board_us=board_us)


def report(seq=7):
    """A successful SendReport (DONE status 0) with fixed stamps 1-4."""
    return SendReport(seq=seq, sent_ns=1, written_ns=2, accepted_ns=3, done_ns=4, accepted=None,
                      done=Done(seq=seq, status=0, fail_step=None, rc=0, t0_us=0, late_us=()))


class FakeLink:
    """A `Link` that records calls in order into `events` (pass a list to share it, e.g. with a clock's sleeps) and
    returns `play_result` / `release_result` (set them to fail)."""

    def __init__(self, events=None):
        self.events = [] if events is None else events
        self.play_result: Result[SendReport, InputError] = Success(report(7))
        self.release_result: Result[SendReport, InputError] = Success(report(8))

    @property
    def calls(self):
        return [e for e in self.events if e[0] in ("play", "release_all")]

    def play(self, steps: Sequence[Step]) -> Result[SendReport, InputError]:
        self.events.append(("play", tuple(steps)))
        return self.play_result

    def release_all(self) -> Result[SendReport, InputError]:
        self.events.append(("release_all",))
        return self.release_result


def write_jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def write_run(tmp_path: Path, commands, keys, *, faults=None, link_faults=(), pongs=None, name: str = "run") -> Path:
    """A C5 run dir: commands.jsonl, and keys.jsonl sorted by qpc_ns. faults=None leaves faults.jsonl absent;
    link_faults=None leaves link_faults.jsonl absent (analyze then reports no drop data); pongs=None leaves pongs.jsonl
    absent. `pongs` is a list of ready rows (dicts with `qpc_ns` and `board_us`)."""
    run = tmp_path / name
    run.mkdir()
    write_jsonl(run / "commands.jsonl", commands)
    write_jsonl(run / "keys.jsonl", sorted(keys, key=lambda e: e["qpc_ns"]))
    if faults is not None:
        write_jsonl(run / "faults.jsonl", faults)
    if link_faults is not None:
        write_jsonl(run / "link_faults.jsonl", link_faults)
    if pongs is not None:
        write_jsonl(run / "pongs.jsonl", pongs)
    return run


def focus_lost_row(seq: int) -> dict:
    """A commands.jsonl row for a command the sink lost focus during: every field None but seq, action and status."""
    row: dict[str, object] = {k: None for k in COMMAND_KEYS}
    row.update(seq=seq, action="tap", status="FOCUS_LOST", error="FOCUS_LOST")
    return row


def _satisfies_link() -> Link:
    """Never called: lets a type checker run on this file confirm FakeLink satisfies `Link`."""
    return FakeLink()
