"""WALKFIX Task 6 (QB283 2d), the walker's side: the picture the walker reads and the pictures the recorder saves carry
the stamp the capture thread published with them (`minimap_frame_ns`, `time.perf_counter_ns()` when Windows handed the
picture over; the capture side is `tests/vision/test_frame_stamps.py`).

This file is `test_route_frame_stamps.py` because `tests/` has no `__init__.py`: pytest imports a test file by its
basename, so a second `test_frame_stamps.py` (the vision one) would fail collection with "import file mismatch".
Piece 2 of Task 6 has the position source and the recorder; piece 3 has the step log and the session's wiring.
"""

import json
import threading
import time

import numpy as np
import pytest
from returns.result import Failure, Success
from test_record_minimap import FakeTime, _lines, _rgb
from test_session import _no_real_blelink, env  # noqa: F401, F811  (the session tests' fixtures: `env` runs a RunSession)

from src.route import runtime
from src.route.harness.record_minimap import record, shared_frames
from src.route.position import MinimapPositionSource, foot_from_dot
from src.route.step_log import FIELDS, STEPS_FILE, RunHeader, StepLog, event_line, header_line, parse_line, read_run
from src.route.walker import STALE, RouteWalker, RunSummary


class _Locator:
    """A `CharacterLocator` stand-in whose answer the test changes between reads."""

    def __init__(self, result) -> None:
        self.result = result

    def locate(self, minimap_rgb):
        return self.result


def _publish(shared: runtime.SharedFrame, frame: np.ndarray, frame_ns: int | None) -> None:
    """What `LostTracker.publish` does: the picture and its stamp, under the lock, in one `with`."""
    with shared.minimap_frame_lock:
        shared.minimap_frame = frame
        shared.minimap_frame_ns = frame_ns


def test_position_source_keeps_the_stamp_of_its_last_good_read():
    shared = runtime.SharedFrame()
    locator = _Locator(Success((40, 50)))
    source = MinimapPositionSource(shared, locator)
    assert source.frame_ns is None  # no read yet

    assert source.read() == Failure("no frame")
    assert source.frame_ns is None  # a failed read sets nothing

    _publish(shared, _rgb(1), 10)
    assert source.read() == Success(foot_from_dot((40, 50)))
    assert source.frame_ns == 10  # the stamp of picture A, which this read located the character on

    assert source.read() == Failure(STALE)
    assert source.frame_ns == 10  # a stale read is no new picture

    _publish(shared, _rgb(2), 20)
    locator.result = Failure("miss")
    assert source.read() == Failure("miss")
    assert source.frame_ns == 10  # picture B (stamp 20) was read but the locator failed: still A's stamp

    _publish(shared, _rgb(3), 30)
    locator.result = Success((41, 50))
    assert source.read() == Success(foot_from_dot((41, 50)))
    assert source.frame_ns == 30  # the next good read moves it on, past B's 20


def test_position_source_reads_the_stamp_under_the_lock_with_the_frame():
    held_at_read: list[bool] = []

    class Holder:
        def __init__(self) -> None:
            self.minimap_frame_lock = threading.Lock()
            self.minimap_frame = _rgb(7)
            self._ns = 77

        @property
        def minimap_frame_ns(self) -> int:
            held_at_read.append(self.minimap_frame_lock.locked())
            return self._ns

    source = MinimapPositionSource(Holder(), _Locator(Success((1, 2))))
    assert source.read() == Success(foot_from_dot((1, 2)))
    assert held_at_read == [True]  # taken in the same `with` as the frame, so the two cannot be of different publications
    assert source.frame_ns == 77


def test_frame_and_stamp_never_mix():
    publications = 10_000
    shared = runtime.SharedFrame()
    done = threading.Event()

    def capture() -> None:
        # the stamp k goes with a picture whose first byte is k % 256; the `sleep(0)` between the two writes hands the
        # reader the GIL at the worst moment, so a reader that did not hold the lock would see a half-written pair
        for k in range(1, publications + 1):
            frame = np.full((2, 2, 3), k % 256, dtype=np.uint8)
            with shared.minimap_frame_lock:
                shared.minimap_frame = frame
                time.sleep(0)
                shared.minimap_frame_ns = k
        done.set()

    read = shared_frames(shared)
    thread = threading.Thread(target=capture, daemon=True)
    thread.start()
    seen: set[int] = set()
    while not done.is_set():
        pair = read()
        if pair is None:
            continue
        frame, stamp = pair
        # a mixed pair is a picture from one publication with the stamp of the one before it
        assert frame[0, 0, 0] == stamp % 256, f"picture of {frame[0, 0, 0]} came with stamp {stamp}"
        seen.add(stamp)
    thread.join(timeout=10)
    assert len(seen) > 1  # the reader did see the publications go by, not only the last one
    last = read()
    assert last is not None and last[1] == publications and last[0][0, 0, 0] == publications % 256


def test_index_lines_carry_frame_ns(tmp_path):
    fake = FakeTime()
    a, b, c = _rgb(1), _rgb(2), _rgb(3)
    pairs = iter([None, (a, 111), (b, None), (c, 333)])  # None is "no frame yet"; a stamp-less holder gives (b, None)
    out = tmp_path / "rec"
    result = record(lambda: next(pairs), out, seconds=0.3, fps=10, clock=fake.clock, sleep=fake.sleep)
    assert result == Success(3)
    lines = _lines(out)
    assert [line["frame_ns"] for line in lines] == [111, None, 333]  # the pair's stamp; null for a None stamp
    assert [line["file"] for line in lines] == ["frame_000000.png", "frame_000001.png", "frame_000002.png"]
    assert [list(line) for line in lines] == [["i", "t_ns", "frame_ns", "file", "w", "h"]] * 3  # frame_ns after t_ns
    # `t_ns` is still the poll's time, not the picture's
    assert [line["t_ns"] - 5_000_000_000 for line in lines] == [100_000_000, 200_000_000, 300_000_000]


# ---- the step log (piece 3) --------------------------------------------------------------------------------------

HEADER = RunHeader(source="sim", clock="sim_ns", map="map.csv", map_sha256=None, character="kanna",
                   params={"poll_s": 0.05}, rotation=None, git_head=None)
OLD_PLANNED = (b'{"ev": "planned", "t_ns": 44469522743700, "pos": [62, 58], "kind": "teleport", "dir": "RIGHT", '
               b'"walk_ms": null, "cost_to_go_s": 3.725, "toward": [81, 58]}\n')  # the spec's example line, before Task 6
OLD_SETTLED = b'{"ev": "settled", "t_ns": 21, "pos": [82, 58], "stopped": false}\n'


def _step_objects(folder) -> list[dict]:
    """The event lines of a run folder's `steps.jsonl` as JSON objects (key order kept), the header left out."""
    return [json.loads(raw) for raw in (folder / STEPS_FILE).read_bytes().splitlines()[1:]]


def test_step_log_writes_frame_ns_when_given_a_source(tmp_path):
    asked_on: list[threading.Thread] = []

    def source() -> int:
        asked_on.append(threading.current_thread())
        return 77

    log = StepLog(frame_ns=source)
    assert log.start(tmp_path, HEADER) == Success(None)
    log.leg(1, 0, 0, None, (113, 60))
    log.planned(2, (62, 58), "teleport", "RIGHT", None, 3.725, (81, 58))
    log.sent(3, "ok", 231.0, 228.4, ((0, 2),))
    log.settled(4, (82, 58), False)
    log.settled(5, None, True)  # the walker's settle gave no position: no picture to stamp
    log.reached(6, (82, 58))
    assert log.close() == Success(None)

    lines = _step_objects(tmp_path)
    assert [(line["ev"], line.get("frame_ns", "absent")) for line in lines] == [
        ("leg", "absent"), ("planned", 77), ("sent", "absent"), ("settled", 77), ("settled", None), ("reached", "absent")]
    assert list(lines[1]) == ["ev", "t_ns", *FIELDS["planned"], "frame_ns"]  # after today's fields, not among them
    assert list(lines[3]) == ["ev", "t_ns", *FIELDS["settled"], "frame_ns"]
    # asked at the call, on the walker's thread (where `positions.frame_ns` was just set), never on the writer thread
    assert asked_on and all(thread is threading.current_thread() for thread in asked_on)
    # and the reader gives it back
    run = read_run(tmp_path).unwrap()
    assert run.bad_lines == 0
    assert [event.data.get("frame_ns", "absent") for event in run.events] == ["absent", 77, "absent", 77, None, "absent"]


def test_step_log_without_a_source_is_unchanged(tmp_path):
    log = StepLog()
    assert log.start(tmp_path, HEADER) == Success(None)
    log.planned(44469522743700, (62, 58), "teleport", "RIGHT", None, 3.725, (81, 58))
    log.settled(21, (82, 58), False)
    assert log.close() == Success(None)
    lines = (tmp_path / STEPS_FILE).read_bytes().splitlines(keepends=True)
    assert lines[1:] == [OLD_PLANNED, OLD_SETTLED]  # byte for byte what Task 5's writer wrote
    # the encoder's old arity is still today's line
    assert event_line(("planned", 44469522743700, (62, 58), "teleport", "RIGHT", None, 3.725, (81, 58))).unwrap() == OLD_PLANNED
    assert event_line(("settled", 21, (82, 58), False)).unwrap() == OLD_SETTLED


@pytest.mark.parametrize("event", [
    ("planned", 1, (62, 58), "walk", "LEFT", 1.0, 2.0, None, "x"),  # frame_ns must be an int or null
    ("planned", 1, (62, 58), "walk", "LEFT", 1.0, 2.0, None, 1.5),
    ("planned", 1, (62, 58), "walk", "LEFT", 1.0, 2.0, None, True),  # a bool is not an int here either
    ("settled", 1, (82, 58), False, "x"),
    ("planned", 1, (62, 58), "walk", "LEFT", 1.0, 2.0, None, 5, 6),  # one value too many
    ("reached", 1, (82, 58), 77),  # no extra for an event that is not in EXTRA_FIELDS
    ("leg_end", 1, "reached", 0, 0, 0, 77),
])
def test_event_line_checks_the_extra_field(event):
    assert isinstance(event_line(event), Failure)


def test_event_line_takes_the_extra_after_the_fields():
    line = event_line(("settled", 21, (82, 58), False, 77)).unwrap()
    assert line == b'{"ev": "settled", "t_ns": 21, "pos": [82, 58], "stopped": false, "frame_ns": 77}\n'
    assert event_line(("settled", 21, None, True, None)).unwrap().endswith(b'"stopped": true, "frame_ns": null}\n')


def test_parse_line_reads_frame_ns_only_when_present(tmp_path):
    old = parse_line(OLD_PLANNED).unwrap()
    assert "frame_ns" not in old.data  # an old line: nothing invented
    assert old.data == dict(zip(FIELDS["planned"], ((62, 58), "teleport", "RIGHT", None, 3.725, (81, 58))))

    new_planned = OLD_PLANNED.replace(b"}\n", b', "frame_ns": 77}\n')
    assert parse_line(new_planned).unwrap().data["frame_ns"] == 77
    new_settled = OLD_SETTLED.replace(b"}\n", b', "frame_ns": null}\n')
    parsed = parse_line(new_settled).unwrap()
    assert "frame_ns" in parsed.data and parsed.data["frame_ns"] is None  # present and null is not absent
    for bad in (b'"x"', b"1.5", b"true", b"[1]"):
        assert isinstance(parse_line(OLD_PLANNED.replace(b"}\n", b', "frame_ns": ' + bad + b"}\n")), Failure)
    # an event that is not in EXTRA_FIELDS keeps ignoring keys it does not know
    reached = parse_line(b'{"ev": "reached", "t_ns": 1, "pos": [1, 2], "frame_ns": "x"}\n').unwrap()
    assert reached.data == {"pos": (1, 2)}

    # a folder written before Task 6 still loads, and one with a stamped line beside it too
    (tmp_path / STEPS_FILE).write_bytes(header_line(HEADER).unwrap() + OLD_PLANNED + new_planned)
    run = read_run(tmp_path).unwrap()
    assert run.bad_lines == 0 and len(run.events) == 2
    assert "frame_ns" not in run.events[0].data and run.events[1].data["frame_ns"] == 77


def test_session_wires_the_stamp_source(env, monkeypatch):  # noqa: F811
    env.positions = MinimapPositionSource(env.shared, _Locator(Success((40, 50))))
    env.shared.minimap_frame_ns = 4242  # the first frame's stamp: the session's own first read takes it
    seen: list[object] = []

    def run(self):
        # a newer picture arrives and the walker reads it: the step log must follow that read, not the one before it
        _publish(env.shared, _rgb(9), 5555)
        assert self._positions.read() == Success(foot_from_dot((40, 50)))
        seen.append(self._positions)
        self._observer.planned(1, (1, 2), "walk", "LEFT", 10.0, 1.0, None)
        self._observer.settled(2, (1, 3), False)
        self._observer.settled(3, None, True)
        return Success(RunSummary(0, 0, "max_laps"))

    monkeypatch.setattr(RouteWalker, "run", run)
    assert env.run() == 0
    assert seen == [env.positions]  # the walker read the very source the session built the step log from
    lines = [line for line in _step_objects(env.out) if line["ev"] in ("planned", "settled")]
    assert [line["frame_ns"] for line in lines] == [5555, 5555, None]


def test_session_with_another_position_source_writes_no_stamp(env, monkeypatch):  # noqa: F811
    # the simulator's source (and any fake) has no stamp: its run folders keep today's lines
    def run(self):
        self._observer.planned(1, (1, 2), "walk", "LEFT", 10.0, 1.0, None)
        self._observer.settled(2, (1, 3), False)
        return Success(RunSummary(0, 0, "max_laps"))

    monkeypatch.setattr(RouteWalker, "run", run)
    assert env.run() == 0
    lines = [line for line in _step_objects(env.out) if line["ev"] in ("planned", "settled")]
    assert len(lines) == 2 and all("frame_ns" not in line for line in lines)
