"""WALKFIX Task 6 (QB283 2d), the walker's side: the picture the walker reads and the pictures the recorder saves carry
the stamp the capture thread published with them (`minimap_frame_ns`, `time.perf_counter_ns()` when Windows handed the
picture over; the capture side is `tests/vision/test_frame_stamps.py`).

This file is `test_route_frame_stamps.py` because `tests/` has no `__init__.py`: pytest imports a test file by its
basename, so a second `test_frame_stamps.py` (the vision one) would fail collection with "import file mismatch".
Piece 2 of Task 6 has the position source and the recorder; the step log's tests are added by piece 3.
"""

import threading
import time

import numpy as np
from returns.result import Failure, Success

from src.route import runtime
from src.route.harness.record_minimap import record, shared_frames
from src.route.position import MinimapPositionSource, foot_from_dot
from src.route.walker import STALE
from test_record_minimap import FakeTime, _lines, _rgb


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
