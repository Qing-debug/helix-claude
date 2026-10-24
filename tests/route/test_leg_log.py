"""Tests for the leg and lap log (plan task B11, contract C9)."""

import dataclasses
import json
import os
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.input.protocol import Key
from src.route.leg_log import (
    JsonlLegLog,
    LapRecord,
    LegOutcome,
    LegRecord,
    LegSink,
    MemorySink,
    MoveEntry,
    lap_to_json,
    leg_to_json,
)

LEG_KEYS = ["type", "lap", "leg", "from", "to", "start_ns", "end_ns", "outcome", "moves", "misses", "off_graph",
            "stalls"]
MOVE_KEYS = ["t_ns", "pos", "kind", "dir", "walk_ms", "cost_to_go_s", "input", "settled"]
LAP_KEYS = ["type", "lap", "start_ns", "end_ns", "duration_s", "legs", "all_reached"]


def _walk_entry() -> MoveEntry:
    return MoveEntry(t_ns=1_100, pos=(82, 60), kind="walk", direction=Key.RIGHT.name, walk_ms=1500.5,
                     cost_to_go_s=2.25, input="ok", settled=(94, 60))


def _drop_entry() -> MoveEntry:
    return MoveEntry(t_ns=2_200, pos=(101, 42), kind="drop", direction=None, walk_ms=None,
                     cost_to_go_s=0.4, input="ACK_TIMEOUT", settled=None)


def _leg(leg: int = 1, lap: int = 1, src: tuple[int, int] | None = (113, 60)) -> LegRecord:
    return LegRecord(lap=lap, leg=leg, src=src, dst=(82, 60), start_ns=1_000, end_ns=3_000_000_000,
                     outcome=LegOutcome.REACHED, moves=(_walk_entry(), _drop_entry()), misses=3, off_graph=1,
                     stalls=2)


def _lap(lap: int = 1) -> LapRecord:
    return LapRecord(lap=lap, start_ns=1_000, end_ns=7_500_001_000, legs=7, all_reached=True)


def _lines(path: Path) -> list[str]:
    return path.read_bytes().decode("utf-8").splitlines()


# 1. The JSON shape


def test_leg_outcome_values():
    assert [o.value for o in LegOutcome] == ["reached", "stopped", "timed_out", "link_down"]


def test_leg_to_json_has_exactly_c9_keys():
    d = leg_to_json(_leg())
    assert list(d) == LEG_KEYS
    assert d["type"] == "leg"
    assert d["lap"] == 1 and d["leg"] == 1
    assert d["from"] == [113, 60] and d["to"] == [82, 60]
    assert d["start_ns"] == 1_000 and d["end_ns"] == 3_000_000_000
    assert d["misses"] == 3 and d["off_graph"] == 1 and d["stalls"] == 2
    assert type(d["outcome"]) is str and d["outcome"] == "reached"
    assert isinstance(d["moves"], list) and len(d["moves"]) == 2
    walk, drop = d["moves"]
    assert list(walk) == MOVE_KEYS and list(drop) == MOVE_KEYS
    assert walk == {"t_ns": 1_100, "pos": [82, 60], "kind": "walk", "dir": "RIGHT", "walk_ms": 1500.5,
                    "cost_to_go_s": 2.25, "input": "ok", "settled": [94, 60]}
    assert drop == {"t_ns": 2_200, "pos": [101, 42], "kind": "drop", "dir": None, "walk_ms": None,
                    "cost_to_go_s": 0.4, "input": "ACK_TIMEOUT", "settled": None}


def test_leg_to_json_turns_tuples_into_lists():
    d = leg_to_json(_leg())
    assert type(d["from"]) is list and type(d["to"]) is list
    assert all(type(m["pos"]) is list for m in d["moves"])
    assert type(d["moves"][0]["settled"]) is list
    assert type(d["moves"]) is list


def test_leg_to_json_null_source_and_outcome_string_values():
    for outcome in LegOutcome:
        rec = dataclasses.replace(_leg(leg=0, lap=0, src=None), outcome=outcome, moves=())
        d = leg_to_json(rec)
        assert d["from"] is None
        assert d["moves"] == []
        assert type(d["outcome"]) is str and d["outcome"] == outcome.value


def test_leg_to_json_survives_json_dumps_and_round_trips():
    for rec in (_leg(), _leg(leg=0, lap=0, src=None)):
        d = leg_to_json(rec)
        assert json.loads(json.dumps(d)) == d


def test_lap_to_json_has_exactly_c9_keys_and_duration():
    d = lap_to_json(_lap())
    assert list(d) == LAP_KEYS
    assert d["type"] == "lap" and d["lap"] == 1 and d["legs"] == 7 and d["all_reached"] is True
    assert d["start_ns"] == 1_000 and d["end_ns"] == 7_500_001_000
    assert d["duration_s"] == (7_500_001_000 - 1_000) / 1e9 == 7.5
    assert json.loads(json.dumps(d)) == d


def test_records_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        _leg().misses = 9  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        _lap().legs = 9  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        _walk_entry().kind = "x"  # type: ignore[misc]


# 2. Opening, and flushing per record


def test_open_creates_the_directory_and_flushes_each_record(tmp_path):
    directory = tmp_path / "runs" / "walk-1"  # a nested path that does not exist yet
    result = JsonlLegLog.open(directory)
    assert is_successful(result), result
    log = result.unwrap()
    try:
        assert directory.is_dir()
        assert log.write_leg(_leg()) == Success(None)
        # Not closed: a second, independent open of the file already sees the whole line.
        raw = (directory / "legs.jsonl").read_bytes()
        assert raw.endswith(b"\n") and b"\r" not in raw
        assert len(raw.splitlines()) == 1
        assert json.loads(raw.decode("utf-8")) == leg_to_json(_leg())
        with open(directory / "legs.jsonl") as independent:
            assert independent.read().endswith("\n")

        assert log.write_lap(_lap()) == Success(None)
        raw = (directory / "laps.jsonl").read_bytes()
        assert raw.endswith(b"\n") and b"\r" not in raw
        assert json.loads(raw.decode("utf-8")) == lap_to_json(_lap())
        assert (directory / "legs.jsonl").read_bytes().count(b"\n") == 1  # laps go to their own file
    finally:
        log.close()


def test_one_object_per_line_in_order(tmp_path):
    log = JsonlLegLog.open(tmp_path).unwrap()
    try:
        for i in range(3):
            assert is_successful(log.write_leg(_leg(leg=i)))
        for i in range(2):
            assert is_successful(log.write_lap(_lap(lap=i)))
    finally:
        log.close()
    assert [json.loads(s)["leg"] for s in _lines(tmp_path / "legs.jsonl")] == [0, 1, 2]
    assert [json.loads(s)["lap"] for s in _lines(tmp_path / "laps.jsonl")] == [0, 1]


def test_record_with_a_non_ascii_string_is_still_one_utf8_line(tmp_path):
    entry = dataclasses.replace(_walk_entry(), kind="walké中")
    rec = dataclasses.replace(_leg(), moves=(entry,))
    log = JsonlLegLog.open(tmp_path).unwrap()
    try:
        assert is_successful(log.write_leg(rec))
    finally:
        log.close()
    (line,) = _lines(tmp_path / "legs.jsonl")
    assert json.loads(line)["moves"][0]["kind"] == "walké中"


# 3. Appending


def test_opening_an_existing_directory_appends(tmp_path):
    first = JsonlLegLog.open(tmp_path).unwrap()
    first.write_leg(_leg(leg=0))
    first.write_leg(_leg(leg=1))
    first.write_lap(_lap(lap=1))
    first.close()
    legs_before = _lines(tmp_path / "legs.jsonl")
    laps_before = _lines(tmp_path / "laps.jsonl")
    assert len(legs_before) == 2 and len(laps_before) == 1

    second = JsonlLegLog.open(tmp_path).unwrap()
    second.write_leg(_leg(leg=2))
    second.write_leg(_leg(leg=3))
    second.write_lap(_lap(lap=2))
    second.close()
    legs_after = _lines(tmp_path / "legs.jsonl")
    laps_after = _lines(tmp_path / "laps.jsonl")
    assert len(legs_after) == 4 and legs_after[:2] == legs_before
    assert [json.loads(s)["leg"] for s in legs_after] == [0, 1, 2, 3]
    assert len(laps_after) == 2 and laps_after[:1] == laps_before


def test_opening_a_file_with_a_torn_last_line_starts_a_new_line(tmp_path):
    # B11-D1: a crash can leave a line without its newline; the next session's first record must not be glued to it.
    first = JsonlLegLog.open(tmp_path).unwrap()
    first.write_leg(_leg(leg=0))
    first.write_lap(_lap(lap=1))
    first.close()
    for name in ("legs.jsonl", "laps.jsonl"):
        with (tmp_path / name).open("ab") as f:
            f.write(b'{"type": "leg", "lap": 1, "le')  # torn mid-record, no newline

    second = JsonlLegLog.open(tmp_path).unwrap()
    assert is_successful(second.write_leg(_leg(leg=1)))
    assert is_successful(second.write_lap(_lap(lap=2)))
    second.close()

    legs = _lines(tmp_path / "legs.jsonl")
    assert len(legs) == 3 and legs[1].startswith('{"type": "leg", "lap": 1, "le')
    assert [json.loads(legs[0])["leg"], json.loads(legs[2])["leg"]] == [0, 1]  # the torn line is its own line
    laps = _lines(tmp_path / "laps.jsonl")
    assert len(laps) == 3 and json.loads(laps[2])["lap"] == 2


def test_opening_files_that_are_empty_missing_or_end_in_a_newline_adds_nothing(tmp_path):
    (tmp_path / "laps.jsonl").write_bytes(b"")  # empty; legs.jsonl is missing
    log = JsonlLegLog.open(tmp_path).unwrap()
    log.close()
    assert (tmp_path / "legs.jsonl").read_bytes() == b""
    assert (tmp_path / "laps.jsonl").read_bytes() == b""

    log = JsonlLegLog.open(tmp_path).unwrap()
    log.write_leg(_leg(leg=0))
    log.close()
    before = (tmp_path / "legs.jsonl").read_bytes()
    assert before.endswith(b"\n")
    JsonlLegLog.open(tmp_path).unwrap().close()  # the last byte is a newline: no extra byte
    assert (tmp_path / "legs.jsonl").read_bytes() == before


# 4. Failures


def test_write_after_close_is_a_failure(tmp_path):
    log = JsonlLegLog.open(tmp_path).unwrap()
    assert is_successful(log.write_leg(_leg(leg=0)))
    log.close()
    assert not is_successful(log.write_leg(_leg(leg=1)))
    assert not is_successful(log.write_lap(_lap()))
    assert isinstance(log.write_leg(_leg()), Failure)
    assert isinstance(log.write_leg(_leg()).failure(), str)
    log.close()  # closing twice is harmless
    assert len(_lines(tmp_path / "legs.jsonl")) == 1
    assert not (tmp_path / "laps.jsonl").read_bytes()


def test_open_on_an_existing_file_is_a_failure(tmp_path):
    a_file = tmp_path / "not_a_directory"
    a_file.write_text("x")
    result = JsonlLegLog.open(a_file)
    assert isinstance(result, Failure) and isinstance(result.failure(), str)
    assert a_file.read_text() == "x"
    below = JsonlLegLog.open(a_file / "sub")  # a directory below a file cannot be made either
    assert isinstance(below, Failure) and isinstance(below.failure(), str)


def test_open_fails_cleanly_when_the_second_file_cannot_be_opened(tmp_path):
    (tmp_path / "laps.jsonl").mkdir()  # a directory where the lap file should be
    result = JsonlLegLog.open(tmp_path)
    assert isinstance(result, Failure)
    (tmp_path / "legs.jsonl").unlink()  # fails on Windows if the half-opened legs file leaked


def test_a_record_that_cannot_be_serialised_is_a_failure_and_writes_nothing(tmp_path):
    log = JsonlLegLog.open(tmp_path).unwrap()
    try:
        bad = dataclasses.replace(_leg(), dst=(np.int64(82), 60))  # numpy ints are not JSON-serialisable
        result = log.write_leg(bad)
        assert isinstance(result, Failure) and isinstance(result.failure(), str)
        assert (tmp_path / "legs.jsonl").read_bytes() == b""
        assert is_successful(log.write_leg(_leg()))  # the log still works afterwards
        assert len(_lines(tmp_path / "legs.jsonl")) == 1
    finally:
        log.close()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_infinity_are_a_failure_and_write_nothing(tmp_path, bad):
    # B11-D2: strict JSON; the bare words NaN / Infinity are not JSON.
    log = JsonlLegLog.open(tmp_path).unwrap()
    try:
        entry = dataclasses.replace(_walk_entry(), cost_to_go_s=bad)
        result = log.write_leg(dataclasses.replace(_leg(), moves=(entry,)))
        assert isinstance(result, Failure) and isinstance(result.failure(), str)
        entry = dataclasses.replace(_walk_entry(), walk_ms=bad)
        assert isinstance(log.write_leg(dataclasses.replace(_leg(), moves=(entry,))), Failure)
        assert isinstance(log.write_lap(dataclasses.replace(_lap(), end_ns=bad)), Failure)
        assert (tmp_path / "legs.jsonl").read_bytes() == b""
        assert (tmp_path / "laps.jsonl").read_bytes() == b""
        assert is_successful(log.write_leg(_leg()))  # the log still works afterwards
        assert len(_lines(tmp_path / "legs.jsonl")) == 1
    finally:
        log.close()


@pytest.mark.parametrize("field,value", [("outcome", "reached"), ("dst", None)])
def test_a_wrong_typed_leg_record_is_a_failure_and_writes_nothing(tmp_path, field, value):
    # B11-fix-2 F1: the conversion to JSON is inside the guarded path, so a wrong type is a Failure, not a raise.
    log = JsonlLegLog.open(tmp_path).unwrap()
    try:
        result = log.write_leg(dataclasses.replace(_leg(), **{field: value}))
        assert isinstance(result, Failure) and isinstance(result.failure(), str)
        assert (tmp_path / "legs.jsonl").read_bytes() == b""
        assert is_successful(log.write_leg(_leg()))  # the log still works afterwards
        assert len(_lines(tmp_path / "legs.jsonl")) == 1
    finally:
        log.close()


def test_a_wrong_typed_lap_record_is_a_failure_and_writes_nothing(tmp_path):
    log = JsonlLegLog.open(tmp_path).unwrap()
    try:
        result = log.write_lap(dataclasses.replace(_lap(), end_ns=None))
        assert isinstance(result, Failure) and isinstance(result.failure(), str)
        assert (tmp_path / "laps.jsonl").read_bytes() == b""
        assert is_successful(log.write_lap(_lap()))
        assert len(_lines(tmp_path / "laps.jsonl")) == 1
    finally:
        log.close()


# 4b. _open_for_append failure branch and the torn-only file (B11-D1)


def _fail_rb_open(monkeypatch) -> None:
    real_open = Path.open

    def opener(self, mode="r", *args, **kwargs):
        if mode == "rb":
            raise PermissionError("simulated read failure")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opener)


def test_a_failed_read_of_the_last_byte_is_a_failure_and_leaves_the_file_and_handle_clean(tmp_path, monkeypatch):
    legs = tmp_path / "legs.jsonl"
    legs.write_bytes(b'{"a": 1}\n{"a": 2')
    _fail_rb_open(monkeypatch)
    result = JsonlLegLog.open(tmp_path)
    assert isinstance(result, Failure) and "simulated read failure" in result.failure()
    monkeypatch.undo()
    assert legs.read_bytes() == b'{"a": 1}\n{"a": 2'
    legs.unlink()  # fails on Windows if the append handle leaked


def test_a_failed_write_of_the_repair_newline_is_a_failure_and_leaves_the_file_and_handle_clean(tmp_path, monkeypatch):
    laps = tmp_path / "laps.jsonl"
    laps.write_bytes(b'{"a": 2')
    real_open = Path.open
    opened: list = []

    class FailingWrite:
        def __init__(self, inner):
            self._inner = inner

        def write(self, data):
            raise OSError(28, "simulated disk full")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def opener(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if self.name == "laps.jsonl" and mode == "ab":
            wrapped = FailingWrite(handle)
            opened.append(handle)
            return wrapped
        return handle

    monkeypatch.setattr(Path, "open", opener)
    result = JsonlLegLog.open(tmp_path)
    assert isinstance(result, Failure) and "simulated disk full" in result.failure()
    monkeypatch.undo()
    assert opened and opened[0].closed
    assert laps.read_bytes() == b'{"a": 2'
    laps.unlink()
    (tmp_path / "legs.jsonl").unlink()  # the first file was closed too


def test_a_file_that_is_only_a_torn_line_gets_a_newline_then_the_new_record(tmp_path):
    (tmp_path / "legs.jsonl").write_bytes(b'{"a"')
    log = JsonlLegLog.open(tmp_path).unwrap()
    assert (tmp_path / "legs.jsonl").read_bytes() == b'{"a"\n'
    assert is_successful(log.write_leg(_leg(leg=5)))
    log.close()
    lines = _lines(tmp_path / "legs.jsonl")
    assert len(lines) == 2 and lines[0] == '{"a"' and json.loads(lines[1])["leg"] == 5


# 5. fsync


def _count_fsync(monkeypatch) -> list[int]:
    calls: list[int] = []
    real = os.fsync

    def counting(fd: int) -> None:
        calls.append(fd)
        real(fd)

    monkeypatch.setattr(os, "fsync", counting)
    return calls


def test_fsync_true_syncs_once_per_record(tmp_path, monkeypatch):
    calls = _count_fsync(monkeypatch)
    log = JsonlLegLog.open(tmp_path, fsync=True).unwrap()
    try:
        assert calls == []
        log.write_leg(_leg(leg=0))
        assert len(calls) == 1
        log.write_leg(_leg(leg=1))
        assert len(calls) == 2
        log.write_lap(_lap())
        assert len(calls) == 3
    finally:
        log.close()
    assert len(calls) == 3  # closing does not sync again


def test_fsync_defaults_to_true(tmp_path, monkeypatch):
    calls = _count_fsync(monkeypatch)
    log = JsonlLegLog.open(tmp_path).unwrap()
    try:
        log.write_leg(_leg())
    finally:
        log.close()
    assert len(calls) == 1


def test_fsync_false_never_syncs(tmp_path, monkeypatch):
    calls = _count_fsync(monkeypatch)
    log = JsonlLegLog.open(tmp_path, fsync=False).unwrap()
    try:
        log.write_leg(_leg(leg=0))
        log.write_leg(_leg(leg=1))
        log.write_lap(_lap())
    finally:
        log.close()
    assert calls == []
    assert len(_lines(tmp_path / "legs.jsonl")) == 2  # still flushed per record


def test_record_is_flushed_before_fsync_is_called(tmp_path, monkeypatch):
    seen_at_fsync: list[bytes] = []
    real = os.fsync

    def probing(fd: int) -> None:
        seen_at_fsync.append((tmp_path / "legs.jsonl").read_bytes())
        real(fd)

    monkeypatch.setattr(os, "fsync", probing)
    log = JsonlLegLog.open(tmp_path).unwrap()
    try:
        log.write_leg(_leg())
    finally:
        log.close()
    expected = json.dumps(leg_to_json(_leg())).encode("utf-8") + b"\n"
    assert seen_at_fsync == [expected]


def test_fsync_failure_is_a_failure_not_an_exception(tmp_path, monkeypatch):
    def broken(fd: int) -> None:
        raise OSError("disk gone")

    log = JsonlLegLog.open(tmp_path).unwrap()
    try:
        monkeypatch.setattr(os, "fsync", broken)
        result = log.write_leg(_leg())
        assert isinstance(result, Failure) and "disk gone" in result.failure()
    finally:
        log.close()


# 6. MemorySink


def test_memory_sink_keeps_records_in_order_and_never_fails():
    sink = MemorySink()
    assert sink.legs == [] and sink.laps == []
    legs = [_leg(leg=i) for i in range(3)]
    laps = [_lap(lap=i) for i in range(2)]
    for rec in legs:
        assert sink.write_leg(rec) == Success(None)
    for rec in laps:
        assert sink.write_lap(rec) == Success(None)
    assert sink.legs == legs and sink.laps == laps
    assert MemorySink().legs == []  # each sink has its own lists


def test_both_sinks_satisfy_leg_sink(tmp_path):
    log = JsonlLegLog.open(tmp_path).unwrap()
    try:
        for sink in (MemorySink(), log):
            typed: LegSink = sink
            assert typed.write_leg(_leg()) == Success(None)
            assert typed.write_lap(_lap()) == Success(None)
    finally:
        log.close()
