"""Tests for src.route.position (plan task B16): the position source reads the shared minimap frame under its lock,
reports a missing or stale frame, and runs the locator on a copy of each new frame outside the lock.

The shared object is a fake: `threading.Lock` cannot be subclassed on Python 3.14, so `TrackingLock` wraps one,
counts its acquires and says whether it is held. `SpyLocator` records whether that lock was held when `locate` ran.
Test 3 runs the real `CharacterLocator` on a synthetic frame built as in test_character.py; it says nothing about
accuracy on a real minimap. Tests 1-5 are the plan's five; 6 and 7 pin the copy and the unchanged result.
"""

import threading
from pathlib import Path

import cv2
import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.route.character import CharacterLocator
from src.route.position import MinimapPositionSource
from src.route.walker import STALE

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets" / "chr"
ICON_PATH = ASSETS / "chr.png"
MASK_PATH = ASSETS / "chr_mask.png"

FRAME_W, FRAME_H = 162, 82


def _load_icon():
    icon_bgr = cv2.imread(str(ICON_PATH), cv2.IMREAD_COLOR)
    mask = cv2.imread(str(MASK_PATH), cv2.IMREAD_GRAYSCALE)
    assert icon_bgr is not None and mask is not None
    return cv2.cvtColor(icon_bgr, cv2.COLOR_BGR2RGB), mask > 0


ICON_RGB, ICON_ON = _load_icon()
ICON_H, ICON_W = ICON_ON.shape
CENTRE_DX, CENTRE_DY = ICON_W // 2, ICON_H // 2


def _frame(seed: int, x: int | None = None, y: int | None = None) -> np.ndarray:
    """A noisy (30, 30, 30) RGB frame; with (x, y), the icon pasted wholly inside it with its top-left there."""
    noise = np.random.default_rng(seed).integers(-10, 11, size=(FRAME_H, FRAME_W, 1))
    frame = np.repeat(30 + noise, 3, axis=2).astype(np.uint8)
    if x is not None:
        frame[y:y + ICON_H, x:x + ICON_W][ICON_ON] = ICON_RGB[ICON_ON]
    return frame


class TrackingLock:
    """A wrapper around a `threading.Lock` that counts its acquires and says whether it is held."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.acquires = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        got = self._lock.acquire(blocking, timeout)
        if got:
            self.acquires += 1
        return got

    def release(self) -> None:
        self._lock.release()

    def held(self) -> bool:
        return self._lock.locked()

    def __enter__(self) -> "TrackingLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


class FakeShared:
    """Duck-types `SharedInfo` (src/main.py): `minimap_frame_lock` and `minimap_frame`."""

    def __init__(self) -> None:
        self.minimap_frame_lock = TrackingLock()
        self.minimap_frame = None


class SpyLocator:
    """Records each frame `locate` gets and whether the shared lock was held at that moment; answers with the real
    locator when given one, else with `result`."""

    def __init__(self, lock: TrackingLock, real: CharacterLocator | None = None, result=Success((1, 2))) -> None:
        self._lock = lock
        self._real = real
        self._result = result
        self.frames = []
        self.held = []

    def locate(self, minimap_rgb):
        self.frames.append(minimap_rgb)
        self.held.append(self._lock.held())
        return self._real.locate(minimap_rgb) if self._real is not None else self._result


@pytest.fixture(scope="module")
def locator() -> CharacterLocator:
    result = CharacterLocator.from_files(ICON_PATH, MASK_PATH)
    assert is_successful(result)
    return result.unwrap()


def _source(real: CharacterLocator | None = None, result=Success((1, 2))):
    shared = FakeShared()
    spy = SpyLocator(shared.minimap_frame_lock, real, result)
    return shared, spy, MinimapPositionSource(shared, spy)


def test_1_no_frame_is_a_failure_and_the_locator_is_not_called():
    shared, spy, source = _source()
    assert source.read() == Failure("no frame")
    assert source.read() == Failure("no frame")
    assert spy.frames == []


def test_2_the_same_array_twice_is_stale_the_second_time():
    shared, spy, source = _source()
    shared.minimap_frame = _frame(0)
    assert source.read() == Success((1, 2))
    assert STALE == "stale"
    assert source.read() == Failure(STALE)
    assert source.read() == Failure(STALE)
    assert len(spy.frames) == 1
    # identity, not content, makes a frame new: an equal array published as a new object is read
    shared.minimap_frame = shared.minimap_frame.copy()
    assert source.read() == Success((1, 2))
    assert len(spy.frames) == 2


def test_3_a_new_array_with_the_icon_pasted_gives_its_position(locator):
    shared, spy, source = _source(real=locator)
    rng = np.random.default_rng(3)
    for seed in range(10):
        x, y = int(rng.integers(0, FRAME_W - ICON_W + 1)), int(rng.integers(0, FRAME_H - ICON_H + 1))
        shared.minimap_frame = _frame(seed, x, y)
        assert source.read() == Success((x + CENTRE_DX, y + CENTRE_DY)), f"seed {seed} at {(x, y)}"
    assert len(spy.frames) == 10


def test_4_locate_never_runs_while_the_lock_is_held(locator):
    shared, spy, source = _source(real=locator)
    with shared.minimap_frame_lock:  # the spy does see a held lock
        spy.locate(_frame(99, 10, 10))
    assert spy.held == [True]
    spy.held.clear()
    first, second = _frame(1, 20, 30), _frame(2, 100, 5)
    for frame in (None, first, first, second, second, _frame(3), _frame(4, 0, 0)):
        shared.minimap_frame = frame
        source.read()
    assert spy.held == [False, False, False, False]


def test_5_one_lock_acquire_per_read():
    shared, spy, source = _source()
    lock = shared.minimap_frame_lock
    first = _frame(1)
    for n, frame in enumerate((None, first, first, _frame(2), None, _frame(3)), start=1):
        shared.minimap_frame = frame
        source.read()
        assert lock.acquires == n
        assert not lock.held()


def test_6_locate_gets_a_copy_and_its_result_is_returned_unchanged():
    for answer in (Success((7, 9)), Failure("miss"), Failure("ambiguous")):
        shared, spy, source = _source(result=answer)
        frame = _frame(6, 50, 40)
        shared.minimap_frame = frame
        assert source.read() is answer
        (got,) = spy.frames
        assert got is not frame
        assert not np.shares_memory(got, frame)
        assert np.array_equal(got, frame)


def test_7_the_copy_is_taken_under_the_lock():
    shared, spy, source = _source()
    lock = shared.minimap_frame_lock
    held_at_copy = []

    class CopyRecordingFrame:
        def copy(self):
            held_at_copy.append(lock.held())
            return _frame(7)

    shared.minimap_frame = CopyRecordingFrame()
    source.read()
    assert held_at_copy == [True]
    assert spy.held == [False]
