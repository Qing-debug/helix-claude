"""Tests for the step-log writer (route view plan task RV-2, contract V3): `StepLog` queues the walker's events and a
daemon thread appends them to `steps.jsonl`; start, close, calls after close, write failures, a slow disk, and no loss
at shutdown."""

import errno
import io
import math
import threading
import time
from pathlib import Path

import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.route.step_log import STEPS_FILE, RunHeader, StepLog, event_line, header_line, read_run

HEADER = RunHeader(
    source="sim",
    clock="sim_ns",
    map="map.csv",
    map_sha256=None,
    character="kanna",
    params={"stall_s": 10.0, "poll_s": 0.05},
    rotation=((113, 60), (82, 60), (49, 28)),
    git_head=None,
)

# One call per V2 method and `end`: (method name, its arguments, the event tuple `event_line` takes).
CALLS = [
    ("leg", (10, 1, 2, (113, 60), (82, 60)), ("leg", 10, 1, 2, (113, 60), (82, 60))),
    ("leg", (11, 0, 0, None, (113, 60)), ("leg", 11, 0, 0, None, (113, 60))),
    ("read_failed", (12, "stale"), ("read_failed", 12, "stale")),
    ("no_plan", (13, (0, 0), "off-graph: (0, 0)"), ("no_plan", 13, (0, 0), "off-graph: (0, 0)")),
    ("reached", (14, (82, 60)), ("reached", 14, (82, 60))),
    ("stall", (15, 3.725), ("stall", 15, 3.725)),
    ("stall", (16, math.inf), ("stall", 16, None)),  # plan choice 3
    ("planned", (17, (62, 58), "teleport", "RIGHT", None, 3.725, (81, 58)),
     ("planned", 17, (62, 58), "teleport", "RIGHT", None, 3.725, (81, 58))),
    ("planned", (18, (62, 58), "walk", "LEFT", 120.5, 1.0, None),
     ("planned", 18, (62, 58), "walk", "LEFT", 120.5, 1.0, None)),
    ("sent", (19, "ok", 231.0, 228.4, ((0, 2), (1200, 0))), ("sent", 19, "ok", 231.0, 228.4, ((0, 2), (1200, 0)))),
    ("sent", (20, "LINK_LOST", None, None, ()), ("sent", 20, "LINK_LOST", None, None, ())),
    ("settled", (21, (82, 58), False), ("settled", 21, (82, 58), False)),
    ("settled", (22, None, True), ("settled", 22, None, True)),
    ("leg_end", (23, "reached", 1, 0, 2), ("leg_end", 23, "reached", 1, 0, 2)),
    ("end", (24, "max_laps", None), ("end", 24, "max_laps", None)),
]
V2_METHODS = ("leg", "read_failed", "no_plan", "reached", "stall", "planned", "sent", "settled", "leg_end")


def _lines(path):
    return path.read_bytes().splitlines(keepends=True)


def _step_log_threads():
    return [t for t in threading.enumerate() if t.name == "step-log"]


def _call(log, name, args):
    getattr(log, name)(*args)


# ---- 9. start, order, end ---------------------------------------------------------------------------------------


def test_9_start_writes_the_header_at_once(tmp_path):
    log = StepLog()
    assert log.start(tmp_path, HEADER) == Success(None)
    assert (tmp_path / STEPS_FILE).read_bytes() == header_line(HEADER).unwrap()
    assert log.close() == Success(None)


def test_9_events_before_start_come_after_the_header_in_call_order_and_end_is_last(tmp_path):
    log = StepLog()
    _call(log, "leg", (1, 0, 0, None, (113, 60)))
    _call(log, "read_failed", (2, "miss"))
    assert is_successful(log.start(tmp_path, HEADER))
    _call(log, "reached", (3, (113, 60)))
    _call(log, "leg_end", (4, "reached", 1, 0, 0))
    log.end(5, "max_laps", None)
    assert log.close() == Success(None)
    lines = _lines(tmp_path / STEPS_FILE)
    assert lines[0] == header_line(HEADER).unwrap()
    assert lines[1:] == [event_line(e).unwrap() for e in [
        ("leg", 1, 0, 0, None, (113, 60)), ("read_failed", 2, "miss"), ("reached", 3, (113, 60)),
        ("leg_end", 4, "reached", 1, 0, 0), ("end", 5, "max_laps", None)]]
    run = read_run(tmp_path)
    assert is_successful(run)
    steps = run.unwrap()
    assert steps.header == HEADER and steps.ended is True and steps.bad_lines == 0
    assert [e.ev for e in steps.events] == ["leg", "read_failed", "reached", "leg_end", "end"]
    assert log.failure is None


# ---- 10. each method writes event_line's bytes ------------------------------------------------------------------


def test_10_every_method_writes_the_line_event_line_makes(tmp_path):
    log = StepLog()
    assert is_successful(log.start(tmp_path, HEADER))
    for name, args, _ in CALLS:
        _call(log, name, args)
    assert log.close() == Success(None)
    assert {name for name, _, _ in CALLS} == set(V2_METHODS) | {"end"}
    lines = _lines(tmp_path / STEPS_FILE)
    assert lines[1:] == [event_line(event).unwrap() for _, _, event in CALLS]
    assert b'"best_cost_s": null' in lines[7]


# ---- 11. exclusive open -------------------------------------------------------------------------------------------


def test_11_start_into_a_folder_that_has_steps_jsonl_fails_and_starts_nothing(tmp_path):
    path = tmp_path / STEPS_FILE
    path.write_bytes(b"an earlier run\n")
    before = len(_step_log_threads())
    log = StepLog()
    started = log.start(tmp_path, HEADER)
    assert not is_successful(started) and STEPS_FILE in started.failure()
    assert path.read_bytes() == b"an earlier run\n"
    assert len(_step_log_threads()) == before
    assert log.close() == Success(None)


def test_11_start_on_a_path_that_cannot_be_opened_fails(tmp_path):
    (tmp_path / STEPS_FILE).mkdir()
    log = StepLog()
    started = log.start(tmp_path, HEADER)
    assert not is_successful(started) and STEPS_FILE in started.failure()
    assert log.close() == Success(None)


# ---- 12. close is idempotent; calls after close are dropped ------------------------------------------------------


def _call_everything_1000_times(log):
    for _ in range(1000):
        for name, args, _ in CALLS:
            _call(log, name, args)


def test_12_close_before_start_then_start_fails_and_nothing_is_written(tmp_path):
    log = StepLog()
    _call(log, "reached", (1, (1, 2)))
    assert log.close() == Success(None)
    assert log._queue.qsize() == 0  # the queue was discarded
    _call_everything_1000_times(log)
    assert log._queue.qsize() == 0
    assert log.start(tmp_path, HEADER) == Failure("step log already closed")
    assert not (tmp_path / STEPS_FILE).exists()
    assert log.close() == Success(None)


def test_12_close_twice_and_calls_after_close_are_dropped(tmp_path):
    log = StepLog()
    assert is_successful(log.start(tmp_path, HEADER))
    _call(log, "reached", (1, (1, 2)))
    assert log.close() == Success(None)
    written = (tmp_path / STEPS_FILE).read_bytes()
    assert written.count(b"\n") == 2
    _call_everything_1000_times(log)
    assert log._queue.qsize() == 0
    assert log.close() == Success(None)
    assert (tmp_path / STEPS_FILE).read_bytes() == written
    assert log.start(tmp_path, HEADER) == Failure("step log already closed")


# ---- 13. a write failure ------------------------------------------------------------------------------------------


class FileProxy:
    """Delegates to the real file; `write` runs `on_write(data)` first (which may raise or sleep)."""

    def __init__(self, real, on_write):
        self._real = real
        self._on_write = on_write

    def write(self, data):
        self._on_write(data)
        return self._real.write(data)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_13_a_write_failure_is_kept_reported_once_and_returned_by_close(tmp_path, capsys):
    log = StepLog()
    assert is_successful(log.start(tmp_path, HEADER))

    def fail(data):
        raise OSError("disk full")

    log._file = FileProxy(log._file, fail)
    for name, args, _ in CALLS:
        _call(log, name, args)
    closed = log.close()
    assert not is_successful(closed) and "disk full" in closed.failure()
    assert log.failure is not None and "disk full" in log.failure
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and "step log write failed (not reported again this run)" in err[0] and "disk full" in err[0]
    assert _lines(tmp_path / STEPS_FILE) == [header_line(HEADER).unwrap()]


def test_13_an_encode_failure_is_kept_and_later_events_are_not_written(tmp_path, capsys):
    log = StepLog()
    assert is_successful(log.start(tmp_path, HEADER))
    _call(log, "reached", (1, (1, 2)))
    _call(log, "reached", (2, (1.5, 2)))  # not ints: event_line refuses it
    _call(log, "reached", (3, (1, 2)))
    closed = log.close()
    assert not is_successful(closed) and closed.failure() == log.failure
    assert len(capsys.readouterr().err.splitlines()) == 1
    assert _lines(tmp_path / STEPS_FILE)[1:] == [event_line(("reached", 1, (1, 2))).unwrap()]


class FullDisk(io.RawIOBase):
    """A raw file on a full disk: every write fails with ENOSPC; `close` closes the real file under it."""

    def __init__(self, real):
        self.real = real

    def writable(self):
        return True

    def write(self, b):
        raise OSError(errno.ENOSPC, "No space left on device")

    def fileno(self):
        return self.real.fileno()

    def close(self):
        self.real.close()
        super().close()


def test_13_a_full_disk_still_closes_the_file_when_the_last_flush_fails(tmp_path, capsys):
    # The line stays in the BufferedWriter's buffer (its raw write failed), so the writer's flush at the sentinel fails
    # again; the file must still be closed (RV-2 review I1). Test 13's proxy raises before the line reaches the buffer.
    log = StepLog()
    assert is_successful(log.start(tmp_path, HEADER))
    real = log._file.detach()  # the header is already flushed; the OS file now sits under a full disk
    log._file = io.BufferedWriter(FullDisk(real))
    _call(log, "reached", (1, (1, 2)))
    closed = log.close()
    assert not is_successful(closed) and "No space left" in closed.failure()
    assert real.closed and log._file.closed
    assert len(capsys.readouterr().err.splitlines()) == 1
    assert _lines(tmp_path / STEPS_FILE) == [header_line(HEADER).unwrap()]


def test_13_a_header_that_cannot_be_written_leaves_no_file_and_a_retry_works(tmp_path, monkeypatch):
    # RV-2 review m2: a failed start leaves no steps.jsonl, so a retry into the same folder is not refused as "exists".
    real_open = Path.open

    def disk_full(data):
        raise OSError("disk full")

    def open_on_a_full_disk(self, *args, **kwargs):
        return FileProxy(real_open(self, *args, **kwargs), disk_full)

    before = len(_step_log_threads())
    with monkeypatch.context() as m:
        m.setattr(Path, "open", open_on_a_full_disk)
        started = StepLog().start(tmp_path, HEADER)
    assert not is_successful(started) and "disk full" in started.failure()
    assert not (tmp_path / STEPS_FILE).exists()
    assert len(_step_log_threads()) == before
    retry = StepLog()
    assert retry.start(tmp_path, HEADER) == Success(None)
    assert retry.close() == Success(None)
    assert _lines(tmp_path / STEPS_FILE) == [header_line(HEADER).unwrap()]


# ---- 14. a slow disk ----------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_14_close_gives_up_on_a_slow_writer_and_does_not_raise(tmp_path):
    log = StepLog()
    assert is_successful(log.start(tmp_path, HEADER))
    slept = []

    def slow_once(data):
        if not slept:
            slept.append(True)
            time.sleep(3.0)

    log._file = FileProxy(log._file, slow_once)
    for name, args, _ in CALLS:
        _call(log, name, args)
    began = time.perf_counter()
    closed = log.close(join_s=0.2)
    took = time.perf_counter() - began
    assert took < 0.5
    assert closed == Failure("writer still busy after 0.2 s")
    # the writer goes on in the background and finishes the file itself
    for thread in _step_log_threads():
        thread.join(timeout=10.0)
    assert not _step_log_threads()
    assert _lines(tmp_path / STEPS_FILE)[1:] == [event_line(event).unwrap() for _, _, event in CALLS]


# ---- 15. no loss at shutdown -------------------------------------------------------------------------------------


def test_15_ten_thousand_events_then_close_loses_none(tmp_path):
    log = StepLog()
    assert is_successful(log.start(tmp_path, HEADER))
    for i in range(10_000):
        log.settled(i, (i % 200, i % 100), False)
    assert log.close() == Success(None)
    lines = _lines(tmp_path / STEPS_FILE)
    assert len(lines) == 10_001
    assert lines[1:] == [event_line(("settled", i, (i % 200, i % 100), False)).unwrap() for i in range(10_000)]
