"""Tests for the 3 px shift (off-graph fix plan, Task 4, OFFP141, Hong's QB213): every position source reports the point
`DOT_TO_FOOT_PX` below the dot's centre, the row the map editor's nodes sit on. The minimap source shifts what its
locator found, the simulator's source shifts what it draws (and draws the dot `dot_above_foot_px` above the foot), and a
failure passes through unchanged.
"""

import threading
from types import SimpleNamespace

import numpy as np
from returns.result import Failure, Success

from src.route.position import DOT_TO_FOOT_PX, MinimapPositionSource, foot_from_dot
from src.route.sim.world import SimParams
from src.route.walker import STALE


class _Locator:
    """A `CharacterLocator` stand-in that answers with `result` and counts its calls."""

    def __init__(self, result):
        self._result = result
        self.calls = 0

    def locate(self, minimap_rgb):
        self.calls += 1
        return self._result


def _shared(frame=None):
    return SimpleNamespace(minimap_frame_lock=threading.Lock(), minimap_frame=frame)


def _frame():
    return np.zeros((82, 162, 3), dtype=np.uint8)


def test_foot_from_dot():
    assert foot_from_dot((70, 22)) == (70, 25)
    assert DOT_TO_FOOT_PX == 3


def test_minimap_source_shifts_the_located_dot():
    source = MinimapPositionSource(_shared(_frame()), _Locator(Success((40, 50))))
    assert source.read() == Success((40, 53))


def test_failures_pass_through():
    # no frame yet
    locator = _Locator(Success((40, 50)))
    assert MinimapPositionSource(_shared(None), locator).read() == Failure("no frame")
    assert locator.calls == 0

    # the same frame twice is stale the second time
    shared = _shared(_frame())
    source = MinimapPositionSource(shared, locator)
    assert source.read() == Success((40, 53))
    assert source.read() == Failure(STALE)

    # the locator's own failure comes back as it was
    answer = Failure("miss")
    assert MinimapPositionSource(_shared(_frame()), _Locator(answer)).read() is answer


def test_sim_dot_matches_the_live_offset():
    assert SimParams().dot_above_foot_px == DOT_TO_FOOT_PX
